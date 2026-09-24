# -*- coding: utf-8 -*-
"""把一张卡渲染成 PNG —— **给外部程序 / agent 用**。

前端是在浏览器里用 DOM 分层拼卡面的（文字保持矢量、放大不糊）。
但外部调用者往往只想要一张图，不该被迫起浏览器。所以这里用 PIL 做同样的事。

两边共用同一套坐标（`paths.py` 里的 `RC_*` 常量，基准 356×512，
来自 Deck Builder 的 `CardInfo.cs:79-107`）。

    from render import render_card
    img = render_card("_SHIVAN_DRAGON_CW_129730", width=744)
    img.save("shivan.png")

坐标换算到任意尺寸：所有矩形 × (width/356)。
"""

import os
import io

from PIL import Image, ImageDraw, ImageFont

import paths
from log import get_logger

log = get_logger("render")

# 中文字体候选。雅黑可读性最好，宋体更像真牌，都是 Windows 自带。
FONT_REGULAR = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
FONT_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]

_font_cache = {}
_base = 356          # 坐标常量都是按这个宽度定的


def _font(bold, size):
    key = (bold, size)
    if key in _font_cache:
        return _font_cache[key]
    for p in (FONT_BOLD if bold else FONT_REGULAR):
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _font_cache[key] = f
                return f
            except Exception as e:
                log.warning("加载字体失败 %s：%s", p, e)
    log.warning("没有可用的中文字体，回退到 PIL 默认位图字体（中文会变方块）")
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _asset(group, name):
    p = os.path.join(paths.FRAMES, group, name + ".png")
    if not os.path.exists(p):
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception as e:
        log.warning("读素材失败 %s：%s", p, e)
        return None


def _wrap(draw, text, font, max_w):
    """按可用宽度折行。中英混排都要处理，所以按字符累加测宽。"""
    out = []
    for para in (text or "").split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        for ch in para:
            trial = line + ch
            if draw.textlength(trial, font=font) <= max_w or not line:
                line = trial
            else:
                out.append(line)
                line = ch
        out.append(line)
    return out


def _pick_frame(card):
    """颜色 + 类型 -> 卡框文件名。和前端 `pickFrame` 同一套规则。"""
    colors = card.get("colors") or []
    t = (card.get("type") or "").lower()
    is_art = "artifact" in t
    is_land = "land" in t

    if not colors:
        if is_land:
            return ("c_artifact", "ptbox_a") if is_art else ("c_land", "")
        return ("c_artifact", "ptbox_a") if is_art else ("c", "ptbox_c")
    if len(colors) == 1:
        c = colors[0].lower()
        return ((c + "_artifact") if is_art else c, "ptbox_" + c)
    if len(colors) == 2:
        pair = "".join(x.lower() for x in "WUBRG" if x in colors)
        return ((pair + "_artifact") if is_art else pair, "ptbox_gold")
    return "z", "ptbox_gold"


def _mana_file(sym):
    s = sym.strip()
    if not s:
        return None
    if "/" in s:
        a, b = s.split("/", 1)
        if b.upper() == "P":
            cn = {"w": "white", "u": "blue", "b": "black",
                  "r": "red", "g": "green"}.get(a.lower())
            return ("phyrexian_%s_mana" % cn) if cn else None
        return "mana_" + (a + b).lower()
    return "mana_" + s.lower()


def _parse_cost(cost):
    import re
    return [m.group(1) for m in re.finditer(r"\{([^}]+)\}", cost or "")]


def _rich_lines(draw, text, font, max_w, sym_w):
    """把富文本切成行，每行是 `[(kind, 值, 宽度), …]`。

    `kind` 是 `"txt"`（一个字）或 `"sym"`（一个法术力符号）。
    PIL 不会自动换行，也不会混排图片，所以宽度全得自己算。
    """
    import re as _re
    tokens = []
    for part in _re.split(r"(\{[^}]+\})", text or ""):
        if not part:
            continue
        if len(part) > 2 and part.startswith("{") and part.endswith("}"):
            tokens.append(("sym", part[1:-1], sym_w))
        else:
            tokens.append(("txt", part, None))

    lines, cur, cur_w = [], [], 0.0
    for kind, val, w in tokens:
        if kind == "sym":
            if cur and cur_w + w > max_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append((kind, val, w))
            cur_w += w
            continue
        for ch in val:                      # 文字按字符断行（中英混排都适用）
            if ch == "\n":
                lines.append(cur)
                cur, cur_w = [], 0.0
                continue
            cw = draw.textlength(ch, font=font)
            if cur and cur_w + cw > max_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append(("txt", ch, cw))
            cur_w += cw
    if cur:
        lines.append(cur)
    return lines


def _draw_rich(canvas, draw, lines, x, y, font, fill, line_h, sym_px, limit_y):
    """把 `_rich_lines` 的结果画出来，符号用游戏自带的贴图。"""
    for ln in lines:
        if y + line_h > limit_y:
            break
        cx = x
        for kind, val, w in ln:
            if kind == "txt":
                draw.text((cx, y), val, font=font, fill=fill)
            else:
                fn = _mana_file(val)
                im = _asset("mana", fn) if fn else None
                if im is not None:
                    im = im.resize((sym_px, sym_px), Image.LANCZOS)
                    canvas.alpha_composite(im, (int(cx), int(y + (line_h - sym_px) * 0.30)))
                else:
                    draw.text((cx, y), val, font=font, fill=fill)
            cx += w
        y += line_h
    return y


RARITY_FILE = {"C": "expansion_common", "U": "expansion_uncommon",
               "R": "expansion_rare", "M": "expansion_mythic"}


def render_card(key, width=356, cardset=None, artcache=None) -> Image.Image:
    """卡池 key -> PIL Image（RGBA）。卡不存在抛 KeyError。"""
    if cardset is None:
        import cardset as m
        cardset = m.get()
    if artcache is None:
        import artcache as m
        artcache = m.get()

    card = cardset.full(key)
    if card is None:
        raise KeyError("卡池里没有这张卡：%s" % key)

    s = width / float(_base)
    W, H = width, int(round(512 * s))

    def rc(name):
        x, y, w, h = getattr(paths, name)
        return (int(x * s), int(y * s), int(w * s), int(h * s))

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))

    # ---- 1. 插画：**精确放进插画窗口**，不是铺满整张卡 ----
    #
    # 早先写成「铺满整张画布再用卡框遮住四周」，那样会**横着裁掉一半**：
    # 插画是 512×376（横），画布是 356×512（竖），cover 会按高度缩放 →
    # 宽度变成 697，水平居中后只剩中间 51%。分裂牌那种横向构图的卡看着就是
    # 「截到了两张画面的中间」。
    #
    # 插画窗口 324×238 的宽高比 1.361 和插画的 1.362 几乎一致，精确放置几乎不裁。
    art = None
    try:
        art = artcache.image(key, size="full")
    except Exception as e:
        log.warning("取插画失败 %s：%s", key, e)
    if art is not None:
        ax, ay, aw, ah = rc("RC_ILLUSTRATION")
        r = max(aw / float(art.width), ah / float(art.height))
        a = art.resize((max(1, int(round(art.width * r))),
                        max(1, int(round(art.height * r)))), Image.LANCZOS)
        left = max(0, (a.width - aw) // 2)
        top = max(0, (a.height - ah) // 2)
        a = a.crop((left, top, min(left + aw, a.width), min(top + ah, a.height)))
        canvas.paste(a, (ax, ay))

    frame_name, ptbox = _pick_frame(card)
    frame = _asset("frames", frame_name)
    if frame is None:
        frame = _asset("frames", "c")
    if frame is None:
        log.error("找不到卡框素材（data/frames/frames/）—— 跑 tools/export_frames.py")
        return canvas
    canvas.alpha_composite(frame.resize((W, H), Image.LANCZOS))

    draw = ImageDraw.Draw(canvas)

    # ---- 2. 牌名 ----
    # 基准字号和前端 `CardFace` 保持一致。放不下时**先缩字号**（最低 70%），
    # 实在不行才截断加省略号 —— 直接砍字会让「万世创伤伊莫库」变成「万世创伤伊莫」。
    x, y, w, h = rc("RC_NAME")
    name = card.get("zh") or card.get("en") or ""
    f = None
    for scale in (1.0, 0.92, 0.84, 0.76, 0.70):
        f = _font(True, max(6, int(19.9 * s * scale)))
        if draw.textlength(name, font=f) <= w:
            break
    else:
        while draw.textlength(name + "…", font=f) > w and len(name) > 2:
            name = name[:-1]
        name += "…"
    draw.text((x, y + h / 2), name, font=f, fill=(10, 10, 10, 255), anchor="lm")

    # ---- 3. 费用符号（右上角，右对齐） ----
    syms = _parse_cost(card.get("cost") or "")
    if syms:
        size = int(32 * s)
        gap = max(1, int(2 * s))
        total_w = len(syms) * (size + gap) - gap
        cx = int(344 * s) - total_w
        cy = int(13 * s)
        for sym in syms:
            fn = _mana_file(sym)
            im = _asset("mana", fn) if fn else None
            if im is not None:
                canvas.alpha_composite(im.resize((size, size), Image.LANCZOS),
                                       (cx, cy))
            else:
                draw.rectangle([cx, cy, cx + size, cy + size],
                               fill=(20, 20, 20, 220))
                draw.text((cx + size / 2, cy + size / 2), sym,
                          font=_font(True, max(6, int(10 * s))),
                          fill=(255, 255, 255, 255), anchor="mm")
            cx += size + gap

    # ---- 4. 类型行 ----
    x, y, w, h = rc("RC_TYPE_LINE")
    line = card.get("line") or card.get("type") or ""
    f = None
    for scale in (1.0, 0.9, 0.8, 0.72):
        f = _font(True, max(5, int(15.3 * s * scale)))
        if draw.textlength(line, font=f) <= w:
            break
    else:
        while draw.textlength(line + "…", font=f) > w and len(line) > 2:
            line = line[:-1]
        line += "…"
    draw.text((x, y + h / 2), line, font=f, fill=(15, 15, 15, 255), anchor="lm")

    # ---- 5. 系列稀有度符号 ----
    rf = RARITY_FILE.get((card.get("rarity") or "").upper())
    if rf:
        im = _asset("misc", rf)
        if im is not None:
            x, y, w, h = rc("RC_EXPANSION")
            canvas.alpha_composite(im.resize((w, h), Image.LANCZOS), (x, y))

    # ---- 6. 规则文本 + 风味 ----
    # 字号按长度分档，和前端 `CardFace` 的 `fit` 一套逻辑 ——
    # 文本区只占卡面 29%，长文本（分裂牌那种）不缩就会溢出。
    x, y, w, h = rc("RC_TEXT")
    text = card.get("text") or ""
    flavor = card.get("flavor") or ""
    n = len(text) + len(flavor) * 0.85
    fit = 1.0 if n <= 150 else 0.92 if n <= 210 else 0.84 if n <= 300 \
        else 0.76 if n <= 420 else 0.68

    # 字号加粗 + `{R}`/`{T}` 换成游戏自带的法术力符号图（和前端 `RichText` 一致）
    fs = max(5, int(15.0 * s * fit))
    f = _font(True, fs)
    line_h = int(fs * 1.38)
    sym_px = max(5, int(fs * 1.30))
    ty = _draw_rich(canvas, draw, _rich_lines(draw, text, f, w, sym_px),
                    x, y, f, (10, 10, 10, 255), line_h, sym_px, y + h)

    if flavor:
        ffs = max(5, int(13.5 * s * fit))
        ff = _font(True, ffs)
        flh = int(ffs * 1.26)
        ty += int(fs * 0.5)
        fsym = max(5, int(ffs * 1.30))
        _draw_rich(canvas, draw, _rich_lines(draw, flavor, ff, w, fsym),
                   x, ty, ff, (46, 46, 46, 255), flh, fsym, y + h)

    # ---- 7. 力量/防御 ----
    pw, tg = card.get("power"), card.get("tough")
    if pw and tg:
        x, y, w, h = rc("RC_PT_BOX")
        im = _asset("ptbox", ptbox or "ptbox_c")
        if im is not None:
            canvas.alpha_composite(im.resize((w, h), Image.LANCZOS), (x, y))
        draw.text((x + w * 0.62, y + h / 2), "%s/%s" % (pw, tg),
                  font=_font(True, max(7, int(26 * s))),
                  fill=(8, 8, 8, 255), anchor="mm")

    return canvas


def render_png(key, width=356, **kw) -> bytes:
    """同上，直接给 PNG 字节 —— 网络端点用。"""
    img = render_card(key, width=width, **kw)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_sheet(keys, cols=5, width=356, gap=8, bg=(14, 16, 22)):
    """把多张卡拼成一张联系表（contact sheet）—— 一次看一批卡很方便。"""
    cs = keys[:]
    if not cs:
        return Image.new("RGB", (10, 10), bg)
    w, h = width, int(round(512 * width / 356.0))
    rows = (len(cs) + cols - 1) // cols
    out = Image.new("RGB", (cols * w + (cols + 1) * gap,
                            rows * h + (rows + 1) * gap), bg)
    for i, k in enumerate(cs):
        try:
            img = render_card(k, width=width)
        except Exception as e:
            log.warning("联系表跳过 %s：%s", k, e)
            continue
        r, c = divmod(i, cols)
        out.paste(img.convert("RGB"),
                  (gap + c * (w + gap), gap + r * (h + gap)))
    return out


def main():
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    key = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 356
    out = sys.argv[3] if len(sys.argv) > 3 else (key.strip("_") + ".png")
    img = render_card(key, width=w)
    img.save(out)
    print("%s -> %s (%dx%d)" % (key, out, img.width, img.height))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
