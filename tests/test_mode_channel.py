# -*- coding: utf-8 -*-
"""档位写入通道的用例：钉死「谁是输入、谁是输出」这条刚被实测纠正过来的方向。

2026-09-30 12:19-12:24 在本机做的可逆验证（README 6.11 十五）：

  1. 面板写 EC `0x0751 = 0x10`（Turbo_Mode），回读一致 —— **PL1 死守 10 W，
     100 秒 52 个采样点一次没动**。唯一可见效果是键盘那颗实体灯亮了。
  2. 改发 GCUBridge 的 `OPERATING_TURBO_MODE` —— 约 24 秒后 PL1 从 10 爬到 75，
     并且**是服务自己**把 `0x0751` 写成 0x10 的。
  3. 发 `OPERATING_OFFICE_MODE` —— PL1 掉回 10，`0x0751` 被服务改回 0xA0。
  4. 再发 TURBO —— 回到 75。往返都改到真东西、都能还原。

结论：`0x0751` 是服务 `SetUserProfile` 的**输出**，不是输入。
所以这几条规矩必须有用例盯着，否则迟早漂回去：
  * 档位按钮只能走 GCUBridge 的 OPERATING_*，EC 通道不许写档位；
  * 自动跟随不许写那个字节（它只会把物理指示灯拨到和真实功耗墙不符的状态）；
  * 按钮清单由后端给，且只含本机验过的档位（不许有点了报错的死按钮）；
  * 词表沿用调度那套（省电/均衡/流畅/性能），不许再用「办公/狂暴」——
    机主明确说过本机 Creator Center 没有这三档。
"""
import inspect
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import (MODES, MODE_LABELS, MODE_VERIFIED,  # noqa: E402
                                   HW_MODE_BY_FAN_FLAG)
from app.act.channels import ec_gpd, mqtt_gcu                        # noqa: E402
from app.config import DEFAULTS                                      # noqa: E402
from app.policy.scheduler import TIER_LABELS                         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case('EC 通道不写档位：每个 OEM 档都硬拒')
def t_ec_refuses_mode():
    for m in MODES:
        ok, detail = ec_gpd.EcChannel.set_mode(None, m)
        assert ok is False, '%s 竟然允许 EC 写档位' % m
        assert '输出' in detail, '%s 的拒绝理由没说清字节是服务的输出：%s' % (m, detail)


@case('自动跟随不许写档位字节（只改灯、不改墙，会把灯骗人）')
def t_auto_follow_blocked():
    src = inspect.getsource(ec_gpd.EcChannel.set_fan_mode)
    guard = src.find('if not who:')
    assert guard > 0, 'set_fan_mode 里没有 who 为空的硬拦'
    # 硬拦必须落在真正动手之前：在取地址、更在 dev.write 之前
    assert guard < src.find('self.addr(FAN_KEY)'), '硬拦位置太靠后'
    assert guard < src.find('self.dev.write'), '硬拦位置太靠后'


@case('fan_follow_tier 默认关掉')
def t_follow_default_off():
    assert DEFAULTS['hardware']['ec']['fan_follow_tier'] is False, \
        '自动跟随默认还开着，会继续拿指示灯骗人'


@case('档位按钮清单：验过的都有动作名，没验过的不暴露')
def t_mode_options_are_actionable():
    actions = mqtt_gcu.MODE_ACTION
    for m in MODE_VERIFIED:
        assert m in actions, '验过的档位 %s 没有动作名，上不了按钮' % m
        assert actions[m].startswith('OPERATING_'), actions[m]
    assert set(actions) <= set(MODE_VERIFIED), \
        '动作表里出现了未验证档位：%s' % (set(actions) - set(MODE_VERIFIED))


@case('档位标签沿用调度词表，不许另造第三套说法')
def t_labels_use_one_vocabulary():
    words = set(TIER_LABELS.values())
    for m, label in MODE_LABELS.items():
        if m == 'unknown':
            continue
        head = label.split('（')[0]
        assert head in words, '%s 的标签 %r 不在调度词表里' % (m, label)


@case('本机 Creator Center 没有「办公/均衡/狂暴」，这三个词不许当本机模式')
def t_no_oem_words_as_machine_modes():
    for banned in ('办公', '狂暴'):
        assert banned not in ' '.join(MODE_LABELS.values()), banned
    assert 'OperatingMode' not in str(HW_MODE_BY_FAN_FLAG)


@case('MQTT 通道的 mode.write 已经是 verified，且带原因和按钮清单')
def t_cap_verified():
    src = inspect.getsource(mqtt_gcu.MqttChannel._session)
    assert "self.caps[CAP_MODE_WRITE] = 'verified'" in src
    assert 'mode_options' in src, '没发布按钮清单，前端只能写死档位'
    assert 'MODE_VERIFIED' in src, '清单没按已验证档位过滤'


@case('前端不许再硬编码档位清单')
def t_frontend_no_hardcoded_modes():
    js = io.open(os.path.join(ROOT, 'app', 'web', 'app.js'), encoding='utf-8').read()
    assert "['office', 'balance', 'turbo']" not in js, '前端还在自己列档位'
    assert 'mode_options' in js, '前端没改用后端给的档位清单'


@case('档位字节只用来读回模式，认不出来的不猜')
def t_byte_still_read_only_mapping():
    from app.act.channels.base import hw_mode_of_fan_flag
    assert hw_mode_of_fan_flag('Turbo_Mode') == 'perf'
    # 0x80 位那一族（Mode/HiMode/Level1~5）全是低功耗档，读成 eco
    assert hw_mode_of_fan_flag('User_Fan_HiMode') == 'eco'
    assert hw_mode_of_fan_flag('Nonsense') is None
    assert hw_mode_of_fan_flag(None) is None


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print('  OK   %s' % name)
        except AssertionError as e:
            bad += 1
            print('  FAIL %s —— %s' % (name, e))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
