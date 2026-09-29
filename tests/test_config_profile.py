# -*- coding: utf-8 -*-
"""调度性格预设的读写测试。

    runtime\\python.exe tests\\test_config_profile.py

这里盯的是上一版真出过的 bug：性格补丁被原地写进 cfg.data，
换回「标准」后旧阈值留在内存里，下一次保存就把 cpu_perf=70 这类值落进了
config.json —— 面板显示标准档，跑的却是安静档，而且再也回不去。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DEFAULTS, SCHED_PROFILES, Config, apply_sched_profile

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def mk_cfg(raw=None):
    cfg = Config(raw if raw is not None else {'scheduler': {'profile': 'standard'}})
    return apply_sched_profile(cfg)


@case('性格只是叠层：运行时值变、原值不动')
def _():
    cfg = mk_cfg({'scheduler': {'profile': 'quiet'}})
    assert cfg.data['scheduler']['cpu_perf'] == 70, cfg.data['scheduler']['cpu_perf']
    # sched_base 是「默认值 + 用户自己写的」，不含任何性格补丁
    assert cfg.sched_base['cpu_perf'] == DEFAULTS['scheduler']['cpu_perf'], cfg.sched_base
    assert cfg.sched_base['profile'] == 'quiet', cfg.sched_base


@case('换回标准档必须干净地回到默认阈值')
def _():
    cfg = mk_cfg()
    cfg.set_profile('quiet')
    assert cfg.data['scheduler']['cpu_perf'] == 70
    cfg.set_profile('performance')
    assert cfg.data['scheduler']['cpu_perf'] == 45, cfg.data['scheduler']['cpu_perf']
    cfg.set_profile('standard')
    assert cfg.data['scheduler']['cpu_perf'] == DEFAULTS['scheduler']['cpu_perf'], \
        cfg.data['scheduler']
    assert cfg.sched_base.get('profile') == 'standard', cfg.sched_base


@case('落盘的文件里不许出现性格补丁值')
def _():
    cfg = mk_cfg()
    cfg.set_profile('quiet')
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    try:
        cfg.path = path
        cfg.save()
        with open(path, encoding='utf-8') as f:
            disk = json.load(f)
        sched = disk['scheduler']
        assert sched.get('profile') == 'quiet', sched.get('profile')
        # 文件里可以是默认值，但绝不能是被性格改过的那个值
        for key, want in SCHED_PROFILES['quiet']['patch'].items():
            assert sched.get(key) != want, (key, sched.get(key), want)
    finally:
        os.unlink(path)


@case('重启后（重读文件）运行时值和内存里一致')
def _():
    cfg = mk_cfg()
    cfg.set_profile('performance')
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    try:
        cfg.path = path
        cfg.save()
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        again = apply_sched_profile(Config(raw, path=path))
        assert again.data['scheduler']['cpu_perf'] == \
            cfg.data['scheduler']['cpu_perf'], (again.data['scheduler'], cfg.data['scheduler'])
        assert again.data['scheduler']['min_down_dwell_s'] == 90.0, again.data['scheduler']
    finally:
        os.unlink(path)


@case('没得选的性格不许把调度算崩')
def _():
    cfg = mk_cfg({'scheduler': {'profile': '不存在的档'}})
    assert cfg.data['scheduler']['cpu_perf'] == DEFAULTS['scheduler']['cpu_perf'], \
        cfg.data['scheduler']
    assert cfg.data['scheduler']['perf_hold_s'] == DEFAULTS['scheduler']['perf_hold_s']


@case('用户自己改过的键不受性格影响')
def _():
    cfg = mk_cfg({'scheduler': {'profile': 'standard', 'dwell_s': 900}})
    for name in SCHED_PROFILES:
        cfg.set_profile(name)
        assert cfg.data['scheduler']['dwell_s'] == 900, (name, cfg.data['scheduler']['dwell_s'])


def main():
    print('调度性格读写：%d 个场景' % len(CASES))
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
