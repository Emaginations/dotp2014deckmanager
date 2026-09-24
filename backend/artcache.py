# -*- coding: utf-8 -*-
"""取插画 + 缩略图缓存。

为什么不能照抄 `dotp2014decks`
-------------------------------
两个实测过的坑：

1. `Wad.__init__` 是 `self.raw = f.read()` —— **整个文件读进内存**。
   `deck_note.Art()` 因此常驻 **4.03 GB**。这里走 `wadlite.MMapWad`（mmap），
   打开 172MB 的包不吃 172MB 常驻。
2. `cover.load_art()` **每取一张图就重开一个 100MB 的包**。2.2 万张图走那条路会卡死。
   这里靠 `WadPool` 的句柄 LRU + 每个包一次的 `路径 -> File` 字典。

再加一层**磁盘缩略图缓存**：解码一张 512×376 约 8ms，2.2 万张 ≈ 3 分钟纯 CPU。
缩略图按需生成、落盘、二次访问直接读文件。

缩略图分片存放（`thumbs/<前两位>/<key>.webp`）—— 2 万多个文件堆一个目录下，
Windows 的资源管理器和某些工具会很难受。
"""

import os
import io
import json
import time
import threading
import collections

from PIL import Image

import paths
import settings
import tex
from log import get_logger
from wadlite import WadPool

log = get_logger("artcache")


class ArtCache(object):
    def __init__(self, game_dir=None):
        self.game_dir = game_dir or settings.game_dir()
        self.pool = WadPool(self.game_dir, limit=5)
        self._lock = threading.RLock()
        self._files = {}          # wad 名 -> {路径(大写): File}
        self._inflight = set()    # 正在生成缩略图的 key，避免重复劳动

        idx = self._load_index()
        self.art = idx.get("art") or {}          # ARTID -> [wad, path]
        self.by_key = idx.get("by_key") or {}    # 卡牌 FILENAME -> ARTID
        self.box = self._load_box_index()        # 牌盒封面名 -> [wad, path]
        n = len(self.art)
        log.info("插画索引：%d 张图 / %d 张卡有映射 / %d 张牌盒封面",
                 n, len(self.by_key), len(self.box))

        cfg = settings.load()
        self.thumb_w = int(cfg.get("thumb_width", 256))
        self.thumb_q = int(cfg.get("thumb_quality", 78))
        self.thumb_fmt = str(cfg.get("thumb_format", "WEBP")).upper()

    def _load_index(self):
        if not os.path.exists(paths.ARTIDX_JSON):
            log.error("插画索引不存在：%s（跑 tools/rebuild.py）", paths.ARTIDX_JSON)
            return {}
        try:
            with open(paths.ARTIDX_JSON, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error("插画索引读不了：%s", e, exc_info=True)
            return {}

    def _load_box_index(self):
        if not os.path.exists(paths.DECKBOX_JSON):
            log.warning("牌盒封面索引不存在：%s（跑 tools/build_deckbox.py）",
                        paths.DECKBOX_JSON)
            return {}
        try:
            with open(paths.DECKBOX_JSON, encoding="utf-8") as f:
                return json.load(f).get("box") or {}
        except Exception as e:
            log.error("牌盒封面索引读不了：%s", e, exc_info=True)
            return {}

    # ---------------------------------------------------------------- 取图

    def _file_map(self, wad_name, w):
        """包名 -> {路径(大写): File}。每个包只建一次。"""
        with self._lock:
            m = self._files.get(wad_name)
            if m is None:
                m = {f.path.replace("\\", "/").upper(): f for f in w.files}
                self._files[wad_name] = m
            return m

    def _open(self, wad_name, path, what):
        """(wad 名, 包内路径) -> (Wad, File)；任何一步失败都返回 (None, None)。"""
        w = self.pool.get(wad_name)
        if w is None:
            log.warning("打不开图包 %s（取 %s）", wad_name, what)
            return None, None
        f = self._file_map(wad_name, w).get(path.replace("\\", "/").upper())
        if f is None:
            log.warning("图包里找不到条目 %s（取 %s）", path, what)
            return None, None
        return w, f

    def _entry(self, key):
        """卡牌 key -> (Wad, File)；找不到返回 (None, None)。"""
        aid = self.by_key.get(key)
        if not aid:
            return None, None
        e = self.art.get(aid)
        if not e:
            return None, None
        return self._open(e[0], e[1], key)

    def raw(self, key):
        """卡牌 key -> 原始 TDX bytes；没有返回 None。"""
        w, f = self._entry(key)
        if w is None:
            return None
        try:
            return w.read(f)
        except Exception as e:
            log.warning("读图失败 %s: %s", key, e)
            return None

    def image(self, key, size="full"):
        """卡牌 key -> PIL Image（RGBA）；没有返回 None。

        `size` 取 `"full"`（原图）或 `"thumb"`（网格缩略图）。
        """
        raw = self.raw(key)
        if raw is None:
            return None
        try:
            img = tex.decode(raw)
        except Exception as e:
            log.warning("解码失败 %s: %s", key, e)
            return None
        if size == "thumb" and img.width > self.thumb_w:
            h = max(1, int(img.height * self.thumb_w / float(img.width)))
            img = img.resize((self.thumb_w, h), Image.LANCZOS)
        return img

    # ---------------------------------------------------------------- 牌盒封面

    def box_image(self, name):
        """牌盒封面名（牌组 XML 里的 `deck_box_image`）-> PIL Image；没有返回 None。

        **只裁正面，而且只取上面一截**：整张贴图是 512×512 的 3D 盒子渲染，
        四周大量透明留白、左边还有一条带鹏洛客符号的盒脊。原样铺到列表条上
        会是个飘着的盒子，而且没法做左侧溶图渐变。
        正面整块是竖直的（0.63），截到上面 40% 后变成 1.59，列表条里宽一倍多。
        各包尺寸一致（D240 那批是 256×256，按比例缩）。
        """
        e = self.box.get((name or "").strip().upper())
        if not e:
            return None
        w, f = self._open(e[0], e[1], "牌盒 %s" % name)
        if w is None:
            return None
        try:
            img = tex.decode(w.read(f))
        except Exception as ex:
            log.warning("牌盒解码失败 %s: %s", name, ex)
            return None
        s = img.width / float(paths.DECKBOX_BASE)
        x, y, cw, ch = paths.RC_DECKBOX_FRONT
        x, y = int(x * s), int(y * s)
        cw = int(cw * s)
        ch = int(ch * s * paths.DECKBOX_CROP_TOP)
        if x + cw > img.width or y + ch > img.height:
            log.warning("牌盒 %s 尺寸不对（%dx%d），不裁", name, img.width, img.height)
            return img
        return img.crop((x, y, x + cw, y + ch))

    def _box_key(self, name):
        """缩略图缓存键。**带上裁剪份额** —— 调 `DECKBOX_CROP_TOP` 之后
        旧图不会被打扮成新的，不用手动清 `data/thumbs/`。"""
        return "box%d_%s" % (round(paths.DECKBOX_CROP_TOP * 100), name)

    def box_thumb_path(self, name):
        """牌盒封面缩略图文件路径；不存在就现生成。失败返回 None。"""
        name = (name or "").strip().upper()
        if not name or name not in self.box:
            return None
        key = self._box_key(name)
        p = self._thumb_file(key)
        if os.path.exists(p):
            return p
        if not self._make_thumb(key, p, lambda: self.box_image(name)):
            return None
        return p

    def has_box(self, name):
        return bool(self.box.get((name or "").strip().upper()))

    # ---------------------------------------------------------------- 缩略图

    def thumb_path(self, key):
        """缩略图文件路径；不存在就现生成。失败返回 None。"""
        p = self._thumb_file(key)
        if os.path.exists(p):
            return p
        if not self._make_thumb(key, p):
            return None
        return p

    def _thumb_file(self, key):
        # 分片：2 万多个文件摊到 256 个子目录里
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:80]
        shard = "%02x" % (hash(key) % 256 & 0xFF)
        d = os.path.join(paths.THUMBS, shard)
        return os.path.join(d, safe + ".webp")

    def _make_thumb(self, key, dest, factory=None):
        """`factory` 不给就按卡牌 key 取图；牌盒封面走自己的 `box_image`。"""
        with self._lock:
            if key in self._inflight:
                # 别的线程正在做同一张 —— 等它，别重复解码
                for _ in range(100):
                    time.sleep(0.02)
                    if os.path.exists(dest):
                        return True
                return False
            self._inflight.add(key)
        try:
            img = factory() if factory else self.image(key, size="thumb")
            if img is None:
                return False
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            # 先写临时文件再改名 —— 半截文件被读到会让浏览器缓存坏图
            tmp = dest + ".tmp%d" % threading.get_ident()
            img.convert("RGB").save(tmp, self.thumb_fmt, quality=self.thumb_q, method=4)
            os.replace(tmp, dest)
            return True
        except Exception as e:
            log.warning("生成缩略图失败 %s: %s", key, e)
            return False
        finally:
            with self._lock:
                self._inflight.discard(key)

    # ---------------------------------------------------------------- 杂项

    def has_art(self, key):
        return bool(self.by_key.get(key))

    def stats(self):
        n = 0
        size = 0
        if os.path.isdir(paths.THUMBS):
            for root, _dirs, files in os.walk(paths.THUMBS):
                for f in files:
                    if f.endswith(".webp"):
                        n += 1
                        size += os.path.getsize(os.path.join(root, f))
        return {"art_total": len(self.art), "key_mapped": len(self.by_key),
                "deckbox_total": len(self.box),
                "thumbs_cached": n, "thumbs_bytes": size,
                "wad_pool": self.pool.stats}

    def close(self):
        self.pool.close_all()


_instance = None
_instance_lock = threading.Lock()


def get():
    """进程内单例 —— 索引只读一次。"""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ArtCache()
        return _instance


def reset():
    """换了游戏目录之后调它，下次 get() 会重建。"""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.close()
        _instance = None


def main():
    """自检：随便抽几张卡取图 + 生成缩略图，看耗时和大小。"""
    a = get()
    print(json.dumps(a.stats(), ensure_ascii=False, indent=1)[:600])
    with open(paths.POOL_JSON, encoding="utf-8") as f:
        cards = json.load(f)["cards"]
    keys = [k for k in list(cards)[:6]]
    for k in keys:
        t0 = time.time()
        img = a.image(k)
        t1 = time.time()
        tp = a.thumb_path(k)
        t2 = time.time()
        print("  %-44s %s  解码%.0fms  缩略图%.0fms  %s"
              % (k[:42], ("%dx%d" % img.size) if img else "无图",
                 (t1 - t0) * 1000, (t2 - t1) * 1000,
                 ("%d 字节" % os.path.getsize(tp)) if tp else "失败"))
    print(json.dumps(a.stats(), ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
