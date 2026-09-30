# -*- coding: utf-8 -*-
"""给 tools/fan_table_test.py 的三个纯函数上保险（不碰硬件、不跑那个工具）。

    runtime\\python.exe tests\\test_fan_table_tool.py

为什么单独给工具写测试：#31 的风扇表写入实测只有一次机会（要机主在场听风扇），
判据错一次就白烧一次。这个工具自己在 import 期只做常量与 sys.path 处理、不连硬件，
所以可以安全 import —— 它连带 import 的 EC 通道模块确实会用到内核接口绑定，
那是**正常**的：本条纪律禁的是「跑」和「连」，不是 import。

本文件一行都不改 tools/fan_table_test.py；测的是它**现在的**行为，
哪怕你觉得某处判据不够严，也只写进交付说明，不在这里放宽或收紧断言。
"""
import importlib.util
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.fanrules as fanrules   # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# tools/ 不是包（没有 __init__.py），所以不能 import tools.fan_table_test；
# 用 importlib 按文件路径加载，仓库根从 tests/ 往上**一层**。
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_tool():
    path = os.path.join(REPO, 'tools', 'fan_table_test.py')
    spec = importlib.util.spec_from_file_location('fan_table_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ftt = _load_tool()


# ---- 测试数据：自己造一份最小可用表（16 点），照 test_fan_rules.py 的实测基线形状 ----
# 不读真硬件：up_t 前 9 点递增、点 9 起 0xFF 哨兵；点 0 占空比 0%、其余单调不减。
def make_points(duty_tail=None, up_tail=None):
    ups = [0, 53, 57, 59, 61, 63, 67, 69, 71] + [255] * 7
    downs = [48, 50, 60, 62, 64, 68, 70, 72] + [255] * 8
    duties = [0.0, 66.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0] + [99.0] * 7
    if duty_tail is not None:
        duties = duty_tail
    if up_tail is not None:
        ups = up_tail
    return [{'id': k, 'up_t': ups[k], 'down_t': downs[k], 'duty_pct': duties[k]}
            for k in range(16)]


def cpu():
    return make_points()


# ============ 1. in_table_range：写表地址白名单 ============

@case('in_table_range：CPU 表上界是 base+0x2F，0x0F30 是 GPU 表的头，必须 False')
def _():
    # 上界取错成 base+0x3F 就会同时改到 GPU 表 —— 两场实验混在一起
    assert ftt.in_table_range('CPU', 0x0F00) is True
    assert ftt.in_table_range('CPU', 0x0F2F) is True
    assert ftt.in_table_range('CPU', 0x0F30) is False
    assert ftt.in_table_range('CPU', 0x0F3F) is False


@case('in_table_range：GPU 表上界 base+0x2F = 0x0F5F，0x0F60 必须 False')
def _():
    assert ftt.in_table_range('GPU', 0x0F30) is True
    assert ftt.in_table_range('GPU', 0x0F5F) is True
    assert ftt.in_table_range('GPU', 0x0F60) is False


@case('in_table_range：括号字节 0x07C6 允许，PL1 区 0x0783 必须 False')
def _():
    assert ftt.in_table_range('CPU', fanrules.BRACKET_ADDR) is True
    assert fanrules.BRACKET_ADDR == 0x07C6
    assert ftt.in_table_range('CPU', 0x0783) is False      # PL1 功耗墙区，不属于风扇表
    assert ftt.in_table_range('GPU', 0x0783) is False


@case('in_table_range：两张表的区间互不重叠')
def _():
    # CPU 的上界之下，不能有任何 GPU 表地址被判 True
    assert all(not ftt.in_table_range('CPU', a)
               for a in range(0x0F30, 0x0F60))
    # GPU 表地址在 CPU 视角下也必须是 False
    assert all(ftt.in_table_range('CPU', a) is False
               for a in (0x0F30, 0x0F41, 0x0F50, 0x0F5F))


# ============ 2. modified：整体抬高，保住两条 EC 硬规则 ============

@case('modified：点 0 的占空比恒为 0.0（首点非 0 % 会被 EC 整表拒绝）')
def _():
    pts = cpu()
    out = ftt.modified(pts, 15.0)
    assert out[0]['duty_pct'] == 0.0, out[0]['duty_pct']
    # 就算原文点 0 被写成非 0，抬高逻辑也不该动它（id==0 直接跳过）
    pts2 = cpu()
    pts2[0]['duty_pct'] = 5.0
    assert ftt.modified(pts2, 15.0)[0]['duty_pct'] == 5.0


@case('modified：点 1 起整体抬高 delta')
def _():
    pts = cpu()
    out = ftt.modified(pts, 15.0)
    # 点 1 原 66.0 → 81.0；点 2 起本来就是 99.0，抬高后封顶 100.0
    assert out[1]['duty_pct'] == 81.0, out[1]['duty_pct']
    assert out[2]['duty_pct'] == 100.0, out[2]['duty_pct']


@case('modified：封顶 100，不会溢出')
def _():
    out = ftt.modified(cpu(), 50.0)
    assert all(p['duty_pct'] <= 100.0 for p in out)
    assert out[1]['duty_pct'] == 100.0


@case('modified：delta=0 时与原表逐点相同')
def _():
    pts = cpu()
    out = ftt.modified(pts, 0.0)
    assert out == pts, [(a, b) for a, b in zip(out, pts) if a != b]


@case('modified：结果过 fanrules.validate 零错误（单调不降仍成立）')
def _():
    out = ftt.modified(cpu(), 15.0)
    errors, _ = fanrules.validate('CPU', out)
    assert errors == [], errors


@case('modified：不原地修改入参（还原步骤会拿着同一份数据）')
def _():
    pts = cpu()
    snapshot = [dict(p) for p in pts]
    ftt.modified(pts, 30.0)
    assert pts == snapshot, '入参 points 被改脏了，还原时就会写回错误的表'


@case('modified：温度阈值一个都不动（动了就分不清是风扇猛还是升档早）')
def _():
    pts = cpu()
    out = ftt.modified(pts, 15.0)
    assert [p['up_t'] for p in out] == [p['up_t'] for p in pts]
    assert [p['down_t'] for p in out] == [p['down_t'] for p in pts]


# ============ 3. active_point：温度落在哪个点 ============

@case('active_point：温度正好等于某点 up_t 时取该点（不是下一个）')
def _():
    pts = cpu()
    hit = ftt.active_point(pts, 57)
    assert hit is not None and hit['id'] == 2, hit
    assert hit['up_t'] == 57


@case('active_point：低于点 0 的 up_t 时取点 0')
def _():
    # 点 0 的 up_t 是 0，温度 30 落在点 0
    hit = ftt.active_point(cpu(), 30)
    assert hit is not None and hit['id'] == 0, hit


@case('active_point：高于最后一点（点 8，up_t=71）时取最后一点')
def _():
    hit = ftt.active_point(cpu(), 95)
    assert hit is not None and hit['id'] == 8, hit
    assert hit['up_t'] == 71


@case('active_point：跳过哨兵点（up_t=255）与信箱槽（duty_pct=None）')
def _():
    hit = ftt.active_point(cpu(), 250)
    assert hit is not None and hit['id'] == 8, hit      # 不会落到 up_t=255 的尾部


@case('active_point：temp=None 时返回 None（还没读到温度）')
def _():
    assert ftt.active_point(cpu(), None) is None


@case('测哪张表就读哪一路：GPU 必须落在 fan2_rpm / fan_duty_r')
def _():
    # 早先 sample() 把占空比写死成 fan_duty_l，测 GPU 表时量的是**左路（CPU 风扇）**，
    # 于是「转速没动」的结论其实来自读错通道。方向不能反过来。
    assert ftt.RPM_KEY['CPU'] == 'fan_rpm' and ftt.RPM_KEY['GPU'] == 'fan2_rpm'
    assert ftt.DUTY_KEY['CPU'] == 'fan_duty_l'
    assert ftt.DUTY_KEY['GPU'] == 'fan_duty_r'


@case('「EC 在不在按这张表给占空」的闸看档位集合，不看温度查表的偏差')
def _():
    # 22:2x 实测推翻了旧判据：EC 查表的温度带滞后、也不是我们读的那个源，
    # 拿「我们的温度 → 表里该点」硬比会把活表误杀（54~59 °C 给 100 % 正是表里 57 °C 那档）。
    # 手动档的真特征是给出表里**没有**的占空值（90 %/87 %，表里只有 0/78/100）。
    assert ftt.DUTY_MATCH_PCT == 3.0
    # 30 % 是固件常量地板（0x3C），空转时就读到它；不把它算进「表里的档位」，
    # 正常空转状态会被误判成「EC 没在查表」而拒写
    assert ftt.EC_DUTY_FLOOR == 30.0
    assert 'EC_DUTY_FLOOR' in inspect.getsource(ftt.main)
    assert not hasattr(ftt, 'TABLE_FOLLOW_DEV'), '旧的偏差闸已经废了，别再留着'
    src = inspect.getsource(ftt.sample)
    assert "'duty_vals'" in src, src
    body = inspect.getsource(ftt.main)
    assert 'table_duties' in body and 'on_table' in body, '闸必须落在档位集合上'
    assert "dev_min' is not None and base['dev_min'] >" not in body


@case('modified：delta 为负时往下平移也不许出负数（turbo 档只有压得动，抬高会撞 100 %）')
def _():
    out = ftt.modified(cpu(), -30.0)
    assert all(p['duty_pct'] >= 0.0 for p in out if p['duty_pct'] is not None), out
    assert out[0]['duty_pct'] == 0.0
    assert out[1]['duty_pct'] == 36.0, out[1]['duty_pct']
    assert out[2]['duty_pct'] == 69.0, out[2]['duty_pct']
    assert fanrules.validate('CPU', out)[0] == []
    # 极端负值：整表压到 0 也必须合法（点 0 本来就是 0）
    deep = ftt.modified(cpu(), -500.0)
    assert all(p['duty_pct'] == 0.0 for p in deep if p['duty_pct'] is not None), deep
    assert fanrules.validate('CPU', deep)[0] == []


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print('  ok  %s' % name)
        except AssertionError as exc:
            bad += 1
            print('  FAIL %s -> %r' % (name, exc))
        except Exception as exc:                          # noqa: BLE001
            bad += 1
            print('  ERR  %s -> %s: %s' % (name, type(exc).__name__, exc))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
