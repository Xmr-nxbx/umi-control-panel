# -*- coding: utf-8 -*-
"""只读：从 UEFI ROM（或裸 EC 镜像）里把 EC 自带的**默认风扇表**全部解出来。

用法:
    runtime\\python.exe tools\\rom_ec_fantable.py <ROM或EC镜像> [--live tools\\out\\fantable-live.json]
    runtime\\python.exe tools\\rom_ec_fantable.py <EC镜像> --raw

活表那份 JSON 由 tools\\ec_fantable_dump.py 产出（那个只发 ECREAD）。
两个脚本一个读机器、一个读固件，都不写任何东西。

纯离线，只 open 磁盘文件：不刷写、不碰 EC、不发 IOCTL、不调用任何驱动。

它做的事：
  1. 认 EC 镜像——判据是镜像里有 `ITE EC-V` 标识串（五份样本全部命中）；
     整份 ROM 的情况下，先走 FFS 链找出 256 KiB 的 FREEFORM 段，再看 ROM 头部
     0x0..0x40000 那段（它在任何固件卷之外，也是一份 EC 镜像）。
  2. 在镜像里按 48 字节步长扫记录。一条记录 = UpT[16] + DownT[16] + Duty[16]，
     与 EC 寄存器 `0x0F00`(CPU) / `0x0F30`(GPU) 的布局**逐字段对应**：
     UpT[0] 是点 1 的升温阈值（点 0 不存），DownT[0] 是那个从不读的字节、
     DownT[1..15] 才是点 0..14 的降温阈值，Duty 存的是**百分比 × 2**。
  3. 判据（少一条就会把错位数据当成表）：升温阈值严格递增且都在 20..100，
     降温阈值严格递增且都在 15..100，占空比非降且 ≤200。
     再把命中按 `偏移 % 48` 分组，只留**主相位**那一组——真表是定长数组，
     相位必然一致；散落的巧合命中相位是乱的。
  4. `--live` 时拿面板转储的活表逐套比对：温度点对上了就报"点同、占空比差多少"。
"""
import argparse
import json
import os
import re
import struct
import sys

REC = 48                      # UpT 16 + DownT 16 + Duty 16
EC_MARK = b'ITE EC-V'
FFS_FREEFORM = 0x02
EC_SIZE = 256 * 1024


def ffs_blobs(data, fv_start, fv_len):
    """走一个固件卷的 FFS 链，yield (绝对偏移, 段体) —— 只要 FREEFORM 的 256 KiB 段。"""
    fv = data[fv_start:fv_start + fv_len]
    hdrlen = struct.unpack('<H', fv[48:50])[0]
    off = hdrlen
    n = len(fv)
    while off + 24 <= n:
        raw = fv[off:off + 24]
        size = raw[20] | (raw[21] << 8) | (raw[22] << 16)
        if size == 0xFFFFFF or size < 24:
            off += 8
            continue
        if off + size > n:
            off += 8
            continue
        if raw[18] == FFS_FREEFORM and size - 28 == EC_SIZE:
            # 24 字节 FFS 头 + 4 字节 RAW 段头
            yield fv_start + off, fv[off + 28: off + size]
        off = (off + size + 7) & ~7


def find_volumes(data):
    """找 ROM 里的真固件卷（_FVH 且卷长合法、不越界、头长在 48..2048）。"""
    out = []
    off = 0
    n = len(data)
    while True:
        i = data.find(b'_FVH', off)
        if i < 0:
            break
        off = i + 1
        base = i - 40
        if base < 0:
            continue
        fvlen = struct.unpack('<Q', data[base + 32:base + 40])[0]
        hdrlen = struct.unpack('<H', data[base + 48:base + 50])[0]
        if fvlen and fvlen <= n and base + fvlen <= n and 48 <= hdrlen <= 2048:
            out.append((base, fvlen))
    return out


def collect_ec_images(data, is_raw):
    """返回 [(标签, 绝对偏移, 镜像字节)]。"""
    if is_raw:
        return [('镜像', 0, data)]
    out = []
    head = data[:EC_SIZE]
    if len(head) == EC_SIZE and EC_MARK in head:
        out.append(('ROM头部', 0, head))
    for base, fvlen in find_volumes(data):
        for off, blob in ffs_blobs(data, base, fvlen):
            if EC_MARK in blob:
                out.append(('FREEFORM@0x%X' % off, off, blob))
    return out


def plausible(upt, dnt, duty):
    u = [v for v in upt if v != 0xFF]
    d = [v for v in dnt[1:] if v != 0xFF]
    w = [v for v in duty if v != 0xFF]
    if not (4 <= len(u) <= 15) or not (4 <= len(d) <= 15) or len(w) < 8:
        return False
    if any(not (20 <= v <= 100) for v in u) or any(not (15 <= v <= 100) for v in d):
        return False
    if any(v > 200 for v in w):
        return False
    if any(u[i] >= u[i + 1] for i in range(len(u) - 1)):
        return False
    if any(d[i] >= d[i + 1] for i in range(len(d) - 1)):
        return False
    return not any(w[i] > w[i + 1] for i in range(len(w) - 1))


def scan_tables(img, lo=0x4000, hi=0xA000):
    """扫出所有合理的 48 字节表记录，只留主相位。返回 [(偏移, UpT, DownT, Duty)]。"""
    hits = []
    for off in range(lo, min(hi, len(img) - REC)):
        rec = (img[off:off + 16], img[off + 16:off + 32], img[off + 32:off + 48])
        if plausible(*rec):
            hits.append((off,) + rec)
    if not hits:
        return []
    phases = {}
    for h in hits:
        phases[h[0] % REC] = phases.get(h[0] % REC, 0) + 1
    main = max(phases, key=lambda p: phases[p])
    return [h for h in hits if h[0] % REC == main]


def dedupe(recs):
    seen = {}
    for r in recs:
        seen.setdefault((r[1], r[2], r[3]), r)
    return list(seen.values())


def nums(raw):
    """去掉 0xFF 填充，只留真正启用的点。"""
    return [v for v in raw if v != 0xFF]


def fmt_row(vals, ff='—'):
    out = []
    for v in vals:
        if v is None:
            out.append(ff)
        elif isinstance(v, float):
            out.append('%g' % v)
        else:
            out.append('%d' % v)
    return ','.join(out)


def pct(raw):
    return [None if v == 0xFF else v / 2.0 for v in raw]


def print_table(rec):
    off, upt, dnt, duty = rec
    print('  @0x%06X' % off)
    print('    升温阈值  %s' % fmt_row([None if v == 0xFF else v for v in upt]))
    print('    降温阈值  %s' % fmt_row([None if v == 0xFF else v for v in dnt[1:]] + [None]))
    print('    占空比%%   %s' % fmt_row(pct(duty)))


def load_live(path):
    """把面板的 fan-curve JSON 读成 {'CPU': (upt, dnt, duty), 'GPU': ...}，字节与 EC 同形。"""
    doc = json.load(open(path, encoding='utf-8'))
    out = {}
    for chip in ('CPU', 'GPU'):
        raw = doc.get(chip, {}).get('raw', {})
        if not raw:
            continue
        by = {int(k, 16): v for k, v in raw.items()}
        base = 0x0F00 if chip == 'CPU' else 0x0F30
        upt = bytes(by.get(base + i, 0xFF) for i in range(16))
        dnt = bytes(by.get(base + 0x10 + i, 0xFF) for i in range(16))
        duty = bytes(by.get(base + 0x20 + i, 0xFF) for i in range(16))
        out[chip] = (upt, dnt, duty)
    return out


def compare(live, tables):
    """活表和固件默认表逐套比：先看温度点，再看占空比。"""
    for chip, (lu, ld, lw) in live.items():
        lu_pts = nums(lu)
        ld_pts = nums(ld[1:])
        print('\n--- 活表 %s：升温 %s / 降温 %s / 占空比%% %s'
              % (chip, fmt_row(lu_pts), fmt_row(ld_pts), fmt_row(pct(lw))))
        best = None
        for rec in tables:
            same_u = nums(rec[1]) == lu_pts
            same_d = nums(rec[2][1:]) == ld_pts
            score = (2 if same_u else 0) + (1 if same_d else 0)
            if score and (best is None or score > best[0]):
                best = (score, rec, same_u, same_d)
        if best is None:
            print('    固件默认表里**没有**温度点相同的记录')
            continue
        score, rec, same_u, same_d = best
        print('    最接近的固件默认表 @0x%06X（升温%s / 降温%s）'
              % (rec[0], '同' if same_u else '不同', '同' if same_d else '不同'))
        print('      默认占空比%% %s' % fmt_row(pct(rec[3])))
        print('      活表占空比%% %s' % fmt_row(pct(lw)))
        if same_u and same_d and list(rec[3]) != list(lw):
            print('      → **温度点完全一致、占空比不一致**：本机现在跑的不是固件默认曲线')


def main():
    ap = argparse.ArgumentParser(description='只读解出 EC 镜像里的默认风扇表')
    ap.add_argument('image', help='UEFI ROM 或裸 EC 镜像')
    ap.add_argument('--raw', action='store_true', help='输入就是裸 EC 镜像，别去找固件卷')
    ap.add_argument('--live', help='面板转储的 fan-curve JSON，用来对照')
    ap.add_argument('--lo', type=lambda x: int(x, 0), default=0x4000)
    ap.add_argument('--hi', type=lambda x: int(x, 0), default=0xA000)
    args = ap.parse_args()

    if not os.path.exists(args.image):
        print('找不到文件: %s' % args.image)
        return 2
    data = open(args.image, 'rb').read()
    print('输入 %s  %d 字节  raw=%s' % (args.image, len(data), args.raw))

    images = collect_ec_images(data, args.raw)
    if not images:
        print('没认出任何 EC 镜像（判据是镜像里有 %r）' % EC_MARK.decode())
        return 1

    live = load_live(args.live) if args.live else {}
    for label, off, img in images:
        ver = re.search(rb'ITE EC-V[0-9.]*', img)
        recs = dedupe(scan_tables(img, args.lo, args.hi))
        print('\n=== %s  ROM偏移 0x%X  %d 字节  %s  表 %d 套 ==='
              % (label, off, len(img),
                 ver.group(0).decode() if ver else '无版本串', len(recs)))
        for r in recs:
            print_table(r)
        if live:
            compare(live, recs)
    return 0


if __name__ == '__main__':
    sys.exit(main())
