# -*- coding: utf-8 -*-
"""TCC 偏移与风扇换挡间隔的写入编排（不连硬件）。

    runtime\\python.exe tests\\test_tcc_fan_speed_mqtt.py

对应 2026-10-20 的抓包与可逆验证：
  * OEM 报文是 SET_OPERATING_MODE_DETAIL + CpuTccOffset / CpuTccOffsetSwitch，
    以及 FanSwitchSpeed / FanSwitchSpeedEnabled；
  * 先发值、再发使能位，服务才会应用新值；
  * 上限来自 Fan/Status 的 CPU_TccOffsetMaximum（本机 5）；
  * 换挡间隔按 EC 落点 (ms/100)|0x80 约束为 100~2000、100 的整数倍。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import (ALL_CAPS, CAP_FAN_SWITCH_WRITE,        # noqa: E402
                                   CAP_TCC_WRITE)
from app.act.channels.mqtt_gcu import MqttChannel                          # noqa: E402

CASES = []


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


def _channel():
    ch = MqttChannel(FakeCfg(), FakeLog())
    ch.alive = True
    ch.caps[CAP_TCC_WRITE] = 'verified'
    ch.caps[CAP_FAN_SWITCH_WRITE] = 'verified'
    return ch


def _seed(ch, **overrides):
    fan = {'CPU_TccOffset': '5', 'CPU_TccOffsetSwitch': '0',
           'CPU_TccOffsetMaximum': '5',
           'FAN_FanSwitchSpeed': '300', 'FAN_FanSwitchSpeedEnabled': '0'}
    fan.update(overrides)
    ch._payloads['Fan/Status'] = {'ts': 1.0, 'data': fan}


def _record(ch):
    calls = []

    def send(action, extra=None, note=''):
        calls.append((action, dict(extra or {})))
        return True, '已记录'

    ch.send_action = send
    return calls


@case('新能力登记在 ALL_CAPS 里')
def t_caps():
    assert CAP_TCC_WRITE in ALL_CAPS
    assert CAP_FAN_SWITCH_WRITE in ALL_CAPS


@case('TCC 读状态：值/开关/上限原样来自 Fan/Status')
def t_state():
    ch = _channel()
    _seed(ch)
    assert ch.tcc_state() == {'value': 5, 'enabled': False, 'max': 5}, ch.tcc_state()


@case('TCC 先发值、再发使能位')
def t_order():
    ch = _channel()
    _seed(ch)
    calls = _record(ch)
    ok, _ = ch.set_tcc_offset(0, True)
    assert ok
    assert calls == [('SET_TCC_OFFSET', {'CpuTccOffset': '0'}),
                     ('SET_TCC_SWITCH', {'CpuTccOffsetSwitch': '1'})], calls


@case('TCC 只关开关时不重发值')
def t_disable():
    ch = _channel()
    _seed(ch, CPU_TccOffsetSwitch='1')
    calls = _record(ch)
    ok, _ = ch.set_tcc_offset(enabled=False)
    assert ok
    assert calls == [('SET_TCC_SWITCH', {'CpuTccOffsetSwitch': '0'})], calls


@case('TCC 已开启时改值会补发一次使能位')
def t_reenable():
    ch = _channel()
    _seed(ch, CPU_TccOffsetSwitch='1')
    calls = _record(ch)
    ok, _ = ch.set_tcc_offset(2, True)
    assert ok
    assert calls == [('SET_TCC_OFFSET', {'CpuTccOffset': '2'}),
                     ('SET_TCC_SWITCH', {'CpuTccOffsetSwitch': '1'})], calls


@case('TCC 已经关着也要再发一次开关 0：清掉 EC 里可能残留的使能位')
def t_disable_idempotent():
    ch = _channel()
    _seed(ch, CPU_TccOffsetSwitch='0')
    calls = _record(ch)
    ok, _ = ch.set_tcc_offset(enabled=False)
    assert ok
    assert calls == [('SET_TCC_SWITCH', {'CpuTccOffsetSwitch': '0'})], calls


@case('只改值且当前是关的：先值后开关 0，绝不能把 EC 留在使能态')
def t_value_while_disabled():
    ch = _channel()
    _seed(ch, CPU_TccOffsetSwitch='0')
    calls = _record(ch)
    ok, _ = ch.set_tcc_offset(2)
    assert ok
    assert calls == [('SET_TCC_OFFSET', {'CpuTccOffset': '2'}),
                     ('SET_TCC_SWITCH', {'CpuTccOffsetSwitch': '0'})], calls


@case('TCC 超过 OEM 上限拒发')
def t_tcc_bounds():
    ch = _channel()
    _seed(ch)
    calls = _record(ch)
    ok, detail = ch.set_tcc_offset(6, True)
    assert not ok and '0~5' in detail, detail
    assert calls == [], calls


@case('换挡间隔先发值、再发使能位')
def t_speed_order():
    ch = _channel()
    _seed(ch)
    calls = _record(ch)
    ok, _ = ch.set_fan_switch_speed(500, True)
    assert ok
    assert calls == [('SET_FAN_SWITCH_SPEED', {'FanSwitchSpeed': '500'}),
                     ('SET_FAN_SWITCH_SPEED_ENABLED', {'FanSwitchSpeedEnabled': '1'})], calls


@case('换挡间隔的 100ms 步进与范围')
def t_speed_bounds():
    ch = _channel()
    _seed(ch)
    calls = _record(ch)
    for bad in (0, 150, 2500):
        ok, detail = ch.set_fan_switch_speed(bad, True)
        assert not ok, (bad, detail)
    assert calls == [], calls


@case('换挡已经关着也要再发一次开关 0：清掉 EC 里可能残留的使能位')
def t_speed_disable_idempotent():
    ch = _channel()
    _seed(ch, FAN_FanSwitchSpeedEnabled='0')
    calls = _record(ch)
    ok, _ = ch.set_fan_switch_speed(enabled=False)
    assert ok
    assert calls == [('SET_FAN_SWITCH_SPEED_ENABLED',
                      {'FanSwitchSpeedEnabled': '0'})], calls


@case('Fan/Status 缺字段时拒绝写')
def t_missing():
    ch = _channel()
    ch._payloads['Fan/Status'] = {'ts': 1.0, 'data': {'CPU_PL1': '75'}}
    calls = _record(ch)
    ok, _ = ch.set_tcc_offset(0, True)
    assert not ok
    ok, _ = ch.set_fan_switch_speed(500, True)
    assert not ok
    assert calls == [], calls


@case('能力未 verified 时 send_action 仍然挡住（不是只在 UI 挡）')
def t_gate():
    ch = _channel()
    ch.caps[CAP_TCC_WRITE] = 'unknown'
    _seed(ch)
    ok, detail = ch.set_tcc_offset(0, True)
    assert not ok and 'cpu.tcc.write' in detail, detail
    assert ch._outq.empty(), '被拒绝的命令不应进入发送队列'


@case('OEM 快照暴露 TCC/换挡读数与 TCC 上限')
def t_snapshot_fields():
    ch = _channel()
    _seed(ch, CPU_TccOffset='5', CPU_TccOffsetSwitch='1',
          FAN_FanSwitchSpeed='500', FAN_FanSwitchSpeedEnabled='1')
    oem = ch._oem_switches(ch.payloads())
    assert oem['tcc_offset_c'] == 5
    assert oem['tcc_offset_enabled'] is True
    assert oem['fan_switch_speed_ms'] == 500
    assert oem['fan_switch_speed_enabled'] is True
    assert oem['limits']['tcc_max'] == 5


@case('SPC 只读字段：GPU 目标温度/TGP/DynamicBoost/核心与显存偏移都照 OEM 原值报')
def t_spc_readonly_fields():
    ch = _channel()
    _seed(ch, GPU_TargetTemperature='87', GPU_CoreClockOffset='12',
          GPU_MemoryClockOffset='-34', CPU_OffsetCoreVoltage='0',
          GPU_ConfigurableTGPSwitch='1', GPU_ConfigurableTGPTarget='115',
          GPU_DynamicBoostSwitch='1', GPU_DynamicBoost='5')
    oem = ch._oem_switches(ch.payloads())
    assert oem['gpu_target_temp'] == 87
    assert oem['gpu_core_offset'] == 12
    assert oem['gpu_mem_offset'] == -34
    assert oem['core_voltage_offset'] == 0
    assert oem['tgp_enabled'] is True and oem['tgp_target'] == 115
    assert oem['dynamic_boost_on'] is True and oem['dynamic_boost'] == 5


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
