"""
AnZaiBot 配置文件
从此文件加载所有配置，支持从 .env 文件读取敏感信息
"""

import os
from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件
# 这使得你可以将敏感信息（如API密钥）放在.env文件中，而不用提交到git
load_dotenv()

class Config:
    # 数据库配置
    DATABASE_PATH = os.getenv("DATABASE_PATH", "anzai_data.db")

    # go-cqhttp 配置
    GO_CQHTTP_URL = os.getenv("GO_CQHTTP_URL", "http://127.0.0.1:42300")
    GO_CQHTTP_ACCESS_TOKEN = os.getenv("GO_CQHTTP_ACCESS_TOKEN", None)
    
    # AnZaiBot 服务配置
    ANZAI_BOT_LISTEN_PORT = int(os.getenv("ANZAI_BOT_LISTEN_PORT", 42401))

    # go-cqhttp 进程管理配置
    GOCQ_EXECUTABLE_WIN = "go-cqhttp_windows_amd64.exe"
    GOCQ_EXECUTABLE_LINUX = "go-cqhttp_linux_amd64" # 示例

    # API Keys 配置
    # 建议将API密钥存储在 .env 文件中，格式为 GEMINI_API_KEYS="key1,key2,key3"
    GEMINI_API_KEYS_STR = os.getenv("GEMINI_API_KEYS", "")
    GEMINI_API_KEYS = [key.strip() for key in GEMINI_API_KEYS_STR.split(',') if key.strip()]

    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

    # 管理员配置
    ADMIN_QQ = os.getenv("ADMIN_QQ", "996386279")  # 管理员QQ号

    # QQ 机器人自身ID
    QQ_BOT_ID = os.getenv("QQ_BOT_ID", "3671063394") # 从 .env 或默认值获取机器人QQ号

    # 其他配置
    MAX_HISTORY_MESSAGES = 200  # 最大历史消息数
    MAX_RETRY_COUNT = 3  # API 调用最大重试次数
    DEFAULT_MEMO_CAPACITY = 5000 # 创建命名备忘录时的默认容量
