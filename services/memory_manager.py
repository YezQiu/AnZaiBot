"""
AnZaiBot 记忆与数据持久化核心
负责所有数据库的交互操作。
"""

import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from utils.logger import memory_logger as logger

class MemoryManager:
    """
    管理所有与数据库anzai_data.db的交互。
    所有方法都设计为异步，以适应asyncio事件循环。
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        # 确保数据库文件所在的目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
        # 初始化时检查并创建表
        self._init_db()
        self._nickname_cache: Dict[str, str] = {} # 内存中的昵称缓存

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接。可以配置超时等参数。"""
        return sqlite3.connect(self.db_path, timeout=10)

    def _init_db(self):
        """
        初始化数据库表结构。
        使用 "CREATE TABLE IF NOT EXISTS" 确保操作的幂等性。
        """
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                # 创建消息历史表
                cursor.execute("""
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
                
                # 检查并添加 group_id 列 (如果不存在)
                cursor.execute("PRAGMA table_info(message_history)")
                columns = [col[1] for col in cursor.fetchall()]
                if 'group_id' not in columns:
                    cursor.execute("ALTER TABLE message_history ADD COLUMN group_id TEXT")
                    logger.info("已向 message_history 表添加 group_id 列。")

                cursor.execute("CREATE INDEX IF NOT EXISTS idx_message_history_user_id ON message_history(user_id, timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_message_history_group_id ON message_history(group_id, timestamp)") # 新增群聊索引
                
                # 创建常识备忘录表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS common_memo (
                        user_id TEXT PRIMARY KEY,
                        content TEXT,
                        updated_at TEXT
                    )
                """)
                
                # 创建命名备忘录表
                cursor.execute("""
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
                cursor.execute("""
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
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS admin_whitelist (
                        user_id TEXT PRIMARY KEY
                    )
                """)
                
                # 创建系统规则表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS system_rules (
                        user_id TEXT PRIMARY KEY,
                        content TEXT,
                        updated_at TEXT
                    )
                """)
                conn.commit()
                logger.info("数据库表结构检查/初始化完成。")
        except Exception as e:
            logger.critical(f"初始化数据库时发生严重错误: {e}", exc_info=True)
            raise

    # --- 消息历史相关 ---

    async def add_message_to_history(self, user_id: str, message_type: str, content: str, role: str, nickname: Optional[str] = None, group_id: Optional[str] = None):
        """异步添加一条消息到历史记录"""
        timestamp = datetime.now().isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO message_history (user_id, nickname, message_type, content, role, timestamp, group_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, nickname, message_type, content, role, timestamp, group_id)
                )
                conn.commit()
                logger.debug(f"消息已记录: User {user_id}, Role {role}, Group {group_id}")
                if nickname: # 更新昵称缓存
                    self._nickname_cache[user_id] = nickname
        except Exception as e:
            logger.error(f"记录消息历史时出错: {e}", exc_info=True)

    def get_cached_nickname(self, user_id: str) -> Optional[str]:
        """从内存缓存中获取用户昵称"""
        return self._nickname_cache.get(user_id)

    async def get_recent_messages(self, user_id: str, group_id: Optional[str] = None, limit: int = 200, content_max_len: int = 300) -> List[Dict[str, Any]]:
        """
        获取用户或群聊最近的消息历史。
        :param user_id: 用户ID。
        :param group_id: 群ID，如果为None则获取私聊消息。
        :param limit: 消息数量限制。
        :param content_max_len: 单条消息内容的最大长度，超过则截断。
        """
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if group_id:
                    # 获取群聊消息
                    cursor.execute(
                        "SELECT * FROM message_history WHERE group_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (group_id, limit)
                    )
                else:
                    # 获取私聊消息 (group_id 为 NULL 的消息)
                    cursor.execute(
                        "SELECT * FROM message_history WHERE user_id = ? AND group_id IS NULL ORDER BY timestamp DESC LIMIT ?",
                        (user_id, limit)
                    )
                
                messages = []
                for row in cursor.fetchall():
                    msg = dict(row)
                    if 'content' in msg and len(msg['content']) > content_max_len:
                        msg['content'] = msg['content'][:content_max_len] + "..." # 截断消息
                    messages.append(msg)
                return messages
        except Exception as e:
            logger.error(f"获取消息历史时出错: {e}", exc_info=True)
            return []

    async def search_all_chat_history(self, query: str, user_id: Optional[str] = None, nickname: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        在所有聊天记录中搜索指定内容。
        :param query: 搜索关键词。
        :param user_id: 可选，按用户ID过滤。
        :param nickname: 可选，按昵称过滤。
        :param limit: 结果数量限制。
        """
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                sql = "SELECT * FROM message_history WHERE content LIKE ?"
                params = [f"%{query}%"]
                
                if user_id:
                    sql += " AND user_id = ?"
                    params.append(user_id)
                if nickname:
                    sql += " AND nickname LIKE ?"
                    params.append(f"%{nickname}%")
                
                sql += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                
                cursor.execute(sql, tuple(params))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"搜索所有聊天历史时出错: {e}", exc_info=True)
            return []

    # --- 权限与规则相关 ---

    async def is_admin(self, user_id: str) -> bool:
        """检查用户是否在管理员白名单中"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM admin_whitelist WHERE user_id = ?", (user_id,))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"检查管理员权限时出错: {e}", exc_info=True)
            return False

    async def get_system_rules(self, user_id: str) -> Optional[str]:
        """获取指定用户的系统规则。如果不存在，返回 None。"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT content FROM system_rules WHERE user_id = ?", (user_id,))
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"获取系统规则时出错: {e}", exc_info=True)
            return None

    async def save_system_rules(self, user_id: str, content: str):
        """保存或更新指定用户的系统规则。"""
        timestamp = datetime.now().isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO system_rules (user_id, content, updated_at) VALUES (?, ?, ?)",
                    (user_id, content, timestamp)
                )
                conn.commit()
                logger.info(f"用户 {user_id} 的系统规则已更新。")
        except Exception as e:
            logger.error(f"保存系统规则时出错: {e}", exc_info=True)

    # --- 常识备忘录 (Common Memo) ---

    async def get_common_memo_content(self, user_id: str) -> str:
        """获取常识备忘录内容，优先用户专属，否则返回通用"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT content FROM common_memo WHERE user_id = ?", (user_id,))
                result = cursor.fetchone()
                if result and result[0]:
                    return result[0]
                
                cursor.execute("SELECT content FROM common_memo WHERE user_id = 'common'")
                result = cursor.fetchone()
                return result[0] if result else ""
        except Exception as e:
            logger.error(f"获取常识备忘录时出错: {e}", exc_info=True)
            return ""

    async def update_common_memo(self, user_id: str, content: str):
        """更新或创建用户的常识备忘录"""
        timestamp = datetime.now().isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO common_memo (user_id, content, updated_at) VALUES (?, ?, ?)",
                    (user_id, content, timestamp)
                )
                conn.commit()
                logger.info(f"用户 {user_id} 的常识备忘录已更新。")
        except Exception as e:
            logger.error(f"更新常识备忘录时出错: {e}", exc_info=True)

    # --- 命名备忘录 (Named Memos) ---

    async def create_named_memo(self, user_id: str, title: str, capacity: int) -> bool:
        """创建一个新的命名备忘录"""
        now = datetime.now().isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO named_memos (user_id, title, content, capacity, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, title, "", capacity, now, now)
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            logger.warning(f"用户 {user_id} 尝试创建已存在的备忘录 '{title}'")
            return False
        except Exception as e:
            logger.error(f"创建命名备忘录时出错: {e}", exc_info=True)
            return False

    async def update_named_memo(self, user_id: str, title: str, content: str) -> bool:
        """更新命名备忘录的内容"""
        timestamp = datetime.now().isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                # 使用追加模式
                cursor.execute(
                    "UPDATE named_memos SET content = content || ?, updated_at = ? WHERE user_id = ? AND title = ?",
                    ("\n" + content, timestamp, user_id, title)
                )
                if cursor.rowcount == 0:
                    return False # 没有找到匹配的记录
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"更新命名备忘录时出错: {e}", exc_info=True)
            return False

    async def get_named_memo_content(self, user_id: str, title: str) -> Optional[str]:
        """获取指定命名备忘录的内容"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT content FROM named_memos WHERE user_id = ? AND title = ?", (user_id, title))
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"获取命名备忘录内容时出错: {e}", exc_info=True)
            return None

    # --- 为AI上下文构建所需的摘要方法 ---

    async def get_notebooks_summary(self, user_id: str) -> str:
        """获取用户所有Notebook的摘要信息"""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, last_edited, credit_remaining FROM notebooks WHERE user_id = ? ORDER BY last_edited DESC LIMIT 10",
                    (user_id,)
                )
                notebooks = cursor.fetchall()
                if not notebooks:
                    return "无"
                summary_lines = [
                    f"- '{nb['name']}' (额度: {nb['credit_remaining']}, 最近编辑: {nb['last_edited']})"
                    for nb in notebooks
                ]
                return "\n".join(summary_lines)
        except Exception as e:
            logger.error(f"获取Notebooks摘要时出错: {e}", exc_info=True)
            return "获取摘要失败"
            
    async def get_memos_summary(self, user_id: str) -> str:
        """获取用户所有命名备忘录的摘要信息"""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT title, updated_at, length(content) as content_len, capacity FROM named_memos WHERE user_id = ? ORDER BY updated_at DESC LIMIT 10",
                    (user_id,)
                )
                memos = cursor.fetchall()
                if not memos:
                    return "无"
                summary_lines = [
                    f"- '{memo['title']}' (已用/容量: {memo['content_len']}/{memo['capacity']}, 更新于: {memo['updated_at']})"
                    for memo in memos
                ]
                return "\n".join(summary_lines)
        except Exception as e:
            logger.error(f"获取Memos摘要时出错: {e}", exc_info=True)
            return "获取摘要失败"

    # --- GUI 相关的数据获取方法 ---
    # 这些方法是同步的，因为GUI本身是同步的。
    # 在高并发应用中，这些也应该异步并通过队列与GUI通信，但对于此项目，直接查询是可接受的。

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """异步获取所有有记录的用户ID和昵称列表"""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                # 假设我们想获取所有有消息记录的用户，以及他们最近的昵称
                # 这可能需要更复杂的查询，这里简化为只获取user_id
                cursor.execute("SELECT DISTINCT user_id FROM message_history ORDER BY user_id")
                return [{"user_id": row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取用户列表时出错: {e}", exc_info=True)
            return []

    async def get_user_messages(self, user_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        """异步获取指定用户的所有消息"""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM message_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (user_id, limit)
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取用户消息时出错: {e}", exc_info=True)
            return []
