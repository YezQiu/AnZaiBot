"""
AnZaiBot 外部服务集成层
封装 Tavily 等外部 API 的调用，统一处理请求、响应与异常。
这个模块现在非常干净，只负责API调用。
"""

import requests
import asyncio
from typing import Any, Dict, List, Optional
from utils.logger import ai_logger as logger

class TavilyClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        if not self.api_key:
            logger.warning("Tavily API Key 未配置，搜索功能将不可用。")
        self.base_url = "https://api.tavily.com/search"

    async def search(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """调用 Tavily API 进行网络搜索，返回结构化结果"""
        if not self.api_key:
            return {"error": "Tavily API Key 未配置"}

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "advanced", # 使用高级搜索获取更丰富内容
            "max_results": max_results,
            "include_raw_content": True, # 获取原始网页内容以供总结
        }
        headers = {"Content-Type": "application/json"}

        try:
            # 使用 asyncio.to_thread 运行同步的 requests 调用，避免阻塞事件循环
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,  # 使用默认线程池
                lambda: requests.post(self.base_url, json=payload, headers=headers, timeout=20)
            )
            response.raise_for_status()  # 如果HTTP状态码是4xx或5xx，则抛出异常
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Tavily 搜索请求失败: {e}")
            return {"error": f"网络搜索失败: {e}"}
        except Exception as e:
            logger.error(f"Tavily 搜索处理失败: {e}", exc_info=True)
            return {"error": f"处理搜索时发生未知错误: {e}"}

class ExternalServiceManager:
    """
    统一管理所有外部API客户端的容器。
    """
    def __init__(self, tavily_api_key: str):
        """
        通过依赖注入初始化所有外部服务客户端。
        """
        self.tavily_client = TavilyClient(api_key=tavily_api_key)

    async def search(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """统一入口：网络搜索"""
        logger.info(f"执行网络搜索: '{query}'")
        return await self.tavily_client.search(query, max_results)