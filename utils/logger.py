"""
统一的日志配置模块
提供分模块的logger实例，确保日志格式统一并能输出到对应文件。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
import sys

def setup_logger(name: str, log_file: str = None, level=logging.INFO) -> logging.Logger:
    """
    配置一个logger实例。
    :param name: logger名称
    :param log_file: 日志文件路径。如果为None，则只输出到控制台。
    :param level: 日志级别
    """
    # 嘿，老铁，整一个日志格式，时间-名字-级别-内容，看着专业！
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger = logging.getLogger(name)
    logger.setLevel(level)
    # 防止日志冒泡到root logger，避免重复打印
    logger.propagate = False

    # 如果已经有处理器了，就别再加了，先清掉
    if logger.hasHandlers():
        logger.handlers.clear()

    # 控制台处理器，打工人总得看看实时输出吧
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件处理器，万一崩了还能翻记录
    if log_file:
        # 确认放日志的文件夹在不在，不在就建一个
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 用RotatingFileHandler，日志太大了就自动分文件，省得硬盘炸了
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB一个文件
            backupCount=5,           # 最多留5个备份
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

# --- 全局Logger实例 ---
# 确保 'logs' 目录存在
if not os.path.exists('logs'):
    os.makedirs('logs')

# 主程序日志
main_logger = setup_logger('main', 'logs/main.log')

# go-cqhttp通信日志
cqhttp_logger = setup_logger('cqhttp', 'logs/cqhttp.log')

# 调度器和进程管理日志
scheduler_logger = setup_logger('scheduler', 'logs/scheduler.log')

# AI相关日志
ai_logger = setup_logger('ai', 'logs/ai.log')

# 内存管理日志
memory_logger = setup_logger('memory', 'logs/memory.log')

# 上下文管理日志
context_logger = setup_logger('context', 'logs/context.log')

# GUI日志
gui_logger = setup_logger('gui', 'logs/gui.log')