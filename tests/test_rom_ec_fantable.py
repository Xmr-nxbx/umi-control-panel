# -*- coding: utf-8 -*-
"""`tools/rom_ec_fantable.py` 的用例：全部用合成镜像，不需要真 ROM。

跑法（本仓库没有 pytest）：
    runtime\\python.exe tests\\test_rom_ec_fantable.py
"""
import io
import json
import os
import struct
import sys
import tempfile
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import rom_ec_fantable as T   # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print('%s %s%s' % ('  ok  ' if cond else ' FAIL ', name, ('  ← ' + extra) if extra and not cond else ''))


def rec(upt, dnt, duty):
    """拼一条 48 字节记录。upt/dnt/duty 各给启用的点，其余补 0xFF。"""
    def pad(vals, n=16):
        b = bytearray([0xFF] * n)
        for i, v in enumerate(vals):
            b[i] = v
        return bytes(b)
    # DownT 的第 0 字节是那个从不读的字节，真表里恒为 0x00
    d = bytearray([0x00] * 16)
    for i, v in enumerate(dnt):
        d[i + 1] = v
    for i in range(len(dnt) + 1, 16):
        d[i] = 0xFF
    return pad(upt) + bytes(d) + pad(duty)


def ec_image(tables, phase_off=0x5852, size=T.EC_SIZE, with_mark=True):
    """造一份 256 KiB 的假 EC 镜像，把表按 48 字节等距摆好。"""
    img = bytearray(b'\x00' * size)
    if with_mark:
        img[0x50:0x50 + 12] = b'ITE EC-V14.6'
    off = phase_off
    for t in tables:
        img[off:off + 48] = t
        off += 48
    return bytes(img)


CPU_UPT = [53, 57, 59, 61, 63, 67, 69, 71]
CPU_DNT = [48, 50, 60, 62, 64, 68, 70, 72]
CPU_DUTY = [0, 60, 60, 70, 90, 96, 100, 110, 110, 110, 110, 110, 110, 110, 110, 110]
GPU_UPT = [52, 53, 56, 58, 60, 63, 65, 67]
GPU_DNT = [48, 50, 57, 59, 61, 64, 66, 68]


def fv_with_freeform(body):
    """造一个固件卷，里面放一个 FREEFORM FFS，段体就是 body。"""
    sect = struct.pack('<I', len(body) + 4)[:3] + bytes([0x19]) + body   # RAW 段
    ffs_size = 24 + len(sect)
    ffs = (bytes(range(16))                                    # Name GUID
           + b'\x00\x00'                                       # IntegrityCheck
           + bytes([T.FFS_FREEFORM])                           # Type
           + b'\x00'                                           # Attributes
           + struct.pack('<I', ffs_size)[:3]                   # Size
           + b'\xF8')                                          # State
    payload = ffs + sect
    fvlen = 72 + ((len(payload) + 7) & ~7)
    head = bytearray(72)
    head[16:32] = bytes(range(16))
    struct.pack_into('<Q', head, 32, fvlen)
    head[40:44] = b'_FVH'
    struct.pack_into('<I', head, 44, 0x400)
    struct.pack_into('<H', head, 48, 72)
    struct.pack_into('<H', head, 50, 0)
    struct.pack_into('<H', head, 52, 0)
    head[54] = 0
    head[55] = 2
    struct.pack_into('<I', head, 56, 1)
    struct.pack_into('<I', head, 60, fvlen - 72)
    struct.pack_into('<II', head, 64, 0, 0)
    return bytes(head) + payload


def test_plausible():
    good = rec(CPU_UPT, CPU_DNT, CPU_DUTY)
    check('正常表判为合理', T.plausible(good[0:16], good[16:32], good[32:48]))

    bad_up = rec([53, 57, 56, 61], CPU_DNT[:4], CPU_DUTY)
    check('升温阈值不递增 → 拒', not T.plausible(bad_up[0:16], bad_up[16:32], bad_up[32:48]))

    bad_duty = rec(CPU_UPT, CPU_DNT, [0, 250] + CPU_DUTY[2:])
    check('占空比 >200 → 拒', not T.plausible(bad_duty[0:16], bad_duty[16:32], bad_duty[32:48]))

    dec = rec(CPU_UPT, CPU_DNT, [0, 100, 90] + CPU_DUTY[3:])
    check('占空比下降 → 拒', not T.plausible(dec[0:16], dec[16:32], dec[32:48]))

    hot = rec([53, 57, 150, 160, 170], CPU_DNT[:5], CPU_DUTY)
    check('阈值超 100 → 拒', not T.plausible(hot[0:16], hot[16:32], hot[32:48]))

    short = rec([53, 57, 59], CPU_DNT[:3], CPU_DUTY)
    check('点太少（<4）→ 拒', not T.plausible(short[0:16], short[16:32], short[32:48]))


def test_scan_and_phase():
    t1 = rec(CPU_UPT, CPU_DNT, CPU_DUTY)
    t2 = rec(GPU_UPT, GPU_DNT, CPU_DUTY)
    t3 = rec([54, 58, 62, 66, 69, 72, 75, 78], [48, 50, 61, 65, 68, 71, 74, 77], CPU_DUTY)
    img = ec_image([t1, t2, t3])
    found = T.scan_tables(img)
    check('等距三套表全部找到', len(found) == 3, '实得 %d' % len(found))
    check('偏移就是摆进去的偏移', [f[0] for f in found] == [0x5852, 0x5882, 0x58B2],
          str([hex(f[0]) for f in found]))

    # 去重：同一套表摆两遍只算一套
    img2 = ec_image([t1, t1, t2])
    check('重复表去重', len(T.dedupe(T.scan_tables(img2))) == 2)

    # 相位过滤：3 条同相位 + 1 条错相位，错相位那条要被判掉
    img3 = bytearray(ec_image([t1, t2, t3]))
    stray = 0x5A01                                     # 0x5A01 % 48 != 0x5852 % 48
    img3[stray:stray + 48] = rec([40, 45, 50, 55, 60, 65, 70, 75],
                                 [38, 43, 48, 53, 58, 63, 68, 73], CPU_DUTY)
    found3 = T.scan_tables(bytes(img3))
    check('错相位的巧合命中被相位过滤掉',
          all(f[0] % 48 == 0x5852 % 48 for f in found3) and len(found3) == 3,
          str([(hex(f[0]), f[0] % 48) for f in found3]))


def test_raw_and_ffs():
    t1 = rec(CPU_UPT, CPU_DNT, CPU_DUTY)
    img = ec_image([t1])
    got = T.collect_ec_images(img, is_raw=True)
    check('--raw 直接当一份镜像', len(got) == 1 and got[0][2] == img)

    no_mark = ec_image([t1], with_mark=False)
    check('没有 ITE EC-V 标识就不认', T.collect_ec_images(no_mark, is_raw=False) == [])

    # 整份 ROM：头部一份 + 固件卷里一个 FREEFORM
    rom = bytearray(b'\xFF' * (0x40000 + 0x100000))
    rom[0:0x40000] = img
    fv = fv_with_freeform(img)
    rom[0x40000:0x40000 + len(fv)] = fv
    got2 = T.collect_ec_images(bytes(rom), is_raw=False)
    labels = [g[0] for g in got2]
    check('ROM 头部那份认出来了', 'ROM头部' in labels, str(labels))
    check('固件卷里的 FREEFORM 认出来了',
          any(l.startswith('FREEFORM@') for l in labels), str(labels))
    check('认出来的都是 256 KiB', all(len(g[2]) == T.EC_SIZE for g in got2))


def test_live_and_compare():
    live = {
        'ts': 'x', 'reads': 96, 'writes': 0,
        'CPU': {'raw': {'0x%04X' % (0x0F00 + i): v for i, v in enumerate(
            [53, 57, 59, 61, 63, 67, 69, 71] + [255] * 8)}},
        'GPU': {'raw': {}},
    }
    live['CPU']['raw'].update({'0x%04X' % (0x0F11 + i): v
                               for i, v in enumerate(CPU_DNT + [255] * 7)})
    live['CPU']['raw'].update({'0x%04X' % (0x0F20 + i): v
                               for i, v in enumerate([0, 132] + [198] * 14)})
    fd, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(live, f)
    try:
        loaded = T.load_live(path)
    finally:
        os.unlink(path)
    check('活表 JSON 解出 CPU', 'CPU' in loaded and 'GPU' not in loaded, str(list(loaded)))
    lu, ld, lw = loaded['CPU']
    check('活表升温阈值对得上', T.nums(lu) == CPU_UPT, str(T.nums(lu)))
    check('活表降温阈值对得上（跳过从不读的那个字节）', T.nums(ld[1:]) == CPU_DNT, str(T.nums(ld[1:])))
    check('活表占空比是原始字节', T.nums(lw)[1:] == [132] + [198] * 14, str(T.nums(lw)))

    tables = T.scan_tables(ec_image([rec(CPU_UPT, CPU_DNT, CPU_DUTY)]))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        T.compare(loaded, tables)
    out = buf.getvalue()
    check('温度点同、占空比不同 → 明确报出来',
          '温度点完全一致、占空比不一致' in out, out[-200:])

    same = T.scan_tables(ec_image([rec(CPU_UPT, CPU_DNT, [0, 132] + [198] * 14)]))
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        T.compare(loaded, same)
    check('占空比也相同时不喊那句', '占空比不一致' not in buf2.getvalue())

    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        T.compare(loaded, T.scan_tables(ec_image([rec(GPU_UPT, GPU_DNT, CPU_DUTY)])))
    check('温度点对不上就如实说没有', '没有' in buf3.getvalue(), buf3.getvalue()[-200:])


def test_formatting():
    check('pct 把 0xFF 显示成空', T.pct(bytes([0, 110, 255])) == [0.0, 55.0, None])
    check('fmt_row 整数不带小数点', T.fmt_row([0, 55]) == '0,55')
    check('fmt_row 半整数保留', T.fmt_row([32.5]) == '32.5')
    check('fmt_row None 显示破折号', T.fmt_row([1, None]) == '1,—')


def main():
    for fn in (test_plausible, test_scan_and_phase, test_raw_and_ffs,
               test_live_and_compare, test_formatting):
        print('\n[%s]' % fn.__name__)
        fn()
    print('\n通过 %d / 失败 %d' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  FAIL %s' % f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
