# -*- coding: utf-8 -*-
"""只读解开 UEFI ROM 的固件卷，找内部机型名、OEM 模块名与厂商自己写的字符串表。

    runtime\\python.exe tools\\rom_fv_dump.py <rom路径> [选项]

**只读一个磁盘文件**：不刷写、不碰 EC、不碰 UEFI 变量、不调用任何驱动。
手上没有 UEFITool / ifrextractor 也能跑——LZMA 用 Python 自带的 lzma 解。

选项：
  --out DIR          落盘目录（--dump 必需）
  --dump A,B,C       按 USER_INTERFACE 模块名导出 PE32/TE 段体
  --find REGEX       ASCII 定位检索，打印所在卷、偏移与上下文
  --find-u16 A,B     UTF-16LE 定位检索（逗号分隔的词）
  --model-re REGEX   换掉默认的机型名候选正则
  --ui N             列多少个模块名（0 = 不列）
  --save-all         把扫到的每个段体都落盘（几百兆，一般不用）

实现要点（都是本机 ROM 上试出来的，不是照文档猜的）：
  1. 扫 `_FVH` 定位固件卷；卷头里 FvLength 在 +32、HeaderLength 在 +48，
     文件链从 start+HeaderLength 开始。47 个 `_FVH` 里只有 19 个是真卷，
     其余是压缩数据里的巧合，所以头部合法性检查不能省。
  2. FFS 文件头 24 字节（Name GUID/IntegrityCheck/Type/Attributes/Size[3]/State），
     Size==0xFFFFFF 表示已擦除；段头 4 字节（Size[3]/Type），Size==0xFFFFFF 时
     跟一个 64 位扩展头。
  3. **GUID_DEFINED 的 DataOffset 是从段头算起的**，而 data 已经去掉了 4 字节的
     Size/Type，所以要减 4。这个 off-by-4 会让 LZMA 全军覆没（第一版就栽在这）。
  4. LZMA 段是**标准 LZMA_Alone 头**：props(1) + dict(4) + uncompressed_size(8) + 裸流，
     直接喂 FORMAT_ALONE 就能解。`5d 00 00 00 01` + `10 e0 99 00 00 00 00 00`
     声明 0x99E010 = 10,084,368，解出来正好这么多，里面有 21 个 `_FVH`。
     dict_size 必须夹在 4 KiB..256 MiB，否则 0xFFFFFFFF 会让解压器直接 Internal error。
  5. TIANO 压缩（COMPRESSION 段 ctype=0）没实现，遇到就如实记 `undecoded`，不假装解开。
"""
import binascii
import collections
import io
import os
import re
import struct
import sys

SECTION_NAMES = {
    0x01: 'COMPRESSION', 0x02: 'GUID_DEFINED', 0x03: 'DISPOSABLE',
    0x10: 'PE32', 0x11: 'PIC', 0x12: 'TE', 0x13: 'DXE_DEPEX', 0x14: 'VERSION',
    0x15: 'USER_INTERFACE', 0x16: 'COMPAT16', 0x17: 'FV_IMAGE',
    0x18: 'FREEFORM_GUID', 0x19: 'RAW', 0x1B: 'PEI_DEPEX', 0x1C: 'MM_DEPEX',
}

GUID_LZMA = 'ee4e5898-3914-4259-9d6e-dc7bd79403cf'


def guid_str(raw):
    a, b, c = struct.unpack('<IHH', raw[:8])
    return '%08x-%04x-%04x-%s-%s' % (a, b, c,
                                      binascii.hexlify(raw[8:10]).decode(),
                                      binascii.hexlify(raw[10:16]).decode())


def guid_bytes(text):
    d = binascii.unhexlify(text.replace('-', ''))
    a, b, c = struct.unpack('>IHH', d[:8])
    return struct.pack('<IHH', a, b, c) + d[8:]


def lzma_decompress(data):
    """解 EDK2 的 LZMA 段。本机 ROM 实测是标准 LZMA_Alone 头：

        props(1) + dict_size(4) + uncompressed_size(8) + 裸流

    （`5d 00 00 00 01` + `10 e0 99 00 00 00 00 00`，声明 0x99E010，解出来正好这么多。）
    有的构建只写 props 不写长度，所以再退两步：补 8 个 0xFF 当「长度未知」，
    最后按 FORMAT_RAW 从第 5 字节起解。
    """
    import lzma
    try:
        return lzma.decompress(data, format=lzma.FORMAT_ALONE)
    except (lzma.LZMAError, ValueError, MemoryError):
        pass
    try:
        return lzma.decompress(data[:5] + b'\xff' * 8 + data[5:], format=lzma.FORMAT_ALONE)
    except (lzma.LZMAError, ValueError, MemoryError):
        pass
    if len(data) < 6:
        return None
    props, dict_size = data[0], struct.unpack('<I', data[1:5])[0]
    lc = props % 9
    rem = props // 9
    lp, pb = rem % 5, rem // 5
    if lc > 8 or lp > 4 or pb > 4 or not (4096 <= dict_size <= 1 << 28):
        return None          # 不是真的 props 头，别拿它去建解压器（dict 0xFFFFFFFF 会炸）
    try:
        dec = lzma.LZMADecompressor(
            format=lzma.FORMAT_RAW,
            filters=[{'id': lzma.FILTER_LZMA1, 'dict_size': dict_size,
                      'lc': lc, 'lp': lp, 'pb': pb}])
        return dec.decompress(data[5:]) or None
    except (lzma.LZMAError, ValueError, MemoryError):
        return None


class Walker:
    def __init__(self, rom, outdir=None, regex=None, find=None, find_u16=None,
                 dump=None, save_all=False):
        self.rom = rom
        self.outdir = outdir
        self.regex = regex or MODEL_RE
        self.save_all = save_all
        self.dump = set(dump.split(',')) if dump else set()
        self.find_re = re.compile(find.encode()) if find else None
        self.find_re_u16 = u16_pattern(find_u16.split(',')) if find_u16 else None
        self.finds = []                        # (label, off, 编码, 命中, 前文, 后文)
        self.files = []          # 每条: dict(vol, off, name, type, size, sections)
        self.guids = collections.Counter()
        self.sectypes = collections.Counter()
        self.hits = collections.Counter()      # 机型名候选（ASCII）
        self.u16 = collections.Counter()       # 机型名候选（UTF-16）
        self.saved = []                        # 落盘的块名
        self.fvs = []
        self.stats = collections.Counter()
        self.total_uncompressed = 0

    def scan(self, buf, label, save=True):
        """在一段字节里找机型名候选。找完就丢，不整块留在内存里。"""
        self.hits.update(strings_hits(buf, self.regex))
        for w in U16_WORDS:
            c = buf.count(w.encode('utf-16-le'))
            if c:
                self.u16[w] += c
        if self.find_re:
            for m in self.find_re.finditer(buf):
                lo, hi = max(0, m.start() - 24), min(len(buf), m.end() + 40)
                self.finds.append((label, m.start(), 'ASCII', m.group(0),
                                   printable(buf[lo:m.start()]),
                                   printable(buf[m.end():hi])))
        if self.find_re_u16:
            for m in self.find_re_u16.finditer(buf):
                lo = max(0, (m.start() & ~1) - 48)
                hi = min(len(buf), (m.end() & ~1) + 80)
                self.finds.append((label, m.start(), 'UTF-16', m.group(0),
                                   printable16(buf[lo:m.start()]),
                                   printable16(buf[m.end():hi])))
        if save and self.save_all and len(buf) <= MAX_SAVE:
            safe = re.sub(r'[^0-9A-Za-z_.\-]', '_', label)[:150]
            with io.open(os.path.join(self.outdir, safe + '.bin'), 'wb') as f:
                f.write(buf)
            self.saved.append(safe + '.bin')

    def run(self):
        for off in [m.start() - 40 for m in re.finditer(rb'_FVH', self.rom)]:
            if off < 0:
                continue
            self.fv(off, 'FV@0x%X' % off, depth=0)
        return self

    def fv(self, start, label, depth):
        if depth > 6 or start + 64 > len(self.rom):
            return
        hdr = self.rom[start:start + 64]
        if len(hdr) < 64 or hdr[40:44] != b'_FVH':
            return
        fvlen = struct.unpack('<Q', hdr[32:40])[0]
        hdrlen = struct.unpack('<H', hdr[48:50])[0]
        if not (0 < fvlen <= len(self.rom) - start) or hdrlen < 56:
            self.stats['fv_bad'] += 1
            return
        body = self.rom[start + hdrlen:start + fvlen]
        self.fvs.append((label, start, fvlen, hdrlen))
        self.stats['fv_ok'] += 1
        self.ffs_chain(body, label, depth)

    def ffs_chain(self, body, label, depth):
        off = 0
        n = len(body)
        guard = 0
        while off + 24 <= n and guard < 20000:
            guard += 1
            h = body[off:off + 24]
            size = h[20] | (h[21] << 8) | (h[22] << 16)
            ftype = h[18]
            if size == 0xFFFFFF or size < 24:
                off += 8                 # 已擦除的空闲区，按 8 字节对齐往前挪
                continue
            if off + size > n:
                off += 8                 # 不是真文件头（从空闲区里错位读出来的），重新对齐
                continue
            name = guid_str(h[:16])
            rec = {'vol': label, 'off': off, 'name': name, 'type': ftype,
                   'size': size, 'sections': []}
            self.files.append(rec)
            self.stats['file'] += 1
            self.sections(body[off:off + size], rec, label, depth)
            self.maybe_dump(rec, label)
            off = (off + size + 7) & ~7
        return

    def maybe_dump(self, rec, label):
        """--dump 指定的模块名，把它的 PE32/TE 段体落盘（名字在 USER_INTERFACE 段里）。"""
        keep = rec.pop('_keep', None)
        if not keep or not self.dump:
            return
        uis = [s.get('ui') for s in rec['sections'] if s.get('ui')]
        want = [u for u in uis if u in self.dump]
        if not want:
            return
        for kind, data in keep:
            fn = '%s.%s' % (want[0], kind.lower())
            with io.open(os.path.join(self.outdir, fn), 'wb') as f:
                f.write(data)
            self.saved.append(fn)
            print('导出 %s (%s, %d 字节) ← %s' % (fn, kind, len(data), label))

    def sections(self, buf, rec, label, depth):
        off = 24
        n = len(buf)
        guard = 0
        while off + 4 <= n and guard < 4000:
            guard += 1
            ssize = buf[off] | (buf[off + 1] << 8) | (buf[off + 2] << 16)
            stype = buf[off + 3]
            hdr = 4
            if ssize == 0xFFFFFF:                     # 扩展头：64 位长度
                if off + 16 > n:
                    break
                ssize = struct.unpack('<Q', buf[off + 4:off + 12])[0]
                hdr = 16
            if ssize < hdr or off + ssize > n:
                break
            data = buf[off + hdr:off + ssize]
            self.sectypes[stype] += 1
            info = {'type': stype, 'name': SECTION_NAMES.get(stype, 'OEM_0x%02X' % stype),
                    'size': ssize, 'off': off}
            rec['sections'].append(info)
            if self.dump and stype in (0x10, 0x12):
                rec.setdefault('_keep', []).append((info['name'], data))
            if stype == 0x02 and len(data) >= 20:      # GUID_DEFINED
                g = guid_str(data[:16])
                self.guids[g] += 1
                info['guid'] = g
                doff = struct.unpack('<H', data[16:18])[0]
                # DataOffset 是从段头（含 4 字节 Size/Type）算起的，data 已经去掉了那 4 字节
                payload = data[doff - 4:] if 4 <= doff <= len(data) else data[20:]
                if g == GUID_LZMA:
                    self.try_uncompress(payload, rec, label, depth, 'lzma')
                else:
                    info['undecoded'] = len(payload)
            elif stype == 0x01 and len(data) >= 5:     # COMPRESSION
                ulen, ctype = struct.unpack('<IB', data[:5])
                info['uncompressed_len'] = ulen
                info['ctype'] = ctype
                self.stats['compression_type_%d' % ctype] += 1
                if ctype == 1:
                    self.try_uncompress(data[5:], rec, label, depth, 'lzma')
                else:
                    info['undecoded'] = len(data) - 5
            elif stype == 0x17 and b'_FVH' in data[:64]:
                self.scan(data, '%s/%s:FV_IMAGE' % (label, rec['name']))
                self.fv_of_blob(data, label, depth)
            elif stype == 0x15:                        # USER_INTERFACE，UTF-16 名字
                try:
                    info['ui'] = data.decode('utf-16-le').rstrip('\x00')
                except UnicodeDecodeError:
                    pass
            elif stype == 0x14 and data:               # VERSION
                info['version'] = data.rstrip(b'\x00').decode('ascii', 'replace')
            else:
                self.scan(data, '%s/%s:%s' % (label, rec['name'], info['name']))
            off = (off + ssize + 3) & ~3

    def try_uncompress(self, payload, rec, label, depth, how):
        if how != 'lzma':
            return
        self.stats['lzma_try'] += 1
        out = lzma_decompress(payload)
        if not out:
            self.stats['lzma_fail'] += 1
            return
        if len(out) > MAX_BLOB:
            self.stats['lzma_too_big'] += 1
            return
        self.stats['lzma_ok'] += 1
        self.total_uncompressed += len(out)
        rec['uncompressed'] = rec.get('uncompressed', 0) + len(out)
        tag = '%s/%s:lzma' % (label, rec['name'])
        self.scan(out, tag)
        self.fv_of_blob(out, tag, depth + 1)

    def fv_of_blob(self, data, label, depth):
        if depth > 6:
            return
        for m in re.finditer(rb'_FVH', data):
            self.fv_of_inner(data, m.start() - 40, label, depth)

    def fv_of_inner(self, data, start, label, depth):
        if start < 0 or start + 64 > len(data) or data[start + 40:start + 44] != b'_FVH':
            return
        fvlen = struct.unpack('<Q', data[start + 32:start + 40])[0]
        hdrlen = struct.unpack('<H', data[start + 48:start + 50])[0]
        if not (0 < fvlen <= len(data) - start) or hdrlen < 56:
            self.stats['inner_fv_bad'] += 1
            return
        sub = '%s>inner@0x%X' % (label, start)
        self.fvs.append((sub, -1, fvlen, hdrlen))
        self.stats['inner_fv_ok'] += 1
        self.ffs_chain(data[start + hdrlen:start + fvlen], sub, depth + 1)


MODEL_RE = re.compile(
    rb'(?:GM\d?MG\d?\w{0,12}|MECHREVO\w*|Uniwill\w*|TONGFANG\w*|TongFang\w*|'
    rb'Umi\s?Pro\s?\d?|UmiPro\w*|\w*MRO\d\w*|N109\w*|Clevo\w*|ProjectName\w*)')

U16_WORDS = ('GM5MG0Y', 'GM7MG7P', 'MECHREVO', 'Umi', 'Uniwill', 'TONGFANG',
             'PowerMode', 'INOU', 'Creator', 'OverClock')

MAX_BLOB = 96 * 1024 * 1024      # 解出来比这还大的当异常，不收（防内存炸）
MAX_SAVE = 32 * 1024 * 1024      # 落盘只收这个尺寸以下的


def strings_hits(buf, regex):
    out = collections.Counter()
    for m in regex.finditer(buf):
        s = m.group(0)
        if 4 <= len(s) <= 64:
            out[s.decode('ascii', 'replace')] += 1
    return out


def printable(raw):
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in raw)


def printable16(raw):
    return raw.decode('utf-16-le', 'replace').replace('\x00', '.')


def u16_pattern(words):
    """把若干词编成 UTF-16LE 的检索正则（每个字符后面跟一个 0x00）。"""
    return re.compile(rb'(?:' + rb'|'.join(
        rb'(?:' + rb''.join(re.escape(c.encode('utf-16-le')) for c in w) + rb')'
        for w in words) + rb')')


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('rom')
    ap.add_argument('--out', default=None, help='把解出的块落盘的目录')
    ap.add_argument('--model-re', default=None, help='自定义机型名检索正则')
    ap.add_argument('--ui', type=int, default=40, help='列多少个模块名')
    ap.add_argument('--find', default=None, help='ASCII 定位检索（正则），打印所在卷与上下文')
    ap.add_argument('--find-u16', default=None,
                    help='UTF-16 定位检索，逗号分隔的词，例：PowerMode,OverClock')
    ap.add_argument('--dump', default=None,
                    help='按 USER_INTERFACE 模块名导出 PE32/TE 段体，逗号分隔；需要 --out')
    ap.add_argument('--save-all', action='store_true',
                    help='把扫到的每个段体都落盘（几百兆，一般不用）')
    a = ap.parse_args()
    path, outdir = a.rom, a.out
    if a.dump and not outdir:
        ap.error('--dump 需要 --out 指定落盘目录')
    regex = re.compile(a.model_re) if a.model_re else MODEL_RE
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir)
    with io.open(path, 'rb') as f:
        rom = f.read()
    print('ROM: %s  %d 字节' % (path, len(rom)))
    w = Walker(rom, outdir, regex, a.find, a.find_u16, a.dump, a.save_all)
    w.scan(rom, 'ROM', save=False)        # 先扫未压缩的部分（不落盘）
    w.run()
    print('固件卷: 顶层 %d 个 _FVH，解析成功 %d，头部不合法 %d，内部卷 %d（坏 %d）'
          % (len([m for m in re.finditer(rb'_FVH', rom)]), w.stats['fv_ok'],
             w.stats['fv_bad'], w.stats['inner_fv_ok'], w.stats['inner_fv_bad']))
    print('FFS 文件 %d 个；段类型 %s'
          % (w.stats['file'], sorted(w.sectypes.items(), key=lambda kv: -kv[1])[:12]))
    print('LZMA: 试 %d，成功 %d，失败 %d，太大丢弃 %d；解出合计 %.1f MiB'
          % (w.stats['lzma_try'], w.stats['lzma_ok'], w.stats['lzma_fail'],
             w.stats['lzma_too_big'], w.total_uncompressed / 1048576.0))
    print('GUID_DEFINED 里的 GUID（前 12）:')
    for g, c in w.guids.most_common(12):
        print('   %-38s %d' % (g, c))
    names = collections.Counter()
    for rec in w.files:
        for s in rec['sections']:
            if s.get('ui'):
                names[s['ui']] += 1
    print('USER_INTERFACE 模块名 %d 个（前 %d）:' % (len(names), a.ui))
    for nm, c in names.most_common(a.ui):
        print('   %-46s %d' % (nm, c))
    print('机型名候选（ASCII，全 ROM + 所有解出的块）:')
    for s, c in w.hits.most_common(60):
        print('   %-40s %d' % (s, c))
    print('UTF-16 命中: %s' % (dict(w.u16) or '无'))
    if w.finds:
        print('定位命中 %d 处:' % len(w.finds))
        for label, off, enc, hit, before, after in w.finds[:200]:
            h = printable(hit) if enc == 'ASCII' else printable16(hit)
            print('   [%s] %s +0x%X  ...%s |%s| %s' % (enc, label, off, before, h, after))
    if outdir:
        print('解出的块写到: %s（%d 个）' % (outdir, len(w.saved)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
