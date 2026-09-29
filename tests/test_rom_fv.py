# -*- coding: utf-8 -*-
"""ROM 固件卷解析器的测试（纯离线，用合成镜像，不需要真 ROM）。

    runtime\\python.exe tests\\test_rom_fv.py

tools\\rom_fv_dump.py 是只读工具：只 open 一个磁盘文件，不刷写、不碰 EC、不碰 UEFI 变量。
它有两个坑是**在本机 ROM 上真栽过**的，所以用合成镜像钉住：

  1. GUID_DEFINED 段的 DataOffset 从段头算起，而代码里的 data 已经去掉了 4 字节
     Size/Type，所以要减 4。忘了减，LZMA 全解不开（第一版 74 次全失败）。
  2. LZMA 段是标准 LZMA_Alone 头（props+dict+8 字节长度），不是「只有 props」。
     自己拼 8 个 0xFF 当长度反而解不开；dict_size 不夹范围还会 Internal error。
"""
import binascii
import lzma
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import rom_fv_dump as R                                # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 合成镜像

def sect(stype, data):
    """一个段：Size(3) + Type(1) + data，4 字节对齐。"""
    body = struct.pack('<I', len(data) + 4)[:3] + bytes([stype]) + data
    return body + b'\x00' * ((4 - len(body) % 4) % 4)


def sect_ui(name):
    return sect(0x15, name.encode('utf-16-le') + b'\x00\x00')


def sect_guid_lzma(payload):
    """GUID_DEFINED + LZMA：4 字节段头 + GUID(16) + DataOffset(2) + Attr(2) + 载荷。

    DataOffset = 24，**从段头算起**——这就是那个差 4 的坑。
    """
    head = R.guid_bytes(R.GUID_LZMA) + struct.pack('<HH', 24, 1)
    return sect(0x02, head + payload)


def ffs(ftype, sections, name_guid=None):
    body = b''.join(sections)
    size = 24 + len(body)
    gid = name_guid or bytes(range(16))
    return gid + b'\x00\x00' + bytes([ftype, 0]) + \
        struct.pack('<I', size)[:3] + b'\xF8' + body


def fv(files, pad_to=0x100):
    """一个最小固件卷：72 字节头（HeaderLength=72，含 BlockMap），文件链跟在后面。"""
    body = b''.join(files)
    hdrlen = 72
    total = max(pad_to, hdrlen + len(body))
    head = (b'\x00' * 16                       # ZeroVector
            + b'\x11' * 16                     # FileSystemGuid（解析器不校验）
            + struct.pack('<Q', total)         # FvLength
            + b'_FVH'
            + struct.pack('<I', 0x0004FEFF)    # Attributes
            + struct.pack('<H', hdrlen)        # HeaderLength
            + struct.pack('<H', 0)             # Checksum
            + struct.pack('<H', 0)             # ExtHeaderOffset
            + b'\x00'                          # Reserved
            + b'\x02'                          # Revision
            + struct.pack('<II', 1, total)     # BlockMap
            + struct.pack('<II', 0, 0))        # BlockMap 结束
    assert len(head) == hdrlen, len(head)
    return head + body + b'\xFF' * (total - hdrlen - len(body))


def alone(data):
    return lzma.compress(data, format=lzma.FORMAT_ALONE)


def walk(rom, **kw):
    w = R.Walker(rom, None, None, **kw)
    w.run()
    return w


def ui_names(w):
    return [s['ui'] for rec in w.files for s in rec['sections'] if s.get('ui')]


# ---------------------------------------------------------------- 用例

@case('guid_str / guid_bytes 互逆，LZMA GUID 认得出来')
def _():
    raw = R.guid_bytes(R.GUID_LZMA)
    assert len(raw) == 16
    assert R.guid_str(raw) == R.GUID_LZMA
    assert R.guid_str(bytes.fromhex('93fd219e729c154c8c4be77f1db2d792')) == \
        '9e21fd93-9c72-4c15-8c4b-e77f1db2d792'      # 本机 ROM 里的 DxeIpl


@case('LZMA_Alone 载荷能原样解回来（13 字节头，不是只有 props）')
def _():
    blob = b'GM7MG0M' * 5000
    assert R.lzma_decompress(alone(blob)) == blob
    head = alone(blob)[:13]
    assert head[0] == 0x5D, binascii.hexlify(head).decode()
    # 长度字段要么写实数、要么写 8 个 0xFF 表示未知，两种都得能解
    declared = struct.unpack('<Q', head[5:13])[0]
    assert declared in (len(blob), 0xFFFFFFFFFFFFFFFF), declared


@case('LZMA 解不开时返回 None，不抛异常')
def _():
    assert R.lzma_decompress(b'\x00\x01\x02\x03') is None
    assert R.lzma_decompress(b'') is None
    # dict_size = 0xFFFFFFFF：真拿它去建解压器会 Internal error（本机第一版就崩在这）
    assert R.lzma_decompress(b'\x5d\xff\xff\xff\xff' + b'\x00' * 64) is None


@case('一个卷一个模块：走得出 USER_INTERFACE 名字')
def _():
    w = walk(fv([ffs(0x07, [sect_ui('OemPowerModeDxe')])]))
    assert w.stats['fv_ok'] == 1 and w.stats['fv_bad'] == 0
    assert ui_names(w) == ['OemPowerModeDxe']


@case('GUID_DEFINED+LZMA 里的内层卷能递归进去（钉住 DataOffset 差 4）')
def _():
    inner = fv([ffs(0x07, [sect_ui('InnerDxe')])])
    outer = fv([ffs(0x0B, [sect_guid_lzma(alone(inner))])])
    w = walk(outer)
    assert w.stats['lzma_ok'] == 1 and w.stats['lzma_fail'] == 0
    assert 'InnerDxe' in ui_names(w), ui_names(w)


@case('DataOffset 不减 4 就一定解不开（反向确认这个坑存在）')
def _():
    inner = fv([ffs(0x07, [sect_ui('InnerDxe')])])
    payload = alone(inner)
    # 手工按「忘了减 4」的方式取载荷，应当解不出来
    assert R.lzma_decompress(payload[4:]) is None or \
        R.lzma_decompress(payload[4:]) != inner


@case('TIANO 压缩段如实记 undecoded，不假装解开')
def _():
    comp = sect(0x01, struct.pack('<IB', 1234, 0) + b'\xAA' * 40)
    w = walk(fv([ffs(0x07, [comp])]))
    assert w.stats['compression_type_0'] == 1
    got = [s for rec in w.files for s in rec['sections'] if s['type'] == 0x01]
    assert got and got[0]['undecoded'] == 40
    assert got[0]['uncompressed_len'] == 1234


@case('_FVH 撞上但卷长不合法：记 fv_bad，不崩')
def _():
    bad = bytearray(fv([ffs(0x07, [sect_ui('Nope')])]))
    struct.pack_into('<Q', bad, 32, len(bad) * 99)      # FvLength 超出镜像
    w = walk(bytes(bad))
    assert w.stats['fv_bad'] == 1 and w.stats['fv_ok'] == 0
    assert ui_names(w) == []


@case('已擦除的文件（Size=0xFFFFFF）跳过，不卡死')
def _():
    erased = bytes(16) + b'\x00\x00' + bytes([0x07, 0]) + b'\xff\xff\xff' + b'\xF8'
    w = walk(fv([erased + b'\xFF' * 8, ffs(0x07, [sect_ui('AfterErase')])]))
    assert 'AfterErase' in ui_names(w)


@case('--find 记下命中位置与前后文，UTF-16 也认')
def _():
    body = b'....Taitan Series GM7MG0M.Standard' + b'\x00' * 8 + \
        'PowerMode'.encode('utf-16-le')
    w = walk(fv([ffs(0x07, [sect(0x19, body)])]),
             find='GM7MG0M', find_u16='PowerMode')
    kinds = [f[2] for f in w.finds]
    assert 'ASCII' in kinds and 'UTF-16' in kinds, w.finds
    ascii_hit = [f for f in w.finds if f[2] == 'ASCII'][0]
    assert ascii_hit[3] == b'GM7MG0M' and 'Taitan' in ascii_hit[4]


@case('机型名候选正则只收 4..64 字节的可见串')
def _():
    hits = R.strings_hits(b'\x00GM\x00 \x00\x00MECHREVO\x00\x00GM7MG0M\x00', R.MODEL_RE)
    assert hits['MECHREVO'] == 1 and hits['GM7MG0M'] == 1
    assert 'GM' not in hits


@case('扫描不写盘：没给 --out 就不该产生任何文件')
def _():
    w = R.Walker(fv([ffs(0x07, [sect_ui('X')])]), None, None)
    w.scan(b'GM7MG0M', 'synthetic')
    assert w.saved == [] and w.outdir is None
    assert w.hits['GM7MG0M'] == 1


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print('  ok  %s' % name)
        except AssertionError as exc:
            bad += 1
            print('  FAIL %s -> %r' % (name, exc))
        except Exception as exc:                          # noqa: BLE001
            bad += 1
            print('  ERR  %s -> %s: %s' % (name, type(exc).__name__, exc))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
