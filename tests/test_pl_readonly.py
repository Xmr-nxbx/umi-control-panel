# -*- coding: utf-8 -*-
"""功耗墙「只读展示」的边界：展示字段不许变成写路径（不连硬件）。

    runtime\\python.exe tests\\test_pl_readonly.py

这一批给面板加了三样只读的东西：读数行（PL1/PL2/PL4）、历史曲线（pl1_setting）、
OEM 边界（detail.pl_bounds）。它们都必须是**展示**，不能顺手变成写口：
`pl_bounds` 出现在 set_power_limits / send_action 的函数体里就意味着边界被拿去写，
而不是给人看；`httpd.py` 里多一条 POST 就是多一个能改状态的入口。
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels import mqtt_gcu   # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_src(*parts):
    with open(os.path.join(REPO, *parts), encoding='utf-8') as f:
        return f.read()


@case('mqtt_gcu 源码里确实挂了 pl_bounds（只读展示字段）')
def _():
    src = read_src('app', 'act', 'channels', 'mqtt_gcu.py')
    assert "self.detail['pl_bounds']" in src


@case('pl_bounds 不许出现在 set_power_limits 的函数体里（展示字段不是写路径）')
def _():
    body = inspect.getsource(mqtt_gcu.MqttChannel.set_power_limits)
    assert 'pl_bounds' not in body, body


@case('pl_bounds 不许出现在 send_action 的函数体里')
def _():
    body = inspect.getsource(mqtt_gcu.MqttChannel.send_action)
    assert 'pl_bounds' not in body, body


@case('do_POST 路由 10 条：新增的只能是已验证的风扇曲线写入和只读抓包开关')
def _():
    src = read_src('app', 'server', 'httpd.py')
    seg = src.split('def do_POST', 1)[1]
    seg = re.split(r'\n    def ', seg, 1)[0]          # 截到下一个方法定义
    n = len(re.findall(r'elif path ==', seg))
    # 这条计数是为了防止「只读展示」的批次偷偷加写入口。两个例外都点名：
    # 风扇曲线写入挂在 POST /api/fan-curve（2026-10-20 已做可逆验证，
    # 五条闸在 MqttChannel.set_fan_curve 里）；/api/mqtt-capture 只开/关一个
    # 本地 JSONL 记录器，不向 broker 发任何命令。
    assert n == 10, 'POST 路由数变了（%d 条）' % n
    assert "elif path == '/api/fan-curve':" in seg, '风扇曲线写入路由不见了'
    assert "elif path == '/api/mqtt-capture':" in seg, '抓包开关路由不见了'


@case('httpd.py 源码里没有 pl_bounds（展示字段不进 HTTP 层，走 hardware 快照）')
def _():
    assert 'pl_bounds' not in read_src('app', 'server', 'httpd.py')


@case('前端三样都在：读数行 / 独立曲线组 / OEM 边界行')
def _():
    js = read_src('app', 'web', 'app.js')
    assert "hwRow('功耗墙 PL1/PL2/PL4'" in js
    assert 'wall: [' in js and "key: 'pl1_setting'" in js
    assert 'wallBoundsRows' in js and 'pl_bounds' in js
    html = read_src('app', 'web', 'index.html')
    assert 'id="chart-wall"' in html


@case('功耗墙说明原文照抄，没有被改写（机主不是开发者，这句话是给他看的）')
def _():
    html = read_src('app', 'web', 'index.html')
    assert '由 OEM 服务按档位下发' in html and '几十秒内改回去' in html


@case('边界行的取数路径：通道在 s.channels，读数行用只读能力点灯')
def _():
    # 踩过一次：写成 hw.channels 永远找不到通道（hardware 是拍平的 EC 读数，没有 channels），
    # 结果是「OEM 允许范围」整块静默不显示，测试还全绿。所以把取数路径钉住。
    js = read_src('app', 'web', 'app.js')
    seg = js.split('function wallBoundsRows', 1)[1].split('\n}', 1)[0]
    assert 's.channels' in seg, seg
    assert 'hw.channels' not in seg, seg
    assert 'wallBoundsRows(s)' in js, '调用点传的必须是快照 s'
    # 展示 EC 读数的行不许挂在写能力上：写能力哪天降级，只读行会跟着变灰
    row = js.split("hwRow('功耗墙 PL1/PL2/PL4'", 1)[1].split('wallBoundsRows', 1)[0]
    assert 'power_limit.read' in row, row
    assert 'power_limit.write' not in row, row


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
