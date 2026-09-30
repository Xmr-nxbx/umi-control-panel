# -*- coding: utf-8 -*-
"""精细功耗墙写入（SET_OPERATING_MODE_DETAIL）的护栏。

    runtime\\python.exe tests\\test_pl_guard.py

这条路径是 OEM 唯一受支持的改墙口（裸写 `0x0783-0x0785` 会被服务盖回去，
`0x0751` 又是它的输出不是输入，见 notes/hardware-channels.md 6.11 十五）。
但它的服务端**一个字节都不校验**：`Convert.ToInt32` 之后直接 `(byte)` 截断，
发 300 会静默变成 44 W，`_SmartApcTable` 和 `CpuPL1Minimum` 只是发给 UI 的
Maximum/Minimum，不参与 clamp。所以护栏必须由我们自己加，而且这几条规矩得钉住：

  * 拿不到 OEM 自己报的上下限就**不发**（没护栏的写入不做）；
  * 越界是**拒绝**，不是悄悄夹到边上——悄悄夹会让人以为设成了 300；
  * 一个字节放不下的值直接拒，并把「会被截断成几」说给对方看；
  * 报文形状照 OEM 的样子：`Fan/Control` + `{"Action":"SET_OPERATING_MODE_DETAIL",
    "PL1":"45",...}`，值是**字符串**；
  * `power_limit.write` 没升 verified 之前，HTTP 那条路必须发不出去（第三道闸）。
"""
import inspect
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import CAP_PL_WRITE           # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel        # noqa: E402

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

    def warn(self, *a):
        pass

    def error(self, *a):
        pass


# 本机 Fan/Status 真报过的一组数（notes 6.11 十二）：PL1 10~120、PL4 ≤165
LIMITS = {'CPU_PL1Minimum': '10', 'CPU_PL1Maximum': '120', 'CPU_PL4Maximum': '165'}


def _channel(fan_status=LIMITS, verified=()):
    ch = MqttChannel(FakeCfg(True), FakeLog())
    ch.alive = True
    if fan_status is not None:
        ch._payloads = {'Fan/Status': {'ts': 0.0, 'data': dict(fan_status)}}
    for cap in verified:
        ch.caps[cap] = 'verified'
    return ch


def _sent(ch):
    topic, payload = ch._outq.get_nowait()
    return topic, json.loads(payload)


@case('拿不到 Fan/Status 的边界就不发：没护栏的功耗墙写入不做')
def t_no_limits():
    ch = _channel(fan_status=None)
    ok, detail = ch.set_power_limits(pl1=45)
    assert ok is False, '没有边界也发出去了'
    assert '护栏' in detail, detail
    assert ch._outq.empty()


@case('边界只报了一半也不敢发（min 有 max 没有 = 不知道上限）')
def t_half_limits():
    ch = _channel({'CPU_PL1Minimum': '10'})
    ok, detail = ch.set_power_limits(pl1=45)
    assert ok is False, detail
    assert '边界' in detail, detail


@case('越界是拒绝，不是悄悄夹到 120')
def t_out_of_bounds_refuses():
    ch = _channel()
    ok, detail = ch.set_power_limits(pl1=130)
    assert ok is False, '130 超出 10~120 还发出去了'
    assert '10~120' in detail, detail
    assert ch._outq.empty(), '拒了却还是把命令塞进队列'
    # 下界同样要管
    ok2, detail2 = ch.set_power_limits(pl1=5)
    assert ok2 is False and '10~120' in detail2, detail2


@case('一个字节放不下的值：拒发，并把会被截断成几说清楚')
def t_byte_truncation():
    ch = _channel()
    ok, detail = ch.set_power_limits(pl1=300)
    assert ok is False, detail
    assert '44' in detail, '服务端会静默截断成 44 W，文案里必须说出来：%s' % detail
    assert ch._outq.empty()


@case('合法值照 OEM 的报文形状发：Fan/Control + Action + 字符串值')
def t_in_bounds_sends_oem_shape():
    ch = _channel(verified=('power_limit.write',))
    ok, detail = ch.set_power_limits(pl1=45, pl2=45, pl4=165)
    assert ok is True, detail
    topic, payload = _sent(ch)
    assert topic == 'Fan/Control', topic
    assert payload['Action'] == 'SET_OPERATING_MODE_DETAIL', payload
    # PL1/PL2/PL4 是三个独立 if，所以一条消息可以同时带；值必须是字符串
    assert payload['PL1'] == '45' and payload['PL4'] == '165', payload
    assert all(isinstance(v, str) for k, v in payload.items() if k.startswith('PL')), payload


@case('没这个动作的 cap 就发不出去：第三道闸对功耗墙同样有效')
def t_cap_gate_blocks_pl():
    ch = _channel()                      # power_limit.write 没标 verified
    ok, detail = ch.set_power_limits(pl1=45)
    assert ok is False, '没验证过的写动作居然发出去了'
    assert 'power_limit.write' in detail, detail
    assert ch._outq.empty()


@case('allow_write=false 时更不用谈（第一道闸在最前面）')
def t_allow_write_off():
    ch = _channel()
    ch.allow_write = False
    ok, detail = ch.set_power_limits(pl1=45)
    assert ok is False and 'allow_write' in detail, detail


@case('一个值都不给时不许发空命令')
def t_no_values():
    ch = _channel(verified=('power_limit.write',))
    ok, detail = ch.set_power_limits()
    assert ok is False and '没有要改的值' in detail, detail
    assert ch._outq.empty()


@case('动作名与报文形状由白名单条目声明，代码里不写死 SET_OPERATING_MODE_DETAIL')
def t_shape_from_whitelist():
    src = inspect.getsource(MqttChannel.set_power_limits)
    # 文档字符串里提一嘴命令名是有用的，但不许出现在真正的代码里
    code = ''.join(p for i, p in enumerate(src.split('"""')) if i != 1)
    assert 'SET_OPERATING_MODE_DETAIL' not in code, '命令名写死在代码里了'
    assert "send_action('SET_PL_DETAIL'" in code, '没走白名单条目'
    ch = _channel(verified=('power_limit.write',))
    spec = ch.actions['actions']['SET_PL_DETAIL']
    assert spec['value'] == 'SET_OPERATING_MODE_DETAIL', spec
    assert spec['cap'] == 'power_limit.write', spec
    assert spec['topic'] == 'fan_control', spec


@case('本机 Fan/Status 报全了三对边界时各用各的，不再借 PL1 那一组')
def t_uses_own_per_channel_bounds():
    # 2026-09-30 19:45 往 Fan/Control 问到的原文（节选）：PL1 10~120、PL2 10~120、PL4 10~165
    full = {'CPU_PL1Minimum': '10', 'CPU_PL1Maximum': '120',
            'CPU_PL2Minimum': '10', 'CPU_PL2Maximum': '60',
            'CPU_PL4Minimum': '15', 'CPU_PL4Maximum': '165'}
    ch = _channel(full, verified=('power_limit.write',))
    assert ch.pl_limits() == {'pl1_min': 10, 'pl1_max': 120, 'pl2_min': 10, 'pl2_max': 60,
                              'pl4_min': 15, 'pl4_max': 165}, ch.pl_limits()
    # PL2 的上限现在是 60：越过它必须被拒，而不是按 PL1 的 120 放行
    ok, detail = ch.set_power_limits(pl2=75)
    assert ok is False and '10~60' in detail, detail
    ok, detail = ch.set_power_limits(pl4=12)
    assert ok is False and '15~165' in detail, detail


@case('功耗墙已升 verified，但原因里必须说清验的是哪几个值；掉线要把它收回')
def t_cap_reported_and_reset():
    src = inspect.getsource(MqttChannel._session)
    assert "self.caps[CAP_PL_WRITE] = 'verified'" in src, '验证做完了还写着 unknown，面板永远点不亮'
    assert 'pl_write_reason' in src, '没写清楚验过什么、没验什么'
    reason = re.search(r"self\.detail\['pl_write_reason'\] = \(\s*((?:'[^']*'\s*)+)", src)
    assert reason and 'PL1' in reason.group(1) and 'PL2' in reason.group(1), \
        '文案没区分「亲手验过的 PL1」和「同一条命令但没单独试的 PL2/PL4」'
    whole = inspect.getsource(sys.modules[MqttChannel.__module__])
    assert 'CAP_PL_READ, CAP_PL_WRITE' in whole, \
        '复位列表里没有它：通道掉线后会留着上一次的 verified 骗人'
    # 掉线那段必须把 PL_WRITE 一起收回，不能只收 Win 锁
    except_block = whole.split('except Exception as exc')[1].split('self.detail[\'reason\']')[0]
    assert 'CAP_PL_WRITE' in except_block, '断线后功耗墙还挂着 verified = 面板会点出失败'


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
