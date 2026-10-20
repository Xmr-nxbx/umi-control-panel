# -*- coding: utf-8 -*-
"""一条命令重启面板并自检：停 → 起 → 等活 → 核对三通道与写能力。

    runtime\\python.exe tools\\restart_panel.py
    runtime\\python.exe tools\\restart_panel.py --keep-running   # 只自检，不重启

为什么需要它：面板一停，OEM 的 GCUService 会把功耗墙压回它自己存的低值，
所以「停完必须立刻拉起、并且确认三个通道都真的活了」——手工做容易漏，
尤其是 `_session` 里 NameError 那种只在线程里炸、端口却照常应答的故障。
本工具全程只发 HTTP GET/POST，不碰 EC、不碰 MQTT 命令。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.paths import data_path   # noqa: E402

CREATE_NO_WINDOW = 0x08000000


def _port():
    try:
        with open(data_path('port'), encoding='utf-8') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 8747


def _get(path, timeout=3):
    url = 'http://127.0.0.1:%d%s' % (_port(), path)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _post(path, body, timeout=5):
    url = 'http://127.0.0.1:%d%s' % (_port(), path)
    req = urllib.request.Request(
        url, data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Origin': 'http://127.0.0.1:%d' % _port()},
        method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _alive(timeout=1.0):
    try:
        return _get('/api/ping', timeout=timeout).get('pong') is True
    except Exception:                                # noqa: BLE001
        return False


def _wait(cond, seconds, step=0.5):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _exe():
    panel = os.path.join(ROOT, 'runtime', 'UmiPanel.exe')
    return panel if os.path.exists(panel) else sys.executable


def _check(verbose=True):
    hw = _get('/api/hardware', timeout=8)
    health = _get('/api/health', timeout=15)
    channels = hw.get('channels') or []
    caps = hw.get('caps') or {}
    ok = True
    if verbose:
        print('通道：')
        for ch in channels:
            print('  %-10s alive=%s  %s' % (ch.get('name'), ch.get('alive'),
                                            (ch.get('detail') or {}).get('reason', '')))
            if not ch.get('alive'):
                ok = False
        print('关键能力：')
        for cap in ('mode.write', 'power_limit.write', 'fan.curve.write',
                    'winkey.write', 'lighting.rgb.write'):
            item = caps.get(cap) or {}
            print('  %-20s %s · %s' % (cap, item.get('state', '?'), item.get('channel', '-')))
        print('\n体检：\n%s' % (health.get('text') or '').strip())
    return ok, hw


def main(argv=None):
    ap = argparse.ArgumentParser(description='重启面板并自检')
    ap.add_argument('--keep-running', action='store_true', help='不重启，只做一次自检')
    args = ap.parse_args(argv)

    if args.keep_running:
        ok, _hw = _check()
        return 0 if ok else 1

    print('1/4 优雅停止面板（会先还原电源设置）…')
    stopped = False
    try:
        _post('/api/shutdown', {})
        stopped = True
    except Exception as exc:                         # noqa: BLE001
        print('   面板没响应或已经停了：%r' % (exc,))
    if stopped:
        if not _wait(lambda: not _alive(0.5), 20):
            print('   20 秒内面板还没停，放弃重启（避免双实例）')
            return 2
        print('   已停止')
    time.sleep(1.5)                                  # 等守护进程看到 code=0 退出

    print('2/4 启动守护模式…')
    exe = _exe()
    proc = subprocess.Popen([exe, os.path.join(ROOT, 'main.py'), '--supervise', '--no-browser'],
                            cwd=ROOT, creationflags=CREATE_NO_WINDOW)
    print('   已拉起 supervisor pid=%s' % proc.pid)

    print('3/4 等端口应答…')
    if not _wait(_alive, 40, 0.5):
        print('   40 秒内 /api/ping 没应答，启动失败；看 data\\umi-control-panel.log')
        return 3
    ping = _get('/api/ping')
    print('   已应答：uptime=%ss tick_age=%ss' % (ping.get('uptime_s'), ping.get('tick_age_s')))

    print('4/4 核对通道与能力…')
    time.sleep(3)                                    # PDH 频率要两次采样，别把刚启动的 None 当故障
    ok, _hw = _check()
    print('\n结论：%s' % ('通过' if ok else '有通道没活，按上面的 reason 排查'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
