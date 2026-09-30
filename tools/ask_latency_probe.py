# -*- coding: utf-8 -*-
"""量一次「点按钮 → 面板读数翻过来」到底几秒（只走 HTTP，不碰 EC）。

    runtime\\python.exe tools\\ask_latency_probe.py

为什么需要：用户反馈的「开关启动关闭时间太长」，慢的不是硬件而是面板读数——
Setting/Status 不问了不推，而改之前有两个卡点：写完不开口问（只有 45 秒的周期 tick），
以及收包超时那一轮会把发送队列整个跳过（notes/hardware-channels.md 6.11 二十）。
修法（超时也倒队列 + 写完开 30 秒追问窗口 + 前端「正在生效」）生效没有，得掐表量。

背光这条命令做过可逆验证（2026-09-30 15:31），开完一定关回原值；
拿不到原始读数就一条都不发。Win 锁不在这里动：留给用户自己在面板上点。
"""
import json
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:8747'


def get(path):
    req = urllib.request.Request(BASE + path, headers={'Origin': BASE})
    with urllib.request.urlopen(req, timeout=6) as res:
        return json.loads(res.read().decode('utf-8'))


def post(path, body):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', 'Origin': BASE})
    with urllib.request.urlopen(req, timeout=6) as res:
        return json.loads(res.read().decode('utf-8'))


def kb_state():
    return ((get('/api/state').get('hardware') or {}).get('oem') or {}).get('kb_power_on')


def wait_for(want, limit):
    """每 0.2 秒读一次，返回读数变成 want 用了多久；超时返回 None。"""
    t0 = time.time()
    while time.time() - t0 < limit:
        if kb_state() == want:
            return time.time() - t0
        time.sleep(0.2)
    return None


def main():
    cur = kb_state()
    if cur is None:
        print('读不到 Keyboard/Status 的背光状态，什么都不发。')
        return 1
    print('起始背光 = %s' % cur)
    want = not cur
    r = post('/api/action', {'action': 'KB_POWER_ON' if want else 'KB_POWER_OFF'})
    print('已下发：%s -> %s' % (r.get('detail'), want))
    lat = wait_for(want, 25)
    print('面板读数翻成 %s 用了 %s' % (want, '没翻（25 秒超时）' if lat is None else '%.1f 秒' % lat))
    back = post('/api/action', {'action': 'KB_POWER_OFF' if want else 'KB_POWER_ON'})
    print('还原 %s -> %s' % (want, cur, ))
    lat2 = wait_for(cur, 25)
    print('还原后面板读数回到 %s 用了 %s' % (cur, '没回（25 秒超时）' if lat2 is None else '%.1f 秒' % lat2))
    if kb_state() != cur:
        print('注意：还原没对上，现在还是 %s，请手动确认' % kb_state())
        return 3
    print('背光已回到起始值 %s' % cur)
    print(back.get('detail'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
