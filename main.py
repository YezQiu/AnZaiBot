"""
AnZaiBot 主启动入口
负责组装所有服务、启动Web服务器、管理应用生命周期。
"""

import asyncio
import argparse
import signal
import sys
import threading
import contextlib # 导入 contextlib
from fastapi import FastAPI, Request, HTTPException
import uvicorn

from config import Config
from utils.logger import main_logger as logger, cqhttp_logger, gui_logger
# 导入所有服务和核心模块
from services.memory_manager import MemoryManager
from services.context_manager import ContextManager
from services.process_manager import ProcessManager
from services.scheduler import Scheduler
from services.external_service import ExternalServiceManager
from services.tool_executor import ToolExecutor
from utils.search_helper import SearchHelper
from core.anloop_interpreter import AnLoopInterpreter
from core.ai_inference_layer import AIInferenceLayer
from core.anzai_bot import AnZaiBot
from bot.qqbot import QQBot
from gui.memory_manager_gui import run_gui


class Application:
    """
    应用容器，负责创建和持有所有核心服务的实例。
    这是依赖注入的核心。
    """
    def __init__(self):
        logger.info("开始组装AnZaiBot应用...")
        
        self.config = Config()
        self.shutdown_event = asyncio.Event()

        # --- 实例化所有服务 ---
        # 1. 基础服务 (无依赖或仅依赖config)
        self.memory_manager = MemoryManager(db_path=self.config.DATABASE_PATH)
        self.process_manager = ProcessManager(config=self.config)
        
        # 2. 依赖基础服务的服务
        self.scheduler = Scheduler(config=self.config, process_manager=self.process_manager)
        self.context_manager = ContextManager(memory_manager=self.memory_manager)
        self.external_service_manager = ExternalServiceManager(tavily_api_key=self.config.TAVILY_API_KEY or "") # 确保 tavily_api_key 不为 None

        # 3. 核心业务逻辑 (AI大脑) - 必须在 ToolExecutor 和 SearchHelper 之前实例化
        self.anloop_interpreter = AnLoopInterpreter(tool_executor=None) # 临时设置为None，稍后设置
        self.ai_inference_layer = AIInferenceLayer(
            memory_manager=self.memory_manager,
            anloop_interpreter=self.anloop_interpreter, # 此时 anloop_interpreter 尚未完全初始化，但 AIInferenceLayer 只需要其引用
            config=self.config
        )
        
        # 4. 搜索助手 (依赖 AIInferenceLayer)
        self.search_helper = SearchHelper(ai_inference_layer=self.ai_inference_layer)

        # 6. AnZaiBot 核心 (依赖 AIInferenceLayer)
        self.anzai_bot = AnZaiBot(
            memory_manager=self.memory_manager,
            context_manager=self.context_manager,
            ai_inference_layer=self.ai_inference_layer
        )

                # 5. 接入层 (QQ机器人)
        self.qq_bot = QQBot(
            config=self.config,
            anzai_bot=self.anzai_bot,
            memory_manager=self.memory_manager,
            scheduler=self.scheduler,  # 添加scheduler参数
            context_manager=self.context_manager # 添加 context_manager 参数
        )

        # 5. 工具执行器 (依赖众多服务，包括 AIInferenceLayer 和 SearchHelper)
        self.tool_executor = ToolExecutor(
            memory_manager=self.memory_manager,
            external_service_manager=self.external_service_manager,
            scheduler=self.scheduler,
            search_helper=self.search_helper,
            config=self.config,
            ai_inference_layer=self.ai_inference_layer,
            qq_bot=self.qq_bot  # 添加对 QQBot 的引用
        )
        # 修正 anloop_interpreter 的 tool_executor 依赖
        self.anloop_interpreter.tool_executor = self.tool_executor
        
        # 6. FastAPI 应用
        self.fastapi_app = FastAPI(
            title="AnZaiBot API Server",
            version="2.0",
            lifespan=self.lifespan_event_handler # 使用 lifespan
        )
        self._setup_routes()
        
        # --- 关键修改：在所有实例创建后，进行回调注册 ---
        self.scheduler.register_send_message_callback(self.qq_bot.send_message)
        
        logger.info("AnZaiBot应用组装完成！")

    @contextlib.asynccontextmanager # 添加 lifespan 装饰器
    async def lifespan_event_handler(self, app: FastAPI):
        """FastAPI 应用生命周期事件处理器"""
        logger.info("FastAPI 启动事件触发，正在启动后台服务...")
        # 启动调度器，它会管理gocq进程和所有定时任务
        asyncio.create_task(self.scheduler.start())
        yield # 在这里应用启动，等待请求
        logger.info("FastAPI 关闭事件触发，正在停止后台服务...")
        await self.scheduler.stop()

    def _setup_routes(self):
        """配置FastAPI路由和生命周期事件"""
        
        @self.fastapi_app.post("/")
        @self.fastapi_app.post("/cqhttp/event")
        async def handle_cqhttp_event(request: Request):
            try:
                event_data = await request.json()
                # 使用 create_task 在后台处理事件，立即返回响应给go-cqhttp
                asyncio.create_task(self.qq_bot.handle_event(event_data))
                return {"status": "ok", "message": "Event received."}
            except Exception as e:
                cqhttp_logger.error(f"处理 go-cqhttp 事件时发生顶层错误: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Internal Server Error: {e}")

    def start_gui(self):
        """在单独的线程中启动 GUI"""
        try:
            gui_logger.info("正在启动 GUI...")
            run_gui(self.memory_manager, self.shutdown_event)
        except Exception as e:
            gui_logger.error(f"GUI 运行时发生错误: {e}", exc_info=True)

    async def run(self, args):
        """运行整个应用"""
        # 启动 GUI (如果需要)
        if not args.no_gui:
            gui_thread = threading.Thread(target=self.start_gui, daemon=True)
            gui_thread.start()
            gui_logger.info("GUI 线程已启动")

        # 配置 Uvicorn 服务器
        server_config = uvicorn.Config(
            self.fastapi_app,
            host="127.0.0.1",
            port=self.config.ANZAI_BOT_LISTEN_PORT,
            log_level="info",
            access_log=False, # 禁用访问日志
        )
        server = uvicorn.Server(server_config)
        
        # 运行 uvicorn 服务器
        server_task = asyncio.create_task(server.serve())

        # 等待退出信号
        # 在Windows上，asyncio.run() 会捕获KeyboardInterrupt，所以这里不需要额外的try-except
        await self.shutdown_event.wait()

        # 开始优雅退出
        logger.info("开始执行优雅退出...")
        
        # 停止Uvicorn服务器
        server.should_exit = True
        await server_task
        
        logger.info("程序已完全退出。")

    async def wait_for_shutdown(self):
        """等待退出事件"""
        # 这个方法现在只负责等待事件，实际的信号处理在main函数中
        pass


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='AnZaiBot - AI QQ Bot')
    parser.add_argument('--no-gui', action='store_true', help='不启动 GUI 界面')
    parser.add_argument('--init-db', action='store_true', help='仅初始化数据库然后退出')
    return parser.parse_args()

def main():
    """主入口函数"""
    args = parse_args()
    
    if args.init_db:
        from init_db import init_db
        init_db()
        sys.exit(0)

    app = Application()
    
    try:
        asyncio.run(app.run(args))
    except (KeyboardInterrupt, SystemExit):
        logger.info("程序被用户中断。")
    except Exception as e:
        logger.critical(f"应用顶层发生致命错误: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
