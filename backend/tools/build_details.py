# -*- coding: utf-8 -*-
"""离线索引：从卡牌 XML 里抽出 `pool.json` **没有**的那几样。

`pool.json`（21,386 条）只有 名字/费用/CMC/颜色/类型/副类别/攻防/稀有度/来源包。
**没有**：关键词、规则文本、风味文本、系列、画师、赛制合法性 —— 而前两样正是
卡面合成和筛选要用的。

数据来源是每张卡自己的 XML（`pool.json` 的 `file` 字段已给出精确路径，
不必重新全盘扫）。一张卡长这样::

    <CARD_V2 ExportVersion="1">
      <FILENAME text="SHIVAN_DRAGON_CW_129730" />
      <TITLE>       <LOCALISED_TEXT LanguageCode="zh-CN">西瓦巨龙</LOCALISED_TEXT> …
      <CASTING_COST cost="{4}{R}{R}" />
      <FLAVOURTEXT> <LOCALISED_TEXT …>西瓦山脉无庸置疑的主人。</LOCALISED_TEXT> …
      <EXPANSION value="M15" />
      <STATIC_ABILITY>    <LOCALISED_TEXT …>飞行</LOCALISED_TEXT>
                          <INTRINSIC characteristic="CHARACTERISTIC_FLYING" />
      <ACTIVATED_ABILITY> <LOCALISED_TEXT …>{R}：此生物得+1/+0直到回合结束。</LOCALISED_TEXT>
      <FORMAT value="Commander" status="Legal" />

**规则文本 = 按顺序拼接所有能力块的 LOCALISED_TEXT。**

关于 `en-US` 和 `zh-CN` 两个槽：这个游戏**只读 `en-US` 槽**（汉化就是往那里写中文），
所以 `en-US` 才是「游戏里实际显示的文本」；`zh-CN` 是官方译名，质量常常更好但不一定
和游戏内一致。两个都存，界面上以 `text`（= en-US）为准，`text_cn` 作备选。

输出 `data/card_details.json`。

用法::

    python tools/build_details.py            # 全量重建
    python tools/build_details.py --probe     # 只跑 200 张，看看结果长什么样
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

# 能力块 —— 规则文本就是这些块按文档顺序拼起来的
ABILITY_TAGS = ("STATIC_ABILITY", "ACTIVATED_ABILITY", "TRIGGERED_ABILITY",
                "UTILITY_ABILITY", "SPELL_ABILITY", "MANA_ABILITY")

ABILITY_RE = re.compile(r"<(%s)\b[^>]*>(.*?)</\1>" % "|".join(ABILITY_TAGS), re.S)
LOC_RE = re.compile(
    r'<LOCALISED_TEXT LanguageCode="([^"]+)"\s*><!\[CDATA\[(.*?)\]\]></LOCALISED_TEXT>', re.S)
INTRINSIC_RE = re.compile(r'<INTRINSIC[^>]*characteristic="([^"]+)"')
FORMAT_RE = re.compile(r'<FORMAT[^>]*value="([^"]+)"[^>]*status="([^"]+)"')
EXPANSION_RE = re.compile(r'<EXPANSION[^>]*value="([^"]*)"')
ARTIST_RE = re.compile(r'<ARTIST[^>]*name="([^"]*)"')

# `<FORMAT>` 的 value 里混着两种东西：**赛制**和**系列名**
# （`value="Eldritch Moon"` 是「这个系列内的合法性」，不是赛制）。
# 只留真正的赛制，否则筛选器里会冒出一堆系列名。
REAL_FORMATS = {
    "standard", "modern", "legacy", "vintage", "commander", "duel",
    "pauper", "penny", "pioneer", "frontier", "brawl", "historic",
    "alchemy", "explorer", "timeless", "oathbreaker", "predh",
    "prismatic", "singleton", "tiny leaders", "future", "block",
    "freeform", "prerelease",
}


def loc_map(block):
    """块内的 {语言码: 文本}。同语言多条时后一条覆盖前一条（罕见）。"""
    return {k: v.strip() for k, v in LOC_RE.findall(block)}


def pick_text(d):
    """en-US 优先（游戏实际显示的），回退 zh-CN。"""
    return (d.get("en-US") or d.get("zh-CN") or "").strip()


CJK_RE = re.compile(r"[㐀-鿿]")


def has_cjk(s):
    return bool(CJK_RE.search(s or ""))


# 关键词中文名：从「块的文本很短且带 INTRINSIC」的 STATIC_ABILITY 里自动学出来。
# 学不到的（块的说明文本太长，或卡池里就没几张这种卡）用下面的表兜底。
KW_CN = {}

# 主力表：这些都是标准万智牌关键词，中文名固定，不该指望从卡面文本里自动学。
# （自动学习只当补充 —— 卡池里没汉化的卡会把英文名学进来，反而更差。）
KW_CN_FALLBACK = {
    "CHARACTERISTIC_FLYING": "飞行",
    "CHARACTERISTIC_TRAMPLE": "践踏",
    "CHARACTERISTIC_HASTE": "敏捷",
    "CHARACTERISTIC_FLASH": "闪现",
    "CHARACTERISTIC_VIGILANCE": "警戒",
    "CHARACTERISTIC_DEFENDER": "守军",
    "CHARACTERISTIC_REACH": "延势",
    "CHARACTERISTIC_FIRST_STRIKE": "先攻",
    "CHARACTERISTIC_DOUBLE_STRIKE": "连击",
    "CHARACTERISTIC_DEATHTOUCH": "死触",
    "CHARACTERISTIC_LIFELINK": "系命",
    "CHARACTERISTIC_INDESTRUCTIBLE": "不灭",
    "CHARACTERISTIC_HEXPROOF": "辟邪",
    "CHARACTERISTIC_SHROUD": "帷幕",
    "CHARACTERISTIC_INFECT": "侵染",
    "CHARACTERISTIC_WITHER": "枯灵",
    "CHARACTERISTIC_FEAR": "恐惧",
    "CHARACTERISTIC_INTIMIDATE": "威吓",
    "CHARACTERISTIC_FLANKING": "侧击",
    "CHARACTERISTIC_PHASING": "相位",
    "CHARACTERISTIC_SHADOW": "次元幽影",
    "CHARACTERISTIC_BATTLE_CRY": "战吼",
    "CHARACTERISTIC_BANDING": "联合",
    "CHARACTERISTIC_RAMPAGE": "狂暴",
    "CHARACTERISTIC_PROVOKE": "挑拨",
    "CHARACTERISTIC_ABSORB": "吸收",
    "CHARACTERISTIC_PLAINSWALK": "平原行者",
    "CHARACTERISTIC_ISLANDWALK": "海岛行者",
    "CHARACTERISTIC_SWAMPWALK": "沼泽行者",
    "CHARACTERISTIC_MOUNTAINWALK": "山脉行者",
    "CHARACTERISTIC_FORESTWALK": "树林行者",
    "CHARACTERISTIC_CANNOT_ATTACK_ALONE": "不能单独攻击",
    "CHARACTERISTIC_CANNOT_BLOCK_ALONE": "不能单独阻挡",
    "CHARACTERISTIC_CANT_ATTACK": "不能攻击",
    "CHARACTERISTIC_CANT_BLOCK": "不能阻挡",
}


def parse_card(t):
    """一张卡的 XML 文本 -> 详情 dict；解析不出东西就返回 None。"""
    # 去掉注释，免得里面的示例标签被当成真的
    t = re.sub(r"<!--.*?-->", "", t)

    title = ""
    m = re.search(r"<TITLE\b[^>]*>(.*?)</TITLE>", t, re.S)
    if m:
        d = loc_map(m.group(1))
        title = pick_text(d)
        title_cn = (d.get("zh-CN") or "").strip()
    else:
        title_cn = ""

    flavor = ""
    m = re.search(r"<FLAVOURTEXT\b[^>]*>(.*?)</FLAVOURTEXT>", t, re.S)
    if m:
        flavor = pick_text(loc_map(m.group(1)))

    kws, texts, seen = [], [], set()
    for am in ABILITY_RE.finditer(t):
        blk = am.group(2)
        d = loc_map(blk)
        txt = pick_text(d)
        chars = INTRINSIC_RE.findall(blk)
        kws.extend(chars)
        # 「短文本 + 有 INTRINSIC」= 这一块就是关键词本身，拿来学中文名。
        # 汉化后的 `en-US` 是中文，但有些卡没汉化（`Islandwalk` / `Shroud`），
        # 所以取 `zh-CN` 与 `en-US` 里**含中日韩字符**的那个；都不含就留着当备选。
        if chars and txt and len(txt) <= 24 and "\n" not in txt:
            cand = (d.get("zh-CN") or txt).strip()
            for c in chars:
                old = KW_CN.get(c)
                if old is None or (has_cjk(cand) and not has_cjk(old)):
                    KW_CN[c] = cand
        # **同一段文本只收一次。** DotP 的 XML 里同一个效果会出现两遍：
        #   `<SPELL_ABILITY>`  施放时的效果
        #   `<UTILITY_ABILITY>` 手牌里显示的提示
        # 两者 en-US 完全相同，不去重卡面就会把规则文本印两遍（分裂牌上很明显）。
        # 另外那几十个 `<TRIGGERED_ABILITY>` 是引擎脚本块，en-US 是空的，`if txt` 已经滤掉。
        if txt and txt not in seen:
            seen.add(txt)
            texts.append(txt)

    legal = [k for k, v in FORMAT_RE.findall(t)
             if v.lower() == "legal" and k.lower() in REAL_FORMATS]
    exp = EXPANSION_RE.search(t)
    art = ARTIST_RE.search(t)

    return {
        "kw": sorted(set(kws)),
        "text": "\n".join(texts),
        "flavor": flavor,
        "set": exp.group(1) if exp else "",
        "artist": art.group(1) if art else "",
        "legal": sorted(set(legal)),
        "title": title,
        "title_cn": title_cn if title_cn != title else "",
    }


def scan_xml_index(pool, game_dir):
    """扫出 pool 用到的那几个 WAD，建 `XML 路径(大写) -> (包名, File)`。"""
    # pool 的 src 标签 -> 可能是哪些包。直接扫所有含 CARDS 目录的包更省心。
    want = set()
    for rec in pool.values():
        p = (rec.get("file") or "").replace("\\", "/").upper()
        if p:
            want.add(p)

    pool_wad = WadPool(game_dir, limit=4)
    idx = {}
    names = sorted(n for n in os.listdir(game_dir) if n.lower().endswith(".wad"))
    t0 = time.time()
    for i, n in enumerate(names, 1):
        w = pool_wad.get(n)
        if w is None:
            continue
        hit = 0
        for f in w.files:
            up = f.path.replace("\\", "/").upper()
            if up in want:
                idx[up] = (n, f)
                hit += 1
        if hit:
            print("  %-38s 命中 %4d" % (n, hit))
        if i % 20 == 0:
            print("  …扫到 %d/%d 个包，%.0fs" % (i, len(names), time.time() - t0))
    pool_wad.close_all()
    print("  索引建好：%d 条，用时 %.1fs" % (len(idx), time.time() - t0))
    return idx


def build(game_dir, force=False, verbose=True):
    """建 `card_details.json`。指纹没变且不强制就跳过。"""
    fresh, meta, why = settings.index_status(paths.DETAILS_JSON, game_dir)
    if fresh and not force:
        if verbose:
            print("  卡牌详情索引还是新的（%d 条），跳过" % (meta or {}).get("count", 0))
        return meta or {}
    if verbose and why:
        print("  重建原因：%s" % "；".join(why))

    if not os.path.exists(paths.POOL_JSON):
        raise SystemExit("先跑 tools/build_pool.py —— 卡池索引还没建")

    with open(paths.POOL_JSON, encoding="utf-8") as f:
        pool = json.load(f)["cards"]
    if verbose:
        print("  卡池 %d 条" % len(pool))

    if verbose:
        print("  扫 WAD 建 XML 索引 …")
    idx = scan_xml_index(pool, game_dir)

    if verbose:
        print("  解析卡牌 XML …")
    pool_wad = WadPool(game_dir, limit=4)
    out, miss, err = {}, [], collections.Counter()
    t0 = time.time()
    items = list(pool.items())
    for i, (key, rec) in enumerate(items, 1):
        p = (rec.get("file") or "").replace("\\", "/").upper()
        ent = idx.get(p)
        if not ent:
            miss.append(key)
            continue
        wad_name, f = ent
        w = pool_wad.get(wad_name)
        if w is None:
            miss.append(key)
            continue
        try:
            d = parse_card(w.read(f).decode("utf-8", "replace"))
        except Exception as e:
            err[type(e).__name__] += 1
            continue
        d["wad"] = wad_name
        out[key] = d
        if verbose and i % 5000 == 0:
            print("     %5d/%d  %.0fs" % (i, len(items), time.time() - t0))

    pool_wad.close_all()
    if verbose:
        print("  解析 %d 条，%d 条找不到 XML，异常 %s，用时 %.1fs"
              % (len(out), len(miss), dict(err) or "无", time.time() - t0))

    n_kw = sum(1 for v in out.values() if v["kw"])
    n_text = sum(1 for v in out.values() if v["text"])
    n_set = sum(1 for v in out.values() if v["set"])
    if verbose:
        print("  有关键词 %d / 有规则文本 %d / 有系列 %d"
              % (n_kw, n_text, n_set))

    # 学不到的用兜底表补上；兜底表里多余的（卡池里根本没这种卡）也留着，
    # 界面上按「卡池里实际出现过哪些」来显示，不按这张表。
    kw_cn = dict(KW_CN_FALLBACK)
    kw_cn.update({k: v for k, v in KW_CN.items() if has_cjk(v) or k not in kw_cn})

    # 卡池里**实际出现过**的关键词全集 —— 界面按这个列筛选项
    used = collections.Counter()
    for v in out.values():
        used.update(v["kw"])
    unknown = [k for k in used if k not in kw_cn]
    if verbose:
        print("  卡池里实际用到关键词 %d 种，其中没中文名的 %d 个 %s"
              % (len(used), len(unknown), unknown[:5] if unknown else ""))

    meta = settings.stamp({
        "count": len(out),
        "kw_cn": kw_cn,
        "kw_used": {k: n for k, n in used.most_common()},
    }, game_dir)
    with open(paths.DETAILS_JSON, "w", encoding="utf-8") as f:
        json.dump({"_meta": meta, "cards": out}, f,
                  ensure_ascii=False, separators=(",", ":"))
    if verbose:
        print("  -> %s（%.1f MB）" % (paths.DETAILS_JSON,
                                     os.path.getsize(paths.DETAILS_JSON) / 1e6))
        if miss:
            print("  找不到 XML 的前 5 条：%s" % miss[:5])
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
