# -*- coding: utf-8 -*-
"""FastAPI 路由。

设计取舍
--------
**卡图不走 IPC 传字节**：前端 `<img src="/api/art/xxx">` 由浏览器自己管并发和缓存，
后端命中磁盘缩略图就是静态文件。2.2 万张图不能用 JSON 塞进响应体。

**搜索分页**（默认 175 条）—— 这是 phase 两万张卡不卡的根本原因，
它压根没用虚拟滚动，就是限流。这里照抄。

**错误一律结构化**：任何异常都返回
`{"ok": false, "error": {"type","msg","where","trace","hint"}}` 并写进 `data/logs/app.log`，
界面上的「诊断」面板读 `/api/logs` 就能看到 —— 用户截图就能报障，不用去翻目录。
"""

import io
import os
import time
import traceback

from fastapi import FastAPI, Request, Query, Body
from fastapi.responses import (JSONResponse, Response, FileResponse,
                               PlainTextResponse)
from fastapi.staticfiles import StaticFiles

import paths
import settings
import deck as deckmod
from log import get_logger, recent, logfile

log = get_logger("api")

app = FastAPI(title="DotP 2014 卡组编辑器", docs_url="/api/docs")

_cardset = None
_artcache = None


def cardset():
    global _cardset
    if _cardset is None:
        import cardset as m
        _cardset = m.get()
    return _cardset


def artcache():
    global _artcache
    if _artcache is None:
        import artcache as m
        _artcache = m.get()
    return _artcache


# ---------------------------------------------------------------- 错误处理

HINTS = {
    "FileNotFoundError": "有文件找不到 —— 检查游戏目录设置和 data/ 下的索引",
    "PermissionError": "文件被占用或无权限 —— 游戏可能开着，或索引只读",
    "MemoryError": "内存不够 —— 试试关掉其它程序，或调小缩略图尺寸",
    "KeyError": "取不到某个字段 —— 索引可能是旧版本建的，跑 tools/rebuild.py --force",
    "JSONDecodeError": "索引文件损坏 —— 跑 tools/rebuild.py --force 重建",
    "ValueError": "数据不合法（常见于解码坏贴图）",
}


def _err_payload(e, where=""):
    tb = traceback.format_exc()
    return {
        "ok": False,
        "error": {
            "type": type(e).__name__,
            "msg": str(e),
            "where": where,
            "hint": HINTS.get(type(e).__name__, ""),
            "trace": tb[-2500:],
        },
    }


@app.exception_handler(Exception)
async def _on_error(request: Request, exc: Exception):
    where = "%s %s" % (request.method, request.url.path)
    log.error("请求出错 %s：%s: %s", where, type(exc).__name__, exc, exc_info=True)
    return JSONResponse(_err_payload(exc, where), status_code=500)


def api(fn):
    """给只读端点套一层 —— 出错也返回 200 + `ok:false`，前端统一处理。"""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        t0 = time.time()
        try:
            data = fn(*a, **kw)
            out = {"ok": True, "data": data}
        except Exception as e:
            log.warning("%s 出错：%s: %s", fn.__name__, type(e).__name__, e,
                        exc_info=True)
            out = _err_payload(e, fn.__name__)
        out["ms"] = int((time.time() - t0) * 1000)
        return out
    return wrapper


# ---------------------------------------------------------------- 基础

@app.get("/api/health")
def health():
    import json
    st = {}
    for name, p in (("pool", paths.POOL_JSON), ("details", paths.DETAILS_JSON),
                    ("art", paths.ARTIDX_JSON), ("frames", paths.FRAMEIDX_JSON)):
        st[name] = os.path.exists(p) and os.path.getsize(p) > 0
    cfg = settings.load()
    return {
        "ok": True, "version": "0.1.0",
        "game_dir": cfg.get("game_dir"),
        "game_dir_ok": os.path.isdir(cfg.get("game_dir") or ""),
        "indexes": st,
        "log_file": logfile(),
    }


@app.get("/api/logs")
def logs(limit: int = 100):
    return {"ok": True, "data": recent(limit), "file": logfile()}


@app.get("/api/logtail")
def logtail(lines: int = 200):
    """日志文件尾部 —— 诊断面板用。"""
    try:
        with open(logfile(), encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return PlainTextResponse("".join(all_lines[-lines:]))
    except FileNotFoundError:
        return PlainTextResponse("（还没有日志）")


# ---------------------------------------------------------------- 配置

@app.get("/api/settings")
@api
def get_settings():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
    import rebuild
    cfg = dict(settings.load())
    cfg["_index_status"] = {
        name: {"fresh": f, "why": w}
        for name, (f, _m, w) in rebuild.status(cfg.get("game_dir")).items()
    }
    cfg["_thumbs"] = artcache().stats()
    return cfg


@app.post("/api/settings")
@api
def set_settings(patch: dict = Body(...)):
    old = settings.load().get("game_dir")
    cfg = settings.save(patch)
    changed = old != cfg.get("game_dir")
    if changed:
        # 换目录后所有索引和句柄都失效
        global _cardset, _artcache
        import cardset as cs
        import artcache as ac
        ac.reset()
        cs.reset()
        _cardset = _artcache = None
    return {"settings": cfg, "game_dir_changed": changed}


@app.post("/api/browse-dir")
@api
def browse_dir(payload: dict = Body(default=None)):
    """弹原生目录选择框。只有 pywebview 在跑的时候可用 —— 浏览器模式下让用户手输。"""
    try:
        import webview
    except ImportError:
        return {"path": None, "reason": "没装 pywebview，请手动输入路径"}
    if not getattr(webview, "windows", None):
        return {"path": None, "reason": "窗口还没建好，请手动输入路径"}
    try:
        w = webview.windows[0]
        r = w.create_file_dialog(webview.FOLDER_DIALOG,
                                 directory=(payload or {}).get("start") or "")
        if not r:
            return {"path": None, "reason": "取消了"}
        return {"path": r[0] if isinstance(r, (list, tuple)) else r}
    except Exception as e:
        log.warning("目录选择框打不开：%s", e)
        return {"path": None, "reason": "对话框打不开，请手动输入路径"}


# ---------------------------------------------------------------- WAD 维护

@app.get("/api/wads")
@api
def list_wads():
    import wadtools
    ws = wadtools.list_wads()
    return {"dir": settings.game_dir(), "count": len(ws),
            "bytes": sum(w["size"] for w in ws), "wads": ws}


@app.post("/api/wads/backup")
@api
def backup_wads(payload: dict = Body(default=None)):
    """把 WAD 整包备份到 `data/wad_backup/`（已存在的跳过）。"""
    import wadtools
    return wadtools.backup_wads(names=(payload or {}).get("names"))


@app.get("/api/wads/backups")
@api
def wad_backups():
    import wadtools
    return wadtools.backup_list()


@app.post("/api/wads/restore")
@api
def restore_wad(payload: dict = Body(...)):
    import wadtools
    return wadtools.restore_backup(payload.get("name") or "")


@app.get("/api/bsf/scan")
@api
def bsf_scan():
    """扫所有 WAD 里的 `.bsf`，看有没有会让引擎越界读的。

    `DATA_DECKS_D910.WAD`（中文包）历史上中过这个招 —— 9 个 `.bsf` 末尾各多
    一个 `0x00`，导致**约 40% 概率、启动 4 秒后随机崩溃**。游戏报错时可以试试这个。
    """
    import wadtools
    return wadtools.scan()


@app.post("/api/bsf/fix")
@api
def bsf_fix(payload: dict = Body(...)):
    import wadtools
    return wadtools.fix(payload.get("wad") or "", verbose=True)


@app.post("/api/reindex")
@api
def reindex(force: bool = Body(False, embed=True)):
    """重建索引。前端加个按钮 + 进度提示。"""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
    import rebuild
    out = rebuild.ensure_all(force=force, verbose=True)
    global _cardset, _artcache
    import cardset as cs
    import artcache as ac
    ac.reset()
    cs.reset()
    _cardset = _artcache = None
    return {k: {"count": (v or {}).get("count"),
                "art_count": (v or {}).get("art_count")} for k, v in out.items()}


# ---------------------------------------------------------------- 卡牌

@app.get("/api/meta")
@api
def meta():
    return cardset().meta()


@app.get("/api/cards")
@api
def cards(
    q: str = "", colors: str = "", color_mode: str = "any", type: str = "",
    sub: str = "", cmc_min: int = None, cmc_max: int = None, rarity: str = "",
    kw: str = "", set: str = "", src: str = "", legal: str = "",
    has_art: bool = False, limit: int = 175, offset: int = 0,
    sort: str = "name", desc: bool = False, focus: str = "",
):
    """逗号分隔的多选参数：`colors=W,U`、`rarity=R,M`、`kw=A,B`。

    `focus=<key>` 额外返回 `focus_index` —— 这张卡在**完整结果**里排第几。
    界面拿它算出该加载哪一页，好把卡池滚到那张卡（卡片本身可能没被当前页加载）。
    """
    def split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()]

    cs = cardset()
    ac = artcache()
    keys, total, focus_index = cs.search(
        q=q, colors=split(colors), color_mode=color_mode, type=type, sub=sub,
        cmc_min=cmc_min, cmc_max=cmc_max, rarity=split(rarity), kw=split(kw),
        set_=split(set), src=split(src), legal=split(legal),
        has_art=has_art, limit=limit, offset=offset, sort=sort, desc=desc,
        art_checker=ac.has_art if has_art else None, focus=focus or None)
    return {
        "total": total, "offset": offset, "limit": limit,
        "focus_index": focus_index,
        "items": [cs.slim(k) for k in keys],
    }


@app.get("/api/cardkey")
@api
def cardkey(name: str = ""):
    """卡名（中英文都认）-> **卡池 key**；查不到返回 `{"key": ""}`。

    「拖回卡池删除后跳过去」那条链需要 key。之前只从界面缓存里翻，
    缓存里可能混着 `key` 是空的条目，于是 `focus` 参数被 URLSearchParams
    静默丢掉、后端收不到，表现成「怎么都跳不过去」。这里给它一个权威来源。

    路径和 `/api/card/{key}` 不冲突（`cardkey` 是独立字面量，不在 `/api/card/` 下面），
    但**别改名叫 `/api/card/name`** —— 那会被 `{key}` 吞掉。
    """
    k = cardset().key_of(name) if name else ""
    return {"key": k, "name": name}


@app.get("/api/card/{key}")
@api
def card(key: str):
    c = cardset().full(key)
    if c is None:
        raise KeyError("卡池里没有这张卡：%s" % key)
    c["has_art"] = artcache().has_art(key)
    return c


# ---------------------------------------------------------------- 图片

def _img_response(data, media_type, cache_seconds=86400 * 30):
    return Response(
        content=data, media_type=media_type,
        headers={"Cache-Control": "public, max-age=%d" % cache_seconds})


@app.get("/api/art/{key}")
def art(key: str, size: str = "thumb"):
    """插画。`size=thumb` 走磁盘 webp 缓存；`size=full` 现解码。"""
    ac = artcache()
    try:
        if size == "thumb":
            p = ac.thumb_path(key)
            if p and os.path.exists(p):
                return FileResponse(p, media_type="image/webp",
                                    headers={"Cache-Control": "public, max-age=2592000"})
        img = ac.image(key, size=("thumb" if size == "thumb" else "full"))
        if img is None:
            return Response(status_code=404)
        buf = io.BytesIO()
        if size == "thumb":
            img.convert("RGB").save(buf, "WEBP", quality=78, method=4)
            return _img_response(buf.getvalue(), "image/webp")
        img.save(buf, "PNG", optimize=True)
        return _img_response(buf.getvalue(), "image/png", 86400 * 7)
    except Exception as e:
        log.warning("取图失败 %s：%s", key, e)
        return Response(status_code=500)


@app.get("/api/deckbox/{name}")
def deckbox(name: str):
    """牌盒封面（牌组 XML 里的 `deck_box_image`）。

    已经**裁到正面**了（原图是 512×512 的 3D 盒子渲染，四周全是透明留白，
    左边还有条盒脊）。界面上直接丢进 `background-image` 就行。
    """
    ac = artcache()
    try:
        p = ac.box_thumb_path(name)
        if p and os.path.exists(p):
            return FileResponse(p, media_type="image/webp",
                                headers={"Cache-Control": "public, max-age=2592000"})
        img = ac.box_image(name)
        if img is None:
            return Response(status_code=404)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "WEBP", quality=82, method=4)
        return _img_response(buf.getvalue(), "image/webp")
    except Exception as e:
        log.warning("取牌盒封面失败 %s：%s", name, e)
        return Response(status_code=500)


@app.get("/api/preview/cover")
def preview_cover(key: str, zoom: float = 1.0):
    """合成后的**牌盒封面**预览 PNG（带透明）。`key` 是卡池 key 或中英文卡名。

    走的是和打包时**同一条** `artgen.make_cover()`，所见即所得。
    """
    import artgen
    try:
        img = _preview_art(key)
        if img is None:
            return Response(status_code=404)
        return _img_response(
            artgen.preview_png(artgen.make_cover(img, zoom=max(0.2, min(zoom, 3.0)))),
            "image/png", 86400)
    except Exception as e:
        log.warning("封面预览失败 %s：%s", key, e)
        return Response(status_code=500)


@app.get("/api/preview/portrait")
def preview_portrait(key: str, kind: str = "avatar"):
    """合成后的**鹏洛客立绘**预览 PNG。`kind`：avatar / locked / backplate / full。"""
    import artgen
    try:
        img = _preview_art(key)
        if img is None:
            return Response(status_code=404)
        fn = {"avatar": lambda: artgen.make_avatar(img),
              "locked": lambda: artgen.make_avatar(img, True),
              "backplate": lambda: artgen.make_backplate(img),
              "full": lambda: artgen.make_full(img)}.get(kind)
        if fn is None:
            return Response(status_code=400)
        return _img_response(artgen.preview_png(fn()), "image/png", 86400)
    except Exception as e:
        log.warning("立绘预览失败 %s：%s", key, e)
        return Response(status_code=500)


def _preview_art(key):
    """卡池 key / 卡名 -> 原图 Image。取不到返回 None。"""
    cs = cardset()
    k = key if key in cs.cards else (cs.key_of(key) or "")
    return artcache().image(k, size="full") if k else None


@app.get("/api/frames/{group}/{name}")
def frame(group: str, name: str):
    """卡面素材 PNG（卡框 / PT 框 / 法术力符号 / 系列符号）。"""
    # 防目录穿越
    if "/" in name or "\\" in name or ".." in name or "/" in group or ".." in group:
        return Response(status_code=400)
    p = os.path.join(paths.FRAMES, group, name if name.endswith(".png") else name + ".png")
    if not os.path.exists(p):
        return Response(status_code=404)
    return FileResponse(p, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=2592000"})


# ------------------------------------------------- 卡面渲染（给外部/agent 用）

def _card_key(name_or_key: str):
    """既接受卡池 key，也接受中英文卡名 —— agent 手里通常只有卡名。"""
    cs = cardset()
    if name_or_key in cs.cards:
        return name_or_key
    k = cs.key_of(name_or_key)
    if k:
        return k
    raise KeyError("找不到这张卡：%s" % name_or_key)


@app.get("/api/render/{name}")
def render_one(name: str, width: int = 356, fmt: str = "png"):
    """把一张卡渲染成图。`name` 可以是卡池 key，也可以是中英文卡名。

    例：`/api/render/Shivan%20Dragon?width=744`
    """
    import render as rendermod
    try:
        key = _card_key(name)
        if fmt.lower() == "webp":
            img = rendermod.render_card(key, width=max(80, min(width, 1600)))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "WEBP", quality=90, method=4)
            return _img_response(buf.getvalue(), "image/webp", 86400)
        return _img_response(
            rendermod.render_png(key, width=max(80, min(width, 1600))),
            "image/png", 86400)
    except KeyError as e:
        return JSONResponse(_err_payload(e, "render"), status_code=404)
    except Exception as e:
        log.error("渲染失败 %s：%s", name, e, exc_info=True)
        return JSONResponse(_err_payload(e, "render"), status_code=500)


@app.post("/api/render-sheet")
@api
def render_sheet(payload: dict = Body(...)):
    """多张卡拼一张联系表。`{"cards": ["Shivan Dragon", ...], "cols": 5, "width": 240}`"""
    import render as rendermod
    names = payload.get("cards") or []
    keys = [_card_key(n) for n in names]
    img = rendermod.render_sheet(
        keys, cols=int(payload.get("cols") or 5),
        width=int(payload.get("width") or 240))
    import base64
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return {"png_base64": base64.b64encode(buf.getvalue()).decode(),
            "width": img.width, "height": img.height, "count": len(keys)}


@app.get("/api/framelist")
@api
def framelist():
    """前端要知道有哪些卡框可选（按颜色/类型推导）。"""
    out = {}
    for sub in ("frames", "ptbox", "mana", "misc"):
        d = os.path.join(paths.FRAMES, sub)
        out[sub] = sorted(f[:-4] for f in os.listdir(d)) if os.path.isdir(d) else []
    return out


# ---------------------------------------------------------------- 卡组

def _deck_cards(d, cs):
    """牌组里用到的卡的详情 —— 界面要拿它显示费用/颜色/类别。

    牌组存的是**卡名**，不是 key，所以要过一次 `key_of`。查不到的卡
    （卡池里没有，比如名字拼错）会返回 None，这里跳过但记日志。
    """
    out, seen = [], set()
    for name in list(d.get("main", {})) + list(d.get("side", {})):
        k = cs.key_of(name)
        if not k:
            log.warning("牌组 %s 里的卡在卡池里找不到：%s", d.get("id"), name)
            continue
        if k in seen:
            continue
        seen.add(k)
        s = cs.slim(k)
        if s:
            out.append(s)
    return out


# ------------------------------------------------- 游戏里已有的牌组（只读浏览）

@app.get("/api/gamedecks")
@api
def gamedecks_list():
    """按「自制 / 系统自带 / 社区包」分组的游戏牌组。

    扫的是游戏目录里所有 WAD 的 `/DECKS/*.XML`（128 副），不是本程序的工程目录。
    """
    import gamedecks
    groups = gamedecks.list_grouped()
    _decks, meta = gamedecks.load()
    return {"groups": groups, "meta": meta,
            "total": sum(g["count"] for g in groups)}


@app.get("/api/gamedecks/{deck_id:path}/writable")
@api
def gamedeck_writable(deck_id: str):
    """能不能直接改这个牌组，以及改它的后果。前端据此决定弹不弹警告。"""
    import deckwrite
    ok, why = deckwrite.writable(deck_id)
    import gamedecks
    d = gamedecks.get(deck_id)
    return {"writable": ok, "reason": why,
            "group": (d or {}).get("group"),
            "wad": (d or {}).get("wad")}


@app.post("/api/gamedecks/{deck_id:path}/save")
@api
def gamedeck_save(deck_id: str, payload: dict = Body(...)):
    """**把卡表直接写回游戏里的 WAD**（不是复制，是覆盖原牌组）。

    `cards` 是**已展开的卡名列表**（顺序 = `deckOrderId`）；后端翻成卡池的
    `<FILENAME>` 再写。`allow_system=true` 才允许改官方/社区包 —— 界面必须先弹警告。

    写之前自动整包备份到 `data/backups/`，走 `Wad.rebuild()` 做外科手术
    （**不能重新打包** —— 那会丢 headerXml 和空目录，游戏会变英文、中文变方块）。
    """
    import deckwrite
    cs = cardset()
    names = payload.get("cards") or []
    keys, unknown = [], []
    for n in names:
        k = cs.key_of(n)
        if k:
            keys.append(k)
        else:
            unknown.append(n)
    if not keys:
        raise ValueError("卡表是空的")
    r = deckwrite.save(deck_id, keys,
                       payload.get("land_config"),
                       allow_system=bool(payload.get("allow_system")))
    r["unknown"] = unknown
    return r


@app.delete("/api/gamedecks/{deck_id:path}")
@api
def gamedeck_delete(deck_id: str):
    """删除自制牌组（把整个包挪到 `data/trash/`，不是真删）。"""
    import deckwrite
    import gamedecks
    r = deckwrite.delete(deck_id)
    # 删完要重扫，否则列表里还在
    gamedecks.build(settings.game_dir(), force=True, verbose=False)
    return r


@app.get("/api/backups")
@api
def list_backups():
    import deckwrite
    return deckwrite.backups()


@app.get("/api/trash")
@api
def list_trash():
    import deckwrite
    return deckwrite.trash()


@app.post("/api/trash/restore")
@api
def restore_trash(payload: dict = Body(...)):
    import deckwrite
    import gamedecks
    r = deckwrite.untrash(payload.get("stamp") or "", payload.get("name") or "")
    gamedecks.build(settings.game_dir(), force=True, verbose=False)
    return r


@app.post("/api/backups/restore")
@api
def restore_backup(payload: dict = Body(...)):
    import deckwrite
    return deckwrite.restore(payload.get("name") or "")


@app.post("/api/gamedecks/{deck_id:path}/import")
@api
def gamedeck_import(deck_id: str, payload: dict = Body(default=None)):
    """把游戏里的牌组**复制成一份可编辑的工程**。

    不直接改原牌组 —— 游戏自带的那些是只读的参考，
    而且 `never_available` 的（boss 牌组）改了也没法用。
    """
    import gamedecks
    cs = cardset()
    d = gamedecks.get(deck_id)
    if d is None:
        raise FileNotFoundError("没有这副牌组：%s" % deck_id)
    proj, unmapped = gamedecks.to_project(d, cs)
    if (payload or {}).get("name_cn"):
        proj["name_cn"] = payload["name_cn"]

    # id 撞了就加后缀
    base, i = proj["id"], 2
    import deck as dm
    while dm.load(proj["id"]):
        proj["id"] = "%s-%d" % (base, i)
        i += 1
    proj = dm.save(proj)
    return {"deck": proj, "stats": dm.stats(proj, cs),
            "unmapped": unmapped,
            "issues": dm.validate(proj, cs)}


# ⚠️ 这个**必须放在最后** —— `{deck_id:path}` 是贪婪匹配，会吞掉
# `/writable` `/save` `/import` 这些子路由。FastAPI 按注册顺序匹配，
# 通用路由排在前面的话，子路由永远进不去（踩过）。
@app.get("/api/gamedecks/{deck_id:path}")
@api
def gamedeck_get(deck_id: str):
    import gamedecks
    d = gamedecks.get(deck_id)
    if d is None:
        raise FileNotFoundError("没有这副牌组：%s" % deck_id)
    # 把 FILENAME 翻成可读名字，界面才显示得出来
    cs = cardset()

    def details(bag):
        out = []
        for key, cnt in (bag or {}).items():
            s = cs.slim(key)
            if s:
                s["count"] = cnt
                out.append(s)
            else:
                out.append({"key": key, "en": key, "zh": key, "count": cnt,
                            "missing": True})
        return out

    d = dict(d)
    d["card_details"] = details(d.get("cards"))
    # 解锁表（游戏里的「已解锁」那 30 张）。**不返回的话界面「解锁表」标签永远是 0** ——
    # 官方牌组里那 13 副是有的（uid 1~10 的玩家牌组 + 几副 DLC）。
    d["unlock_details"] = details(d.get("unlocks"))
    d.pop("card_order", None)
    return d


@app.get("/api/decks")
@api
def decks_list():
    cs = cardset()
    return [{"deck": d, "stats": deckmod.stats(d, cs)} for d in deckmod.list_all()]


@app.get("/api/decks/{deck_id}")
@api
def deck_get(deck_id: str):
    cs = cardset()
    d = deckmod.load(deck_id)
    if d is None:
        raise FileNotFoundError("没有这个卡组：%s" % deck_id)
    return {"deck": d, "stats": deckmod.stats(d, cs), "cards": _deck_cards(d, cs)}


@app.post("/api/decks")
@api
def deck_new(payload: dict = Body(default=None)):
    payload = payload or {}
    d = deckmod.blank(payload.get("name_cn") or "新卡组",
                      payload.get("name_en") or "New Deck")
    # 撞车就加后缀 —— **id、名字、uid 三个一起加**。
    #
    # 界面上「新建」是一键、默认名 `NEW`，连点很容易出好几副同名草稿。
    # 只改 id 的话它们的 `uid` 全是 `DATA_DLC_NEW`：列表里几行都叫「NEW」
    # 分不出谁是谁，将来打包还会落到**同一个 WAD 文件名**上互相覆盖。
    #
    # ⚠️ 基准值要在循环**外面**取 —— 在循环里读 `d["name_cn"]` 会每轮
    # 追加一次后缀，三轮下来变成「NEW 2 3 4」。
    base, base_cn = d["id"], d["name_cn"]
    base_en, base_uid = d["name_en"], d["uid"]
    i = 2
    while deckmod.load(d["id"]):
        d["id"] = "%s-%d" % (base, i)
        d["name_cn"] = "%s %d" % (base_cn, i)
        d["name_en"] = "%s %d" % (base_en, i)
        d["uid"] = "%s%d" % (base_uid, i)
        i += 1
    return deckmod.save(d)


@app.put("/api/decks/{deck_id}")
@api
def deck_save(deck_id: str, payload: dict = Body(...)):
    payload = dict(payload)
    payload["id"] = deck_id
    return deckmod.save(payload)


@app.delete("/api/decks/{deck_id}")
@api
def deck_delete(deck_id: str):
    return {"deleted": deckmod.delete(deck_id)}


@app.post("/api/decks/{deck_id}/duplicate")
@api
def deck_duplicate(deck_id: str, payload: dict = Body(default=None)):
    """复制一份工程。返回新卡组（含 stats）。

    **注册在 `{deck_id}` 的 PUT/DELETE 之后**，而且路径不一样，所以不会被吞。
    """
    d = deckmod.duplicate(deck_id, (payload or {}).get("suffix") or " 副本")
    if d is None:
        raise FileNotFoundError("没有这份卡组：%s" % deck_id)
    return {"deck": d, "stats": deckmod.stats(d)}


@app.get("/api/decks/{deck_id}/pack-plan")
@api
def deck_pack_plan(deck_id: str, cover: str = "", avatar: str = ""):
    """打包前的体检 —— 不写任何文件。

    返回 `{ok, problems, info, cover, avatar, uid, uid_num, filename}`。
    最要命的一条是 **`<CARD>` 条数 + 补的地必须正好 60**。
    """
    import packer
    d = deckmod.load(deck_id)
    if d is None:
        raise FileNotFoundError("没有这份卡组：%s" % deck_id)
    cs = cardset()
    p = packer.plan(d, cs)
    return {
        "ok": p["ok"], "problems": p["problems"], "info": p["info"],
        "cover": cover or d.get("cover") or "",
        "avatar": avatar or d.get("avatar") or d.get("cover") or "",
        "uid": packer.uid_name(d),
        "uid_num": packer.peek_uid_num(),      # 只看一眼，不占号
        "filename": packer.uid_name(d) + ".wad",
    }


@app.post("/api/decks/{deck_id}/pack")
@api
def deck_pack(deck_id: str, payload: dict = Body(default=None)):
    """**把这份卡组打包成游戏 WAD**，默认直接装进游戏目录。

    - `cover` / `avatar`：卡池 key（中英文卡名也认）。`avatar` 不给就跟 `cover`
    - `install`：默认 true。装之前若游戏目录已有同名包，**先自动备份**到 `data/backups/`
    - 写盘走临时文件 + `os.replace` 原子替换

    打包前跑 `packer.plan()`，不合格直接 `PackError`（前端把 `msg` 给用户看）。
    """
    import packer
    d = deckmod.load(deck_id)
    if d is None:
        raise FileNotFoundError("没有这份卡组：%s" % deck_id)
    cs = cardset()

    def resolve(v):
        if not v:
            return ""
        return v if v in cs.cards else (cs.key_of(v) or "")

    body = payload or {}
    cover = resolve(body.get("cover") or d.get("cover") or "")
    avatar = resolve(body.get("avatar") or d.get("avatar") or "") or cover
    if not cover:
        raise packer.PackError("要先选一张卡当牌盒封面")

    # 把选择记进工程，下次打开还是这张
    d["cover"] = cover
    d["avatar"] = avatar
    deckmod.save(d)

    return packer.pack(d, cs, cover, avatar,
                       install=body.get("install", True),
                       overwrite=bool(body.get("overwrite")))


@app.post("/api/decks/{deck_id}/cards")
@api
def deck_edit_cards(deck_id: str, payload: dict = Body(...)):
    """增删改牌 —— **按卡名操作，不用先查 key**（agent 手里通常只有卡名）。

    `{"add": {"Oath of Druids": 4}, "remove": {"Ponder": 1},
      "set": {"Sol Ring": 3}, "section": "main" | "side"}`

    `add` / `remove` 会累积（负数结果自动删掉那一行）；`set` 是直接覆盖数量，0 等于删。
    卡名走归一化，中英文都能认；认不出的会进 `unknown` 返回，不静默丢弃。
    """
    cs = cardset()
    d = deckmod.load(deck_id)
    if d is None:
        raise FileNotFoundError("没有这个卡组：%s" % deck_id)
    section = payload.get("section") or "main"
    if section not in ("main", "side"):
        raise ValueError("section 只能是 main 或 side")

    box = dict(d.get(section) or {})
    unknown = []

    def resolve(name):
        k = cs.key_of(name)
        return cs.cards[k].get("en") if k else ""

    for name, cnt in (payload.get("add") or {}).items():
        real = resolve(name)
        if not real:
            unknown.append(name)
            continue
        box[real] = box.get(real, 0) + int(cnt)

    for name, cnt in (payload.get("remove") or {}).items():
        real = resolve(name)
        if not real:
            unknown.append(name)
            continue
        left = box.get(real, 0) - int(cnt)
        if left > 0:
            box[real] = left
        else:
            box.pop(real, None)

    for name, cnt in (payload.get("set") or {}).items():
        real = resolve(name)
        if not real:
            unknown.append(name)
            continue
        n = int(cnt)
        if n > 0:
            box[real] = n
        else:
            box.pop(real, None)

    d[section] = box
    if payload.get("min_lands") is not None:
        d["min_lands"] = {k: int(v) for k, v in (payload["min_lands"] or {}).items()
                          if int(v) > 0}
    d = deckmod.save(d)
    return {"deck": d, "stats": deckmod.stats(d, cs), "unknown": unknown,
            "issues": deckmod.validate(d, cs)}


@app.get("/api/decks/{deck_id}/text")
def deck_to_text(deck_id: str):
    d = deckmod.load(deck_id)
    if d is None:
        return PlainTextResponse("没有这个卡组：%s" % deck_id, status_code=404)
    return PlainTextResponse(deckmod.to_text(d), media_type="text/plain; charset=utf-8")


@app.post("/api/decks/{deck_id}/import")
@api
def deck_import_text(deck_id: str, payload: dict = Body(...)):
    """文本牌表导入。

    `{"text": "4 Oath of Druids\\n…", "mode": "replace" | "merge",
      "name_map": {"卡表里的名字": "卡池里的名字"}}`

    `name_map` 是给拼写对不上的卡用的逃生口 —— 但**能自动修的都自动修**：
    卡名走 `norm_name` 归一化（弯引号/破折号），中英文都能认。
    """
    cs = cardset()
    d = deckmod.load(deck_id)
    if d is None:
        raise FileNotFoundError("没有这个卡组：%s" % deck_id)

    parsed = deckmod.parse_text(payload.get("text") or "")
    name_map = payload.get("name_map") or {}

    def resolve(name):
        fixed = name_map.get(name, name)
        k = cs.key_of(fixed)
        return cs.cards[k].get("en") if k else ""

    main, side, unknown = {}, {}, []
    for src, dest in ((parsed["main"], main), (parsed["side"], side)):
        for name, cnt in src.items():
            real = resolve(name)
            if not real:
                unknown.append(name)
                continue
            dest[real] = dest.get(real, 0) + cnt

    mode = payload.get("mode") or "replace"
    if mode == "merge":
        for k, v in main.items():
            d["main"][k] = d["main"].get(k, 0) + v
        for k, v in side.items():
            d["side"][k] = d["side"].get(k, 0) + v
    else:
        d["main"], d["side"] = main, side
    if parsed["min_lands"]:
        d["min_lands"] = parsed["min_lands"]

    d = deckmod.save(d)
    return {"deck": d, "stats": deckmod.stats(d, cs),
            "unknown": unknown,
            "issues": deckmod.validate(d, cs),
            "parsed": {"main": len(main), "side": len(side),
                       "min_lands": parsed["min_lands"]}}


@app.post("/api/decks/{deck_id}/stats")
@api
def deck_stats(deck_id: str, payload: dict = Body(default=None)):
    """未保存的草稿也能算统计 —— 编辑时要实时看张数和曲线。"""
    d = payload if payload else deckmod.load(deck_id)
    if not d:
        raise FileNotFoundError("没有这个卡组：%s" % deck_id)
    return deckmod.stats(deckmod.normalize(d), cardset())


# ---------------------------------------------------------------- 前端静态文件

def mount_frontend():
    """有构建产物就托管 —— 没有也不影响 API 调试。"""
    dist = paths.FRONTEND_DIST
    if os.path.isdir(dist) and os.path.exists(os.path.join(dist, "index.html")):
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
        log.info("托管前端：%s", dist)
        return True
    log.warning("没有前端构建产物（%s）—— 只提供 API。"
                "跑 cd frontend && npm run build", dist)
    return False
