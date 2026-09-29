# -*- coding: utf-8 -*-
"""功耗墙写入的可逆验证：把 PL1/PL2 设成「办公档」的出厂值，用跑分量出真实差距，再还原。

为什么要做这个实验：本机实测证明 Windows 电源属性完全压不住频率（max=30% 仍跑 4.2GHz），
所以「换挡要有体感」只能靠 EC 侧的功耗墙。寄存器名字与取值都来自 OEM 自己的表：
    ADDR_PL1_SETTING_VALUE / ADDR_PL2_SETTING_VALUE / ADDR_PL4_SETTING_VALUE
    ADDR_OFFICE_PL1_DEFAULT_VALUE = 35、ADDR_GAMING_PL1_DEFAULT_VALUE = 60
实验设计（全部可逆、有温度保险）：
  1. 记录原始值（本机读到的是 0，推测 0 = 用出厂默认）；
  2. 写 PL1=PL2=35（办公档默认值），回读确认；
  3. 空载采样 + 3 秒单线程负载采样，记录实际频率；
  4. 立刻还原原值并回读确认；
  5. 再测一遍作为对照，报告频率/跑分差。
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

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'ec-pl-test.txt')
TEMP_GUARD_C = 88.0


def sample(ch, thermal, clock, seconds, label, out):
    clock.read()
    end = time.time() + seconds
    temps, clocks = [], []
    while time.time() < end:
        time.sleep(1.0)
        ch._last_fast = 0.0
        ch.tick()
        snap = ch.read()
        temp, _ = thermal.read()
        mhz = clock.read()
        if temp:
            temps.append(temp)
        if mhz:
            clocks.append(mhz)
        out('  [%s] PL1=%s PL2=%s 风扇=%s RPM 占空=%s%% CPU=%s°C 频率=%s MHz' % (
            label, snap.get('pl1_setting'), snap.get('pl2_setting'), snap.get('fan_rpm'),
            snap.get('fan_duty_l'), temp, mhz))
        if temp and temp >= TEMP_GUARD_C:
            out('  [温度保险] %s°C ≥ %.0f°C，提前结束采样' % (temp, TEMP_GUARD_C))
            break
    return {'temp_max': max(temps) if temps else None,
            'clock_avg': round(sum(clocks) / len(clocks)) if clocks else None}


def measure(bench, ch, out, label, workers=8):
    """用**全核**负载量：PL1 是持续功耗墙，单线程负载根本碰不到它。"""
    ch._last_fast = 0.0
    ch.tick()
    pre = ch.read()
    result = bench.load(workers=workers, target_s=2.5)
    ch._last_fast = 0.0
    ch.tick()
    post = ch.read()
    out('  [%s] 全核 %.1f MB/s @%s MHz（峰值 %s MHz），最高 %s°C' % (
        label, result['mb_s'], result.get('clock_mhz'), result.get('clock_peak_mhz'),
        result.get('temp_c')))
    for key, text in (('pl1_setting', 'PL1设置值'), ('pl2_setting', 'PL2设置值'),
                      ('vrm_limit', 'VRM限流'), ('vrm_max_limit', 'VRM上限'),
                      ('complex_power_status', '复杂电源状态'), ('fan_rpm', '风扇')):
        out('        %-12s 前=%-6s 后=%-6s' % (text, pre.get(key), post.get(key)))
    return result


def main(argv):
    target = int(argv[0]) if argv else 35
    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    cfg, _err, _bad = Config.load()
    log = Log('ec-pl-test')
    ch = EcChannel(cfg, log)
    thermal, clock = Thermal(), ClockSense()
    bench = Bench(log=log, thermal=thermal, clock=clock)
    detail = ch.probe()
    out('EC 功耗墙可逆验证  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    out('通道状态：%s；allow_write=%s' % (detail.get('state'), ch.allow_write))
    if not ch.alive or not ch.allow_write:
        out('[停止] 通道不可用或写入未开启。')
        return _finish(lines, thermal, clock, ch, 1)

    ch.tick()
    original = {}
    snap0 = ch.read()
    for name, key in (('ADDR_PL1_SETTING_VALUE', 'pl1_setting'),
                      ('ADDR_PL2_SETTING_VALUE', 'pl2_setting'),
                      ('ADDR_PL4_SETTING_VALUE', 'pl4_setting')):
        original[name] = snap0.get(key)
    out('原始值：%s' % original)
    out('出厂默认（只读）：办公 PL1=%s PL2=%s；均衡/游戏 PL1=%s PL2=%s；省电 PL1=%s' % (
        ch.values.get('defaults', {}).get('ADDR_OFFICE_PL1_DEFAULT_VALUE'),
        ch.values.get('defaults', {}).get('ADDR_OFFICE_PL2_DEFAULT_VALUE'),
        ch.values.get('defaults', {}).get('ADDR_GAMING_PL1_DEFAULT_VALUE'),
        ch.values.get('defaults', {}).get('ADDR_GAMING_PL2_DEFAULT_VALUE'),
        ch.values.get('defaults', {}).get('ADDR_BATTERYSAVER_PL1_DEFAULT_VALUE')))

    out('\n=== 对照组：不改任何东西，全核测一次 ===')
    base = measure(bench, ch, out, '对照')

    out('\n=== 写入 PL1=PL2=%d W ===' % target)
    wrote = []
    for name in ('ADDR_PL1_SETTING_VALUE', 'ADDR_PL2_SETTING_VALUE'):
        ok, msg = ch.write_register(name, target)
        out('  %s' % msg)
        wrote.append((name, ok))
    if not all(ok for _, ok in wrote):
        out('[停止] 写入未全部成功，直接还原。')
        _restore(ch, original, out)
        return _finish(lines, thermal, clock, ch, 1)

    ch._last_fast = 0.0
    ch.tick()
    after_write = ch.read()
    out('  回读：PL1=%s PL2=%s PL4=%s' % (after_write.get('pl1_setting'),
                                          after_write.get('pl2_setting'),
                                          after_write.get('pl4_setting')))
    limited = measure(bench, ch, out, '限功耗')
    sample(ch, thermal, clock, 6.0, '限功耗', out)

    out('\n=== 还原 ===')
    _restore(ch, original, out)
    restored = measure(bench, ch, out, '还原后')

    out('\n--- 判读 ---')
    out('全核对照 %.1f MB/s @%s MHz → 限功耗 %.1f MB/s @%s MHz → 还原后 %.1f MB/s @%s MHz'
        % (base['mb_s'], base.get('clock_mhz'), limited['mb_s'], limited.get('clock_mhz'),
           restored['mb_s'], restored.get('clock_mhz')))
    gain = (base['mb_s'] - limited['mb_s']) * 100.0 / max(base['mb_s'], 0.001)
    clock_drop = ((base.get('clock_mhz') or 0) - (limited.get('clock_mhz') or 0))
    if gain >= 8.0 or clock_drop >= 200:
        out('结论：EC 功耗墙**确实生效**（跑分低 %.1f%%，频率低 %d MHz）。'
            '这就是「换挡有体感」的抓手：办公/均衡/狂暴 = 不同 PL + 不同风扇模式。'
            % (gain, clock_drop))
    else:
        out('结论：写进去了但没看出性能变化（跑分差 %.1f%%，频率差 %d MHz）。'
            '可能这个寄存器不是生效路径，或需要配合 TRIGGER/STATUS 命令时序。'
            '不猜、不乱写，下一步只做静态分析。' % (gain, clock_drop))
    out('共 %d 次读、%d 次写' % (ch.dev.reads, ch.dev.writes))
    return _finish(lines, thermal, clock, ch, 0)


def _restore(ch, original, out):
    for name, value in original.items():
        if value is None:
            out('  %s 原值未知，跳过还原' % name)
            continue
        ok, msg = ch.write_register(name, value)
        out('  %s（%s）' % (msg, '成功' if ok else '失败'))


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
