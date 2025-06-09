"""
AnZaiBot QQ 客户端消息处理模块
负责从 QQ 客户端接收原始消息，标准化，并传递给核心处理。
"""

import httpx
import asyncio
import random
from collections import deque
from typing import Dict, Any, Optional, List

from utils.logger import cqhttp_logger as logger
from config import Config
from core.anzai_bot import AnZaiBot
from services.memory_manager import MemoryManager
from services.scheduler import Scheduler # 导入 Scheduler
from services.context_manager import ContextManager # 导入 ContextManager

class QQBot:
    def __init__(self, config: Config, anzai_bot: AnZaiBot, memory_manager: MemoryManager, scheduler: Scheduler, context_manager: ContextManager):
        self.config = config
        self.anzai_bot = anzai_bot
        self.memory_manager = memory_manager
        self.scheduler = scheduler # 保存 scheduler 实例
        self.context_manager = context_manager # 保存 context_manager 实例
        
        self.http_client = httpx.AsyncClient(
            base_url=self.config.GO_CQHTTP_URL,
            timeout=20.0
        )
        if self.config.GO_CQHTTP_ACCESS_TOKEN:
            self.http_client.headers['Authorization'] = f'Bearer {self.config.GO_CQHTTP_ACCESS_TOKEN}'
            
        self.processed_message_ids = deque(maxlen=200) # 存储已处理消息ID，防止重复
        
        # 群聊消息缓冲区和定时器
        self.group_message_buffers: Dict[str, List[Dict]] = {} # {group_id: [message_event, ...]}
        self.group_message_timers: Dict[str, asyncio.Task] = {} # {group_id: asyncio.Task}
        self.GROUP_MESSAGE_BUFFER_THRESHOLD_FIXED = 5 # 固定消息数量阈值
        self.GROUP_MESSAGE_BUFFER_THRESHOLD_RANDOM = 5 # 随机消息数量阈值 (0到此值之间)
        self.GROUP_MESSAGE_BUFFER_TIMEOUT = 10 # 秒，超时时间
        self.MAX_MESSAGE_LENGTH_PRIVATE = 3000 # 私聊消息最大长度
        self.GROUP_REPLY_COOLDOWN = 20 # 群聊回复冷却时间，单位秒
        self.last_group_reply_time: Dict[str, float] = {} # 记录每个群聊的上次回复时间
        self.group_reply_buffers: Dict[str, List[str]] = {} # 存储每个群聊在冷却期间积累的回复

    async def handle_event(self, event: Dict[str, Any]):
        """处理所有从 go-cqhttp 上报的事件"""
        post_type = event.get('post_type')
        
        # --- 关键修改：处理心跳包 ---
        if post_type == 'meta_event' and event.get('meta_event_type') == 'heartbeat':
            self.scheduler.update_heartbeat() # 通知调度器心跳
            return

        logger.info(f"接收到 go-cqhttp 事件: {event}")

        if post_type == 'message':
            await self._handle_message_event(event)
        # 可以在此处理其他事件类型，如加好友请求、群成员增加等

    async def _handle_message_event(self, msg_event: Dict[str, Any]):
        """专门处理消息事件"""
        message_id = msg_event.get('message_id')
        if message_id in self.processed_message_ids:
            logger.warning(f"忽略重复消息: ID {message_id}")
            return
        self.processed_message_ids.append(message_id)

        user_id = str(msg_event.get('user_id'))
        group_id = str(msg_event.get('group_id')) if msg_event.get('message_type') == 'group' else None
        nickname = msg_event.get('sender', {}).get('card') or msg_event.get('sender', {}).get('nickname')
        raw_content = msg_event.get('raw_message', '').strip()
        message_type = msg_event.get('message_type')
        
        if not raw_content:
            return # 忽略空消息

        # 检查是否 @了机器人
        self_qq = str(self.config.QQ_BOT_ID) # 从 config 中获取机器人QQ号
        is_at_me = False
        if message_type == 'group' and f"[CQ:at,qq={self_qq}]" in raw_content:
            is_at_me = True
            logger.info(f"群聊中 @了你: {raw_content}") # 添加日志
            # 移除 @机器人的CQ码，以便AI处理纯净内容
            content = raw_content.replace(f"[CQ:at,qq={self_qq}]", "").strip()
        else:
            content = raw_content

        # 1. 将用户消息存入历史记录
        await self.memory_manager.add_message_to_history(
            user_id=user_id,
            message_type=message_type,
            content=content,
            role='user',
            nickname=nickname,
            group_id=group_id
        )

        # 2. 获取上下文
        context = await self.context_manager.get_context(
            user_id=user_id,
            nickname=nickname,
            message_type=message_type,
            group_id=group_id,
            is_at_me=is_at_me
        )

        # 3. 群聊消息打包处理逻辑
        if message_type == 'group' and not is_at_me:
            if group_id not in self.group_message_buffers:
                self.group_message_buffers[group_id] = []
            self.group_message_buffers[group_id].append(msg_event) # 存储原始事件，方便后续处理
            
            # 取消之前的定时器
            if group_id in self.group_message_timers and not self.group_message_timers[group_id].done():
                self.group_message_timers[group_id].cancel()
            
            # 检查是否达到消息数量阈值
            threshold = self.GROUP_MESSAGE_BUFFER_THRESHOLD_FIXED + random.randint(0, self.GROUP_MESSAGE_BUFFER_THRESHOLD_RANDOM)
            if len(self.group_message_buffers[group_id]) >= threshold:
                logger.info(f"群 {group_id} 消息达到阈值 ({threshold} 条)，立即处理。")
                await self._process_buffered_group_messages(group_id)
            else:
                # 启动新的定时器
                logger.debug(f"群 {group_id} 消息未达阈值，启动/重置 {self.GROUP_MESSAGE_BUFFER_TIMEOUT} 秒定时器。")
                self.group_message_timers[group_id] = asyncio.create_task(
                    self._start_group_message_timer(group_id)
                )
            return # 群聊非@消息，先缓冲，不立即处理

        # 4. 调用核心逻辑处理 (私聊消息或 @AI 的群聊消息)
        is_admin = (user_id == self.config.ADMIN_QQ) # 判断是否为管理员
        try:
            reply_content = await self.anzai_bot.handle_message(context, content, is_admin) # 传递 context, content 和 is_admin
        except Exception as e:
            logger.error(f"处理消息时发生严重错误: {e}", exc_info=True)
            reply_content = "抱歉，我的内部逻辑出现了一点问题，我已经记录下来了。"

        # 5. 发送回复
        if reply_content:
            await self.send_message(user_id, group_id, message_type, reply_content)

    async def _start_group_message_timer(self, group_id: str):
        """为群聊消息缓冲区设置定时器"""
        try:
            await asyncio.sleep(self.GROUP_MESSAGE_BUFFER_TIMEOUT)
            if group_id in self.group_message_buffers and self.group_message_buffers[group_id]:
                # 检查是否达到消息数量阈值，只有达到阈值才处理
                threshold = self.GROUP_MESSAGE_BUFFER_THRESHOLD_FIXED + random.randint(2, self.GROUP_MESSAGE_BUFFER_THRESHOLD_RANDOM)
                if len(self.group_message_buffers[group_id]) >= threshold:
                    logger.info(f"群 {group_id} 消息超时且达到阈值 ({threshold} 条)，处理缓冲区消息。")
                    await self._process_buffered_group_messages(group_id)
                else:
                    logger.info(f"群 {group_id} 消息超时但未达阈值 ({len(self.group_message_buffers[group_id])} < {threshold} 条)，不处理，等待后续消息触发。")
                    # 根据用户要求，当定时器超时且消息数量未达到阈值时，不处理缓冲区中的消息。
                    # 当前定时器任务会被移除，以便新的消息到来时能重新启动新的定时器。
                    pass # 明确不处理
        except asyncio.CancelledError:
            logger.debug(f"群 {group_id} 消息定时器被取消。")
        except Exception as e:
            logger.error(f"群 {group_id} 消息定时器发生错误: {e}", exc_info=True)
        finally:
            self.group_message_timers.pop(group_id, None) # 移除当前定时器任务，等待新的消息重新触发


    async def _process_buffered_group_messages(self, group_id: str):
        """处理指定群聊缓冲区中的消息"""
        if group_id not in self.group_message_buffers or not self.group_message_buffers[group_id]:
            return

        buffered_events = self.group_message_buffers.pop(group_id)
        
        # 组合消息内容
        combined_content = ""
        # 提取最后一个消息的发送者信息作为当前会话的用户信息
        last_event = buffered_events[-1]
        user_id = str(last_event.get('user_id'))
        nickname = last_event.get('sender', {}).get('card') or last_event.get('sender', {}).get('nickname')

        for event in buffered_events:
            sender_nickname = event.get('sender', {}).get('card') or event.get('sender', {}).get('nickname')
            msg_content = event.get('raw_message', '').strip()
            combined_content += f"{sender_nickname}({event.get('user_id')}): {msg_content}\n"
            
            # 将缓冲区的每条消息也存入历史记录 (如果之前没有存的话)
            # 注意：这里需要确保不会重复存储，因为 _handle_message_event 已经存储了
            # 更好的做法是 _handle_message_event 只存储，然后这里只处理AI逻辑
            # 考虑到当前设计，_handle_message_event 已经存储了，这里不再重复存储

        logger.info(f"处理群 {group_id} 打包消息，共 {len(buffered_events)} 条。")
        
        # 获取群聊上下文
        # 注意：这里不再需要单独获取上下文，因为 AnZaiBot.handle_message 会处理上下文加载
        # context = await self.context_manager.get_context(
        #     user_id=user_id, # 使用最后一条消息的发送者作为当前处理的用户
        #     nickname=nickname,
        #     message_type='group',
        #     group_id=group_id,
        #     is_at_me=False # 打包消息默认不是@AI触发
        # )

        is_admin = (user_id == self.config.ADMIN_QQ) # 判断是否为管理员
        try:
            # 调用核心逻辑处理打包消息
            # 在这里重新获取一次上下文，因为打包消息的 context 可能与单条消息的 context 不同
            # 并且 AnZaiBot.handle_message 现在需要 ContextObject
            context_for_buffered = await self.context_manager.get_context(
                user_id=user_id,
                nickname=nickname,
                message_type='group',
                group_id=group_id,
                is_at_me=False # 打包消息默认不是@AI触发
            )
            reply_content = await self.anzai_bot.handle_message(context_for_buffered, combined_content, is_admin)
        except Exception as e:
            logger.error(f"处理打包群聊消息时发生严重错误: {e}", exc_info=True)
            reply_content = "抱歉，处理群聊消息时我遇到了一些问题。"

        if reply_content:
            await self.send_message(user_id, group_id, 'group', reply_content)

    async def send_message(self, user_id: str, group_id: Optional[str], message_type: str, content: str, at_user_id: Optional[str] = None):
        """
        通过 go-cqhttp 发送 QQ 消息，支持 @特定用户。
        :param user_id: 接收消息的用户ID (私聊) 或触发消息的用户ID (群聊)。
        :param group_id: 群ID (如果为群聊)。
        :param message_type: 消息类型 ('private' 或 'group')。
        :param content: 要发送的消息内容。
        :param at_user_id: 可选，要在群聊中 @的QQ号。
        """
        endpoint = ""
        payload = {}
        log_target = ""
        
        messages_to_send = []

        if message_type == 'private':
            log_target = f"私聊用户 {user_id}"
            if len(content) > self.MAX_MESSAGE_LENGTH_PRIVATE:
                # 分割长消息
                for i in range(0, len(content), self.MAX_MESSAGE_LENGTH_PRIVATE):
                    messages_to_send.append(content[i:i+self.MAX_MESSAGE_LENGTH_PRIVATE])
            else:
                messages_to_send.append(content)
            endpoint = "/send_private_msg"
            payload["user_id"] = int(user_id)
        elif message_type == 'group' and group_id:
            log_target = f"群聊 {group_id}"
            if at_user_id:
                # 在消息内容前添加 @CQ码
                messages_to_send.append(f"[CQ:at,qq={at_user_id}] {content}")
            else:
                messages_to_send.append(content)
            endpoint = "/send_group_msg"
            payload["group_id"] = int(group_id)
        else:
            logger.error(f"无法发送消息：未知的消息类型或缺少群组ID。Type: {message_type}, GroupID: {group_id}")
            return

        current_time = asyncio.get_event_loop().time()

        if message_type == 'group' and group_id:
            last_reply_time = self.last_group_reply_time.get(group_id, 0.0)
            if current_time - last_reply_time < self.GROUP_REPLY_COOLDOWN:
                # 处于冷却时间，将回复内容加入缓冲区
                if group_id not in self.group_reply_buffers:
                    self.group_reply_buffers[group_id] = []
                self.group_reply_buffers[group_id].append(content)
                logger.info(f"群 {group_id} 处于冷却时间，回复已加入缓冲区。当前缓冲区消息数: {len(self.group_reply_buffers[group_id])}")
                return # 不发送消息，等待冷却结束

            # 如果不在冷却时间，或者冷却时间已过，处理积累的消息
            if group_id in self.group_reply_buffers and self.group_reply_buffers[group_id]:
                # 将当前回复与积累的回复合并
                all_buffered_replies = self.group_reply_buffers.pop(group_id)
                all_buffered_replies.append(content)
                combined_reply_content = "\n".join(all_buffered_replies)
                
                logger.info(f"群 {group_id} 冷却结束，处理积累的 {len(all_buffered_replies)} 条回复。")
                
                # 调用 AI 模型总结积累的回复
                try:
                    # 获取上下文，用于总结
                    context_for_summary = await self.context_manager.get_context(
                        user_id=user_id, # 使用触发消息的用户ID
                        nickname="AnZaiBot", # 机器人自己的昵称
                        message_type='group',
                        group_id=group_id,
                        is_at_me=False # 总结回复不是@AI触发
                    )
                    summary_prompt = f"""
你是一个AI助手，需要总结以下多条回复内容，并生成一个简洁、连贯的最终回复。
这些回复是AnZaiBot在短时间内对群聊消息的响应。

请将以下内容总结成一条回复：
{combined_reply_content}

请直接输出总结后的回复，不要包含任何额外的解释。
"""
                    # 使用 Pro 模型进行总结，不限制token
                    final_reply_content = await self.anzai_bot.ai_inference_layer._call_gemini_api(
                        self.anzai_bot.ai_inference_layer.pro_model_name, 
                        summary_prompt, 
                        system_instruction="你是一个AI助手，负责总结多条回复。", 
                        unlimited_tokens=True
                    )
                    messages_to_send = [final_reply_content] # 替换为总结后的内容
                    logger.info(f"群 {group_id} 积累回复总结完成。")
                except Exception as e:
                    logger.error(f"总结群 {group_id} 积累回复时发生错误: {e}", exc_info=True)
                    messages_to_send = ["抱歉，我尝试总结群聊回复时遇到了一些问题。"] # 总结失败，发送错误消息
            
            # 更新上次回复时间
            self.last_group_reply_time[group_id] = current_time

        for i, msg_part in enumerate(messages_to_send):
            payload["message"] = msg_part
            try:
                logger.info(f"准备发送消息到 {log_target} (部分 {i+1}/{len(messages_to_send)}): {payload['message'][:50]}...")
                response = await self.http_client.post(endpoint, json=payload)
                response.raise_for_status()
                result = response.json()
                if result.get('status') == 'ok':
                    logger.info(f"消息发送成功。")
                    # 将机器人回复也存入历史记录 (只记录一次完整内容，或者记录每个分段)
                    # 这里选择记录完整内容，但只在第一次发送时记录，或者记录每个分段
                    # 为了简化，我们只记录原始的完整内容，而不是每个分段
                    if i == 0: # 只在发送第一个分段时记录到历史
                        await self.memory_manager.add_message_to_history(
                            user_id=user_id, # 这里的user_id是触发消息的用户，不是机器人自己
                            message_type=message_type,
                            content=content, # 存储不带@的纯净完整内容
                            role='assistant',
                            group_id=group_id
                        )
                else:
                    logger.error(f"消息发送失败: {result}")
            except Exception as e:
                logger.error(f"发送消息到 {log_target} (部分 {i+1}/{len(messages_to_send)}) 时发生错误: {e}", exc_info=True)
                break # 某个分段发送失败，停止后续发送
            
            if len(messages_to_send) > 1 and i < len(messages_to_send) - 1:
                await asyncio.sleep(0.5) # 分段发送之间添加短暂延迟，避免触发频率限制
