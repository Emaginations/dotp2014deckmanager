# -*- coding: utf-8 -*-
"""离线导出：把 `DATA_CORE.WAD` 里的**卡面素材**解码成 PNG。

前端要**用 DOM 拼出一整张万智牌**（卡框 + 插画 + 文字 + 费用符号），
不是把文字烤进位图 —— 这样悬浮放大时字依然锐利、可选择、可换中文字体。
所以这里只需要把游戏自带的**零件**导出来，一次导好永久缓存。

导出四类：

| 目录 | 来源 | 说明 |
|---|---|---|
| `frames/` | `ART_ASSETS/TEXTURES/CARD_FRAMES/` | 162 个卡框。头部写 512×356（**横放**），要转 90° 变成竖版 356×512 |
| `ptbox/` | `ART_ASSETS/MODELS/CARD/PTBOX_*.TDX` | 力防框底图 9 个 |
| `mana/` | `ART_ASSETS/TEXTURES/MANA/` | 法术力符号（费用串要拆成一串小图） |
| `misc/` | `MODELS/CARD/` | 系列符号、力防数字框等零碎 |

旋转方向：`CardInfo.cs:772` 是 `RotateFlip(RotateFlipType.Rotate270FlipNone)`。
numpy 里对应 `np.rot90(px, k=1)`（已实测：512×356 → 356×512，且插画窗口
(16,47,324,238) 区域 alpha 全 0，正是 DOM 分层要的镂空）。

用法::

    python tools/export_frames.py            # 增量（已存在的跳过）
    python tools/export_frames.py --force    # 全量重导
"""

import os
import re
import sys
import json
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

import paths
import settings
import tex
from log import get_logger
from wadlite import WadPool


# 计划：(目录关键字 | None, 文件名正则 | None, 输出子目录, 是否转竖)
# **按顺序匹配，先命中的赢**，所以限目录的规则要排前面。
#
# 踩过的坑：9 个 PTBOX 只有 `PTBOX_W` 在 `MODELS/CARD/` 下，
# 其余 8 个（A/B/C/G/GOLD/R/U/W/FULL）都在 `TEXTURES/` 根下。
# 按目录找只能捞到 1 个 —— 得按文件名找，目录只用来消歧。
PLAN = [
    ("CARD_FRAMES/", None, "frames", True),
    (None, re.compile(r"^PTBOX_[A-Z]+\.TDX$", re.I), "ptbox", False),
    ("TEXTURES/MANA/", None, "mana", False),
    (None, re.compile(r"^(EXPANSION_[A-Z]+|POWER_TOUGH|CARD_BACK)\.TDX$", re.I),
     "misc", False),
]


def build(game_dir, force=False, verbose=True):
    """导出卡面素材。`DATA_CORE.WAD` 没变且文件齐就跳过。"""
    log = get_logger("frames")
    fresh, meta, why = settings.index_status(paths.FRAMEIDX_JSON, game_dir)
    counts = {}
    if not force and os.path.isdir(paths.FRAMES):
        counts = {sub: len(os.listdir(os.path.join(paths.FRAMES, sub)))
                  for _s, _r, sub, _v in PLAN
                  if os.path.isdir(os.path.join(paths.FRAMES, sub))}
    # 指纹新鲜**且**每个子目录都真的还有文件（用户手删过缓存就得重导）
    if fresh and all(counts.get(sub, 0) > 0 for _s, _r, sub, _v in PLAN):
        if verbose:
            print("  卡面素材还是新的 %s，跳过" % counts)
        return meta or {}
    if verbose and why:
        print("  重建原因：%s" % "；".join(why))

    t0 = time.time()
    for _s, _r, sub, _v in PLAN:
        os.makedirs(os.path.join(paths.FRAMES, sub), exist_ok=True)

    pool = WadPool(game_dir, limit=2)
    core = os.path.join(game_dir, paths.CORE_WAD_NAME)
    w = pool.get(paths.CORE_WAD_NAME)
    if w is None:
        log.error("打不开 %s", core)
        raise SystemExit("打不开 %s —— 游戏目录对吗？" % core)

    done = skipped = failed = 0
    fails = []

    written = set()
    for f in w.files:
        up = f.path.replace("\\", "/").upper()
        base = os.path.basename(up)
        for src, pat, sub, rotate in PLAN:
            if not up.endswith(".TDX"):
                continue
            if src is not None and src.upper() not in up:
                continue
            if pat is not None and not pat.match(base):
                continue
            out = os.path.join(paths.FRAMES, sub, base[:-4].lower() + ".png")
            # 同名只导一次（`CARD_BACK` / `EXPANSION_COMMON` 在好几个目录下都有）
            if out in written:
                break
            written.add(out)
            if os.path.exists(out) and not force:
                skipped += 1
                break
            try:
                img = tex.decode(w.read(f))
            except Exception as e:
                failed += 1
                fails.append((base, str(e)))
                log.warning("解码失败 %s: %s", f.path, e)
                break
            if rotate:
                img = Image.fromarray(np.rot90(np.array(img), k=1))
            img.save(out, "PNG", optimize=True)
            done += 1
            break

    pool.close_all()

    if verbose:
        print("  导出 %d 张，跳过 %d 张，失败 %d 张，用时 %.1fs"
              % (done, skipped, failed, time.time() - t0))
        for n, e in fails[:10]:
            print("     失败 %-30s %s" % (n, e))

    # 统计
    counts = {}
    for _s, _r, sub, _v in PLAN:
        d = os.path.join(paths.FRAMES, sub)
        files = sorted(os.listdir(d)) if os.path.isdir(d) else []
        size = sum(os.path.getsize(os.path.join(d, x)) for x in files)
        counts[sub] = len(files)
        if verbose:
            print("  %-8s %3d 个文件  %6.2f MB" % (sub, len(files), size / 1e6))

    # 抽查：卡框必须是竖的 356×512
    fd = os.path.join(paths.FRAMES, "frames")
    if os.path.isdir(fd):
        bad = []
        for n in sorted(os.listdir(fd))[:12]:
            try:
                with Image.open(os.path.join(fd, n)) as im:
                    if im.size != (paths.FACE_W, paths.FACE_H):
                        bad.append((n, im.size))
            except Exception as e:
                bad.append((n, "打不开 %s" % e))
        if bad:
            log.error("卡框尺寸不对（应为 %dx%d）：%s",
                      paths.FACE_W, paths.FACE_H, bad)
            if verbose:
                print("  !! 尺寸不对（应为 %dx%d）：%s"
                      % (paths.FACE_W, paths.FACE_H, bad))
        elif verbose:
            print("  抽查卡框：尺寸都是 %dx%d ✓" % (paths.FACE_W, paths.FACE_H))

    meta = settings.stamp({"counts": counts, "failed": failed}, game_dir)
    with open(paths.FRAMEIDX_JSON, "w", encoding="utf-8") as f:
        json.dump({"_meta": meta, "counts": counts}, f, ensure_ascii=False, indent=1)
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
