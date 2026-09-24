# -*- coding: utf-8 -*-
"""WAD 的**低内存**读取层。

为什么需要它
------------
`vendor/wadtool.py` 的 `Wad.__init__` 是 `self.raw = f.read()` —— **整个文件读进内存**。
`dotp2014decks/deck_note.py` 的 `Art()` 因此常驻 **4.03 GB**（实测），
`cover.load_art()` 更是每取一张图就重开一个 100MB 的包。

本模块做两件事：

1. `MMapWad` —— 把 `self.raw` 换成 `mmap`。`_parse()` 全程只读
   （`struct.unpack_from` + 切片），所以继承即可，字节接口完全一致，
   但内存交给操作系统按页调度 —— 打开 172MB 的 CW 包不再吃掉 172MB 常驻。

2. `WadPool` —— WAD 句柄的 LRU。游戏目录 71 个包、合计 4 GB，
   不可能全开着。给个上限（默认 6），按最近使用淘汰，`close()` 时统一释放。

用法::

    pool = WadPool()
    w = pool.get("DATA_CORE.WAD")        # -> MMapWad
    raw = w.read(w.files[0])
"""

import os
import mmap
import threading
from collections import OrderedDict

from vendor.wadtool import Wad


class MMapWad(Wad):
    """和 `Wad` 完全同接口，只是背后是 mmap 而不是一整块 bytes。"""

    __slots__ = ("_fh", "_mm")

    def __init__(self, path):
        self.path = path
        self._fh = open(path, "rb")
        try:
            self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:
            # 空文件 —— mmap 不接受长度 0
            self._fh.close()
            raise ValueError("空文件：%s" % path)
        self.raw = self._mm
        try:
            self._parse()
        except Exception:
            self.close()
            raise

    def close(self):
        mm = getattr(self, "_mm", None)
        if mm is not None:
            try:
                mm.close()
            except (BufferError, ValueError):
                pass          # 还有切片引用着，交给 GC
            self._mm = None
        fh = getattr(self, "_fh", None)
        if fh is not None:
            fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class WadPool(object):
    """WAD 句柄池，LRU 淘汰。**线程安全**（缩略图预生成会用到多线程）。"""

    def __init__(self, game_dir, limit=6):
        self.game_dir = game_dir
        self.limit = limit
        self._lock = threading.RLock()
        self._open = OrderedDict()          # basename -> MMapWad
        self._missing = set()               # 打不开的，别每次重试

    def _path(self, name):
        return os.path.join(self.game_dir, name)

    def get(self, name):
        """按 WAD 文件名取句柄；打不开返回 None。"""
        with self._lock:
            w = self._open.get(name)
            if w is not None:
                self._open.move_to_end(name)
                return w
            if name in self._missing:
                return None
            p = self._path(name)
            if not os.path.exists(p):
                self._missing.add(name)
                return None
            try:
                w = MMapWad(p)
            except Exception:
                self._missing.add(name)
                return None
            self._open[name] = w
            while len(self._open) > self.limit:
                _old, victim = self._open.popitem(last=False)
                victim.close()
            return w

    def close_all(self):
        with self._lock:
            for w in self._open.values():
                w.close()
            self._open.clear()

    @property
    def stats(self):
        with self._lock:
            return {"open": list(self._open), "limit": self.limit,
                    "missing": sorted(self._missing)}


def main():
    """自检：mmap 版和原版解析出的文件表必须**逐条一致**。"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import paths

    pool = WadPool(paths.GAME)
    names = sorted(n for n in os.listdir(paths.GAME)
                   if n.lower().endswith(".wad"))
    bad = 0
    for n in names:
        a = pool.get(n)
        if a is None:
            print("  !! 打不开 %s" % n)
            bad += 1
            continue
        try:
            b = Wad(os.path.join(paths.GAME, n))
        except Exception as e:
            print("  !! 原版也打不开 %s: %s" % (n, e))
            bad += 1
            continue
        ok = (len(a.files) == len(b.files)
              and a.header_xml == b.header_xml
              and a.data_offsets == b.data_offsets)
        if ok:
            for x, y in zip(a.files, b.files):
                if (x.path, x.size, x.offset_index) != (y.path, y.size, y.offset_index):
                    ok = False
                    break
        print("  %-34s %5d 条目  %s" % (n, len(a.files), "✓" if ok else "✗ 不一致"))
        if not ok:
            bad += 1
        del b
    pool.close_all()
    print("\n%d 个包，%d 个有问题" % (len(names), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
