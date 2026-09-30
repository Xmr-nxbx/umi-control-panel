# -*- coding: utf-8 -*-
"""OEM GCUBridge 通道观察器：**只订阅、只打印，一条命令都不发**。

为什么需要它：Creator Center 的每个开关（键盘背光、灯条、Win 锁、触摸板、电池养护）
最终都要经过本机 127.0.0.1:13688 这个 MQTT broker。直接听它说话，比拿 EC 全表差分
去猜寄存器可靠得多——报文体里就写着 Action 名和参数，那是 OEM 自己的词汇。

用法：runtime\\python.exe tools\\mqtt_watch.py [秒数] [--ask]      默认 180 秒

  --ask  连上之后在 **Setting/Control** 上发一条 {"Action":"GETSTATUS"} 问当前状态。
  --ask-all
         同时在几个控制主题上各问一条 GETSTATUS。2026-09-30 19:41 实测：只问 Setting/Control
         服务只回 Setting/Keyboard/HidLightbar 三份，**Fan/Status 与 Tray/Status 根本不报**，
         所以想知道功耗墙边界（护栏要用）或当前档位，得按主题分开问。
         不加就纯被动，只能听到「你点功能时 Creator Center 发出去的命令」和变化推送。
         实测不加 --ask 时 20 秒只收到一条 ProcessControl/Status，所以默认建议加上。

操作建议：**一次只点一个功能，点完等 10 秒再点下一个**，并把顺序记下来。
报告里每条消息都带时间戳，按你记的顺序就能一一对回去。

安全边界：
  * 除了 --ask/--ask-all 那几条 GETSTATUS，不 publish 任何主题；GETSTATUS 是问状态，不改设置；
  * 不打开 \\\\.\\ACPIDriver，不发 IOCTL，不读写任何 EC 寄存器；
  * 身份从 data\\mqtt_identity.json 读（已 gitignore），报告里绝不出现口令原文；
  * broker 会因为「clientId 同名进程不在」或「同名客户端后到」而踢线，所以本工具跑之前
    要先停面板，否则两个客户端抢同一个身份，谁也留不住。被踢之后本工具会**自动重连**
    继续观察到时间用完（2026-09-30 那次就是被踢之后剩下的 3 分钟全丢了）。
"""
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.mqtt_gcu import STATUS_TOPICS, MqttClient  # noqa: E402
from app.config import Config                                    # noqa: E402

# 控制主题也要听：Creator Center 点一下，命令就是从这些主题发出去的，
# 报文体里的 Action 名和参数就是我们要的语义（gcu_actions.json 里那份白名单的来源）。
CONTROL_TOPICS = ('Fan/Control', 'Setting/Control', 'Display/Control', 'Keyboard/Ctrl',
                  'MyRgbLightbar/Control', 'BatteryProtection/Control')
ALL_TOPICS = tuple(dict.fromkeys(CONTROL_TOPICS + STATUS_TOPICS))
RETRY_S = 3.0
# OEM 的 GETSTATUS 是**按控制主题分工**的：Setting/Control 问回来的只有 Setting/Keyboard 那几份，
# 功耗墙边界在 Fan/Status，得往 Fan/Control 问（2026-09-30 19:41 实测只问 Setting 拿不到墙）。
ASK_ALL_TOPICS = ('Setting/Control', 'Fan/Control', 'Keyboard/Ctrl')


def main(argv):
    seconds = next((float(a) for a in argv if a.replace('.', '').isdigit()), 180.0)
    low = [a.lower() for a in argv]
    ask_all = '--ask-all' in low
    ask = ask_all or '--ask' in low
    ask_topics = ASK_ALL_TOPICS if ask_all else ('Setting/Control',)
    report = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out',
                          'mqtt-watch.txt')
    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    cfg, _err, _bad = Config.load()
    m = cfg.get('hardware', 'mqtt', default={}) or {}
    host, port = m.get('host', '127.0.0.1'), int(m.get('port', 13688))
    client_id = m.get('client_id') or ''
    username = m.get('username') or ''
    password = m.get('password') or ''
    out('GCUBridge 通道观察器  %s（观察 %.0f 秒，%s）'
        % (time.strftime('%Y-%m-%d %H:%M:%S'), seconds,
           ('在 %d 个控制主题上各问一条 GETSTATUS' % len(ask_topics)) if ask
           else '只订阅不发送'))
    out('broker %s:%d，身份 %s（口令不打印）'
        % (host, port, client_id or '未配置'))
    if not client_id or not username:
        out('\n没有 broker 身份，连不上。请把 clientId/username/password 放进 '
            'data\\mqtt_identity.json（抄 mqtt_identity.example.json）。')
        return _finish(lines, report, 1)
    out('订阅 %d 个主题：%s' % (len(ALL_TOPICS), ', '.join(ALL_TOPICS)))

    last = {}
    dup = {}
    stat = {'seen': 0, 'sent': 0, 'kicks': 0}
    deadline = time.time() + seconds
    while time.time() < deadline:
        cli = MqttClient(host, port, client_id, username, password)
        try:
            cli.connect(timeout=3.0)
            cli.subscribe(ALL_TOPICS)
        except (OSError, ConnectionError) as exc:
            out('  [%s] 连接失败：%r' % (time.strftime('%H:%M:%S'), exc))
            if not stat['kicks']:
                out('      常见原因：GCUBridge 服务没在跑（scripts\\启用造物者档控制.bat），'
                    '或者面板还开着，两个客户端抢同一个身份被踢。')
            if _wait(deadline, RETRY_S):
                break
            continue
        if ask and not stat['sent']:
            for topic in ask_topics:
                cli.publish(topic, json.dumps({'Action': 'GETSTATUS'}))
                stat['sent'] += 1
            out('已发送 %d 条 GETSTATUS（问状态，不改任何设置）：%s'
                % (stat['sent'], ', '.join(ask_topics)))
        if not stat['kicks']:
            out('\n已连接。现在开始观察——请在这段时间里去点 Creator Center 里要研究的功能：')
            out('  键盘背光（调一格 / 换个灯效）、灯条开关、Win 键锁定、触摸板开关、')
            out('  电池充电阈值、独显直连（会要求重启，可以点了再取消）。')
            out('  一次只点一个，点完等 10 秒。屏幕提示（OSD）开关也算一个。\n')
        _observe(cli, out, last, dup, stat, deadline, ask_topics)
        try:
            cli.close()
        except OSError:
            pass
        if time.time() < deadline:
            stat['kicks'] += 1
            out('  [%s] 掉线了，%.0f 秒后重连继续观察（第 %d 次）'
                % (time.strftime('%H:%M:%S'), RETRY_S, stat['kicks']))
            if _wait(deadline, RETRY_S):
                break

    for topic, n in sorted(dup.items()):
        out('      （%s 最后那条重复了 %d 次）' % (topic, n))
    out('\n观察结束：收到 %d 条消息，涉及 %d 个主题，发送 %d 条，掉线重连 %d 次。'
        % (stat['seen'], len(last), stat['sent'], stat['kicks']))
    if not stat['seen']:
        out('一条都没收到。可能原因：GCUBridge 没在跑；身份一直被踢；'
            '或者这段时间 Creator Center 没有任何动作。')
    return _finish(lines, report, 0)


def _observe(cli, out, last, dup, stat, deadline, ask_topics):
    """听到时间用完或掉线为止；掉线就返回，由上层决定要不要重连。"""
    last_ping = time.time()
    cli.sock.settimeout(1.0)
    while time.time() < deadline:
        try:
            code, body = cli.read_packet()
        except socket.timeout:
            if time.time() - last_ping > 25:
                try:
                    cli.ping()
                except OSError as exc:
                    out('  [%s] 连接中断：%r' % (time.strftime('%H:%M:%S'), exc))
                    return
                last_ping = time.time()
            continue
        except (OSError, ConnectionError, struct.error) as exc:
            out('  [%s] 连接中断：%r' % (time.strftime('%H:%M:%S'), exc))
            return
        if code & 0xF0 != 0x30 or len(body) < 2:
            continue                      # PINGRESP 之类，不是我们要的 PUBLISH
        tlen = struct.unpack('>H', body[:2])[0]
        topic = body[2:2 + tlen].decode('utf-8', 'replace')
        raw = body[2 + tlen:].decode('utf-8', 'replace')
        stat['seen'] += 1
        if last.get(topic) == raw:
            dup[topic] = dup.get(topic, 0) + 1
            continue                      # 周期性的重复状态包，刷屏没有意义
        if dup.get(topic):
            out('      （上一条重复了 %d 次）' % dup.pop(topic))
        last[topic] = raw
        note = ''
        if topic in ask_topics and '"GETSTATUS"' in raw:
            note = '   ← 这是本工具自己发的那条，broker 原样回显，不是 Creator Center 干的'
        out('  [%s] %-24s %s%s' % (time.strftime('%H:%M:%S'), topic, _pretty(raw), note))


def _wait(deadline, seconds):
    """等到时间用完为止；返回 True 表示整轮观察该收工了。"""
    end = min(deadline, time.time() + seconds)
    while time.time() < end:
        time.sleep(0.2)
    return time.time() >= deadline


def _pretty(raw):
    """报文体压成一行；解不开就原样打出来（OEM 有时发的是紧凑 JSON）。"""
    try:
        obj = json.loads(raw or '{}')
    except ValueError:
        return raw
    if isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return raw


def _finish(lines, report, code):
    try:
        os.makedirs(os.path.dirname(report), exist_ok=True)
        with open(report, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print('\n报告已写到 %s' % report)
    except OSError as exc:
        print('\n报告写入失败：%r' % (exc,))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
