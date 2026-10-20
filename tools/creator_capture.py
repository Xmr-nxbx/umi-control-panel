# -*- coding: utf-8 -*-
"""让 Creator Center 当一次「示范」：你点它，我把原始 MQTT 报文抄下来。

    runtime\\python.exe tools\\creator_capture.py              # 交互：回车结束
    runtime\\python.exe tools\\creator_capture.py --seconds 300
    runtime\\python.exe tools\\creator_capture.py --minutes 20

原理：面板本来就握着 GCUBridge 的连接，这个工具只是让面板把收到的每一条 MQTT
报文写进 `logs\\mqtt-capture-*.jsonl`。**不新建连接、不发命令、不停面板。**
结束时会把每个主题的动作名/参数去重后打印出来，并在抓包文件旁写一份
`*.summary.txt`。

抓包只对「已经订阅的控制主题」有效（见 mqtt_gcu.SUBSCRIBE_TOPICS），
不是全量抓包；这些主题覆盖了 Creator Center 的灯光、风扇、电池、设置页。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.paths import data_path   # noqa: E402

CLICKS = """\
请按下面的顺序在 Creator Center 里点，**每点完一项停 10 秒**，让我能按时间对上：

  1) 灯光调节 → 键盘灯光管理：背光 关→开；灯效切到「呼吸」；调一次颜色；
     亮度 +1；速度 +1
  2) 灯光调节 → 海岸灯带：开→关；切一个模式；调一次颜色
  3) 造物者模式 → 性能配置：打开 Tcc Offset、设为 10 °C、保存；
     再关掉、保存
  4) 造物者模式 → 性能配置 → 风扇设定：打开风扇开关速度、设 300 ms、保存；
     再关掉、保存
  5) 风扇配置：只改一个点的转速（比如点 3 调到 50 %），保存；再改回原值、保存
  6) 电池保养：平衡 → 健康 → 长效（最后切回你原来的模式）
  7) 其他设置：关闭 OSD 开→关；USB 充电 开→关（最后恢复原状）

不要点：独显直连（要重启）、一键清理（会清回收站）、任何 BIOS/固件相关。
点完记得把 Tcc/风扇速度/电池档/背光恢复成你原来喜欢的状态。
"""


def _port():
    try:
        with open(data_path('port'), encoding='utf-8') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 8747


def _api(path, body=None, timeout=8):
    url = 'http://127.0.0.1:%d%s' % (_port(), path)
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data,
                                 headers={'Content-Type': 'application/json',
                                          'Origin': url.rsplit('/', 1)[0]},
                                 method='POST' if body is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _summarize(path):
    rows, first_ts, last_ts = [], None, None
    by_topic = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                ts = item.get('ts')
                topic = item.get('topic') or '?'
                payload = item.get('payload')
                first_ts = ts if first_ts is None else min(first_ts, ts or first_ts)
                last_ts = ts if last_ts is None else max(last_ts, ts or last_ts)
                rows.append((ts, topic, payload))
                key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                bucket = by_topic.setdefault(topic, {})
                if key not in bucket:
                    bucket[key] = {'n': 0, 'first': ts, 'last': ts, 'payload': payload}
                bucket[key]['n'] += 1
                bucket[key]['last'] = ts
    except OSError as exc:
        print('读不到抓包文件：%r' % (exc,))
        return None

    out = []
    out.append('抓包文件：%s' % path)
    out.append('共 %d 条，%d 个主题' % (len(rows), len(by_topic)))
    if first_ts:
        out.append('时间：%s ~ %s' % (time.strftime('%H:%M:%S', time.localtime(first_ts)),
                                     time.strftime('%H:%M:%S', time.localtime(last_ts))))
    for topic in sorted(by_topic):
        out.append('')
        out.append('== %s ==' % topic)
        items = sorted(by_topic[topic].values(), key=lambda v: v['first'] or 0)
        for item in items:
            body = json.dumps(item['payload'], ensure_ascii=False)
            if len(body) > 500:
                body = body[:500] + '…'
            out.append('  x%-3d %s  %s' % (item['n'],
                                           time.strftime('%H:%M:%S',
                                                         time.localtime(item['first'] or 0)),
                                           body))
    text = '\n'.join(out)
    print('\n' + text)
    summary_path = path + '.summary.txt'
    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print('\n摘要已写入：%s' % summary_path)
    except OSError as exc:
        print('\n摘要写盘失败：%r' % (exc,))
    return summary_path


def main(argv=None):
    ap = argparse.ArgumentParser(description='抓取 Creator Center 点出来的 MQTT 报文')
    ap.add_argument('--minutes', type=float, default=15, help='最长抓多久（分钟，默认 15）')
    ap.add_argument('--seconds', type=float, default=0,
                    help='不用回车，抓这么多秒后自动结束（0=交互等回车）')
    args = ap.parse_args(argv)

    print('让面板开始记录 MQTT 报文（只记录，不发命令、不停面板）…')
    try:
        st = _api('/api/mqtt-capture', {'action': 'start', 'minutes': args.minutes})
    except Exception as exc:                       # noqa: BLE001
        print('面板没响应：%r（先确认面板在跑，地址见 data\\port）' % (exc,))
        return 2
    if not st.get('active'):
        print('启动失败：%s' % (st.get('error') or st))
        return 3
    print('开始抓包：%s' % st.get('path'))
    print('最长 %.0f 分钟；' % args.minutes)
    print(CLICKS)

    try:
        if args.seconds and args.seconds > 0:
            print('%.0f 秒后自动结束…' % args.seconds)
            time.sleep(args.seconds)
        else:
            input('点完上面的项目后，回到这个窗口按回车结束抓包…')
    finally:
        try:
            st2 = _api('/api/mqtt-capture', {'action': 'stop'})
        except Exception as exc:                   # noqa: BLE001
            print('停止抓包失败：%r（面板可能重启了；文件仍在 logs\\ 下）' % (exc,))
            return 4
    path = st2.get('path') or st.get('path')
    print('抓包结束：%s 条' % st2.get('count'))
    if path:
        _summarize(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
