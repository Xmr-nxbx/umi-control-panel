# -*- coding: utf-8 -*-
"""EC 写通道的一次可逆验证：改风扇模式字节，看硬件是否真的响应，然后改回去。

为什么先拿风扇模式开刀：
  * 取值来自 OEM 自己的枚举（MyFanCTLByteFlag：Normal_Mode=0 / Turbo_Mode=0x10 /
    FanBoost_Mode=0x40 …），不是我们编的；
  * 目标寄存器就是当前读出 Turbo_Mode 的那个，读得通、语义明确；
  * 完全可逆：先记住原值，测完立刻写回，并回读确认；
  * 有温度保险：CPU 超过阈值立即还原并退出，不硬撑。

前置条件：config.hardware.ec.allow_write = true（默认是 false，通道会直接拒绝）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.ec_gpd import EcChannel  # noqa: E402
from app.config import Config  # noqa: E402
from app.logx import Log  # noqa: E402
from app.sense.clock import ClockSense  # noqa: E402
from app.sense.thermal import Thermal  # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'ec-write-test.txt')
TEMP_GUARD_C = 85.0
SAMPLE_S = 2.0


def main(argv):
    flag = argv[0] if argv else 'Normal_Mode'
    seconds = float(argv[1]) if len(argv) > 1 else 24.0

    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    cfg, err, _bad = Config.load()
    if err:
        out('配置读取失败：%s' % err)
    log = Log('ec-write-test')
    ch = EcChannel(cfg, log)
    thermal, clock = Thermal(), ClockSense()

    detail = ch.probe()
    out('EC 写通道可逆验证  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    out('通道状态：%s（%s）' % (detail.get('state'), detail.get('reason')))
    out('allow_write=%s  可用风扇模式=%s' % (ch.allow_write, sorted(ch.fan_modes()) or '无'))
    if not ch.alive:
        return _finish(lines, thermal, clock, ch, 1)
    if not ch.allow_write:
        out('[停止] config.hardware.ec.allow_write 不是 true，通道会拒绝写入。')
        return _finish(lines, thermal, clock, ch, 1)

    ch.tick()
    before = ch.read()
    original = before.get('fan_ctl_byte')
    temp0, _zones = thermal.read()
    out('\n改动前：风扇模式字节=%s(%s)  转速=%s/%s RPM  占空=%s%%/%s%%  CPU=%s°C  频率=%s MHz'
        % (original, before.get('fan_mode_flag'), before.get('fan_rpm'), before.get('fan2_rpm'),
           before.get('fan_duty_l'), before.get('fan_duty_r'), temp0, clock.read()))
    if original is None:
        out('[停止] 读不到风扇模式字节原值，不能做可逆写入。')
        return _finish(lines, thermal, clock, ch, 1)

    out('\n写入 %s …' % flag)
    ok, msg = ch.set_fan_mode(flag)
    out('结果：%s → %s' % ('成功' if ok else '失败', msg))
    if not ok:
        return _finish(lines, thermal, clock, ch, 1)

    samples = []
    out('\n观察 %.0f 秒（每 %.0f 秒采一次）：' % (seconds, SAMPLE_S))
    deadline = time.time() + seconds
    aborted = False
    while time.time() < deadline:
        time.sleep(SAMPLE_S)
        ch._last_fast = 0.0
        ch.tick()
        snap = ch.read()
        temp, _zones = thermal.read()
        mhz = clock.read()
        samples.append((snap.get('fan_rpm'), snap.get('fan2_rpm'),
                        snap.get('fan_duty_l'), temp, mhz))
        out('  转速=%s/%s RPM  占空=%s%%  CPU=%s°C  频率=%s MHz' % (
            snap.get('fan_rpm'), snap.get('fan2_rpm'), snap.get('fan_duty_l'), temp, mhz))
        if temp is not None and temp >= TEMP_GUARD_C:
            out('  [温度保险] CPU %s°C ≥ %.0f°C，立即还原' % (temp, TEMP_GUARD_C))
            aborted = True
            break

    out('\n还原原值 %s(%s) …' % (original, ch._fan_flag_name(original)))
    ok2, msg2 = ch.write_register('ADDR_MAFAN_CONTROL_BYTE', original)
    out('还原结果：%s → %s' % ('成功' if ok2 else '失败', msg2))
    if not ok2:
        out('!! 还原失败，请手动检查风扇模式（原值 %s）' % original)

    def stats(idx):
        vals = [s[idx] for s in samples if s[idx] is not None]
        return (min(vals), round(sum(vals) / len(vals)), max(vals)) if vals else (None, None, None)

    rpm_min, rpm_avg, rpm_max = stats(0)
    duty_min, duty_avg, duty_max = stats(2)
    out('\n--- 对比 ---')
    out('转速（主风扇）：改动前 %s RPM → 期间 min/avg/max = %s/%s/%s' % (
        before.get('fan_rpm'), rpm_min, rpm_avg, rpm_max))
    out('占空比：改动前 %s%% → 期间 min/avg/max = %s/%s/%s' % (
        before.get('fan_duty_l'), duty_min, duty_avg, duty_max))
    if rpm_avg is not None and before.get('fan_rpm'):
        delta = rpm_avg - before['fan_rpm']
        out('判读：平均转速变化 %+d RPM（%+.0f%%）→ %s' % (
            delta, delta * 100.0 / before['fan_rpm'],
            'EC 写入对硬件确实有效' if abs(delta) >= 100 or (duty_avg or 0) != before.get('fan_duty_l')
            else '没看出明显变化，需要更长观察或该模式下转速本就相同'))
    if aborted:
        out('（本次因温度保险提前结束）')
    out('\n共发送 %d 次读、%d 次写请求' % (ch.dev.reads, ch.dev.writes))
    return _finish(lines, thermal, clock, ch, 0 if ok2 else 1)


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
