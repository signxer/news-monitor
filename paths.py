#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台路径管理模块
使用 platformdirs 获取各平台标准数据目录，确保打包后路径正确。
"""

import os
import sys
import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_packaged():
    """判断是否在 PyInstaller 打包环境中运行"""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_app_dir() -> Path:
    """获取应用数据目录（配置、数据库、日志等）

    - macOS:   ~/Library/Application Support/NewsMonitor/
    - Windows: %APPDATA%/NewsMonitor/
    - Linux:   ~/.local/share/news-monitor/
    """
    try:
        from platformdirs import user_data_dir
        data_dir = Path(user_data_dir(appname='news-monitor', appauthor='NewsMonitor'))
    except ImportError:
        # fallback: 使用用户主目录
        if sys.platform == 'darwin':
            data_dir = Path.home() / 'Library' / 'Application Support' / 'NewsMonitor'
        elif sys.platform == 'win32':
            appdata = os.environ.get('APPDATA', str(Path.home()))
            data_dir = Path(appdata) / 'NewsMonitor'
        else:
            data_dir = Path.home() / '.local' / 'share' / 'news-monitor'

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_config_path() -> Path:
    """获取配置文件路径"""
    return get_app_dir() / 'config.json'


def get_db_path() -> Path:
    """获取数据库文件路径"""
    return get_app_dir() / 'news.db'


def get_log_path() -> Path:
    """获取日志文件路径"""
    return get_app_dir() / 'news_monitor.log'


def get_template_dir() -> Path:
    """获取模板目录路径

    打包环境中从 sys._MEIPASS 获取，开发环境中从项目根目录获取。
    """
    if _is_packaged():
        # PyInstaller 解压目录
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).parent

    template_dir = base_dir / 'templates'
    if template_dir.exists():
        return template_dir

    # fallback
    logger.warning(f"模板目录不存在: {template_dir}，使用当前目录")
    return Path(__file__).parent / 'templates'


def migrate_from_cwd():
    """首次运行时，将当前目录下的旧文件迁移到新路径

    向后兼容：如果用户之前在项目目录下运行过，config.json/news.db 会在 cwd 下。
    """
    app_dir = get_app_dir()

    migrations = [
        ('config.json', get_config_path()),
        ('news.db', get_db_path()),
        ('news_monitor.log', get_log_path()),
    ]

    for src_name, dst_path in migrations:
        src_path = Path.cwd() / src_name
        if src_path.exists() and not dst_path.exists():
            try:
                shutil.copy2(str(src_path), str(dst_path))
                logger.info(f"已迁移 {src_name} 到 {dst_path}")
            except Exception as e:
                logger.warning(f"迁移 {src_name} 失败: {e}")
