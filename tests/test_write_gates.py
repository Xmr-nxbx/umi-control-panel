# -*- coding: utf-8 -*-
"""写操作的两道闸：来源校验 + allow_write。

    runtime\\python.exe tests\\test_write_gates.py

面板只听 127.0.0.1，但这**不等于**安全：机主浏览器里任何一个网页都能往
http://127.0.0.1:8747/api/action 发 POST（CSRF），而那个接口能下发 OEM 硬件命令、
还能把面板关掉。所以要钉住两件事：
  * 跨站 / 非回环 Host 的写请求一律 403，curl 这类不带 Origin 的照常放行；
  * `MqttChannel.send_action` 必须查 `config.hardware.ec.allow_write`——
    白名单只回答「OEM 认不认这个命令」，不回答「现在准不准发」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.mqtt_gcu import MqttChannel      # noqa: E402
from app.server.httpd import is_local_same_origin      # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class FakeCfg:
    """只喂 send_action 需要的那几个键；allow_write 由构造参数决定。"""

    def __init__(self, allow_write=False):
        self.allow_write = allow_write

    def get(self, *keys, default=None):
        if keys == ('hardware', 'mqtt'):
            return {'enabled': True, 'client_id': 'x', 'username': 'x'}
        if keys == ('hardware', 'ec', 'allow_write'):
            return self.allow_write
        return default


class FakeLog:
    def info(self, *a):
        pass

    def error(self, *a):
        pass

    def warn(self, *a):
        pass


def _channel(allow_write):
    ch = MqttChannel(FakeCfg(allow_write), FakeLog())
    ch.alive = True                 # 只测闸门，不连 broker
    return ch


@case('本机页面发来的写请求放行（Origin 与 Host 一致）')
def _():
    for host in ('127.0.0.1:8747', 'localhost:8747'):
        h = {'Host': host, 'Origin': 'http://%s' % host}
        assert is_local_same_origin(h) is True, host


@case('别的网站发来的写请求挡掉（CSRF）')
def _():
    for origin in ('https://evil.example', 'http://127.0.0.1:9999', 'null'):
        h = {'Host': '127.0.0.1:8747', 'Origin': origin}
        assert is_local_same_origin(h) is False, origin


@case('只有 Referer 也照同一套规矩判')
def _():
    assert is_local_same_origin({'Host': '127.0.0.1:8747',
                                 'Referer': 'http://127.0.0.1:8747/'}) is True
    assert is_local_same_origin({'Host': '127.0.0.1:8747',
                                 'Referer': 'http://evil.example/x'}) is False


@case('curl / 本机工具不带 Origin，照常放行（不能把脚本挡死）')
def _():
    assert is_local_same_origin({'Host': '127.0.0.1:8747'}) is True


@case('Host 不是回环地址就挡掉（DNS rebinding）')
def _():
    for host in ('panel.example.com', 'attacker.test:8747', '192.168.1.20:8747', ''):
        assert is_local_same_origin({'Host': host}) is False, host


@case('allow_write=false 时，白名单里的命令也不许发')
def _():
    ok, detail = _channel(False).send_action('WINKEY_LOCK')
    assert ok is False, detail
    assert 'allow_write' in detail, detail


@case('allow_write=true 时才放行，且只发白名单里的')
def _():
    ch = _channel(True)
    ok, detail = ch.send_action('WINKEY_UNLOCK')
    assert ok is True, detail
    topic, payload = ch._outq.get_nowait()
    assert topic == 'Setting/Control', topic
    assert '"WINKEY_UNLOCK"' in payload, payload
    assert 'Action' in payload


@case('不在白名单里的命令一律拒发（哪怕 allow_write 开着）')
def _():
    ok, detail = _channel(True).send_action('FORMAT_C_DRIVE')
    assert ok is False
    assert '白名单' in detail, detail


@case('通道没连上时不许假装发出去了')
def _():
    ch = _channel(True)
    ch.alive = False
    ok, detail = ch.send_action('WINKEY_LOCK')
    assert ok is False
    assert '未连接' in detail, detail


@case('写能力一律从 unsupported 起步，连上并验证过才许点亮')
def _():
    ch = _channel(True)
    ch.alive = False
    # 面板按 caps 决定要不要画按钮，所以「没连上」必须等于「画不出按钮」
    for cap in ('winkey.write', 'battery.mode.write', 'mode.write'):
        assert ch.caps.get(cap) == 'unsupported', (cap, ch.caps)


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
