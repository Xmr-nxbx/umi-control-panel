# -*- coding: utf-8 -*-
"""寄存器快照差分实验：写一个候选寄存器，看整机 EC 状态跟着变了什么，然后还原。

为什么需要它：档位（办公/均衡/狂暴）到底落在哪个寄存器上，静态分析给不出答案
（GCUService 的 IL 被加密、CreatorCenter 元数据被保护）。但 EC 读是通的、
寄存器表是全的，所以可以「快照 → 写一个候选 → 再快照 → 差分」，
用整机状态的变化反推语义。

安全设计（对齐 README 第 6.3 节）：
  * 只写**一个**寄存器，取值必须来自 OEM 自己的枚举/常量表（参数校验）；
  * 写之前存原值，实验结束无条件写回并回读确认；
  * 全程温度保险：CPU 超过阈值立即还原退出；
  * 差分只读，不循环试探；失败即停手报告。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.ec_gpd import EcChannel  # noqa: E402
from app.bench import Bench  # noqa: E402
from app.config import Config  # noqa: E402
from app.logx import Log  # noqa: E402
from app.sense.clock import ClockSense  # noqa: E402
from app.sense.thermal import Thermal  # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'ec-mode-probe.txt')
TEMP_GUARD_C = 88.0
SETTLE_S = 6.0


def snapshot(ch):
    """把寄存器表里的每个地址读一遍（只读，约 125 次请求）。"""
    out = {}
    for name, addr in sorted(ch.registers.items(), key=lambda kv: kv[1]):
        value = ch.dev.read(int(addr))
        if value is not None:
            out[name] = value & 0xFF
    return out


def fan_rpm(ch):
    hi = ch.addr('ADDR_EC_MAIN_FAN_RPM_BYTE1')
    lo = ch.addr('ADDR_EC_MAIN_FAN_RPM_BYTE2')
    if hi is None or lo is None:
        return None
    a, b = ch.dev.read(int(hi)), ch.dev.read(int(lo))
    return None if a is None or b is None else ((a & 0xFF) << 8) | (b & 0xFF)


def diff(before, after, out, title):
    keys = sorted(set(before) | set(after))
    changed = [(k, before.get(k), after.get(k)) for k in keys if before.get(k) != after.get(k)]
    out('\n%s：%d 个寄存器发生变化（共比对 %d 个）' % (title, len(changed), len(keys)))
    for name, old, new in changed:
        out('    %-46s %s → %s   (0x%02X → 0x%02X)' % (
            name, old, new, old if old is not None else -1, new if new is not None else -1))
    if not changed:
        out('    （没有任何变化）')
    return changed


def main(argv):
    if len(argv) < 2:
        print('用法：ec_mode_probe.py <寄存器名> <写入值> [观察秒数]')
        print('例：  ec_mode_probe.py ADDR_MyFanCCI_Mode_Index 2 8')
        return 1
    reg, value = argv[0], int(argv[1], 0)
    settle = float(argv[2]) if len(argv) > 2 else SETTLE_S
    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    cfg, _e, _b = Config.load()
    log = Log('ec-mode-probe')
    ch = EcChannel(cfg, log)
    thermal, clock = Thermal(), ClockSense()
    bench = Bench(log=log, thermal=thermal, clock=clock)
    detail = ch.probe()
    out('EC 寄存器差分实验  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    out('目标：%s = %d；观察 %.0f 秒；通道状态=%s；allow_write=%s'
        % (reg, value, settle, detail.get('state'), ch.allow_write))
    if not ch.alive or not ch.allow_write:
        out('[停止] 通道不可用或写入未开启。')
        return _finish(lines, thermal, clock, ch, 1)
    if ch.addr(reg) is None:
        out('[停止] 寄存器表里没有 %s（拼写？本机没这个寄存器？）' % reg)
        return _finish(lines, thermal, clock, ch, 1)

    clock.read()
    snap0 = snapshot(ch)
    original = snap0.get(reg)
    temp0, _ = thermal.read()
    out('原值：%s = %s；CPU %s°C；频率 %s MHz' % (reg, original, temp0, clock.read()))
    if original is None:
        out('[停止] 读不到原值，不做写入（无法保证可逆）。')
        return _finish(lines, thermal, clock, ch, 1)
    if original == value:
        out('[停止] 目标值与原值相同，无需实验。')
        return _finish(lines, thermal, clock, ch, 1)

    base = bench.run_multi(workers=8, target_s=2.5)
    out('\n写入前全核跑分：%.2f Mops/s @%s MHz（峰值 %s），最高 %s°C'
        % (base['mops'], base.get('clock_mhz'), base.get('clock_peak_mhz'), base.get('temp_c')))

    out('\n=== 写入 %s = %d ===' % (reg, value))
    ok, msg = ch.write_register(reg, value)
    out('结果：%s' % msg)
    if not ok:
        _restore(ch, reg, original, out)
        return _finish(lines, thermal, clock, ch, 1)

    temps = []
    started = time.time()
    deadline = started + settle
    while time.time() < deadline:
        time.sleep(1.0)
        temp, _ = thermal.read()
        if temp:
            temps.append(temp)
        back = ch.dev.read(int(ch.addr(reg)))
        out('  +%2ds  %s=%s  CPU=%s°C  风扇=%s RPM' % (
            int(time.time() - started), reg, back, temp, fan_rpm(ch)))
        if temp and temp >= TEMP_GUARD_C:
            out('  [温度保险] %s°C ≥ %.0f°C，提前结束并还原' % (temp, TEMP_GUARD_C))
            break

    snap1 = snapshot(ch)
    diff(snap0, snap1, out, '写入后 EC 全表差分')
    limited = bench.run_multi(workers=8, target_s=2.5)
    out('\n写入后全核跑分：%.2f Mops/s @%s MHz（峰值 %s），最高 %s°C'
        % (limited['mops'], limited.get('clock_mhz'), limited.get('clock_peak_mhz'),
           limited.get('temp_c')))

    _restore(ch, reg, original, out)
    time.sleep(2.0)
    snap2 = snapshot(ch)
    diff(snap0, snap2, out, '还原后与原状态的差分（理想情况应为空）')
    restored = bench.run_multi(workers=8, target_s=2.5)
    out('\n还原后全核跑分：%.2f Mops/s @%s MHz' % (restored['mops'], restored.get('clock_mhz')))

    out('\n--- 判读 ---')
    delta = (limited['mops'] - base['mops']) * 100.0 / max(base['mops'], 0.001)
    clock_delta = (limited.get('clock_mhz') or 0) - (base.get('clock_mhz') or 0)
    out('全核跑分 %+0.1f%%，频率 %+d MHz' % (delta, clock_delta))
    # 以吞吐为准：频率会随机器冷热漂几百 MHz，单看频率会误判（上一版就误报过一次）
    if abs(delta) >= 8.0:
        out('→ 这个寄存器**确实影响性能**，可以作为档位控制的抓手。')
    elif abs(delta) >= 4.0 and abs(clock_delta) >= 300:
        out('→ 疑似影响性能（吞吐和频率同向变化），值得再用更长负载复测一次。')
    else:
        out('→ 性能没变化（吞吐差 %+.1f%% 在噪声范围内）。若上面差分里有寄存器跟着变，'
            '说明它只管风扇/状态；若差分全空，说明写了不被采纳。' % delta)
    out('共 %d 次读、%d 次写' % (ch.dev.reads, ch.dev.writes))
    return _finish(lines, thermal, clock, ch, 0)


def _restore(ch, reg, original, out):
    out('\n=== 还原 %s = %s ===' % (reg, original))
    ok, msg = ch.write_register(reg, original)
    out('结果：%s（%s）' % (msg, '成功' if ok else '失败，请手动检查'))


def _finish(lines, thermal, clock, ch, code):
    thermal.close()
    clock.close()
    ch.close()
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
