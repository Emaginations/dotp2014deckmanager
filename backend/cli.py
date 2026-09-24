# -*- coding: utf-8 -*-
"""命令行入口 —— **不进界面也能组卡、渲染卡面**。

给脚本和 agent 用。所有子命令都输出 JSON（`--human` 换成表格），
方便被别的程序解析。

    # 找卡
    python cli.py search "dragon" --color R --cmc-max 4
    python cli.py search --rarity M --kw CHARACTERISTIC_FLYING --limit 10
    python cli.py card "Sol Ring"

    # 组卡
    python cli.py deck new "我的卡组"
    python cli.py deck add <id> "Oath of Druids" 4 "Forbidden Orchard" 4
    python cli.py deck set <id> "Sol Ring" 3
    python cli.py deck rm  <id> "Ponder" 1
    python cli.py deck show <id>
    python cli.py deck import <id> --file list.txt
    python cli.py deck export <id> --out list.txt

    # 卡面
    python cli.py render "Shivan Dragon" -o shivan.png --width 744
    python cli.py sheet "Sol Ring" "Black Lotus" --cols 4 -o sheet.png

    # 其它
    python cli.py meta                # 可筛的取值域
    python cli.py rebuild             # 重建索引
    python cli.py doctor              # 自检：路径/索引/日志
"""

import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import paths
import settings
from log import get_logger, recent, logfile

log = get_logger("cli")


def _out(obj, human=None, as_json=False):
    if as_json or not human:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(human)


def _need_indexes():
    """索引缺了就报清楚，别让调用方拿到一堆看不懂的 KeyError。"""
    missing = [n for n, p in (("pool", paths.POOL_JSON),
                              ("details", paths.DETAILS_JSON),
                              ("art", paths.ARTIDX_JSON)) if not os.path.exists(p)]
    if missing:
        print("缺索引：%s\n跑一下：python cli.py rebuild" % "、".join(missing),
              file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------- 子命令

def cmd_search(a):
    if not _need_indexes():
        return 2
    import cardset
    cs = cardset.get()
    keys, total, _ = cs.search(
        q=a.query or "", colors=a.color or [], color_mode=a.color_mode,
        type=a.type or "", sub=a.sub or "", cmc_min=a.cmc_min, cmc_max=a.cmc_max,
        rarity=a.rarity or [], kw=a.kw or [], set_=a.set or [], src=a.src or [],
        legal=a.legal or [], limit=a.limit, offset=a.offset,
        sort=a.sort, desc=a.desc)
    rows = [cs.slim(k) for k in keys]
    human = "\n".join(
        "%3d  %-30s %-12s %-24s %s %s" % (
            r["cmc"], (r["zh"] or r["en"])[:28], r["cost"], r["line"][:22],
            r["rarity_cn"] or r["rarity"], r["set"])
        for r in rows)
    _out({"total": total, "count": len(rows), "items": rows},
         "命中 %d，本页 %d：\n%s" % (total, len(rows), human), a.json)
    return 0


def cmd_card(a):
    if not _need_indexes():
        return 2
    import cardset
    cs = cardset.get()
    k = cs.key_of(a.name) or a.name
    c = cs.full(k)
    if not c:
        print("找不到：%s" % a.name, file=sys.stderr)
        return 1
    human = "\n".join([
        "%s / %s" % (c.get("zh") or "-", c.get("en") or "-"),
        "费用 %s   法术力值 %d   颜色 %s" % (c.get("cost"), c.get("cmc"),
                                        "".join(c.get("colors") or []) or "无色"),
        "类别 %s" % c.get("line"),
        "系列 %s   稀有度 %s   画师 %s" % (c.get("set"), c.get("rarity"), c.get("artist")),
        "关键词 %s" % ("、".join(c.get("kw_cn") or []) or "—"),
        "",
        c.get("text") or "(无规则文本)",
    ])
    _out(c, human, a.json)
    return 0


def cmd_meta(a):
    if not _need_indexes():
        return 2
    import cardset
    m = cardset.get().meta()
    human = "\n".join([
        "卡池 %d 张" % m["count"],
        "类型：" + "  ".join("%s %d" % (k, v) for k, v in m["types"]),
        "稀有度：" + "  ".join("%s(%s) %d" % (c, cn, n) for c, n, cn in m["rarities"]),
        "来源：" + "  ".join("%s %d" % (k, v) for k, v in m["srcs"]),
        "关键词 %d 种，系列 %d 个" % (len(m["keywords"]), len(m["sets"])),
    ])
    _out(m, human, a.json)
    return 0


def cmd_deck(a):
    import deck as dm
    import cardset
    cs = cardset.get() if os.path.exists(paths.POOL_JSON) else None

    if a.action == "list":
        ds = dm.list_all()
        _out([d["id"] for d in ds] if a.json else ds,
             "\n".join("%-28s %-20s %s" % (d["id"], d["name_cn"], d["updated"])
                       for d in ds) or "(没有卡组)", a.json)
        return 0

    if a.action == "new":
        # `deck new 我的卡组` 和 `deck new --name 我的卡组` 都要能用
        name = a.name if a.name != "新卡组" else (a.id or "新卡组")
        d = dm.blank(name, a.en or name)
        base, i = d["id"], 2
        while dm.load(d["id"]):
            d["id"] = "%s-%d" % (base, i)
            i += 1
        d = dm.save(d)
        _out(d, "建好了：%s（%s）" % (d["id"], d["name_cn"]), a.json)
        return 0

    if a.action == "show":
        d = dm.load(a.id)
        if not d:
            print("没有这个卡组：%s" % a.id, file=sys.stderr)
            return 1
        st = dm.stats(d, cs)
        if a.json:
            _out({"deck": d, "stats": st, "issues": dm.validate(d, cs) if cs else []}, None, True)
        else:
            print(dm.to_text(d))
            print("\n--- 统计 ---")
            print("牌表 %d 条 · 全场 %d 张（引擎补 %d 张地）· 地 %d（%.0f%%）"
                  % (st["n_main"], st["total"], st["auto_land"],
                     st["n_land"], st["land_pct"]))
            print("曲线 %s   均费 %.2f" % (st["curve"], st["avg_cmc"]))
            if cs:
                for it in dm.validate(d, cs):
                    print("[%s] %s" % (it["level"], it["msg"]))
        return 0

    if a.action == "delete":
        _out({"deleted": dm.delete(a.id)}, "删了：%s" % a.id, a.json)
        return 0

    # 以下都要改牌
    d = dm.load(a.id)
    if not d:
        print("没有这个卡组：%s" % a.id, file=sys.stderr)
        return 1
    if cs is None:
        print("没有卡池索引，先跑 rebuild", file=sys.stderr)
        return 2

    box = dict(d.get(a.section) or {})
    unknown = []

    def resolve(n):
        k = cs.key_of(n)
        return cs.cards[k].get("en") if k else ""

    if a.action in ("add", "set", "rm"):
        # 参数是一串「名 数量 名 数量」，也允许只给名字（默认 1）
        pairs, i = [], 0
        while i < len(a.items):
            nm = a.items[i]
            if i + 1 < len(a.items) and a.items[i + 1].lstrip("-").isdigit():
                pairs.append((nm, int(a.items[i + 1])))
                i += 2
            else:
                pairs.append((nm, 1))
                i += 1
        for nm, cnt in pairs:
            real = resolve(nm)
            if not real:
                unknown.append(nm)
                continue
            if a.action == "add":
                box[real] = box.get(real, 0) + cnt
            elif a.action == "rm":
                left = box.get(real, 0) - cnt
                box[real] = left if left > 0 else box.pop(real, None)
                if left <= 0:
                    box.pop(real, None)
            else:
                if cnt > 0:
                    box[real] = cnt
                else:
                    box.pop(real, None)
        d[a.section] = box

    elif a.action == "import":
        text = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
        p = dm.parse_text(text)
        if a.mode == "merge":
            for k, v in p["main"].items():
                real = resolve(k) or k
                d["main"][real] = d["main"].get(real, 0) + v
            for k, v in p["side"].items():
                real = resolve(k) or k
                d["side"][real] = d["side"].get(real, 0) + v
        else:
            for src, dest in ((p["main"], d["main"]), (p["side"], d["side"])):
                dest.clear()
                for k, v in src.items():
                    real = resolve(k)
                    if not real:
                        unknown.append(k)
                    else:
                        dest[real] = v
        if p["min_lands"]:
            d["min_lands"] = p["min_lands"]

    elif a.action == "export":
        txt = dm.to_text(d)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(txt)
            print("-> %s" % a.out)
        else:
            print(txt)
        return 0

    d = dm.save(d)
    st = dm.stats(d, cs)
    human = "牌表 %d 条 · 全场 %d 张 · 地 %d" % (st["n_main"], st["total"], st["n_land"])
    if unknown:
        human += "\n!! 卡池里找不到：%s" % "、".join(unknown)
    for it in dm.validate(d, cs):
        human += "\n[%s] %s" % (it["level"], it["msg"])
    _out({"deck": d, "stats": st, "unknown": unknown}, human, a.json)
    return 0


def cmd_render(a):
    if not _need_indexes():
        return 2
    import cardset, render
    cs = cardset.get()
    k = cs.key_of(a.name) or a.name
    if not cs.cards.get(k):
        print("找不到：%s" % a.name, file=sys.stderr)
        return 1
    img = render.render_card(k, width=a.width)
    out = a.out or (k.strip("_")[:40] + ".png")
    img.save(out)
    print("%s -> %s (%dx%d)" % (a.name, out, img.width, img.height))
    return 0


def cmd_sheet(a):
    if not _need_indexes():
        return 2
    import cardset, render
    cs = cardset.get()
    keys = []
    for n in a.names:
        k = cs.key_of(n) or n
        if cs.cards.get(k):
            keys.append(k)
        else:
            print("跳过（找不到）：%s" % n, file=sys.stderr)
    if not keys:
        return 1
    img = render.render_sheet(keys, cols=a.cols, width=a.width)
    out = a.out or "sheet.png"
    img.save(out)
    print("%d 张 -> %s (%dx%d)" % (len(keys), out, img.width, img.height))
    return 0


def cmd_rebuild(a):
    import rebuild
    rebuild.ensure_all(force=a.force, verbose=True)
    return 0


def cmd_doctor(a):
    gd = settings.game_dir()
    print("游戏目录   %s  %s" % (gd, "OK" if os.path.isdir(gd) else "!! 不存在"))
    print("配置文件   %s" % paths.SETTINGS_JSON)
    print("日志       %s" % logfile())
    print("项目根     %s" % paths.ROOT)
    for name, p, cn in (("pool", paths.POOL_JSON, "卡池"),
                        ("details", paths.DETAILS_JSON, "卡牌详情"),
                        ("art", paths.ARTIDX_JSON, "插画索引"),
                        ("frames", paths.FRAMEIDX_JSON, "卡面素材")):
        ok = os.path.exists(p) and os.path.getsize(p) > 0
        print("索引 %-8s %-10s %s%s" % (name, cn, "OK" if ok else "!! 缺",
                                        "" if ok else "（%s）" % p))
    n = len(os.listdir(paths.THUMBS)) if os.path.isdir(paths.THUMBS) else 0
    print("缩略图缓存 %s 个分片目录" % n)
    errs = recent(10)
    print("\n最近 %d 条警告/错误：" % len(errs))
    for e in errs[:10]:
        print("  %s %-7s %s  %s" % (e["t"], e["level"], e["where"], e["msg"][:90]))
    if not errs:
        print("  （无）")
    return 0


# ---------------------------------------------------------------- 参数

def build_parser():
    p = argparse.ArgumentParser(
        prog="cli", description="万智牌 2014 卡组编辑器 · 命令行",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--json", action="store_true", help="输出 JSON（默认给人类看的）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="搜卡")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--color", action="append", help="W/U/B/R/G/C/M，可重复")
    s.add_argument("--color-mode", default="any", choices=["any", "exact", "only"])
    s.add_argument("--type", default="")
    s.add_argument("--sub", default="")
    s.add_argument("--cmc-min", type=int)
    s.add_argument("--cmc-max", type=int)
    s.add_argument("--rarity", action="append", help="C/U/R/M/T/S，可重复")
    s.add_argument("--kw", action="append", help="CHARACTERISTIC_*，可重复")
    s.add_argument("--set", action="append")
    s.add_argument("--src", action="append")
    s.add_argument("--legal", action="append")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--sort", default="name", choices=["name", "cmc", "rarity", "set"])
    s.add_argument("--desc", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("card", help="看一张卡的详情")
    s.add_argument("name")
    s.set_defaults(fn=cmd_card)

    s = sub.add_parser("meta", help="可筛的取值域")
    s.set_defaults(fn=cmd_meta)

    s = sub.add_parser("deck", help="卡组操作")
    s.add_argument("action",
                   choices=["list", "new", "show", "add", "set", "rm",
                            "delete", "import", "export"])
    s.add_argument("id", nargs="?", default="")
    s.add_argument("items", nargs="*", help="「卡名 数量」成对，数量可省（默认 1）")
    s.add_argument("--name", default="新卡组")
    s.add_argument("--en", default="")
    s.add_argument("--section", default="main", choices=["main", "side"])
    s.add_argument("--file")
    s.add_argument("--out")
    s.add_argument("--mode", default="replace", choices=["replace", "merge"])
    s.set_defaults(fn=cmd_deck)

    s = sub.add_parser("render", help="渲染单张卡面为 PNG")
    s.add_argument("name")
    s.add_argument("-o", "--out")
    s.add_argument("--width", type=int, default=356)
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("sheet", help="多张卡拼一张联系表")
    s.add_argument("names", nargs="+")
    s.add_argument("-o", "--out")
    s.add_argument("--cols", type=int, default=5)
    s.add_argument("--width", type=int, default=240)
    s.set_defaults(fn=cmd_sheet)

    s = sub.add_parser("rebuild", help="重建索引")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_rebuild)

    s = sub.add_parser("doctor", help="自检")
    s.set_defaults(fn=cmd_doctor)
    return p


def main():
    a = build_parser().parse_args()
    try:
        return a.fn(a) or 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        log.error("命令 %s 失败：%s", a.cmd, e, exc_info=True)
        print("\n出错了：%s: %s" % (type(e).__name__, e), file=sys.stderr)
        print("日志：%s" % logfile(), file=sys.stderr)
        if not a.json:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
