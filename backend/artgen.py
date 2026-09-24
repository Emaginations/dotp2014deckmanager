# -*- coding: utf-8 -*-
"""牌盒封面 + 鹏洛客立绘的合成。

**不自己画盒子、不自己画圆。** 照抄 DotP 2014 Deck Builder 的做法 ——
拿三张官方素材走它自己的三步流程（`ImageBuilder.BuildImage()`）：

    1. MaskImage   把卡图画进 rect，盖上 Mask，再把 Mask 底色抠成透明
    2. 叠 Overlay  盒子本身的边框/底座/蓝边/明暗全在这里，不用自己画高光
    3. ApplyAlpha  RGB 取上一步结果，Alpha 取 Alpha 图

四种产物**走的是同一套流程**，只是换素材和矩形（`EditAiPersonality.cs:93/100/129`）：

| 产物 | mask | overlay | alpha | rect |
|---|---|---|---|---|
| 牌盒封面 | DeckBoxMask | DeckBoxOverlay | DeckBoxAlpha | (150,93,216,257) |
| 圆形头像 | CircularMask | — | CircularAlpha | (38,38,180,180) |
| 大厅背板 | — | — | BackplateAlpha | (0,0,256,512) |
| 全身立绘 | — | — | — | (0,0,512,512) |

搬运自 `dotp2014decks/cover.py` + `d240_tex.py`，只改了素材路径和日志。

TDX 头部（本机逆向出来的，两套格式）
------------------------------------
**原始 BGRA**（封面 / 头像 / 背板）：`u16 512, u16 W, u16 H, u16 1, u32 1480, u32 21`
之后是 `W×H×4` 字节 BGRA，**不压缩**。

**DXT1**（全身立绘 1024，太大不压不行）：`u16 512, u16 1024, u16 1024, u16 1, u32 1312, b"DXT1"`

> [!IMPORTANT] 第一个 u16 **恒为 512**
> 官方 165 张立绘 + 24 张封面无一例外 —— 那是图集瓦片尺寸，**不是宽或高**。
> 写成实际宽度的话，256×512 的背板会变成「512×512 的头 + 524288 字节数据」，
> 游戏按 512×512×4 = 1048576 去读，直接越界崩溃。
"""

import os
import struct

import numpy as np
from PIL import Image

import paths
import dxt
from log import get_logger

log = get_logger("artgen")

# ---------------------------------------------------------------- 矩形

# 牌盒封面：Deck Builder 里写死的 (x, y, w, h)
MASK_OPTIMAL = (150, 93, 216, 257)
# 立绘三个矩形，抄自 EditAiPersonality.cs:93/100/129
AVATAR_OPTIMAL = (38, 38, 180, 180)
BACKPLATE_OPTIMAL = (0, 0, 256, 512)
FULL_SIZE = 1024

# 贴图目标尺寸（宽, 高）—— 和游戏里官方立绘逐字节对齐
AVATAR_SIZE = (256, 256)
BACKPLATE_SIZE = (256, 512)


# ---------------------------------------------------------------- 素材

_cache = {}


def _load(path, key):
    if key not in _cache:
        _cache[key] = Image.open(path).convert("RGBA")
    return _cache[key]


def deckbox_assets():
    d = paths.DECKBOX_ASSETS
    return {
        "Mask": _load(os.path.join(d, "D14_DeckBoxMask.png"), "bx_mask"),
        "Overlay": _load(os.path.join(d, "D14_DeckBoxOverlay.png"), "bx_overlay"),
        "Alpha": _load(os.path.join(d, "D14_DeckBoxAlpha.png"), "bx_alpha"),
    }


def personality_assets():
    d = paths.PW_ASSETS
    return {
        "CircularMask":
            _load(os.path.join(d, "D14_PersonalityCircularMask.png"), "pw_mask"),
        "CircularAlpha":
            _load(os.path.join(d, "D14_PersonalityCircularAlpha.png"), "pw_circ"),
        "BackplateAlpha":
            _load(os.path.join(d, "D14_PersonalityBackplateAlpha.png"), "pw_back"),
    }


# ---------------------------------------------------------------- 合成

def initial_adjust(loaded, optimal):
    """卡图在矩形里怎么摆 —— 逐行照抄 `ImageBuilder.InitialImageAdjust()`。

    先试「按高度缩放到铺满」，够宽就用；否则试「按宽度缩放到铺满」，
    两种都取浪费最少的那种，最后居中。
    """
    ox, oy, ow, oh = optimal
    lw, lh = loaded
    sz = None
    px = py = 0

    r = float(oh) / lh
    if ow <= int(r * lw):
        sz = (int(r * lw), oh)
        px = (ow - sz[0]) // 2
        py = 0

    r = float(ow) / lw
    if oh <= int(r * lh):
        if sz and sz[0] != 0:
            test = (ow, int(r * lh))
            px = (ow - sz[0]) // 2
            py = 0
            waste_h = float(oh) / test[1]
            waste_w = float(ow) / sz[0]
            if waste_h < waste_w:
                sz = test
                px, py = 0, (oh - sz[1]) // 2
        else:
            sz = (ow, int(r * lh))
            px, py = 0, (oh - sz[1]) // 2

    if sz is None:
        sz = (ow, oh)
    return (ox + px, oy + py, sz[0], sz[1])


def build_image(art_img, mask=None, overlay=None, alpha=None, optimal=None,
                zoom=1.0):
    """复刻 `ImageBuilder.BuildImage()` + `Tools.AdjustImage()`。见模块开头那张表。"""
    size = None
    for a in (overlay, mask, alpha):
        if a is not None:
            size = a.size
            break
    if size is None:
        size = (optimal[2], optimal[3])

    art = art_img.convert("RGBA")
    if zoom != 1.0:
        art = art.resize((max(1, int(art.width * zoom)),
                          max(1, int(art.height * zoom))), Image.LANCZOS)

    x, y, w, h = initial_adjust(art.size, optimal)
    scaled = art.resize((max(1, w), max(1, h)), Image.LANCZOS)

    if mask is not None:
        # 1. MaskImage：画进 rect → 盖 Mask → 把 Mask 左上角那个底色抠成透明
        out = Image.new("RGBA", mask.size, (0, 0, 0, 0))
        out.paste(scaled, (x, y))
        out.alpha_composite(mask)
        key = mask.getpixel((0, 0))[:3]
        a = np.array(out)
        sel = ((a[..., 0] == key[0]) & (a[..., 1] == key[1]) & (a[..., 2] == key[2]))
        a[sel, 3] = 0
        out = Image.fromarray(a)          # (h,w,4) uint8 -> 自动认成 RGBA
    else:
        # Tools.AdjustImage：透明画布上按 rect 画，超出裁掉
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        out.paste(scaled, (x, y))

    # 2. 叠 Overlay（盒子本身的边框/底座/蓝边/明暗都在里面）
    if overlay is not None:
        out = out.copy()
        out.alpha_composite(overlay)

    # 3. 套 Alpha：RGB 取结果，Alpha 取 Alpha 图
    if alpha is not None:
        out.putalpha(alpha.getchannel("A"))
    return out


def make_cover(art_img, zoom=1.0):
    """卡图 -> 牌盒封面（512×512 RGBA）"""
    A = deckbox_assets()
    return build_image(art_img, A["Mask"], A["Overlay"], A["Alpha"],
                       MASK_OPTIMAL, zoom)


def _desaturate(art_img):
    """压暗去饱和，当「未解锁」头像。系数照抄 d240_tex.locked_art()。"""
    a = np.asarray(art_img.convert("RGB"), np.float32)
    g = a @ np.array([0.299, 0.587, 0.114], np.float32)
    a = (g[..., None] * 0.55 + a * 0.12) * 0.72
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")


def make_avatar(art, locked=False):
    """圆形头像 256×256 —— 走 CircularMask/CircularAlpha，不是自己画个圆。"""
    A = personality_assets()
    src = _desaturate(art) if locked else art
    return build_image(src, A["CircularMask"], None, A["CircularAlpha"],
                       AVATAR_OPTIMAL)


def make_backplate(art):
    """大厅背板 256×512 —— 只套 BackplateAlpha。"""
    A = personality_assets()
    return build_image(art, None, None, A["BackplateAlpha"], BACKPLATE_OPTIMAL)


def make_full(art, size=FULL_SIZE):
    """全身立绘 1024×1024 —— 无遮罩无 alpha 的 AdjustImage。"""
    return build_image(art, None, None, None, (0, 0, size, size))


# ---------------------------------------------------------------- 编码

def bgra_header(w, h, unk=1480):
    """原始 BGRA 的头。**第一个 u16 恒为 512**，写成宽会越界（见模块 docstring）。"""
    return struct.pack("<4H2I", 512, w, h, 1, unk, 21)


def dxt1_header(w, h, unk=1312):
    """DXT1 的头。同样第一个 u16 恒为 512。"""
    return struct.pack("<4HI", 512, w, h, 1, unk) + b"DXT1"


def _bgra_bytes(img):
    """PIL RGBA -> BGRA 字节流。"""
    return np.asarray(img.convert("RGBA"), np.uint8)[:, :, [2, 1, 0, 3]].tobytes()


def _fit(img, size):
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    return img


def encode_cover(img):
    """PIL 图 -> 牌盒封面 TDX（16 字节头 + 512×512 BGRA）。"""
    img = _fit(img, (512, 512))
    return bgra_header(512, 512) + _bgra_bytes(img)


def encode_portraits(art, name):
    """一张插画 -> 4 张立绘 TDX。返回 `{包内文件名: 字节}`。

    和 `d240_tex.build_planeswalkers()` 逐项对齐：

    | 文件 | 规格 | personality XML 字段 |
    |---|---|---|
    | `<名>.tdx` | 256×256 BGRA | MEDIUM / SMALL_AVATAR_IMAGE |
    | `<名>_locked.tdx` | 256×256 BGRA | SMALL_AVATAR_IMAGE_LOCKED |
    | `<名>_Backplate.tdx` | 256×512 BGRA | LOBBY_IMAGE |
    | `<名>_full.tdx` | 1024×1024 DXT1 | LARGE_AVATAR_IMAGE |
    """
    out = {}
    av = _fit(make_avatar(art, False), AVATAR_SIZE)
    out["%s.tdx" % name] = bgra_header(*AVATAR_SIZE) + _bgra_bytes(av)

    lk = _fit(make_avatar(art, True), AVATAR_SIZE)
    out["%s_locked.tdx" % name] = bgra_header(*AVATAR_SIZE) + _bgra_bytes(lk)

    bp = _fit(make_backplate(art), BACKPLATE_SIZE)
    out["%s_Backplate.tdx" % name] = bgra_header(*BACKPLATE_SIZE) + _bgra_bytes(bp)

    full = make_full(art, FULL_SIZE).convert("RGBA")
    px = np.asarray(full, np.uint8)
    out["%s_full.tdx" % name] = dxt1_header(FULL_SIZE, FULL_SIZE) + \
        dxt.dxt1_encode(px)
    return out


def preview_png(img, max_w=520):
    """给界面预览用的 PNG 字节。太大就缩一下 —— 1024 的立绘直接发没必要。"""
    import io
    if img.width > max_w:
        h = max(1, int(img.height * max_w / float(img.width)))
        img = img.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def selftest():
    """自检：拿一张假图跑完整条链路，验证尺寸和头部。"""
    art = Image.new("RGBA", (512, 376), (200, 60, 40, 255))
    cov = make_cover(art)
    assert cov.size == (512, 512), cov.size
    data = encode_cover(cov)
    assert len(data) == 16 + 512 * 512 * 4, len(data)
    assert struct.unpack_from("<4H", data, 0) == (512, 512, 512, 1), "封面头不对"

    ps = encode_portraits(art, "TEST")
    assert set(ps) == {"TEST.tdx", "TEST_locked.tdx", "TEST_Backplate.tdx",
                       "TEST_full.tdx"}, sorted(ps)
    assert len(ps["TEST.tdx"]) == 16 + 256 * 256 * 4
    assert len(ps["TEST_Backplate.tdx"]) == 16 + 256 * 512 * 4
    assert struct.unpack_from("<3H", ps["TEST_Backplate.tdx"], 0) == (512, 256, 512), \
        "背板头不对（第一个 u16 必须恒为 512）"
    assert ps["TEST_full.tdx"][12:16] == b"DXT1"
    print("artgen 自检通过：封面 %d 字节，立绘四张 %s"
          % (len(data), {k: len(v) for k, v in sorted(ps.items())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
