# -*- coding: utf-8 -*-
"""离线定位：驱动里有没有这些 IOCTL 常量，以及附近的缓冲长度校验。

背景：GCUService.exe 的常量表给出了 OEM 自己的 IOCTL 族（IOCTL_GPD_*，
设备类型 0x9C40，和微软 ACPI 的 0x32 完全不是一路），例如
    IOCTL_GPD_ACPI_ECREAD  = 0x9C40A488
    IOCTL_GPD_ACPI_ECWRITE = 0x9C40A48C
之前的扫描按「device=0x001」的假设筛，所以一个都没命中——不是没有，是找错了范围。

本工具只读文件，不打开设备、不发任何请求。做三件事：
  1. 在驱动镜像里找这些常量的 4 字节小端形式；
  2. 打印命中点前后的十六进制窗口；
  3. 在窗口里识别 x64 的 `cmp reg/mem, imm` 形式，把立即数（很可能就是
     InputBufferLength / OutputBufferLength 的期望值）列出来。
"""
import sys

IOCTLS = {
    0x9C406400: 'IOCTL_GPD_READ_PORT_UCHAR',
    0x9C406404: 'IOCTL_GPD_READ_PORT_USHORT',
    0x9C406408: 'IOCTL_GPD_READ_PORT_ULONG',
    0x9C40A440: 'IOCTL_GPD_WRITE_PORT_UCHAR',
    0x9C40A480: 'IOCTL_GPD_ACPI_CMREAD',
    0x9C40A484: 'IOCTL_GPD_ACPI_CMWRITE',
    0x9C40A488: 'IOCTL_GPD_ACPI_ECREAD',
    0x9C40A48C: 'IOCTL_GPD_ACPI_ECWRITE',
    0x9C40A490: 'IOCTL_GPD_ACPI_MMREADB',
    0x9C40A4A0: 'IOCTL_GPD_ACPI_PEREAD',
    0x9C40A4C0: 'IOCTL_GPD_ACPI_IOREAD',
    0x9C40A4D0: 'IOCTL_GPD_ACPI_TMPREAD1',
    0x9C40A500: 'IOCTL_GPD_ACPI_SMAPCTABLE',
}

MODRM_REGS = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']
WINDOW = 96


def modrm_extra(data, j):
    """modrm 字节之后还跟着多少 SIB/位移字节（不含立即数）。"""
    if j >= len(data):
        return 0
    modrm = data[j]
    mod, rm = modrm >> 6, modrm & 7
    if mod == 3:
        return 0
    n = 0
    if rm == 4:                       # SIB
        n += 1
        if j + 1 < len(data) and mod == 0 and (data[j + 1] & 7) == 5:
            n += 4                      # disp32
    elif mod == 0 and rm == 5:          # RIP 相对
        n += 4
    if mod == 1:
        n += 1
    elif mod == 2:
        n += 4
    return n


def operand_text(data, j):
    """粗略还原 modrm 指向的操作数文本，够看懂就行。"""
    modrm = data[j]
    mod, rm = modrm >> 6, modrm & 7
    if mod == 3:
        return MODRM_REGS[rm]
    if rm == 4 and j + 1 < len(data):
        sib = data[j + 1]
        base = MODRM_REGS[sib & 7]
        idx = (sib >> 3) & 7
        scale = 1 << (sib >> 6)
        text = base if (sib & 7) != 5 or mod != 0 else ''
        if idx != 4:
            text = (text + '+' if text else '') + '%s*%d' % (MODRM_REGS[idx], scale)
        return '[%s+…]' % text
    return '[%s+…]' % MODRM_REGS[rm] if rm != 5 else '[rip+…]'


def decode_cmp(data, off, end):
    """在 [off,end) 里找 cmp/test 立即数形式，返回 [(位置, 描述)]。"""
    out = []
    i = off
    while i < end - 3:
        j = i
        rex = 0
        if 0x40 <= data[j] <= 0x4F:
            rex = data[j]
            j += 1
        if j >= end:
            break
        op = data[j]
        w = bool(rex & 0x08)
        pre = 'r' if w else 'e'
        if op in (0x83, 0x81) and ((data[j + 1] >> 3) & 7) == 7:
            extra = modrm_extra(data, j + 1)
            imm_off = j + 1 + extra
            size = 1 if op == 0x83 else 4
            if imm_off + size > end:
                i += 1
                continue
            imm = int.from_bytes(data[imm_off:imm_off + size], 'little', signed=True)
            out.append((i, 'cmp %s, %d (0x%X)' % (operand_text(data, j + 1)
                                                   if extra or (data[j + 1] >> 6) != 3
                                                   else pre + MODRM_REGS[data[j + 1] & 7],
                                                   imm, imm & 0xFFFFFFFF)))
            i = imm_off + size
            continue
        if op in (0xF6, 0xF7) and ((data[j + 1] >> 3) & 7) == 0:
            extra = modrm_extra(data, j + 1)
            imm_off = j + 1 + extra
            size = 1 if op == 0xF6 else 4
            if imm_off + size > end:
                i += 1
                continue
            imm = int.from_bytes(data[imm_off:imm_off + size], 'little')
            out.append((i, 'test %s, 0x%X' % (operand_text(data, j + 1), imm)))
            i = imm_off + size
            continue
        if op in (0x3C, 0x3D):
            size = 1 if op == 0x3C else 4
            if j + 1 + size > end:
                i += 1
                continue
            imm = int.from_bytes(data[j + 1:j + 1 + size], 'little')
            out.append((i, 'cmp %s, 0x%X (%d)' % ('al' if size == 1 else 'eax', imm, imm)))
            i = j + 1 + size
            continue
        i += 1
    return out


def hexdump(data, start, end):
    lines = []
    for base in range(start, end, 16):
        chunk = data[base:base + 16]
        if not chunk:
            break
        hexs = ' '.join('%02X' % b for b in chunk)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append('    %06X  %-47s  %s' % (base, hexs, asc))
    return lines


def scan(path):
    with open(path, 'rb') as f:
        data = f.read()
    print('=' * 78)
    print('%s（%d 字节，只读，不发请求）' % (path, len(data)))
    total = 0
    for code, name in sorted(IOCTLS.items()):
        needle = code.to_bytes(4, 'little')
        pos = 0
        hits = []
        while True:
            pos = data.find(needle, pos)
            if pos < 0:
                break
            hits.append(pos)
            pos += 1
        if not hits:
            continue
        total += len(hits)
        for h in hits:
            print('\n[命中] %s = 0x%08X  位于文件偏移 0x%X' % (name, code, h))
            lo = max(0, h - WINDOW)
            hi = min(len(data), h + WINDOW)
            for line in hexdump(data, lo, hi):
                print(line)
            cmps = decode_cmp(data, lo, hi)
            if cmps:
                print('    附近的 cmp 立即数（很可能就是缓冲长度校验）：')
                for off, text in cmps[:14]:
                    print('      0x%06X  %s' % (off, text))
    if not total:
        print('\n这个镜像里一个 IOCTL_GPD_* 常量都没有 → 它不是处理这些请求的驱动')
    return total


def dump_region(path, offset, length):
    """看某个处理函数附近的字节与 cmp 立即数（离线，只读）。"""
    with open(path, 'rb') as f:
        data = f.read()
    lo = max(0, offset - 16)
    hi = min(len(data), offset + length)
    print('%s 偏移 0x%X～0x%X' % (path, lo, hi))
    for line in hexdump(data, lo, hi):
        print(line)
    print('\ncmp/test 立即数：')
    for off, text in decode_cmp(data, lo, hi):
        print('  0x%06X  %s' % (off, text))
    return 0


def u16(b, o):
    return int.from_bytes(b[o:o + 2], 'little')


def u32(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def pe_sections(data):
    pe = u32(data, 0x3C)
    nsec = u16(data, pe + 6)
    opt_size = u16(data, pe + 20)
    off = pe + 24 + opt_size
    out = []
    for i in range(nsec):
        e = off + i * 40
        out.append({'name': data[e:e + 8].rstrip(b'\0').decode('ascii', 'replace'),
                    'vaddr': u32(data, e + 12), 'vsize': u32(data, e + 8),
                    'raw': u32(data, e + 20), 'rawsize': u32(data, e + 16)})
    ddir = pe + 24 + (112 if u16(data, pe + 24) == 0x20b else 96)
    dirs = [(u32(data, ddir + i * 8), u32(data, ddir + i * 8 + 4)) for i in range(16)]
    return out, dirs


def off_to_rva(secs, off):
    for s in secs:
        if s['raw'] and s['raw'] <= off < s['raw'] + s['rawsize']:
            return s['vaddr'] + (off - s['raw'])
    return None


def rva_to_off(secs, rva):
    for s in secs:
        if s['vaddr'] <= rva < s['vaddr'] + max(s['vsize'], s['rawsize']):
            return s['raw'] + (rva - s['vaddr'])
    return None


def decode_calls(data, lo, hi):
    """列出 call/jmp rel32 的目标（文件偏移），用于顺着调用链看下去。"""
    out = []
    i = lo
    while i < hi - 5:
        if data[i] in (0xE8, 0xE9):
            rel = int.from_bytes(data[i + 1:i + 5], 'little', signed=True)
            target = i + 5 + rel
            out.append((i, '%s → 0x%X' % ('call' if data[i] == 0xE8 else 'jmp ', target)))
            i += 5
            continue
        i += 1
    return out


def dump_function(path, offset):
    """用 .pdata 找到包含该偏移的函数，整段打出来（离线，只读）。"""
    with open(path, 'rb') as f:
        data = f.read()
    secs, dirs = pe_sections(data)
    rva = off_to_rva(secs, offset)
    if rva is None:
        print('偏移 0x%X 不在任何节里' % offset)
        return 1
    pdata_rva, pdata_size = dirs[3]
    pdata_off = rva_to_off(secs, pdata_rva)
    print('%s：偏移 0x%X → RVA 0x%X；.pdata 在文件 0x%X（%d 字节）' % (
        path, offset, rva, pdata_off, pdata_size))
    found = None
    for i in range(pdata_size // 12):
        e = pdata_off + i * 12
        begin, end, unwind = u32(data, e), u32(data, e + 4), u32(data, e + 8)
        if begin <= rva < end:
            found = (begin, end, unwind)
            break
    if not found:
        print('没找到包含该 RVA 的 RUNTIME_FUNCTION')
        return 1
    begin, end, _unwind = found
    foff = rva_to_off(secs, begin)
    fend = foff + (end - begin)
    print('函数范围：RVA 0x%X～0x%X（%d 字节）→ 文件偏移 0x%X～0x%X' % (
        begin, end, end - begin, foff, fend))
    print('\n十六进制：')
    for line in hexdump(data, foff, fend):
        print(line)
    print('\ncmp/test 立即数（缓冲长度校验通常在这里）：')
    for off, text in decode_cmp(data, foff, fend):
        print('  0x%06X  %s' % (off, text))
    print('\ncall/jmp 目标：')
    for off, text in decode_calls(data, foff, fend):
        print('  0x%06X  %s' % (off, text))
    return 0


def main(argv):
    if len(argv) >= 4 and argv[0] == '--region':
        return dump_region(argv[1], int(argv[2], 0), int(argv[3], 0))
    if len(argv) >= 3 and argv[0] == '--func':
        return dump_function(argv[1], int(argv[2], 0))
    paths = argv or [
        r'C:\Windows\System32\drivers\UWACPIDriver.sys',
        r'C:\Program Files\OEM\CreatorCenter\ACPIDriver\ACPIDriver.sys',
    ]
    for p in paths:
        try:
            scan(p)
        except OSError as exc:
            print('%s 读取失败：%s' % (p, exc))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
