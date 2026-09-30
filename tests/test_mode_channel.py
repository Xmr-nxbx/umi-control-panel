# -*- coding: utf-8 -*-
"""档位写入通道的用例：钉死「谁是输入、谁是输出」这条刚被实测纠正过来的方向。

2026-09-30 12:19-12:24 在本机做的可逆验证（notes/hardware-channels.md 6.11 十五）：

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
    用户明确说过本机 Creator Center 没有这三档。
"""
import inspect
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act import hardware as hardware_mod                         # noqa: E402
from app.act.channels.base import (MODES, MODE_LABELS, MODE_VERIFIED,  # noqa: E402
                                   HW_MODE_BY_FAN_FLAG)
from app.act.channels import ec_gpd, mqtt_gcu                        # noqa: E402
from app.config import DEFAULTS                                      # noqa: E402
from app.policy.scheduler import TIER_LABELS                          # noqa: E402
from app.tray import fankey                                           # noqa: E402

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


@case('档位命令的回声不许被认成实体按键（#39，实测复现两次）')
def t_echo_window_suppresses_key():
    st = _stub(echo_left=30.0)
    ec_gpd.EcChannel._watch(st)
    assert st.took == [], '回声窗口内还去抢「实体按键」优先锁：%s' % st.took
    assert st.fired == [], '回声窗口内还触发了按键提示（会给用户弹假消息）：%s' % st.fired
    assert st.changes and st.changes[0].get('echo'), '变化本身也该照实记下来'


@case('窗口过期之后，真实按键仍然认得出来')
def t_expired_window_still_detects_key():
    st = _stub(echo_left=0.0)
    ec_gpd.EcChannel._watch(st)
    assert st.took == ['实体按键'], '按键优先锁没抢，说明判定被写死了：%s' % st.took
    assert len(st.fired) == 1, '屏幕提示没响，用户按了键不会有任何反馈'
    assert st.hold_last, '按键前后值没记录'


@case('回声窗口必须大于实测的服务延迟（24~26 秒）')
def t_window_covers_measured_latency():
    assert ec_gpd.FAN_KEY_ECHO_S >= 40.0, ec_gpd.FAN_KEY_ECHO_S
    assert hardware_mod.ECHO_SUPPRESS_S >= ec_gpd.FAN_KEY_ECHO_S - 5.0, \
        'MQTT 状态回声窗口 %s 秒比 EC 的 %s 秒还短，档位回报仍会被当外部事件' % (
            hardware_mod.ECHO_SUPPRESS_S, ec_gpd.FAN_KEY_ECHO_S)


@case('hardware.set_mode 成功后必须打招呼，不然回声照旧误判')
def t_set_mode_arms_window():
    src = inspect.getsource(hardware_mod.Hardware.set_mode)
    assert 'expect_fan_key_change' in src, '发完档位命令没开回声窗口'
    assert src.find('expect_fan_key_change') < src.find("self.log.info('[硬件] 切档"), \
        '招呼打晚了，回声可能已经先被处理'


@case('回声到达时必须回调一次「已生效」，不然用户以为没点上')
def t_echo_landing_fires_applied_callback():
    st = _stub(echo_left=30.0)
    ec_gpd.EcChannel._watch(st)
    assert st.applied == ['applied'], '回声没触发生效回调：%s' % st.applied
    ec_gpd.EcChannel._watch(st)          # 同一窗口里再来一次也只认一次
    assert st.applied == ['applied'], '生效提示弹了不止一次：%s' % st.applied
    assert st.took == [], '生效回调不该顺手抢按键优先锁'


@case('面板点档位必须弹提示：这条路约 24 秒才生效，不弹等于没反应')
def t_panel_mode_click_announces():
    src = _daemon_src()
    body = _block(src, 'def set_mode_now')
    assert 'mode_send_text' in body and 'self._osd' in body, '点档位没弹屏幕提示：%s' % body
    assert 'on_applied' in body, '没挂生效回调，约 24 秒后不会有第二条反馈'
    late = _block(src, 'def on_mode_applied')
    assert 'mode_applied_text' in late and 'self._osd' in late, '生效了不吭声：%s' % late


@case('自动跟随与息屏拦截那条路不许弹：干活干到一半跳东西是打扰')
def t_auto_paths_stay_silent():
    daemon = _daemon_src()
    hw = io.open(os.path.join(ROOT, 'app', 'act', 'hardware.py'), encoding='utf-8').read()
    # 自适应切档（daemon）和息屏拦截（hardware）两处调用都不许挂 on_applied
    sched = _line_with(daemon, "hw.set_mode(want, reason='调度档位")
    assert sched and 'on_applied' not in sched, '自适应换档挂了生效提示：%s' % sched
    guard = _line_with(hw, "set_mode(want_mode, reason='息屏")
    assert guard and 'on_applied' not in guard, '息屏拦截挂了生效提示：%s' % guard


@case('生效延迟的秒数要写进文案，且不许低于实测的 24 秒')
def t_send_text_states_the_delay():
    assert fankey.MODE_DELAY_S >= 24, fankey.MODE_DELAY_S
    for m in MODE_VERIFIED:
        title, sub = fankey.mode_send_text(m)
        assert str(fankey.MODE_DELAY_S) in sub, '%s 的提示没写延迟：%s' % (m, sub)
        assert title and '未知' not in title, m
    # 没实测过的档位不给具体瓦数
    title, sub = fankey.mode_send_text('balance')
    assert 'W' not in sub, 'balance 的功耗墙没实测过，文案里不许编数字：%s' % sub
    assert title == '硬件档位已下发', title


@case('档位文案沿用同一套模式词，不许长出「办公/狂暴」')
def t_mode_texts_use_one_vocabulary():
    words = ' '.join('%s %s' % (t, s) for t, s in fankey.MODE_RESULT_TEXT.values())
    for banned in ('办公', '狂暴'):
        assert banned not in words, banned


def _daemon_src():
    return io.open(os.path.join(ROOT, 'app', 'daemon.py'), encoding='utf-8').read()


def _block(src, header):
    """从某个 def 开始截到下一个同缩进的 def 为止。"""
    i = src.index(header)
    rest = src[i:]
    lines = rest.split('\n')
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith('    def ') or ln.startswith('class ') or ln.startswith('def '):
            break
        out.append(ln)
    return '\n'.join(out)


def _line_with(src, needle):
    i = src.find(needle)
    if i < 0:
        return None
    line = src[i:].split('\n')[0]
    # 调用可能跨行：把后续以缩进继续的行也并进来
    tail = src[i:].split('\n')[1:4]
    for t in tail:
        if t.startswith(' ' * 10) or t.strip().startswith('reason='):
            line += ' ' + t.strip()
        else:
            break
    return line


def _stub(echo_left):
    """造一个只够 _watch 用的假通道：只有档位字节会变。"""
    class _Dev:
        def read(self, addr):
            return 0x10                      # 服务把 Turbo 写了回来

    class _Stub:
        def __init__(self):
            self.dev = _Dev()
            self._watch_prev = {ec_gpd.FAN_KEY: 0xA0}   # 之前是不亮/省电那一态
            self._expect = {}
            self.changes = []
            self.took = []
            self.fired = []
            self.hold_last = None
            self.on_key = lambda o, n: self.fired.append((o, n))
            self.log = None
            self.respect_s = 900.0
            self.hold_until = 0.0
            self._fan_key_echo_until = time.time() + echo_left
            self._fan_key_echo_why = '面板下发 turbo'
            self._echo_until = {}              # 其他字段的回声归因（Win 锁那一类）
            # 生效回调：用列表假装，回声到达时往里追加一次
            self.applied = []
            self._fan_key_echo_applied = (lambda: self.applied.append('applied')
                                          if echo_left > 0 else None)

        def addr(self, name):
            return 5 if name == ec_gpd.FAN_KEY else None

        def take_fan_control(self, who='面板'):
            self.took.append(who)

    return _Stub()


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
