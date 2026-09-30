# -*- coding: utf-8 -*-
"""键盘背光写入的可逆验证（待办 #29）：读原值 → 开 → 回读 → 关回原值 → 回读。

为什么走 GCUBridge 而不是裸写 EC 或自己发 HID 报告：
  * 2026-09-30 已确认背光**不在 EC 上**（用户点背光时 EC 全表纹丝不动，notes 6.7）；
  * HID 那条路要新写发送通道，而 OEM 服务本来就在做同一件事，语义由它自己保证。

命令形状不是猜的，读的是 OEM 自己的实现（notes 6.11 十八）：
  `GCUService/MyRGBKeyboard/RGBKeyboard.cs` 的 `OnMqttMessage`：
      case "SetPower":  RGBKB_PowerStatus p = val["powerstatus"]; ...
  枚举 `RGBKB_PowerStatus`：Off=0, On=1, Lighting_off=2, Lighting_on=3, Welcome_off=4, Welcome_on=5
  本机 GCUService 的字符串表里有同一批日志文本
  （"RGBKeyboard_ITE | ILM_RGBKB_SetPower powerstatus = {0}"），所以这条在本机是同一套代码。

安全边界：
  * 只发 `Keyboard/Ctrl {"function":"SetPower","powerstatus":0|1}` 这一种命令，
    取值只允许 0/1；**收尾必须关回原状态**，回读不等于原值就退出码 3；
  * 拿不到初始 `Keyboard/Status` 就一个命令都不发（没有原值就谈不上还原）；
  * 不打开 \\\\.\\ACPIDriver、不发 IOCTL、不读写 EC 寄存器、不发送 HID 报告；
  * 身份从 data\\mqtt_identity.json 读（已 gitignore），口令不打印；
  * **跑之前必须停面板**：broker 认 clientId 同名进程，两个客户端抢同一身份会被踢。

用法：
    scripts\\停止面板.bat
    runtime\\python.exe tools\\kb_power_test.py              # 默认亮 8 秒后关回
    runtime\\python.exe tools\\kb_power_test.py 15           # 亮 15 秒
    runtime\\python.exe tools\\kb_power_test.py --keep       # 验完不关（自己想留着看）
"""
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.mqtt_gcu import MqttClient          # noqa: E402
from app.config import Config                             # noqa: E402

TOPIC_CTRL = 'Keyboard/Ctrl'
TOPIC_STATUS = 'Keyboard/Status'
PANEL_PORT = 8747
WAIT_STATUS_S = 20.0
ACTIONS = ('KB_POWER_ON', 'KB_POWER_OFF')


def panel_alive():
    """面板在跑就先拦下来：它和这个脚本用同一个 broker 身份，会互相踢下线。"""
    try:
        with socket.create_connection(('127.0.0.1', PANEL_PORT), timeout=0.4):
            return True
    except OSError:
        return False


def load_specs():
    """命令形状取自白名单，脚本里不另存一份（改了白名单这里跟着变）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'app', 'act', 'channels', 'gcu_actions.json')
    data = json.load(open(path, encoding='utf-8'))
    out = {}
    for name in ACTIONS:
        spec = data['actions'][name]
        obj = {spec.get('field', 'Action'): spec.get('value', name)}
        obj.update(spec.get('args') or {})
        power = obj.get('powerstatus')
        if power not in (0, 1):
            raise SystemExit('白名单条目 %s 的 powerstatus=%r 不在 0/1 里，拒发'
                             % (name, power))
        out[name] = (data['topics'][spec['topic']], json.dumps(obj, ensure_ascii=False))
    return out


def split_publish(body):
    """QoS0 PUBLISH：[2 字节主题长度][主题][负载]。"""
    if len(body) < 2:
        return None, b''
    tlen = struct.unpack('>H', body[:2])[0]
    return body[2:2 + tlen].decode('utf-8', 'replace'), body[2 + tlen:]


def read_until(cli, want, deadline, log):
    """等 `Keyboard/Status` 报出想要的 powerStatus。返回 (最后一帧, 是否等到)。"""
    last = {}
    cli.sock.settimeout(1.0)
    while time.time() < deadline:
        try:
            code, body = cli.read_packet()
        except socket.timeout:
            continue
        except ConnectionError as exc:
            log('  掉线：%r' % (exc,))
            return last, False
        if code & 0xF0 != 0x30:
            continue
        topic, raw = split_publish(body)
        if topic != TOPIC_STATUS:
            continue
        try:
            data = json.loads(raw.decode('utf-8', 'replace') or '{}')
        except ValueError:
            continue
        last = data.get('data') or data
        log('  [%s] powerStatus=%s 亮度=%s 灯效=%s 速度=%s'
            % (time.strftime('%H:%M:%S'), last.get('powerStatus'),
               last.get('brightNess') if last.get('brightNess') is not None else last.get('light'),
               last.get('effect'), last.get('speed')))
        if str(last.get('powerStatus', '')).lower() == want:
            return last, True
    return last, False


def ask_status(cli):
    cli.publish(TOPIC_CTRL, json.dumps({'Action': 'GETSTATUS'}, ensure_ascii=False))


def main(argv):
    keep = '--keep' in argv
    seconds = next((float(a) for a in argv if a.replace('.', '').isdigit()), 8.0)

    if panel_alive():
        print('面板还在跑（端口 %d），它和脚本抢同一个 broker 身份会被互踢。' % PANEL_PORT)
        print('先停面板：scripts\\停止面板.bat  或  runtime\\python.exe main.py --stop')
        return 2

    cfg, _err, _bad = Config.load()
    m = cfg.get('hardware', 'mqtt', default={}) or {}
    host, port = m.get('host', '127.0.0.1'), int(m.get('port', 13688))
    if not (m.get('client_id') and m.get('username')):
        print('没有 broker 身份（data\\mqtt_identity.json），连不上 %d。' % port)
        return 2

    specs = load_specs()
    print('键盘背光可逆验证  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    print('broker %s:%d，身份 %s（口令不打印）' % (host, port, m['client_id']))
    print('命令 %s %s' % specs['KB_POWER_ON'])

    cli = MqttClient(host, port, m['client_id'], m['username'], m['password'])
    try:
        cli.connect(timeout=3.0)
        cli.subscribe((TOPIC_STATUS,))
    except (OSError, ConnectionError) as exc:
        print('连接失败：%r（GCUBridge 在跑吗？scripts\\启用造物者档控制.bat）' % (exc,))
        return 2

    sent = 0
    try:
        ask_status(cli)
        sent += 1
        before, _got = read_until(cli, 'off', time.time() + 6.0, print)
        # 上面那 6 秒只是拿来收帧，回读到的可能是 On（本来就开着）
        was_on = str(before.get('powerStatus', '')).lower() == 'on'
        print('初始：powerStatus=%s 亮度=%s 灯效=%s 速度=%s'
              % (before.get('powerStatus'),
                 before.get('brightNess') or before.get('light'),
                 before.get('effect'), before.get('speed')))
        if not before:
            print('没读到 Keyboard/Status，一个命令都不发（没有原值就谈不上还原）。')
            return 3
        target = 'on' if was_on else 'off'

        print('\n>>> 发「开」，键盘灯应该亮起来。')
        cli.publish(*specs['KB_POWER_ON'])
        sent += 1
        _seen, ok_on = read_until(cli, 'on', time.time() + WAIT_STATUS_S, print)
        print('回读：期望 On → %s' % ('对上了' if ok_on else '**没对上**'))

        hold = 3.0 if was_on else seconds
        print('保持 %.0f 秒，请看着键盘。' % hold)
        time.sleep(hold)

        if keep:
            print('--keep：留着不关（原状态是 %s，记得自己关回去）' % target.upper())
            return 0

        name = 'KB_POWER_ON' if target == 'on' else 'KB_POWER_OFF'
        print('>>> 关回 %s' % target.upper())
        cli.publish(*specs[name])
        sent += 1
        back, ok_back = read_until(cli, target, time.time() + WAIT_STATUS_S, print)
        print('回读：powerStatus=%s → %s'
              % (back.get('powerStatus'), '已还原' if ok_back else '**没还原，请手动检查**'))
        return 0 if (ok_on and ok_back) else 3
    finally:
        print('\n本次发送 %d 条命令（GETSTATUS 1 条，其余都是 SetPower）。' % sent)
        try:
            cli.close()
        except OSError:
            pass


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
