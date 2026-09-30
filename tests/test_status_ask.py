# -*- coding: utf-8 -*-
"""写完命令之后「多久能问到新状态」的用例。

    runtime\\python.exe tests\\test_status_ask.py

2026-09-30 15:45 用户的反馈：「键盘背光和锁定 win 键，这个开关启动关闭时间太长了」。
实测命令本身不慢（背光约 0.3 秒、Win 锁约 6 秒），慢的是**面板上那行读数**：
`Setting/Status` 不是周期广播，不问了就不推，而通道只在每 45 秒的 tick 里问一次，
所以点完最长要等 45 秒才翻——看着就像「开关反应慢」。

修法：下发成功就开一段「追问窗口」（30 秒内每 3 秒问一次），窗口一过回到 45 秒例行问。
这里钉住四条，防止以后漂回去：
  * 没写东西时不许把周期问改密（EC 那边已经够忙，broker 也没必要被狂问）；
  * 写完必须立刻能问到，而且不是一次——只问一次拿到的是旧值（命令还没落到 EC）；
  * 窗口有上界，不许变成永久轮询；
  * 窗口长度要盖得住最慢的那条命令（档位实测 9~26 秒）。
"""
import ast
import inspect
import json
import os
import sys
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import CAP_WINKEY_WRITE, CAP_RGB_WRITE   # noqa: E402
from app.act.channels import mqtt_gcu                                # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel                    # noqa: E402

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


def _channel(verified=()):
    ch = MqttChannel(FakeCfg(True), FakeLog())
    ch.alive = True
    for cap in verified:
        ch.caps[cap] = 'verified'
    return ch


def _asks(ch):
    """把队列里攒的报文都倒出来，返回 GETSTATUS 的条数。"""
    n = 0
    while not ch._outq.empty():
        topic, payload = ch._outq.get_nowait()
        if topic == 'Setting/Control' and json.loads(payload).get('Action') == 'GETSTATUS':
            n += 1
    return n


@case('没写东西时维持 45 秒一次的节奏：连点 tick 不许多发')
def t_idle_cadence_unchanged():
    ch = _channel()
    ch.tick()                      # 第一次：到点了，问一次
    assert _asks(ch) == 1, '例行问不该一次塞两条'
    for _ in range(5):
        ch.tick()                  # 紧接着再点几下
    assert _asks(ch) == 0, '没到 45 秒就不该再问（EC 和 broker 都不该被狂问）'


@case('下发成功后开追问窗口：立刻问、几秒后再问，而不是等 45 秒')
def t_burst_after_write():
    ch = _channel(verified=(CAP_WINKEY_WRITE,))
    ok, detail = ch.send_action('WINKEY_LOCK', note='测试')
    assert ok is True, detail
    assert _asks(ch) == 1, 'send_action 自己就该问一次（那条 WINKEY 之外）'
    # 模拟「3 秒后」：把上次问的时间往前拨，tick 就该补问
    ch._last_ask_ts = time.time() - 4
    ch.tick()
    assert _asks(ch) == 1, '追问期内 tick 要补问，否则 Win 锁那 6 秒的空窗还是看不到'
    ch.tick()
    assert _asks(ch) == 0, '追问也不许一次 tick 问两条'


@case('追问窗口有上界：过期就回到 45 秒节奏，不许变成永久轮询')
def t_burst_expires():
    ch = _channel()
    ch.request_status()
    assert _asks(ch) == 1, '开窗时该问一次（顺带把队列倒干净）'
    ch._ask_until = time.time() - 1          # 窗口到点
    ch._last_probe_ts = time.time() - 10     # 例行问还没到 45 秒
    ch._last_ask_ts = time.time() - 100      # 就算很久没问
    ch.tick()
    assert _asks(ch) == 0, '窗口过期后还密集追问 = 变成永久轮询'


@case('窗口长度要盖得住最慢的命令（档位实测 9~26 秒）')
def t_burst_covers_slowest():
    assert MqttChannel.ASK_BURST_S >= 30, \
        '窗口只有 %s 秒，档位那条要 26 秒才生效，尾巴会被截掉' % MqttChannel.ASK_BURST_S
    assert 1 <= MqttChannel.ASK_BURST_GAP_S <= 5, \
        '追问间隙 %s 秒：太密白忙 EC，太松前端等待态就露出来了' % MqttChannel.ASK_BURST_GAP_S


@case('通道没连上时不开口：request_status 返回 False 且不发消息')
def t_dead_channel_quiet():
    ch = _channel()
    ch.alive = False
    assert ch.request_status() is False
    ch.tick()
    assert _asks(ch) == 0, '没连上却往队列里塞 GETSTATUS'


@case('写背光同样开窗口（这条 0.3 秒就生效，靠的是问到，不是等）')
def t_kb_action_opens_window():
    ch = _channel(verified=(CAP_RGB_WRITE,))
    ok, detail = ch.send_action('KB_POWER_ON', note='测试')
    assert ok is True, detail
    assert _asks(ch) == 1, '写完背光没开追问窗口'
    assert ch._ask_until > time.time(), '窗口没打开'


@case('拒发不开窗口：闸门拦下来的命令不该让 EC 白答一次')
def t_refused_action_no_ask():
    ch = _channel()                    # winkey.write 没升 verified
    ok, detail = ch.send_action('WINKEY_LOCK', note='测试')
    assert ok is False, detail
    assert _asks(ch) == 0, '命令都没出去，却去问状态'


@case('收包超时不许跳过发送队列（真正的「开关半天不翻」根子）')
def t_timeout_still_drains():
    """OEM 不主动推状态，所以「等到有包进来才发东西」等于自己把自己卡死。

    2026-09-30 15:57 实测：下发背光之后 25 秒里 `_outq` 一条都没出去，面板读数纹丝不动。
    这条用例盯的是循环结构，不看文案：socket.timeout 分支里不许 continue，
    而倒空 `_outq` 的那个 while 必须是循环体的直接成员（在 Try 之后，不在 except 里）。
    """
    fn = ast.parse(textwrap.dedent(inspect.getsource(mqtt_gcu.MqttChannel._session))).body[0]
    loops = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
    assert loops, '_session 里找不到主循环'
    main = max(loops, key=lambda w: len(w.body))
    tried = [n for n in main.body if isinstance(n, ast.Try)]
    assert tried, '主循环里没有读包的 try'
    for handler in tried[0].handlers:
        for node in ast.walk(handler):
            assert not isinstance(node, ast.Continue), \
                'except 里 continue 会把后面的发送队列整个跳过'
    drains = [n for n in main.body if isinstance(n, ast.While) and _is_outq_drain(n)]
    assert drains, '倒空发送队列的 while 不在主循环体里（超时那一轮就没人发消息了）'


def _is_outq_drain(node):
    """这个 while 的测试是不是 `not self._outq.empty()`。"""
    for d in ast.walk(node):
        if isinstance(d, ast.Attribute) and d.attr == 'empty':
            v = d.value
            if isinstance(v, ast.Attribute) and v.attr == '_outq':
                return True
    return False


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
            print('  FAIL %s -> %r' % (name, exc))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
