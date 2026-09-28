r"""离线静态分析：从内核驱动镜像里挑出可能的 IOCTL 码。

为什么要这么干：`\\.\ACPIDriver`（OEM 的 UWACPIDriver）以普通用户身份就能读写打开，
但本机没有 `\\.\ACPI` 设备名，所以 EC 只能走这个驱动。缺的就是它的 IOCTL 码。
上一轮有人对同一驱动暴力发送 7000+ 组 IOCTL 直接把机器干蓝屏了，
所以本脚本**只读磁盘上的驱动镜像文件，不打开设备、不发送任何请求**。

原理：KMDF 的 EvtIoDeviceControl 回调签名是
    (WDFQUEUE, WDFREQUEST, size_t OutLen, size_t InLen, ULONG IoControlCode)
x64 下第 5 个参数落在 [rsp+0x28]，所以分发逻辑里一定出现
    mov  eax, [rsp+0x28]   ; 8B 44 24 28
    cmp  eax, imm32        ; 3D ib... 或 81 F8 ib... / 39 C8...
把 imm32 符合 IOCTL 编码规律（设备类型/访问位/功能号/传输方式）的常量抓出来即可。

用法：
    runtime\\python.exe tools\\driver_ioctl_scan.py [驱动路径]
默认分析 C:\\Windows\\System32\\drivers\\UWACPIDriver.sys
"""
import os
import struct
import sys

DEFAULT_DRIVER = r'C:\Windows\System32\drivers\UWACPIDriver.sys'

# 常见设备类型：0x22 FILE_DEVICE_UNKNOWN、0x32 FILE_DEVICE_ACPI、0x8000 以上为厂商自定义
VALID_DEVICE_TYPES = {0x22, 0x32, 0x37, 0x14, 0x1}
METHOD_NAMES = {0: 'BUFFERED', 1: 'IN_DIRECT', 2: 'OUT_DIRECT', 3: 'NEITHER'}
ACCESS_NAMES = {0: 'ANY', 1: 'READ', 2: 'WRITE', 3: 'READ|WRITE'}

# mov eax, [rsp+0x28]
MOV_RSP28 = b'\x8b\x44\x24\x28'
# cmp reg, imm32 常见形态
CMP_EAX_IMM = b'\x3d'           # cmp eax, imm32
CMP_MOD_IMM = b'\x81'           # cmp r/m32, imm32（/7 = 与 eax 等）


def safe_name(raw):
    """驱动段名经常带前导 \0 或非可打印字节，直接打印会把控制台编码打崩。"""
    text = raw.split(b'\0')[0] if b'\0' in raw else raw
    clean = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw[:8])
    return clean


def parse_pe(data):
    """只解析定位代码段所需的字段，不做完整 PE 加载。"""
    if data[:2] != b'MZ':
        raise ValueError('不是 PE 文件（缺 MZ）')
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b'PE\0\0':
        raise ValueError('不是 PE 文件（缺 PE 头）')
    machine, nsec = struct.unpack_from('<HH', data, pe_off + 4)
    # IMAGE_FILE_HEADER: +0 Machine, +2 NumberOfSections, +4 TimeDateStamp,
    # +8 PointerToSymbolTable, +12 NumberOfSymbols, +16 SizeOfOptionalHeader
    # 相对 "PE\0\0" 签名就是 pe_off+4+16 = pe_off+20（读错成 NumberOfSymbols 会把段表整体错位）
    opt_size = struct.unpack_from('<H', data, pe_off + 20)[0]
    magic = struct.unpack_from('<H', data, pe_off + 24)[0]
    if magic != 0x20b:
        raise ValueError('只支持 PE32+（x64），实际 magic=0x%X' % magic)
    sec_off = pe_off + 24 + opt_size
    sections = []
    for i in range(nsec):
        base = sec_off + i * 40
        name = data[base:base + 8]
        vsize, vaddr, rawsize, rawptr = struct.unpack_from('<IIII', data, base + 8)
        chars = struct.unpack_from('<I', data, base + 36)[0]
        sections.append({'name': safe_name(name), 'name_raw': name.hex(' '),
                         'vsize': vsize, 'vaddr': vaddr, 'rawsize': rawsize,
                         'rawptr': rawptr, 'chars': chars})
    return {'machine': machine, 'nsec': nsec, 'opt_size': opt_size,
            'pe_off': pe_off, 'file_size': len(data), 'sections': sections}


def looks_like_ioctl(code):
    """IOCTL = (设备类型<<16) | (访问位<<14) | (功能号<<2) | 传输方式。

    设备类型占 bit16-31（16 位，不是 10 位）；只认驱动常用的
    0x22 FILE_DEVICE_UNKNOWN / 0x32 FILE_DEVICE_ACPI 与厂商自定义段（>=0x8000），
    否则普通指令里的 4 字节片段会被误判成一堆命中。
    """
    method = code & 0x3
    access = (code >> 14) & 0x3
    devtype = (code >> 16) & 0xFFFF
    func = (code >> 2) & 0xFFF
    if not (devtype in VALID_DEVICE_TYPES or devtype >= 0x8000):
        return None
    if method not in METHOD_NAMES:
        return None
    # 高 6 位保留必须为 0，否则不是合法 IOCTL 编码
    if code & 0xFFFF0000 and devtype < 0x8000 and devtype > 0xFF:
        return None
    return {'code': code, 'devtype': devtype, 'access': access, 'func': func,
            'method': METHOD_NAMES[method], 'access_name': ACCESS_NAMES[access]}


# cmp 形态：opcode 后紧跟 imm32，这才是"与 IoControlCode 比较"的样子
CMP_FORMS = (
    (b'\x3d', 'eax', 0),                       # cmp eax, imm32
    (b'\x81\xf8', 'eax', 0),                   # cmp eax, imm32
    (b'\x81\xf9', 'ecx', 0),                   # cmp ecx, imm32
    (b'\x81\xfa', 'edx', 0),                   # cmp edx, imm32
    (b'\x81\xfb', 'ebx', 0),                   # cmp ebx, imm32
    (b'\x81\xfe', 'esi', 0),                   # cmp esi, imm32
    (b'\x81\xff', 'edi', 0),                   # cmp edi, imm32
    (b'\x3b', 'r,r', 0),                       # cmp reg, r/m32（后面才是地址）
)


def harvest_offsets(section):
    """扫描代码段：只把「紧跟 cmp 立即数形式」且符合 IOCTL 编码的常量当候选。"""
    out = {}
    for off in range(0, len(section) - 5):
        for form, reg, _ in CMP_FORMS:
            if form == b'\x3b':
                continue
            width = len(form)
            if section[off:off + width] != form:
                continue
            code = struct.unpack_from('<I', section, off + width)[0]
            hit = looks_like_ioctl(code)
            if hit and code not in out:
                hit['at'] = off + width
                hit['form'] = 'cmp %s, imm32' % reg
                out[code] = hit
    # 顺带收集 .text 里出现的合法编码常量（可能是查表/内联 CTL_CODE），单独标注可信度低
    loose = {}
    for off in range(0, len(section) - 4):
        code = struct.unpack_from('<I', section, off)[0]
        hit = looks_like_ioctl(code)
        if hit and code not in out and code not in loose:
            hit['at'] = off
            hit['form'] = 'bare immediate'
            loose[code] = hit
    out.update(loose)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DRIVER
    lines = []

    def say(text):
        lines.append(text)

    if not os.path.exists(path):
        print('driver not found: %s' % path)
        return 1
    data = open(path, 'rb').read()
    say('分析目标：%s（%d 字节）' % (path, len(data)))
    say('模式：只读磁盘镜像，不打开设备、不发送任何 IOCTL')
    try:
        info = parse_pe(data)
    except ValueError as exc:
        say('解析失败：%s' % exc)
        return _finish(lines)
    say('')
    say('PE：machine=0x%X sections=%d optionalHeader=%d pe_off=0x%X' % (
        info['machine'], info['nsec'], info['opt_size'], info['pe_off']))
    say('节表（chars 低位含 0x20=CNT_CODE 即代码段）：')
    code_sections = []
    for sec in info['sections']:
        cnt_code = bool(sec['chars'] & 0x20)
        mem_exec = bool(sec['chars'] & 0x20000000)
        beyond = sec['rawptr'] + sec['rawsize'] > info['file_size']
        is_code = cnt_code or sec['name'] in ('.text', 'PAGE', 'text')
        say('  %-8s (%s) vaddr=0x%X vsize=%d raw=0x%X rawsize=%d chars=0x%08X'
            ' 代码=%s 执行=%s %s' % (
                sec['name'], sec['name_raw'], sec['vaddr'], sec['vsize'], sec['rawptr'],
                sec['rawsize'], sec['chars'], is_code, mem_exec,
                '[超出文件长度，跳过]' if beyond else ''))
        if is_code and not beyond and sec['rawsize']:
            code_sections.append(data[sec['rawptr']:sec['rawptr'] + sec['rawsize']])
    if not code_sections:
        say('')
        say('没识别到代码段——把上面节表原样发我，我按 raw 偏移再定位。')
        return _finish(lines)

    blob = b''.join(code_sections)
    say('')
    say('代码段合计 %d 字节' % len(blob))
    hits = harvest_offsets(blob)
    anchor_codes = set()
    for pos in range(len(blob) - 44):
        if blob[pos:pos + 4] == MOV_RSP28:
            for back in range(4, 40):
                code = struct.unpack_from('<I', blob, pos + back)[0]
                hit = looks_like_ioctl(code)
                if hit:
                    anchor_codes.add(code)
    say('')
    say('=== 代码段里符合 IOCTL 编码规律的常量（共 %d 个，按可信度排序）===' % len(hits))
    ranked = sorted(hits.values(),
                    key=lambda h: (0 if h['code'] in anchor_codes else 1,
                                   h['devtype'], h['code']))
    for hit in ranked[:40]:
        star = '*' if hit['code'] in anchor_codes else ' '
        say('  %s 0x%08X  device=0x%04X access=%-10s func=0x%03X method=%-10s %-20s (偏移 0x%X)'
            % (star, hit['code'], hit['devtype'], hit['access_name'], hit['func'],
               hit['method'], hit.get('form', ''), hit['at']))
    say('')
    say('带 * = 常量出现在 mov eax,[rsp+0x28]（读 IoControlCode）之后；cmp 形态优先于裸常量。')
    say('下一步：只挑一个带 * 的码，用完整合法的读请求做单次验证；失败即停手，绝不穷举。')
    return _finish(lines)


def _finish(lines):
    text = '\n'.join(lines)
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'tools', 'out')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'ioctl-scan.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                     # noqa: BLE001
        pass
    print(text)
    print('\n[report] %s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
