# -*- coding: utf-8 -*-
"""会话事件分发测试：往窗口过程喂一条 WM_WTSSESSION_CHANGE，看它有没有转成动作。

    runtime\\python.exe tests\\test_tray_session.py

不锁屏幕也能验证：真正的解锁要人输密码，但「收到事件 → 调回调」这一段
是纯消息分发，直接喂给 _wnd_proc 就是它运行时会走的那条路。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tray.tray import (NOTIFY_FOR_THIS_SESSION, WM_WTSSESSION_CHANGE,
                           WTS_SESSION_LOCK, WTS_SESSION_UNLOCK, Tray)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class FakeLog:
    def __init__(self):
        self.lines = []

    def info(self, t):
        self.lines.append(('info', t))

    def warn(self, t):
        self.lines.append(('warn', t))

    def error(self, t):
        self.lines.append(('error', t))


def make_tray(**kw):
    return Tray(None, FakeLog(), lambda: None, lambda i: None, lambda: None,
                lambda m: None, **kw)


@case('解锁事件转成 on_session("unlock")')
def _():
    got = []
    t = make_tray(on_session=got.append)
    t._wnd_proc(1, WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK, 0)
    assert got == ['unlock'], got


@case('锁定事件转成 on_session("lock")')
def _():
    got = []
    t = make_tray(on_session=got.append)
    t._wnd_proc(1, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK, 0)
    assert got == ['lock'], got


@case('其它会话子类型（如服务启动）不当成用户回来')
def _():
    got = []
    t = make_tray(on_session=got.append)
    t._wnd_proc(1, WM_WTSSESSION_CHANGE, 4, 0)      # WTS_SESSION_LOGON
    assert got == [], got


@case('没注册回调时收到事件不许抛异常')
def _():
    t = make_tray()
    t._wnd_proc(1, WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK, 0)


@case('回调抛异常不能弄丢托盘线程')
def _():
    def boom(kind):
        raise RuntimeError('回调里炸了')
    t = make_tray(on_session=boom)
    t._wnd_proc(1, WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK, 0)   # 不许冒出异常
    assert any('异常' in line for _lvl, line in t.log.lines), t.log.lines


@case('常量对齐 Win32 头文件（对错了事件永远收不到）')
def _():
    assert WM_WTSSESSION_CHANGE == 0x02B1, hex(WM_WTSSESSION_CHANGE)
    assert (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK) == (7, 8)
    assert NOTIFY_FOR_THIS_SESSION == 0


def main():
    print('会话事件分发：%d 个场景' % len(CASES))
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
