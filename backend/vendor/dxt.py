# -*- coding: utf-8 -*-
"""DXT1 / DXT5 编解码。

`cover.py` 里那份 DXT1 解码只够读卡牌插画（不透明，忽略 alpha）。
这里补齐两件事：

* **DXT5 解码** —— 游戏自带的法术力符号贴图
  （`DATA_CORE :: ART_ASSETS/TEXTURES/MANA/MANA_{W,U,B,R,G}.TDX`）是 512×256 DXT5，
  带真 alpha 通道；DXT1 那套解出来是花的。
* **DXT1 编码** —— 要自己造插画（给 RSN 的假法术力衍生物配图），得有编码器。

DXT5 块 16 字节：8 字节 alpha（2 端点 + 16×3bit 索引）+ 8 字节颜色（同 DXT1）。
"""

import numpy as np


# ------------------------------------------------------------------ 公共

def _rgb565(c):
    """u16 数组 -> (..., 3) uint8"""
    r = ((c >> 11) & 0x1F).astype(np.int16)
    g = ((c >> 5) & 0x3F).astype(np.int16)
    b = (c & 0x1F).astype(np.int16)
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1)


def _to565(rgb):
    """(N, 3) uint8 -> (N,) uint16"""
    r = (rgb[:, 0].astype(np.uint16) >> 3) & 0x1F
    g = (rgb[:, 1].astype(np.uint16) >> 2) & 0x3F
    b = (rgb[:, 2].astype(np.uint16) >> 3) & 0x1F
    return (r << 11) | (g << 5) | b


def _color_palette(c0, c1, four):
    """按 DXT1 规则由两个 565 端点造 4 色调色板。

    `four=True`（c0 > c1）走 4 色模式；否则第 3、4 项是 2/3 混合和一个透明黑。
    """
    p0 = _rgb565(c0)
    p1 = _rgb565(c1)
    # `four` 可能是标量、也可能是 (N,) 或 (bh,bw,1)，统一补一维好和 p0 的 (...,3) 广播
    four = np.asarray(four)
    if four.ndim == p0.ndim - 1:
        four = four[..., None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, np.zeros_like(p0))
    return np.stack([p0, p1, p2, p3], axis=-2)      # (..., 4, 3)


# ------------------------------------------------------------------ 解码

def dxt1_decode(data, w, h):
    """DXT1 -> (h, w, 4) uint8 RGBA"""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    need = bw * bh * 8
    if len(data) < need:
        raise ValueError("DXT1 数据不够：需要 %d，只有 %d" % (need, len(data)))
    blk = np.frombuffer(data[:need], dtype=np.uint8).reshape(bh, bw, 8)

    c0 = blk[:, :, 0].astype(np.uint16) | (blk[:, :, 1].astype(np.uint16) << 8)
    c1 = blk[:, :, 2].astype(np.uint16) | (blk[:, :, 3].astype(np.uint16) << 8)
    bits = (blk[:, :, 4].astype(np.uint32) | (blk[:, :, 5].astype(np.uint32) << 8)
            | (blk[:, :, 6].astype(np.uint32) << 16) | (blk[:, :, 7].astype(np.uint32) << 24))

    gt = c0 > c1
    pal = _color_palette(c0, c1, gt[:, :, None])              # (bh, bw, 4, 3)
    idx = np.stack([((bits >> (2 * i)) & 0x3) for i in range(16)], -1)   # (bh,bw,16)

    flat = bh * bw
    row = np.arange(flat)[:, None]
    sel = pal.reshape(flat, 4, 3)[row, idx.reshape(flat, 16)]
    rgb = sel.reshape(bh, bw, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 3)

    a4 = np.stack([np.full((bh, bw), 255, np.uint8)] * 3
                  + [np.where(gt, 255, 0).astype(np.uint8)], -1)
    al = a4.reshape(flat, 4)[row, idx.reshape(flat, 16)]
    alpha = al.reshape(bh, bw, 4, 4).transpose(0, 2, 1, 3).reshape(bh * 4, bw * 4)

    return np.dstack([rgb.astype(np.uint8), alpha])[:h, :w]


def dxt5_decode(data, w, h):
    """DXT5 -> (h, w, 4) uint8 RGBA

    alpha 块 8 字节：a0, a1 两个端点 + 16 个 3bit 索引（6 字节，小端 48 位）。
    a0 > a1 走 8 档插值，否则 4 档 + 0/255 两个端点。
    """
    bw, bh = (w + 3) // 4, (h + 3) // 4
    need = bw * bh * 16
    if len(data) < need:
        raise ValueError("DXT5 数据不够：需要 %d，只有 %d" % (need, len(data)))
    blk = np.frombuffer(data[:need], dtype=np.uint8).reshape(bh, bw, 16)

    a0 = blk[:, :, 0].astype(np.int32)
    a1 = blk[:, :, 1].astype(np.int32)
    abits = np.zeros((bh, bw), np.uint64)
    for i in range(6):
        abits |= blk[:, :, 2 + i].astype(np.uint64) << np.uint64(8 * i)

    ai = np.stack([((abits >> np.uint64(3 * i)) & np.uint64(7)).astype(np.int32)
                   for i in range(16)], -1)                      # (bh,bw,16)
    a0e, a1e = a0[:, :, None], a1[:, :, None]
    gt = a0e > a1e
    # 8 档 / 4 档两套查表，先算好再按 gt 选
    lut8 = np.stack([a0e, a1e] + [((7 - i) * a0e + i * a1e) // 7 for i in range(1, 7)], -2)
    lut4 = np.stack([a0e, a1e,
                     (4 * a0e + 1 * a1e) // 5,
                     (3 * a0e + 2 * a1e) // 5,
                     (2 * a0e + 3 * a1e) // 5,
                     (1 * a0e + 4 * a1e) // 5,
                     np.zeros_like(a0e), np.full_like(a0e, 255)], -2)
    flat = bh * bw
    row = np.arange(flat)[:, None]
    ii = ai.reshape(flat, 16)
    alpha = np.where(gt.reshape(flat, 1),
                     lut8.reshape(flat, 8)[row, ii],
                     lut4.reshape(flat, 8)[row, ii])
    alpha = alpha.reshape(bh, bw, 4, 4).transpose(0, 2, 1, 3).reshape(bh * 4, bw * 4)

    col = blk[:, :, 8:].copy()                                    # 后 8 字节就是 DXT1 那块
    c0 = col[:, :, 0].astype(np.uint16) | (col[:, :, 1].astype(np.uint16) << 8)
    c1 = col[:, :, 2].astype(np.uint16) | (col[:, :, 3].astype(np.uint16) << 8)
    bits = (col[:, :, 4].astype(np.uint32) | (col[:, :, 5].astype(np.uint32) << 8)
            | (col[:, :, 6].astype(np.uint32) << 16) | (col[:, :, 7].astype(np.uint32) << 24))
    # **DXT5 的颜色块恒走 4 色模式**，不看 c0/c1 大小 —— 按 DXT1 的规则判会把
    # 74% 的块错当成「3 色 + 透明黑」，解出来偏紫（实测 MANA_B 均值 103,89,184）。
    pal = _color_palette(c0, c1, True)
    idx = np.stack([((bits >> (2 * i)) & 0x3) for i in range(16)], -1)
    sel = pal.reshape(flat, 4, 3)[row, idx.reshape(flat, 16)]
    rgb = sel.reshape(bh, bw, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 3)

    return np.dstack([rgb.astype(np.uint8), alpha.astype(np.uint8)])[:h, :w]


# ------------------------------------------------------------------ 编码

def dxt1_encode(rgba):
    """(h, w, 4) uint8 RGBA -> DXT1 字节。

    用 range fit：每块取 RGB 的包围盒两角当端点。对这种「大色块 + 平滑渐变」的
    图够用；换成 PCA 主轴拟合能再好一点，但不值当。

    强制 `c0 > c1` 走 4 色模式 —— 3 色模式最后一个槽是透明黑，卡面用不上。
    """
    h, w = rgba.shape[:2]
    bh, bw = (h + 3) // 4, (w + 3) // 4
    pad = np.zeros((bh * 4, bw * 4, 4), np.uint8)
    pad[:h, :w] = rgba
    # (bh, 4, bw, 4, 4) -> (bh, bw, 4, 4, 4) -> (N, 16, 4)
    blk = pad.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    rgb = blk[:, :, :3].astype(np.int16)

    c0 = _to565(rgb.max(axis=1))                     # (N,)
    c1 = _to565(rgb.min(axis=1))
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1

    pal = _color_palette(c0, c1, np.ones(len(c0), bool))          # (N,4,3)
    d = ((pal[:, None, :, :].astype(np.int32) - rgb[:, :, None, :].astype(np.int32)) ** 2).sum(-1)
    idx = d.argmin(axis=2).astype(np.uint32)                     # (N,16)

    bits = np.zeros(len(idx), np.uint32)
    for i in range(16):
        bits |= idx[:, i] << np.uint32(2 * i)

    out = np.empty((len(idx), 8), np.uint8)
    out[:, 0] = c0 & 0xFF
    out[:, 1] = c0 >> 8
    out[:, 2] = c1 & 0xFF
    out[:, 3] = c1 >> 8
    for i in range(4):
        out[:, 4 + i] = (bits >> np.uint32(8 * i)) & 0xFF
    out[same, 2] = out[same, 0]        # 单色块：端点取一样，索引全 0
    out[same, 3] = out[same, 1]
    out[same, 4:] = 0
    return out.tobytes()


def mip_levels(rgba):
    """逐级 2×2 盒式降采样，直到 1×1。"""
    out = [rgba]
    cur = rgba
    while cur.shape[0] > 1 or cur.shape[1] > 1:
        h, w = cur.shape[:2]
        nh, nw = max(1, h // 2), max(1, w // 2)
        if h >= 2 and w >= 2:
            a = cur[:nh * 2, :nw * 2].astype(np.uint16)
            cur = (a[0::2, 0::2] + a[0::2, 1::2] + a[1::2, 0::2] + a[1::2, 1::2]) // 4
            cur = cur.astype(np.uint8)
        else:
            cur = cur[:nh, :nw]
        out.append(cur)
    return out


def encode_illustration(rgba):
    """RGBA -> 卡牌插画 TDX 字节（u16×4 + u32 + "DXT1" + 完整 mip 链）。"""
    import struct
    h, w = rgba.shape[:2]
    levels = mip_levels(rgba)
    body = b"".join(dxt1_encode(l) for l in levels)
    return struct.pack("<4HI", 512, w, h, len(levels), 0) + b"DXT1" + body


def decode_illustration(raw):
    """卡牌插画 TDX -> (h, w, 4) uint8 RGBA（只解基级）

    **数据恒从偏移 16 开始** —— 头里那个 u32（偏移 8）看着像「额外头长度」，
    其实不是要跳过的字节数。实证：`512x376 mip=10 extra=32` 的插画
    `len(raw) - 16` 正好等于完整 10 级 DXT1 链的 128536 字节；
    DXT5 的 `MANA_B.TDX`（`extra=40`）同理，`len - 16 = 87408` 正好是完整链。
    照 `16+extra` 解会整体错位（extra=32 → 横移 4 个块 = 16 像素），
    图案轻微斜切，不容易一眼看出来。
    """
    import struct
    if raw[12:16] != b"DXT1":
        raise ValueError("不是 DXT1 插画：%s" % raw[:16].hex(" "))
    _a, w, h, _mip, _extra = struct.unpack_from("<4HI", raw, 0)
    return dxt1_decode(raw[16:], w, h)
