"""
AnZaiBot AnLoop 协议解释器与工具调度器
负责解析 AnLoop 协议字符串，识别并调度工具执行。
"""

import re
import asyncio
from typing import List, Tuple, Dict, Any, Optional # 导入 Optional
from services.tool_executor import ToolExecutor, ToolExecutionResult
from utils.logger import ai_logger as logger

class AnLoopInterpreter:
    def __init__(self, tool_executor: ToolExecutor):
        self.tool_executor = tool_executor

    def get_tools_description(self) -> str:
        """返回 AnZaiBot 可用工具的详细描述，供 AI 模型参考。"""
        # 这里是您提供的完整工具清单
        return """
# AnZaiBot 工具清单：用法与作用

AnZaiBot 的所有工具都遵循 AnLoop 协议，即以 `<Loops>#工具名=参数</Loops>` 的格式进行调用。多个工具调用会按其在 AnZaiBot 回复中出现的顺序被严格执行。

## I. 核心行为控制工具

### #NotResp
- **用法**: `<Loops>#NotResp</Loops>`
- **作用**: 抑制 AnZaiBot 本轮的即时文本回复。当 AnZaiBot 需要在后台执行一系列工具操作时使用。如果在一个 AnLoop 序列中存在，它必须是第一个被调用的工具。

### #ErrorLib
- **用法**: `<Loops>#ErrorLib=错误原因描述</Loops>`
- **作用**: 用于礼貌地拒绝或回避超出能力范围的请求。
- **示例**: `<Loops>#ErrorLib=我无法提供投资建议</Loops>`

## II. 记忆与知识管理工具

### #Memo (快速写入)
- **用法**: `<Loops>#Memo=要写入的内容;target_memo=备忘录标题</Loops>` (target_memo可选，默认为常识备忘录)
- **作用**: 将内容快速写入备忘录。

### #Memo (深度编辑)
- **用法**: `<Loops>#Memo</Loops>` (不带任何参数)
- **作用**: 进入备忘录专用编辑模式，可查看并修改全部内容。消耗1次Notebook额度。

### #MemoSize
- **用法**: `<Loops>#MemoSize=字数</Loops>` (例如: `<Loops>#MemoSize=4000</Loops>`)
- **作用**: 申请创建或扩充命名备忘录的容量，上限5000字。必须在 #NameMemo 之前调用。

### #NameMemo
- **用法**: `<Loops>#NameMemo=备忘录标题</Loops>`
- **作用**: 为通过 #MemoSize 申请的备忘录指定标题。必须在 #MemoSize 之后调用。

### #MemoRef
- **用法**: `<Loops>#MemoRef=备忘录标题</Loops>`
- **作用**: 只读加载指定备忘录的全部内容作为临时上下文。

### #Notebook
- **用法**: `<Loops>#Notebook=笔记本名称|参数</Loops>`
- **作用**: 进入一个专用的深度思考空间（Notebook）。

### #Credit
- **用法**: `<Loops>#Credit=额度数量</Loops>`
- **作用**: 申报本次进入 Notebook 将获得的额外编辑次数。必须在 #Notebook 之前调用。

## III. 信息获取与定时工具

### #Search
- **用法**: `<Loops>#Search=搜索查询词</Loops>`
- **作用**: 触发网络搜索。

### #BingMe
- **用法**: `<Loops>#BingMe=年/月/日-时:分</Loops>`
- **作用**: 设定一个定时提醒的时间点。

### #BingMsg
- **用法**: `<Loops>#BingMsg=要发送给用户的消息内容</Loops>`
- **作用**: 指定定时提醒时发送的消息。

### #BingNote
- **用法**: `<Loops>#BingNote=要回顾的Notebook名称</Loops>`
- **作用**: 指定定时提醒时回顾的Notebook。

### #GlobalSearch
- **用法**: `<Loops>#GlobalSearch=搜索关键词;user_id=用户ID;nickname=昵称</Loops>` (user_id和nickname可选)
- **作用**: 在所有聊天记录中搜索指定内容。

### #AtUser
- **用法**: `<Loops>#AtUser=要@的QQ号;content=消息内容;group_id=群号</Loops>`
- **作用**: 在群聊中@特定用户并发送消息。
"""

    def _parse_tool_call(self, tool_str: str) -> Tuple[str, Dict[str, Any]]:
        """健壮地解析单个工具调用字符串"""
        parts = tool_str.strip().split('=', 1)
        tool_name = parts[0][1:]  # 移除'#'
        
        params = {}
        if len(parts) > 1:
            param_str = parts[1]
            # 简单处理，假设参数值不包含分号
            param_pairs = param_str.split(';')
            for i, pair in enumerate(param_pairs):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    params[key.strip()] = value.strip()
                elif i == 0: # 第一个无键参数通常是主要内容
                    params["content"] = pair.strip()
        
        # 兼容 #Search=query 这种简单格式
        if 'content' in params and not params.get('content'):
             del params['content']
        if not params and len(parts) > 1:
            params['query'] = parts[1]


        return tool_name, params

    async def execute_anloop_sequence(self, anloop_string: str, user_id: str, group_id: Optional[str] = None) -> Tuple[List[ToolExecutionResult], bool]:
        """解析并串行执行 AnLoop 序列"""
        loops_match = re.search(r'<Loops>(.*?)</Loops>', anloop_string, re.DOTALL)
        if not loops_match:
            return [], False

        loop_content = loops_match.group(1).strip()
        
        not_resp = False
        if loop_content.startswith("#NotResp"):
            not_resp = True
            loop_content = loop_content.replace("#NotResp", "", 1).strip('; ')

        # 拆分工具调用，这里用分号作为分隔符
        tool_calls_str = [tc.strip() for tc in loop_content.split(';') if tc.strip() and tc.startswith('#')]
        
        results: List[ToolExecutionResult] = []
        
        # 当前设计为串行执行，因为工具间可能有依赖
        for tool_str in tool_calls_str:
            tool_name, params = self._parse_tool_call(tool_str)
            params["user_id"] = user_id  # 注入user_id
            if group_id:
                params["group_id"] = group_id # 注入 group_id

            logger.info(f"调度执行工具: '{tool_name}'，参数: {params}")
            result = await self.tool_executor.dispatch_tool(tool_name, params)
            results.append(result)

            if not result.success:
                logger.warning(f"工具 {tool_name} 执行失败，但将继续执行序列。错误: {result.error}")
                #可以选择在这里中断 `break`
        
        return results, not_resp
