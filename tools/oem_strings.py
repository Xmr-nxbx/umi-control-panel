# -*- coding: utf-8 -*-
r"""离线字符串取证：在 OEM 二进制里找设备路径、EC 方法名、MQTT 主题等。

只读文件，不加载、不执行、不发任何请求。用法：
    runtime\python.exe tools/oem_strings.py <文件1> [文件2 ...]
"""
import re
import sys

MIN_LEN = 5
PATTERNS = {
    # 任意 \\.\XXX 设备路径，以及带 ACPIDriver / ACPI# 的串
    'device': re.compile(r'[\\./A-Za-z0-9_#{}-]*(?:\\\\\.\\[A-Za-z0-9_]{2,32}|ACPIDriver|ACPI#)[\\./A-Za-z0-9_#{}-]*'),
    'ec_method': re.compile(r'\b(?:ECRR|ECRW|SMRW|ECRD|ECWR|ReadEC|WriteEC|TempRead\d|TempWrite\d|SMAPCTable)\b'),
    'ioctl': re.compile(r'\bIOCTL[_A-Z0-9]{3,40}\b'),
    'mqtt': re.compile(r'[/A-Za-z0-9_-]*(?:MQTT|mqtt|/gcu|/uw/|topic)[/A-Za-z0-9_-]*'),
    'fan_mode': re.compile(r'[A-Za-z0-9_ ]*(?:Fan|FAN|fan)[A-Za-z0-9_ ]*'),
}


def strings_of(data):
    """返回 [(offset, text)]，同时覆盖 ASCII 与 UTF-16LE。"""
    out = []
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % MIN_LEN, data):
        out.append((m.start(), m.group(0).decode('ascii')))
    for m in re.finditer(rb'(?:[\x20-\x7e]\x00){3,}', data):
        out.append((m.start(), m.group(0).decode('utf-16-le')))
    return out


def pe_kind(data):
    if data[:2] != b'MZ':
        return '非 PE'
    pe = int.from_bytes(data[0x3C:0x40], 'little')
    if data[pe:pe + 4] != b'PE\0\0':
        return '非 PE'
    magic = int.from_bytes(data[pe + 24:pe + 26], 'little')
    ddir = pe + 24 + (112 if magic == 0x20b else 96)
    dotnet = int.from_bytes(data[ddir + 14 * 8:ddir + 14 * 8 + 4], 'little') != 0
    arch = 'x64' if magic == 0x20b else 'x86'
    if dotnet:
        return '.NET 托管程序集（%s，可反编译出 P/Invoke 签名）' % arch
    return '原生 PE（%s）' % arch


def scan(path, limit_per_kind=40):
    with open(path, 'rb') as f:
        data = f.read()
    print('=' * 78)
    print('%s（%d 字节）  类型：%s' % (path, len(data), pe_kind(data)))
    strings = strings_of(data)
    for kind, pat in PATTERNS.items():
        seen, hits = set(), []
        for off, s in strings:
            for m in pat.finditer(s):
                text = m.group(0).strip()
                if len(text) < 4 or text in seen:
                    continue
                seen.add(text)
                hits.append((off, text))
        if not hits:
            continue
        print('\n[%s] %d 个不同命中：' % (kind, len(hits)))
        for off, text in hits[:limit_per_kind]:
            print('   0x%-9X %s' % (off, text[:110]))
        if len(hits) > limit_per_kind:
            print('   …另有 %d 条未列出' % (len(hits) - limit_per_kind))


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    for p in argv:
        try:
            scan(p)
        except OSError as exc:
            print('%s 读取失败：%s' % (p, exc))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
