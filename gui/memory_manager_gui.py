"""
AnZaiBot 记忆管理器 GUI
负责展示和管理数据库中的记忆数据。
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import asyncio
import os
from typing import List, Dict, Any

from services.memory_manager import MemoryManager
from utils.logger import gui_logger as logger

class MemoryManagerGUI:
    """
    GUI主类，负责所有界面的创建和逻辑处理。
    """
    def __init__(self, root: tk.Tk, memory_manager: MemoryManager, shutdown_event: threading.Event = None):
        self.root = root
        self.memory_manager = memory_manager
        self.shutdown_event = shutdown_event
        
        # 为了在同步的Tkinter中调用异步方法，我们需要一个正在运行的事件循环
        # 如果主程序没有运行事件循环，我们需要自己创建一个
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            threading.Thread(target=self.loop.run_forever, daemon=True).start()

        self.selected_user_id = None
        self.current_view = "messages"  # messages, memos, notebooks, etc.
        
        self.root.title("AnZaiBot 记忆管理器")
        self.root.geometry("1024x768")
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._setup_styles()
        self._create_widgets()
        self._bind_shortcuts()
        
        # 初始加载数据
        self.load_users()
        self.refresh_data()

    def _run_async(self, coro):
        """在后台事件循环中安全地运行一个协程"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def _setup_styles(self):
        """配置UI样式"""
        style = ttk.Style()
        style.theme_use('clam') # clam主题看起来更现代
        style.configure("Treeview", rowheight=25, font=('Microsoft YaHei UI', 9))
        style.configure("Treeview.Heading", font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure("TButton", padding=5, font=('Microsoft YaHei UI', 10))
        style.configure("TLabelframe.Label", font=('Microsoft YaHei UI', 10, 'bold'))

    def _create_widgets(self):
        """创建所有UI组件"""
        # --- 主框架 ---
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.rowconfigure(2, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # --- 工具栏 ---
        toolbar = ttk.Frame(main_frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        # 用户选择
        user_frame = ttk.LabelFrame(toolbar, text="用户")
        user_frame.pack(side=tk.LEFT, padx=(0, 10), fill=tk.Y)
        self.user_var = tk.StringVar()
        self.user_selector = ttk.Combobox(user_frame, textvariable=self.user_var, state="readonly", width=20)
        self.user_selector.pack(side=tk.LEFT, padx=5, pady=5)
        self.user_selector.bind("<<ComboboxSelected>>", lambda e: self.select_user())

        # 视图切换
        view_frame = ttk.LabelFrame(toolbar, text="视图")
        view_frame.pack(side=tk.LEFT, padx=(0, 10), fill=tk.Y)
        ttk.Button(view_frame, text="消息历史", command=lambda: self.switch_view("messages")).pack(side=tk.LEFT, padx=2, pady=5)
        ttk.Button(view_frame, text="备忘录", command=lambda: self.switch_view("memos")).pack(side=tk.LEFT, padx=2, pady=5)
        ttk.Button(view_frame, text="系统设定", command=lambda: self.switch_view("system_settings")).pack(side=tk.LEFT, padx=2, pady=5)
        
        # 操作按钮
        action_frame = ttk.LabelFrame(toolbar, text="操作")
        action_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.refresh_button = ttk.Button(action_frame, text="刷新", command=self.refresh_data)
        self.refresh_button.pack(side=tk.LEFT, padx=2, pady=5)
        
        # --- 搜索栏 ---
        search_frame = ttk.Frame(main_frame)
        search_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        search_frame.columnconfigure(0, weight=1)
        self.search_entry = ttk.Entry(search_frame)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.search_button = ttk.Button(search_frame, text="搜索", command=self.search_data)
        self.search_button.pack(side=tk.LEFT)

        # --- 数据表格 ---
        tree_frame = ttk.Frame(main_frame)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        
        self.tree = ttk.Treeview(tree_frame, show="headings")
        self.tree.grid(row=0, column=0, sticky="nsew")
        
        # 滚动条
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # --- 状态栏 ---
        self.status_bar = ttk.Label(main_frame, text="就绪", anchor=tk.W, relief=tk.SUNKEN)
        self.status_bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))

    def _bind_shortcuts(self):
        """绑定快捷键"""
        self.root.bind("<Control-r>", lambda e: self.refresh_data())
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.root.bind("<Return>", lambda e: self.search_data() if self.root.focus_get() == self.search_entry else None)

    def _update_status(self, message: str, clear_after_ms: int = 0):
        """更新状态栏信息"""
        self.status_bar.config(text=message)
        if clear_after_ms > 0:
            self.root.after(clear_after_ms, lambda: self.status_bar.config(text="就绪"))

    def _set_ui_state(self, state: str):
        """设置UI为 'normal' 或 'disabled' 状态"""
        widgets = [self.user_selector, self.refresh_button, self.search_button, self.tree]
        # 遍历所有按钮
        for child in self.root.winfo_children():
            if isinstance(child, ttk.Frame):
                for grandchild in child.winfo_children():
                    if isinstance(grandchild, ttk.Button):
                        widgets.append(grandchild)

        for widget in widgets:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass # 有些组件如Treeview没有state属性，直接忽略

    def _run_task_with_ui_lock(self, task_func, *args, **kwargs):
        """在新线程中运行任务，并锁定/解锁UI"""
        def task_wrapper():
            self._set_ui_state('disabled')
            self._update_status("正在处理，请稍候...")
            try:
                task_func(*args, **kwargs)
            except Exception as e:
                logger.error(f"GUI任务执行失败: {e}", exc_info=True)
                messagebox.showerror("任务失败", str(e))
            finally:
                self._set_ui_state('normal')
                self._update_status("就绪")
        
        threading.Thread(target=task_wrapper, daemon=True).start()

    def switch_view(self, view_name: str):
        """切换视图 (消息历史, 备忘录等)"""
        if self.current_view == view_name:
            return
        
        self.current_view = view_name
        self.refresh_data()

    def refresh_data(self):
        """刷新表格中的数据"""
        self._run_task_with_ui_lock(self._refresh_data_sync)
    
    def _refresh_data_sync(self):
        """同步刷新数据的核心逻辑"""
        # 清空现有表格
        for item in self.tree.get_children():
            self.tree.delete(item)

        if self.current_view == "messages":
            self._display_messages()
        elif self.current_view == "memos":
            self._display_memos()
        elif self.current_view == "system_settings":
            self._display_system_settings()

    def _display_messages(self):
        """显示消息历史"""
        cols = ("ID", "用户ID", "昵称", "类型", "角色", "内容", "时间")
        self.tree.config(columns=cols)
        for col in cols:
            self.tree.heading(col, text=col)
        self.tree.column("ID", width=60, anchor='center')
        self.tree.column("用户ID", width=120)
        self.tree.column("昵称", width=120)
        self.tree.column("类型", width=80, anchor='center')
        self.tree.column("角色", width=80, anchor='center')
        self.tree.column("内容", width=400)
        self.tree.column("时间", width=160)

        user_id = self.selected_user_id
        if not user_id:
            self._update_status("请先选择一个用户来查看消息历史。")
            return
        
        # 这是同步调用异步方法的关键
        messages: List[Dict] = self._run_async(self.memory_manager.get_recent_messages(user_id, limit=500))
        
        for msg in reversed(messages): # reversed чтобы最新的在下面
            values = (
                msg.get("id", ""),
                msg.get("user_id", ""),
                msg.get("nickname", ""),
                msg.get("message_type", ""),
                msg.get("role", ""),
                msg.get("content", "").replace("\n", " "), # 替换换行符防止UI错乱
                msg.get("timestamp", "")
            )
            self.tree.insert("", tk.END, values=values)
        self._update_status(f"已加载用户 {user_id} 的 {len(messages)} 条最新消息。")

    def _display_memos(self):
        """显示备忘录数据"""
        # (此处省略备忘录视图的代码，与上面消息视图类似)
        # 您需要先在MemoryManager中实现获取备忘录的异步方法
        self._update_status("备忘录视图待实现。")
        pass

    def _display_system_settings(self):
        """显示系统设定界面"""
        # 清空现有表格区域，准备显示新的UI
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree.config(columns=()) # 清空列配置
        
        # 创建一个框架来放置系统设定UI
        settings_frame = ttk.Frame(self.tree)
        settings_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        settings_frame.columnconfigure(0, weight=1)
        settings_frame.rowconfigure(1, weight=1)

        ttk.Label(settings_frame, text="系统提示词 (System Instruction):", font=('Microsoft YaHei UI', 10, 'bold')).grid(row=0, column=0, sticky="nw", pady=(0, 5))
        
        self.system_instruction_text = tk.Text(settings_frame, wrap=tk.WORD, height=15, font=('Microsoft YaHei UI', 10))
        self.system_instruction_text.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        
        # 添加滚动条
        text_vsb = ttk.Scrollbar(settings_frame, orient="vertical", command=self.system_instruction_text.yview)
        text_vsb.grid(row=1, column=1, sticky="ns")
        self.system_instruction_text.config(yscrollcommand=text_vsb.set)

        save_button = ttk.Button(settings_frame, text="保存系统设定", command=self._save_system_instruction)
        save_button.grid(row=2, column=0, sticky="ew", pady=(5, 0))

        self._load_system_instruction() # 加载现有设定

    def _load_system_instruction(self):
        """从MemoryManager加载系统提示词并显示"""
        self._run_task_with_ui_lock(self.__load_system_instruction_sync)

    def __load_system_instruction_sync(self):
        """同步加载系统提示词的核心逻辑"""
        try:
            # 假设有一个全局的系统提示词，或者与用户ID关联
            # 这里我们先假设是全局的，或者使用一个默认的user_id来存储
            # 实际应用中，系统提示词可能与特定用户无关，或者有一个特殊的“系统”用户ID
            system_instruction = self._run_async(self.memory_manager.get_system_rules("global_system_user")) # 假设一个全局用户ID
            self.system_instruction_text.delete(1.0, tk.END)
            self.system_instruction_text.insert(tk.END, system_instruction if system_instruction else "")
            self._update_status("系统提示词已加载。")
        except Exception as e:
            logger.error(f"加载系统提示词失败: {e}", exc_info=True)
            messagebox.showerror("加载失败", f"加载系统提示词失败: {e}")
            self._update_status("加载系统提示词失败。")

    def _save_system_instruction(self):
        """保存系统提示词到MemoryManager"""
        self._run_task_with_ui_lock(self.__save_system_instruction_sync)

    def __save_system_instruction_sync(self):
        """同步保存系统提示词的核心逻辑"""
        try:
            new_instruction = self.system_instruction_text.get(1.0, tk.END).strip()
            self._run_async(self.memory_manager.save_system_rules("global_system_user", new_instruction)) # 假设一个全局用户ID
            self._update_status("系统提示词已保存。", clear_after_ms=3000)
        except Exception as e:
            logger.error(f"保存系统提示词失败: {e}", exc_info=True)
            messagebox.showerror("保存失败", f"保存系统提示词失败: {e}")
            self._update_status("保存系统提示词失败。")

    def load_users(self):
        """加载所有有记录的用户到下拉框"""
        self._run_task_with_ui_lock(self.__load_users_sync)

    def __load_users_sync(self):
        """同步加载用户列表的核心逻辑"""
        try:
            all_users = self._run_async(self.memory_manager.get_all_users()) # 假设 MemoryManager 提供了异步接口
            user_ids = [user['user_id'] for user in all_users]
            self.user_selector['values'] = user_ids
            if user_ids and not self.selected_user_id:
                self.user_var.set(user_ids[0])
                self.selected_user_id = user_ids[0]
            self._update_status(f"已加载 {len(user_ids)} 个用户。")
        except Exception as e:
            logger.error(f"加载用户列表失败: {e}", exc_info=True)
            messagebox.showerror("加载失败", f"加载用户列表失败: {e}")
            self._update_status("加载用户列表失败。")

    def select_user(self):
        """当用户从下拉框选择一个用户时触发"""
        self.selected_user_id = self.user_var.get()
        if self.selected_user_id:
            logger.info(f"GUI切换到用户: {self.selected_user_id}")
            self.refresh_data()

    def search_data(self):
        """根据搜索框内容进行搜索"""
        keyword = self.search_entry.get().strip()
        if not keyword:
            return
        self._run_task_with_ui_lock(self._search_data_sync, keyword)

    def _search_data_sync(self, keyword: str):
        """同步搜索数据的核心逻辑"""
        # (此处省略搜索逻辑，需要MemoryManager提供搜索接口)
        self._update_status(f"搜索功能待实现，关键词: {keyword}")
        pass

    def on_closing(self):
        """处理窗口关闭事件"""
        if messagebox.askokcancel("退出", "确定要关闭 AnZaiBot 记忆管理器吗？"):
            logger.info("GUI 正在关闭...")
            if self.shutdown_event:
                logger.info("GUI 发送关闭信号到主应用。")
                self.shutdown_event.set()
            
            # 停止内部事件循环
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            
            self.root.destroy()
            logger.info("GUI 已关闭。")

def run_gui(memory_manager: MemoryManager, shutdown_event: threading.Event = None):
    """
    启动GUI的入口函数。
    :param memory_manager: 必须传入一个 MemoryManager 的实例。
    :param shutdown_event: 用于通知主程序关闭的线程事件。
    """
    try:
        root = tk.Tk()
        # 传递 memory_manager 实例
        MemoryManagerGUI(root, memory_manager, shutdown_event)
        root.mainloop()
    except Exception as e:
        logger.critical(f"启动GUI时发生致命错误: {e}", exc_info=True)
        # 可以在这里弹出一个简单的错误窗口
        messagebox.showerror("GUI 启动失败", f"发生严重错误: {e}\n请查看 logs/gui.log 获取详情。")
