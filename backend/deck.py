# -*- coding: utf-8 -*-
"""卡组工程 —— 一个卡组一个 JSON，存在 `projects/`。

字段刻意对齐 `dotp2014decks/decks_data.py` 里的 `DECKS` 条目，
这样第二阶段导出 WAD 时能直接喂给现有的 `build.py` 那套 XML 生成逻辑，
不用再做一次翻译。

    {
      "id": "oath-of-druids",          # 文件名，唯一
      "name_cn": "誓约德鲁伊",
      "name_en": "Oath of Druids",
      "uid": "DATA_DLC_1M_DECK1",      # 导出时的包名
      "colors": ["green", "blue"],
      "cover": "_OATH_OF_DRUIDS_CW_6151",   # 牌盒封面用的卡（**卡池 key**）
      "avatar": "_OATH_OF_DRUIDS_CW_6151",  # 鹏洛客立绘用的卡（空则跟 cover）
      "main": {"Oath of Druids": 4},   # 主牌（不含基本地）
      "min_lands": {"minIsland": 4},   # 基本地下限
      "side": {"Regrowth": 2},         # 解锁表
      "guide": {"特色": "…"},          # 说明文字
      "created": "...", "updated": "..."
    }

卡名存**英文名**（`en`），和 `decks_data.py` 一致 —— 导出时按名字查卡池拿 FILENAME。
"""

import os
import re
import json
import time

import paths
import fsx
from log import get_logger

log = get_logger("deck")

LAND_ATTRS = ["minPlains", "minIsland", "minSwamp", "minMountain", "minForest"]
LAND_CN = {"PLAINS": "平原", "ISLAND": "海岛", "SWAMP": "沼泽",
           "MOUNTAIN": "山脉", "FOREST": "森林"}
COLOR_CN = {"white": "白", "blue": "蓝", "black": "黑", "red": "红", "green": "绿"}
BASIC_OF_COLOR = {"W": "PLAINS", "U": "ISLAND", "B": "SWAMP",
                  "R": "MOUNTAIN", "G": "FOREST"}


def slug(s):
    """卡组 id。

    **保留中日韩字符** —— 早先只留 `[a-zA-Z0-9]`，中文名全被抹掉后回退成时间戳
    （`deck-20260924-221903`），用户看着莫名其妙。Windows 的文件名、URL 编码、
    前端 `encodeURIComponent` 都支持中文，没必要牺牲可读性。
    """
    s = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", (s or "").strip().lower())
    return s.strip("-") or ("deck-" + time.strftime("%Y%m%d-%H%M%S"))


def path_of(deck_id):
    return os.path.join(paths.PROJECTS, deck_id + ".json")


def slug_ascii(s, fallback="DECK"):
    """卡组名 -> 纯 ASCII 大写标识。**中文转拼音**。

    用在**包名 / XML 文件名 / 贴图名前缀**上 —— 游戏按名字查资源，
    中文和符号都会找不到，所以必须转。

        深海王座          -> SHENHAIWANGZUO
        Throne of the Deep -> THRONEOFTHEDEEP

    早先用的是 `D14_1M_<name_en 去符号>`，中文名会被整个抹掉
    （`deep` 之类残留几个字母），出来的 `D14_1M_THRONEOFTHEDEEP`
    是给机器看的，用户在游戏里看到一脸问号。
    """
    s = (s or "").strip()
    if not s:
        return fallback
    try:
        from pypinyin import lazy_pinyin
        parts = lazy_pinyin(s)
    except ImportError:
        # 没装拼音库也别炸 —— 英文名照样能用，中文名会退化成空串走 fallback
        log.warning("没装 pypinyin，中文卡组名转不出拼音（pip install pypinyin）")
        parts = [s]
    out = re.sub(r"[^A-Za-z0-9]", "", "".join(parts)).upper()
    return out[:32] or fallback


def blank(name_cn="新卡组", name_en="New Deck"):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        # id 优先取中文名 —— 用户看到的是「誓约德鲁伊」而不是 `data-dlc-1m-deck1`
        "id": slug(name_cn) if slug(name_cn) != slug("新卡组") else slug(name_en),
        "name_cn": name_cn,
        "name_en": name_en,
        # 包名 = `DATA_DLC_` + 卡组名的拼音。`DATA_DLC_` 是游戏自己的约定
        # （`DATA_DLC_D240` / `DATA_DLC_KEV` / `DATA_DLC_TFM_*` 都这么起）。
        # 中段**不再加 `1M_`** —— 那是开发者的代号，用户在游戏里看到会莫名其妙。
        "uid": "DATA_DLC_" + slug_ascii(name_cn or name_en),
        "colors": [],
        # 打包用的两张源图，存的都是**卡池 key**（不是 ARTID）——
        # 前端网格给的就是 key，`artcache.image(key)` 也吃 key，少一次翻译。
        # 早先这里记的是 ARTID，而且 `openGameDeck` 往里塞的是牌盒**贴图名**
        # （`D14_KRUFA`），两个命名空间混在一起，又没有任何 UI 用它。已纠正。
        "cover": "",
        "avatar": "",
        "main": {},
        "min_lands": {},
        "side": {},
        "guide": {},
        "source": "",
        "created": now,
        "updated": now,
    }


def normalize(d):
    """补全缺字段 + 丢掉坏数据。外部传进来的 JSON 不能全信。"""
    out = blank()
    out.update({k: v for k, v in (d or {}).items() if k in out or k == "id"})
    for k, t in (("main", dict), ("side", dict), ("min_lands", dict), ("guide", dict)):
        if not isinstance(out.get(k), t):
            out[k] = {}
    if not isinstance(out.get("colors"), list):
        out["colors"] = []
    # 数量必须是正整数
    for k in ("main", "side"):
        out[k] = {n: int(c) for n, c in out[k].items()
                  if isinstance(c, (int, float)) and int(c) > 0}
    out["min_lands"] = {n: int(c) for n, c in out["min_lands"].items()
                        if n in LAND_ATTRS and isinstance(c, (int, float)) and int(c) > 0}
    if not out.get("id"):
        out["id"] = slug(out.get("name_en"))
    return out


def list_all():
    """全部卡组（按更新时间倒序）。坏文件跳过并记日志，别让一个烂文件废掉整个列表。"""
    os.makedirs(paths.PROJECTS, exist_ok=True)
    out = []
    for n in sorted(os.listdir(paths.PROJECTS)):
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(paths.PROJECTS, n), encoding="utf-8") as f:
                out.append(normalize(json.load(f)))
        except Exception as e:
            log.warning("卡组文件坏了，跳过 %s：%s", n, e)
    out.sort(key=lambda d: d.get("updated") or "", reverse=True)
    return out


def load(deck_id):
    p = path_of(deck_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return normalize(json.load(f))


def save(d):
    d = normalize(d)
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(paths.PROJECTS, exist_ok=True)
    # 先写临时文件再改名 —— 写一半崩了不会毁掉原文件。
    # 走 `fsx` 是因为 Windows 上没有 FILE_SHARE_DELETE，前端并发读列表时会挡住 replace。
    fsx.write_text(path_of(d["id"]), json.dumps(d, ensure_ascii=False, indent=2))
    return d


def delete(deck_id):
    p = path_of(deck_id)
    if os.path.exists(p):
        # **记一笔**。草稿是硬删（`os.remove`，不进回收站），
        # 出过一次「工程文件不见了但日志里查不到是谁删的」（真事）。
        log.warning("删除卡组工程 %s（%d 字节）", p, os.path.getsize(p))
        os.remove(p)
        return True
    log.warning("要删的卡组不存在：%s", p)
    return False


def duplicate(deck_id, suffix=" 副本"):
    """复制一份工程。返回新卡组；源不存在返回 None。

    id 会一直加后缀直到不撞（`x` -> `x-2` -> `x-3`），名字同理
    （`静默圣所 副本` -> `静默圣所 副本 2`）—— 否则连点两次复制，
    第二份会把第一份**覆盖掉**。
    """
    src = load(deck_id)
    if src is None:
        return None
    d = json.loads(json.dumps(src))          # 深拷贝，别共享 main 字典
    base = src["id"]
    i = 2
    d["id"] = slug(base + "-2")
    while os.path.exists(path_of(d["id"])):
        i += 1
        d["id"] = slug("%s-%d" % (base, i))
    base_name = (src.get("name_cn") or src.get("name_en") or "卡组").rstrip()
    base_name = re.sub(r"\s*副本(\s*\d+)?$", "", base_name)   # 副本的副本不要叠成「副本 副本」
    name = base_name + suffix
    j = 2
    while any(x.get("name_cn") == name for x in list_all()):
        name = "%s %d" % (base_name + suffix, j)
        j += 1
    d["name_cn"] = name
    d["name_en"] = name
    # **uid 必须跟着新名字走**。早先从 `d["id"]` 取，中文 id 被 `[^A-Z0-9]` 抹光后
    # 只剩个位数，或者干脆沿用源的 uid —— 结果两份工程拿到**同一个包名**，
    # 谁后打包谁覆盖谁。现在用拼音，再撞就加后缀。
    d["uid"] = "DATA_DLC_" + slug_ascii(name)
    seen = {x.get("uid") for x in list_all() if x.get("id") != d["id"]}
    base_uid, n = d["uid"], 2
    while d["uid"] in seen:
        d["uid"] = "%s%d" % (base_uid, n)
        n += 1
    d["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 别留着源的来源标注 —— 副本是独立工程，不是那个包里的
    d["source"] = "复制自 %s" % (src.get("name_cn") or src["id"])
    return save(d)


# ---------------------------------------------------------------- 文本牌表
#
# 给外部程序 / agent 用的格式。**故意做得宽松**：中英文卡名都收，
# 数量写在前面后面都行，注释用 `//` `#` `;` 都可以，还认 MTGA 那种
# `Deck` / `Sideboard` 分段标题。
#
#     4 Oath of Druids
#     2 Emrakul, the Aeons Torn
#
#     Sideboard
#     2 Ancient Tomb
#
# 也认基本地：`4 Island` 会进 min_lands（写成 minIsland=4）。

_BASIC_ATTR = {"plains": "minPlains", "island": "minIsland", "swamp": "minSwamp",
               "mountain": "minMountain", "forest": "minForest"}
_BASIC_CN = {"平原": "minPlains", "海岛": "minIsland", "沼泽": "minSwamp",
             "山脉": "minMountain", "森林": "minForest"}

_LINE_RE = re.compile(r"^\s*(?:(\d+)\s*[xX×]?\s+(.+?)|(.+?)\s*[xX×]\s*(\d+))\s*$")
_SIDE_MARK = ("sideboard", "side", "解锁", "解锁表", "备牌", "sb")
_MAIN_MARK = ("deck", "main", "mainboard", "主牌", "主卡组")


def parse_text(text):
    """文本牌表 -> `{main, side, min_lands, unknown}`。

    `unknown` 是解析出来但**卡池里没有**的卡名 —— 调用方该把它报给用户，
    而不是默默丢掉（那样用户会以为导进去了）。
    """
    main, side, min_lands, unknown = {}, {}, {}, []
    section = "main"

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("//", "#", ";", "--")):
            # 注释里的分段提示也认
            body = low.lstrip("/#;- ").strip()
            if any(m in body for m in _SIDE_MARK) and len(body) < 16:
                section = "side"
            elif any(m in body for m in _MAIN_MARK) and len(body) < 16:
                section = "main"
            continue
        if low.rstrip(":") in _SIDE_MARK:
            section = "side"
            continue
        if low.rstrip(":") in _MAIN_MARK:
            section = "main"
            continue

        m = _LINE_RE.match(line)
        if not m:
            continue
        if m.group(1):
            cnt, name = int(m.group(1)), m.group(2)
        else:
            name, cnt = m.group(3), int(m.group(4))
        name = name.strip().strip("　")
        if not name or cnt <= 0:
            continue

        key = name.lower()
        attr = _BASIC_ATTR.get(key) or _BASIC_CN.get(name)
        if attr:
            min_lands[attr] = min_lands.get(attr, 0) + cnt
            continue

        (side if section == "side" else main)[name] = \
            (side if section == "side" else main).get(name, 0) + cnt

    return {"main": main, "side": side, "min_lands": min_lands, "unknown": unknown}


def to_text(d, include_lands=True, include_side=True):
    """卡组 -> 文本牌表。"""
    out = ["// %s / %s" % (d.get("name_cn") or "", d.get("name_en") or ""), ""]
    for n, c in sorted((d.get("main") or {}).items(), key=lambda kv: -kv[1]):
        out.append("%d %s" % (c, n))
    if include_lands and d.get("min_lands"):
        out += ["", "// 基本地下限"]
        for k, v in d["min_lands"].items():
            basic = k.replace("min", "").upper()
            out.append("%d %s" % (v, basic.capitalize()))
    if include_side and d.get("side"):
        out += ["", "// 解锁表"]
        for n, c in sorted(d["side"].items(), key=lambda kv: -kv[1]):
            out.append("%d %s" % (c, n))
    return "\n".join(out)


def validate(d, cardset):
    """导入/编辑后检查一遍，把问题列给用户。返回 `[{level, msg}]`。"""
    issues = []
    main = d.get("main") or {}
    if not main:
        issues.append({"level": "warn", "msg": "主牌是空的"})

    missing = [n for n in list(main) + list(d.get("side") or {})
               if not cardset.key_of(n)]
    if missing:
        issues.append({
            "level": "error",
            "msg": "卡池里找不到这 %d 张卡：%s" % (len(missing), "、".join(missing[:6])),
            "cards": missing,
        })

    # 颜色覆盖 —— 每个用到的颜色都得有基本地下限，否则对局中会崩
    need = set()
    for n in main:
        r = cardset.cards.get(cardset.key_of(n))
        if r:
            need.update(r.get("colors") or [])
    have = {BASIC_OF_COLOR[c] for c in need if c in BASIC_OF_COLOR}
    declared = {k.replace("min", "").upper() for k in (d.get("min_lands") or {})}
    lack = have - declared
    if lack:
        issues.append({
            "level": "error",
            "msg": "牌组用到 %s 色，但没有对应的基本地下限：%s"
                   "（游戏中会对局崩溃）" % ("".join(sorted(need)), "、".join(sorted(lack))),
        })

    n = sum(main.values())
    if n > 60:
        issues.append({"level": "warn",
                       "msg": "主牌 %d 条，已超过 60 —— 引擎只会用 60 张" % n})
    return issues


# ---------------------------------------------------------------- 统计

def stats(d, cardset=None):
    """张数 / 地数 / 法术力曲线 / 颜色分布 —— 界面右栏用。

    「自动补地」照抄 Deck Builder 的 `BasicLandAmount`（`Deck.cs:829`）：
        还差多少张到 60，就补多少张基本地；
        **玩家手工加的基本地要把自动补的量相应减掉**。
    """
    main = d.get("main") or {}
    side = d.get("side") or {}
    min_lands = d.get("min_lands") or {}

    n_spell = n_nonbasic_land = 0
    curve = {}
    color_dist = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
    total_cmc = 0

    if cardset is not None:
        for name, cnt in main.items():
            r = cardset.cards.get(cardset.key_of(name))
            if not r:
                n_spell += cnt          # 查不到就当咒语，别丢
                continue
            colors = r.get("colors") or []
            if "LAND" in (r.get("type") or "").upper():
                n_nonbasic_land += cnt
            else:
                n_spell += cnt
                c = r.get("cmc") or 0
                curve[min(c, 6)] = curve.get(min(c, 6), 0) + cnt
                total_cmc += c * cnt
            if not colors:
                color_dist["C"] += cnt
            for c in colors:
                color_dist[c] = color_dist.get(c, 0) + cnt
    else:
        n_spell = sum(main.values())

    # **引擎凑 60 的规矩**：`<CARD>` 条数 + 自动补的基本地 = 60。
    # `LandConfig` 的 `min*` 只是**下限**，不是实际补的数量 ——
    # 只有当下限之和超过 `60 - 条数` 时才会补得比 60 多。
    # （官方 D14_SLIVERS：40 条 + 补 20；KEV D14_GRISEL：44 条 + min(10+6) = 60）
    n_main = sum(main.values())
    min_sum = sum(min_lands.values())
    engine_land = max(60 - n_main, min_sum)
    total = n_main + engine_land
    n_land = n_nonbasic_land + engine_land
    return {
        "n_spell": n_spell,
        "n_nonbasic_land": n_nonbasic_land,
        "n_land": n_land,
        "n_basic": engine_land,          # 引擎实际会补多少张基本地
        "min_lands_sum": min_sum,        # 声明的地下限之和
        "n_main": n_main,
        "total": total,
        "n_side": sum(side.values()),
        "n_side_kinds": len(side),
        "curve": [curve.get(i, 0) for i in range(7)],
        "avg_cmc": round(float(total_cmc) / n_spell, 2) if n_spell else 0,
        "color_dist": color_dist,
        "auto_land": engine_land,
        "to_60": 60 - total,
        "land_pct": round(100.0 * n_land / total, 1) if total else 0,
        # 下限之和超过 60-条数 就会被补过头
        "over_60": total > 60,
        "min_lands_cn": {LAND_CN.get(k.replace("min", "").upper(), k): v
                         for k, v in min_lands.items()},
    }
