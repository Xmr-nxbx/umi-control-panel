# -*- coding: utf-8 -*-
"""跑分算分与解析的测试。

    runtime\\python.exe tests\\test_bench_score.py

盯的是这一版换负载时最容易出错的三件事：
  * 分数是「相对基准的指数」，方向不能搞反（短任务延迟越低越好）；
  * CoreMark 是加分项，缺了必须按剩下的项重新归一化，不能白掉 25%；
  * zstd 一行里既有压缩速度也有解压速度，只许取压缩那个。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.bench as bench                                     # noqa: E402
from app.bench import (best_of_each_tier, power_verdict, save_record, score_of,
                       set_baseline, _best_speed, _metrics)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def rec(**kw):
    base = {'ts': 1.0, 'tier': 'bal', 'label': '均衡', 'all_mb_s': 170.0,
            'core_mb_s': 40.0, 'coremark': 30000.0, 'burst_ms': 250.0,
            'clock_mhz': 3300, 'temp_after_c': 70}
    base.update(kw)
    return base


# zstd -b 的真实输出（一行里压缩速度在前、解压速度在后，两者都是「NNN MB/s」）
ZSTD_LINE = (' 9#-bench_input.bin   :  46140624 ->   19000000 (x2.428), '
             ' 171.4 MB/s,  431.0 MB/s')


@case('自己当基准：每一项都该是 100 分')
def _():
    r = rec()
    s = score_of(r, _metrics(r))
    assert s['overall'] == 100.0, s
    assert all(s[k] == 100.0 for k in ('all', 'core', 'cpu', 'burst')), s


@case('吞吐翻倍 → 200 分；延迟减半 → 也是 200 分（方向不能搞反）')
def _():
    base = _metrics(rec())
    fast = score_of(rec(all_mb_s=340.0, burst_ms=125.0), base)
    assert fast['all'] == 200.0, fast
    assert fast['burst'] == 200.0, fast
    slow = score_of(rec(all_mb_s=85.0, burst_ms=500.0), base)
    assert slow['all'] == 50.0 and slow['burst'] == 50.0, slow


@case('没编出 CoreMark 时按剩下三项归一化，总分不会白掉一截')
def _():
    base = _metrics(rec())
    s = score_of(rec(coremark=None), base)
    assert s['cpu'] is None, s
    # 其余三项都和基准一样，所以总分必须是 100，而不是 75
    assert s['overall'] == 100.0, s


@case('没有基准就不许编出分数')
def _():
    assert score_of(rec(), None) is None


@case('zstd 输出只取压缩速度，不把解压速度混进来')
def _():
    best, vals = _best_speed(ZSTD_LINE)
    assert best == 171.4, (best, vals)
    assert 431.0 not in vals, vals


@case('zstd 多轮迭代取最好的那一轮')
def _():
    text = ZSTD_LINE.replace('171.4', '160.2') + '\n' + ZSTD_LINE.replace('171.4', '175.9')
    best, vals = _best_speed(text)
    assert best == 175.9, (best, vals)
    assert len(vals) == 2, vals


def _with_history(rows, fn):
    """把 data/bench.json 换成临时文件再跑，绝不碰用户的真实成绩。"""
    tmp = tempfile.mkdtemp(prefix='umi-bench-')
    orig = bench.data_path
    bench.data_path = lambda name: os.path.join(tmp, name)
    try:
        for r in rows:
            save_record(r)
        return fn()
    finally:
        bench.data_path = orig
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)


@case('每个档位取最好成绩，顺序是 省电→均衡→流畅→性能')
def _():
    rows = [rec(tier='perf', all_mb_s=180.0), rec(tier='eco', all_mb_s=150.0),
            rec(tier='perf', all_mb_s=200.0), rec(tier='bal', all_mb_s=170.0)]

    def check():
        view = best_of_each_tier()
        tiers = [t['tier'] for t in view['tiers']]
        assert tiers == ['eco', 'bal', 'perf'], tiers
        by = {t['tier']: t['record'] for t in view['tiers']}
        assert by['perf']['all_mb_s'] == 200.0, by['perf']
    _with_history(rows, check)


@case('结论只认最近一次对比跑分：跨批次混着比会得出假结论')
def _():
    rows = [
        # 老批次：性能档比省电档慢（机器当时在烤机降温），不能被拿来下结论
        rec(tier='eco', ts=100.0, run_id=1, all_mb_s=200.0),
        rec(tier='perf', ts=101.0, run_id=1, all_mb_s=150.0),
        # 新批次：性能档快 20%
        rec(tier='eco', ts=200.0, run_id=2, all_mb_s=150.0),
        rec(tier='perf', ts=201.0, run_id=2, all_mb_s=180.0),
    ]

    def check():
        v = power_verdict()
        assert v is not None
        assert v['tiers_measured'] == 2, v
        assert v['effective'] is True, v
        assert v['perf_gain_pct'] == 20.0, v
    _with_history(rows, check)


@case('四档只差 2% 时，结论既要说「电源档位压不住」，也要指出真正管用的是 EC 硬件模式')
def _():
    rows = [rec(tier='eco', ts=300.0, run_id=9, all_mb_s=170.0, clock_mhz=3300),
            rec(tier='bal', ts=301.0, run_id=9, all_mb_s=171.0, clock_mhz=3310),
            rec(tier='mid', ts=302.0, run_id=9, all_mb_s=172.0, clock_mhz=3320),
            rec(tier='perf', ts=303.0, run_id=9, all_mb_s=173.0, clock_mhz=3330)]

    def check():
        v = power_verdict()
        assert v['effective'] is False, v
        assert '压不住' in v['reason'], v['reason']
        # 只说「压不住」等于把用户晾在半路：必须给出那什么才压得住，
        # 并且带上实测量级（2026-09-30 全表差分：风扇字节一动，PL1 75W↔10W）。
        assert 'EC 侧' in v['reason'] and '强冷' in v['reason'], v['reason']
        assert '23~26%' in v['reason'] and '75W/10W' in v['reason'], v['reason']
        # 直接写 PL 寄存器实测不生效，结论里不许出现「已解锁功耗墙」这种承诺。
        assert '不裸写功耗墙' in v['reason'], v['reason']
    _with_history(rows, check)


@case('钉基准：之后所有分数都相对这一次算')
def _():
    rows = [rec(tier='bal', ts=400.0, all_mb_s=100.0)]

    def check():
        set_baseline(rec(all_mb_s=100.0))
        save_record(rec(tier='perf', ts=401.0, all_mb_s=150.0))
        view = best_of_each_tier()
        by = {t['tier']: t for t in view['tiers']}
        assert by['perf']['score']['all'] == 150.0, by['perf']['score']
    _with_history(rows, check)


@case('换负载之前的老成绩整批丢掉：字段对不上就没有可比性')
def _():
    tmp = tempfile.mkdtemp(prefix='umi-bench-old-')
    orig = bench.data_path
    bench.data_path = lambda name: os.path.join(tmp, name)
    try:
        import json
        with open(os.path.join(tmp, bench.BENCH_NAME), 'w', encoding='utf-8') as f:
            json.dump({'baseline': {'single_mops': 5.48, 'multi_mops': 18.9},
                       'runs': [{'ts': 1.0, 'tier': 'eco', 'single_mops': 4.7},
                                {'ts': 2.0, 'tier': 'bal', 'all_mb_s': 170.0}]}, f)
        data = bench.load_history()
        assert data['baseline'] is None, data['baseline']
        assert len(data['runs']) == 1 and data['runs'][0]['tier'] == 'bal', data['runs']
        assert best_of_each_tier()['baseline'] is None
    finally:
        bench.data_path = orig
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)


def main():
    print('跑分算分与解析：%d 个场景' % len(CASES))
    bad = 0
    for i, (name, fn) in enumerate(CASES, 1):
        try:
            fn()
            print('  ok   [%02d] %s' % (i, name))
        except AssertionError as exc:
            bad += 1
            print('  FAIL [%02d] %s -> %s' % (i, name, exc))
        except Exception as exc:                            # noqa: BLE001
            bad += 1
            print('  ERR  [%02d] %s -> %r' % (i, name, exc))
    print('\n结果：%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
