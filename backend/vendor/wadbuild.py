# -*- coding: utf-8 -*-
"""
从零构造一个 DotP WAD —— `wadtool.py` 只能读和重建已有归档，做新卡包得能凭空造。

布局严格对着 `wadtool._parse()` 写，写完用「解包 → 重造 → 逐字节比对」验证。

    u16 magic=0x1234 · u16 version=0x202 · u32 flags
    u32 headerXmlLength + headerXml
    u32 stringTableSize + stringTable          CP1252，偏移寻址，null 结尾
    u32 dataTypesCount(=0) + …                 仅当 flags & HasDataTypes
    u32 totalFileCount · u32 totalDirectoryCount
    u32 dataOffsetsCount + u32 dataOffsets[]
    fileTable                                   (目录数+文件数)×16，递归树
    [数据区]                                     条目首尾相接

fileTable 节点：
    目录: u32 名字索引, u32 本目录文件数, u32 子目录数, u32 0  → 先子目录，再文件
    文件: u32 名字索引, u32 Size, u32 Flags, u32 0
          Flags 低 24 位 = dataOffsets 下标；高 8 位 = 1
"""

import os
import struct
import zlib

MAGIC = 0x1234
VERSION = 0x202
FLAG_HAS_COMPRESSED_FILES = 1 << 1
FLAG_UNKNOWN6 = 1 << 6
FLAG_HAS_DATA_TYPES = 1 << 9

# 游戏里的卡包/牌组包统一是这个组合（未压缩条目的包用 0x240）
FLAGS_COMPRESSED = FLAG_HAS_COMPRESSED_FILES | FLAG_UNKNOWN6 | FLAG_HAS_DATA_TYPES
FLAGS_PLAIN = FLAG_UNKNOWN6 | FLAG_HAS_DATA_TYPES


class _Dir(object):
    def __init__(self, name):
        self.name = name
        self.dirs = {}          # name -> _Dir
        self.files = []         # (name, content)


def _insert(root, path, content):
    """路径**不含**根目录名 —— 根由 root_name 单独给"""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    node = root
    for p in parts[:-1]:
        node = node.dirs.setdefault(p, _Dir(p))
    node.files.append((parts[-1], content))


def _count(node):
    nd, nf = 1, len(node.files)
    for d in node.dirs.values():
        a, b = _count(d)
        nd += a
        nf += b
    return nd, nf


def build(entries, header_xml=b"", root_name="", compress=True, preset=False):
    """entries: [(相对路径, 内容 bytes)]；header_xml: 容器头里的 <WAD_HEADER> 声明；
    root_name: 树的根目录名 —— **实测就是包 UID**（如 DATA_DLC_TFM_D_Devotion_to_Chaos），
    不是空串。写成空串会让游戏找不到内容。

    返回完整的 WAD 字节。
    """
    flags = FLAGS_COMPRESSED if compress else FLAGS_PLAIN

    root = _Dir(root_name)
    for path, content in entries:
        _insert(root, path, content if isinstance(content, bytes) else content.encode("utf-8"))
    n_dirs, n_files = _count(root)

    # ---- 字符串表 ----
    # 实测原包**没有**开头的空串占位：偏移 0 直接就是根目录名（`DATA_DLC_KEV\0…`）。
    # 而且整张表会**补零到 16 的整数倍**（TFM 包 180 字节的名字 → 192）。
    st = bytearray()
    index = {}

    def name_index(name):
        if name in index:
            return index[name]
        off = len(st)
        st.extend(name.encode("cp1252", "replace"))
        st.append(0)
        index[name] = off
        return off

    def walk(node):
        """先给本目录和所有子目录的文件登记名字，顺序要和写 fileTable 时一致"""
        name_index(node.name)
        for d in node.dirs.values():
            walk(d)
        for fname, _c in node.files:
            name_index(fname)

    walk(root)
    # 补零到 16 的整数倍（对齐方式和原包一致）
    while len(st) % 16:
        st.append(0)

    # ---- 数据块 ----
    def make_blob(content):
        # preset=True：content 已经是「u32 原始长度 + 压缩流」的完整块，
        # 原样写进去 —— 用来原封不动地搬运已有归档里的数据（见 repair_wad.py）。
        if preset:
            return content
        """压缩块 = u32 原始长度 + 压缩流；**压不动就写 0xFFFFFFFF + 原样数据**。

        Deck Builder 的 `WadWrapper.cs:909` 就是这么分的支：
            if (msTemp.Length < mfeFile.FileData.Length) { ...写真实长度 + 压缩流... }
            else { ...写 0xFFFFFFFFu + 原始数据... }
        封面那种 1MB 的未压缩 BGRA 压完往往更大，少了这条回退会白写一遍。
        """
        if not compress:
            return content
        packed = zlib.compress(content, 9)
        if len(packed) < len(content):
            return struct.pack("<I", len(content)) + packed
        return struct.pack("<I", 0xFFFFFFFF) + content

    blobs = []          # (node_file_ref, blob)
    def collect(node):
        # 顺序必须和 write_dir() 一致：**先递归子目录，本目录的文件排最后**。
        # 搞反的话内容仍然正确、偏移自洽，但和原包对不上字节
        # （原包把根目录的 HEADER.XML 排在整块数据的最末尾）。
        for d in node.dirs.values():
            collect(d)
        for fname, content in node.files:
            blobs.append((node, fname, make_blob(content)))
    collect(root)

    # ---- 先算出文件表/头的长度，才能定数据区起点 ----
    header_xml = header_xml if isinstance(header_xml, bytes) else header_xml.encode("utf-8")
    head_len = 2 + 2 + 4 + 4 + len(header_xml) + 4 + len(st) + 4
    head_len += 4 + 4                      # totalFileCount / totalDirectoryCount
    head_len += 4 + 4 * n_files            # dataOffsets 数组（每个文件一个独立块）
    table_len = (n_dirs + n_files) * 16
    data_start = head_len + table_len

    offsets = []
    cur = data_start
    for _n, _f, blob in blobs:
        offsets.append(cur)
        cur += len(blob)
    total_len = cur

    # ---- 写 ----
    out = bytearray()
    out += struct.pack("<HHI", MAGIC, VERSION, flags)
    out += struct.pack("<I", len(header_xml)) + header_xml
    out += struct.pack("<I", len(st)) + bytes(st)
    out += struct.pack("<I", 0)             # dataTypesCount = 0
    out += struct.pack("<II", n_files, n_dirs)
    out += struct.pack("<I", n_files)
    out += struct.pack("<%dI" % n_files, *offsets)

    # fileTable：目录节点里，文件用的是它在 blobs 里的下标
    blob_index = {}
    for i, (node, fname, _b) in enumerate(blobs):
        blob_index[(id(node), fname)] = i

    def write_dir(node):
        n_sub = len(node.dirs)
        out.extend(struct.pack("<IIII", name_index(node.name), len(node.files), n_sub, 0))
        for d in node.dirs.values():
            write_dir(d)
        for fname, _c in node.files:
            i = blob_index[(id(node), fname)]
            out.extend(struct.pack("<IIII", name_index(fname), len(blobs[i][2]),
                                   i | (1 << 24), 0))

    write_dir(root)
    assert len(out) == data_start, "文件表长度算错了：%d != %d" % (len(out), data_start)

    for _n, _f, blob in blobs:
        out += blob
    assert len(out) == total_len
    return bytes(out)


def make_header_xml(uid, content_pack=None):
    """容器头里的内容声明。uid 就是包目录名（如 DATA_DLC_1M_DECK3）。

    `content_pack` 给了就**再挂一个 <CONTENTPACK> 块**，把这个包装成一个
    「内容包启用器」—— 这样这个包里的牌组才能用 `content_pack="<那个 UID>"` 挂进来。

    为什么必须这么做（社区 wiki「Best Practices」原文）：
        DotP 2014 can only handle **9 decks in content pack 0** with the
        always_available="true" attribute. Decks put into a different content
        pack do not seem to have this limitation.
    而且启用 XML**只能用一次**，用两次会出现一整套重复牌组。所以正确结构是
    「一个独立的启用器 WAD + 若干牌组 WAD」，跟游戏自带的
    `Data_DLC_1000_Content_Pack_Enabler.wad` 和玩家自制包的做法一致。
    """
    xml = (b'<?xml version="1.0"?>\n<WAD_HEADER>\n'
           b'  <ENTRY platform="ALL" source="' + uid.encode() +
           b'/DATA_ALL_PLATFORMS/" alias="Content" order="3" />\n')
    if content_pack is not None:
        xml += (b'  <CONTENTPACK UID="' + str(content_pack).encode() + b'">\n'
                b'    <PD_SECTION>\n'
                b'      <APP_ID ID="213850" />\n'
                b'    </PD_SECTION>\n'
                b'    <CONTENTFLAGS>\n'
                b'      <AVATAR_CONTENT />\n'
                b'      <DECK_CONTENT />\n'
                b'      <GLOSSARY_CONTENT />\n'
                b'      <UNLOCK_CONTENT />\n'
                b'    </CONTENTFLAGS>\n'
                b'  </CONTENTPACK>\n')
    xml += b'</WAD_HEADER>\n'
    return xml


def write(path, entries, uid, compress=True, content_pack=None):
    """entries 用**相对路径**（不含 uid），uid 既是根目录名也是内容声明里的 source"""
    data = build(entries, make_header_xml(uid, content_pack),
                 root_name=uid, compress=compress)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
