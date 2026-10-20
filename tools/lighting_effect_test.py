# -*- coding: utf-8 -*-
"""灯效 SetEffectALL 的首次可逆验证（用 Creator Center 自己点出来的报文当契约）。

    runtime\\python.exe tools\\lighting_effect_test.py            # 只读预演
    runtime\\python.exe tools\\lighting_effect_test.py --write    # 真下发（最后一定还原）

报文来源：
  * --restore-capture 里取用户最后的键盘 Lighting + Welcome 两条 SetEffectALL（原样收藏）；
  * --test-capture 里取 Creator Center 点「波浪」时发的那一条 SetEffectALL。

判据（不靠肉眼）：
  `Keyboard/Status` 的 effect/speed/light/rkgcolor 只在 SetPower 处理里现读键盘；
  所以每次读状态前发一条 `SetPower powerstatus=1`（当前已开着，幂等，已 verified），
  再收服务随后推的 `Keyboard/Status`。状态里报的是 ITE 固件值（界面 speed 2 → 5），
  映射抄自逆向仓库，不是把界面值直接比。
    1. 还原到用户基线后读一次（S1）：必须与还原报文逐字段一致；
    2. 下发 Wave 后读一次（S2）：必须与 Wave 报文逐字段一致（effect 2→3）；
    3. 原样还原后再读一次（S3）：必须与 S1 逐字段相同。
任何一步不符就立即还原并判失败；默认只读预演。

只碰键盘灯效，不碰灯条、不碰电源/风扇。
"""
import argparse
import json
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.act.channels.mqtt_gcu import MqttClient   # noqa: E402
from app.config import Config                      # noqa: E402

RESTORE_CAPTURE = os.path.join(ROOT, 'logs', 'mqtt-capture-20261020-201219.jsonl')
TEST_CAPTURE = os.path.join(ROOT, 'logs', 'mqtt-capture-20261020-205150.jsonl')

SET_EFFECT = 'SetEffectALL'

# OEM 服务把界面值翻译成 ITE 固件值再落键盘，Keyboard/Status 报的是翻译后的值。
# 映射抄自逆向仓库 HIDKeyboard.cs：Translate_ITE_EffectIndex / SpeedValue / DirectionValue。
EFFECT_MAP = {'Single': 1, 'Breathing': 2, 'Wave': 3, 'Reactive': 4, 'Rainbow': 5,
              'Ripple': 6, 'RippleO': 22, 'Marquee': 9, 'Raindrop': 10, 'Spark': 17,
              'Aurora': 14, 'UserMode': 51, 'Music': 34, 'Flash': 18, 'Mix': 19,
              'Neon': 15, 'Stack': 12, 'Impact': 13, 'Gaming': 21, 'Alphabet': 23,
              'StarHitting': 25, 'StarSpark': 24, 'Thinking': 33, 'Manual': 64,
              'BatteryPercent': 35}
SPEED_MAP = {0: 10, 1: 7, 2: 5, 3: 3, 4: 1}
DIRECTION_MAP = {'None': 0, 'LeftRight': 1, 'RightLeft': 2, 'DownUp': 3}


def _load_identity():
    cfg, err, _ = Config.load()
    if err:
        return None, '配置读取失败：%s' % err
    mqtt_cfg = cfg.get('hardware', 'mqtt', default={}) or {}
    ident = {k: mqtt_cfg.get(k) for k in ('client_id', 'username', 'password')}
    if not all(ident.values()):
        return None, '没有 broker 身份（data\\mqtt_identity.json）'
    return ident, None


def _records(path):
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get('topic') == 'Keyboard/Ctrl':
                yield rec


def load_restore(path):
    """取最后一条 Lighting 和最后一条 Welcome 的 SetEffectALL。"""
    out = {}
    for rec in _records(path):
        pl = rec.get('payload') or {}
        if pl.get('function') != SET_EFFECT:
            continue
        mode = pl.get('mode')
        if mode in ('Lighting', 'Welcome'):
            out[mode] = pl
    return out.get('Lighting'), out.get('Welcome')


def load_effect(path, effect):
    last = None
    for rec in _records(path):
        pl = rec.get('payload') or {}
        if pl.get('function') == SET_EFFECT and pl.get('effect') == effect:
            last = pl
    return last


def _summary(pl):
    colors = ((pl.get('color') or {}).get('ColorBuffer') or [])
    return ('mode=%s effect=%s light=%s speed=%s direction=%s colors=%s'
            % (pl.get('mode'), pl.get('effect'), pl.get('light'), pl.get('speed'),
               pl.get('direction'), [(c.get('R'), c.get('G'), c.get('B')) for c in colors]))


def _connect(ident, topics):
    cli = MqttClient('127.0.0.1', 13688, ident['client_id'],
                     ident['username'], ident['password'])
    cli.connect(timeout=2.5)
    if topics:
        cli.subscribe(topics)
    return cli


def _send(ident, payload, attempts=3):
    """短连接只发一条，不订阅、不等应答。"""
    last = None
    for _ in range(attempts):
        cli = None
        try:
            cli = _connect(ident, [])
            cli.publish('Keyboard/Ctrl', json.dumps(payload))
            time.sleep(0.2)
            return
        except (OSError, ConnectionError) as exc:  # noqa: PERF203
            last = exc
            time.sleep(1.2)
        finally:
            if cli is not None:
                cli.close()
    raise ConnectionError('连 broker 三次都失败：%r' % (last,))


def _read_status(ident, attempts=3, wait_s=3.0):
    """发 SetPower（幂等）让服务现读键盘，再收 Keyboard/Status。"""
    last = None
    for _ in range(attempts):
        cli = None
        try:
            cli = _connect(ident, ['Keyboard/Status'])
            cli.sock.settimeout(0.4)
            cli.publish('Keyboard/Ctrl', json.dumps({'function': 'SetPower', 'powerstatus': 1}))
            end = time.time() + wait_s
            status = None
            while time.time() < end:
                try:
                    code, body = cli.read_packet()
                except socket.timeout:
                    continue
                if code & 0xF0 != 0x30 or len(body) < 2:
                    continue
                tlen = int.from_bytes(body[:2], 'big')
                topic = body[2:2 + tlen].decode('utf-8', 'replace')
                if topic != 'Keyboard/Status':
                    continue
                try:
                    status = json.loads(body[2 + tlen:].decode('utf-8', 'replace') or '{}')
                except ValueError:
                    continue
            if status:
                return status
            last = '没收到 Keyboard/Status'
        except (OSError, ConnectionError) as exc:  # noqa: PERF203
            last = exc
            time.sleep(1.2)
        finally:
            if cli is not None:
                cli.close()
    raise ConnectionError('读键盘状态失败：%r' % (last,))


def _view(status):
    color = status.get('rkgcolor')
    if isinstance(color, str):
        try:
            color = json.loads(color)
        except ValueError:
            color = None
    return {
        'effect': str(status.get('effect')),
        'light': str(status.get('light')),
        'speed': str(status.get('speed')),
        'direction': str(status.get('direction')),
        'power': status.get('powerStatus'),
        'colors': [(c.get('R'), c.get('G'), c.get('B'))
                   for c in ((color or {}).get('ColorBuffer') or [])],
    }


def _show(tag, status):
    v = _view(status)
    print('%s：effect=%s light=%s speed=%s direction=%s power=%s colors=%s'
          % (tag, v['effect'], v['light'], v['speed'], v['direction'],
             v['power'], v['colors']))
    return v


def _same(a, b):
    return all(a[k] == b[k] for k in ('effect', 'light', 'speed', 'direction', 'colors'))


def _expected(payload):
    effect = EFFECT_MAP.get(payload.get('effect'))
    speed = SPEED_MAP.get(int(payload.get('speed')))
    direction = DIRECTION_MAP.get(str(payload.get('direction')))
    colors = [(c.get('R'), c.get('G'), c.get('B'))
              for c in ((payload.get('color') or {}).get('ColorBuffer') or [])]
    return {'effect': str(effect), 'light': str(payload.get('light')),
            'speed': str(speed), 'direction': str(direction), 'colors': colors}


def _matches(view, payload):
    exp = _expected(payload)
    bad = [k for k in ('effect', 'light', 'speed', 'direction', 'colors')
           if view.get(k) != exp.get(k)]
    return bad, exp


def main(argv=None):
    ap = argparse.ArgumentParser(description='键盘灯效 SetEffectALL 的可逆验证')
    ap.add_argument('--write', action='store_true', help='真的下发（默认只读预演）')
    ap.add_argument('--restore-capture', default=RESTORE_CAPTURE)
    ap.add_argument('--test-capture', default=TEST_CAPTURE)
    args = ap.parse_args(argv)

    ident, err = _load_identity()
    if err:
        print(err)
        return 2
    try:
        restore_l, restore_w = load_restore(args.restore_capture)
        wave = load_effect(args.test_capture, 'Wave')
    except OSError as exc:
        print('读抓包文件失败：%r' % (exc,))
        return 2
    if not restore_l or not restore_w:
        print('还原抓包里缺 Lighting 或 Welcome 的 SetEffectALL，拒绝继续')
        return 2
    if not wave:
        print('测试抓包里没有 Wave 的 SetEffectALL，拒绝继续')
        return 2

    print('== 1. 计划 ==')
    print('还原 Lighting：%s' % _summary(restore_l))
    print('还原 Welcome ：%s' % _summary(restore_w))
    print('测试 Wave    ：%s' % _summary(wave))
    print('状态期望（ITE 值）：基线 %s；Wave %s' % (_expected(restore_l), _expected(wave)))
    print('读状态 = 发 SetPower powerstatus=1（当前开着，幂等）再收 Keyboard/Status。')
    if not args.write:
        print('\n只读预演结束。要真下发请加 --write。')
        return 0

    sent_test = False
    restored = False
    try:
        print('\n== 2. 先还原到用户基线 ==')
        _send(ident, restore_l)
        _send(ident, restore_w)
        time.sleep(2.0)
        s1 = _show('S1 基线', _read_status(ident))
        bad, exp = _matches(s1, restore_l)
        if bad:
            print('FAIL：还原后的状态与报文不符：%s（期望 %s）' % (bad, exp))
            return 4

        print('\n== 3. 下发 Wave（只发 Lighting，和 Creator Center 一样）==')
        _send(ident, wave)
        sent_test = True
        time.sleep(2.0)
        s2 = _show('S2 波形', _read_status(ident))
        bad, exp = _matches(s2, wave)
        if bad:
            print('FAIL：Wave 状态与报文不符：%s（期望 %s）' % (bad, exp))
            return 4

        print('\n== 4. 原样还原（Lighting + Welcome）==')
        _send(ident, restore_l)
        _send(ident, restore_w)
        restored = True
        time.sleep(2.0)
        s3 = _show('S3 还原', _read_status(ident))
        if not _same(s1, s3):
            print('FAIL：还原后的状态与基线不一致，请人工检查键盘灯！')
            return 5
        print('\n通过：SetEffectALL 的下发/还原都能被 Keyboard/Status 现读确认。')
        return 0
    finally:
        if sent_test and not restored:
            print('\n[保险] 异常退出，正在还原用户基线…')
            try:
                _send(ident, restore_l)
                _send(ident, restore_w)
                print('[保险] 已下发还原，请用 Creator Center 或肉眼看一眼键盘。')
            except Exception as exc:                # noqa: BLE001
                print('[保险] 还原失败：%r —— 请立即在 Creator Center 里手动选回呼吸灯！' % (exc,))


if __name__ == '__main__':
    sys.exit(main())
