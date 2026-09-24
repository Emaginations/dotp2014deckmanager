# -*- coding: utf-8 -*-
"""统一入口：**按需**建索引 —— 没变的读缓存，变了才重建。

四个索引，每个都在 `_meta.sources` 里记着「建的时候游戏目录长什么样」
（每个 .wad 的大小 + mtime）。启动时比一下：

    python tools/rebuild.py            # 该建的建，该跳的跳
    python tools/rebuild.py --check    # 只看状态，什么都不建
    python tools/rebuild.py --force    # 全部强制重建

依赖顺序：`pool` 是其它三个的基础，必须先有。

| 索引 | 内容 | 全量耗时 |
|---|---|---|
| `pool.json` | 卡名/费用/颜色/类别/**稀有度**/来源包 | ~2s |
| `card_details.json` | 关键词/规则文本/风味/系列/画师/赛制 | ~8s |
| `art_index.json` | 卡 → ARTID → 插画在哪个包 | ~15s |
| `frame_index.json` + `data/frames/` | 卡框/PT 框/法术力符号 PNG | ~14s |
| `deckbox_index.json` | 牌盒封面名 → 贴图在哪个包 | ~3s |
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

import paths
import settings
from log import get_logger

sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)
import build_pool
import build_details
import build_artidx
import build_deckbox
import export_frames
import gamedecks

log = get_logger("rebuild")

INDEXES = [
    ("pool", paths.POOL_JSON, "卡池"),
    ("details", paths.DETAILS_JSON, "卡牌详情"),
    ("art", paths.ARTIDX_JSON, "插画索引"),
    ("frames", paths.FRAMEIDX_JSON, "卡面素材"),
    ("gamedecks", paths.GAMEDECKS_JSON, "游戏牌组"),
    ("deckbox", paths.DECKBOX_JSON, "牌盒封面"),
]


def status(game_dir=None):
    """`{名字: (fresh, meta, reasons)}`"""
    gd = game_dir or settings.game_dir()
    return {name: settings.index_status(path, gd)
            for name, path, _cn in INDEXES}


def ensure_all(force=False, verbose=True):
    """哪个不新鲜就建哪个。返回 `{名字: meta}`。"""
    gd = settings.game_dir()
    if not os.path.isdir(gd):
        # RuntimeError 而不是 SystemExit —— 见 cardset.py 里同一处的说明。
        # 打 exe 后首次启动必然走到这儿（还没配游戏目录），
        # 抛 SystemExit 会让 API 端点直接穿透、服务半死。
        raise RuntimeError(
            "游戏目录不存在：%s\n"
            "改配置：编辑 %s 里的 game_dir，或调 settings.save({'game_dir': ...})"
            % (gd, paths.SETTINGS_JSON))

    if verbose:
        print("游戏目录：%s" % gd)
    out = {}
    t0 = time.time()

    # pool 必须最先 —— 后面两个都要读它
    if verbose:
        print("[1/6] 卡池")
    out["pool"] = build_pool.build(gd, force=force, verbose=verbose)

    if verbose:
        print("[2/6] 卡牌详情")
    out["details"] = build_details.build(gd, force=force, verbose=verbose)

    if verbose:
        print("[3/6] 插画索引")
    out["art"] = build_artidx.build(gd, force=force, verbose=verbose)

    if verbose:
        print("[4/6] 卡面素材")
    out["frames"] = export_frames.build(gd, force=force, verbose=verbose)

    if verbose:
        print("[5/6] 游戏牌组")
    out["gamedecks"] = gamedecks.build(gd, force=force, verbose=verbose)

    if verbose:
        print("[6/6] 牌盒封面")
    out["deckbox"] = build_deckbox.build(gd, force=force, verbose=verbose)

    if verbose:
        print("全部就绪，用时 %.1fs" % (time.time() - t0))
    return out


def main():
    gd = settings.game_dir()
    check_only = "--check" in sys.argv
    force = "--force" in sys.argv

    if check_only:
        print("游戏目录：%s\n" % gd)
        st = status(gd)
        for name, path, cn in INDEXES:
            fresh, meta, why = st[name]
            mark = "✓ 新鲜" if fresh else "✗ 需重建"
            n = (meta or {}).get("count") or (meta or {}).get("art_count") \
                or (meta or {}).get("counts") or "-"
            print("  %-8s %-18s %s  条目=%s" % (name, cn, mark, n))
            if not fresh:
                for w in why:
                    print("           %s" % w)
                print("           文件：%s" % path)
        print("\n加 --force 强制重建，或直接跑（不带 --check）按需重建")
        return 0

    ensure_all(force=force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
