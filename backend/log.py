# -*- coding: utf-8 -*-
"""日志与错误处理 —— **出问题时能查到原因**是第一目标。

做四件事：

1. **落盘**：`data/logs/app.log`，5MB × 3 轮转。程序崩了、卡了、图出不来，
   翻这个文件就能看到上下文。
2. **内存环形缓冲**：最近 300 条 WARNING 以上，带 traceback。界面上的
   「诊断」面板读它 —— 用户不用去翻文件目录，截图就能发给开发者。
3. **兜底钩子**：`sys.excepthook` / `threading.excepthook`。没被 try 包住的异常
   也一定进日志，不会「程序一闪就没了，什么都没有」。
4. **`guard()` 装饰器**：给「单张卡坏了不该拖垮整批」这类场景用 ——
   捕获 + 记日志 + 返回默认值，让批处理继续跑。

用法::

    from log import get_logger, guard, recent
    log = get_logger(__name__)
    log.info("建索引：%d 张", n)

    @guard(default=None, what="解码插画")
    def decode(key): ...
"""

import os
import sys
import time
import logging
import traceback
import threading
import collections
from logging.handlers import RotatingFileHandler

import paths

LOG_DIR = os.path.join(paths.DATA, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

RING_SIZE = 300
_setup_lock = threading.Lock()
_setup_done = False

# 最近 WARNING 以上的记录，供界面「诊断」面板读取
_ring = collections.deque(maxlen=RING_SIZE)
_ring_lock = threading.Lock()


class _RingHandler(logging.Handler):
    """把 WARNING 以上的记录抄一份进内存环形缓冲。"""

    def emit(self, record):
        try:
            entry = {
                "t": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "where": "%s:%d" % (record.module, record.lineno),
                "msg": record.getMessage(),
            }
            if record.exc_info:
                entry["trace"] = "".join(
                    traceback.format_exception(*record.exc_info))[-4000:]
            with _ring_lock:
                _ring.append(entry)
        except Exception:
            pass          # 日志自己出错绝不能反过来炸程序


def _utf8_console():
    """把 stdout/stderr 拧成 UTF-8（`errors="replace"`）。

    Windows 控制台默认是 GBK（cp936），而 `tools/*.py` 里有一堆带 `✓` `→` 的
    进度输出 —— 编码不了会直接抛 `UnicodeEncodeError` **把整个程序打挂**，
    而且崩在启动阶段（`main.py` 建索引的时候），看起来像程序坏了。
    （真踩过：改了游戏目录触发 `frames` 重建，`export_frames.py:161` 那个 `✓` 一崩到底。）
    """
    # `--windowed` 打包（PyInstaller）后**没有控制台**，`sys.stdout` / `sys.stderr`
    # 会是 `None`。此时满地的 `print()` 会抛 AttributeError 把程序打挂 ——
    # 而且是在启动建索引那条路上，用户看到的就是「双击没反应」。
    # 塞一个什么都吃的空写入器，print 照常跑，只是没人看。
    class _Null(object):
        encoding = "utf-8"
        errors = "replace"

        def write(self, *_a):
            return 0

        def flush(self):
            pass

        def isatty(self):
            return False

        def reconfigure(self, **_kw):
            pass

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _Null())

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass          # 被重定向成非 TextIOWrapper 之类，不管它


def setup(level=logging.INFO):
    """初始化。可重复调用，只生效一次。"""
    global _setup_done
    with _setup_lock:
        if _setup_done:
            return
        _setup_done = True

    _utf8_console()
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s:%(lineno)d — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024,
                             backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    rh = _RingHandler()
    rh.setLevel(logging.WARNING)
    root.addHandler(rh)

    # 没被 try 包住的异常也要留下痕迹
    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        root.critical("未捕获异常", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    # 第三方库的 DEBUG 会把日志淹掉（PIL 光是 import 就刷几十行
    # `Importing XxxImagePlugin`），只留它们的警告以上。
    for noisy in ("PIL", "uvicorn", "uvicorn.error", "uvicorn.access",
                  "asyncio", "watchfiles", "multipart", "python_multipart",
                  "webview", "clr_loader", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    sys.excepthook = _hook

    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        root.critical("线程 %s 未捕获异常",
                      getattr(args.thread, "name", "?"),
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _thread_hook

    root.info("=" * 70)
    root.info("日志启动 -> %s", LOG_FILE)


def get_logger(name):
    setup()
    return logging.getLogger(name)


def recent(limit=100, level=None):
    """给界面用的最近错误。新→旧。"""
    with _ring_lock:
        items = list(_ring)
    if level:
        items = [x for x in items if x["level"] == level]
    return list(reversed(items))[:limit]


def clear():
    with _ring_lock:
        _ring.clear()


def guard(default=None, what="操作", reraise=False, logger=None):
    """包一层 try —— 单条失败不要拖垮整批。

    「解一张图炸了」不该让整个索引脚本挂掉，也不该让一个 API 请求 500。
    这个装饰器的语义是：**记下来，继续跑**。

    `reraise=True` 用于「失败必须中断」的场合（比如写盘），
    但依然会先记日志再抛。
    """
    log = logger or get_logger("guard")

    def deco(fn):
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:
                log.warning("%s失败：%s: %s", what, type(e).__name__, e,
                            exc_info=True)
                if reraise:
                    raise
                return default() if callable(default) else default
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def logfile():
    return LOG_FILE
