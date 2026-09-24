# -*- coding: utf-8 -*-
"""卡牌查询层 —— 把 `pool.json` + `card_details.json` 合成一份内存索引，负责筛选。

可筛的维度（**含稀有度**）：

| 参数 | 说明 |
|---|---|
| `q` | 自由文本。中英文卡名 + 规则文本 + 风味，空格分词 AND |
| `colors` | 颜色多选 `W/U/B/R/G`；`C` = 无色，`M` = 多色 |
| `color_mode` | `any`（含这些色，默认）/ `exact`（恰好这些）/ `only`（**仅限混血**，见下） |

### `color_mode` 三档到底差在哪

选 `B+G` 时实测（全池 21,386）：

| 档 | 判据 | 张数 | 出什么 |
|---|---|---|---|
| `any` | 颜色里有其中任意一个 | 7,549 | 含 B **或** 含 G（含 `{B}{U}` 这种带局外色的） |
| `exact` | 颜色**正好**是这些 | 117 | 金卡 `{B}{G}` —— **且** 的关系 |
| `only` | 含混血符号 **且** 符号只用到这些色 | 36 | `{B/G}`、`{2/B}`、`{B/P}`、`{G/P}` —— **或** 的关系 |

`only` 是**「仅含有 `{B/G}`」**的意思：单黑、单绿、`{B}{G}` 金卡都不出，
因为它们的费用里没有混血符号 —— 那些请用 `exact` 或 `any`。

> [!IMPORTANT]
> **判据必须读 `cost` 字符串，不能读 `colors`。**
> 这份数据里混血符号**不赋予颜色**：`{B/G}{B/G}` 的 `colors` 是 `[]`，
> `{G/U}{W}` 只记 `['W']`（纯色那部分），连 `{3}{G/P}` 的**诞生荚**（真卡是绿牌）
> 都是 `[]`。全池 355 张带混血符号的卡全是这个待遇。
> 所以看 `colors` 永远分不出 `{B}{G}` 和 `{B/G}{B/G}` ——
> 这正是「仅限」和「恰好」一度撞成同一个谓词（都是 117）的根因。
| `type` | 主类别（Creature / Instant / …） |
| `sub` | 副类别（龙、精灵…） |
| `cmc_min` / `cmc_max` | 法术力值区间 |
| **`rarity`** | **稀有度多选 C/U/R/M/T/S** |
| `kw` | 特征性异能（30 种，来自 XML 的 `INTRINSIC`） |
| `set` | 系列代码（M15 / XLN …） |
| `src` | 来源包（CW / D14 / TFM / KEV / D240） |
| `legal` | 赛制合法性（Commander / Modern …） |
| `has_art` | 只看有插画的 |

性能：2.1 万条线性扫描 + 提前剪枝，实测 10~40ms。前端有 300ms 防抖，
够用，所以不预先建倒排索引 —— 少一份要同步的状态。

分页契约照抄 phase：**默认只返回 175 条**，同时给出 `total` 全量命中数。
这是它 2 万张卡不卡的根本原因（不是虚拟滚动）。
"""

import re
import json
import time
import threading

import paths
from log import get_logger, guard

log = get_logger("cardset")

DEFAULT_LIMIT = 175          # 一页多少条（对齐 phase / Scryfall 的手感）
MAX_LIMIT = 500

# 稀有度代码 -> 中文
RARITY_CN = {"C": "普通", "U": "非普通", "R": "稀有", "M": "神话",
             "T": "衍生物", "S": "特殊"}

_PUNCT = (("’", "'"), ("‘", "'"), ("`", "'"), ("´", "'"),
          ("–", "-"), ("—", "-"), ("−", "-"))


def norm(s):
    """卡名归一化：小写 + 弯引号/破折号统一成 ASCII + 压空白。

    社区包写 `Sensei’s Divining Top`（U+2019），官方写 `Sensei's`。
    不归一化会搜不到。
    """
    s = (s or "").lower()
    for a, b in _PUNCT:
        s = s.replace(a, b)
    return " ".join(s.split())


_SYM_RE = re.compile(r"\{([^}]*)\}")
_HYBRID_RE = re.compile(r"\{[^}]*/[^}]*\}")


def sym_colors(cost):
    """费用里所有**有色**法术力符号涉及的颜色 —— 混血符号按 `/` 拆成分量。

    `{2}{B/G}{B/G}` -> `{'B','G'}`；`{2/B}` -> `{'B'}`；`{3}{G/P}` -> `{'G'}`。
    注意 `2` / `P`（两点生命）不是颜色，会被丢掉。
    """
    out = set()
    for s in _SYM_RE.findall((cost or "").upper()):
        for part in s.split("/"):
            if part in "WUBRG":
                out.add(part)
    return out


class CardSet(object):
    def __init__(self):
        t0 = time.time()
        self.cards = {}
        self.kw_cn = {}
        self.kw_used = {}
        self._load()
        log.info("卡池 %d 张，用时 %.2fs", len(self.cards), time.time() - t0)

    def _load(self):
        if not paths.POOL_JSON or not __import__("os").path.exists(paths.POOL_JSON):
            # 用 RuntimeError 而**不是** SystemExit —— `SystemExit` 是
            # `BaseException`，`@api` / `guard` 只兜 `Exception`，会直接穿透
            # 把服务打死。打成 exe 后首次启动还没建索引，用户看到的就是
            # 「窗口开了但一片空白且服务已死」。现在会正常返回 ok:false。
            raise RuntimeError("卡池索引不存在，先跑 tools/rebuild.py")
        with open(paths.POOL_JSON, encoding="utf-8") as f:
            self.cards = json.load(f)["cards"]

        # 详情是可选的 —— 没有也能用（只是没有关键词/规则文本）
        try:
            with open(paths.DETAILS_JSON, encoding="utf-8") as f:
                d = json.load(f)
            det = d["cards"]
            meta = d.get("_meta") or {}
            self.kw_cn = meta.get("kw_cn") or {}
            self.kw_used = meta.get("kw_used") or {}
            for k, rec in self.cards.items():
                x = det.get(k)
                if x:
                    rec.update(x)       # kw / text / flavor / set / artist / legal
        except FileNotFoundError:
            log.warning("没有 %s —— 关键词/规则文本筛选不可用", paths.DETAILS_JSON)
        except Exception as e:
            log.error("读卡牌详情失败：%s", e, exc_info=True)

        # 预算好搜索用的 haystack，别在每次查询里现拼
        for rec in self.cards.values():
            rec["_n"] = norm(rec.get("en")) + " " + norm(rec.get("zh")) \
                + " " + norm(rec.get("text"))

        # 归一化名字 -> key。卡组里存的是英文名，查回 key 走这张表，
        # 不能每次遍历 2 万条（那会把牌组统计拖到几百毫秒）。
        self.by_name = {}
        for key, rec in self.cards.items():
            for nm in (rec.get("en"), rec.get("zh"), rec.get("cardname")):
                if nm:
                    self.by_name.setdefault(norm(nm), key)

    def key_of(self, name):
        """卡名（中英文都行）-> 卡池 key；找不到返回 ''。"""
        return self.by_name.get(norm(name), "")

    # ---------------------------------------------------------------- 单卡

    def get(self, key):
        return self.cards.get(key)

    # ---------------------------------------------------------------- 筛选

    @guard(default=([], 0, []), what="卡牌筛选")
    def search(self, q="", colors=None, color_mode="any", type="", sub="",
               cmc_min=None, cmc_max=None, rarity=None, kw=None, set_=None,
               src=None, legal=None, has_art=False, exclude_art=False,
               limit=DEFAULT_LIMIT, offset=0, sort="name", desc=False,
               art_checker=None, focus=None):
        colors = [c.upper() for c in (colors or []) if c]
        rarity = [r.upper() for r in (rarity or []) if r]
        kw = [k for k in (kw or []) if k]
        set_ = set_ or []
        src = src or []
        legal = legal or []

        words = [w for w in norm(q).split() if w] if q else []
        type_l = (type or "").lower()
        sub_l = (sub or "").lower()

        want_colorless = "C" in colors
        want_multi = "M" in colors
        plain_colors = {c for c in colors if c in "WUBRG"}

        hits = []
        for key, r in self.cards.items():
            if words:
                hay = r["_n"]
                if not all(w in hay for w in words):
                    continue

            cs = r.get("colors") or []
            if colors:
                cset = set(cs)
                if color_mode == "exact":
                    if len(cset) != len(plain_colors) or not cset <= plain_colors:
                        continue
                elif color_mode == "only":
                    # 「仅限」= **仅含有 `{B/G}`** —— 费用里含混血/替代符号
                    # （`{B/G}`、`{2/B}`、`{B/P}`、`{G/P}`），**且**所有有色符号
                    # 都落在所选色内。是「**或**」的关系：`{B/G}` 拿 B 或 G 都能付。
                    #
                    # 与之相对，`exact` 是「**且**」：`{B}{G}` 金卡两个色都得有。
                    # 选 B+G 时：exact 出 117 张金卡，only 出 36 张混血，两者不相交。
                    #
                    # ⚠️ 判据走 `cost` 而**不是** `colors` —— 混血符号在这份数据里
                    # 不赋予颜色（`{B/G}{B/G}` 的 colors 是 `[]`），看 colors 的话
                    # 这 36 张会被当成无色牌，和 `exact` 撞成同一个谓词。
                    #
                    # 走过三版弯路，都记在这儿免得绕回去：
                    #   1. `cset <= plain_colors`（颜色子集）—— 选 B+G 时把单黑、单绿
                    #      也放进来（用户看到的「万物元气兽」就是单绿牌），被否
                    #   2. `cset == plain_colors or not cset` —— 无色牌 4502 张灌进来，被否
                    #   3. `cset == plain_colors` —— 和 `exact` 完全等价，成了死按钮，被否
                    cost = r.get("cost") or ""
                    if not _HYBRID_RE.search(cost):
                        continue
                    syms = sym_colors(cost)
                    if not syms or not syms <= plain_colors:
                        continue
                    # 注意 `C` / `M` 在这一档不参与：混血符号必定带颜色，
                    # 想单独要无色牌请用 `any` + `C`。
                else:
                    # `any` = 选中的每个「桶」取并集：具体色 / 无色 / 多色。
                    #
                    # ⚠️ 早先写的是 `if plain_colors and not (cset & plain_colors):`
                    # —— 只勾「无色」时 `plain_colors` 是**空集**，这个 `if` 直接短路，
                    # 于是**一个过滤条件都不生效**，两万多张全过。用户看到的是
                    # 「筛无色却混进一堆蓝牌」。
                    # 现在逐桶判，最后再决定收不收。
                    ok = bool(cset & plain_colors)
                    if want_colorless and not cset:
                        ok = True                 # 无色 = **一个颜色都没有**，不是"含无色法术力"
                    if want_multi and len(cset) > 1:
                        ok = True
                    if not ok:
                        continue

            if type_l and type_l not in (r.get("type") or "").lower():
                continue
            if sub_l and sub_l not in (r.get("sub") or "").lower():
                continue

            c = r.get("cmc")
            if cmc_min is not None and (c is None or c < cmc_min):
                continue
            if cmc_max is not None and (c is None or c > cmc_max):
                continue

            if rarity and (r.get("rarity") or "").upper() not in rarity:
                continue

            if kw:
                have = set(r.get("kw") or [])
                if not all(k in have for k in kw):
                    continue

            if set_ and (r.get("set") or "") not in set_:
                continue
            if src and (r.get("src") or "") not in src:
                continue
            if legal:
                have = set(r.get("legal") or [])
                if not all(l in have for l in legal):
                    continue

            if (has_art or exclude_art) and art_checker is not None:
                a = art_checker(key)
                if has_art and not a:
                    continue
                if exclude_art and a:
                    continue

            hits.append(key)

        total = len(hits)

        # 排序
        def sort_key(k):
            r = self.cards[k]
            if sort == "cmc":
                return (r.get("cmc") or 0, r.get("zh") or r.get("en") or "")
            if sort == "rarity":
                order = {"M": 0, "R": 1, "U": 2, "C": 3, "S": 4, "T": 5}
                return (order.get((r.get("rarity") or "").upper(), 9),
                        r.get("cmc") or 0, r.get("zh") or r.get("en") or "")
            if sort == "set":
                return (r.get("set") or "", r.get("cmc") or 0)
            return ((r.get("zh") or r.get("en") or "").lower(),)

        hits.sort(key=sort_key, reverse=bool(desc))

        # `focus`：这张卡排在完整结果的第几位。界面靠它算出「该加载哪一页」
        # 再滚过去 —— 卡池两万多张，一页只有 175，绝大多数卡根本不在 DOM 里。
        focus_index = None
        if focus:
            try:
                focus_index = hits.index(focus)
            except ValueError:
                focus_index = None       # 存在，但不满足当前筛选条件

        page = hits[offset:offset + min(limit or DEFAULT_LIMIT, MAX_LIMIT)]
        return page, total, focus_index

    # ---------------------------------------------------------------- 元数据

    def slim(self, key):
        """列表用的记录。

        **带规则文本和风味** —— 网格里的缩略卡面也要渲染描述，数据不带的话
        那一块永远是空的（得再去逐个拉 `/api/card/{key}`，175 条就是 175 个请求）。
        文本截到 600 字够画满卡面了；完整版走 `full()`。
        """
        r = self.cards.get(key)
        if not r:
            return None
        return {
            "key": key,
            "en": r.get("en") or "",
            "zh": r.get("zh") or "",
            "cost": r.get("cost") or "",
            "cmc": r.get("cmc") or 0,
            "colors": r.get("colors") or [],
            "type": r.get("type") or "",
            "sub": r.get("sub") or "",
            "line": r.get("line") or "",
            "rarity": (r.get("rarity") or "").upper(),
            "rarity_cn": RARITY_CN.get((r.get("rarity") or "").upper(), ""),
            "set": r.get("set") or "",
            "src": r.get("src") or "",
            "power": r.get("power") or "",
            "tough": r.get("tough") or "",
            "kw": r.get("kw") or [],
            "text": (r.get("text") or "")[:600],
            "flavor": (r.get("flavor") or "")[:300],
        }

    def full(self, key):
        """单卡全量 —— 卡面合成 + 详情面板用。"""
        r = self.cards.get(key)
        if not r:
            return None
        d = dict(r)
        d.pop("_n", None)
        d["rarity_cn"] = RARITY_CN.get((r.get("rarity") or "").upper(), "")
        d["kw_cn"] = [self.kw_cn.get(k, k) for k in (r.get("kw") or [])]
        return d

    def meta(self):
        """筛选面板要用的取值域 —— 只列卡池里**真的出现过**的。"""
        from collections import Counter
        types, sets, srcs, rarities = Counter(), Counter(), Counter(), Counter()
        legal, subs = Counter(), Counter()
        cmcs = Counter()
        for r in self.cards.values():
            if r.get("type"):
                types[(r["type"].split("—")[0].strip())] += 1
            if r.get("set"):
                sets[r["set"]] += 1
            if r.get("src"):
                srcs[r["src"]] += 1
            rarities[(r.get("rarity") or "?").upper()] += 1
            cmcs[r.get("cmc") or 0] += 1
            for l in (r.get("legal") or []):
                legal[l] += 1
            if r.get("sub"):
                subs[r["sub"]] += 1
        return {
            "count": len(self.cards),
            "types": types.most_common(),
            "sets": sets.most_common(),
            "srcs": srcs.most_common(),
            "rarities": [(k, v, RARITY_CN.get(k, k)) for k, v in rarities.most_common()],
            "legal": legal.most_common(),
            "keywords": [(k, (self.kw_used or {}).get(k, 0), self.kw_cn.get(k, k))
                         for k in sorted(self.kw_used, key=lambda x: -self.kw_used[x])],
            "cmc_max": max(cmcs) if cmcs else 16,
            "cmc_hist": sorted(cmcs.items()),
            "colors": ["W", "U", "B", "R", "G", "C", "M"],
            "color_names": {"W": "白", "U": "蓝", "B": "黑", "R": "红",
                            "G": "绿", "C": "无色", "M": "多色"},
            "top_subs": subs.most_common(120),
        }


_instance = None
_lock = threading.Lock()


def get():
    global _instance
    with _lock:
        if _instance is None:
            _instance = CardSet()
        return _instance


def reset():
    global _instance
    with _lock:
        _instance = None


if __name__ == "__main__":
    cs = get()
    print("卡牌", len(cs.cards))
    for name, kw in [
        ("蓝 + CMC<=3 + 飞行", dict(colors=["U"], cmc_max=3, kw=["CHARACTERISTIC_FLYING"])),
        ("只看神话稀有", dict(rarity=["M"])),
        ("白蓝 + 稀有/神话", dict(colors=["W", "U"], color_mode="exact", rarity=["R", "M"])),
        ("名字含 dragon", dict(q="dragon")),
    ]:
        t0 = time.time()
        keys, total, _ = cs.search(**kw)
        print("  %-22s 命中 %5d，本页 %3d，%.0fms  例：%s"
              % (name, total, len(keys), (time.time() - t0) * 1000,
                 (cs.slim(keys[0]) or {}).get("zh") if keys else "—"))

    # 颜色筛选的自检 —— 「无色」和「仅限混血」两档都出过事，逐张验证谓词，
    # 不只比数量（数量对上但内容错是最难发现的）。
    # 谓词收**整条记录**而不是颜色集合：`only` 要读 `cost`，颜色里根本没这信息。
    print("\n颜色筛选自检")
    allw = len(cs.cards)
    _cols = lambda r: set(r.get("colors") or [])
    cases = [
        (["C"], "any", lambda r: not _cols(r), "无色 = 一个颜色都没有"),
        (["W"], "any", lambda r: "W" in _cols(r), "白 = 含白"),
        (["W", "C"], "any", lambda r: ("W" in _cols(r)) or not _cols(r),
         "白 + 无色 = 并集"),
        (["M"], "any", lambda r: len(_cols(r)) > 1, "多色 = 至少两色"),
        (["C"], "exact", lambda r: not _cols(r), "无色（exact 模式）"),
        # 「且」：金卡 `{B}{G}`，两个色都得有，正好两个
        (["B", "G"], "exact", lambda r: _cols(r) == {"B", "G"},
         "恰好 = 正好 B+G（且）"),
        # 「或」：含混血符号，且所有有色符号只在 B/G 内 —— 注意不能拿 colors 判，
        # 混血卡的 colors 是 []
        (["B", "G"], "only",
         lambda r: bool(_HYBRID_RE.search(r.get("cost") or ""))
                   and sym_colors(r.get("cost")) <= {"B", "G"},
         "仅限 = 含混血且只用 B/G（或）"),
    ]
    bad = 0
    for cols, mode, pred, label in cases:
        keys, total, _ = cs.search(colors=cols, color_mode=mode, limit=MAX_LIMIT)
        wrong = [k for k in keys if not pred(cs.cards[k])]
        if wrong:
            bad += 1
        # 命中数超过一页上限时只验了前 MAX_LIMIT 张，如实标出来
        cover = "全量" if total <= len(keys) else "抽 %d/%d" % (len(keys), total)
        print("  %s %-26s 命中 %5d（%s）%s"
              % ("✓" if not wrong else "✗", label, total, cover,
                 "" if not wrong else "  ← 混进 %d 张，例：%s"
                 % (len(wrong), [cs.cards[k].get("zh") or cs.cards[k].get("en")
                                 for k in wrong[:3]])))
    assert bad == 0, "颜色筛选有 %d 项不对" % bad
    print("  自检通过")

    # 三档摆一起看 —— 最怕的就是其中两档又变成同一个谓词（真出过）
    print("\n=== 三档对比（选 B+G）===")
    seen = {}
    for mode in ("any", "exact", "only"):
        keys, total, _ = cs.search(colors=["B", "G"], color_mode=mode,
                                   limit=MAX_LIMIT)
        seen[mode] = total
        ex = cs.slim(keys[0]) if keys else {}
        print("  %-6s 命中 %5d  例：%s" % (mode, total,
                                          ex.get("zh") or ex.get("en") or "—"))
    dup = [m for m in ("exact", "only") if seen[m] == seen["any"]]
    if dup:
        print("  ⚠️ %s 和 any 命中数相同，检查是不是又短路了" % dup)
    if seen["exact"] == seen["only"]:
        print("  ⚠️ exact 和 only 命中数相同 —— 这两档语义不同，不该相等")
