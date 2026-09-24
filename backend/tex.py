# -*- coding: utf-8 -*-
"""TDX 贴图解码 —— 离线导出和运行时取图共用这一份。

TDX 的头是 16 字节::

    u16 a        ← **恒为 512**，是常数不是宽/高（详见 dotp2014decks/readme.md）
    u16 width
    u16 height
    u16 mipmaps
    u32 未知      ← **不是**「要跳过的字节数」
    u32 D3DFormat / FourCC

**数据恒从偏移 16 开始。** 照 `16 + 未知` 解会整体错位，图案轻微斜切，肉眼几乎看不出来 ——
这个坑在 dotp2014decks 的 `cover.py` 里潜伏了很久。

三种格式：
  - FourCC `DXT1` / `DXT5`
  - D3DFormat 21 = A8R8G8B8、22 = X8R8G8B8（裸 BGRA，要从后往前排）
  - 其它一律当坏数据（D240 那 180 个 TDX 头里写的是非法值 13，解出来全是噪声）
"""

import struct

import numpy as np
from PIL import Image

import dxt

OK_ENUM = (21, 22)


def is_ok(raw):
    """头里的格式字段是不是认识的。取图前先拿它筛一遍，别等解码炸。"""
    if len(raw) < 16:
        return False
    fmt = raw[12:16]
    if fmt in (b"DXT1", b"DXT5"):
        return True
    return int.from_bytes(fmt, "little") in OK_ENUM


def decode(raw):
    """TDX bytes -> PIL RGBA Image。解不了抛 ValueError。"""
    if len(raw) < 16:
        raise ValueError("TDX 太短（%d 字节）" % len(raw))
    _a, w, h, _mips, _unk = struct.unpack_from("<4HI", raw, 0)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        raise ValueError("尺寸离谱 %dx%d" % (w, h))
    fmt = raw[12:16]
    if fmt == b"DXT1":
        px = dxt.dxt1_decode(raw[16:], w, h)
    elif fmt == b"DXT5":
        px = dxt.dxt5_decode(raw[16:], w, h)
    elif int.from_bytes(fmt, "little") in OK_ENUM:
        need = w * h * 4
        if len(raw) - 16 < need:
            raise ValueError("裸 BGRA 数据不够（要 %d，只有 %d）" % (need, len(raw) - 16))
        px = np.frombuffer(raw[16:16 + need], np.uint8).reshape(h, w, 4)
        px = px[:, :, [2, 1, 0, 3]]            # BGRA -> RGBA
    else:
        raise ValueError("格式不认识：%s" % fmt.hex(" "))
    return Image.fromarray(px)
