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
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import CAP_FAN_CURVE, CAP_FAN_CURVE_WRITE   # noqa: E402
from app.act.channels import mqtt_gcu                                  # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel, STATUS_TOPICS       # noqa: E402

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


def _curve_ok(duties, name='M3T1'):
    points = [{'id': i, 'up_t': 255 if i >= 11 else (0 if i == 0 else 50 + i),
               'down_t': 255 if i >= 11 else 48 + i, 'duty_pct': float(d),
               'duty_raw': d * 2, 'sentinel': i >= 11, 'mailbox': False}
              for i, d in enumerate(duties)]
    return {'ok': True, 'name': name,
            'tables': {'CPU': points, 'GPU': [dict(p) for p in points]}}


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
