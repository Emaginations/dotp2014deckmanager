# -*- coding: utf-8 -*-
"""离线索引：卡牌 -> ARTID -> 插画在哪个 WAD 的哪一条。

为什么要自己建而不是直接用现成的
--------------------------------
`dotp2014decks/data/art_index.json` 已经有了 `ARTID -> 包名`（22,944 条），
但它有两个毛病：

1. **不校验格式**。D240 那个包里有 180 个 TDX 的头写死了非法的 D3DFormat=13，
   解出来全是噪声。收录进来会顶掉真正能用的图。
2. **只记包名，不记是哪一条**。取图时还得在这个包里线性扫一遍找文件名。

而且要驱动卡面，光有 `ARTID -> 图` 不够，还得有 `卡牌 <FILENAME> -> ARTID`
（在卡牌 XML 的 `<ARTID value="..." />` 里，`pool.json` 没抽这个字段）。

优先级：`ART_MAIN` 系列 > 其它包。同名的以先见到的为准（对齐原项目的策略）。

输出 `data/art_index.json`::

    {"_meta": {...},
     "art":  {"SHIVAN_DRAGON": ["DATA_DLC_CW_ART_MAIN_03.wad", "…/SHIVAN_DRAGON.tdx"]},
     "by_key": {"_SHIVAN_DRAGON_CW_129730": "SHIVAN_DRAGON"}}

用法::

    python tools/build_artidx.py
"""

import os
import re
import sys
import json
import time
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

import paths
import settings
from log import get_logger
from wadlite import WadPool

# 认识的 TDX 格式。D3DFormat 21=A8R8G8B8 / 22=X8R8G8B8，其余靠 FourCC。
OK_FOURCC = (b"DXT1", b"DXT5")
OK_ENUM = (21, 22)

ARTID_RE = re.compile(rb'<ARTID[^>]*value="([^"]*)"')
FILENAME_RE = re.compile(rb'<FILENAME[^>]*text="([^"]*)"')
CARDNAME_RE = re.compile(rb'<CARDNAME[^>]*text="([^"]*)"')


def tdx_ok(raw):
    """头 16 字节里的格式字段是不是认识的。太短也当坏。"""
    if len(raw) < 16:
        return False
    fmt = raw[12:16]
    if fmt in OK_FOURCC:
        return True
    return int.from_bytes(fmt, "little") in OK_ENUM


def build(game_dir, force=False, verbose=True):
    """建 `art_index.json`。指纹没变且不强制就跳过。"""
    log = get_logger("artidx")
    fresh, meta, why = settings.index_status(paths.ARTIDX_JSON, game_dir)
    if fresh and not force:
        if verbose:
            print("  插画索引还是新的（%d 张），跳过" % (meta or {}).get("art_count", 0))
        return meta or {}
    if verbose and why:
        print("  重建原因：%s" % "；".join(why))

    t0 = time.time()
    pool = WadPool(game_dir, limit=4)

    try:
        names = sorted(n for n in os.listdir(game_dir) if n.lower().endswith(".wad"))
    except OSError as e:
        log.error("游戏目录读不了：%s", e)
        raise SystemExit("游戏目录读不了：%s" % e)
    # 美术包优先，其余排后面
    names.sort(key=lambda n: (0 if "ART" in n.upper() else 1,
                              0 if "ART_MAIN" in n.upper() else 1, n))

    art = {}          # ARTID(大写) -> [wad, path]
    by_key = {}       # 卡牌 FILENAME -> ARTID
    n_bad = 0
    per_wad = []

    for n in names:
        w = pool.get(n)
        if w is None:
            log.warning("跳过打不开的包：%s", n)
            continue
        n_art = n_bad_wad = 0
        for f in w.files:
            up = f.path.upper()
            # 注意别在这里提前 `if not up.endswith(".TDX"): continue` ——
            # 那样下面的 XML 分支永远进不去（踩过）。
            if up.endswith(".TDX") and "/ILLUSTRATIONS/" in up:
                # 插画：只收格式认识的
                try:
                    raw = w.read(f)
                except Exception as e:
                    n_bad += 1
                    log.debug("读不出 %s: %s", f.path, e)
                    continue
                if not tdx_ok(raw):
                    n_bad_wad += 1
                    n_bad += 1
                    log.debug("坏格式跳过 %s（头 %s）", f.path, raw[12:16].hex(" "))
                    continue
                aid = os.path.basename(up)[:-4]
                if aid not in art:              # 先见到的胜（已按优先级排过序）
                    art[aid] = [n, f.path]
                n_art += 1
            elif up.endswith(".XML") and "/CARDS/" in up:
                try:
                    raw = w.read(f)
                except Exception as e:
                    log.debug("读不出卡牌 XML %s: %s", f.path, e)
                    continue
                m = ARTID_RE.search(raw)
                if not m:
                    continue
                aid = m.group(1).decode("cp1252", "replace").strip().upper()
                fk = FILENAME_RE.search(raw)
                if not fk or not aid:
                    continue
                key = fk.group(1).decode("cp1252", "replace").strip()
                by_key.setdefault(key, aid)
        if n_art or n_bad_wad:
            per_wad.append((n, n_art, n_bad_wad))

    pool.close_all()

    if verbose:
        print("  插画 %d 张（跳过坏格式 %d 张），卡->ARTID %d 条，用时 %.1fs"
              % (len(art), n_bad, len(by_key), time.time() - t0))

    # 有多少卡能在图库里找到图
    if not os.path.exists(paths.POOL_JSON):
        raise SystemExit("先跑 tools/build_pool.py —— 卡池索引还没建")
    with open(paths.POOL_JSON, encoding="utf-8") as f:
        pooljson = json.load(f)["cards"]
    hit = miss = 0
    missing_samples = []
    for key in pooljson:
        aid = by_key.get(key)
        if aid and aid in art:
            hit += 1
        else:
            miss += 1
            if len(missing_samples) < 8:
                missing_samples.append((key, aid or "(卡里没写 ARTID)"))
    if verbose:
        print("  卡池 %d 条：有图 %d（%.1f%%），缺图 %d"
              % (hit + miss, hit, 100.0 * hit / max(1, hit + miss), miss))
        for k, a in missing_samples[:5]:
            print("     缺图 %-44s %s" % (k, a))

    meta = settings.stamp({
        "art_count": len(art), "key_count": len(by_key),
        "bad_skipped": n_bad, "cards_with_art": hit, "cards_without_art": miss,
    }, game_dir)
    with open(paths.ARTIDX_JSON, "w", encoding="utf-8") as f:
        json.dump({"_meta": meta, "art": art, "by_key": by_key}, f,
                  ensure_ascii=False, separators=(",", ":"))
    if verbose:
        print("  -> %s（%.1f MB）" % (paths.ARTIDX_JSON,
                                     os.path.getsize(paths.ARTIDX_JSON) / 1e6))
    return meta


def main():
    gd = settings.game_dir()
    print("游戏目录：%s" % gd)
    force = "--force" in sys.argv
    t0 = time.time()
    build(gd, force=force)
    print("用时 %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
