# -*- coding: utf-8 -*-
"""GCUBridge 上报状态的解析测试（只读那一层，不碰连接、不发消息）。

    runtime\\python.exe tests\\test_oem_status.py

盯的是 2026-09-30 那次观察之后写进面板的 `MqttChannel._oem_switches()`。
最要紧的一条是 UNLOCK/LOCK 那个坑：OEM 的原文是
`WINKEY_STATUS_LOCK` / `WINKEY_STATUS_UNLOCK`，用 endswith('LOCK') 判断的话
「没锁」会被判成「锁着」——面板会显示一个和机器真实状态相反的值，
比不显示更糟。所以这里把每个分支都钉住。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.mqtt_gcu import MqttChannel          # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _payloads(setting=None, bar=None, kb=None, fan=None):
    """按通道真实的存放格式造样本：payloads[topic] = {'data': {...}, 'ts': n}。"""
    out = {}
    for topic, data in (('Setting/Status', setting), ('HidLightbar/Status', bar),
                        ('Keyboard/Status', kb), ('Fan/Status', fan)):
        if data is not None:
            out[topic] = {'data': data, 'ts': 1790000000}
    return out


SETTING_ALL_OFF = {
    'WinKey': 'WINKEY_STATUS_UNLOCK',
    'TouchpadToggle': 'TOUCHPAD_STATUS_OFF',
    'LightBar': 'LIGHTBAR_STATUS_OFF',
    'SingleColorKBBL': 'SINGLE_COLOR_OFF',
    'UsbCharger': 'USB_CHARGER_OFF',
    'OSD': 'OSD_HIDDEN_OFF',
    'FnKey': 'FNKEY_UNLOCK',
    'NumPad': 'NUMPAD_UNLOCK',
    'DiscreteGpuDirectConnectionSwitch_Status': 'MUX_STATUS_OFF',
    'DiscreteGpuDirectConnectionSwitch_Support': 'Support',
    'DGpu': 'AUTO_SELECT',
    'DisplayMode': 'DISPLAY_STANDARD_MODE',
    'AcRecoverySwitch_Support': 'NotSupport',
}


@case('UNLOCK 必须判成「没锁」，不能被 LOCK 后缀骗到')
def _():
    oem = MqttChannel._oem_switches(_payloads(setting=SETTING_ALL_OFF))
    assert oem['win_key_locked'] is False, oem['win_key_locked']
    assert oem['fn_locked'] is False
    assert oem['numpad_locked'] is False
    # 同理：OFF 结尾的字段也不能被 ON 的判定吃掉
    assert oem['lightbar_on'] is False
    assert oem['osd_hidden'] is False


@case('LOCK / ON 原文判成 True')
def _():
    setting = dict(SETTING_ALL_OFF, WinKey='WINKEY_STATUS_LOCK', FnKey='FNKEY_LOCK',
                   NumPad='NUMPAD_LOCK', TouchpadToggle='TOUCHPAD_STATUS_ON',
                   LightBar='LIGHTBAR_STATUS_ON', SingleColorKBBL='SINGLE_COLOR_ON',
                   UsbCharger='USB_CHARGER_ON', OSD='OSD_HIDDEN_ON',
                   DiscreteGpuDirectConnectionSwitch_Status='MUX_STATUS_ON')
    oem = MqttChannel._oem_switches(_payloads(setting=setting))
    for key in ('win_key_locked', 'fn_locked', 'numpad_locked', 'touchpad_on',
                'lightbar_on', 'kb_single_color_on', 'usb_charger_on', 'osd_hidden',
                'mux_on'):
        assert oem[key] is True, (key, oem[key])


@case('空值、None、没有下划线的原文一律 None（面板写「未知」，不猜）')
def _():
    setting = dict(SETTING_ALL_OFF, WinKey=None, TouchpadToggle='',
                   LightBar='SUPPORT', OSD=None)
    oem = MqttChannel._oem_switches(_payloads(setting=setting))
    for key in ('win_key_locked', 'touchpad_on', 'lightbar_on', 'osd_hidden'):
        assert oem[key] is None, (key, oem[key])
    assert oem['_raw']['WinKey'] is None
    assert oem['_raw']['TouchpadToggle'] == ''


@case('Fan/Status 的允许范围要转成整数，字符串数字也算')
def _():
    fan = {'CPU_PL1Minimum': 10, 'CPU_PL1Maximum': '120', 'CPU_PL4Maximum': 165,
           'GPU_ConfigurableTGPMinimum': 80, 'GPU_ConfigurableTGPMaximum': 115,
           'GPU_DynamicBoostMinimum': 5, 'GPU_DynamicBoostMaximum': 15,
           'GPU_TargetTemperatureMinimum': 75, 'GPU_TargetTemperatureMaximum': 87,
           'PowerMode': 1, 'ProfileName': 'Mode3_Profile1', 'FAN_TableName': 'M3T1'}
    oem = MqttChannel._oem_switches(_payloads(setting=SETTING_ALL_OFF, fan=fan))
    lim = oem['limits']
    assert lim == {'pl1_min': 10, 'pl1_max': 120, 'pl4_max': 165, 'tgp_min': 80,
                   'tgp_max': 115, 'boost_min': 5, 'boost_max': 15,
                   'gpu_temp_min': 75, 'gpu_temp_max': 87}, lim
    assert oem['power_mode_raw'] == 1
    assert oem['profile_name'] == 'Mode3_Profile1'
    assert oem['fan_table'] == 'M3T1'


@case('灯条亮度与背光档位数照实带出来')
def _():
    oem = MqttChannel._oem_switches(
        _payloads(setting=SETTING_ALL_OFF, bar={'brightNess': 60},
                  kb={'ACBrightness': 3, 'DCBrightness': 2, 'solution': 'ITE'}))
    assert oem['lightbar_brightness'] == 60
    assert oem['kb_brightness_ac'] == 3 and oem['kb_brightness_dc'] == 2
    assert oem['kb_controller'] == 'ITE'
    assert oem['ts'] == 1790000000


@case('什么都没收到就返回 None：面板该显示「未连接」，而不是一整屏「未知」')
def _():
    assert MqttChannel._oem_switches({}) is None


@case('Support / NotSupport 判成本机是否具备该能力')
def _():
    oem = MqttChannel._oem_switches(_payloads(setting=SETTING_ALL_OFF))
    assert oem['mux_support'] is True
    assert oem['ac_recovery_support'] is False
    setting = dict(SETTING_ALL_OFF, DiscreteGpuDirectConnectionSwitch_Support='NotSupport')
    assert MqttChannel._oem_switches(_payloads(setting=setting))['mux_support'] is False


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
