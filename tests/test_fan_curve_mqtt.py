# -*- coding: utf-8 -*-
"""MQTT 风扇曲线只读通道的用例。

    runtime\\python.exe tests\\test_fan_curve_mqtt.py

对应 notes/creator-center-features.md 第 3 节：
  * 应答主题是 `Fan/Table`（不是 `Fan/Status`），不订阅就永远等不到；
  * GET 是只读，`allow_write=false` 也必须能发；
  * 服务发来的是**内存里的表**，形状对不上（不是 16 点）就不许把能力点亮；
  * 写入动作 `SET_FAN_CURVE` 先挂白名单，但能力仍是 `fan.curve.write`（未验证），
    三道闸必须继续挡住它。
"""
import io
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import CAP_FAN_CURVE, CAP_FAN_CURVE_WRITE   # noqa: E402
from app.act.channels import mqtt_gcu                                  # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel, STATUS_TOPICS       # noqa: E402
from app.act.hardware import Hardware                                  # noqa: E402
from app.server.httpd import make_handler                              # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class FakeCfg:
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


def _channel(allow_write=False):
    ch = MqttChannel(FakeCfg(allow_write), FakeLog())
    ch.alive = True
    return ch


def _table(name='M3T1'):
    """本机 2026-09-18 抓包里的 M3T1（CPU 点 10 起 UpT=255，点 15 是死点）。"""
    up = [0, 53, 57, 59, 61, 63, 65, 67, 75, 80, 83, 255, 255, 255, 255, 255]
    down = [48, 50, 58, 60, 62, 64, 66, 69, 79, 82, 255, 255, 255, 255, 255, 255]
    duty = [0, 30, 30, 35, 45, 48, 50, 55, 70, 85, 100, 100, 100, 100, 100, 100]
    rows = [{'ID': i, 'UpT': up[i], 'DownT': down[i], 'Duty': duty[i]}
            for i in range(16)]
    return {'Activated': False, 'Name': name, 'FanControlRespective': False,
            'CpuTemp_DefaultMaxLevel': 11, 'GpuTemp_DefaultMaxLevel': 11,
            'CPU': rows, 'GPU': [dict(r) for r in rows]}


@case('Fan/Table 在订阅清单里：只问 Fan/Control 是收不到曲线的')
def t_subscribed():
    assert 'Fan/Table' in STATUS_TOPICS, STATUS_TOPICS


@case('收到完整表：能力点成 verified，16 点按原值归一化')
def t_ingest():
    ch = _channel()
    assert ch.caps[CAP_FAN_CURVE] == 'unsupported'
    ch._ingest('Fan/Table', json.dumps(_table()).encode('utf-8'))
    assert ch.caps[CAP_FAN_CURVE] == 'verified', ch.caps
    out = ch.fan_curve(wait_s=0)          # 用刚灌进去的缓存，不再等
    assert out['ok'] is True, out
    assert out['name'] == 'M3T1', out
    assert len(out['tables']['CPU']) == 16, out
    p15 = out['tables']['CPU'][15]
    assert p15['up_t'] == 255 and p15['sentinel'] is True, p15
    assert p15['duty_pct'] == 100.0 and p15['duty_raw'] == 200, p15
    assert out['tables']['CPU'][1]['duty_pct'] == 30.0


@case('形状不对（15 点）不点亮能力，读数 ok=false')
def t_short_table():
    ch = _channel()
    bad = _table()
    bad['CPU'] = bad['CPU'][:15]
    ch._ingest('Fan/Table', json.dumps(bad).encode('utf-8'))
    assert ch.caps[CAP_FAN_CURVE] != 'verified', ch.caps
    out = ch.fan_curve(wait_s=0)
    assert out['ok'] is False, out


@case('GET 是只读：allow_write=false 也照发，报文形状固定')
def t_request_without_write_permission():
    ch = _channel(allow_write=False)
    ok, detail = ch.request_fan_curve()
    assert ok is True, detail
    topic, payload = ch._outq.get_nowait()
    assert topic == 'Fan/Control', topic
    assert json.loads(payload) == {'Action': 'GET_FAN_SPEED_CURVE_SETTING'}, payload


@case('等不到新应答：退回旧缓存并标 stale，不冒充这次读到的')
def t_stale_fallback():
    ch = _channel()
    ch._ingest('Fan/Table', json.dumps(_table()).encode('utf-8'))
    out = ch.fan_curve(wait_s=0)          # GET 之后没有新包
    assert out['ok'] is True and out['stale'] is True, out
    assert out['age_s'] >= 0, out


@case('没有任何 Fan/Table：明确报失败，不返回空表')
def t_no_payload():
    ch = _channel()
    out = ch.fan_curve(wait_s=0)
    assert out['ok'] is False, out
    assert 'Fan/Table' in out['reason'], out


@case('未连接/未验证时，写入动作仍被能力闸拦住')
def t_write_still_gated():
    ch = _channel(allow_write=True)
    spec = ch.actions['actions']['SET_FAN_CURVE']
    assert spec['value'] == 'SET_FAN_SPEED_CURVE_SETTING', spec
    assert spec['cap'] == CAP_FAN_CURVE_WRITE, spec
    assert ch.caps.get(CAP_FAN_CURVE_WRITE) != 'verified', ch.caps
    extra = {'Name': 'M3T1', 'Type': 'CPU'}
    extra.update({'T%d' % i: 50 for i in range(16)})
    ok, detail = ch.send_action('SET_FAN_CURVE', extra=extra)
    assert ok is False, detail
    assert '还没做过可逆验证' in detail, detail
    assert ch._outq.empty(), '被拒的命令不许进发送队列'


def _curve_ok(duties, name='M3T1', stale=False):
    points = [{'id': i, 'up_t': 255 if i >= 11 else (0 if i == 0 else 50 + i),
               'down_t': 255 if i >= 11 else 48 + i, 'duty_pct': float(d),
               'duty_raw': d * 2, 'sentinel': i >= 11, 'mailbox': False}
              for i, d in enumerate(duties)]
    return {'ok': True, 'name': name, 'stale': stale,
            'tables': {'CPU': points, 'GPU': [dict(p) for p in points]}}


def _hardware(ch):
    """不构造真实硬件通道，只测试 Hardware 的转发。"""
    hw = Hardware.__new__(Hardware)
    hw.channels = [ch]
    return hw


def _post_curve(body, hw):
    """直接调用 HTTP handler，用内存流代替连接，不启动 HTTP 服务。"""
    raw = json.dumps(body).encode('utf-8')
    handler_type = make_handler(SimpleNamespace(hw=hw), None, None)
    handler = handler_type.__new__(handler_type)
    handler.path = '/api/fan-curve'
    handler.headers = {'Host': '127.0.0.1:8747',
                       'Origin': 'http://127.0.0.1:8747',
                       'Content-Length': str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    responses = []
    handler._send = lambda code, payload: responses.append((code, payload))
    handler.do_POST()
    assert len(responses) == 1, responses
    return responses[0]


def _prepare_write_channel():
    ch = _channel(allow_write=True)
    ch.caps[CAP_FAN_CURVE_WRITE] = 'verified'
    ch.caps[CAP_FAN_CURVE] = 'verified'
    ch._status_fresh = lambda wait_s=0: {'FAN_SafetyProtect': '0'}
    return ch


def _sent_set(ch):
    """看一眼队列里的 SET（不取出——后面还要断言它发到了哪）。"""
    for topic, payload in list(ch._outq.queue):
        data = json.loads(payload)
        if data.get('Action') == 'SET_FAN_SPEED_CURVE_SETTING':
            return topic, data
    return None, None


@case('写曲线成功：发一条 SET、16 点给齐，回读一致才说 ok')
def t_write_ok():
    ch = _prepare_write_channel()
    calls = {'n': 0}

    def fake_curve(wait_s=0):
        calls['n'] += 1
        if calls['n'] == 1:
            return _curve_ok([100] * 16)
        topic, data = _sent_set(ch)
        duties = [data['T%d' % i] for i in range(16)] if data else [100] * 16
        return _curve_ok(duties)

    ch.fan_curve = fake_curve
    ok, detail = ch.set_fan_curve([90] * 16)
    assert ok is True, detail
    assert '回读一致' in detail, detail
    topic, data = _sent_set(ch)
    assert topic == 'Fan/Control', topic
    assert data['Name'] == 'M3T1' and data['Type'] == 'CPU', data
    assert [data['T%d' % i] for i in range(16)] == [90] * 16, data


@case('高温保护置位：拒绝下发，队列里一条 SET 都没有')
def t_write_safety_protect():
    ch = _prepare_write_channel()
    ch.fan_curve = lambda wait_s=0: _curve_ok([100] * 16)
    ch._status_fresh = lambda wait_s=0: {'FAN_SafetyProtect': '1'}
    ok, detail = ch.set_fan_curve([90] * 16)
    assert ok is False and '高温保护' in detail, detail
    _topic, data = _sent_set(ch)
    assert data is None, data


@case('缺一个点 / 越界 / 非 CPU-GPU：都在发之前拒掉')
def t_write_validation():
    ch = _prepare_write_channel()
    ch.fan_curve = lambda wait_s=0: _curve_ok([100] * 16)
    for duties, type_ in (([50] * 15, 'CPU'), ([50] * 17, 'CPU'),
                          ([-1] + [50] * 15, 'CPU'), ([101] + [50] * 15, 'CPU'),
                          ([50] * 16, 'FAN')):
        ok, detail = ch.set_fan_curve(duties, type_)
        assert ok is False, (duties, type_, detail)
    _topic, data = _sent_set(ch)
    assert data is None, data


@case('服务没报表名：拒绝写（绝不写死 M3T1）')
def t_write_needs_name():
    ch = _prepare_write_channel()
    ch.fan_curve = lambda wait_s=0: _curve_ok([100] * 16, name='')
    ok, detail = ch.set_fan_curve([90] * 16)
    assert ok is False and '名字' in detail, detail
    _topic, data = _sent_set(ch)
    assert data is None, data


@case('硬件转发 expected_name，同时保留旧工具不传参数的调用')
def t_hardware_expected_name():
    calls = []

    def record(duties, type_='CPU', expected_name=None):
        calls.append((duties, type_, expected_name))
        return True, '已记录'

    hw = _hardware(SimpleNamespace(alive=True, set_fan_curve=record))
    duties = [90] * 16
    assert hw.set_fan_curve(duties) == (True, '已记录')
    assert hw.set_fan_curve(duties, 'GPU') == (True, '已记录')
    assert hw.set_fan_curve(duties, 'GPU', expected_name='M2T1') == (True, '已记录')
    assert calls == [(duties, 'CPU', None), (duties, 'GPU', None),
                     (duties, 'GPU', 'M2T1')], calls
    assert all(call[0] is duties for call in calls), calls


@case('HTTP 表名匹配：经硬件层只发一条 SET，CPU 默认值与 GPU 都可回读')
def t_http_write_expected_name_matches():
    for type_ in ('CPU', 'GPU'):
        ch = _prepare_write_channel()
        reads = []

        def fake_curve(wait_s=0):
            reads.append(wait_s)
            _topic, data = _sent_set(ch)
            duties = [data['T%d' % i] for i in range(16)] if data else [100] * 16
            return _curve_ok(duties, name='M2T1')

        ch.fan_curve = fake_curve
        body = {'duties': [90] * 16, 'expected_name': 'M2T1'}
        if type_ == 'GPU':
            body['type'] = type_
        code, payload = _post_curve(body, _hardware(ch))
        assert code == 200 and payload['ok'] is True, (code, payload)
        assert '回读一致' in payload['detail'], payload
        topic, data = _sent_set(ch)
        assert topic == 'Fan/Control', topic
        assert data['Name'] == 'M2T1' and data['Type'] == type_, data
        assert [data['T%d' % i] for i in range(16)] == body['duties'], data
        sets = [json.loads(p) for _t, p in list(ch._outq.queue)
                if json.loads(p).get('Action') == 'SET_FAN_SPEED_CURVE_SETTING']
        assert len(sets) == 1, sets
        assert reads == [2.0, 2.5], reads


@case('HTTP 草稿来自旧表：fresh 读取表名已变化，409 且任何写命令之前拒发')
def t_http_write_name_changed():
    ch = _prepare_write_channel()
    tables = iter((_curve_ok([100] * 16, name='M3T1'),
                   _curve_ok([100] * 16, name='M2T1')))
    reads, status_reads = [], []

    def fake_curve(wait_s=0):
        reads.append(wait_s)
        return next(tables, _curve_ok([100] * 16, name='M2T1'))

    ch.fan_curve = fake_curve
    ch._status_fresh = lambda wait_s=0: (
        status_reads.append(wait_s) or {'FAN_SafetyProtect': '0'})
    draft = ch.fan_curve(wait_s=0)
    duties = [int(p['duty_pct']) for p in draft['tables']['CPU']]
    duties[1] = 40
    code, payload = _post_curve({'duties': duties, 'type': 'CPU',
                                 'expected_name': draft['name']}, _hardware(ch))
    assert code == 409 and payload['ok'] is False, (code, payload)
    assert 'M3T1' in payload['detail'] and 'M2T1' in payload['detail'], payload
    assert reads == [0, 2.0], reads
    assert status_reads == [], status_reads
    assert ch._outq.empty(), '表名变化时不许把任何写命令放进队列'


@case('fresh 读取退回 stale：表名匹配或旧调用没传 expected_name，都必须拒发')
def t_write_stale():
    for expected_name in (None, 'M3T1'):
        ch = _prepare_write_channel()
        reads, status_reads = [], []

        def fake_curve(wait_s=0):
            reads.append(wait_s)
            return _curve_ok([100] * 16, stale=True)

        ch.fan_curve = fake_curve
        ch._status_fresh = lambda wait_s=0: (
            status_reads.append(wait_s) or {'FAN_SafetyProtect': '0'})
        if expected_name is None:
            ok, detail = ch.set_fan_curve([90] * 16)
        else:
            ok, detail = ch.set_fan_curve([90] * 16, expected_name=expected_name)
        assert ok is False and 'stale' in detail, (expected_name, ok, detail)
        assert reads == [2.0], reads
        assert status_reads == [], status_reads
        assert ch._outq.empty(), 'stale 表不许触发任何写命令'


@case('HTTP 必须提供非空字符串 expected_name：缺失、空白与非字符串均 400')
def t_http_write_requires_expected_name():
    calls = []

    def record(duties, type_='CPU', expected_name=None):
        calls.append((duties, type_, expected_name))
        return True, '已记录'

    hw = _hardware(SimpleNamespace(alive=True, set_fan_curve=record))
    bodies = [{'duties': [90] * 16}]
    bodies.extend({'duties': [90] * 16, 'expected_name': bad}
                  for bad in ('', ' \t\n', None, True, 123, [], {}))
    for body in bodies:
        code, payload = _post_curve(body, hw)
        assert code == 400 and payload['ok'] is False, (body, code, payload)
        assert 'expected_name' in payload['detail'], payload
        assert calls == [], 'HTTP 参数无效时不许调用硬件层'


@case('默认表 GET 仅调用本地只读加载器，不访问硬件通道')
def t_default_get():
    for query, name in (('name=M3T1', 'M3T1'), ('name=M2T5', 'M2T5'),
                        ('name=M3T1&name=M1T1', None), ('', None),
                        ('name=..%2Fconfig', '../config')):
        handler_type = make_handler(None, None, None)
        handler = handler_type.__new__(handler_type)
        handler.path = '/api/fan-curve/defaults?' + query
        responses = []
        handler._send = lambda code, payload: responses.append((code, payload))
        for ok in (True, False):
            response = {'ok': ok, 'reason': '' if ok else '默认表不可用'}
            with patch('app.server.httpd.load_fan_defaults', return_value=response) as loader:
                handler.do_GET()
                loader.assert_called_once_with(name)
            assert responses[-1] == (200 if ok else 409, response), responses


@case('订阅清单包含 Creator Center 的控制主题：抓包才听得到命令')
def t_subscribe_control_topics():
    for topic in ('Fan/Control', 'Setting/Control', 'Keyboard/Ctrl',
                  'MyRgbLightbar/Control', 'BatteryProtection/Control', 'Display/Control'):
        assert topic in mqtt_gcu.SUBSCRIBE_TOPICS, topic
    assert 'Fan/Table' in mqtt_gcu.SUBSCRIBE_TOPICS


@case('抓包：只记录、能停、文件里是 topic+payload，且不需要 allow_write')
def t_capture():
    tmp = tempfile.mkdtemp(prefix='umi-cap-')
    old_dir = mqtt_gcu.CAPTURE_DIR
    mqtt_gcu.CAPTURE_DIR = tmp
    try:
        ch = _channel(allow_write=False)
        st = ch.start_capture(1)
        assert st['active'] is True, st
        assert st['path'].startswith(tmp), st
        ch._ingest('Fan/Control', json.dumps({'Action': 'SET_SOMETHING', 'X': 1}).encode())
        st2 = ch.stop_capture()
        assert st2['active'] is False, st2
        assert st2['count'] == 1, st2
        with open(st2['path'], encoding='utf-8') as f:
            lines = [json.loads(x) for x in f if x.strip()]
        assert len(lines) == 1, lines
        assert lines[0]['topic'] == 'Fan/Control', lines
        assert lines[0]['payload']['Action'] == 'SET_SOMETHING', lines
    finally:
        mqtt_gcu.CAPTURE_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


@case('抓包到点自动停：不会一直往盘里写')
def t_capture_expires():
    tmp = tempfile.mkdtemp(prefix='umi-cap-')
    old_dir = mqtt_gcu.CAPTURE_DIR
    mqtt_gcu.CAPTURE_DIR = tmp
    try:
        ch = _channel()
        ch.start_capture(1)
        ch._cap_until = 0            # 模拟时间已过
        st = ch.capture_status()
        assert st['active'] is False, st
        assert ch._cap_fh is None, '到期必须关文件'
    finally:
        mqtt_gcu.CAPTURE_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print('ok   %s' % name)
        except AssertionError as exc:
            failed += 1
            print('FAIL %s: %s' % (name, exc))
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            print('ERR  %s: %r' % (name, exc))
    print('%d/%d 通过' % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
