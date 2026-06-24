"""WuYou 热更新文件监视器。

通过后台线程定时扫描指定目录的文件修改时间（mtime），检测到变更后
触发回调函数清除内存缓存（如语言包缓存、主题 CSS 缓存、插件缓存）。

入口函数：
- ``watch_directory(path, callback)`` — 注册监视目录与回调
- ``start_watcher(interval_seconds)`` — 启动后台扫描线程
- ``reload_static_assets()`` — 清除静态资源缓存
- ``reload_plugins()`` — 重新加载插件
"""

import logging
import os
import time
import threading
from pathlib import Path

from typing import Callable

logger = logging.getLogger(__name__)

_watched_dirs: list[Path] = []
_reload_callbacks: list[Callable] = []
_mtimes: dict[str, float] = {}


def watch_directory(path: Path, callback: Callable):
    """注册一个监视目录及变更回调。

    Args:
        path: 要监视的目录路径。
        callback: 检测到文件变更时调用的无参回调函数。
    """
    _watched_dirs.append(path)
    _reload_callbacks.append(callback)


def _scan():
    """Scan all watched directories for file changes."""
    changed = False
    for d in _watched_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            key = str(f.absolute())
            mtime = f.stat().st_mtime
            if _mtimes.get(key) != mtime:
                _mtimes[key] = mtime
                changed = True
                logger.debug("Hot-reload: %s changed", key)
    if changed:
        for cb in _reload_callbacks:
            try:
                cb()
            except Exception as exc:
                logger.exception("Hot-reload callback failed: %s", exc)


def _watcher_loop(interval_seconds: int = 5):
    """Background thread: scan every N seconds."""
    time.sleep(3)
    while True:
        try:
            _scan()
        except Exception:
            logger.exception("Hot-reload scan error")
        time.sleep(interval_seconds)


def start_watcher(interval_seconds: int = 5):
    """启动热更新后台监视线程。

    Args:
        interval_seconds: 文件扫描间隔（秒），默认 5 秒。
    """
    t = threading.Thread(target=_watcher_loop, args=(interval_seconds,), daemon=True)
    t.start()
    logger.info("Hot-reload watcher started (interval=%ss)", interval_seconds)


def reload_static_assets():
    """清除语言包和主题 CSS 等静态资源的内存缓存。"""
    # Clear locale dict caches, theme CSS caches, etc.
    from app.services import locale_cache
    from app.services import theme_cache
    locale_cache.clear()
    theme_cache.clear()
    logger.info("Hot-reload: static asset caches cleared")


def reload_plugins():
    """从磁盘重新加载已安装插件。"""
    try:
        from app.services.plugin_loader import reload_plugins as _reload
        _reload()
    except ImportError:
        logger.debug("Hot-reload: plugin_loader not available, skipping plugin reload")
    except Exception as exc:
        logger.exception("Hot-reload: plugin reload failed: %s", exc)
    else:
        logger.info("Hot-reload: plugins reloaded")
