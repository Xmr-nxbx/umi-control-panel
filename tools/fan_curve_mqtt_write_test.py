# -*- coding: utf-8 -*-
"""#46 首次可逆验证：经 OEM 服务写一格风扇占空，再写回原值。

    runtime\\python.exe tools\\fan_curve_mqtt_write_test.py            # 只读预演
    runtime\\python.exe tools\\fan_curve_mqtt_write_test.py --write    # 真下发（一条 SET，随后还原）

纸上预期（本机 2026-10-20 的 M3T1 表，EC 直读与 OEM 服务逐点一致）：
  * 基线 CPU Duty[15] = 100 %（EC 原始字节 0x0F2F = 200）；
  * 点 15 的 UpT = 255，是永远到不了的哨兵点 ⇒ 改它的占空**不影响风扇**；
  * 下发 T15=90 后：只有 0x0F2F 由 200 → 180，另外 15 个 Duty 字节与
    全部 UpT/DownT 一个都不许变；
  * 写回原 T15=100 后：整张 CPU 表逐点与基线相同。
如果实测偏离任何一条，脚本会立刻写回原值并报失败。

安全闸（缺一条就拒绝写）：
  * `Fan/Status.FAN_SafetyProtect` 必须是 0；是 1 时服务只落盘不写 EC，
    验不出东西，而且会在保护解除后突然生效；
  * OEM 表与 EC 直读的 16 点占空必须逐点一致，否则说明服务内存和 EC 不同步；
  * 目标点必须是 UpT=255 的哨兵点；
  * `--write` 必须显式给；默认只读预演。

只改 Duty，不碰 UpT/DownT；不需要负载、不需要停面板。
面板的 MQTT 会被顶掉几秒（同一身份），HTTP 面板与 EC 读都不受影响。
"""
import argparse
import json
import os
import socket
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.act.channels.mqtt_gcu import MqttClient   # noqa: E402
from app.config import Config                      # noqa: E402
from app.paths import discover_panel_port, local_mqtt_endpoint  # noqa: E402

PANEL = None
TARGET = 15
DELTA = 10


def _http_json(path, timeout=10):
    with urllib.request.urlopen(PANEL + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _ec_curve_fresh(after_ts, timeout=45.0):
    """等一份**缓存时间晚于 after_ts** 的 EC 直读。面板内缓存 10 秒，所以这里要轮询。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            out = _http_json('/api/fan-curve')
        except Exception as exc:                    # noqa: BLE001
            last = 'EC 端点不可用：%r' % (exc,)
            time.sleep(2)
            continue
        last = out
        if out.get('ok') and not out.get('cached') and (out.get('ts') or 0) >= after_ts:
            return out
        time.sleep(2)
    return {'ok': False, 'reason': '等不到写后的新鲜 EC 直读：%r' % (last,)}


def _parse_table(payload):
    rows = (payload or {}).get('CPU') or []
    if len(rows) != 16:
        return None
    points = []
    for row in rows:
        try:
            points.append({'id': int(row['ID']), 'up_t': int(row['UpT']),
                           'down_t': int(row['DownT']), 'duty_pct': int(row['Duty'])})
        except (KeyError, TypeError, ValueError):
            return None
    return points


def _mqtt_read(ident, wait_s=2.0, attempts=3):
    """短连接：GET 曲线 + GETSTATUS，拿到 Fan/Table 和 Fan/Status 后立刻断开。

    面板用同一个 clientId，会在被顶掉约 5 秒后重连、反过来把我们顶掉。
    所以每次会话都短，并且失败时重试；重试前隔 1.5 秒让面板先连上。
    """
    last = None
    for attempt in range(attempts):
        try:
            return _mqtt_read_once(ident, wait_s)
        except (OSError, ConnectionError) as exc:  # noqa: PERF203
            last = exc
            time.sleep(1.5)
    raise ConnectionError('连 broker 三次都失败：%r' % (last,))


def _mqtt_read_once(ident, wait_s):
    cli = MqttClient(ident['host'], ident['port'], ident['client_id'],
                     ident['username'], ident['password'])
    cli.connect(timeout=2.5)
    try:
        cli.subscribe(['Fan/Table', 'Fan/Status'])
        cli.sock.settimeout(0.4)
        cli.publish('Fan/Control', json.dumps({'Action': 'GET_FAN_SPEED_CURVE_SETTING'}))
        cli.publish('Fan/Control', json.dumps({'Action': 'GETSTATUS'}))
        table = status = None
        end = time.time() + wait_s
        while time.time() < end:
            try:
                code, body = cli.read_packet()
            except socket.timeout:
                continue
            if code & 0xF0 != 0x30 or len(body) < 2:
                continue
            tlen = int.from_bytes(body[:2], 'big')
            topic = body[2:2 + tlen].decode('utf-8', 'replace')
            try:
                data = json.loads(body[2 + tlen:].decode('utf-8', 'replace') or '{}')
            except ValueError:
                continue
            if topic == 'Fan/Table':
                table = data
            elif topic == 'Fan/Status':
                status = data
            if table and status:
                break
        return table, status
    finally:
        cli.close()


def _mqtt_send(ident, payload, attempts=3):
    """短连接：只发一条，不订阅、不等应答。消息进 broker 就够了。"""
    last = None
    for attempt in range(attempts):
        cli = MqttClient(ident['host'], ident['port'], ident['client_id'],
                         ident['username'], ident['password'])
        try:
            cli.connect(timeout=2.5)
            cli.publish('Fan/Control', json.dumps(payload))
            time.sleep(0.15)
            return
        except (OSError, ConnectionError) as exc:  # noqa: PERF203
            last = exc
            time.sleep(1.0)
        finally:
            cli.close()
    raise ConnectionError('发命令三次都失败：%r' % (last,))


def _duties(points):
    return [p['duty_pct'] for p in points]


def _ec_duties(ec):
    rows = ((ec.get('tables') or {}).get('CPU') or [])
    return [p.get('duty_pct') for p in rows]


def _ec_raw(ec, idx, field):
    rows = ((ec.get('tables') or {}).get('CPU') or [])
    return rows[idx].get(field) if idx < len(rows) else None


def _payload(name, duties):
    msg = {'Action': 'SET_FAN_SPEED_CURVE_SETTING', 'Name': name, 'Type': 'CPU'}
    for i, duty in enumerate(duties):
        msg['T%d' % i] = int(duty)
    return msg


def _diff(before, after):
    out = []
    for i in range(min(len(before), len(after))):
        if before[i] is not None and after[i] is not None and before[i] != after[i]:
            out.append((i, before[i], after[i]))
    return out


def main(argv=None):
    global PANEL
    ap = argparse.ArgumentParser(description='风扇曲线 MQTT 写入的首次可逆验证')
    ap.add_argument('--write', action='store_true', help='真的下发（默认只读预演）')
    ap.add_argument('--port', type=int, help='面板端口（优先于 data/port 和配置）')
    args = ap.parse_args(argv)

    cfg, err, _ = Config.load()
    if err:
        print('配置读取失败：%s' % err)
        return 2
    mqtt_cfg = cfg.get('hardware', 'mqtt', default={}) or {}
    ident = {k: mqtt_cfg.get(k) for k in ('client_id', 'username', 'password')}
    if not all(ident.values()):
        print('没有 broker 身份（data\\mqtt_identity.json），无法下发。')
        return 2
    try:
        PANEL = 'http://127.0.0.1:%d' % discover_panel_port(args.port, cfg)
        ident['host'], ident['port'] = local_mqtt_endpoint(mqtt_cfg)
    except (OSError, ValueError) as exc:
        print('端口配置无效，拒绝继续：%s' % exc)
        return 2

    print('== 1. 读基线 ==')
    try:
        ec0 = _http_json('/api/fan-curve')
    except Exception as exc:                        # noqa: BLE001
        print('EC 直读不可用：%r' % (exc,))
        return 3
    if not ec0.get('ok'):
        print('EC 直读失败：%s' % ec0.get('reason'))
        return 3
    table, status = _mqtt_read(ident)
    points = _parse_table(table)
    if not points:
        print('没拿到完整的 OEM Fan/Table（16 点 CPU）')
        return 3
    name = table.get('Name')
    if not name:
        print('Fan/Table 没带 Name，拒绝继续')
        return 3
    duties0 = _duties(points)
    ec_duties0 = _ec_duties(ec0)
    print('表名 %s' % name)
    print('OEM Duty: %s' % duties0)
    print('EC  Duty: %s' % ec_duties0)
    if len(ec_duties0) != 16 or any(a is None or b is None for a, b in zip(duties0, ec_duties0)):
        print('EC 直读的点不全，拒绝写')
        return 3
    mismatch = [(i, a, b) for i, (a, b) in enumerate(zip(duties0, ec_duties0)) if a != b]
    if mismatch:
        print('OEM 服务与 EC 直读不一致（点, OEM, EC）：%s，拒绝写' % mismatch)
        return 3

    target = points[TARGET]
    if target['up_t'] != 255:
        print('目标点 %d 的 UpT=%s，不是哨兵点；为了不影响风扇，拒绝写'
              % (TARGET, target['up_t']))
        return 3
    original = duties0[TARGET]
    changed = original - DELTA if original >= DELTA else original + DELTA
    if not 0 <= changed <= 100:
        print('目标点占空 %s 无法安全改 %s，拒绝写' % (original, DELTA))
        return 3

    protect = (status or {}).get('FAN_SafetyProtect')
    print('FAN_SafetyProtect = %r' % protect)
    if str(protect) != '0':
        print('高温保护开着（或状态没报），服务只会落盘不写 EC，拒绝写')
        return 3

    duties1 = list(duties0)
    duties1[TARGET] = changed
    print('\n== 2. 计划 ==')
    print('只改 CPU 点 %d：%d %% -> %d %%（该点 UpT=255，永远不会被查到）' %
          (TARGET, original, changed))
    print('报文：%s' % json.dumps(_payload(name, duties1), ensure_ascii=False))
    print('预期：EC 0x0F%02X 由 %d -> %d，其余 15 个 Duty 与全部 UpT/DownT 不变'
          % (0x20 + TARGET, original * 2, changed * 2))
    if not args.write:
        print('\n只读预演结束。要真下发请加 --write。')
        return 0

    restored = False
    wrote = False
    try:
        print('\n== 3. 下发修改值 ==')
        _mqtt_send(ident, _payload(name, duties1))
        wrote = True
        print('已下发。等 EC 新鲜直读（面板缓存 10 秒，最多等 45 秒）…')
        after_ts = time.time()
        ec1 = _ec_curve_fresh(after_ts)
        if not ec1.get('ok'):
            print('写后 EC 直读失败：%s' % ec1.get('reason'))
            return 4
        got = _ec_raw(ec1, TARGET, 'duty_raw')
        print('EC 点 %d duty_raw = %s（预期 %d）' % (TARGET, got, changed * 2))
        diffs = _diff(ec_duties0, _ec_duties(ec1))
        print('EC 占空变化点：%s' % diffs)
        ok_expect = (got == changed * 2 and diffs == [(TARGET, original, changed)])
        if not ok_expect:
            print('实测与纸上预期不符——立即还原，然后判失败')
            return 4

        print('\n== 4. 写回原值 ==')
        _mqtt_send(ident, _payload(name, duties0))
        restored = True
        print('已下发还原。等 EC 新鲜直读…')
        ec2 = _ec_curve_fresh(time.time())
        if not ec2.get('ok'):
            print('还原后 EC 直读失败：%s' % ec2.get('reason'))
            return 5
        diffs2 = _diff(ec_duties0, _ec_duties(ec2))
        print('还原后 EC 占空差异：%s' % diffs2)
        table2, _ = _mqtt_read(ident)
        points2 = _parse_table(table2) or []
        diffs3 = _diff(duties0, _duties(points2))
        print('还原后 OEM 表差异：%s' % diffs3)
        if diffs2 or diffs3:
            print('还原没有逐点回到基线，请人工检查！')
            return 5
        print('\n通过：一条 SET 只改了一个哨兵点，原样写回后整表逐点一致。')
        return 0
    finally:
        if wrote and not restored:
            print('\n[保险] 异常退出，正在写回原值…')
            try:
                _mqtt_send(ident, _payload(name, duties0))
                print('[保险] 已下发还原，请用面板 EC 直读再核对一次。')
            except Exception as exc:                # noqa: BLE001
                print('[保险] 还原失败：%r —— 请立即手动检查风扇表！' % (exc,))


if __name__ == '__main__':
    sys.exit(main())
