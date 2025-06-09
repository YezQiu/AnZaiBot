"""
跨平台的外部进程管理器。
封装了启动、停止和监控 go-cqhttp 进程的底层逻辑。
"""
import subprocess
import sys
import os
import time
import psutil  # 引入强大的跨平台进程管理库

from utils.logger import scheduler_logger as logger
from config import Config

class ProcessManager:
    """
    管理 go-cqhttp 外部进程的生命周期。
    这个类只关心进程本身，不关心进程提供的服务（如API）。
    """
    def __init__(self, config: Config):
        self.config = config
        self.process: subprocess.Popen = None
        self.platform = sys.platform

        # 从配置中获取可执行文件路径
        # __file__ 指的是当前文件(process_manager.py)的路径
        # os.path.dirname(__file__) 是 services 目录
        # os.path.dirname(os.path.dirname(__file__)) 是项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.gocq_dir = os.path.join(project_root, "Go-cqhttp")
        
        if self.platform == "win32":
            self.gocq_executable = self.config.GOCQ_EXECUTABLE_WIN
        else:  # 假设是 linux 或 macos
            self.gocq_executable = self.config.GOCQ_EXECUTABLE_LINUX
        
        self.gocq_path = os.path.join(self.gocq_dir, self.gocq_executable)

    def is_running(self) -> bool:
        """
        使用 psutil 检查我们启动的进程是否仍在运行。
        这是比 process.poll() 更可靠的方式。
        """
        if not self.process:
            return False
        # psutil.pid_exists() 是检查进程PID是否还存在的最好方法
        return psutil.pid_exists(self.process.pid)

    def start(self) -> bool:
        """
        根据不同平台启动 go-cqhttp 进程。
        返回操作是否成功。
        """
        if not os.path.exists(self.gocq_path):
            logger.error(f"go-cqhttp 可执行文件未找到，请检查路径: {self.gocq_path}")
            return False
            
        if self.is_running():
            logger.warning(f"go-cqhttp 进程 (PID: {self.process.pid}) 已在运行，无需重复启动。")
            return True

        logger.info(f"正在启动 go-cqhttp...")
        try:
            if self.platform == "win32":
                # 在Windows上，用 'start' 命令在新窗口中启动，这样关闭窗口就能结束进程
                # 这种方式的缺点是，我们无法直接获取到新窗口中 go-cqhttp 的真实PID，
                # self.process.pid 只是 cmd.exe 的PID。
                # 但 psutil 的 parent.children() 机制可以帮助我们找到它。
                cmd = f'start cmd /K "cd /d {self.gocq_dir} && {self.gocq_executable}"'
                # 使用 CREATE_NEW_PROCESS_GROUP 标志，以便能完整地终止进程树
                self.process = subprocess.Popen(
                    cmd, 
                    shell=True,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                # 在 Linux/macOS 上的标准启动方式
                logger.info(f"为 {self.gocq_path} 添加执行权限...")
                os.chmod(self.gocq_path, 0o755)
                # 直接启动可执行文件，这样 self.process.pid 就是 go-cqhttp 的真实PID
                self.process = subprocess.Popen([self.gocq_path], cwd=self.gocq_dir)
            
            logger.info(f"go-cqhttp 启动命令已发送。启动器进程 PID: {self.process.pid}")
            return True
        except FileNotFoundError:
            logger.error(f"命令或程序未找到: {self.gocq_path}。请确保 go-cqhttp 在指定目录中。")
            return False
        except Exception as e:
            logger.error(f"启动 go-cqhttp 时发生未知错误: {e}", exc_info=True)
            return False

    def stop(self):
        """
        使用 psutil 跨平台地、强制地停止 go-cqhttp 进程及其所有子进程。
        """
        if not self.process or not self.is_running():
            logger.info("go-cqhttp 进程未运行或未由本管理器启动，无需停止。")
            return

        logger.info(f"正在停止 go-cqhttp 进程树 (基于启动器PID: {self.process.pid})...")
        try:
            parent = psutil.Process(self.process.pid)
            # 找到所有子进程（包括孙子进程等）
            children = parent.children(recursive=True)
            
            # 先终止所有子进程
            for child in children:
                try:
                    logger.info(f"正在终止子进程 {child.name()} (PID: {child.pid})...")
                    child.kill()
                except psutil.NoSuchProcess:
                    continue # 可能已经被父进程关闭了
            
            # 最后终止父进程（启动器进程）
            try:
                logger.info(f"正在终止启动器进程 {parent.name()} (PID: {parent.pid})...")
                parent.kill()
            except psutil.NoSuchProcess:
                pass # 可能已经自己退出了

            # 等待进程完全终止
            _, still_alive = psutil.wait_procs(children + [parent], timeout=5)
            if not still_alive:
                logger.info("go-cqhttp 进程树已成功终止。")
            else:
                for p in still_alive:
                    logger.warning(f"进程 {p.pid} 未能终止。")

        except psutil.NoSuchProcess:
            logger.warning(f"尝试停止时，启动器进程 {self.process.pid} 已不存在。")
        except Exception as e:
            logger.error(f"停止 go-cqhttp 进程时发生错误: {e}", exc_info=True)
        finally:
            self.process = None