# -*- coding: utf-8 -*-
"""历史环形缓冲测试：不碰系统，只喂假数据 + 假时钟。

    runtime\\python.exe tests\\test_history.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.history import History

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def mk(keep_s=60.0, sample_s=5.0):
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    os.unlink(path)
    return History(path=path, keep_s=keep_s, sample_s=sample_s), path


def snap(cpu=10.0, temp=60.0, mhz=2000, rpm=2500):
    return {'sensor': {'cpu_pct': cpu, 'cpu_temp': temp, 'cpu_mhz': mhz,
                       'gpu_pct': 0.0, 'fan_duty_l': 30.0},
            'hardware': {'fan_rpm': rpm, 'fan2_rpm': 0, 'battery_pct_ec': 100}}


@case('按采样间隔节流：一秒一拍也只收一个点')
def _():
    h, path = mk()
    try:
        got = [h.record(snap(), 'bal', now=1000.0 + i) for i in range(10)]
        assert sum(1 for g in got if g) == 2, got        # 1000 与 1005
        assert len(h.samples) == 2, h.samples
    finally:
        h.close()
        os.path.exists(path) and os.unlink(path)


@case('超出保留窗口会丢最老的点，不会无限长')
def _():
    h, path = mk(keep_s=30.0, sample_s=5.0)             # maxlen = 10
    try:
        for i in range(40):
            h.record(snap(cpu=float(i)), 'bal', now=1000.0 + i * 5)
        assert len(h.samples) <= 10, len(h.samples)
        assert h.samples[-1]['cpu_pct'] == 39.0, h.samples[-1]
        assert h.samples[0]['cpu_pct'] == 30.0, h.samples[0]
    finally:
        h.close()
        os.path.exists(path) and os.unlink(path)


@case('档位变化单独记一条标记，供曲线画竖线')
def _():
    h, path = mk()
    try:
        h.record(snap(), 'bal', now=1000.0)
        h.record(snap(), 'bal', now=1005.0)
        h.record(snap(), 'eco', now=1010.0)
        h.record(snap(), 'perf', now=1015.0)
        h.record(snap(), 'perf', now=1020.0)
        assert [m['tier'] for m in h.marks] == ['eco', 'perf'], h.marks
    finally:
        h.close()
        os.path.exists(path) and os.unlink(path)


@case('温度保护标志会跟着采样点存下来')
def _():
    h, path = mk()
    try:
        h.record(snap(), 'bal', throttle=True, now=1000.0)
        assert h.samples[0]['th'] == 1, h.samples[0]
        h.record(snap(), 'bal', now=1005.0)
        assert h.samples[1]['th'] == 0, h.samples[1]
    finally:
        h.close()
        os.path.exists(path) and os.unlink(path)


@case('落盘后重启能接上（守护模式会不定期重启）')
def _():
    h, path = mk()
    try:
        for i in range(4):
            h.record(snap(), 'bal', now=1000.0 + i * 5)
        h._save()
        h2, _p = History(path=path, keep_s=60.0, sample_s=5.0), None
        n = h2.load(now=1020.0)                          # 假时钟：窗口按注入时间算
        assert n == 4, n
        assert [s['cpu_temp'] for s in h2.samples] == [60.0] * 4, h2.samples
    finally:
        h.close()
        os.path.exists(path) and os.unlink(path)


@case('历史文件坏了不能拖垮启动')
def _():
    fd, path = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write('{ this is not json')
        h = History(path=path)
        assert h.load() == 0
        assert h.record(snap(), 'bal', now=1000.0) is True
    finally:
        os.unlink(path)


@case('缺读数就写 null，画曲线时断线而不是画成 0')
def _():
    h, path = mk()
    try:
        row = {'sensor': {'cpu_pct': None, 'cpu_temp': 55.0, 'cpu_mhz': None},
               'hardware': {}}
        h.record(row, 'bal', now=1000.0)
        s = h.samples[0]
        assert s['cpu_pct'] is None and s['cpu_mhz'] is None and s['fan_rpm'] is None, s
        assert s['cpu_temp'] == 55.0, s
    finally:
        h.close()
        os.path.exists(path) and os.unlink(path)


def main():
    print('历史缓冲：%d 个场景' % len(CASES))
    bad = 0
    for i, (name, fn) in enumerate(CASES, 1):
        try:
            fn()
            print('  ok   [%02d] %s' % (i, name))
        except AssertionError as exc:
            bad += 1
            print('  FAIL [%02d] %s -> %s' % (i, name, exc))
    print('\n结果：%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
