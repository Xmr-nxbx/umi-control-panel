#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""实测「造物者模式」按键的三态到底改变了什么。

结论前提（tools/ec_watch.py 实测）：按一次实体键只有 ADDR_MAFAN_CONTROL_BYTE 在动，
在 Normal_Mode(0x00) → User_Fan_Mode(0x80) → Turbo_Mode(0x10) 之间循环，
PL1/PL2/PL4、MyFanCCI_Mode_Index 全部不动。所以这个工具回答的其实是：
「只改风扇这一字节，满载时频率/温度/吞吐会不会跟着变」。

安全约束（README 第 6.3 节，不可协商）：
  * 只写 OEM 枚举 MyFanCTLByteFlag 里的取值，不发明数值；
  * 全程可逆：先存原值，finally 里写回并回读确认；
  * 带温度保险：任一阶段超过 TEMP_ABORT 立即还原并停手；
  * 面板服务在跑时会拒绝执行——两边的自动跟随会互相抢方向盘，测出来的是噪声。

用法：runtime\python.exe tools\ec_mode_bench.py   （结果建议重定向到 tools\out）
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.ec_gpd import EcChannel, FAN_KEY
from app.bench import Bench, _Monitor
from app.config import Config
from app.logx import Log
from app.sense.clock import ClockSense
from app.sense.thermal import Thermal

FLAGS = ('Normal_Mode', 'Turbo_Mode', 'User_Fan_Mode')
LOAD_S = 25.0          # 满载持续多久：短时测不出热墙，只会测到出厂一致的功耗上限
SETTLE_S = 8.0         # 风扇爬到目标转速要时间
COOLDOWN_S = 20.0
TEMP_ABORT = 97.0
RPM_REGS = ('ADDR_EC_MAIN_FAN_RPM_BYTE1', 'ADDR_EC_MAIN_FAN_RPM_BYTE2')


def _port_open(port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(('127.0.0.1', port)) == 0
    finally:
        s.close()


class RpmSampler(threading.Thread):
    """满载期间旁路采风扇转速（和 _Monitor 采频率/温度并行）。"""

    def __init__(self, ch, interval=1.0):
        super().__init__(daemon=True)
        self.ch = ch
        self.interval = interval
        self.rpms = []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            hi = self.ch._read_named(RPM_REGS[0])
            lo = self.ch._read_named(RPM_REGS[1])
            if hi is not None and lo is not None:
                self.rpms.append((hi << 8) | lo)
            self._halt.wait(self.interval)

    def halt(self):
        self._halt.set()

    def result(self):
        if not self.rpms:
            return {'rpm_avg': None, 'rpm_peak': None}
        return {'rpm_avg': int(sum(self.rpms) / len(self.rpms)), 'rpm_peak': max(self.rpms)}


def measure(ch, bench, thermal, clock, flag):
    ok, detail = ch.set_fan_mode(flag, who='工具实测')
    if not ok:
        raise RuntimeError('写入 %s 失败：%s' % (flag, detail))
    print('  已写入 %s，稳定 %.0fs…' % (detail, SETTLE_S), flush=True)
    time.sleep(SETTLE_S)
    rpm = RpmSampler(ch)
    mon = _Monitor(clock, thermal)
    rpm.start()
    mon.start()
    try:
        multi = bench.load(target_s=LOAD_S)
    finally:
        mon.halt()
        mon.join(timeout=2.0)
        rpm.halt()
        rpm.join(timeout=2.0)
    out = dict(multi)
    out.update(mon.result())
    out.update(rpm.result())
    temp = out.get('temp_c')
    if temp and temp >= TEMP_ABORT:
        raise RuntimeError('温度 %s°C 触顶，立刻还原' % temp)
    print('  → 多线程 %.1f MB/s，平均 %s MHz（峰值 %s），风扇均 %s / 峰 %s RPM，最高 %s°C'
          % (out.get('mb_s') or 0, out.get('clock_mhz'), out.get('clock_peak_mhz'),
             out.get('rpm_avg'), out.get('rpm_peak'), out.get('temp_c')), flush=True)
    return out


def main(argv=None):
    # 反序复测：三态是依次测的，机器冷热不一样。如果结论只出现在某个顺序里，
    # 那就是顺序效应而不是档位效应，所以支持把顺序倒过来再跑一遍对照。
    flags = tuple(reversed(FLAGS)) if 'reverse' in (argv or []) else FLAGS
    cfg = Config.load()[0]
    log = Log('ec-mode-bench')
    port = int(cfg.get('server', 'port', default=8747) or 8747)
    if _port_open(port):
        print('面板服务还在跑（端口 %d）。它的自动跟随会和本工具抢同一个寄存器，'
              '先执行 runtime\\python.exe main.py --stop 再测。' % port)
        return 2
    if not bool(cfg.get('hardware', 'ec', 'allow_write', default=False)):
        print('EC 写入未开启（config.hardware.ec.allow_write=false），本工具不测写。')
        return 2

    thermal, clock = Thermal(), ClockSense()
    ch = EcChannel(cfg, log)
    detail = ch.probe()
    print('EC 通道：%s' % detail.get('state'))
    if not ch.alive:
        print(detail.get('reason'))
        return 1
    original = ch._read_named(FAN_KEY)
    print('起始 %s = 0x%02X（%s），测完会原样写回' % (
        FAN_KEY, original or 0, ch.fan_flag_name(original) or '?'))
    bench = Bench(log=log, thermal=thermal, clock=clock)

    results = {}
    code = 0
    try:
        for flag in flags:
            print('\n=== %s（0x%02X）===' % (flag, int(ch.fan_modes().get(flag, -1))))
            try:
                results[flag] = measure(ch, bench, thermal, clock, flag)
            except Exception as exc:                        # noqa: BLE001
                print('  [中断] %r' % (exc,))
                code = 1
                break
            time.sleep(COOLDOWN_S)
    finally:
        back = ch.set_fan_mode(
            ch.fan_flag_name(original) or 'Normal_Mode', who='工具还原') \
            if original is not None else (False, '起始值没读到，未还原')
        print('\n还原起始值：%s' % (back[1],))
        thermal.close()
        clock.close()
        ch.close()

    if len(results) == len(flags):
        base = results[flags[0]]
        print('\n=== 对比（基准 = %s，本轮顺序 %s）===' % (flags[0], '→'.join(flags)))
        print('%-16s %10s %10s %9s %9s %8s' % (
            '状态', 'MB/s', '平均MHz', '均RPM', '最高°C', '相对'))
        for flag, r in results.items():
            gain = (r['mb_s'] / base['mb_s'] - 1.0) * 100.0 if base.get('mb_s') else 0.0
            print('%-16s %10.2f %10s %9s %9s %7.1f%%' % (
                flag, r.get('mb_s') or 0, r.get('clock_mhz'), r.get('rpm_avg'),
                r.get('temp_c'), gain))
        spread = [r.get('rpm_avg') or 0 for r in results.values()]
        print('\n风扇转速跨度 %d RPM；吞吐最大差 %.1f%%。'
              '如果吞吐差不到 5%%，说明这一档改变的是噪音与温度，不是性能上限。'
              % (max(spread) - min(spread),
                 max((r.get('mb_s') or 0) for r in results.values()) /
                 min((r.get('mb_s') or 1) for r in results.values()) * 100 - 100))
    return code


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
