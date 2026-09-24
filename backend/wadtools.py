# -*- coding: utf-8 -*-
"""WAD 目录的维护工具：列包、备份、**检测并修复 `.bsf` 越界读**。

## 为什么会有「`.bsf` 越界读」

`.bsf` 是 UI 文字表（`BZBT` 魔数 + UTF-16 键值对）。引擎的解析循环
（`DotP_D14.exe +0x30A13F`）逐字节扫 `0x00` 找条目起点，但**只检查条目起点是否越界**：

```asm
0x70a3d9  cmp ebx, [ebp-0x44]      ; 起点 < 缓冲区尾 ?
0x70a3dc  jb  0x70a180
0x70a180  mov al, byte [ebx]       ; 逐字节扫 0x00
0x70a182  inc ebx
0x70a185  jne 0x70a3d9
; ---- 出循环，ebx 指向 NUL 之后 ----
0x70a18b  movzx ecx, word [ebx+1]  ; ★ 读 valLen —— 没有边界检查
0x70a18f  mov al, byte [ebx]       ; ★ 读 keyLen —— 同样没有
```

**起点在界内 ≠ 数据在界内。** 如果文件末尾多一个孤立的 `0x00`，
循环会再进一次、把 `ebx` 推到缓冲区尾，那两条读指令就落到缓冲区**外面 3 字节**。

读到的垃圾被当成 `valLen`，第二段拷贝循环按它拷 —— 内存可读时只是白拷一段垃圾，
**一旦缓冲区顶在已提交页边界上就是 ACCESS_VIOLATION**。

这解释了 `dotp2014decks` 里追了很久的那个「**约 40% 概率、启动 4 秒后随机崩溃**」：
堆布局随装了哪些包而变，缓冲区落点也跟着变，所以改 WAD 会影响崩溃率。

`DATA_DECKS_D910.WAD`（中文包）的 9 个 `.bsf` 就各多了一个尾部 `0x00`。
`DATA_CORE` 里的同名文件是干净的 —— 所以这是那个包在制作时引入的。

## 修法

把尾部多余的字节删掉，让循环在最后一条记录后正常退出。
**必须走 `Wad.rebuild()`**，不能重新打包 —— 那会丢头部的 `headerXml`
（内容包声明）和空目录，后果是**游戏变英文、中文全渲染成 `•`**。
"""

import os
import re
import sys
import time
import shutil
import struct

import paths
import fsx
import settings
from log import get_logger
from wadlite import MMapWad

log = get_logger("wadtools")

BACKUP_DIR = os.path.join(paths.DATA, "wad_backup")

# 会被引擎那样解析的文件
BSF_MAGIC = b"BZBT"


def list_wads(game_dir=None):
    """游戏目录里的所有 WAD，带大小和时间。"""
    gd = game_dir or settings.game_dir()
    out = []
    if not os.path.isdir(gd):
        return out
    for n in sorted(os.listdir(gd)):
        if not n.lower().endswith(".wad"):
            continue
        p = os.path.join(gd, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            "name": n,
            "size": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "backed_up": os.path.exists(os.path.join(BACKUP_DIR, n)),
        })
    return out


def backup_wads(game_dir=None, names=None, verbose=True):
    """把 WAD 整包备份到 `data/wad_backup/`。已存在的默认跳过。"""
    gd = game_dir or settings.game_dir()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    todo = [w for w in list_wads(gd) if not names or w["name"] in names]
    done, skipped, total = [], 0, 0
    for w in todo:
        src = os.path.join(gd, w["name"])
        dest = os.path.join(BACKUP_DIR, w["name"])
        if os.path.exists(dest) and os.path.getsize(dest) == w["size"]:
            skipped += 1
            continue
        try:
            shutil.copy2(src, dest)
            done.append(w["name"])
            total += w["size"]
            if verbose:
                log.info("备份 %s（%.1f MB）", w["name"], w["size"] / 1e6)
        except Exception as e:
            log.warning("备份失败 %s：%s", w["name"], e)
    return {"dir": BACKUP_DIR, "backed": done, "skipped": skipped,
            "count": len(done), "bytes": total}


# ---------------------------------------------------------------- .bsf 解析

def scan_bsf(raw):
    """模拟引擎那套解析，看会不会越界读。

    返回 `(条目数, 是否越界, 越界位置, 最后一条记录之后剩下的字节)`。
    """
    end = len(raw)
    if end < 12:
        return 0, False, None, b""
    ptr = struct.unpack_from("<I", raw, 8)[0]
    n = 0
    while ptr < end:
        ebx = ptr
        while ebx < end and raw[ebx] != 0:
            ebx += 1
        if ebx < end:
            ebx += 1
        # 引擎在这里没有边界检查，我们模拟它的行为来判「会不会读出去」
        if ebx + 2 >= end:
            return n, True, ebx, raw[ptr:end]
        kl = raw[ebx]
        vl = struct.unpack_from("<H", raw, ebx + 1)[0]
        nxt = ebx + 3 + (kl + vl) * 2
        if nxt > end:
            return n, True, ebx, raw[ptr:end]
        ptr = nxt
        n += 1
    return n, False, None, raw[ptr:end]


def audit_wad(wad_path, verbose=False):
    """一个包里所有 `.bsf` 的体检结果。"""
    src = MMapWad(wad_path)
    try:
        out = []
        for f in src.files:
            if not f.path.lower().endswith(".bsf"):
                continue
            try:
                raw = src.read(f)
            except Exception as e:
                log.debug("读不出 %s：%s", f.path, e)
                continue
            if raw[:4] != BSF_MAGIC:
                continue
            n, oob, where, tail = scan_bsf(raw)
            out.append({
                "path": f.path,
                "name": os.path.basename(f.path),
                "size": len(raw),
                "entries": n,
                "overflow": oob,
                "where": where,
                "tail": tail.hex(" ") if tail else "",
                "tail_len": len(tail),
            })
        return out
    finally:
        src.close()


def scan(game_dir=None, verbose=True):
    """扫全部 WAD 里的 `.bsf`，列出有问题的。"""
    gd = game_dir or settings.game_dir()
    bad, clean, n_wad = [], 0, 0
    for w in list_wads(gd):
        try:
            rows = audit_wad(os.path.join(gd, w["name"]))
        except Exception as e:
            log.warning("扫不了 %s：%s", w["name"], e)
            continue
        if rows:
            n_wad += 1
        for r in rows:
            r["wad"] = w["name"]
            if r["overflow"]:
                bad.append(r)
            else:
                clean += 1
        if verbose and any(r["overflow"] for r in rows):
            log.warning("%s 里有 %d 个 .bsf 会越界读",
                        w["name"], sum(1 for r in rows if r["overflow"]))
    return {"bad": bad, "clean": clean, "wads_scanned": n_wad,
            "need_fix": bool(bad)}


def fix(wad_name, game_dir=None, verbose=True):
    """修复一个包里所有尾部多余的字节。

    和 `deckwrite` 一样下四道闸：自检 → 备份 → 外科手术 → 复验。
    """
    gd = game_dir or settings.game_dir()
    wad_path = os.path.join(gd, wad_name)
    if not os.path.exists(wad_path):
        raise FileNotFoundError("包不在游戏目录里：%s" % wad_path)

    rows = audit_wad(wad_path)
    bad = [r for r in rows if r["overflow"]]
    if not bad:
        return {"changed": False, "wad": wad_name,
                "msg": "这个包的 .bsf 都是干净的，不用修"}

    src = MMapWad(wad_path)
    try:
        # 自检 —— 注意用 memoryview 比，`bytes == mmap` 在 Python 里恒为 False
        rebuilt = src.rebuild({})
        if len(rebuilt) != len(src.raw) or memoryview(rebuilt) != memoryview(src.raw):
            raise RuntimeError(
                "自检失败：`rebuild({})` 还原不出原文件，这个包的布局 rebuild "
                "处理不了，已中止（原文件没动）")

        want = {r["path"]: bytes.fromhex(r["tail"]) for r in bad}
        repl, fixed, skipped = {}, [], []
        for f in src.files:
            t = want.get(f.path)
            if not t:
                continue
            raw = src.read(f)
            if not raw.endswith(t):
                skipped.append((f.path, "尾部对不上"))
                continue
            if f.offset_index in repl:
                skipped.append((f.path, "与别的条目共用数据块"))
                continue
            repl[f.offset_index] = raw[:-len(t)]
            fixed.append({"path": f.path, "name": os.path.basename(f.path),
                          "removed": len(t)})
        if not repl:
            return {"changed": False, "wad": wad_name, "msg": "没有可修的条目",
                    "skipped": skipped}

        data = src.rebuild(repl)
        size_before = len(src.raw)
        header_len = len(src.header_xml)
        dirs = src.total_directory_count
    finally:
        src.close()

    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DIR, "%s.%s.bak" % (wad_name, time.strftime("%Y%m%d-%H%M%S")))
    shutil.copy2(wad_path, bak)

    fsx.write_bytes(wad_path, data)

    # 复验：修完必须不再越界，而且**头部和目录数一个都不能变**
    after = audit_wad(wad_path)
    left = [r for r in after if r["overflow"]]
    chk = MMapWad(wad_path)
    try:
        head_ok = len(chk.header_xml) == header_len
        dir_ok = chk.total_directory_count == dirs
    finally:
        chk.close()

    log.info("修复 %s：%d 个 .bsf，%d -> %d 字节", wad_name, len(fixed),
             size_before, len(data))
    return {"changed": True, "wad": wad_name, "fixed": fixed, "skipped": skipped,
            "size_before": size_before, "size_after": len(data),
            "backup": bak, "still_bad": len(left),
            "header_xml_ok": head_ok, "dirs_ok": dir_ok,
            "header_xml_len": header_len, "dirs": dirs}


def restore_backup(name, game_dir=None):
    """从 `data/wad_backup/` 还原一个包。"""
    gd = game_dir or settings.game_dir()
    src = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(src):
        raise FileNotFoundError("没有这个备份：%s" % name)
    dest = os.path.join(gd, name)
    shutil.copy2(src, dest)
    log.info("还原 %s", dest)
    return {"restored": dest}


def backup_list():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for n in sorted(os.listdir(BACKUP_DIR), reverse=True):
        p = os.path.join(BACKUP_DIR, n)
        if os.path.isfile(p):
            out.append({"name": n, "size": os.path.getsize(p),
                        "time": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(os.path.getmtime(p)))})
    return out


def make_bsf(entries, extra_tail=b""):
    """按引擎认的格式拼一个 `.bsf`（给自测用）。

    布局：`BZBT` + 4 字节 + u32 起始偏移，然后是一串记录::

        klen 的 `00` 会在解析时被当作分隔符扫到
    """
    body = b""
    for k, v in entries:
        kb, vb = k.encode("utf-16-le"), v.encode("utf-16-le")
        body += b"\x00" + bytes([len(k)]) + struct.pack("<H", len(v)) + kb + vb
    return BSF_MAGIC + b"\x00" * 4 + struct.pack("<I", 12) + body + extra_tail


def selftest():
    """拿构造出来的样本验证检测逻辑 —— 确认它**真的能发现问题**，
    而不是因为扫不到才报「干净」。"""
    ok = True

    # 1. 干净的：正好在最后一条记录处结束
    good = make_bsf([("A", "xy"), ("BB", "z")])
    n, oob, _w, tail = scan_bsf(good)
    r1 = (n == 2 and not oob and not tail)
    print("  干净的样本      -> 条目 %d，越界 %s，尾部 %d 字节   %s"
          % (n, oob, len(tail), "✓" if r1 else "✗"))
    ok &= r1

    # 2. 末尾多一个 0x00 —— 就是 D910 那个毛病
    bad = make_bsf([("A", "xy"), ("BB", "z")], extra_tail=b"\x00")
    n, oob, _w, tail = scan_bsf(bad)
    r2 = (oob and tail == b"\x00")
    print("  多一个 0x00     -> 条目 %d，越界 %s，尾部 [%s]      %s"
          % (n, oob, tail.hex(" "), "✓ 检出" if r2 else "✗ 没检出"))
    ok &= r2

    # 3. 末尾多好几个 —— 也一样要检出来
    bad2 = make_bsf([("K", "v")], extra_tail=b"\x00\x00\x00")
    n, oob, _w, tail = scan_bsf(bad2)
    r3 = bool(oob)
    print("  多三个 0x00     -> 条目 %d，越界 %s，尾部 [%s]      %s"
          % (n, oob, tail.hex(" "), "✓ 检出" if r3 else "✗ 没检出"))
    ok &= r3

    # 4. 记录本身越界（valLen 写大了）
    broken = (BSF_MAGIC + b"\x00" * 4 + struct.pack("<I", 12)
              + b"\x00" + bytes([1]) + struct.pack("<H", 9999) + "A".encode("utf-16-le"))
    n, oob, _w, tail = scan_bsf(broken)
    r4 = bool(oob)
    print("  valLen 写大了   -> 条目 %d，越界 %s                  %s"
          % (n, oob, "✓ 检出" if r4 else "✗ 没检出"))
    ok &= r4

    print("\n  %s" % ("检测逻辑正常 ✓" if ok else "!! 检测逻辑有问题"))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        print("检测逻辑自测（构造样本）：")
        return selftest()

    gd = settings.game_dir()
    print("游戏目录：%s" % gd)
    t0 = time.time()
    r = scan(gd, verbose=False)
    print("扫了 %d 个含 .bsf 的包（%.1fs）" % (r["wads_scanned"], time.time() - t0))
    print("干净 %d 个，**会越界读 %d 个**" % (r["clean"], len(r["bad"])))
    for b in r["bad"]:
        print("  ✗ %-30s %-44s %7d 字节  条目%5d  尾部 %d 字节 [%s]"
              % (b["wad"], b["name"], b["size"], b["entries"],
                 b["tail_len"], b["tail"]))
    if not r["bad"]:
        print("  ✓ 没有需要修的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
