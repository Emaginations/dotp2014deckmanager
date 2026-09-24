# -*- coding: utf-8 -*-
"""离线索引①：卡池（卡名 / 费用 / 颜色 / 类别 / 攻防 / **稀有度** / 来源包）。

从 `dotp2014decks/pool.py` 移植，两处改动：

1. **游戏目录不再写死** —— 从 `settings.json` 读（见 `settings.py`）。
2. **带 WAD 指纹** —— `_meta.sources` 记下每个包的大小和 mtime。
   启动时比一下，没变就直接读缓存，不必重扫。

索引的键是卡牌的 **`<FILENAME>`**，不是 `<CARDNAME>_<MULTIVERSEID>`。
游戏只认前者，社区包（CW）的文件名带额外标记（`OATH_OF_DRUIDS_CW_6151`），
用后两个拼出来的名字**对不上** —— 历史教训：6 副牌 299 条引用里 261 条指向不存在的卡，
游戏解析不出就留坏指针，**退出时崩溃**。

关于中英文名：这个游戏**只读 `en-US` 槽**，而汉化补丁就是往那里写中文的。
所以真英文名得从汉化前的备份 `WAD备份/<包名>.orig` 里取。备份丢了的话
`en` 会退化成中文，按英文名查卡的功能就废了 —— 界面上会提示。

输出 `data/pool.json`（`{"_meta":…, "cards":{key: rec}}`）。

用法::

    python tools/build_pool.py            # 指纹没变就跳过
    python tools/build_pool.py --force    # 强制重建
    python tools/build_pool.py --stat     # 顺便打统计
"""

import os
import re
import sys
import json
import time
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

import paths
import settings
from wadlite import WadPool

CJK = re.compile("[一-鿿]")

# 哪些包里的卡算「可用卡池」
CARD_WADS = [
    ("DATA_DLC_COMMUNITY_CORE.wad", "CW"),
    ("DATA_DECKS_D14.WAD", "D14"),
    ("DATA_DECKS_D910.WAD", "D910"),
    ("DATA_DLC_TFM_CARDS.wad", "TFM"),
    ("DATA_DLC_KEV.WAD", "KEV"),
    ("DATA_DLC_D240.WAD", "D240"),
]

BASIC_NAMES = {"PLAINS", "ISLAND", "SWAMP", "MOUNTAIN", "FOREST"}


def field(data, tag):
    m = re.search(rb'<%s[^>]*?(?:text|value|metaname|cost)="([^"]*)"' % tag.encode(), data)
    return m.group(1).decode("utf-8", "replace") if m else ""


def title_of(data, lang="en-US"):
    m = re.search(rb'<TITLE>.*?LanguageCode="%s"><!\[CDATA\[(.*?)\]\]>' % lang.encode(),
                  data, re.S)
    return m.group(1).decode("utf-8", "replace").strip() if m else ""


def colors_from_cost(cost):
    """`{2}{U}{U}` -> `['U']`；无色返回 `[]`"""
    return sorted(set(re.findall(r"\{([WUBRG])\}", cost or "")), key="WUBRG".index)


def cmc_of(cost):
    """法术力值 = 通用费数字之和 + 每个有色符号 1 点（`{X}` 记 0）"""
    tot = 0
    for s in re.findall(r"\{([^}]*)\}", cost or ""):
        if s.isdigit():
            tot += int(s)
        elif s != "X":
            tot += 1
    return tot


def build(game_dir, force=False, verbose=True):
    fresh, meta, why = settings.index_status(paths.POOL_JSON, game_dir)
    if fresh and not force:
        if verbose:
            print("  卡池索引还是新的（%d 张），跳过" % (meta or {}).get("count", 0))
        return meta or {}

    if verbose and why:
        print("  重建原因：%s" % "；".join(why))

    bak = os.path.join(game_dir, paths.WAD_BACKUP_DIRNAME)
    has_backup = os.path.isdir(bak)
    if not has_backup and verbose:
        print("  !! 没有 %s —— 取不到英文原名，en 会退化成中文" % paths.WAD_BACKUP_DIRNAME)

    pool_wad = WadPool(game_dir, limit=4)
    pool = {}
    has_en = 0

    for wad, label in CARD_WADS:
        if not os.path.exists(os.path.join(game_dir, wad)):
            if verbose:
                print("  跳过（不存在）：%s" % wad)
            continue
        w = pool_wad.get(wad)
        if w is None:
            continue
        # 汉化前的备份，用来取真英文名
        wo = None
        p_orig = os.path.join(bak, wad + ".orig")
        if os.path.exists(p_orig):
            try:
                from wadlite import MMapWad
                wo = MMapWad(p_orig)
            except Exception:
                wo = None
        om = {f.path: f for f in wo.files} if wo else {}
        n = 0
        for f in w.files:
            if not f.path.lower().endswith(".xml"):
                continue
            try:
                c = w.read(f)
            except Exception:
                continue
            if b"<CARD_V2" not in c or b"<FILENAME" not in c:
                continue
            key = field(c, "FILENAME")
            cname = field(c, "CARDNAME")
            if not key or cname.upper() in BASIC_NAMES:
                continue          # 基本地不进卡池，由 LAND_POOL 按颜色提供
            n += 1

            en = ""
            if wo and f.path in om:
                try:
                    en = title_of(wo.read(om[f.path]))
                except Exception:
                    en = ""
            cur = title_of(c)
            zh = cur if CJK.search(cur) else ""
            if en:
                has_en += 1
            else:
                en = cur

            cost = field(c, "CASTING_COST")
            typ = field(c, "TYPE")
            sub = field(c, "SUB_TYPE")
            rec = {
                "key": key,
                "cardname": cname,
                "mv": field(c, "MULTIVERSEID"),
                "en": en,
                "zh": zh,
                "cost": cost,
                "cmc": cmc_of(cost),
                "colors": colors_from_cost(cost),
                "type": typ,
                "sub": sub,
                "line": (typ + (" — " + sub if sub else "")).strip(),
                "power": field(c, "POWER"),
                "tough": field(c, "TOUGHNESS"),
                "rarity": field(c, "RARITY"),      # C/U/R/M/T/S —— 界面按它筛
                "src": label,
                "file": f.path,
            }
            old = pool.get(key)
            if old is None or (old["src"] != "CW" and rec["src"] == "CW"):
                pool[key] = rec
        if wo:
            wo.close()
        if verbose:
            print("  %-34s 卡牌 %6d" % (wad, n))

    pool_wad.close_all()

    # 逐包累加会数进「被 CW 覆盖掉的重复卡」，所以在最终卡池上重算一次
    has_en = sum(1 for r in pool.values() if r["en"] and not CJK.search(r["en"]))
    meta = settings.stamp({
        "count": len(pool),
        "english_names": has_en,
        "backup_dir_found": has_backup,
    }, game_dir)
    with open(paths.POOL_JSON, "w", encoding="utf-8") as fp:
        json.dump({"_meta": meta, "cards": pool}, fp,
                  ensure_ascii=False, separators=(",", ":"))
    print("  卡池 %d 张（有英文名 %d）-> %s" % (len(pool), has_en, paths.POOL_JSON))
    return meta


def stat(pool):
    print("\n=== 按来源 ===")
    for k, v in collections.Counter(r["src"] for r in pool.values()).most_common():
        print("  %-8s %6d" % (k, v))
    print("\n=== 按稀有度 ===")
    RAR = {"C": "普通", "U": "非普通", "R": "稀有", "M": "神话", "T": "衍生物", "S": "特殊"}
    for k, v in collections.Counter(r["rarity"] or "?" for r in pool.values()).most_common():
        print("  %-3s %-8s %6d" % (k, RAR.get(k, "?"), v))
    print("\n=== 按主类别 top12 ===")
    for k, v in collections.Counter(
            (r["type"] or "?").split("—")[0].strip() for r in pool.values()).most_common(12):
        print("  %-22s %6d" % (k, v))
    print("\n=== 按颜色组合 top12 ===")
    for k, v in collections.Counter("".join(r["colors"]) or "无色"
                                    for r in pool.values()).most_common(12):
        print("  %-8s %6d" % (k, v))


def main():
    force = "--force" in sys.argv
    gd = settings.game_dir()
    print("游戏目录：%s" % gd)
    t0 = time.time()
    meta = build(gd, force=force)
    print("用时 %.1fs" % (time.time() - t0))

    if "--stat" in sys.argv and os.path.exists(paths.POOL_JSON):
        with open(paths.POOL_JSON, encoding="utf-8") as f:
            stat(json.load(f)["cards"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
