# -*- coding: utf-8 -*-
"""selfcheck 的「功耗墙」体检项测试（只测纯函数，不发任何 HTTP）。

    runtime\\python.exe tests\\test_selfcheck_wall.py

只测 sec_power_wall：selfcheck.py 的 main() 会真的打面板接口，测试里绝不调它。
导入方式用 importlib（tools/ 不是包），仓库根从 tests/ 往上**一层**。
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_tool():
    path = os.path.join(REPO, 'tools', 'selfcheck.py')
    spec = importlib.util.spec_from_file_location('selfcheck_tool', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sc = _load_tool()


def hw(pl1=None, mode=None, hw_mode=None, drop=False):
    state = {}
    if pl1 is not None:
        state['pl1_setting'] = pl1
    if mode is not None:
        state['mode'] = mode
    if hw_mode is not None:
        state['hw_mode'] = hw_mode
    return {} if drop else {'state': state}


@case('正常档位：报出 PL1 值和档位名，不算问题')
def _():
    lines, problems = sc.sec_power_wall(hw(pl1=60, mode='performance'))
    assert problems == 0, problems
    assert 'PL1=60' in lines[0], lines
    assert 'performance' in lines[0], lines


@case('低墙：PL1<=15 明确说会卡，并计成一个问题')
def _():
    lines, problems = sc.sec_power_wall(hw(pl1=10, mode='office'))
    assert problems == 1, problems
    assert 'PL1=10' in lines[0], lines
    assert '卡' in lines[0], lines


@case('边界 15：仍是低墙（判据是 <=15）')
def _():
    _, problems = sc.sec_power_wall(hw(pl1=15))
    assert problems == 1, problems


@case('边界 16：已越过低墙线，不算问题')
def _():
    lines, problems = sc.sec_power_wall(hw(pl1=16))
    assert problems == 0, problems
    assert 'PL1=16' in lines[0], lines


@case('没有 mode 时退回 hw_mode 当档位名')
def _():
    lines, _ = sc.sec_power_wall(hw(pl1=75, hw_mode='turbo'))
    assert 'turbo' in lines[0], lines


@case('mode 与 hw_mode 都缺：档位写「未知」而不是编一个')
def _():
    lines, _ = sc.sec_power_wall(hw(pl1=75))
    assert '未知' in lines[0], lines


@case('pl1 缺失：归「可选」，不算故障（通道掉线时别重复报）')
def _():
    lines, problems = sc.sec_power_wall(hw())
    assert problems == 0, problems
    assert '没报到 PL1' in lines[0], lines


@case('pl1 是 bool（True/False）必须走「没报到」那档：_is_num 排除 bool')
def _():
    for bad in (True, False):
        lines, problems = sc.sec_power_wall(hw(pl1=bad))
        assert problems == 0, (bad, problems)
        assert '没报到 PL1' in lines[0], (bad, lines)


@case('hw 是 {} 或 state 缺失：不抛异常，走「没报到」')
def _():
    for bad in ({}, {'state': None}, {'state': 'not-a-dict'}):
        lines, problems = sc.sec_power_wall(bad)
        assert problems == 0, (bad, problems)
        assert '没报到 PL1' in lines[0], (bad, lines)


@case('返回类型是本文件约定的 (lines, problems) 二元组')
def _():
    out = sc.sec_power_wall(hw(pl1=60))
    assert isinstance(out, tuple) and len(out) == 2, out
    assert isinstance(out[0], list) and isinstance(out[1], int), out


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
