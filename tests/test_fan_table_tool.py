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
