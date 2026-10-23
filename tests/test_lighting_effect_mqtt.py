# -*- coding: utf-8 -*-
"""键盘灯效 SetEffectALL 的报文编排（不连硬件）。

    runtime\\python.exe tests\\test_lighting_effect_mqtt.py

契约来自 2026-10-20 在 Creator Center 点「波浪」时抓到的报文
（logs/mqtt-capture-20261020-205150.jsonl），并用 tools/lighting_effect_test.py
做过呼吸→波浪→呼吸的可逆验证。这里盯四件事：
  * 报文形状一个字段不少（Mode/Effect/Light/Speed/Direction/nv_save/7 色块/那些 null）；
  * 波浪必须给方向，闪烁可给无/左/右，其它灯效固定发 None，与 Creator Center 一致；
  * Welcome 是第二条报文，除了 mode 之外与 Lighting 完全相同；
  * 闸门（allow_write / verified / 值域）在执行层，不在 UI。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import (ALL_CAPS, CAP_LIGHTING_EFFECT_WRITE)     # noqa: E402
from app.act.channels.mqtt_gcu import (MqttChannel, _kb_colors,            # noqa: E402
                                       LIGHTING_EFFECTS)

CASES = []
COLORS = [[255, 255, 0]] + [[0, 255, 255]] * 6


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class FakeCfg:
    def __init__(self, allow_write=True):
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


def _channel(allow_write=True):
    ch = MqttChannel(FakeCfg(allow_write), FakeLog())
    ch.alive = True
    ch.caps[CAP_LIGHTING_EFFECT_WRITE] = 'verified'
    return ch


def _record(ch):
    calls = []

    def send(action, extra=None, note=''):
        calls.append((action, dict(extra or {})))
        return True, '已记录'

    ch.send_action = send
    return calls


@case('新能力登记在 ALL_CAPS，白名单动作形状正确')
def t_caps():
    assert CAP_LIGHTING_EFFECT_WRITE in ALL_CAPS
    ch = MqttChannel(FakeCfg(), FakeLog())
    spec = ch.actions['actions']['SET_KEYBOARD_EFFECT']
    assert spec['cap'] == CAP_LIGHTING_EFFECT_WRITE, spec
    assert spec['topic'] == 'keyboard_ctrl', spec
    assert spec['field'] == 'function' and spec['value'] == 'SetEffectALL', spec


@case('呼吸灯报文：形状与 Creator Center 抓到的一致')
def t_payload_shape():
    ch = _channel()
    calls = _record(ch)
    ok, detail = ch.set_keyboard_effect('Breathing', 3, 2, COLORS)
    assert ok, detail
    assert len(calls) == 1, calls
    action, extra = calls[0]
    assert action == 'SET_KEYBOARD_EFFECT', action
    assert 'function' not in extra, 'function 由 send_action 拼，不能出现在 extra 里'
    assert extra['mode'] == 'Lighting'
    assert extra['effect'] == 'Breathing'
    assert extra['light'] == '3' and extra['speed'] == '2'
    assert extra['direction'] == 'None', extra['direction']
    assert extra['nv_save'] == '0'
    assert extra['backgroundcolor'] == 0 and extra['alphabet'] == ''
    assert extra['solution'] == 0 and extra['level'] == 0
    assert extra['powerstatus'] == 0 and extra['AuroraSwitch'] is False and extra['type'] == 0
    for k in ('MonochromeIndex', 'ManualIndex1', 'ManualIndex2', 'ManualIndex3',
              'ManualIndex4', 'ManualIndex5', 'ManualIndex6', 'ManualInterval',
              'BreathingIndex', 'DefaultData'):
        assert k in extra and extra[k] is None, (k, extra.get(k))
    color = extra['color']
    assert color['isCircular'] is True and color['ColorBlocks'] == 7, color
    buf = color['ColorBuffer']
    assert [c['ID'] for c in buf] == list(range(7)), buf
    assert [[c['R'], c['G'], c['B']] for c in buf] == COLORS, buf


@case('波浪：方向必须给，LeftRight/RightLeft 都收')
def t_wave_direction():
    ch = _channel()
    calls = _record(ch)
    ok, _ = ch.set_keyboard_effect('Wave', 3, 2, COLORS, direction='RightLeft')
    assert ok
    assert calls[0][1]['direction'] == 'RightLeft', calls
    ok, detail = ch.set_keyboard_effect('Wave', 3, 2, COLORS, direction='None')
    assert not ok and 'LeftRight/RightLeft' in detail, detail
    assert len(calls) == 1, calls


@case('非波浪灯效忽略方向，固定发 None')
def t_nonwave_direction():
    ch = _channel()
    calls = _record(ch)
    ok, _ = ch.set_keyboard_effect('Rainbow', 4, 0, COLORS, direction='RightLeft')
    assert ok
    assert calls[0][1]['direction'] == 'None', calls


@case('闪烁支持无/左/右方向；其它非波浪仍然固定 None')
def t_flash_direction():
    ch = _channel()
    calls = _record(ch)
    ok, detail = ch.set_keyboard_effect('Flash', 3, 2, COLORS, direction='RightLeft')
    assert ok, detail
    assert calls[-1][1]['direction'] == 'RightLeft', calls
    ok, detail = ch.set_keyboard_effect('Flash', 3, 2, COLORS, direction='None')
    assert ok, detail
    assert calls[-1][1]['direction'] == 'None', calls
    ok, detail = ch.set_keyboard_effect('Flash', 3, 2, COLORS, direction='Bogus')
    assert not ok and 'None/LeftRight/RightLeft' in detail, detail
    ok, detail = ch.set_keyboard_effect('Mix', 3, 2, COLORS, direction='RightLeft')
    assert ok, detail
    assert calls[-1][1]['direction'] == 'None', calls


@case('Welcome 是第二条报文，除 mode 外完全相同')
def t_welcome():
    ch = _channel()
    calls = _record(ch)
    ok, detail = ch.set_keyboard_effect('Breathing', 3, 2, COLORS, welcome=True)
    assert ok, detail
    assert len(calls) == 2, calls
    (a1, e1), (a2, e2) = calls
    assert a1 == a2 == 'SET_KEYBOARD_EFFECT', calls
    assert e1['mode'] == 'Lighting' and e2['mode'] == 'Welcome', calls
    e1 = dict(e1, mode='X')
    e2 = dict(e2, mode='X')
    assert e1 == e2, '两条报文只允许 mode 不同'


@case('灯效名、亮度/速度范围、色块数量与分量都在执行层校验')
def t_validation():
    ch = _channel()
    calls = _record(ch)
    assert not ch.set_keyboard_effect('Disco', 3, 2, COLORS)[0]
    for bad in (-1, 5):
        assert not ch.set_keyboard_effect('Breathing', bad, 2, COLORS)[0]
        assert not ch.set_keyboard_effect('Breathing', 3, bad, COLORS)[0]
    assert not ch.set_keyboard_effect('Breathing', 3, 2, COLORS[:6])[0]
    bad = [list(c) for c in COLORS]
    bad[0][0] = 256
    assert not ch.set_keyboard_effect('Breathing', 3, 2, bad)[0]
    assert calls == [], calls


@case('七个灯效都在允许列表里')
def t_effects():
    assert LIGHTING_EFFECTS == ('Single', 'Breathing', 'Wave', 'Rainbow',
                                'Mix', 'Flash', 'Music'), LIGHTING_EFFECTS


@case('allow_write=false 时拒发')
def t_allow_write():
    ch = _channel(allow_write=False)
    calls = _record(ch)
    ok, detail = ch.set_keyboard_effect('Breathing', 3, 2, COLORS)
    assert not ok and 'allow_write' in detail, detail
    assert calls == [], calls


@case('能力没 verified 时拒发（不是只在 UI 挡）')
def t_gate():
    ch = _channel()
    ch.caps[CAP_LIGHTING_EFFECT_WRITE] = 'unknown'
    calls = _record(ch)
    ok, detail = ch.set_keyboard_effect('Breathing', 3, 2, COLORS)
    assert not ok and 'lighting.effect.write' in detail, detail
    assert calls == [], calls


@case('OEM 快照暴露方向与 rkgcolor 颜色')
def t_snapshot():
    ch = MqttChannel(FakeCfg(), FakeLog())
    ch._payloads['Setting/Status'] = {'ts': 1.0, 'data': {'WinKey': 'WINKEY_STATUS_UNLOCK'}}
    ch._payloads['Fan/Status'] = {'ts': 1.0, 'data': {'CPU_PL1': '75'}}
    ch._payloads['Keyboard/Status'] = {'ts': 1.0, 'data': {
        'powerStatus': 'On', 'effect': '2', 'speed': '5', 'light': '3',
        'direction': '0',
        'rkgcolor': json.dumps({'isCircular': True, 'ColorBlocks': 7, 'ColorBuffer': [
            {'ID': i, 'R': r, 'G': g, 'B': b} for i, (r, g, b) in enumerate(COLORS)]}),
    }}
    oem = ch._oem_switches(ch.payloads())
    assert oem['kb_effect'] == '2', oem
    assert oem['kb_speed'] == '5', oem
    assert oem['kb_light'] == '3', oem
    assert oem['kb_direction'] == '0', oem
    assert oem['kb_colors'] == COLORS, oem['kb_colors']


@case('rkgcolor 坏掉时颜色报 None，不抛异常')
def t_bad_colors():
    assert _kb_colors({'rkgcolor': '{not json'}) is None
    assert _kb_colors({'rkgcolor': json.dumps({'ColorBuffer': [{'R': 1}]})}) is None
    assert _kb_colors(None) is None


@case('灯效后补一条幂等 SetPower 刷新 Keyboard/Status，且不顺手改背光')
def t_status_refresh():
    ch = _channel()
    ch._payloads['Keyboard/Status'] = {'ts': 1.0, 'data': {'powerStatus': 'On'}}
    calls = _record(ch)
    ok, detail = ch.set_keyboard_effect('Wave', 3, 2, COLORS, direction='LeftRight')
    assert ok, detail
    assert [c[0] for c in calls] == ['SET_KEYBOARD_EFFECT', 'KB_POWER_ON'], calls
    assert '状态已回读' in detail, detail

    ch2 = _channel()
    ch2._payloads['Keyboard/Status'] = {'ts': 1.0, 'data': {'powerStatus': 'Off'}}
    calls2 = _record(ch2)
    ok2, detail2 = ch2.set_keyboard_effect('Breathing', 3, 2, COLORS)
    assert ok2, detail2
    assert [c[0] for c in calls2] == ['SET_KEYBOARD_EFFECT', 'KB_POWER_OFF'], calls2
    assert '状态已回读' in detail2, detail2


@case('外部 SetEffectALL 自动补一次刷新；自己发的不重复刷新')
def t_external_effect_refresh():
    ch = _channel()
    calls = []
    ch.refresh_keyboard_status = lambda: calls.append('refresh') or (True, '')
    ch._last_effect_ts = 0.0
    ch._ingest('Keyboard/Ctrl', json.dumps({'function': 'SetEffectALL', 'effect': 'Wave'}).encode())
    assert calls == ['refresh'], calls
    calls.clear()
    ch._last_effect_ts = time.time()
    ch._ingest('Keyboard/Ctrl', json.dumps({'function': 'SetEffectALL', 'effect': 'Wave'}).encode())
    assert calls == [], '自己的灯效命令被当成外部改动'


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
