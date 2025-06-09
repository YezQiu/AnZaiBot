"""
AnZaiBot AI 推理与决策层 (AI Inference & Decision Layer)
AnZaiBot 的大脑，由 Gemini 模型驱动，负责理解意图、执行推理、并生成响应或工具调用序列。
"""

import asyncio
import json
import re # 导入 re 模块
# noinspection PyUnresolvedReferences
from typing import List, Dict, Any, Literal, Optional # 导入 Optional
# noinspection PyUnresolvedReferences
from google import genai
# noinspection PyUnresolvedReferences
from google.genai import types # 导入 types 模块，用于 GenerateContentConfig

from utils.logger import ai_logger as logger
from config import Config
from services.context_manager import ContextObject
from core.anloop_interpreter import AnLoopInterpreter
from services.memory_manager import MemoryManager

class PreProcessedData:
    """预处理结果对象 (来自阶段 2)"""
    def __init__(self, needs_loops: bool, preliminary_intent: str, extracted_params: Dict[str, Any]):
        self.needs_loops = needs_loops
        self.preliminary_intent = preliminary_intent
        self.extracted_params = extracted_params

    def __str__(self):
        return f"需要工具: {self.needs_loops}, 初步意图: '{self.preliminary_intent}', 提取参数: {self.extracted_params}"

class DecisionResult:
    """决策结果对象 (来自阶段 3)"""
    def __init__(self, response_type: Literal["direct_reply", "anloop_sequence"], payload: Any, should_respond: bool = True):
        self.response_type = response_type
        self.payload = payload
        self.should_respond = should_respond

class AIInferenceLayer:
    def __init__(self, memory_manager: MemoryManager, anloop_interpreter: AnLoopInterpreter, config: Config):
        self.memory_manager = memory_manager
        self.anloop_interpreter = anloop_interpreter
        self.config = config
        
        self.valid_keys = self.config.GEMINI_API_KEYS
        if not self.valid_keys:
            raise ValueError("没有配置任何可用的 GEMINI_API_KEY！")
        
        self.current_key_index = 0
        # 使用 genai.Client 初始化
        self.client = genai.Client(api_key=self.valid_keys[self.current_key_index])
        
        self.flash_model_name = 'gemini-2.0-flash' # 更新模型名称
        self.pro_model_name = 'gemini-2.5-flash-preview-05-20' # 更新模型名称
        
        logger.info(f"AIInferenceLayer 初始化完成，已配置 {len(self.valid_keys)} 个 Gemini API Key。")

    def _rotate_api_key(self):
        """轮换到下一个可用的 API key"""
        self.current_key_index = (self.current_key_index + 1) % len(self.valid_keys)
        new_key = self.valid_keys[self.current_key_index]
        self.client = genai.Client(api_key=new_key) # 更新 client 的 API key
        logger.info(f"已切换到第 {self.current_key_index + 1} 个 Gemini API Key。")

    async def _call_gemini_api(self, model_name: str, prompt: str, system_instruction: Optional[str] = None, is_json: bool = False, unlimited_tokens: bool = False): # system_instruction 允许为 None
        """统一的 Gemini API 调用函数，包含重试和Key轮换逻辑"""
        for i in range(len(self.valid_keys) * 2):
            try:
                # 根据 unlimited_tokens 设置 max_output_tokens
                max_tokens = None if unlimited_tokens else 2048

                # 使用 types.GenerateContentConfig 来传递 system_instruction 和其他配置
                config = types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=max_tokens, # 根据 unlimited_tokens 设置
                    candidate_count=1,
                    stop_sequences=[],
                    system_instruction=system_instruction # 直接传递 system_instruction
                )
                
                # 根据 is_json 设置 response_mime_type
                if is_json:
                    config.response_mime_type = "application/json"

                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt, # contents 可以直接是字符串
                    config=config
                )
                
                if response.text is None:
                    logger.warning(f"Gemini API 返回空响应，尝试切换密钥... (尝试 {i+1}/{len(self.valid_keys)*2})")
                    self._rotate_api_key()
                    await asyncio.sleep(1)
                    continue

                resp = response.text.strip()
                return resp

            except Exception as api_error:
                # 对于任何类型的错误，都尝试切换密钥重试
                logger.warning(f"Gemini API 错误: {api_error}，尝试切换密钥... (尝试 {i+1}/{len(self.valid_keys)*2})")
                self._rotate_api_key()
                await asyncio.sleep(1)
                continue
        raise Exception("所有 Gemini API Key 都已尝试且均失败。")

    async def flash_pre_process(self, user_message: str, common_memo_content: str) -> PreProcessedData:
        """[阶段 2] 利用快速的 Gemini Flash 模型对用户请求进行初步判断。"""
        logger.debug(f"[Flash预处理] 用户消息='{user_message[:50]}...'")
        prompt = f"""
你的角色是 AnZaiBot 的一个高效预处理模块。你的任务是根据用户消息和相关的"常识备忘录"，快速分析用户意图，判断是否明显需要调用工具（如搜索、记录、分析、提醒等）。

常识备备忘录（相关背景知识）:
{common_memo_content if common_memo_content else "无"}

用户消息:
"{user_message}"

请根据上述信息，以严格的 JSON 格式输出你的分析结果。
- 如果用户意图明确需要一个或多个工具来完成，请设置 `needs_loops` 为 `true`，并简要描述 `preliminary_intent`，同时提取关键参数到 `extracted_params`。
- 如果用户只是在闲聊或意图不明确，请设置 `needs_loops` 为 `false`，`preliminary_intent` 设为 "直接对话或意图不明"。

JSON 结构:
{{
  "needs_loops": boolean,
  "preliminary_intent": string,
  "extracted_params": {{}}
}}
"""
        try:
            response_text = await self._call_gemini_api(self.flash_model_name, prompt, is_json=True) # 传入模型名称
            if response_text.startswith("```json"):
                response_text = response_text[len("```json"):-len("```")].strip()
            parsed_data = json.loads(response_text)
            return PreProcessedData(**parsed_data)
        except Exception as e:
            logger.error(f"Flash模型调用或JSON解析失败: {e}", exc_info=True)
            return PreProcessedData(True, "预处理失败，需深度分析", {})

    async def make_decision(self, user_message_text: str, full_context: ContextObject) -> DecisionResult:
        """[阶段 3] AnZaiBot 的核心决策引擎。"""
        logger.debug(f"[主控决策] 用户消息: '{user_message_text[:50]}...', 会话: {full_context.session_id}, 群聊: {full_context.is_group_chat}, @我: {full_context.is_at_me}")

        # 获取全局系统提示词，如果不存在则使用默认值
        system_instruction = await self.memory_manager.get_system_rules("global_system_user")
        if not system_instruction:
            system_instruction = "你是一个名为AnZaiBot的AI助手，乐于助人、专业且友好。你能够感知到用户的QQ昵称和是否为管理员，并可以在回复中利用这些信息。"

        available_tools_description = self.anloop_interpreter.get_tools_description()
        common_memo_content = await self.memory_manager.get_common_memo_content(full_context.user_id)
        
        history_lines = []
        for msg in full_context.message_history:
            is_msg_admin = (msg.get('user_id') == self.config.ADMIN_QQ)
            admin_status = "(管理员)" if is_msg_admin else ""
            
            chat_name = ""
            if msg.get('message_type') == 'private':
                chat_name = "私聊"
            elif msg.get('message_type') == 'group':
                chat_name = f"群聊({msg.get('group_id', '未知群')})"
            
            nickname = msg.get('nickname', msg.get('user_id', '未知用户'))
            
            content = msg['content']
            # 处理消息中的 @ 标记，将 [CQ:at,qq=xxx] 替换为对应的昵称
            at_matches = re.finditer(r'\[CQ:at,qq=(\d+)\]', content)
            for match in at_matches:
                qq_id = match.group(1)
                at_nickname = self.memory_manager.get_cached_nickname(qq_id) or qq_id
                content = content.replace(match.group(0), f"@{at_nickname}")

            if msg['role'] == 'user':
                history_lines.append(f"{admin_status}<{chat_name}>[{nickname}]：{content}")
            elif msg['role'] == 'assistant':
                history_lines.append(f"<{chat_name}>[AnZaiBot]：{content}")
        
        history_str = "\n".join(reversed(history_lines)) # 保持最近的消息在底部

        notebook_summary = await self.memory_manager.get_notebooks_summary(full_context.user_id)
        memos_summary = await self.memory_manager.get_memos_summary(full_context.user_id)

        # Flash 模型作为群聊主控逻辑
        if full_context.is_group_chat and not full_context.is_at_me:
            # 群聊非@AI消息，由Flash模型决定是否回复或调用工具
            flash_prompt = f"""
你是一个高效的群聊助手，你的任务是根据群聊的最新消息和历史上下文，判断是否需要回复。
你能够感知到用户的QQ昵称和是否为管理员。请在回复中利用这些信息，让对话更自然。
如果需要回复，请直接生成回复文本，或者生成一个 AnLoop 工具调用序列。
如果不需要回复，请直接输出 `NO_REPLY`。

### 当前群聊上下文
**对话历史 (最近):**
{history_str if history_str else "无"}
**常识备忘录 (你的核心记忆):**
{common_memo_content if common_memo_content else "无"}
**命名备忘录摘要:**
{memos_summary if memos_summary else "无"}
**Notebooks摘要:**
{notebook_summary if notebook_summary else "无"}

### 可用工具清单 (AnLoop协议)
{available_tools_description}

### 最新群聊消息
{full_context.nickname}({full_context.user_id}): {user_message_text}
用户是否为管理员: {'是' if full_context.metadata.get('is_admin') else '否'}

### 你的任务
根据以上所有信息，做出最终决策：
1.  **直接回复**: 如果是简单问候或根据现有知识就能完美回答，请直接生成友好的回复。你可以使用表情增加亲和力。
2.  **调用工具**: 如果任务需要任何工具能完成的功能，请生成一个或多个 AnLoop 工具调用序列。格式: `<Loops>#工具名=参数</Loops>`。如果需要后台执行，请在序列开头加上 `#NotResp`。
3.  **不回复**: 如果你认为当前消息不需要回复，请直接输出 `NO_REPLY`。

请直接输出你的最终决策结果（AnLoop序列、回复文本或 `NO_REPLY`），可以使用 QQ 表情让回复更加生动，不要包含额外解释。
"""
            try:
                flash_response_text = await self._call_gemini_api(self.flash_model_name, flash_prompt, system_instruction=system_instruction)
                logger.info(f"Flash模型群聊决策原始输出: '{flash_response_text[:200]}...'")

                if flash_response_text.strip().upper() == "NO_REPLY":
                    logger.info("Flash模型决定在群聊中不回复。")
                    return DecisionResult("direct_reply", "", should_respond=False)
                else:
                    # 使用正则表达式匹配 <Loops>...</Loops> 标签
                    loops_match = re.search(r"<Loops>(.*?)</Loops>", flash_response_text, re.DOTALL)
                    anloop_part = ""
                    direct_reply_part = flash_response_text # 默认整个响应都是直接回复

                    if loops_match:
                        anloop_part = loops_match.group(0) # 包含 <Loops> 和 </Loops> 标签
                        # 移除 AnLoop 部分，剩余的是直接回复
                        direct_reply_part = flash_response_text.replace(anloop_part, "").strip()
                        logger.info(f"Flash模型检测到 AnLoop 序列。AnLoop部分: '{anloop_part[:100]}...', 直接回复部分: '{direct_reply_part[:100]}...'")
                    
                    if anloop_part: # 如果存在 AnLoop 序列
                        logger.info("Flash模型决策为 AnLoop 序列，移交解释器执行...")
                        tool_results, not_resp_flag = await self.anloop_interpreter.execute_anloop_sequence(
                            anloop_string=anloop_part, user_id=full_context.user_id, group_id=full_context.session_id) # 传递 group_id
                        
                        if not not_resp_flag: # 如果不是后台任务
                            if direct_reply_part: # 如果AI模型已经生成了直接回复
                                logger.info("使用AI模型生成的直接回复。")
                                return DecisionResult("direct_reply", direct_reply_part, should_respond=True)
                            else: # 如果没有直接回复，则根据工具结果总结
                                logger.info("没有AI模型生成的直接回复，根据工具结果总结。")
                                successful_results = [res.result for res in tool_results if res.success and res.result]
                                payload = "\n".join(map(str, successful_results)) if successful_results else "后台任务已启动。"
                                pro_prompt = f"""
你是一个AI助手，Flash模型已经执行了以下工具，并得到了结果：
{payload}

请根据这些工具执行结果，以友好、简洁的方式生成一个回复。
"""
                                final_reply = await self._call_gemini_api(self.pro_model_name, pro_prompt, system_instruction=system_instruction)
                                return DecisionResult("direct_reply", final_reply, should_respond=True)
                        else:
                            logger.info("后台任务，不回复。")
                            successful_results = [res.result for res in tool_results if res.success and res.result]
                            payload = "\n".join(map(str, successful_results)) if successful_results else "后台任务已启动。"
                            return DecisionResult("anloop_sequence", payload, should_respond=False) # 后台任务不回复
                    else: # 没有 AnLoop 序列，直接回复
                        logger.info("Flash模型决定直接回复群聊消息。")
                        return DecisionResult("direct_reply", direct_reply_part, should_respond=True)
            except Exception as e:
                logger.error(f"Flash模型群聊决策失败，转交 Pro 处理: {e}", exc_info=True)
                # Flash 失败，回退到 Pro 模型处理
                pass # 继续执行下面的 Pro 模型逻辑

        # 私聊消息或 @AI 的群聊消息，以及 Flash 模型回退的情况，由 Pro 模型处理
        # 对于私聊，不限制回复token数
        is_private_chat = not full_context.is_group_chat
        
        prompt = f"""### 用户原始消息
{user_message_text}

### 当前完整上下文
**对话历史 (最近):**
{history_str if history_str else "无"}
**常识备忘录 (你的核心记忆):**
{common_memo_content if common_memo_content else "无"}
**命名备忘录摘要:**
{memos_summary if memos_summary else "无"}
**Notebooks摘要:**
{notebook_summary if notebook_summary else "无"}

### 可用工具清单 (AnLoop协议)
{available_tools_description}

### 当前用户信息
用户昵称: {full_context.nickname}
用户ID: {full_context.user_id}
是否为管理员: {'是' if full_context.metadata.get('is_admin') else '否'}

### 你的任务
根据以上所有信息，做出最终决策：
1.  **直接回复**: 如果是简单问候或根据现有知识就能完美回答，请直接生成友好的回复。你可以使用表情增加亲和力。
2.  **调用工具**: 如果任务需要任何工具能完成的功能，请生成一个或多个 AnLoop 工具调用序列。格式: `<Loops>#工具名=参数</Loops>`。如果需要后台执行，请在序列开头加上 `#NotResp`。

请直接输出你的最终决策结果（AnLoop序列或回复文本），可以使用 QQ 表情让回复更加生动，不要包含额外解释。
"""
        try:
            response_text = await self._call_gemini_api(self.pro_model_name, prompt, system_instruction=system_instruction, unlimited_tokens=is_private_chat) # 传入模型名称，私聊不限制token
            logger.info(f"主控模型原始输出: '{response_text[:200]}...'")

            # 使用正则表达式匹配 <Loops>...</Loops> 标签
            loops_match = re.search(r"<Loops>(.*?)</Loops>", response_text, re.DOTALL)
            anloop_part = ""
            direct_reply_part = response_text # 默认整个响应都是直接回复

            if loops_match:
                anloop_part = loops_match.group(0) # 包含 <Loops> 和 </Loops> 标签
                # 移除 AnLoop 部分，剩余的是直接回复
                direct_reply_part = response_text.replace(anloop_part, "").strip()
                logger.info(f"主控模型检测到 AnLoop 序列。AnLoop部分: '{anloop_part[:100]}...', 直接回复部分: '{direct_reply_part[:100]}...'")
            
            if anloop_part: # 如果存在 AnLoop 序列
                logger.info("决策为 AnLoop 序列，移交解释器执行...")
                tool_results, not_resp_flag = await self.anloop_interpreter.execute_anloop_sequence(
                    anloop_string=anloop_part, user_id=full_context.user_id, group_id=full_context.session_id) # 传递 group_id
                
                if not not_resp_flag: # 如果不是后台任务
                    if direct_reply_part: # 如果AI模型已经生成了直接回复
                        logger.info("使用AI模型生成的直接回复。")
                        return DecisionResult("direct_reply", direct_reply_part, should_respond=True)
                    else: # 如果没有直接回复，则根据工具结果总结
                        logger.info("没有AI模型生成的直接回复，根据工具结果总结。")
                        successful_results = [res.result for res in tool_results if res.success and res.result]
                        payload = "\n".join(map(str, successful_results)) if successful_results else "后台任务已启动。"
                        # 如果存在成功的工具结果，就使用它们
                        if successful_results:
                            return DecisionResult("direct_reply", "\n".join(successful_results), should_respond=True)
                        # 否则使用更友好的错误提示
                        error_reply_res = await self.anloop_interpreter.tool_executor.dispatch_tool("errorlib", {"reason": "tool_execution_failed", "user_id": full_context.user_id})
                        return DecisionResult("direct_reply", error_reply_res.result, should_respond=True)
                else:
                    logger.info("后台任务，不回复。")
                    successful_results = [res.result for res in tool_results if res.success and res.result]
                    payload = "\n".join(map(str, successful_results)) if successful_results else "后台任务已启动。"
                    return DecisionResult("anloop_sequence", payload, should_respond=False)
            else:
                logger.info("决策为直接回复。")
                return DecisionResult("direct_reply", direct_reply_part) # 使用 direct_reply_part
        except Exception as e:
            logger.error(f"Pro模型调用或决策处理失败: {e}", exc_info=True)
            error_reply_res = await self.anloop_interpreter.tool_executor.dispatch_tool("errorlib", {"reason": "general_error", "user_id": full_context.user_id})
            return DecisionResult("direct_reply", error_reply_res.result)
