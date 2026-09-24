# -*- coding: utf-8 -*-
"""把卡组改动**写回游戏 WAD**。

> [!CAUTION] 必须用 `Wad.rebuild()`，不能重新打包
> `dotp2014decks` 在这上面踩过一次大坑：第一版用「按路径重新搭树、从头打包」，
> 结果**游戏启动后变英文、中文全渲染成 `•`**。原因是丢了 WAD 头部的
> `headerXml`（内容包声明）和空目录。
>
> `rebuild()` 把头部 + dataOffsets 数组 + 文件表**逐字节原样复制**，
> 只重写变动条目的内容与 Size。所以这里**只做外科手术**。
>
> 每次写之前都会跑一次自检：`rebuild({})` 必须能逐字节还原原文件，
> 不能就中止 —— 说明这个包的布局有 rebuild 处理不了的东西。

另外，**改官方/社区包是有副作用的**：
- 装了这个包的所有存档都会看到改动
- 覆盖了原作者的内容，重装包就没了
所以 `save()` 默认只允许改 `custom` 组，其它组要显式传 `allow_system=True`，
界面上也必须先弹警告。
"""

import os
import re
import time
import shutil

import paths
import fsx
import settings
import gamedecks
from log import get_logger
from wadlite import MMapWad

log = get_logger("deckwrite")

DECK_OPEN_RE = re.compile(rb"<DECK\b[^>]*>", re.I)
STATS_RE = re.compile(rb"<DECKSTATISTICS\b[^>]*/>", re.I)
LANDCFG_RE = re.compile(rb"<LandConfig\b[^>]*/?>", re.I)
BOM = b"\xef\xbb\xbf"

BACKUP_DIR = os.path.join(paths.DATA, "backups")

# 写回时按这个顺序排 LandConfig 属性，读起来顺
LAND_ORDER = ["minPlains", "minIsland", "minSwamp", "minMountain", "minForest"]


def build_xml(orig: bytes, card_keys, land_config=None, keep_stats=True) -> bytes:
    """在**原 XML 的基础上**重写 `<CARD>` 列表和 `<LandConfig>`，其余一概不动。

    保留 `<DECK>` 上的全部属性（uid / content_pack / personality / steam_id…）
    和 `<DECKSTATISTICS>` —— 那些是牌组的身份信息，重新生成容易漏。

    `card_keys` 是卡池的 `<FILENAME>` 列表，**一张一行**
    （游戏两种写法都认；官方基础牌组用的 `@N` 合并写法这里不用，
    因为一张一行改起来清楚，而且 dotp2014decks 那 6 个包就是这样、已在游戏里验证过）。
    """
    has_bom = orig.startswith(BOM)
    body = orig[len(BOM):] if has_bom else orig

    m = DECK_OPEN_RE.search(body)
    if not m:
        raise ValueError("不是牌组 XML（找不到 <DECK>）")
    deck_open = m.group(0)

    stats = STATS_RE.search(body)
    stats_tag = stats.group(0) if (stats and keep_stats) else None

    nl = b"\r\n" if b"\r\n" in body else b"\n"
    lines = [deck_open]

    if stats_tag:
        lines.append(b"  " + stats_tag.strip())

    land_config = {k: int(v) for k, v in (land_config or {}).items()
                   if k in LAND_ORDER and int(v) > 0}
    if land_config:
        attrs = b" ".join(
            b'%s="%d"' % (k.encode("ascii"), land_config[k])
            for k in LAND_ORDER if k in land_config)
        lines.append(b'  <LandConfig ignoreCmcOver="0" ' + attrs + b" />")

    for i, key in enumerate(card_keys):
        lines.append(b'  <CARD name="%s" deckOrderId="%d" />'
                     % (key.encode("utf-8"), i))

    lines.append(b"</DECK>")
    out = nl.join(lines) + nl
    return (BOM + out) if has_bom else out


def _backup(wad_path):
    """写回前整包备份。出事了能直接拷回去。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stem = os.path.basename(wad_path)
    dest = os.path.join(BACKUP_DIR, "%s.%s.bak" % (stem, time.strftime("%Y%m%d-%H%M%S")))
    shutil.copy2(wad_path, dest)
    log.info("备份 -> %s", dest)
    return dest


def writable(deck_id):
    """这副牌组能不能直接改。返回 `(能改, 原因)`。"""
    d = gamedecks.get(deck_id)
    if d is None:
        return False, "没有这副牌组"
    if d["group"] == "custom":
        return True, ""
    cn = {"system": "系统自带", "community": "社区包"}.get(d["group"], d["group"])
    return False, ("这是「%s」里的牌组（%s）。直接改会**永久覆盖原作者的内容**，"
                   "而且重装那个包就没了。" % (cn, d["wad"]))


def save(deck_id, card_keys, land_config=None, game_dir=None, allow_system=False):
    """把卡表写回牌组所在的 WAD。

    `card_keys`：卡池 `<FILENAME>` 的**有序列表**（顺序 = `deckOrderId`）。
    `allow_system`：改官方/社区包时必须显式传 True（界面先弹警告）。

    返回 `{wad, backup, size_before, size_after, cards}`。
    """
    gd = game_dir or settings.game_dir()
    d = gamedecks.get(deck_id)
    if d is None:
        raise KeyError("没有这副牌组：%s" % deck_id)

    ok, why = writable(deck_id)
    if not ok and not allow_system:
        raise PermissionError(why)

    wad_path = os.path.join(gd, d["wad"])
    if not os.path.exists(wad_path):
        raise FileNotFoundError("包不在游戏目录里：%s" % wad_path)

    src = MMapWad(wad_path)
    try:
        # ---- 自检：空替换必须能逐字节还原。还原不了说明这个包 rebuild 处理不了 ----
        #
        # ⚠️ 必须用 `memoryview` 比，**不能直接 `!= src.raw`** ——
        # `src.raw` 是 mmap，而 CPython 里 `bytes == mmap` **恒为 False**
        # （哪怕逐字节完全一样）。直接比会把每个包都误判成「自检失败」而拒绝写入。
        rebuilt = src.rebuild({})
        if len(rebuilt) != len(src.raw) or memoryview(rebuilt) != memoryview(src.raw):
            raise RuntimeError(
                "自检失败：`rebuild({})` 还原不出原文件，这个包的布局有 rebuild "
                "处理不了的东西，已中止（原文件没动）")

        want = d["file"].replace("\\", "/").upper()
        target = None
        for f in src.files:
            if f.path.replace("\\", "/").upper() == want:
                target = f
                break
        if target is None:
            raise FileNotFoundError("包里找不到牌组条目：%s" % d["file"])

        orig = src.read(target)
        new_xml = build_xml(orig, card_keys, land_config)
        if new_xml == orig:
            return {"wad": d["wad"], "changed": False, "cards": len(card_keys)}

        # 多个条目可能共用同一个数据块（罕见但存在），共用就不能单独改
        shared = sum(1 for f in src.files if f.offset_index == target.offset_index)
        if shared > 1:
            raise RuntimeError("这个数据块被 %d 个条目共用，改了会影响别的条目，已中止"
                               % shared)

        data = src.rebuild({target.offset_index: new_xml})
        size_before = len(src.raw)
    finally:
        src.close()

    backup = _backup(wad_path)
    fsx.write_bytes(wad_path, data)          # 原子替换：写一半崩了不会毁掉原包

    log.info("写回 %s :: %s（%d -> %d 字节，%d 张卡）",
             d["wad"], os.path.basename(d["file"]), size_before, len(data),
             len(card_keys))
    return {"wad": d["wad"], "file": d["file"], "backup": backup,
            "size_before": size_before, "size_after": len(data),
            "cards": len(card_keys), "changed": True,
            "group": d["group"], "group_cn":
                {"custom": "自制", "system": "系统自带",
                 "community": "社区包"}.get(d["group"], d["group"])}


def restore(backup_name, game_dir=None):
    """从备份还原一个包。"""
    gd = game_dir or settings.game_dir()
    src = os.path.join(BACKUP_DIR, backup_name)
    if not os.path.exists(src):
        raise FileNotFoundError("没有这个备份：%s" % backup_name)
    # 备份名形如 `DATA_DLC_1M_DECK1.wad.20260924-230000.bak`
    wad_name = backup_name.split(".wad.")[0] + ".wad"
    dest = os.path.join(gd, wad_name)
    shutil.copy2(src, dest)
    log.info("已还原 %s <- %s", dest, backup_name)
    return {"restored": dest, "from": src}


TRASH_DIR = os.path.join(paths.DATA, "trash")


def _deck_count_in_wad(wad_path, keep=None):
    """包里还有几副牌组（不含 `keep` 那一副）。"""
    src = MMapWad(wad_path)
    try:
        n = 0
        for f in src.files:
            u = f.path.upper()
            if u.endswith(".XML") and "/DECKS/" in u and "_LAND_POOL" not in u:
                try:
                    t = src.read(f)
                except Exception:
                    continue
                if DECK_OPEN_RE.search(t):
                    n += 1
        return n
    finally:
        src.close()


def delete(deck_id, game_dir=None):
    """**删除一副自制牌组。**

    自制包是「一包一副」（`DATA_DLC_1M_DECK1~6` 每包各 1 副），所以删牌组 =
    **把整个 WAD 移走** —— 游戏扫不到这个包，牌组自然就消失了。

    **不是真删**：挪到 `data/trash/<时间戳>/` 下，想反悔直接拷回游戏目录即可。
    删一个 WAD 要重建文件表（`rebuild` 只支持替换内容、不支持删条目），
    风险远大于收益，所以不碰包结构。

    包里如果还有别的牌组，会拒绝 —— 免得连带删掉不相干的。
    """
    gd = game_dir or settings.game_dir()
    d = gamedecks.get(deck_id)
    if d is None:
        raise KeyError("没有这副牌组：%s" % deck_id)
    if d["group"] != "custom":
        raise PermissionError(
            "只能删自制牌组。这是「%s」里的，删了会影响原包。"
            % {"system": "系统自带", "community": "社区包"}.get(d["group"], d["group"]))

    wad_path = os.path.join(gd, d["wad"])
    if not os.path.exists(wad_path):
        raise FileNotFoundError("包不在游戏目录里：%s" % wad_path)

    others = _deck_count_in_wad(wad_path) - 1
    if others > 0:
        raise RuntimeError(
            "这个包里还有 %d 副别的牌组，删掉会连带它们一起没了，已中止。"
            "要单独删的话得改 WAD 的文件表，风险太大，没做。" % others)

    stamp_ = time.strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(TRASH_DIR, stamp_)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, d["wad"])
    shutil.move(wad_path, dest)
    log.info("删除自制牌组 %s -> %s", d["name"], dest)
    return {"deleted": d["name"], "wad": d["wad"], "moved_to": dest,
            "restore_hint": "想恢复就把这个文件拷回 %s" % gd}


def trash():
    """回收站内容。"""
    if not os.path.isdir(TRASH_DIR):
        return []
    out = []
    for stamp_ in sorted(os.listdir(TRASH_DIR), reverse=True):
        d = os.path.join(TRASH_DIR, stamp_)
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            p = os.path.join(d, n)
            out.append({"stamp": stamp_, "name": n, "size": os.path.getsize(p)})
    return out


def untrash(stamp_, name, game_dir=None):
    """从回收站拿回来。"""
    gd = game_dir or settings.game_dir()
    src = os.path.join(TRASH_DIR, stamp_, name)
    if not os.path.exists(src):
        raise FileNotFoundError("回收站里没有：%s/%s" % (stamp_, name))
    dest = os.path.join(gd, name)
    if os.path.exists(dest):
        raise FileExistsError("游戏目录里已经有 %s 了，先处理它" % name)
    shutil.move(src, dest)
    log.info("恢复 %s -> %s", name, dest)
    return {"restored": dest}


def backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for n in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if n.endswith(".bak"):
            p = os.path.join(BACKUP_DIR, n)
            out.append({"name": n, "size": os.path.getsize(p),
                        "time": time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(os.path.getmtime(p)))})
    return out


def main():
    """自检：拿一个自制牌组，构建新 XML 再还原，确认没写坏。"""
    import sys
    decks, _ = gamedecks.load()
    d = next((x for x in decks if x["group"] == "custom"), None)
    if not d:
        print("没有自制牌组可测")
        return 1
    print("测试对象：%s :: %s" % (d["name"], d["file"]))
    src = MMapWad(os.path.join(settings.game_dir(), d["wad"]))
    want = d["file"].replace("\\", "/").upper()
    f = next(x for x in src.files if x.path.replace("\\", "/").upper() == want)
    orig = src.read(f)
    print("  原 XML %d 字节" % len(orig))

    keys = list(d["cards"].keys())
    new = build_xml(orig, keys, d.get("land_config"))
    print("  重建 %d 字节（%d 张卡）" % (len(new), len(keys)))
    print("  ---- 前 6 行 ----")
    for line in new.decode("utf-8-sig").splitlines()[:6]:
        print("    " + line)

    # 幂等：拿新 XML 再构建一次，应该一模一样
    again = build_xml(new, keys, d.get("land_config"))
    print("  幂等：%s" % ("✓" if again == new else "✗ 两次结果不同"))

    rb = src.rebuild({})
    same = len(rb) == len(src.raw) and memoryview(rb) == memoryview(src.raw)
    print("  rebuild 自检：%s" % ("✓" if same else "✗"))
    src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
