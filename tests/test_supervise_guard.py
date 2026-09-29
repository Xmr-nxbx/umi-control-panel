# -*- coding: utf-8 -*-
"""守护进程的「卡死判定」测试：活着但不干活，也要能判出来。

    runtime\\python.exe tests\\test_supervise_guard.py

只测判定逻辑，不真的启子进程：把 _http_json 换成假应答即可。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import cli

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class FakeProc:
    """假装一个子进程：poll() 在 n 次之后返回退出码。"""

    def __init__(self, after=999, code=0):
        self.calls = 0
        self.after = after
        self.code = code
        self.killed = False

    def poll(self):
        self.calls += 1
        return self.code if self.calls > self.after else None

    def kill(self):
        self.killed = True


def with_ping(handler):
    """把 cli._http_json 换成假应答，返回还原函数。"""
    real = cli._http_json

    def fake(port, path, body=None, timeout=10):
        return handler(path)
    cli._http_json = fake

    def restore():
        cli._http_json = real
    return restore


@case('节拍在走 → 健康')
def _():
    restore = with_ping(lambda p: {'pong': True, 'tick_age_s': 1.4})
    try:
        ok, age = cli._child_healthy(8747)
        assert ok and age == 1.4, (ok, age)
    finally:
        restore()


@case('HTTP 有回但调度节拍 200 秒没走 → 判卡死')
def _():
    restore = with_ping(lambda p: {'pong': True, 'tick_age_s': 200.0})
    try:
        ok, _age = cli._child_healthy(8747)
        assert not ok, ok
    finally:
        restore()


@case('端口开着但请求超时 → 判卡死')
def _():
    def boom(port, path, body=None, timeout=10):
        raise OSError('timed out')
    real = cli._http_json
    cli._http_json = boom
    try:
        ok, age = cli._child_healthy(8747)
        assert not ok and age is None, (ok, age)
    finally:
        cli._http_json = real


@case('老版本应答里没有 tick_age_s → 不因此误杀')
def _():
    restore = with_ping(lambda p: {'pong': True})
    try:
        ok, age = cli._child_healthy(8747)
        assert ok and age is None, (ok, age)
    finally:
        restore()


@case('子进程自己退出 → 交回退出码，不判卡死')
def _():
    proc = FakeProc(after=2, code=0)
    restore = with_ping(lambda p: {'pong': True, 'tick_age_s': 0.5})
    old_poll, old_stall = cli.STALL_POLL_S, cli.STALL_AFTER_S
    cli.STALL_POLL_S = 0.01
    try:
        kind, code = cli._watch_child(proc, cli.Log('guard-test'))
        assert kind == 'exit' and code == 0, (kind, code)
        assert not proc.killed
    finally:
        cli.STALL_POLL_S, cli.STALL_AFTER_S = old_poll, old_stall
        restore()


@case('一直问不到节拍 → 判 stalled（守护会杀掉重拉）')
def _():
    proc = FakeProc(after=10 ** 6)
    restore = with_ping(lambda p: {'pong': True, 'tick_age_s': 500.0})
    old_poll, old_stall = cli.STALL_POLL_S, cli.STALL_AFTER_S
    cli.STALL_POLL_S = 0.01
    cli.STALL_AFTER_S = 0.05
    try:
        kind, code = cli._watch_child(proc, cli.Log('guard-test'))
        assert kind == 'stalled', (kind, code)
    finally:
        cli.STALL_POLL_S, cli.STALL_AFTER_S = old_poll, old_stall
        restore()


@case('端口文件迟迟不出现（启动即卡）→ 也判 stalled')
def _():
    import tempfile
    proc = FakeProc(after=10 ** 6)
    real_path = cli.data_path
    cli.data_path = lambda name: os.path.join(tempfile.gettempdir(), 'no-such-port-file')
    restore = with_ping(lambda p: {'pong': True, 'tick_age_s': 0.1})
    old_poll, old_stall = cli.STALL_POLL_S, cli.STALL_AFTER_S
    cli.STALL_POLL_S = 0.01
    cli.STALL_AFTER_S = 0.05
    try:
        kind, _code = cli._watch_child(proc, cli.Log('guard-test'))
        assert kind == 'stalled', kind
    finally:
        cli.STALL_POLL_S, cli.STALL_AFTER_S = old_poll, old_stall
        cli.data_path = real_path
        restore()


@case('健康时不会误判：连续 30 次好应答依旧不 stalled')
def _():
    proc = FakeProc(after=31, code=0)
    restore = with_ping(lambda p: {'pong': True, 'tick_age_s': 2.0})
    old_poll, old_stall = cli.STALL_POLL_S, cli.STALL_AFTER_S
    cli.STALL_POLL_S = 0.001
    cli.STALL_AFTER_S = 5.0
    try:
        began = time.time()
        kind, code = cli._watch_child(proc, cli.Log('guard-test'))
        assert kind == 'exit' and code == 0, (kind, code)
        assert time.time() - began < 4.0
    finally:
        cli.STALL_POLL_S, cli.STALL_AFTER_S = old_poll, old_stall
        restore()


def main():
    print('守护卡死判定：%d 个场景' % len(CASES))
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
