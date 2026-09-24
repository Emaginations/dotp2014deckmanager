# -*- coding: utf-8 -*-
"""离线索引：牌盒封面名 -> 贴图在哪个 WAD 的哪一条。

牌组 XML 里有个 `deck_box_image="D14_KRUFA"` 属性，游戏拿它去
`ART_ASSETS/TEXTURES/DECKS/<名字>.TDX` 找**已经合成好的**牌盒封面。
界面要把这个封面画到牌组列表右边，就得能按名字取到这张图。

为什么不能只看牌组自己那个包
------------------------------
实测 128 副牌里有 **30 副**的封面不在自己的包里 —— TFM、DB07 这些 DLC
的牌组包只有牌表，封面统一放在 `DATA_DLC_TFM_ART.wad` 这类共享美术包里。
自己包里的命中率只有 98/128，做成全局索引才是 71/71（71 个不同封面，
多副牌共用一张）。

> [!NOTE] 只收 `TEXTURES/DECKS/` 下的
> `PLANESWALKERS/` 下也有同名的 TDX（鹏洛客立绘），尺寸不一样，混进来会取错图。

输出 `data/deckbox_index.json`::

    {"_meta": {...},
     "box": {"D14_KRUFA": ["DATA_DECKS_D14.WAD", "…/TEXTURES/DECKS/D14_KRUFA.TDX"]}}

用法::

    python tools/build_deckbox.py [--force]
"""

import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

import paths
import settings
from log import get_logger
from wadlite import WadPool

log = get_logger("build_deckbox")

# 封面贴图在包里的位置特征（大小写混用，统一转大写比）
NEEDLE = "/TEXTURES/DECKS/"


def build(game_dir=None, force=False, verbose=True):
    gd = game_dir or settings.game_dir()
    meta = settings.stamp({"box_count": 0}, gd)
    if not force and os.path.exists(paths.DECKBOX_JSON):
        fresh, old, _why = settings.index_status(paths.DECKBOX_JSON, gd)
        if fresh:
            if verbose:
                print("  牌盒封面索引是新的，跳过（%s 个）"
                      % (old or {}).get("box_count", "?"))
            return old

    t0 = time.time()
    pool = WadPool(gd, limit=6)
    names = sorted(n for n in os.listdir(gd)
                   if n.lower().endswith(".wad")) if os.path.isdir(gd) else []

    box = {}
    for n in names:
        w = pool.get(n)
        if w is None:
            log.warning("跳过打不开的包：%s", n)
            continue
        for f in w.files:
            p = f.path.replace("\\", "/")
            up = p.upper()
            if not up.endswith(".TDX") or NEEDLE not in up:
                continue
            name = os.path.basename(up)[:-4]
            # 先见到的胜。同名封面在多个包里出现时内容是一样的，
            # 取哪个都行 —— 但结果必须稳定，否则每次重建缩略图缓存都会失效。
            box.setdefault(name, [n, p])
    pool.close_all()

    if verbose:
        print("  牌盒封面 %d 张，用时 %.1fs" % (len(box), time.time() - t0))

    meta = settings.stamp({"box_count": len(box)}, gd)
    with open(paths.DECKBOX_JSON, "w", encoding="utf-8") as f:
        json.dump({"_meta": meta, "box": box}, f,
                  ensure_ascii=False, separators=(",", ":"))
    if verbose:
        print("  -> %s（%.0f KB）" % (paths.DECKBOX_JSON,
                                     os.path.getsize(paths.DECKBOX_JSON) / 1024.0))
    return meta


def main():
    gd = settings.game_dir()
    print("游戏目录：%s" % gd)
    t0 = time.time()
    build(gd, force="--force" in sys.argv)
    print("用时 %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
