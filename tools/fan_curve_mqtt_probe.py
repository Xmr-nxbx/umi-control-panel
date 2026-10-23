# -*- coding: utf-8 -*-
"""只读：从 OEM 服务要一份风扇表（Fan/Table），并可选和面板的 EC 直读对照。

    runtime\\python.exe tools\\fan_curve_mqtt_probe.py
    runtime\\python.exe tools\\fan_curve_mqtt_probe.py --no-ec
    runtime\\python.exe tools\\fan_curve_mqtt_probe.py --json tools\\out\\fan-curve-mqtt.json

做的事：连 broker → 发 {"Action":"GET_FAN_SPEED_CURVE_SETTING"} → 收 Fan/Table → 打印。
**一个字节都不写**；不发 SET、不改档、不碰 EC。

注意：broker 一个 clientId 只许一条连接。本工具用面板的同一份身份
（data\\mqtt_identity.json），所以跑的时候面板的 MQTT 通道会被顶掉约 5 秒，
之后它自己重连。HTTP 面板不受影响。想完全避开就把面板停掉再跑——
但只是看一眼表，没必要。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.act.channels.mqtt_gcu import MqttChannel   # noqa: E402
from app.config import Config                       # noqa: E402
from app.logx import Log                            # noqa: E402
from app.paths import discover_panel_port           # noqa: E402


def _wait_alive(ch, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if ch.alive:
            return True
        time.sleep(0.1)
    return False


def _ec_curve(base):
    try:
        with urllib.request.urlopen(base + '/api/fan-curve', timeout=8) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as exc:                            # noqa: BLE001
        return {'ok': False, 'reason': '面板 EC 直读不可用：%r' % (exc,)}


def _print_side(name, mqtt_points, ec_points):
    print('\n%s 风扇表（Duty 单位 %%）' % name)
    print('  点   UpT   DownT   OEM服务   EC直读   差')
    for i in range(16):
        m = mqtt_points[i] if i < len(mqtt_points) else {}
        e = ec_points[i] if ec_points and i < len(ec_points) else {}
        mv, ev = m.get('duty_pct'), e.get('duty_pct')
        diff = '' if (mv is None or ev is None) else ('%+.1f' % (mv - ev))
        print('  %2d  %4s   %5s   %6s   %6s   %s'
              % (i, m.get('up_t'), m.get('down_t'),
                 '-' if mv is None else '%.1f' % mv,
                 '-' if ev is None else '%.1f' % ev, diff))


def main(argv=None):
    ap = argparse.ArgumentParser(description='只读读取 OEM 风扇曲线（Fan/Table）')
    ap.add_argument('--no-ec', action='store_true', help='不和面板的 EC 直读对照')
    ap.add_argument('--json', help='把原始结果写到这个文件')
    ap.add_argument('--port', type=int, help='面板端口（优先于 data/port 和配置）')
    args = ap.parse_args(argv)

    cfg, err, broken = Config.load()
    if err:
        print('配置读取有问题：%s%s' % (err, ('（已备份到 %s）' % broken) if broken else ''))
        return 2
    base = None
    if not args.no_ec:
        try:
            base = 'http://127.0.0.1:%d' % discover_panel_port(args.port, cfg)
        except (OSError, ValueError) as exc:
            print('无法确定面板端口：%s' % exc)
            return 2
    log = Log('fan-curve-mqtt-probe')
    ch = MqttChannel(cfg, log)
    if not (ch.client_id and ch.username):
        print('没有 broker 身份：data\\mqtt_identity.json 不存在或为空。'
              '（本工具不打印任何凭据）')
        return 2
    print('连接 127.0.0.1:%s …（会用面板同一身份，面板 MQTT 会被顶掉约 5 秒）'
          % ch.port)
    ch.start()
    try:
        if not _wait_alive(ch):
            print('连不上：%s' % ch.detail.get('reason'))
            return 3
        print('已连接，发 GET_FAN_SPEED_CURVE_SETTING …')
        out = ch.fan_curve(wait_s=3.0)
        print('ok=%s name=%s stale=%s age=%.1fs'
              % (out.get('ok'), out.get('name'), out.get('stale'), out.get('age_s') or 0))
        if not out.get('ok'):
            print('失败：%s' % out.get('reason'))
            return 4
        tables = out.get('tables') or {}
        ec = {'ok': False, 'tables': {}}
        if not args.no_ec:
            ec = _ec_curve(base)
            if not ec.get('ok'):
                print('（EC 对照跳过：%s）' % ec.get('reason'))
        for which in ('CPU', 'GPU'):
            _print_side(which, tables.get(which) or [],
                        (ec.get('tables') or {}).get(which) or [])
        if args.json:
            os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
            with open(args.json, 'w', encoding='utf-8') as f:
                json.dump({'mqtt': out, 'ec': ec}, f, ensure_ascii=False, indent=1)
            print('\n原始结果已写入 %s' % args.json)
        return 0
    finally:
        ch.close()
        log.close()


if __name__ == '__main__':
    sys.exit(main())
