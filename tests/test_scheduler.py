"""调度器决策表测试：不碰系统，只喂假数据 + 假时钟。

    runtime\\python.exe tests\\test_scheduler.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DEFAULTS
from app.policy.scheduler import Scheduler

P = dict(DEFAULTS['scheduler'])
# 判定逻辑和「换挡节流」是两件事：下面这一大批用例只验判定，
# 节流单独用真实参数验（见最后两条）。
P.update({'min_up_dwell_s': 0.0, 'min_down_dwell_s': 0.0})
APPS = dict(DEFAULTS['apps'])


def snap(cpu=0.0, gpu=0.0, temp=None, idle=0.0, on_ac=True, fg=''):
    return {'cpu_pct': cpu, 'gpu_pct': gpu, 'cpu_temp': temp,
            'idle_s': idle, 'on_ac': on_ac, 'foreground': fg}


def drive(sched, points, start=1000.0):
    """points: [(snap, intent), ...] 每点间隔 1 秒，返回每拍的档位序列。"""
    out = []
    now = start
    for s, intent in points:
        out.append(sched.decide(s, now, intent)['tier'])
        now += 1.0
    return out


def run(n, name, fn):
    try:
        fn()
    except AssertionError as exc:
        print('  FAIL [%02d] %s -> %s' % (n, name, exc))
        return False
    print('  ok   [%02d] %s' % (n, name))
    return True


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case('冷启动落在均衡档')
def _():
    s = Scheduler(P, APPS)
    assert s.tier == 'bal', s.tier


@case('中等负载 2 秒升到流畅')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=40), 'auto')] * 4)
    assert seq == ['bal', 'bal', 'mid', 'mid'], seq


@case('高负载 2.5 秒升到性能')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=80), 'auto')] * 5)
    assert seq[-1] == 'perf', seq


@case('性能应用前台立刻拉满')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=5, fg='cs2.exe'), 'auto')])
    assert seq[0] == 'perf', seq


@case('性能档低负载 8 秒只退到流畅，不直通省电')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=80), 'auto')] * 5 + [(snap(cpu=5), 'auto')] * 20)
    assert seq[3] == 'perf', seq[:6]              # 2.5 秒进入性能档
    assert seq[5:13] == ['perf'] * 8, seq         # 低负载头 8 拍仍保持
    assert seq[13] == 'mid', seq[11:16]
    assert 'eco' not in seq, seq


@case('性能档长时间中等负载走软退出到流畅')
def _():
    s = Scheduler(P, APPS)
    pts = [(snap(cpu=80), 'auto')] * 5 + [(snap(cpu=50), 'auto')] * 45
    seq = drive(s, pts)
    assert seq[3] == 'perf', seq[:6]
    assert seq[20] == 'perf', seq[18:24]          # 硬退出条件不满足
    assert seq[45] == 'mid', seq[43:48]           # 40 秒软退出
    assert seq[-1] == 'mid', seq[-6:]
    assert 'eco' not in seq, seq


@case('流畅档低负载 15 秒回到均衡')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=40), 'auto')] * 4 + [(snap(cpu=5), 'auto')] * 20)
    assert seq[10] == 'mid', seq[8:14]
    assert seq[19] == 'bal', seq[17:22]


@case('均衡档要同时低负载且空闲 90 秒才进省电')
def _():
    s = Scheduler(P, APPS)
    busyish = [(snap(cpu=3, idle=10), 'auto')] * 30
    assert drive(s, busyish)[-1] == 'bal'
    seq = drive(s, [(snap(cpu=3, idle=120), 'auto')] * 30)
    assert seq[-1] == 'eco', seq[-6:]


@case('轻应用前台时省电门槛减半')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=3, idle=50, fg='wechat.exe'), 'auto')] * 30)
    assert seq[-1] == 'eco', seq[-6:]


@case('省电档一有负载 5 秒内回均衡')
def _():
    s = Scheduler(P, APPS)
    drive(s, [(snap(cpu=3, idle=200), 'auto')] * 40)
    assert s.tier == 'eco', s.tier
    # idle 保持 30 秒（不是「刚回到电脑前」），只验证负载回升会离开省电档
    seq = drive(s, [(snap(cpu=20, idle=30), 'auto')] * 8, start=2000.0)
    assert seq[-1] == 'bal', seq


@case('温度快速上冲提前解锁性能')
def _():
    # 负载必须是真负载：CPU 20% 那一档实测会被背景噪声顶上来（见下面那条 13% 用例），
    # 所以这里用 55%，只验「升温够快就提前放开功耗墙」。
    s = Scheduler(P, APPS)
    pts = [(snap(cpu=55, temp=68), 'auto')] * 3
    pts += [(snap(cpu=55, temp=t), 'auto') for t in (71, 74, 76, 78)]
    seq = drive(s, pts)
    assert 'perf' in seq, seq


@case('温度平稳时不误触发趋势预判')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=20, temp=75), 'auto')] * 8)
    assert 'perf' not in seq, seq


@case('温度保护把性能档压回均衡')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=90, temp=97), 'auto')] * 8)
    assert seq[-1] == 'perf', seq
    out = s.decide(snap(cpu=90, temp=97), 3000.0, 'auto')
    assert out['throttle'] and out['effective'] == 'bal', out


@case('温度回落到 90 以下解除保护')
def _():
    s = Scheduler(P, APPS)
    drive(s, [(snap(cpu=90, temp=97), 'auto')] * 6)
    out = s.decide(snap(cpu=90, temp=88), 5000.0, 'auto')
    assert not out['throttle'], out


@case('驻留期内不因一时低负载掉档')
def _():
    s = Scheduler(P, APPS)
    s.decide(snap(cpu=5), 1000.0, 'turbo')      # 手动拉满 → 进入驻留期
    assert s.tier == 'perf' and s.dwell_until > 1000.0, (s.tier, s.dwell_until)
    seq = drive(s, [(snap(cpu=3, idle=5), 'turbo')] * 20, start=1001.0)
    assert seq == ['perf'] * 20, seq


@case('手动意图优先于自适应规则')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=95), 'office')] * 10)
    assert set(seq) == {'eco'}, seq


@case('亮屏缓冲：空闲后回到电脑先进流畅档')
def _():
    s = Scheduler(P, APPS)
    drive(s, [(snap(cpu=3, idle=200), 'auto')] * 40)
    assert s.tier == 'eco', s.tier
    out = s.decide(snap(cpu=8, idle=0.2), 4000.0, 'auto')
    assert out['tier'] == 'mid' and out['resume_left'] > 0, out


@case('拔电且禁止电池性能时最高只到均衡')
def _():
    params = dict(P)
    params['allow_perf_on_battery'] = False
    s = Scheduler(params, APPS)
    seq = drive(s, [(snap(cpu=95, on_ac=False), 'auto')] * 10)
    assert 'perf' not in seq, seq


@case('负载反复抖动不会来回跳档')
def _():
    s = Scheduler(P, APPS)
    pts = []
    for i in range(30):
        pts.append((snap(cpu=65 if i % 2 else 20, idle=5), 'auto'))
    seq = drive(s, pts)
    changes = [seq[i] for i in range(1, len(seq)) if seq[i] != seq[i - 1]]
    assert len(changes) <= 2, (seq, changes)


@case('降档防抖：刚进性能档不会 8 秒就掉回流畅')
def _():
    s = Scheduler(dict(DEFAULTS['scheduler']), APPS)
    seq = drive(s, [(snap(cpu=80), 'auto')] * 5 + [(snap(cpu=3), 'auto')] * 30)
    assert seq[4] == 'perf', seq                 # 第一次升档不受限
    assert set(seq[5:]) == {'perf'}, seq         # 低负载 8 秒本该退，被 60 秒降档节流压住
    assert s.pending and s.pending['tier'] == 'mid', s.pending


@case('跨两级的大跳不受节流限制')
def _():
    s = Scheduler(dict(DEFAULTS['scheduler']), APPS)
    drive(s, [(snap(cpu=3, idle=200), 'auto')] * 40)
    assert s.tier == 'eco', s.tier
    seq = drive(s, [(snap(cpu=95), 'auto')] * 6, start=3000.0)
    assert seq[-1] == 'perf', seq                # eco→perf 跨三级，立刻放行


@case('均衡档时亮屏/解锁：立刻升流畅，不等负载起来、也不受防抖限制')
def _():
    # 用真实防抖参数：人工事件必须绕过「刚掉下来不许爬回去」那条
    s = Scheduler(dict(DEFAULTS['scheduler']), APPS)
    seq = drive(s, [(snap(cpu=3, idle=120), 'auto'), (snap(cpu=3, idle=0.2), 'auto')],
                start=5000.0)
    assert seq == ['bal', 'mid'], seq
    assert s.resume_until > 5001.0, s.resume_until


@case('温度趋势预判：真任务点火（5 秒升 7°C、CPU 50%）直接给性能档，不等 2.5 秒负载确认')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=50, temp=t), 'auto') for t in (66, 68, 70, 73, 76)])
    assert seq[:4] == ['bal', 'bal', 'mid', 'perf'], seq
    assert '温度趋势' in s.reason, s.reason


@case('温度趋势预判：只有余温在升、负载是背景噪声（CPU 13%）时不许升档')
def _():
    # 这条是实测逼出来的：跑分刚结束、CPU 13%/GPU 0%，余温还在爬，
    # 面板就升到性能档并把风扇推到强冷 —— 白花噪音，一点性能没换来。
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=13, temp=t), 'auto') for t in (66, 68, 70, 73, 76)])
    assert set(seq) == {'bal'}, seq
    assert '温度趋势' not in s.reason, s.reason


@case('温度趋势预判：没到 70°C 就不算「热」，负载再高也只按负载规则走')
def _():
    s = Scheduler(P, APPS)
    seq = drive(s, [(snap(cpu=50, temp=t), 'auto') for t in (60, 62, 64, 66, 68)])
    assert seq == ['bal', 'bal', 'mid', 'mid', 'mid'], seq
    assert '温度趋势' not in s.reason, s.reason


def main():
    print('调度器决策表：共 %d 个场景' % len(CASES))
    failed = [n for n, fn in CASES if not run(CASES.index((n, fn)) + 1, n, fn)]
    print('\n结果：%d/%d 通过' % (len(CASES) - len(failed), len(CASES)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
