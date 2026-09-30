# -*- coding: utf-8 -*-
"""写操作的三道闸：来源校验 + allow_write + 能力有没有验过。

    runtime\\python.exe tests\\test_write_gates.py

面板只听 127.0.0.1，但这**不等于**安全：用户浏览器里任何一个网页都能往
http://127.0.0.1:8747/api/action 发 POST（CSRF），而那个接口能下发 OEM 硬件命令、
还能把面板关掉。所以要钉住三件事：
  * 跨站 / 非回环 Host 的写请求一律 403，curl 这类不带 Origin 的照常放行；
  * `MqttChannel.send_action` 必须查 `config.hardware.ec.allow_write`——
    白名单只回答「OEM 认不认这个命令」，不回答「现在准不准发」；
  * 还得查这个动作对应的**能力状态**：没做过可逆验证（不是 verified）就不发。
    前两道闸 2026-09-30 才补上，第三道是同日发现「UI 不点亮 ≠ 执行层不发」之后补的。
"""
import inspect
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import CAP_RGB_WRITE          # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel      # noqa: E402
from app.server.httpd import is_local_same_origin      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def _channel(allow_write, verified=()):
    ch = MqttChannel(FakeCfg(allow_write), FakeLog())
    ch.alive = True                 # 只测闸门，不连 broker
    # 真实通道是连上并验过之后才把 cap 置 verified 的（_session 里），
    # 测试要发命令就得先把这一步补上，否则会被第三道闸拦掉——那正是第三道闸该干的活。
    for cap in verified:
        ch.caps[cap] = 'verified'
    return ch


def _next_keyb(ch):
    """倒出下一条 Keyboard/Ctrl 报文。

    写完命令通道会顺带追问一次状态（Setting/Control 的 GETSTATUS），队列里不止一条，
    所以不能拿「第一条」当命令本身。
    """
    while True:
        topic, payload = ch._outq.get_nowait()
        if topic == 'Keyboard/Ctrl':
            return json.loads(payload)


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
    ch = _channel(True, ('winkey.write',))
    ok, detail = ch.send_action('WINKEY_UNLOCK')
    assert ok is True, detail
    topic, payload = ch._outq.get_nowait()
    assert topic == 'Setting/Control', topic
    assert '"WINKEY_UNLOCK"' in payload, payload
    assert 'Action' in payload


@case('第三道闸：能力没验过就不许发，哪怕白名单里有、allow_write 也开着')
def _():
    ch = _channel(True)                       # 一个 cap 都没标 verified
    for action, cap in (('HEALTHYMODE', 'battery.mode.write'),
                        ('KB_POWER_ON', 'lighting.rgb.write'),
                        ('OPERATING_GAMING_MODE', 'mode.write')):
        assert ch.actions['actions'][action]['cap'] == cap, action
        ok, detail = ch.send_action(action)
        assert ok is False, '%s 居然发出去了：%s' % (action, detail)
        assert '可逆验证' in detail, (action, detail)
        assert ch._outq.empty(), '%s 被拒了却还是把命令塞进了发送队列' % action


@case('标成 verified 之后同一条命令就该放行（闸不是写死的）')
def _():
    ch = _channel(True, ('lighting.rgb.write',))
    ok, detail = ch.send_action('KB_POWER_ON')
    assert ok is True, detail
    data = _next_keyb(ch)
    # OEM 的 Keyboard/Ctrl 用的是 function，不是 Action（读 GCUService 的 switch 确认）
    assert data['function'] == 'SetPower', data
    assert data['powerstatus'] == 1, data
    assert 'Action' not in data, data
    ok2, _ = ch.send_action('KB_POWER_OFF')
    assert ok2 is True
    assert _next_keyb(ch)['powerstatus'] == 0


@case('白名单里每条命令的形状由条目自己声明，代码不许写死')
def _():
    body = inspect.getsource(MqttChannel.send_action)
    assert "spec.get('field'" in body, 'payload 形状又写死成 Action 了'
    assert "spec.get('args'" in body, '固定参数没从白名单取'
    assert "self.caps.get(cap) != 'verified'" in body, '没查能力状态就没第三道闸'


@case('不在白名单里的命令一律拒发（哪怕 allow_write 开着）')
def _():
    ok, detail = _channel(True).send_action('FORMAT_C_DRIVE')
    assert ok is False
    assert '白名单' in detail, detail


@case('通道没连上时不许假装发出去了')
def _():
    ch = _channel(True, ('winkey.write',))
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


@case('背光开关验过之后才升 verified，而且按钮由能力画、不是前端写死')
def t_backlight_promoted():
    src = inspect.getsource(MqttChannel._session)
    assert "self.caps[CAP_RGB_WRITE] = 'verified'" in src, '背光没升上来，面板画不出按钮'
    assert 'rgb_write_reason' in src, '升 verified 却不写证据，后面没人知道是怎么验的'
    js = io.open(os.path.join(ROOT, 'app', 'web', 'app.js'), encoding='utf-8').read()
    assert 'btn-kbpower' in js and 'KB_POWER_ON' in js, '前端没有背光按钮'
    assert "'lighting.rgb.write'" in js, '按钮没按能力画，写死成一个永远亮着的按钮了'


@case('通道里用到的 CAP_*/MODE_* 常量必须真的在命名空间里（漏导入会让整条通道连不上）')
def t_constants_resolve():
    """真实事故：2026-09-30 15:33 给 _session 加了一行 caps[CAP_RGB_WRITE]='verified'
    却没导入这个名字，py_compile 和单测全绿，但 `_session` 一跑就 NameError，
    兜底通道直接连不起来——只有现场看 /api/state 才暴露。这条用例补上这个盲区。"""
    import ast
    import importlib
    for mod_name in ('mqtt_gcu', 'ec_gpd', 'hid_ite8291', 'base'):
        mod = importlib.import_module('app.act.channels.' + mod_name)
        path = os.path.join(ROOT, 'app', 'act', 'channels', '%s.py' % mod_name)
        tree = ast.parse(io.open(path, encoding='utf-8').read())
        used = set(n.id for n in ast.walk(tree) if isinstance(n, ast.Name))
        missing = sorted(u for u in used
                         if u.startswith(('CAP_', 'MODE_', 'HW_MODE')) and not hasattr(mod, u))
        assert not missing, '%s 用了却没定义/导入的常量：%s' % (mod_name, missing)


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
