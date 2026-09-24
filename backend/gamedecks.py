# -*- coding: utf-8 -*-
"""扫描**游戏目录里已有的牌组** —— 不只是本程序自己建的。

为什么要扫
----------
用户打开程序看到「卡组 0」，因为他自己那 6 副牌**装进游戏了但不在 `projects/` 里**。
而且游戏本身还有 100 多副（官方战役、社区包、DLC）。这些都能读出来看 / 改 / 另存。

分类按**来源 WAD**：

| 组 | 来源 | 说明 |
|---|---|---|
| `system` | `DATA_DECKS_*.WAD` | 游戏自带（官方战役、遭遇战、被封印的牌池） |
| `community` | `DATA_DLC_*`（除 1M） | 社区包：CW / TFM / KEV / D240 |
| `custom` | `DATA_DLC_1M_*` | 自制（本项目 / dotp2014decks 打出来的） |

牌组名要从**同一个包**的 `TEXT_PERMANENT/*.XML` 里查 `name_tag` —— 牌组 XML 里只有
`name_tag="D14_SLIVERS"` 这种内部标识，界面直接显示它不好看。

扫描结果缓存到 `data/game_decks.json`，带 WAD 指纹（和别的索引一套机制）。
"""

import os
import re
import sys
import json
import time
import collections

import paths
import deck as deckmod
import settings
from log import get_logger
from wadlite import WadPool

log = get_logger("gamedecks")

GROUPS = [
    ("custom", "自制", "本项目 / dotp2014decks 打包的"),
    ("system", "系统自带", "游戏自带（官方战役 / 遭遇战）"),
    ("community", "社区包", "CW / TFM / KEV / D240 等"),
]

# 牌组 XML 里的属性
DECK_RE = re.compile(r"<DECK\b([^>]*)>", re.I)
CARD_RE = re.compile(r'<CARD\s+name="([^"]+)"(?:\s+deckOrderId="(\d+)")?', re.I)
LANDCFG_RE = re.compile(r"<LandConfig\b([^>]*)/?>", re.I)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')

# TEXT_PERMANENT 是 Excel XML。**`<Row>` 带属性**（`<Row ss:AutoFitHeight="0">`），
# 早先写成 `<Row>` 一条都匹配不上 —— 官方牌组的名字就全退化成 `D14_JUND` 这种内部标识了。
ROW_RE = re.compile(r"<Row[^>]*>(.*?)</Row>", re.S)
CELL_RE = re.compile(r"<Data[^>]*>(.*?)</Data>", re.S)


def _group_of(wad_name):
    u = wad_name.upper()
    if u.startswith("DATA_DLC_1M_"):
        return "custom"
    if u.startswith("DATA_DECKS_"):
        return "system"
    if u.startswith("DATA_DLC_"):
        return "community"
    return "system"


CELL_FULL_RE = re.compile(r"<Cell([^>]*?)>(.*?)</Cell>", re.S)
DATA_RE = re.compile(r"<Data[^>]*>(.*?)</Data>", re.S)
SSINDEX_RE = re.compile(r'ss:Index="(\d+)"')


def _row_cells(row_xml):
    """一行 -> `{列号: 值}`（列号从 1 起）。要处理 `ss:Index`，它会跳列。"""
    out, col = {}, 1
    for m in CELL_FULL_RE.finditer(row_xml):
        mi = SSINDEX_RE.search(m.group(1))
        if mi:
            col = int(mi.group(1))
        dm = DATA_RE.search(m.group(2))
        out[col] = dm.group(1).strip() if dm else ""
        col += 1
    return out


def _read_text_table(w):
    """一个包里的 `TEXT_PERMANENT/*.XML` -> `{标识: 显示名}`。

    表格布局是 `Ident | Comment | Master Text | French | Spanish | …`。
    **「显示名」在第 3 列（Master Text）**，不是第 2 列 ——
    第 2 列是 Comment（D240 的表里写的是 uid 数字，取错了名字就变成 "240"）。

    列号靠**表头行**定位，不写死：各包的表不一定同宽，`ss:Index` 还会跳列。
    中文写在了所有语言列里（汉化的做法），所以 Master Text 就是中文。
    """
    out = {}
    for f in w.files:
        u = f.path.upper()
        if not (u.endswith(".XML") and "/TEXT_PERMANENT/" in u):
            continue
        try:
            t = w.read(f).decode("utf-8-sig", "replace")
        except Exception as e:
            log.debug("读文本表失败 %s：%s", f.path, e)
            continue

        name_col = None
        for row in ROW_RE.finditer(t):
            cells = _row_cells(row.group(1))
            if not cells:
                continue
            vals = set(cells.values())
            if "Ident" in vals and "Master Text" in vals:
                for c, v in cells.items():          # 表头行：定位名字列
                    if v == "Master Text":
                        name_col = c
                continue
            ident = cells.get(1, "")
            if not ident or ident == "Ident":
                continue
            val = cells.get(name_col or 3, "")
            if not val:
                # 名字列空着就回退：跳过 Ident/Comment，取第一个非空
                for c in sorted(cells):
                    if c > 2 and cells[c]:
                        val = cells[c]
                        break
            if val:
                out.setdefault(ident, val)
    return out


BASIC_KEY_RE = re.compile(r"^(PLAINS|ISLAND|SWAMP|MOUNTAIN|FOREST)_(\w+)$", re.I)
UNLOCKS_RE = re.compile(r"<UNLOCKS\b([^>]*)>", re.I)


def _parse_unlocks(t):
    """`UNLOCKS/*.XML` -> `(deck_uid, game_mode, Counter(卡的 FILENAME))`。

    结构：`<UNLOCKS uid="206403" deck_uid="6403" content_pack="0" game_mode="0">`
    靠 **`deck_uid`** 关联到牌组 XML 的 `uid` 属性。

    一副牌**可能有多个解锁文件** —— 官方 `D14_SLIVERS` 就有一份正常的
    （30 条，`game_mode="0"`）和一份 `_PROMOS`（10 条，`game_mode="2"`）。
    优先取正常那条，没有才用促销的。
    """
    m = UNLOCKS_RE.search(t)
    if not m:
        return None
    attrs = dict(ATTR_RE.findall(m.group(1)))
    du = (attrs.get("deck_uid") or "").strip()
    if not du:
        return None
    cards = collections.Counter()
    for cm in CARD_RE.finditer(t):
        nm = cm.group(1).strip()
        if "@" in nm:                    # 和牌组一样：解锁表里 `@N` 是洗牌偏向，**不是张数**
            nm = nm.partition("@")[0].strip()
        if nm:
            cards[nm] += 1
    return du, (attrs.get("game_mode") or "0"), cards


def _parse_deck(t, wad_name, path, names):
    m = DECK_RE.search(t)
    if not m:
        return None
    attrs = dict(ATTR_RE.findall(m.group(1)))

    cards = collections.Counter()
    basics = collections.Counter()
    order = []
    for cm in CARD_RE.finditer(t):
        nm = cm.group(1).strip()
        cnt = 1
        # **官方基础牌组用 `@N` 合并写法表示张数**（`SHOCK_348966@2` = 2 张 Shock）。
        # 不解这个后缀的话：卡名对不上卡池（会报「找不到」），张数也只算 1。
        # 注意这和**解锁表**里的 `@N` 不是一回事 —— 那里是洗牌偏向。
        if "@" in nm:
            nm, _sep, n = nm.partition("@")
            nm = nm.strip()
            if n.strip().isdigit():
                cnt = int(n)
        # 牌组里显式写的基本地（`ISLAND_357935`）：卡池**故意不含基本地**，
        # 所以转成 LandConfig 的下限，而不是当成找不到的卡。
        bm = BASIC_KEY_RE.match(nm)
        if bm:
            basics["min" + bm.group(1).capitalize()] += cnt
            continue
        cards[nm] += cnt
        order.extend([nm] * cnt)

    lc = LANDCFG_RE.search(t)
    land_cfg = {}
    if lc:
        for k, v in ATTR_RE.findall(lc.group(1)):
            if k.startswith("min") and v.isdigit() and int(v) > 0:
                land_cfg[k] = int(v)
    for k, v in basics.items():          # 显式基本地叠加到下限上
        land_cfg[k] = land_cfg.get(k, 0) + v

    colors = [c for c in ("white", "blue", "black", "red", "green")
              if attrs.get("is_" + c) == "true"]

    tag = attrs.get("name_tag") or ""
    uid = attrs.get("uid") or ""
    # 显示名：先查文本表，退而求其次用 name_tag / 文件名
    label = names.get(tag) or names.get(uid) or tag or os.path.basename(path)[:-4]
    label = label.strip() or tag
    # 说明文字（TEXT_PERMANENT 里的 `<name_tag>_DESC`）。重新打包时要写回，
    # 否则 `text_xml()` 只能拿卡组名凑一句，简介就没了。
    desc = (names.get(tag + "_DESC") or "").strip()

    never = attrs.get("never_available") == "true"

    return {
        "id": "%s::%s" % (wad_name, os.path.basename(path)[:-4]),
        "name": label,
        "name_tag": tag,
        "uid": uid,
        "wad": wad_name,
        "file": path,
        "group": _group_of(wad_name),
        "colors": colors,
        "content_pack": attrs.get("content_pack") or "",
        "always_available": attrs.get("always_available") == "true",
        # `never_available` = 牌组编辑器里看不到（boss 牌组、被封印的牌池）
        "playable": not never,
        "personality": attrs.get("personality") or "",
        "deck_box_image": attrs.get("deck_box_image") or "",
        "n_card": sum(cards.values()),
        "cards": dict(cards),
        "card_order": order,
        "land_config": land_cfg,
        "desc": desc,
        # `unlocks` 在外面扫描完 UNLOCKS/ 之后回填
        "unlocks": {},
    }


def build(game_dir, force=False, verbose=True):
    """扫描并存盘。指纹没变且不强制就跳过。"""
    fresh, meta, why = settings.index_status(paths.GAMEDECKS_JSON, game_dir)
    if fresh and not force:
        if verbose:
            print("  游戏牌组索引还是新的（%d 副），跳过" % (meta or {}).get("count", 0))
        return meta or {}
    if verbose and why:
        print("  重建原因：%s" % "；".join(why))

    t0 = time.time()
    pool = WadPool(game_dir, limit=3)
    try:
        names_wads = sorted(n for n in os.listdir(game_dir) if n.lower().endswith(".wad"))
    except OSError as e:
        # RuntimeError 而不是 SystemExit —— 见 cardset.py 里同一处的说明，
        # SystemExit 是 BaseException，会穿透 @api 把服务打死。
        raise RuntimeError("游戏目录读不了：%s" % e)

    # **先建一张跨包的全局文本表。** 牌组名不一定在牌组自己那个包里 ——
    # `DATA_DECKS_D14.WAD`（官方牌组定义）里一个 TEXT_PERMANENT 都没有，
    # 名字都在 `DATA_DECKS_D910.WAD`（中文包）里。按包各查各的会全军覆没。
    globals_text = {}
    for n in names_wads:
        w = pool.get(n)
        if w is None:
            continue
        tbl = _read_text_table(w)
        if tbl:
            for k, v in tbl.items():
                globals_text.setdefault(k, v)
    if verbose:
        print("  文本表合计 %d 条" % len(globals_text))

    decks, per_wad = [], []
    for n in names_wads:
        w = pool.get(n)
        if w is None:
            continue
        files = [f for f in w.files
                 if f.path.upper().endswith(".XML") and "/DECKS/" in f.path.upper()
                 and "_LAND_POOL" not in f.path.upper()]
        # **解锁表在同包的 `UNLOCKS/` 下**，靠 `deck_uid` 关联。
        # 不扫它的话，从游戏牌组复制出来的工程 `side` 是空的 ——
        # 重新打包后游戏里「已解锁 / 锁定的」全是 0（真踩过）。
        # 按 `(包, deck_uid)` 分桶：不同包会复用小的 uid（官方用 `9`，D240 用 `240`）。
        raw_unlocks = collections.defaultdict(list)
        for f in w.files:
            up = f.path.upper()
            if not (up.endswith(".XML") and "/UNLOCKS/" in up):
                continue
            try:
                pu = _parse_unlocks(w.read(f).decode("utf-8-sig", "replace"))
            except Exception as e:
                log.debug("读解锁表失败 %s：%s", f.path, e)
                continue
            if pu:
                raw_unlocks[pu[0]].append((pu[1], pu[2]))

        unlocks = {}
        for du, lst in raw_unlocks.items():
            normal = [c for gm, c in lst if gm == "0"]
            total = collections.Counter()
            for c in (normal or [c for _gm, c in lst]):
                total |= c                    # 同一张牌在多个解锁文件里只算一次
            unlocks[du] = dict(total)

        if not files:
            continue
        got = 0
        for f in files:
            try:
                t = w.read(f).decode("utf-8-sig", "replace")
            except Exception as e:
                log.warning("读牌组失败 %s：%s", f.path, e)
                continue
            # `<DECK>` 才算牌组 —— `/DECKS/` 目录下还混着鹏洛客 CONFIG
            # （D240 的 `ELDRAZI2~8.XML` 就是，341 字节，不是牌组）
            d = _parse_deck(t, n, f.path, globals_text)
            if d:
                d["unlocks"] = unlocks.get(d.get("uid") or "", {})
                decks.append(d)
                got += 1
        if got:
            per_wad.append((n, got))

    pool.close_all()

    by_group = collections.Counter(d["group"] for d in decks)
    meta = settings.stamp({
        "count": len(decks),
        "by_group": dict(by_group),
        "wads": len(per_wad),
    }, game_dir)
    with open(paths.GAMEDECKS_JSON, "w", encoding="utf-8") as f:
        json.dump({"_meta": meta, "decks": decks}, f,
                  ensure_ascii=False, separators=(",", ":"))

    if verbose:
        print("  扫到 %d 副牌组，分布在 %d 个包，用时 %.1fs" % (len(decks), len(per_wad),
                                                       time.time() - t0))
        for g, cn, _ in GROUPS:
            if by_group.get(g):
                print("     %-10s %3d 副" % (cn, by_group[g]))
    return meta


def load():
    if not os.path.exists(paths.GAMEDECKS_JSON):
        return [], {}
    try:
        with open(paths.GAMEDECKS_JSON, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("decks") or [], d.get("_meta") or {}
    except Exception as e:
        log.error("游戏牌组索引读不了：%s", e, exc_info=True)
        return [], {}


def list_grouped(with_cards=False):
    """按「自制 / 系统自带 / 社区包」分组返回，界面直接用。"""
    decks, _meta = load()
    out = []
    for gid, cn, desc in GROUPS:
        ds = [d for d in decks if d["group"] == gid]
        if not ds:
            continue
        if not with_cards:
            ds = [{k: v for k, v in d.items() if k not in ("cards", "card_order")}
                  for d in ds]
        ds.sort(key=lambda d: (not d["playable"], -d["n_card"], d["name"]))
        out.append({"group": gid, "name_cn": cn, "desc": desc,
                    "count": len(ds), "decks": ds})
    return out


def get(deck_id):
    decks, _ = load()
    for d in decks:
        if d["id"] == deck_id:
            return d
    return None


def to_project(d, cardset=None):
    """把游戏里的牌组转成本项目的工程格式（可以编辑、另存）。

    卡名：牌组 XML 里的是 `<FILENAME>`（卡池 key），工程里存**英文卡名**。
    转不过来的（卡池里没有）会被跳过并记日志。
    """
    from deck import blank
    # `name_en` 要**可读的英文名** —— 它会写进 TEXT 表当游戏里显示的名字，
    # 而那一栏的字体没有中文字形。
    #
    # 最好来源是简介里的「（English Name）」前缀（`（Throne of the Deep）…`），
    # 那才是给人看的名字；取不到才退回 `name_tag`
    # （`DATA_DLC_1M_DECK3` 这种内部标识，至少是 ASCII，能显示）。
    m = re.match(r"^[（(]([^）)]*)[）)]", d.get("desc") or "")
    nice_en = ((m.group(1).strip() if m else "")
               or d["name_tag"] or d["name"])
    proj = blank(normalize_name(d["name"]), nice_en)
    # 包名交给 `blank()` 按**卡组名的拼音**推（`DATA_DLC_<拼音>`），
    # 和新建卡组、复制卡组走同一套规则。
    #
    # 早先这里从 `name_tag` 推，还带 `DATA_DLC_1M_` 前缀 ——
    # `D14_SLIVERS` 会被拼成 `DATA_DLC_1M_D14SLIVERS`（去掉标点后 `D14_` 前缀
    # 匹配不上，剥不掉），而且中段那个 `1M` 是开发者的代号。
    proj["uid"] = "DATA_DLC_" + deckmod.slug_ascii(proj["name_cn"] or proj["name_en"])
    proj["colors"] = list(d["colors"])
    proj["cover"] = ""
    proj["source"] = "%s · %s" % (d["wad"], d["name_tag"])
    proj["min_lands"] = dict(d.get("land_config") or {})

    unmapped = []
    main = collections.Counter()
    for key, cnt in (d.get("cards") or {}).items():
        if cardset is not None:
            r = cardset.cards.get(key)
            nm = (r or {}).get("en") or key
            if not r:
                unmapped.append(key)
        else:
            nm = key
        main[nm] += cnt
    proj["main"] = dict(main)

    # 解锁表 —— 不带上重新打包后游戏里就没了
    side = collections.Counter()
    for key, cnt in (d.get("unlocks") or {}).items():
        if cardset is not None:
            r = cardset.cards.get(key)
            nm = (r or {}).get("en") or key
            if not r:
                unmapped.append(key)
        else:
            nm = key
        side[nm] += cnt
    proj["side"] = dict(side)

    # 说明文字。TEXT 表里存的是 `（英文名）正文`，把前缀剥掉存正文，
    # 否则 `text_xml()` 再拼一次就成了「（名）（名）正文」。
    desc = re.sub(r"^[（(][^）)]*[）)]\s*", "", d.get("desc") or "").strip()
    if desc:
        proj["guide"] = {"特色": desc}
    return proj, unmapped


def normalize_name(s):
    """牌组显示名里可能带方括号之类的装饰，清一下做工程名。"""
    s = re.sub(r"[\[\]【】]", "", s or "").strip()
    return s or "未命名牌组"


def main():
    gd = settings.game_dir()
    print("游戏目录：%s" % gd)
    force = "--force" in sys.argv
    build(gd, force=force)
    for g in list_grouped():
        print("\n== %s（%s） %d 副" % (g["name_cn"], g["desc"], g["count"]))
        for d in g["decks"][:8]:
            print("   %-34s %-30s %3d 张  %s"
                  % (d["name"][:32], d["name_tag"][:28], d["n_card"],
                     "可玩" if d["playable"] else "不可玩"))
        if g["count"] > 8:
            print("   …还有 %d 副" % (g["count"] - 8))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
