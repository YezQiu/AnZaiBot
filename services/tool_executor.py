"""
AnZaiBot 工具执行器
封装所有具体工具的调用逻辑，处理工具执行的返回值和异常。
"""

from typing import Any, Dict, Optional
from datetime import datetime

from utils.logger import ai_logger as logger
from services.external_service import ExternalServiceManager
from services.memory_manager import MemoryManager
from services.scheduler import Scheduler
from utils.search_helper import SearchHelper
from config import Config
# 导入 AIInferenceLayer 和 QQBot，为了避免循环依赖，这里使用类型提示
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.ai_inference_layer import AIInferenceLayer
    from bot.qqbot import QQBot

class ToolExecutionResult:
    """工具执行结果对象，用于统一返回格式。"""
    def __init__(self, success: bool, result: Any = None, error: Optional[str] = None):
        self.success = success
        self.result = result  # 成功时返回的结果
        self.error = error    # 失败时返回的错误信息

    def __str__(self):
        return f"Success: {self.success}, Result: {self.result}, Error: {self.error}"

class ToolExecutor:
    """
    AnZaiBot 工具执行器。
    通过依赖注入接收所有需要的服务，并根据工具名分发执行。
    """
    def __init__(self, 
                 memory_manager: MemoryManager, 
                 external_service_manager: ExternalServiceManager,
                 scheduler: Scheduler,
                 search_helper: SearchHelper,
                 config: Config,
                 ai_inference_layer: 'AIInferenceLayer',
                 qq_bot: 'QQBot'): # 添加 qq_bot 参数
        self.memory_manager = memory_manager
        self.external_service_manager = external_service_manager
        self.scheduler = scheduler
        self.search_helper = search_helper
        self.config = config
        self.ai_inference_layer = ai_inference_layer # 保存 AIInferenceLayer 实例
        self.qq_bot = qq_bot # 保存 QQBot 实例

    async def dispatch_tool(self, tool_name: str, tool_params: Dict[str, Any]) -> ToolExecutionResult:
        """
        根据工具名称和参数分发调用具体工具。
        这是所有工具调用的统一入口。
        """
        user_id = tool_params.get("user_id")
        if not user_id:
            return ToolExecutionResult(False, error="所有工具调用都必须包含 user_id")

        # 动态查找名为 _execute_TOOLNAME 的方法
        method_name = f"_execute_{tool_name.lower().replace('-', '_')}"
        method = getattr(self, method_name, self._execute_unknown)
        
        logger.info(f"Dispatching tool '{tool_name}' for user '{user_id}' with params: {tool_params}")
        
        try:
            # 调用找到的方法
            result = await method(user_id, tool_params)
            return ToolExecutionResult(True, result=result)
        except Exception as e:
            logger.error(f"执行工具 '{tool_name}' 时发生严重错误: {e}", exc_info=True)
            return ToolExecutionResult(False, error=f"执行工具'{tool_name}'时内部错误: {e}")

    async def _execute_unknown(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理未知工具的调用"""
        tool_name = params.get('_tool_name_from_dispatch', 'unknown') # 假设分发器能传入原始工具名
        logger.warning(f"用户 {user_id} 尝试调用未知工具: {tool_name}")
        return f"错误：未知的工具 '{tool_name}'。"

    # --- 核心行为控制工具 ---

    async def _execute_notresp(self, user_id: str, params: Dict[str, Any]) -> str:
        """#NotResp 是一个标志，不由执行器处理，但保留方法以备将来扩展。"""
        return "后台模式已激活。"

    async def _execute_errorlib(self, user_id: str, params: Dict[str, Any]) -> str:
        """获取预设的错误或拒绝回复。"""
        reason = params.get("reason", "general_error")
        error_messages = {
            "unknown_command": "抱歉，我不理解您的指令。请尝试更明确的表达。",
            "tool_execution_failed": "工具执行失败，请稍后再试或联系管理员。",
            "no_search_results": "未能找到相关搜索结果。",
            "memo_not_found": "未找到指定的备忘录。",
            "notebook_not_found": "未找到指定的Notebook。",
            "insufficient_credit": "Notebook 信用额度不足。",
            "general_error": "发生了一个未知错误，请稍后再试。"
        }
        return error_messages.get(reason, error_messages["general_error"])

    # --- 记忆与知识管理工具 ---

    async def _execute_memo(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #Memo 工具，支持快速写入和深度编辑。"""
        content = params.get("content")
        target_memo_title = params.get("target_memo", "default")

        if content:
            # 快速写入模式
            if target_memo_title == "default":
                # 写入常识备忘录
                await self.memory_manager.update_common_memo(user_id, content)
                return "信息已记录到常识备忘录。"
            else:
                # 写入命名备忘录
                success = await self.memory_manager.update_named_memo(user_id, target_memo_title, content)
                if success:
                    return f"信息已记录到备忘录 '{target_memo_title}'。"
                else:
                    return f"错误：写入失败，未找到名为 '{target_memo_title}' 的备忘录。"
        else:
            # 深度编辑模式
            # TODO: 实现深度编辑逻辑，可能需要与AI层有更复杂的交互
            return "进入深度编辑模式（功能待实现）。"

    async def _execute_memosize(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #MemoSize 工具。此工具的主要作用是为下一步 #NameMemo 提供容量参数。"""
        size_str = params.get("content") or params.get("size")
        if not size_str or not size_str.isdigit():
            return "错误：#MemoSize 需要一个有效的数字作为容量。"
        
        size = int(size_str)
        if size > 5000:
            return "错误：备忘录容量最大不能超过5000字。"
        
        # 这个工具本身不执行数据库操作，它的结果被AnLoop解释器捕获并传递给下一个工具。
        return f"备忘录容量已设定为 {size} 字，请继续使用 #NameMemo 指定标题。"

    async def _execute_namememo(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #NameMemo 工具，创建命名备忘录。"""
        title = params.get("content") or params.get("title")
        if not title:
            return "错误：#NameMemo 需要一个备忘录标题。"
            
        capacity = params.get("capacity", self.config.DEFAULT_MEMO_CAPACITY)

        success = await self.memory_manager.create_named_memo(user_id, title, capacity)
        if success:
            return f"已成功创建命名备忘录 '{title}'，容量为 {capacity} 字。"
        else:
            return f"错误：名为 '{title}' 的备忘录已存在。"

    async def _execute_memoref(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #MemoRef 工具，只读引用备忘录内容。"""
        title = params.get("content") or params.get("title")
        if not title:
            return "错误：#MemoRef 需要一个备忘录标题。"

        memo_content = await self.memory_manager.get_named_memo_content(user_id, title)
        if memo_content is not None:
            # 返回内容供AI在上下文中处理
            return f"--- 备忘录 '{title}' 内容开始 ---\n{memo_content}\n--- 备忘录 '{title}' 内容结束 ---"
        else:
            return f"错误：未找到名为 '{title}' 的备忘录。"

    async def _execute_notebook(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #Notebook 工具，进入深度思考空间。"""
        # 格式: #Notebook=笔记本名称|参数
        combined_content = params.get("content", "")
        parts = combined_content.split('|', 1)
        name = parts[0].strip()
        task_params = parts[1].strip() if len(parts) > 1 else "无"

        if not name:
            return "错误：进入 #Notebook 需要提供笔记本名称。"

        # TODO: 实现与Notebook交互的复杂逻辑
        # 1. 检查或创建Notebook
        # 2. 消耗额度
        # 3. 将任务参数和相关上下文注入Notebook
        # 4. 执行Notebook内的AI思考循环
        # 5. 返回最终结果
        return f"已进入 Notebook '{name}'，任务参数: '{task_params}' (完整功能待实现)。"

    async def _execute_credit(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #Credit 工具，申报额度。"""
        amount_str = params.get("content") or params.get("amount")
        if not amount_str or not amount_str.isdigit():
            return "错误：#Credit 需要一个有效的数字作为额度数量。"

        amount = int(amount_str)
        # TODO: 将这个额度值暂存，并在调用 #Notebook 时使用
        return f"已申报 {amount} 次额外编辑额度，将在下次进入 Notebook 时生效。"

    # --- 信息获取与定时工具 ---

    async def _execute_search(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #Search 工具，执行网络搜索并总结。"""
        query = params.get("query") or params.get("content")
        if not query:
            return "错误：#Search 需要提供查询关键词。"
        
        search_result_json = await self.external_service_manager.search(query)
        
        if "error" in search_result_json:
            return f"搜索失败: {search_result_json['error']}"

        search_snippets = [item.get("content", "") for item in search_result_json.get("results", [])]
        if not search_snippets:
            return "未能找到相关信息。"
        
        summary = await self.search_helper.summarize_search_results(search_snippets)
        return summary

    async def _execute_globalsearch(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #GlobalSearch 工具，在所有聊天记录中搜索。"""
        query = params.get("query") or params.get("content")
        target_user_id = params.get("user_id")
        target_nickname = params.get("nickname")

        if not query and not target_user_id and not target_nickname:
            return "错误：#GlobalSearch 需要提供搜索关键词、用户ID或昵称至少一项。"
        
        search_results = await self.memory_manager.search_all_chat_history(
            query=query,
            user_id=target_user_id,
            nickname=target_nickname,
            limit=20 # 限制返回结果数量
        )

        if not search_results:
            return "未在历史记录中找到相关信息。"
        
        formatted_results = []
        for msg in search_results:
            chat_info = f"群聊 {msg['group_id']}" if msg['group_id'] else "私聊"
            formatted_results.append(
                f"[{msg['timestamp']}] {chat_info} {msg['nickname']}({msg['user_id']}): {msg['content']}"
            )
        
        return "历史记录搜索结果:\n" + "\n".join(formatted_results)

    async def _execute_atuser(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #AtUser 工具，在群聊中 @特定用户并发送消息。"""
        target_user_id = str(params.get("target_user_id") or params.get("qq"))
        message_content = params.get("content")
        group_id = str(params.get("group_id")) # 必须提供 group_id

        if not target_user_id or not message_content or not group_id:
            return "错误：#AtUser 需要 target_user_id (或 qq), content 和 group_id。"
        
        try:
            # 调用 QQBot 的 send_message 方法，并传入 at_user_id
            await self.qq_bot.send_message(
                user_id=target_user_id, # 这里的user_id是@的目标用户，不是触发AI的用户
                group_id=group_id,
                message_type='group',
                content=message_content,
                at_user_id=target_user_id
            )
            return f"已在群 {group_id} 中 @了用户 {target_user_id} 并发送消息。"
        except Exception as e:
            logger.error(f"执行 #AtUser 工具失败: {e}", exc_info=True)
            return f"错误：@用户失败: {e}"


    async def _execute_bingme(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #BingMe 工具，设定定时任务时间点。"""
        time_str = params.get("content")
        if not time_str:
            return "错误：#BingMe 需要提供时间点。"
        
        try:
            run_time = datetime.strptime(time_str, "%Y/%m/%d-%H:%M")
            # --- 关键修改：调用新方法 ---
            return await self.scheduler.add_bing_me_task(user_id, run_time)
        except ValueError:
            return f"错误：无法解析时间格式 '{time_str}'。请使用 '年/月/日-时:分' 格式。"

    async def _execute_bingmsg(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #BingMsg 工具，设定定时发送的消息。"""
        msg = params.get("content")
        if not msg:
            return "错误：#BingMsg 需要提供消息内容。"
        # --- 关键修改：调用新方法 ---
        return await self.scheduler.update_pending_task_with_message(user_id, msg)

    async def _execute_bingnote(self, user_id: str, params: Dict[str, Any]) -> str:
        """处理 #BingNote 工具，设定定时回顾的 Notebook。"""
        notebook_name = params.get("content")
        if not notebook_name:
            return "错误：#BingNote 需要提供 Notebook 名称。"
        # --- 关键修改：调用新方法 ---
        return await self.scheduler.update_pending_task_with_notebook(user_id, notebook_name)
