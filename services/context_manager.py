"""
上下文管理器，管理所有用户的运行时上下文数据。
"""
import asyncio
from typing import Dict, Optional, Any, List
from services.memory_manager import MemoryManager
from utils.logger import context_logger as logger

class ContextObject:
    """上下文对象，存储用户的当前状态和数据"""
    def __init__(self, user_id: str, nickname: str, session_id: str, is_group_chat: bool, is_at_me: bool = False):
        self.user_id = user_id
        self.nickname = nickname
        self.session_id = session_id # 区分私聊和群聊的唯一会话ID
        self.is_group_chat = is_group_chat
        self.is_at_me = is_at_me # 在群聊中是否被@
        self.last_active_time = asyncio.get_event_loop().time()
        self.message_history: List[Dict[str, Any]] = []  # 最近的消息历史
        self.metadata: Dict[str, Any] = {}  # 其他元数据，如 is_admin, group_name 等

    def update_activity(self):
        """更新活跃时间"""
        self.last_active_time = asyncio.get_event_loop().time()

    def is_expired(self, max_age: float) -> bool:
        """检查上下文是否过期"""
        current_time = asyncio.get_event_loop().time()
        return current_time - self.last_active_time > max_age

class ContextManager:
    """管理所有用户的运行时上下文数据"""
    def __init__(self, memory_manager: MemoryManager):
        self.contexts: Dict[str, ContextObject] = {}
        self.memory_manager = memory_manager
        self.context_max_age = 3600 # 上下文在内存中保留1小时

    async def get_context(self, user_id: str, nickname: str, message_type: str, group_id: Optional[str] = None, is_at_me: bool = False) -> ContextObject:
        """
        获取或创建并更新会话上下文。
        :param user_id: 发送消息的用户ID。
        :param nickname: 用户昵称。
        :param message_type: 消息类型 ('private' 或 'group')。
        :param group_id: 如果是群聊，则为群ID。
        :param is_at_me: 如果是群聊，且消息@了机器人，则为True。
        """
        # 根据消息类型确定会话ID
        is_group_chat = (message_type == 'group')
        session_id = group_id if is_group_chat else user_id

        if not session_id:
            logger.error(f"无法确定会话ID。消息类型: {message_type}, 用户ID: {user_id}, 群ID: {group_id}")
            # 返回一个默认的私聊上下文，以防万一
            session_id = user_id # 强制使用user_id作为session_id
            is_group_chat = False
            is_at_me = False

        context = self.contexts.get(session_id)
        if not context:
            logger.info(f"为会话 {session_id} (用户: {user_id}, 群聊: {is_group_chat}) 创建新的上下文。")
            # 尝试从缓存获取昵称，如果缓存中没有，则使用传入的昵称
            cached_nickname = self.memory_manager.get_cached_nickname(user_id)
            final_nickname = cached_nickname if cached_nickname else nickname
            context = ContextObject(user_id, final_nickname, session_id, is_group_chat, is_at_me)
            self.contexts[session_id] = context
        else:
            # 更新现有上下文的元数据和活跃时间
            context.user_id = user_id # 确保user_id是最新的
            # 优先使用传入的最新昵称，如果传入的为空，则尝试从缓存获取
            context.nickname = nickname if nickname else self.memory_manager.get_cached_nickname(user_id) or context.nickname
            context.is_at_me = is_at_me # 更新@状态
            context.update_activity()
        
        try:
            # 加载最近的对话历史到上下文中
            # 对于群聊，即使没有@，也加载最近的群聊消息作为上下文
            context.message_history = await self.memory_manager.get_recent_messages(
                user_id=user_id, # 仍然传递user_id，因为消息历史可能需要按user_id过滤
                group_id=group_id,
                limit=200, # 群聊上下文限制200条
                content_max_len=300 # 单条消息截断300字
            )
            
            # 清理过期的上下文
            await self.clear_expired_contexts()

            return context

        except Exception as e:
            logger.error(f"加载会话 {session_id} 上下文时发生错误: {e}", exc_info=True)
            # 即使失败，也要返回一个基础上下文以保证程序运行
            return ContextObject(user_id, nickname, session_id, is_group_chat, is_at_me)

    async def clear_expired_contexts(self):
        """清理内存中过期的上下文对象"""
        try:
            current_time = asyncio.get_event_loop().time()
            expired_sessions = [
                session_id for session_id, context in self.contexts.items()
                if context.is_expired(self.context_max_age)
            ]
            
            for session_id in expired_sessions:
                del self.contexts[session_id]
                
            if expired_sessions:
                logger.info(f"已从内存中清理 {len(expired_sessions)} 个过期上下文。")
        except Exception as e:
            logger.error(f"清理过期上下文时发生错误: {e}", exc_info=True)
