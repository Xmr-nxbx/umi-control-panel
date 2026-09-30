# -*- coding: utf-8 -*-
"""Win 键锁定 + 电池充电三档的解码测试。

    runtime\\python.exe tests\\test_battery_winlock.py

依据是 2026-09-30 01:16 那次观察（logs/观察3 + tools/out/mqtt-watch.txt）：
用户按顺序点了三次 Win 锁、又按「平衡 → 健康 → 长效」切了电池档，
EC 全表观察器和 MQTT 观察器的时间戳能一一对齐。这里把**当时真实读到的字节值**
钉成测试用例，以后谁改了解码逻辑，跑一遍就知道有没有把已确认的语义改坏。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import (BATTERY_MODE_ACTION, BATTERY_MODE_LABELS,
                                   battery_mode_of_oem_byte4,
                                   win_locked_of_status_byte)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# 观察3 里 ADDR_AP_OEM_BYTE4 实际出现过的四个值（含开头和结尾的默认态）
BYTE4_SEQUENCE = [0x09, 0x19, 0x29, 0x09]
# 对应的 MQTT 动作（01:16:30 / 01:16:43 / 01:17:01，最后一条是切回默认）
BYTE4_ACTIONS = [None, 'BALANCEDMODE', 'HEALTHYMODE', 'PERFORMANCEDMODE']


@case('观察3 的字节序列逐条解对：默认→平衡→健康→长效→回到默认')
def _():
    got = [battery_mode_of_oem_byte4(v) for v in BYTE4_SEQUENCE]
    assert got == ['long', 'balanced', 'healthy', 'long'], got


@case('每条 MQTT 动作都对得上它之后读到的那个字节')
def _():
    by_action = {'BALANCEDMODE': 'balanced', 'HEALTHYMODE': 'healthy',
                 'PERFORMANCEDMODE': 'long'}
    for action, value in zip(BYTE4_ACTIONS[1:], BYTE4_SEQUENCE[1:]):
        assert battery_mode_of_oem_byte4(value) == by_action[action], (action, value)


@case('界面词只用 Creator Center 上的原话：平衡/健康/长效')
def _():
    assert BATTERY_MODE_LABELS == {'balanced': '平衡', 'healthy': '健康', 'long': '长效'}


@case('档位 id 与 OEM 动作名一一对应，且都是抓包抓到过的')
def _():
    assert BATTERY_MODE_ACTION == {'balanced': 'BALANCEDMODE', 'healthy': 'HEALTHYMODE',
                                   'long': 'PERFORMANCEDMODE'}
    assert set(BATTERY_MODE_ACTION) == set(BATTERY_MODE_LABELS)


@case('低半字节不是 9、或高半字节没见过 → None（面板写「未知」，不猜）')
def _():
    for value in (0x00, 0x08, 0x0A, 0x18, 0x39, 0x49, 0xFF, None):
        assert battery_mode_of_oem_byte4(value) is None, hex(value or 0)


@case('STAUTS_BYTE 只认 0/1：观察3 的三次跳变 0→1→0→1')
def _():
    seq = [0, 1, 0, 1]
    assert [win_locked_of_status_byte(v) for v in seq] == [False, True, False, True]


@case('STAUTS_BYTE 出现别的值就当没见过的状态，返回 None')
def _():
    for value in (2, 3, 0x10, 0xFF, None):
        assert win_locked_of_status_byte(value) is None, value


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
