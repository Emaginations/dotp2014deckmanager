# -*- coding: utf-8 -*-
"""
Duels of the Planeswalkers WAD 读取器（Python 实现）

格式来自 Gibbed 的开源实现（zlib 许可）：
    https://github.com/gibbed/Gibbed.Duels
    Gibbed.Duels.FileFormats/WadFile.cs / Wad/DirectoryEntry.cs / Wad/FileEntry.cs
    Gibbed.Duels.FileFormats/Wad/ArchiveFlags.cs
    Gibbed.Duels.Unpack/Program.cs

容器布局
--------
    u16  magic = 0x1234
    u16  version           0x100~0x101 或 0x200~0x202
    u32  flags             仅当 version==0x101 或 >=0x200
    u32  headerXmlLength   仅当 version >= 0x202
    ...  headerXml
    u32  stringTableSize
    ...  stringTable       仅当 version >= 0x200（CP1252，偏移寻址，null 结尾）
    u32  dataTypesCount    仅当 flags & HasDataTypes
         { u32 index; u32 unknown } × count
    u32  totalFileCount
    u32  totalDirectoryCount
    u32  dataOffsetsCount  仅当 version >= 0x200
         u32 dataOffsets[]  ← 所有文件数据区的绝对偏移
    ...  fileTable         (目录数 + 文件数) × 16 字节

fileTable 是一个递归树，每个节点 16 字节：
    目录: u32 名字索引, u32 文件数, u32 子目录数, u32 0
          → 先递归子目录，再列文件
    文件: u32 名字索引, u32 Size, u32 Flags, u32 0
          Flags 低 24 位 = dataOffsets 的下标，高 8 位 = 1

文件数据
--------
    定位: seek(dataOffsets[entry.offset_index])
    flags & HasCompressedFiles (bit 1) == 0  → 直接读 Size 字节
    否则:
        s32 length = 读 4 字节
        length == -1  → 读 Size-4 字节（该条目未压缩）
        否则          → 读 Size-4 字节并 zlib 解压成 length 字节
"""

import os
import struct
import zlib

MAGIC = 0x1234

FLAG_HAS_COMPRESSED_FILES = 1 << 1
FLAG_HAS_DATA_TYPES = 1 << 9


class File(object):
    __slots__ = ("name", "path", "size", "flags", "offset_index", "offset_count")

    def __init__(self, name, path, size, flags, offset_index, offset_count):
        self.name = name
        self.path = path
        self.size = size
        self.flags = flags
        self.offset_index = offset_index
        self.offset_count = offset_count

    def __repr__(self):
        return "<File %s size=%d off=#%d>" % (self.path, self.size, self.offset_index)


class Wad(object):
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.raw = f.read()
        self._parse()

    # ---------------- 解析 ----------------
    def _parse(self):
        d = self.raw
        if len(d) < 12:
            raise ValueError("文件太小")
        magic, version = struct.unpack_from("<HH", d, 0)
        if magic != MAGIC:
            raise ValueError("不是 WAD（magic=0x%04X）" % magic)
        self.version = version

        pos = 4
        self.flags = 0
        if version == 0x101 or version >= 0x200:
            self.flags = struct.unpack_from("<I", d, pos)[0]
            pos += 4

        self.header_xml = b""
        if version >= 0x202:
            n = struct.unpack_from("<I", d, pos)[0]
            pos += 4
            self.header_xml = d[pos:pos + n]
            pos += n

        string_table_size = struct.unpack_from("<I", d, pos)[0]
        pos += 4
        string_table = b""
        if version >= 0x200:
            string_table = d[pos:pos + string_table_size]
            pos += string_table_size

        self.data_types = []
        if self.flags & FLAG_HAS_DATA_TYPES:
            cnt = struct.unpack_from("<I", d, pos)[0]
            pos += 4
            for _ in range(cnt):
                idx, unk = struct.unpack_from("<II", d, pos)
                pos += 8
                self.data_types.append((idx, unk))

        self.total_file_count = struct.unpack_from("<I", d, pos)[0]
        pos += 4
        self.total_directory_count = struct.unpack_from("<I", d, pos)[0]
        pos += 4

        self.data_offsets = []
        self._offsets_array_pos = pos
        if version >= 0x200:
            cnt = struct.unpack_from("<I", d, pos)[0]
            pos += 4
            self._offsets_array_pos = pos
            self.data_offsets = list(struct.unpack_from("<%dI" % cnt, d, pos))
            pos += 4 * cnt

        if version == 0x100:
            string_table = d[pos:pos + string_table_size]
            pos += string_table_size

        self._st = string_table

        # 文件表：递归树，总节点数已知
        self.file_table_pos = pos
        self.files = []
        end = pos + (self.total_directory_count + self.total_file_count) * 16
        self._read_dir(pos, end, "", None)
        self.file_table_end = end

        # 数据区：条目首尾相接、无填充，起点就是最小偏移
        self.data_start = min(self.data_offsets) if self.data_offsets else end
        if self.file_table_end > self.data_start:
            raise ValueError("文件表越界（%d > %d）" % (self.file_table_end, self.data_start))

        # 每个 offset_index 的条目大小（多个文件可能共用同一个数据块）
        self._size_of_index = {}
        for f in self.files:
            self._size_of_index.setdefault(f.offset_index, f.size)

        order = sorted(range(len(self.data_offsets)), key=lambda i: self.data_offsets[i])

        # 数据块 = [偏移, 偏移+Size)，**不含块间填充**。
        # 有些归档（D14/CORE）会在块之间按 4 字节对齐填 0，Size 字段不计入填充 ——
        # 用"到下一个偏移的距离"当大小会把填充算进去，重建时 Size 就被写坏了。
        self._blob_of_index = {}
        for i in order:
            a = self.data_offsets[i]
            s = self._size_of_index.get(i)
            if s is None:
                k = order.index(i)
                b = self.data_offsets[order[k + 1]] if k + 1 < len(order) else len(self.raw)
                s = b - a
            self._blob_of_index[i] = self.raw[a:a + s]

        # 对齐规则：全部偏移都是 4 的倍数、且确实存在块间填充 → 4；否则不填充
        self.align = 4 if (all(o % 4 == 0 for o in self.data_offsets)
                           and self._has_gaps(order)) else 1

        # 末尾多余字节（D14 尾部有 512 字节非零数据，原样保留）
        cur = self.data_start
        for i in order:
            cur += len(self._blob_of_index[i])
            cur = (cur + self.align - 1) // self.align * self.align
        self.tail = self.raw[cur:]

    def _has_gaps(self, order):
        for k in range(len(order) - 1):
            i = order[k]
            end = self.data_offsets[i] + len(self._blob_of_index[i])
            if self.data_offsets[order[k + 1]] > end:
                return True
        return False

    def _name(self, index):
        e = self._st.find(b"\x00", index)
        return self._st[index:e].decode("cp1252", "replace")

    def _read_dir(self, pos, end, prefix, _parent):
        name_index, file_count, dir_count, unknown = struct.unpack_from("<IIII", self.raw, pos)
        pos += 16
        name = self._name(name_index)
        here = prefix + name + "/"

        for _ in range(dir_count):
            pos = self._read_dir(pos, end, here, None)

        for _ in range(file_count):
            rec_pos = pos
            ni, size, flags, unk0c = struct.unpack_from("<IIII", self.raw, pos)
            pos += 16
            fname = self._name(ni)
            entry = File(
                fname, here + fname, size, flags,
                flags & 0x00FFFFFF, (flags & 0xFF000000) >> 24,
            )
            self.files.append(entry)
            # 记下这条记录在文件里的位置，重建时只改它的 Size 字段（记录 +4）
            if not hasattr(self, "_entry_positions"):
                self._entry_positions = []
            self._entry_positions.append((rec_pos, entry))
        return pos

    # ---------------- 取数据 ----------------
    @property
    def has_compressed_files(self):
        return bool(self.flags & FLAG_HAS_COMPRESSED_FILES)

    def read(self, entry):
        """返回该条目的原始（解压后）字节"""
        start = self.data_offsets[entry.offset_index]
        if not self.has_compressed_files:
            return self.raw[start:start + entry.size]

        length = struct.unpack_from("<i", self.raw, start)[0]
        body = self.raw[start + 4:start + entry.size]
        if length == -1:
            return body
        return zlib.decompress(body, 15, length)

    def find(self, needle):
        """按路径子串找条目"""
        n = needle.lower()
        return [f for f in self.files if n in f.path.lower()]

    # ---------------- 改编与重建 ----------------
    def blob(self, index):
        return self._blob_of_index[index]

    def make_blob(self, content):
        """把内容包成条目数据块：s32 解压后长度 + zlib（长度 == -1 表示不压缩）"""
        if self.has_compressed_files:
            return struct.pack("<i", len(content)) + zlib.compress(content, 9)
        return content

    def rebuild(self, replacements):
        """重建整个 WAD。

        replacements: { offset_index: 新的解压后内容 }
        未列出的数据块原样保留（避免碰任何不需要碰的东西）。

        头部区（magic/version/flags/headerXml/字符串表/dataTypes/计数）**原样复制**，
        只重写 dataOffsets 数组、文件表里变动条目的 Size/Flags、以及数据区。
        """
        # dataOffsets 的值不保证按 index 递增（D14 就不是），但物理布局是按偏移排的。
        # 所以按**物理顺序**重新铺，再把新偏移回填到各自下标 —— 下标只是寻址用的。
        order = sorted(range(len(self.data_offsets)), key=lambda i: self.data_offsets[i])

        # 1. 按物理顺序算新数据块，同时记下每个下标的新偏移
        new_offsets = [0] * len(self.data_offsets)
        size_of_index = {}
        laid_out = []
        cur = self.data_start
        for i in order:
            b = self.make_blob(replacements[i]) if i in replacements else self._blob_of_index[i]
            new_offsets[i] = cur
            size_of_index[i] = len(b)
            laid_out.append(b)
            cur += len(b)
            # 块间按归档自己的规矩对齐（D14/CORE 是 4 字节，其余不填）——
            # 填充字节必须真的写进文件，只把偏移算对齐是不够的
            pad = (cur + self.align - 1) // self.align * self.align - cur
            if pad:
                laid_out.append(b"\x00" * pad)
                cur += pad

        # 4. 拼文件：头部原样 + 重写的偏移数组 + 重写的文件表 + 数据区
        out = bytearray(self.raw[:self.data_start])

        # 4a. dataOffsets 数组
        arr_pos = self._offsets_array_pos
        struct.pack_into("<%dI" % len(new_offsets), out, arr_pos, *new_offsets)

        # 4b. 文件表：只改变动条目的 Size 字段（位于记录 +4）
        for pos4, entry in self._entry_positions:
            new_size = size_of_index[entry.offset_index]
            if new_size != entry.size:
                struct.pack_into("<I", out, pos4 + 4, new_size)

        # 4c. 数据区 + 末尾原有字节（D14 尾部那 512 字节非零数据原样带过去）
        out = out[:self.data_start] + b"".join(laid_out) + self.tail
        return bytes(out)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        w = Wad(p)
        print("=== %s" % os.path.basename(p))
        print("   version=0x%03X flags=0x%03X 压缩=%s" % (w.version, w.flags, w.has_compressed_files))
        print("   文件 %d | 目录 %d | 数据区 %d" % (len(w.files), w.total_directory_count, len(w.data_offsets)))
        for f in w.files[:8]:
            print("     %-60s size=%d" % (f.path, f.size))
