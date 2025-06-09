"""
数据库初始化脚本
用于首次创建和初始化 AnZaiBot 所需的数据库表及默认数据。
"""

import sqlite3
from config import Config
from utils.logger import main_logger as logger

def init_db():
    """初始化数据库，创建所需的表和默认数据"""
    db_path = Config.DATABASE_PATH
    logger.info(f"开始初始化数据库: {db_path}")
    
    try:
        with sqlite3.connect(db_path) as conn:
            c = conn.cursor()
            
            # 创建消息历史表
            c.execute("""
                CREATE TABLE IF NOT EXISTS message_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    nickname TEXT,
                    message_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            
            # 创建常识备忘录表
            c.execute("""
                CREATE TABLE IF NOT EXISTS common_memo (
                    user_id TEXT PRIMARY KEY,
                    content TEXT,
                    updated_at TEXT
                )
            """)
            
            # 创建命名备忘录表
            c.execute("""
                CREATE TABLE IF NOT EXISTS named_memos (
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    capacity INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY(user_id, title)
                )
            """)
            
            # 创建 notebook 表
            c.execute("""
                CREATE TABLE IF NOT EXISTS notebooks (
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    params TEXT,
                    content TEXT,
                    credit_remaining INTEGER DEFAULT 100,
                    last_edited TEXT,
                    PRIMARY KEY(user_id, name)
                )
            """)
            
            # 创建管理员白名单表
            c.execute("""
                CREATE TABLE IF NOT EXISTS admin_whitelist (
                    user_id TEXT PRIMARY KEY
                )
            """)
            
            # 创建系统规则表
            c.execute("""
                CREATE TABLE IF NOT EXISTS system_rules (
                    user_id TEXT PRIMARY KEY,
                    content TEXT,
                    updated_at TEXT
                )
            """)

            # --- 插入默认数据 ---
            
            # 插入默认通用常识备忘录
            c.execute("INSERT OR IGNORE INTO common_memo (user_id, content, updated_at) VALUES (?, ?, datetime('now'))",
                     ("common", "这是一个通用的常识备忘录，用于存放所有用户的通用背景信息。"))
            
            # 插入默认管理员
            admin_id = Config.ADMIN_QQ
            if admin_id:
                c.execute("INSERT OR IGNORE INTO admin_whitelist (user_id) VALUES (?)", (admin_id,))
                logger.info(f"已将 {admin_id} 添加到管理员白名单。")

            # 插入默认系统规则
            c.execute("INSERT OR IGNORE INTO system_rules (user_id, content, updated_at) VALUES (?, ?, datetime('now'))",
                     ("system", "你是一个名为AnZaiBot的AI助手，乐于助人、专业且友好。"))
            
            conn.commit()
            logger.info("数据库初始化完成！所有表和默认数据已创建。")
            
    except Exception as e:
        logger.error(f"初始化数据库时发生错误: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    init_db()