# -*- coding: utf-8 -*-
"""用户配置 + **WAD 目录指纹**（决定索引要不要重建）。

为什么不把游戏目录写死
----------------------
`dotp2014decks` 里所有脚本都硬编码 `GAME = r"E:\\game\\Magic 2014"`。
本程序要能换目录（换机器、换盘符、多份游戏副本），所以路径进配置文件。

为什么要指纹
------------
建一次全量索引要几十秒到几分钟（`card_details` 21k 张卡、卡图 2.2 万张）。
但只要游戏目录**没变**，就没必要重建 —— 直接读缓存。

指纹 = 每个 `.wad` 的 (大小, mtime)。逐字节哈希 4GB 太慢，而
「装了新包 / 换了包 / 包被改过」都会改变大小或 mtime，够用。

索引文件都把指纹写进 `_meta.sources`，启动时比一下就知道该读还是该建。
"""

import os
import json
import time

import paths

# 空 = 还没配。**不要**写死开发机的路径 —— 开源出去别人跑不了，
# 而且把本机目录结构泄了个干净。
DEFAULT_GAME = ""

DEFAULTS = {
    "game_dir": DEFAULT_GAME,
    "thumb_width": 256,          # 网格缩略图宽度
    "thumb_quality": 78,
    "thumb_format": "WEBP",
}

_cache = None


def autodetect():
    """程序**就放在游戏目录里**的话，直接认出来 —— 返回目录，认不出返回 ""。

    这个程序的设计用法是把 exe 丢进《Magic 2014》的安装目录。既然它就在那儿，
    再让用户去「设置」里手动指一遍自己所在的目录，纯属多余 ——
    而且新用户第一次打开看到的是「卡池索引不存在」那种报错，很容易以为坏了。

    判据是**同级有 `DATA_CORE.WAD`** —— 那是游戏的卡框/符号素材包，
    任何一份完整安装都有，而别的目录基本不会有。
    """
    d = paths.ROOT
    try:
        if os.path.isfile(os.path.join(d, paths.CORE_WAD_NAME)):
            return d
    except OSError:
        pass
    return ""


def load(force=False):
    """读配置；文件不存在就用默认值建一份。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    d = dict(DEFAULTS)
    if os.path.exists(paths.SETTINGS_JSON):
        try:
            with open(paths.SETTINGS_JSON, encoding="utf-8") as f:
                d.update(json.load(f))
        except Exception:
            pass                                  # 坏了就退回默认，别让程序起不来
    # 配置里**没写**目录才自动认。用户手填过的（哪怕现在盘没插、目录暂时不在）
    # 一律不动 —— 否则「U 盘没插」会变成「设置被悄悄改掉了」。
    if not (d.get("game_dir") or "").strip():
        auto = autodetect()
        if auto:
            d["game_dir"] = auto
    _cache = d
    # `paths.GAME` 是模块级变量，`packer` / `wadlite` 直接读它。
    # **读也要同步** —— 早先只在 `save()` 里同步，于是「配置文件里已经有目录、
    # 但本次进程从没调过 save()」时 `paths.GAME` 还是空串，打包找不到游戏目录。
    paths.GAME = d.get("game_dir") or ""
    return d


def save(patch):
    """合并写入。返回新配置。"""
    global _cache
    d = load()
    d.update(patch)
    os.makedirs(os.path.dirname(paths.SETTINGS_JSON), exist_ok=True)
    with open(paths.SETTINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    _cache = d
    # paths.GAME 是模块级常量，改了目录要同步
    paths.GAME = d["game_dir"]
    return d


def game_dir():
    return load()["game_dir"]


# ---------------------------------------------------------------- 指纹

def wad_fingerprint(game_dir=None):
    """`{wad 文件名: [大小, mtime]}`。目录不存在返回 None。"""
    d = game_dir or game_dir_default()
    if not os.path.isdir(d):
        return None
    out = {}
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for n in names:
        if not n.lower().endswith(".wad"):
            continue
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out[n] = [st.st_size, int(st.st_mtime)]
    return out


def game_dir_default():
    return load()["game_dir"]


def fingerprint_diff(old, new):
    """人话描述指纹差异，用于告诉用户「为什么要重建」。"""
    if old is None:
        return ["没有旧指纹"]
    if new is None:
        return ["游戏目录不存在"]
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    msgs = []
    if added:
        msgs.append("新增 %d 个包：%s" % (len(added), ", ".join(added[:4])))
    if removed:
        msgs.append("移除 %d 个包：%s" % (len(removed), ", ".join(removed[:4])))
    if changed:
        msgs.append("改动 %d 个包：%s" % (len(changed), ", ".join(changed[:4])))
    return msgs


def index_status(index_path, game_dir=None):
    """索引文件相对当前游戏目录是否还新鲜。

    返回 `(fresh: bool, meta: dict|None, reasons: [str])`。
    """
    if not os.path.exists(index_path):
        return False, None, ["索引文件不存在"]
    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, None, ["索引文件读不了：%s" % e]
    meta = data.get("_meta") or {}
    old = meta.get("sources")
    if old is None:
        return False, meta, ["索引里没有指纹（旧版本建的）"]
    new = wad_fingerprint(game_dir)
    if new is None:
        return False, meta, ["游戏目录打不开"]
    if old != new:
        return False, meta, fingerprint_diff(old, new)
    return True, meta, []


def stamp(meta, game_dir=None):
    """给索引的 `_meta` 盖上当前指纹。"""
    meta = dict(meta or {})
    meta["sources"] = wad_fingerprint(game_dir)
    meta["game_dir"] = game_dir or game_dir_default()
    meta["built"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return meta
