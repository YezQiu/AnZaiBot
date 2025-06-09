"""
搜索结果处理工具
提供搜索结果总结等功能
"""

from typing import List
from utils.logger import ai_logger as logger
# 导入 AIInferenceLayer，但为了避免循环依赖，这里使用类型提示，实际导入在需要时进行
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.ai_inference_layer import AIInferenceLayer

SEARCH_SUMMARY_PROMPT = """
你是一个专业的信息分析师，请基于以下搜索结果提供一个全面且客观的总结：

搜索结果:
{search_results}

要求：
1. 直接以自然对话的方式表达，不要说"根据搜索结果"之类的话
2. 使用适当的表情增加回复的亲和力
3. 确保信息准确且易于理解
4. 如有矛盾信息，直接说明不同观点
5. 如涉及专业术语，用通俗语言解释

请直接开始总结，不要有任何额外的解释。
"""

class SearchHelper:
    def __init__(self, ai_inference_layer: 'AIInferenceLayer'):
        """
        初始化SearchHelper。
        :param ai_inference_layer: AIInferenceLayer 的实例，用于调用Pro模型进行总结。
        """
        self.ai_inference_layer = ai_inference_layer
        logger.info("SearchHelper 初始化完成，已连接到 AIInferenceLayer。")

    async def summarize_search_results(self, search_results: List[str]) -> str:
        """使用 AIInferenceLayer 的 Pro 模型总结搜索结果"""
        if not search_results:
            return "没有找到相关信息。"
            
        # 过滤掉空字符串或纯空白的搜索结果
        valid_results = [result.strip() for result in search_results if result and result.strip()]
        if not valid_results:
            return "没有找到有效的相关信息。"
            
        prompt = SEARCH_SUMMARY_PROMPT.format(search_results="\n".join(valid_results))

        try:
            # 直接调用 AIInferenceLayer 的 _call_gemini_api 方法，使用 Pro 模型
            # 对于搜索结果的总结，我们不限制token数，以获得完整的总结
            summary = await self.ai_inference_layer._call_gemini_api(
                model_name=self.ai_inference_layer.pro_model_name,
                prompt=prompt,
                system_instruction="你是一个专业的信息分析师，擅长用通俗易懂的语言总结和分析信息。",
                unlimited_tokens=True
            )
            return summary.strip() or "抱歉，我无法总结这些信息。"
        except Exception as e:
            logger.error(f"总结搜索结果时出错: {e}", exc_info=True)
            return "很抱歉，总结搜索结果时遇到了问题，请稍后再试。"
