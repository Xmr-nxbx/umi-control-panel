# -*- coding: utf-8 -*-
"""单实例与端口的测试。

    runtime\\python.exe tests\\test_singleton_port.py

盯的是 2026-09-30 实测到的那个故障：观察脚本跑到一半，另一个脚本又把面板拉起来，
两个实例**同时绑在 8747 上**、同时用同一个 broker 身份连 GCUBridge，于是互相踢线，
MQTT 报告只留下前 60 秒。两处根因都要有测试看着：
  * CreateMutexW 的 last error 拿不到（ctypes.windll 不捕获），互斥形同虚设；
  * HTTPServer 默认 SO_REUSEADDR，而 Windows 上它的语义是「允许绑别人正在监听的端口」，
    于是「端口被占自动顺延」永远不触发。
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.cli import _find_free_port                    # noqa: E402
from app.server.httpd import Server                    # noqa: E402
from app.singleton import SingleInstance               # noqa: E402

CASES = []
NAME = 'UmiPanelSelfTest%d' % os.getpid()


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _occupy(port):
    s = socket.socket()
    s.bind(('127.0.0.1', port))
    s.listen(1)
    return s


@case('同名互斥：第二个实例必须拿不到（这就是双实例并跑的根因）')
def _():
    first = SingleInstance(NAME)
    assert first.acquire() is True, '第一个实例应当拿到互斥'
    try:
        second = SingleInstance(NAME)
        assert second.acquire() is False, '第二个实例必须被拦住，否则两个面板一起跑'
        second.release()
    finally:
        first.release()


@case('释放之后可以重新拿到（不许出现「一次故障后永久起不来」）')
def _():
    a = SingleInstance(NAME)
    assert a.acquire() is True
    a.release()
    b = SingleInstance(NAME)
    try:
        assert b.acquire() is True, '释放后仍拿不到，就是 open-revo 那个残留锁的坑'
    finally:
        b.release()


@case('名字不同互不影响')
def _():
    a = SingleInstance(NAME)
    b = SingleInstance(NAME + 'Other')
    try:
        assert a.acquire() is True
        assert b.acquire() is True
    finally:
        a.release()
        b.release()


@case('HTTP 服务不许 SO_REUSEADDR（Windows 上它等于允许端口被第二个进程绑走）')
def _():
    assert Server._Httpd.allow_reuse_address is False


@case('端口被占时构造服务必须报错，好让端口顺延那条路走得通')
def _():
    port = _free_port()
    blocker = _occupy(port)
    try:
        Server('127.0.0.1', port, None, None, lambda: None)
    except OSError:
        return
    finally:
        blocker.close()
    raise AssertionError('端口被别人占着还能绑上去，双实例就是这么来的')


@case('首选端口被占 → 顺延到下一个，并返回真实端口')
def _():
    port = _free_port()
    blocker = _occupy(port)
    made = []

    def factory(candidate):
        srv = Server('127.0.0.1', candidate, None, None, lambda: None)
        made.append(srv)
        return srv

    try:
        srv, got = _find_free_port(factory, '127.0.0.1', port)
        assert srv is not None and got == port + 1, (srv, got)
    finally:
        for s in made:
            s.httpd.server_close()
        blocker.close()


def main():
    print('单实例与端口：%d 个场景' % len(CASES))
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
