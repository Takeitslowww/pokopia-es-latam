from __future__ import annotations

import hashlib
import struct

PAGE = 0x1000


def _align(x: int, a: int = PAGE) -> int:
    return (x + a - 1) & ~(a - 1)


def _elf_sections(e: bytes) -> dict[str, tuple[int, int]]:
    shoff, = struct.unpack_from("<Q", e, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", e, 0x3A)
    sh = [struct.unpack_from("<IIQQQQIIQQ", e, shoff + i * shentsize) for i in range(shnum)]
    stroff = sh[shstrndx][4]
    out = {}
    for name, _type, _flags, addr, _off, size, *_ in sh:
        end = e.index(b"\0", stroff + name)
        out[e[stroff + name:end].decode()] = (addr, size)
    return out


def elf_to_nso(elf: bytes, build_id: bytes = b"") -> bytes:
    if elf[:4] != b"\x7fELF" or elf[4] != 2:
        raise ValueError("not an ELF64 file")
    phoff, = struct.unpack_from("<Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x36)
    loads = []
    for i in range(phnum):
        p_type, _flags, off, vaddr, _paddr, filesz, memsz, _align_ = \
            struct.unpack_from("<IIQQQQQQ", elf, phoff + i * phentsize)
        if p_type == 1:
            loads.append((vaddr, elf[off:off + filesz], memsz))
    if len(loads) != 3:
        raise ValueError(f"expected 3 PT_LOAD segments, found {len(loads)}")
    (t_va, t_raw, _), (r_va, r_raw, _), (d_va, d_raw, d_mem) = loads
    text = t_raw + bytes(_align(len(t_raw)) - len(t_raw))
    ro = r_raw + bytes(_align(len(r_raw)) - len(r_raw))
    data = d_raw + bytes(_align(len(d_raw)) - len(d_raw))
    if t_va != 0 or r_va != len(text) or d_va != r_va + len(ro):
        raise ValueError("segments are not contiguous page-aligned text/ro/data")
    bss = max(0, _align(d_mem) - len(data))

    hdr = bytearray(0x100)
    hdr[0:4] = b"NSO0"
    pos = 0x100
    struct.pack_into("<IIII", hdr, 0x10, pos, t_va, len(text), 0)
    struct.pack_into("<IIII", hdr, 0x20, pos + len(text), r_va, len(ro), 0)
    struct.pack_into("<IIII", hdr, 0x30, pos + len(text) + len(ro), d_va, len(data), bss)
    hdr[0x40:0x60] = (build_id + bytes(0x20))[:0x20]
    struct.pack_into("<III", hdr, 0x60, len(text), len(ro), len(data))
    sections = _elf_sections(elf)
    for slot, name in ((0x90, ".dynstr"), (0x98, ".dynsym")):
        if name in sections:
            addr, size = sections[name]
            struct.pack_into("<II", hdr, slot, addr - r_va, size)
    hdr[0xA0:0xC0] = hashlib.sha256(text).digest()
    hdr[0xC0:0xE0] = hashlib.sha256(ro).digest()
    hdr[0xE0:0x100] = hashlib.sha256(data).digest()
    return bytes(hdr) + text + ro + data


def lz4_block_decompress(src: bytes, size: int) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - off
        if off >= mlen:
            out += out[start:start + mlen]
        else:
            chunk = out[start:]
            reps, rest = divmod(mlen, off)
            out += chunk * reps + chunk[:rest]
    if len(out) != size:
        raise ValueError(f"LZ4 output {len(out)} bytes, expected {size}")
    return bytes(out)


def load_image(nso: bytes) -> bytes:
    if nso[:4] != b"NSO0":
        raise ValueError("not an NSO0 file")
    flags, = struct.unpack_from("<I", nso, 0x0C)
    segs = [struct.unpack_from("<III", nso, 0x10 + i * 0x10) for i in range(3)]
    csizes = struct.unpack_from("<III", nso, 0x60)
    end = segs[2][1] + segs[2][2]
    img = bytearray(end)
    for i, (off, loc, size) in enumerate(segs):
        if flags >> i & 1:
            img[loc:loc + size] = lz4_block_decompress(nso[off:off + csizes[i]], size)
        else:
            img[loc:loc + size] = nso[off:off + size]
    return bytes(img)


def build_id(nso: bytes) -> str:
    return nso[0x40:0x54].hex().upper()
