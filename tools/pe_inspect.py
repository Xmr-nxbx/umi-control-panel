# -*- coding: utf-8 -*-
"""离线 PE 体检：只读磁盘文件，不加载、不执行、不碰任何设备。

用途：搞清楚 OEM 用户态库（ACPIDriverDll.dll / GCUService.exe）到底提供了什么。
输出：基本信息、导入表、导出表、.NET 判定、按关键字过滤的字符串。

安全红线（见 README 第 6 节）：本工具不发任何 IOCTL，也不做穷举扫描。
"""
import re
import sys

MACHINE = {0x14c: 'x86', 0x8664: 'x64', 0xAA64: 'ARM64'}
SUBSYSTEM = {2: 'GUI', 3: 'Console', 1: 'Native'}


def u16(b, o):
    return int.from_bytes(b[o:o + 2], 'little')


def u32(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def u64(b, o):
    return int.from_bytes(b[o:o + 8], 'little')


def sections(data, pe_off, nsec, opt_size):
    out = []
    off = pe_off + 24 + opt_size
    for i in range(nsec):
        e = off + i * 40
        name = data[e:e + 8].rstrip(b'\0').decode('ascii', 'replace')
        out.append({'name': name, 'vsize': u32(data, e + 8), 'vaddr': u32(data, e + 12),
                    'rawsize': u32(data, e + 16), 'raw': u32(data, e + 20),
                    'chars': u32(data, e + 36)})
    return out


def rva_to_off(secs, rva):
    for s in secs:
        if s['vaddr'] <= rva < s['vaddr'] + max(s['vsize'], s['rawsize']):
            return s['raw'] + (rva - s['vaddr'])
    return None


def cstr(data, off, limit=200):
    end = data.find(b'\0', off, off + limit)
    if end < 0:
        end = min(len(data), off + limit)
    return data[off:end].decode('ascii', 'replace')


def read_exports(data, secs, dir_entry):
    rva, size = dir_entry
    if not rva:
        return []
    off = rva_to_off(secs, rva)
    if off is None:
        return []
    n_names = u32(data, off + 24)
    names_rva = u32(data, off + 32)
    noff = rva_to_off(secs, names_rva)
    if noff is None:
        return []
    out = []
    for i in range(min(n_names, 4000)):
        p = u32(data, noff + i * 4)
        so = rva_to_off(secs, p)
        if so is None:
            continue
        out.append(cstr(data, so))
    return out


def read_imports(data, secs, dir_entry):
    rva, _size = dir_entry
    if not rva:
        return {}
    off = rva_to_off(secs, rva)
    if off is None:
        return {}
    out = {}
    for i in range(200):
        e = off + i * 20
        name_rva = u32(data, e + 12)
        thunk_rva = u32(data, e + 16) or u32(data, e)
        if not name_rva:
            break
        no = rva_to_off(secs, name_rva)
        if no is None:
            break
        dll = cstr(data, no)
        funcs = []
        to = rva_to_off(secs, thunk_rva)
        if to is not None:
            for j in range(2000):
                val = u64(data, to + j * 8) if u16(data, 4) == 0x8664 else u32(data, to + j * 4)
                if val == 0:
                    break
                width = 8 if u16(data, 4) == 0x8664 else 4
                top = 1 << (width * 8 - 1)
                if val & top:
                    funcs.append('#%d' % (val & 0xFFFF))
                    continue
                fo = rva_to_off(secs, val & 0x7FFFFFFF)
                if fo is None:
                    continue
                funcs.append(cstr(data, fo + 2))
        out[dll] = funcs
    return out


STRINGS_KEYWORDS = re.compile(
    r'(acpi|ecrr|ecrw|smrw|ec[a-z]{2,6}|deviceiocontrol|\\\\\.\\|ioctl|fan|temp|'
    r'pl1|pl2|turbo|office|balance|gaming|mode|battery|charge|wmi|0x[0-9a-f]{4,8})', re.I)


def strings_hit(data, minlen=4, limit=4000):
    found = []
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % minlen, data):
        s = m.group(0).decode('ascii')
        if STRINGS_KEYWORDS.search(s):
            found.append((m.start(), s))
            if len(found) >= limit:
                break
    return found


def utf16_strings(data, limit=400):
    found = []
    for m in re.finditer(rb'(?:[\x20-\x7e]\x00){4,}', data):
        s = m.group(0).decode('utf-16-le')
        if STRINGS_KEYWORDS.search(s):
            found.append((m.start(), s))
            if len(found) >= limit:
                break
    return found


def main(path):
    with open(path, 'rb') as f:
        data = f.read()
    print('文件：%s（%d 字节，只读分析，未加载执行）' % (path, len(data)))
    if data[:2] != b'MZ':
        print('不是 PE 文件')
        return 1
    pe_off = u32(data, 0x3C)
    if data[pe_off:pe_off + 4] != b'PE\0\0':
        print('PE 头签名不对')
        return 1
    machine = u16(data, pe_off + 4)
    nsec = u16(data, pe_off + 6)
    opt_size = u16(data, pe_off + 20)
    opt = pe_off + 24
    magic = u16(data, opt)
    subsystem = u16(data, opt + (68 if magic == 0x20b else 68))
    is_pe32plus = magic == 0x20b
    ddir_off = opt + (112 if is_pe32plus else 96)
    dirs = [(u32(data, ddir_off + i * 8), u32(data, ddir_off + i * 8 + 4)) for i in range(16)]
    secs = sections(data, pe_off, nsec, opt_size)
    print('架构=%s 节数=%d 子系统=%s PE32+=%s' % (
        MACHINE.get(machine, hex(machine)), nsec, SUBSYSTEM.get(subsystem, subsystem),
        is_pe32plus))
    print('.NET 元数据目录=%s（有值说明是托管程序集，可用 .NET 反编译）' % (
        'RVA=0x%X size=%d' % dirs[14] if dirs[14][0] else '无 → 原生代码'))
    print('\n节表：')
    for s in secs:
        flags = []
        if s['chars'] & 0x20000000:
            flags.append('可执行')
        if s['chars'] & 0x40000000:
            flags.append('可读')
        if s['chars'] & 0x80000000:
            flags.append('可写')
        print('  %-9s vaddr=0x%-8X vsize=%-9d raw=0x%-8X rawsize=%-9d %s' % (
            s['name'], s['vaddr'], s['vsize'], s['raw'], s['rawsize'], '/'.join(flags)))

    exports = read_exports(data, secs, dirs[0])
    print('\n导出函数（%d 个）：' % len(exports))
    for e in exports:
        print('   ', e)

    imports = read_imports(data, secs, dirs[1])
    print('\n导入表（%d 个 DLL）：' % len(imports))
    for dll, funcs in imports.items():
        interesting = [f for f in funcs if re.search(
            r'(DeviceIoControl|CreateFile|ControlService|OpenService|WMI|Ioctl)', f, re.I)]
        print('  %-28s %d 个函数%s' % (dll, len(funcs),
                                       ('  ★ ' + ','.join(interesting)) if interesting else ''))

    print('\nASCII 字符串命中（关键字过滤，最多 200 条）：')
    for off, s in strings_hit(data, limit=200):
        print('  0x%-9X %s' % (off, s[:150]))
    print('\nUTF-16 字符串命中（最多 120 条）：')
    for off, s in utf16_strings(data, limit=120):
        print('  0x%-9X %s' % (off, s[:150]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ''))
