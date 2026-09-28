# -*- coding: utf-8 -*-
"""只改用户态电源属性，量「最大处理器状态」在本机到底管不管用。

背景：2026-09-29 四档跑分发现 eco 写了 max=85% 仍跑到 4274 MHz，
和 perf 档几乎没差别。这个脚本逐项验证哪个旋钮真的有效，跑完自动还原。
不碰内核驱动，不需要管理员。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.power import (EPP, MAX_STATE, MIN_STATE, BOOST_MODE, SUB_PROCESSOR,  # noqa: E402
                           active_scheme, read_setting, set_setting)
from app.bench import _spin  # noqa: E402
from app.sense.clock import ClockSense  # noqa: E402
from app.sense.system import power_status  # noqa: E402

NO_WINDOW = 0x08000000

# 逐项试：如果连「max30 + 禁用睿频 + EPP 最省 + 最低频率 5%」都压不住频率，
# 那就说明这台机器的频率完全由 BIOS/EC 管，Windows 电源属性是摆设。
COMBOS = (
    ('基准 max100', {'max': 100}),
    ('max 70%', {'max': 70}),
    ('max 50%', {'max': 50}),
    ('极限省电 max30+boost0+EPP100+min5', {'max': 30, 'boost': 0, 'epp': 100, 'min': 5}),
)


def measure(clock, seconds=2.5, workers=1):
    ips = 5.5e6
    n = int(ips * seconds)
    procs = []
    t0 = time.perf_counter()
    for _ in range(workers):
        procs.append(subprocess.Popen(
            [sys.executable, '-c',
             'import sys\nn=int(sys.argv[1]);x=1;m=0xFFFFFFFF\n'
             'for _ in range(n):\n    x=(x*1103515245+12345)&m\n    x^=x>>13\nprint(x&7)',
             str(n)], stdout=subprocess.DEVNULL, creationflags=NO_WINDOW))
    samples = []
    while time.perf_counter() - t0 < seconds:
        mhz = clock.read()
        if mhz:
            samples.append(mhz)
        time.sleep(0.3)
    for p in procs:
        p.wait(timeout=120)
    dt = time.perf_counter() - t0
    return {'mops': round(workers * n / dt / 1e6, 2),
            'clock_avg': round(sum(samples) / len(samples)) if samples else None,
            'clock_max': max(samples) if samples else None}


def main():
    scheme = active_scheme()
    print('活动方案=%s 供电=%s' % (scheme, power_status()))
    clock = ClockSense()
    print('标称频率=%s MHz 频率计数器=%s' % (clock.base_mhz, '可用' if clock.available else '不可用'))
    original = {'max_ac': read_setting(scheme, MAX_STATE), 'max_dc': read_setting(scheme, MAX_STATE, True),
                'min_ac': read_setting(scheme, MIN_STATE), 'min_dc': read_setting(scheme, MIN_STATE, True),
                'epp_ac': read_setting(scheme, EPP), 'boost_ac': read_setting(scheme, BOOST_MODE)}
    print('原始值=%s' % original)
    try:
        for label, over in COMBOS:
            set_setting(scheme, MAX_STATE, over.get('max', 100))
            set_setting(scheme, MAX_STATE, over.get('max', 100), dc=True)
            set_setting(scheme, MIN_STATE, over.get('min', original['min_ac'] or 5))
            if 'boost' in over:
                set_setting(scheme, BOOST_MODE, over['boost'])
            if 'epp' in over:
                set_setting(scheme, EPP, over['epp'])
            time.sleep(3.0)
            back = read_setting(scheme, MAX_STATE)
            clock.read()
            time.sleep(1.0)
            idle2 = clock.read()
            st = measure(clock, seconds=2.5, workers=1)
            mt = measure(clock, seconds=2.5, workers=os.cpu_count() or 8)
            print('%-34s 回读max=%s%%  空载=%s MHz  '
                  '单线程 %.2f Mops/s @%s MHz  多线程 %.2f Mops/s @%s MHz'
                  % (label, back, idle2, st['mops'], st['clock_avg'],
                     mt['mops'], mt['clock_avg']))
    finally:
        set_setting(scheme, MAX_STATE, original['max_ac'] if original['max_ac'] is not None else 100)
        set_setting(scheme, MAX_STATE, original['max_dc'] if original['max_dc'] is not None else 100,
                    dc=True)
        if original['min_ac'] is not None:
            set_setting(scheme, MIN_STATE, original['min_ac'])
        if original['boost_ac'] is not None:
            set_setting(scheme, BOOST_MODE, original['boost_ac'])
        if original['epp_ac'] is not None:
            set_setting(scheme, EPP, original['epp_ac'])
        print('已还原 max=%s min=%s boost=%s epp=%s' % (
            read_setting(scheme, MAX_STATE), read_setting(scheme, MIN_STATE),
            read_setting(scheme, BOOST_MODE), read_setting(scheme, EPP)))
    clock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
