r"""只读转储本机 EC 风扇表与相关档位字节。

用途：任务 #31（风扇曲线读写实测）需要一份「写之前」的基线，写完再转储一次对比。
本脚本**只发 ECREAD**，不发任何 ECWRITE，不改任何硬件状态。

表布局来自厂商 FanTable_Manager1p5.SetEcFanTable 的解密源码（notes/hardware-channels.md 6.10 末尾）：
    CPU  UpT = 0x0F00+i   DownT = 0x0F11+i   Duty = 0x0F20+i     (i = 0..15)
    GPU  UpT = 0x0F30+j   DownT = 0x0F41+j   Duty = 0x0F50+j     (j = 0..15)
    Duty 存的是「百分比 × 2」
三个坑：
  * UpT 错位一格存——0x0F00+i 存的是 CPU[i+1].UpT，第 0 点的 UpT 不存（隐含为 0），
    而 0x0F0F / 0x0F3F 恒为 0xFF 哨兵；
  * DownT 的基址 0x0F10 / 0x0F40 本身厂商从不写，别拿它当数据；
  * 0x0F5D-0x0F5F 被 RefreshDefaultFanTableAll 借去当信箱
    （0x0F5F=模式、0x0F5D=0xFD、0x0F5E=0xC9），所以 GPU 最后三格占空比槽不可信。

跑之前必须停面板：两个进程同时握着 \\.\ACPIDriver 会让 EC 的口线握手交错，
读出来可能是垃圾（不会损坏硬件，但数据不可信）。

    runtime/python.exe tools/ec_fantable_dump.py [--interval 0.05]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.act.channels.ec_gpd import DEVICE, EcGpd  # noqa: E402

TABLE_BASE = {'CPU': 0x0F00, 'GPU': 0x0F30}
POINTS = 16
SENTINEL = 0xFF

# 表之外的上下文：档位字节、PL、以及 GM7MG7P 逆向里点名的几个状态字节。
# 全是**已知语义的具体地址**，不做范围盲扫——兄弟板曾因为盲扫风扇转速寄存器
# 把风扇扫停（notes/hardware-channels.md 6.11），所以这里一个多余的地址都不读。
CONTEXT = (
    (0x0751, 'MAFAN_CONTROL_BYTE 硬件模式总开关'),
    (0x0741, 'AP_OEM_BYTE：bit0=ap_exist，也是 EC 唯一的 PL 清零闸'),
    (0x0783, 'PL1_SETTING_VALUE'),
    (0x0784, 'PL2_SETTING_VALUE'),
    (0x0785, 'PL4_SETTING_VALUE'),
    (0x078E, 'SUPPORT_BYTE6：bit6=IsSuportRamFan1p5'),
    (0x07C5, 'AP_OEM_BYTE5：bit7=CPU/GPU 分表开关'),
    (0x07C6, 'AP_OEM_BYTE6：bit2=写表期间拉低的括号'),
    (0x0626, '档位状态字节（USER 臂用它和 0x0627 选 CODE 表）'),
    (0x0627, '档位状态字节：低半字节是 CODE 表索引'),
)
# 每档默认值块。EC 从不读它们（全镜像零读点），是发布给 host 取的。
DEFAULTS = tuple([(0x0730 + i, 'DEFAULT_BLOCK_0x0730+%d' % i) for i in range(8)]
                 + [(0x07A7 + i, 'DEFAULT_BLOCK_0x07A7+%d' % i) for i in range(4)])


def read_fan(dev, which, interval):
    """按厂商布局把一张 16 点表还原成逻辑点位。"""
    base = TABLE_BASE[which]
    raw = {}
    by_addr = {}
    for name, off in (('upt', 0), ('downt', 0x11), ('duty', 0x20)):
        for i in range(POINTS):
            # DownT 只写 i=0..14；UpT 的 i=15 是哨兵。都照实读回来，解码时再区分。
            addr = base + off + i
            value = dev.read(addr)
            raw[(name, i)] = value
            by_addr['0x%04X' % addr] = value
            time.sleep(interval)

    points = []
    for k in range(POINTS):
        upt_raw = raw[('upt', k - 1)] if k >= 1 else 0
        # GetEcFanTable 对 i=15 的 DownT 读的是 3856+i（不是 +i+1），也就是和 i=14
        # 同一个地址 0x0F1F，所以最后一点的 DownT 与第 14 点相同，不是"没有"。
        downt = raw[('downt', k)] if k <= 14 else raw[('downt', 14)]
        duty_raw = raw[('duty', k)]
        last_three = which == 'GPU' and k >= 13
        points.append({
            'id': k,
            'up_t': upt_raw,
            'down_t': downt,
            'duty_raw': duty_raw,
            'duty_pct': (duty_raw / 2.0) if duty_raw is not None and not last_three else None,
            # GPU 的 0x0F5D-0x0F5F 是信箱不是占空比槽，读到的值不能当 duty 解释
            'duty_slot_is_mailbox': last_three,
        })
    return points, by_addr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=float, default=0.05,
                    help='每次读之间的间隔秒数。默认 0.05 → 20 次/秒，'
                         '远低于 6.3 第 6 条允许的全表 62 次/秒')
    ap.add_argument('--json', action='store_true', help='只输出 JSON，便于存档对比')
    args = ap.parse_args()

    dev = EcGpd()
    if not dev.open():
        print('打不开 %s：%s' % (DEVICE, dev.error), file=sys.stderr)
        print('面板是不是还在跑？两个进程同时握句柄会让读数不可信。', file=sys.stderr)
        return 2

    out = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'reads': 0, 'writes': 0}
    try:
        ctx = []
        for addr, note in CONTEXT + DEFAULTS:
            ctx.append({'addr': '0x%03X' % addr, 'value': dev.read(addr), 'note': note})
            time.sleep(args.interval)
        out['context'] = ctx

        for which in ('CPU', 'GPU'):
            points, flat = read_fan(dev, which, args.interval)
            out[which] = {'points': points, 'raw': flat}
    finally:
        out['reads'] = dev.reads
        out['writes'] = dev.writes
        dev.close()

    if out['writes'] != 0:
        # 这个脚本只许读。真出现了写，说明有人改了 EcGpd 的默认行为，必须炸出来。
        print('!! 本脚本发出了 %d 次写，这不该发生 !!' % out['writes'], file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print('转储时间 %s · 共 %d 次读 · %d 次写' % (out['ts'], out['reads'], out['writes']))
    print('\n== 档位与上下文 ==')
    for row in out['context']:
        v = row['value']
        print('  %s = %-6s %s' % (row['addr'],
                                  ('0x%02X (%d)' % (v, v)) if v is not None else '读取失败',
                                  row['note']))

    for which in ('CPU', 'GPU'):
        print('\n== %s 风扇表（16 点） ==' % which)
        print('   点  升温阈值  降温阈值  占空比原始  占空比%%')
        for p in out[which]['points']:
            duty = ('%.1f' % p['duty_pct']) if p['duty_pct'] is not None else (
                '信箱占用' if p['duty_slot_is_mailbox'] else '-')
            upt = '0xFF哨兵' if (p['id'] == 15 and p['up_t'] == SENTINEL) else str(p['up_t'])
            print('  %3d  %8s  %8s  %10s  %8s' % (
                p['id'], upt,
                '-' if p['down_t'] is None else str(p['down_t']),
                str(p['duty_raw']), duty))
    return 0


if __name__ == '__main__':
    sys.exit(main())
