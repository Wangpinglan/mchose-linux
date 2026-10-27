#!/usr/bin/env python3
"""列出 .NET 程序集里的类型与方法（手工解析元数据表，不依赖 dnfile 的换算）。

用法: python dump_types.py <assembly.exe>
输出: 每个 TypeDef 及其 MethodDef（含 RVA），便于定位关键函数。
"""
import sys
import struct

data = open(sys.argv[1], "rb").read()

# ---------- PE 基础 ----------
e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
assert data[e_lfanew:e_lfanew + 4] == b"PE\x00\x00"
coff = e_lfanew + 4
nsec = struct.unpack_from("<H", data, coff + 2)[0]
opt_size = struct.unpack_from("<H", data, coff + 16)[0]
opt = coff + 20
magic = struct.unpack_from("<H", data, opt)[0]
pe32plus = magic == 0x20B
# 数据目录起点
dd_off = opt + (112 if pe32plus else 96)
clr_rva, clr_size = struct.unpack_from("<II", data, dd_off + 14 * 8)

sec_off = opt + opt_size
sections = []
for i in range(nsec):
    o = sec_off + i * 40
    name = data[o:o + 8].rstrip(b"\x00").decode(errors="replace")
    vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, o + 8)
    sections.append((va, max(vsize, rawsize), rawptr, name))

def rva2off(rva):
    for va, sz, raw, name in sections:
        if va <= rva < va + sz:
            return raw + (rva - va)
    raise ValueError(f"RVA 0x{rva:x} 不在节区内")

# ---------- CLR 头 ----------
clr = rva2off(clr_rva)
md_rva, md_size = struct.unpack_from("<II", data, clr + 8)
meta = rva2off(md_rva)
assert data[meta:meta + 4] == b"BSJB"

# ---------- 元数据流 ----------
p = meta + 12
ver_len = struct.unpack_from("<I", data, p)[0]
p += 4 + ver_len + 2
nstreams = struct.unpack_from("<H", data, p)[0]
p += 2
streams = {}
for _ in range(nstreams):
    off, size = struct.unpack_from("<II", data, p)
    p += 8
    end = data.index(b"\x00", p)
    name = data[p:end].decode()
    p = (end + 4) & ~3
    streams[name] = (meta + off, size)

print("# streams:", {k: hex(v[0]) for k, v in streams.items()}, file=sys.stderr)
tilde = streams["#~"][0]

# ---------- #~ 头 ----------
heap_sizes = data[tilde + 6]
valid = struct.unpack_from("<Q", data, tilde + 8)[0]
p = tilde + 24
counts = {}
for i in range(64):
    if valid >> i & 1:
        counts[i] = struct.unpack_from("<I", data, p)[0]
        p += 4

def heap_idx(bit):
    return 4 if heap_sizes & bit else 2

str_i = heap_idx(1)
guid_i = heap_idx(2)
blob_i = heap_idx(4)

def ridx(t):
    return 4 if counts.get(t, 0) > 0xFFFF else 2

# 表行宽（ECMA-335 II.22）
def row_width(t):
    if t == 0x00: return 2 + str_i + 3 * guid_i
    if t == 0x01: return ridx(0x02) + 2 * str_i + 2 * guid_i
    if t == 0x02: return 4 + 2 * str_i + 3 * ridx(0x04 if False else 0x06)
    return None

# TypeDef: Flags(4) Name(str) Namespace(str) Extends(coded TypeDefOrRef) FieldList(Field) MethodList(MethodDef)
typedef_or_ref = 2 if max(counts.get(0x02, 0), counts.get(0x01, 0), counts.get(0x1B, 0)) < (1 << 14) else 4
td_width = 4 + str_i + str_i + typedef_or_ref + ridx(0x04) + ridx(0x06)
md_width = 4 + 2 + 2 + str_i + blob_i + ridx(0x08)

# 找到 TypeDef 表偏移：累加所有前面表的宽度
table_order = sorted(counts)
# 需要所有表的宽度，缺的用 4 字节估算（会影响准确性，故打印告警）
WIDTHS = {
    0x00: 2 + str_i + 3 * guid_i,
    0x01: ridx(0x02) + 2 * str_i + 2 * guid_i,
    0x02: td_width,
    0x04: 2 + str_i + blob_i,
    0x06: md_width,
    0x08: 2 + 2 + str_i,
    0x09: ridx(0x02) + ridx(0x01),
    0x0A: ridx(0x02) + 2 * str_i + blob_i + 2,   # MemberRef 近似
    0x0B: 2 + ridx(0x0A) + blob_i,
    0x0C: ridx(0x0A) + ridx(0x0C) + blob_i,      # 近似（父+类型+值）
    0x0D: ridx(0x04) + blob_i,
    0x0E: 2 + ridx(0x02) + blob_i,
    0x0F: 2 + 4 + ridx(0x02),
    0x10: 4 + ridx(0x04),
    0x11: blob_i,
    0x12: ridx(0x02) + ridx(0x14),
    0x14: 2 + str_i + ridx(0x0A),
    0x15: ridx(0x02) + ridx(0x17),
    0x17: 2 + str_i + blob_i,
    0x18: 2 + ridx(0x06) + ridx(0x17),
    0x19: ridx(0x02) + 2 * ridx(0x06),
    0x1A: str_i,
    0x1B: blob_i,
    0x1C: 2 + ridx(0x06) + str_i + ridx(0x1A),
    0x1D: 4 + ridx(0x04),
    0x20: 4 + 2 + 2 + 2 + 2 + 4 + blob_i + 2 * str_i,
    0x21: 1 + 1,
    0x22: 2 + 2 + 2 + 2 + 4 + blob_i + 2 * str_i,
    0x23: 4 + str_i + blob_i,
    0x26: 4 + str_i + ridx(0x23),
    0x27: ridx(0x02) + ridx(0x02),
    0x28: 2 + 2 + ridx(0x02) + str_i,
    0x29: ridx(0x2A) + ridx(0x1B),
    0x2A: ridx(0x02) + ridx(0x2A),
}

# 注意：按表号升序排列即为物理顺序
offsets = {}
cur = p
for t in table_order:
    w = WIDTHS.get(t)
    if w is None:
        print(f"!! 未定义表 0x{t:02x} (行数 {counts[t]})，后续偏移可能不准", file=sys.stderr)
        w = 4
    offsets[t] = cur
    cur += w * counts[t]

strings_off = streams["#Strings"][0]

def s(idx):
    if idx == 0:
        return ""
    o = strings_off + idx
    e = data.index(b"\x00", o)
    return data[o:e].decode("utf-8", errors="replace")

td_off = offsets[0x02]
md_table_off = offsets[0x06]

# 读 TypeDef 行
def read_td(i):
    o = td_off + i * td_width
    flags = struct.unpack_from("<I", data, o)[0]
    q = o + 4
    name = struct.unpack_from("<H" if str_i == 2 else "<I", data, q)[0]; q += str_i
    ns = struct.unpack_from("<H" if str_i == 2 else "<I", data, q)[0]; q += str_i
    q += typedef_or_ref
    q += ridx(0x04)
    ml = struct.unpack_from("<H" if ridx(0x06) == 2 else "<I", data, q)[0]
    return flags, name, ns, ml

def read_md(i):
    o = md_table_off + i * md_width
    rva = struct.unpack_from("<I", data, o)[0]
    q = o + 8
    name = struct.unpack_from("<H" if str_i == 2 else "<I", data, q)[0]
    return rva, name

nd_td = counts[0x02]
nd_md = counts[0x06]
tds = [read_td(i) for i in range(nd_td)]

print(f"=== TypeDef {nd_td} / MethodDef {nd_md} ===")
for i, (flags, name, ns, ml) in enumerate(tds):
    start = ml - 1
    end = tds[i + 1][3] - 1 if i + 1 < nd_td else nd_md
    full = f"{s(ns)}.{s(name)}" if s(ns) else s(name)
    n = max(0, end - start)
    if n == 0 and not full:
        continue
    print(f"[{i:3}] {full}  ({n})")
    for j in range(start, min(end, nd_md)):
        rva, mname = read_md(j)
        print(f"        - {s(mname)}  rva=0x{rva:x}")
