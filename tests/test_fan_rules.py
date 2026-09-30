# -*- coding: utf-8 -*-
"""风扇曲线校验与写序列计划的测试（纯函数，不碰硬件、不碰 EC）。

    runtime\\python.exe tests\\test_fan_rules.py

基线数据抄自 tests/test_fan_curve.py 钉住的那份 **2026-09-30 03:10:52 本机实测转储**
（tools/ec_fantable_dump.py 只读产物）：用它能同时钉两件事——读侧解出来的形状
交给 fanrules 必须判合法，以及 build_plan 的每个地址都落在厂商布局上。
地址对照是逐字节写死的，整体错位一格（点 1 落到 0x0F01 那种）在这里必然红。
"""
import copy
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import fanrules   # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---- 实测基线（形状 = 读侧 fan_curve() 交出来的 points，直接可用） ----
# CPU：up_t 前 9 点是真阈值、后 7 点是 0xFF 哨兵；down_t 从点 8 起是 255；
# duty 0% / 66% / 99%…（寄存器里 0 / 132 / 198…）
CPU_POINTS = [
    {'up_t': 0, 'down_t': 48, 'duty_pct': 0.0},
    {'up_t': 53, 'down_t': 50, 'duty_pct': 66.0},
    {'up_t': 57, 'down_t': 60, 'duty_pct': 99.0},
    {'up_t': 59, 'down_t': 62, 'duty_pct': 99.0},
    {'up_t': 61, 'down_t': 64, 'duty_pct': 99.0},
    {'up_t': 63, 'down_t': 68, 'duty_pct': 99.0},
    {'up_t': 67, 'down_t': 70, 'duty_pct': 99.0},
    {'up_t': 69, 'down_t': 72, 'duty_pct': 99.0},
    {'up_t': 71, 'down_t': 255, 'duty_pct': 99.0},
] + [{'up_t': 255, 'down_t': 255, 'duty_pct': 99.0} for _ in range(7)]

# GPU：同一次转储的 GPU 表；点 13/14/15 的占空比槽是厂商信箱，读侧给 None
GPU_POINTS = [
    {'up_t': 0, 'down_t': 48, 'duty_pct': 0.0},
    {'up_t': 52, 'down_t': 50, 'duty_pct': 76.0},
    {'up_t': 53, 'down_t': 57, 'duty_pct': 100.0},
    {'up_t': 56, 'down_t': 59, 'duty_pct': 100.0},
    {'up_t': 58, 'down_t': 61, 'duty_pct': 100.0},
    {'up_t': 60, 'down_t': 64, 'duty_pct': 100.0},
    {'up_t': 63, 'down_t': 66, 'duty_pct': 100.0},
    {'up_t': 65, 'down_t': 68, 'duty_pct': 100.0},
    {'up_t': 67, 'down_t': 255, 'duty_pct': 100.0},
] + [{'up_t': 255, 'down_t': 255, 'duty_pct': 100.0} for _ in range(4)] \
  + [{'up_t': 255, 'down_t': 255, 'duty_pct': None} for _ in range(3)]


def cpu():
    return copy.deepcopy(CPU_POINTS)


def gpu():
    return copy.deepcopy(GPU_POINTS)


@case('实测基线 CPU 表判为合法，且无任何警告')
def _():
    errors, warnings = fanrules.validate('CPU', cpu())
    assert errors == [], errors
    assert warnings == [], warnings


@case('实测基线 GPU 表判为合法；信箱槽有一条说明性警告')
def _():
    errors, warnings = fanrules.validate('GPU', gpu())
    assert errors == [], errors
    assert any('信箱' in w for w in warnings), warnings


@case('点 0 占空比非 0 被拦（EC 会整表拒绝并悄悄退回内置曲线）')
def _():
    pts = cpu()
    pts[0]['duty_pct'] = 10.0
    errors, _ = fanrules.validate('CPU', pts)
    assert any('点 0' in e and '占空比' in e for e in errors), errors


@case('点 0 的 up_t 非 0 被拦（那格根本没有存储位置）')
def _():
    pts = cpu()
    pts[0]['up_t'] = 40
    errors, _ = fanrules.validate('CPU', pts)
    assert any('点 0' in e and 'up_t' in e for e in errors), errors


@case('已用点升温阈值非严格单调被拦')
def _():
    pts = cpu()
    pts[3]['up_t'] = 56          # 比点 2 的 57 回落
    errors, _ = fanrules.validate('CPU', pts)
    assert any('单调' in e for e in errors), errors


@case('已用点占空比回落被拦（写「成功」但 EC 不照跑）')
def _():
    pts = cpu()
    pts[4]['duty_pct'] = 50.0    # 前面已是 99
    errors, _ = fanrules.validate('CPU', pts)
    assert any('单调不降' in e for e in errors), errors


@case('哨兵中途断开被拦（0xFF 后面又冒出真温度）')
def _():
    pts = cpu()
    pts[11]['up_t'] = 90         # 点 9 起已是 0xFF，点 11 又给真温度
    errors, _ = fanrules.validate('CPU', pts)
    assert any('哨兵' in e for e in errors), errors


@case('温度越界被拦（254 是上限，255 只属于哨兵语义）')
def _():
    pts = cpu()
    pts[3]['up_t'] = 254         # 边界值本身合法（会撞单调，但那不是越界）
    errors, _ = fanrules.validate('CPU', pts)
    assert not any('up_t' in e and '越界' in e for e in errors), errors
    pts[4]['up_t'] = 255         # 255 出现在已用点位置：哨兵从点 4 起、点 5 还是 61，
    errors, _ = fanrules.validate('CPU', pts)   # 撞「哨兵中途断开」，总之必须拦
    assert errors, errors
    pts2 = cpu()
    pts2[5]['down_t'] = 260      # down_t 越界（255 才是哨兵，260 就是错码）
    errors, _ = fanrules.validate('CPU', pts2)
    assert any('down_t' in e and '越界' in e for e in errors), errors


@case('占空比越界被拦（<0 或 >100）')
def _():
    for bad in (-5.0, 101.0):
        pts = cpu()
        pts[5]['duty_pct'] = bad
        errors, _ = fanrules.validate('CPU', pts)
        assert any('占空比' in e and '越界' in e for e in errors), (bad, errors)


@case('点数不等于 16 被拦')
def _():
    errors, _ = fanrules.validate('CPU', cpu()[:15])
    assert any('点数' in e for e in errors), errors
    pts = cpu() + [{'up_t': 255, 'down_t': 255, 'duty_pct': 0.0}]
    errors, _ = fanrules.validate('CPU', pts)
    assert any('点数' in e for e in errors), errors


@case('表名不是 CPU/GPU 被拦')
def _():
    errors, _ = fanrules.validate('FAN', cpu())
    assert any('CPU 或 GPU' in e for e in errors), errors


@case('已用点不足 2 个被拦')
def _():
    pts = cpu()
    pts[1]['up_t'] = 255         # 哨兵从点 1 开始，只剩点 0 一个已用点
    errors, _ = fanrules.validate('CPU', pts)
    assert any('至少 2' in e for e in errors), errors


@case('读侧没读全（None）不许生成计划')
def _():
    pts = cpu()
    pts[2]['up_t'] = None
    errors, _ = fanrules.validate('CPU', pts)
    assert any('缺失' in e for e in errors), errors


@case('GPU 信箱槽的 duty_pct=None 是正常形状，不算缺失')
def _():
    errors, _ = fanrules.validate('GPU', gpu())
    assert not any('缺失' in e for e in errors), errors


@case('警告：相邻已用点温差 <2 °C')
def _():
    pts = cpu()
    pts[1]['up_t'] = 52          # 点 1→2 只差 1 度（52→53），合法但 EC 平滑跟不上
    pts[2]['up_t'] = 53
    errors, warnings = fanrules.validate('CPU', pts)
    assert errors == [], errors
    assert any('温差' in w for w in warnings), warnings


@case('警告：点 1 起占空比全部不低于 90%（噪音档）')
def _():
    # 点 0 必须 0% 是硬规则；点 1 起全 95% 是合法但吵的表
    pts = [{'up_t': 0, 'down_t': 40, 'duty_pct': 0.0},
           {'up_t': 50, 'down_t': 48, 'duty_pct': 95.0}]
    pts += [{'up_t': 52 + 2 * i, 'down_t': 50 + 2 * i, 'duty_pct': 95.0} for i in range(6)]
    pts += [{'up_t': 255, 'down_t': 255, 'duty_pct': 95.0} for _ in range(8)]
    errors, warnings = fanrules.validate('CPU', pts)
    assert errors == [], errors
    assert any('90' in w for w in warnings), warnings


@case('警告：down_t 与 up_t 关系异常只提醒，不当错误拦')
def _():
    pts = cpu()
    pts[3]['down_t'] = 95        # up_t=59，差 36 度
    errors, warnings = fanrules.validate('CPU', pts)
    assert errors == [], errors
    assert any('down_t' in w and '语义未明' in w for w in warnings), warnings


@case('警告：点 14/15 的 down_t 不一致（共用一格，合成写以点 14 为准）')
def _():
    pts = cpu()
    pts[15]['down_t'] = 254
    errors, warnings = fanrules.validate('CPU', pts)
    assert errors == [], errors
    assert any('点 14' in w and '共用' in w for w in warnings), warnings
    plan = dict(fanrules.build_plan('CPU', pts))
    assert plan[0x0F1F] == pts[14]['down_t']    # 写的是点 14 的 255，不是 254


@case('build_plan：校验不过直接拒绝')
def _():
    pts = cpu()
    pts[0]['duty_pct'] = 20.0
    try:
        fanrules.build_plan('CPU', pts)
        raise AssertionError('应当抛 ValueError')
    except ValueError as exc:
        assert '校验' in str(exc)


@case('build_plan（CPU 基线）：首尾括号、条数、逐字节地址对照')
def _():
    plan = fanrules.build_plan('CPU', cpu())
    assert plan[0] == (0x07C6, 0x03), plan[0]        # 0x07 & ~0x04
    assert plan[-1] == (0x07C6, 0x07), plan[-1]      # 0x07 | 0x04
    assert len(plan) == 48, len(plan)                 # 2 括号 + 46 数据
    by_addr = {}
    for addr, value in plan[1:-1]:
        assert addr not in by_addr, ('重复地址', hex(addr))
        by_addr[addr] = value
    assert len(by_addr) == 46, len(by_addr)
    # 升温（防整体错位一格：点 1 落 0x0F00，不是 0x0F01）
    assert by_addr[0x0F00] == 53      # 点 1
    assert by_addr[0x0F01] == 57      # 点 2
    assert by_addr[0x0F0E] == 255     # 点 15（哨兵照原样写）
    # 降温
    assert by_addr[0x0F11] == 48      # 点 0
    assert by_addr[0x0F18] == 72      # 点 7
    assert by_addr[0x0F1F] == 255     # 点 14/15 共用，只出现一次
    # 占空比 ×2 编码
    assert by_addr[0x0F20] == 0       # 0%
    assert by_addr[0x0F21] == 132     # 66%
    assert by_addr[0x0F22] == 198     # 99%
    assert by_addr[0x0F2F] == 198
    # 哨兵位 0x0F0F 与厂商从不碰的 0x0F10 绝不出现
    assert 0x0F0F not in by_addr and 0x0F10 not in by_addr


@case('build_plan（GPU 基线）：43 条数据，信箱三格不在计划里')
def _():
    plan = fanrules.build_plan('GPU', gpu())
    assert plan[0] == (0x07C6, 0x03)
    assert plan[-1] == (0x07C6, 0x07)
    assert len(plan) == 45, len(plan)                 # 2 括号 + 43 数据
    addrs = [a for a, _ in plan]
    for mailbox in (0x0F5D, 0x0F5E, 0x0F5F):
        assert mailbox not in addrs, hex(mailbox)
    by_addr = dict(plan[1:-1])
    assert by_addr[0x0F30] == 52      # GPU 点 1 升温
    assert by_addr[0x0F41] == 48      # GPU 点 0 降温（base+0x11）
    assert by_addr[0x0F4F] == 255     # GPU 点 14/15 共用的降温格
    assert by_addr[0x0F50] == 0       # GPU 点 0 占空比
    assert by_addr[0x0F51] == 152     # GPU 点 1 占空比 76% ×2
    assert by_addr[0x0F52] == 200     # GPU 点 2 占空比 100% ×2
    assert by_addr[0x0F5C] == 200     # GPU 点 12 占空比 100% ×2（点 13 起是信箱）
    assert by_addr[0x0F3E] == 255     # GPU 点 15 升温哨兵


@case('build_plan：bracket_byte 由调用方传，不做任何设备读')
def _():
    plan = fanrules.build_plan('CPU', cpu(), bracket_byte=0x0F)
    assert plan[0] == (0x07C6, 0x0B), plan[0]         # 0x0F & ~0x04
    assert plan[-1] == (0x07C6, 0x0F), plan[-1]       # 0x0F | 0x04


@case('地址常量与读侧 ec_gpd 一字不差（本仓库只许有一套风扇表算法）')
def _():
    # fanrules 不许 import app.act，所以地址是抄来的；抄就得有人盯住别分叉。
    # 读侧哪天改了基址/信箱槽/哨兵，这条会红。
    from app.act.channels import ec_gpd
    assert dict(ec_gpd.FAN_TABLE_BASE) == fanrules.FAN_TABLE_BASE, fanrules.FAN_TABLE_BASE
    assert tuple(ec_gpd.GPU_DUTY_MAILBOX) == tuple(fanrules.GPU_DUTY_MAILBOX)
    assert ec_gpd.FAN_TABLE_SENTINEL == fanrules.SENTINEL
    assert ec_gpd.FAN_TABLE_POINTS == fanrules.FAN_TABLE_POINTS


@case('结构断言：fanrules 是纯计算模块，不存在任何写路径')
def _():
    with open(os.path.join(REPO, 'app', 'fanrules.py'), encoding='utf-8') as f:
        src = f.read()
    # 不许 import ctypes / app.act（文档里提到它们不算，只认真 import 语句）
    assert not re.search(r'^\s*import\s+ctypes\b', src, re.M)
    assert not re.search(r'^\s*from\s+ctypes\b', src, re.M)
    assert not re.search(r'^\s*(import|from)\s+app\.act\b', src, re.M)
    # 不许有写/发/执行语义的函数名，也不许碰设备与网络
    assert not re.search(r'\bdef\s+(write|apply|send|commit)\w*\s*\(', src)
    for bad in ('DeviceIoControl', 'CreateFile', 'paho', 'socket', 'urllib', 'subprocess'):
        assert bad not in src, bad


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
