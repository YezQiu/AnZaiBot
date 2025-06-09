"""
AnZaiBot 主协调器。
负责完整地执行从接收消息到生成回复的五阶段逻辑流程。
"""

import logging
from typing import Optional

from services.context_manager import ContextManager, ContextObject # 导入 ContextObject
from services.memory_manager import MemoryManager
from core.ai_inference_layer import AIInferenceLayer

logger = logging.getLogger(__name__)

class AnZaiBot:
    def __init__(self, 
                 memory_manager: MemoryManager, 
                 context_manager: ContextManager, 
                 ai_inference_layer: AIInferenceLayer):
        """
        初始化各个核心组件 (通过依赖注入)
        """
        self.memory_manager = memory_manager
        self.context_manager = context_manager
        self.ai_inference_layer = ai_inference_layer
        logger.info("AnZaiBot 主协调器已初始化。")

    async def handle_message(self, context: ContextObject, user_message: str, is_admin: bool) -> Optional[str]:
        """
        处理单条用户消息的完整流水线。
        :param context: 包含用户和会话信息的 ContextObject。
        :param user_message: 用户发送的原始消息内容。
        :param is_admin: 指示发送消息的用户是否为管理员。
        """
        logger.info(f"--- [AnZaiBot 流水线开始] UserID: {context.user_id} (Admin: {is_admin}) ---")

        # === 阶段 1：消息接收与上下文加载 ===
        # 上下文已由 QQBot 模块加载并传入
        logger.info("[阶段 1] 上下文已加载并传入...")
        full_context = context
        full_context.metadata['is_admin'] = is_admin # 将 is_admin 信息添加到上下文元数据中
        
        common_memo_content = await self.memory_manager.get_common_memo_content(full_context.user_id)
        logger.debug(f"常识备忘录加载，长度: {len(common_memo_content)} 字")

        # === 阶段 2：预处理层 (Gemini Flash) ===
        logger.info("[阶段 2] 预处理层 (Flash) 快速思考...")
        pre_processed_data = await self.ai_inference_layer.flash_pre_process(
            user_message=user_message,
            common_memo_content=common_memo_content
        )
        logger.info(f"Flash 预处理结果: {pre_processed_data}")
        
        # === 阶段 3-5：主控决策、工具执行与响应生成 ===
        logger.info("[阶段 3-5] 主控层决策、执行与响应...")
        decision_result = await self.ai_inference_layer.make_decision(
            user_message_text=user_message,
            full_context=full_context
        )
        
        logger.info(f"最终决策结果: 类型='{decision_result.response_type}', 是否回复='{decision_result.should_respond}'")
        
        if decision_result.should_respond:
            final_reply = decision_result.payload
            logger.info(f"生成最终回复，内容: '{str(final_reply)[:100]}...'")
            logger.info(f"--- [AnZaiBot 流水线结束] UserID: {full_context.user_id} ---")
            return final_reply
        else:
            logger.info("检测到 #NotResp 或无直接回复内容，本轮不主动发送消息。")
            if decision_result.payload:
                logger.info(f"后台任务日志/结果: {decision_result.payload}")
            logger.info(f"--- [AnZaiBot 流水线结束] UserID: {full_context.user_id} ---")
            return None
