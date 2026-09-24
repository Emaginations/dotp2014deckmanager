# -*- coding: utf-8 -*-
"""文件写入的小工具。

为什么需要这个
--------------
项目里「先写 `.tmp` 再 `os.replace` 原子替换」的模式出现在 4 个地方
（`deck.py` / `deckwrite.py` / `wadtools.py` / `packer.py`），但它在 Windows 上
**会偶发失败**：

    PermissionError: [WinError 5] 拒绝访问。: 'xxx.json.tmp' -> 'xxx.json'

原因不是权限，是**并发读**。CPython 在 Windows 上打开文件用的是
`FILE_SHARE_READ | FILE_SHARE_WRITE`，**没有 `FILE_SHARE_DELETE`** ——
所以只要有任何进程/线程正把目标文件开着读，`os.replace` 就会被拒。
本程序里这个读几乎必然存在：前端在操作后会刷新牌组列表
（`GET /api/decks` → `deck.list_all()` 逐个 `open()`），正好和写入撞上。

真踩过：打包时 `deckmod.save()` 偶发 WinError 5，5 次里中 1 次。

对策就是**小步重试** —— 读操作都是毫秒级的，等一下就好了。
"""

import os
import time

from log import get_logger

log = get_logger("fsx")


def replace(src, dst, tries=8, delay=0.06):
    """`os.replace` + 重试。返回是否成功。

    Windows 上目标文件被别的线程/进程读着时会 `PermissionError`，
    读完了就能换 —— 所以等一下再试，别直接失败。
    """
    last = None
    for i in range(tries):
        try:
            os.replace(src, dst)
            if i:
                log.info("replace 第 %d 次成功：%s", i + 1, os.path.basename(dst))
            return True
        except PermissionError as e:
            last = e
            time.sleep(delay * (i + 1))
        except OSError as e:
            # 磁盘满之类的别硬试
            last = e
            break
    log.error("替换文件失败 %s -> %s：%s", src, dst, last)
    raise last


def write_bytes(path, data):
    """原子写二进制。临时文件名带 pid + 线程 id，避免并发写撞同一个 `.tmp`。"""
    tmp = "%s.tmp%d.%d" % (path, os.getpid(), _tid())
    with open(tmp, "wb") as f:
        f.write(data)
    replace(tmp, path)
    return len(data)


def write_text(path, text, encoding="utf-8"):
    """原子写文本。"""
    tmp = "%s.tmp%d.%d" % (path, os.getpid(), _tid())
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
    replace(tmp, path)
    return len(text)


def _tid():
    import threading
    return threading.get_ident() & 0xFFFF
