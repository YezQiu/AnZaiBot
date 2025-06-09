"""
AnZaiBot 定时任务调度器
使用 apscheduler 实现强大的任务调度功能。
使用被动心跳包机制监控 go-cqhttp 的存活状态。
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger

from utils.logger import scheduler_logger as logger
from config import Config
from services.process_manager import ProcessManager

class Scheduler:
    def __init__(self, config: Config, process_manager: ProcessManager):
        self.config = config
        self.process_manager = process_manager
        
        # --- 心跳监控相关 ---
        self.last_heartbeat_time = 0
        self.heartbeat_timeout = 100  # 100秒没收到心跳就认为gocq挂了

        # --- 定时任务相关 ---
        # 使用数据库作为任务持久化存储，这样即使程序重启，任务也不会丢失
        jobstores = {
            'default': SQLAlchemyJobStore(url=f'sqlite:///{self.config.DATABASE_PATH}')
        }
        self.apscheduler = AsyncIOScheduler(jobstores=jobstores, timezone='Asia/Shanghai')
        
        self.running = False
        self.monitor_task = None
        
        # 用于保存 #BingMe 创建的临时任务ID，以便 #BingMsg 更新
        self._pending_user_tasks: Dict[str, str] = {}
        
        # 回调函数，用于发送消息
        self.send_message_callback: Callable[[str, str, str, str], Awaitable[None]] = None

    def register_send_message_callback(self, callback: Callable[[str, str, str, str], Awaitable[None]]):
        """注册发送消息的回调函数，解耦与QQBot的依赖。"""
        self.send_message_callback = callback
        logger.info("发送消息的回调函数已注册到调度器。")

    def update_heartbeat(self):
        """由外部（QQBot）调用，用于更新心跳时间戳。"""
        self.last_heartbeat_time = time.time()
        logger.debug("接收到 go-cqhttp 心跳。")

    async def _monitor_gocq_process(self):
        """定期检查心跳时间戳，并在需要时重启 go-cqhttp。"""
        logger.info("go-cqhttp 心跳监控任务已启动。")
        await asyncio.sleep(self.heartbeat_timeout) # 首次启动后等待一个超时周期

        while self.running:
            try:
                now = time.time()
                if now - self.last_heartbeat_time > self.heartbeat_timeout:
                    logger.warning(f"超过 {self.heartbeat_timeout} 秒未收到 go-cqhttp 心跳，判定为失联，准备重启...")
                    
                    # 重启逻辑
                    self.process_manager.stop()
                    await asyncio.sleep(3) # 等待旧进程完全终止
                    if self.process_manager.start():
                        logger.info("go-cqhttp 重启成功。")
                        # 重启后，重置心跳时间，并给予启动时间
                        self.update_heartbeat()
                        await asyncio.sleep(10)
                    else:
                        logger.error("go-cqhttp 重启失败。")
                else:
                    logger.debug("go-cqhttp 心跳正常。")

            except Exception as e:
                logger.error(f"监控 go-cqhttp 时发生严重错误: {e}", exc_info=True)
            
            await asyncio.sleep(10)  # 每10秒检查一次心跳

    # --- 定时任务核心方法 (TODOs 实现) ---

    async def add_bing_me_task(self, user_id: str, run_time: datetime) -> str:
        """
        处理 #BingMe 工具。
        创建一个临时的 'pending' 任务，并返回其ID。
        """
        job_id = f"pending_{user_id}_{int(run_time.timestamp())}"
        
        # 添加一个什么都不做的临时任务，只是为了占位
        self.apscheduler.add_job(
            func=lambda: None, 
            trigger=DateTrigger(run_date=run_time),
            id=job_id,
            name=f"Pending task for {user_id}",
            replace_existing=True
        )
        # 记录这个待处理的任务ID
        self._pending_user_tasks[user_id] = job_id
        logger.info(f"为用户 {user_id} 创建了一个待处理的定时任务，ID: {job_id}")
        return f"时间点 '{run_time.strftime('%Y-%m-%d %H:%M:%S')}' 已设定。请继续使用 #BingMsg 或 #BingNote 指定任务内容。"

    async def update_pending_task_with_message(self, user_id: str, message: str) -> str:
        """
        处理 #BingMsg 工具。
        找到用户的待处理任务，并将其修改为真正的“发送消息”任务。
        """
        pending_job_id = self._pending_user_tasks.pop(user_id, None)
        if not pending_job_id:
            return "错误：请先使用 #BingMe 设定一个时间点。"
            
        job = self.apscheduler.get_job(pending_job_id)
        if not job:
            return "错误：待处理的任务已过期或不存在。"

        if not self.send_message_callback:
            return "错误：系统内部错误，无法发送消息（回调未注册）。"

        # 修改任务，使其在到期时调用 send_message_callback
        job.modify(
            func=self.send_message_callback,
            args=[user_id, None, 'private', message], # 假设默认私聊
            name=f"Send '{message[:10]}...' to {user_id}"
        )
        logger.info(f"任务 {pending_job_id} 已更新为发送消息任务。")
        return "定时消息已设定。"

    async def update_pending_task_with_notebook(self, user_id: str, notebook_name: str) -> str:
        """
        处理 #BingNote 工具。
        找到用户的待处理任务，并将其修改为“回顾Notebook”任务。
        """
        pending_job_id = self._pending_user_tasks.pop(user_id, None)
        if not pending_job_id:
            return "错误：请先使用 #BingMe 设定一个时间点。"
            
        job = self.apscheduler.get_job(pending_job_id)
        if not job:
            return "错误：待处理的任务已过期或不存在。"
        
        # TODO: 定义一个回顾Notebook的回调函数
        async def review_notebook_job(uid, nb_name):
            logger.info(f"执行定时任务：用户 {uid} 回顾 Notebook '{nb_name}'")
            # 在这里可以调用 AnZaiBot 的核心逻辑来处理回顾
            # await self.anzai_bot.handle_notebook_review(uid, nb_name)

        job.modify(
            func=review_notebook_job,
            args=[user_id, notebook_name],
            name=f"Review notebook '{notebook_name}' for {user_id}"
        )
        logger.info(f"任务 {pending_job_id} 已更新为回顾Notebook任务。")
        return f"定时回顾 Notebook '{notebook_name}' 的任务已设定。"

    # --- 调度器生命周期管理 ---

    async def start(self):
        """启动调度器及其管理的服务"""
        if self.running:
            return
        logger.info("调度器正在启动...")
        
        # 启动 apscheduler
        self.apscheduler.start()
        
        # 启动 go-cqhttp
        self.process_manager.start()
        
        # 初始化心跳时间
        self.update_heartbeat()
        
        # 启动心跳监控任务
        self.monitor_task = asyncio.create_task(self._monitor_gocq_process())
        
        self.running = True
        logger.info("调度器已完全启动。")

    async def stop(self):
        """停止调度器及其管理的服务"""
        if not self.running:
            return
        logger.info("调度器正在停止...")
        self.running = False
        
        # 停止监控任务
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                logger.info("心跳监控任务已取消。")

        # 优雅地关闭 apscheduler，等待当前任务完成
        self.apscheduler.shutdown()
        
        # 停止 go-cqhttp 进程
        self.process_manager.stop()
        
        logger.info("调度器已停止。")