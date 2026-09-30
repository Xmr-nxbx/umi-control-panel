# -*- coding: utf-8 -*-
"""EC 0x0786（TCC 偏移 + 使能位）的解码测试。

    runtime\\python.exe tests\\test_tcc_offset.py

这一个字节有两个 OEM 名字，两边都出自厂商自己的东西：
  * 本机 Creator Center 的 ECSpec 叫它 ADDR_L1_PWM_DEFAULT_MYFAN3（风扇 PWM 默认值）；
  * 客服 ROM 里那份 DSDT 的 ECMG 字段表把它拆成 APTC(bit0-6) + APTN(bit7)，
    厂商服务侧的 SetCpuTccOffset 也按这个语义写：使能时写 offset|0x80，不使能写 0。
认后一对（DSDT + 服务互相印证），理由记在 notes/hardware-channels.md 6.11 十二。

为什么要读它：0x07D8-0x07DA 那三个「每档一个 TCC 偏移默认值」本机读出来是 5/5/5，
但**只有 APTN 置位时才生效**——参考仓库 2026-09-23 的实测里这个字节一直是 0x00，
也就是偏移根本没开。所以「降频点 = TjMax-5 = 95 °C」这个推论是错的，
必须先把这一位读出来，才能谈跑分工具那道温度保险该定多少。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import (TCC_ENABLE_BIT, TCC_OFFSET_MASK, tcc_offset_of)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# 位定义照抄 DSDT 的 ECMG 字段表：bit7 = APTN（使能），bit0-6 = APTC（偏移，单位 °C）
@case('位掩码就是 bit7 + bit0-6，没有重叠')
def _():
    assert TCC_ENABLE_BIT == 0x80
    assert TCC_OFFSET_MASK == 0x7F
    assert TCC_ENABLE_BIT & TCC_OFFSET_MASK == 0


# 参考仓库实测：0x0786 在他们整轮抓取里一直是 0x00（偏移没开）。
# 厂商服务写使能态的形式是 offset|0x80，所以 5 °C 使能 = 0x85。
@case('实测过的四个字节值逐条解对')
def _():
    got = {v: tcc_offset_of(v) for v in (0x00, 0x80, 0x85, 0x05)}
    assert got == {0x00: (False, 0), 0x80: (True, 0),
                   0x85: (True, 5), 0x05: (False, 5)}, got


@case('使能位只看 bit7，偏移只看 bit0-6，两者互不干扰')
def _():
    for off in range(0, 0x80):
        assert tcc_offset_of(off) == (False, off), off
        assert tcc_offset_of(off | 0x80) == (True, off), off


@case('全 1 是上界：使能 + 127 °C，不越界也不翻成负数')
def _():
    assert tcc_offset_of(0xFF) == (True, 127)
    assert tcc_offset_of(0x7F) == (False, 127)


@case('读不到就是 (None, None)，面板写「未知」，不猜成 0')
def _():
    assert tcc_offset_of(None) == (None, None)


@case('返回的是 bool 不是 int：面板可以直接当真假用')
def _():
    enabled, _ = tcc_offset_of(0x85)
    assert enabled is True
    enabled, _ = tcc_offset_of(0x05)
    assert enabled is False


# 下面两条钉的是「放在哪儿」，不是「怎么解」。
# 0x0786 是 60 秒一轮的低频项：它不是每秒都在动的量，进 2 秒高频组
# 就白占限速预算（notes/hardware-channels.md 6.3 第 6 条），而且会让全表差分的基线变脏。
@case('0x0786 在 60 秒低频组里，不在 2 秒高频组里')
def _():
    from app.act.channels import ec_gpd
    fast = {k for _, keys in ec_gpd.FAST_GROUPS for k in keys}
    slow = {k for _, keys in ec_gpd.SLOW_GROUPS for k in keys}
    assert 'ADDR_L1_PWM_DEFAULT_MYFAN3' in slow
    assert 'ADDR_L1_PWM_DEFAULT_MYFAN3' not in fast
    assert [g for g, _ in ec_gpd.SLOW_GROUPS].count('tcc') == 1


# 原始字节照 fan_rpm_raw / battery_temp_raw 的先例留在 ec_raw 里不进 state：
# state 是给面板和判据用的语义值，原始值只在需要复核时去 ec_raw 翻。
@case('派生的两个语义值进 state 白名单，原始字节不进')
def _():
    from app.act.hardware import STATE_KEYS
    assert 'tcc_offset_enabled' in STATE_KEYS
    assert 'tcc_offset_c' in STATE_KEYS
    assert 'tcc_offset_raw' not in STATE_KEYS


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
