# -*- coding: utf-8 -*-
"""面板只读体检：一条命令把「慢在哪一段」说清楚。

用法：

    runtime\\python.exe tools\\selfcheck.py
    runtime\\python.exe tools\\selfcheck.py --port 8748

只读，可随时运行：全程只用 GET（本仓库所有会改状态的动作都挂在 POST 上），
数据全部走面板的 HTTP 接口，不读 data/ 下任何文件，配置内容一个字不碰。

核心判据（notes/hardware-channels.md 6.11 第二十节的教训）：「面板读数翻得慢」
和「硬件真的生效慢」是两件事——看 OEM 状态快照的时间戳（state.oem.ts）：
读数陈旧 = 面板压根没去问；读数新鲜但值没变 = 硬件真的没变。
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def get(port, path, timeout=8):
    """只发 GET。这个脚本里不允许出现第二个请求方法。

    必须显式禁用代理：本机常驻系统代理（Clash 一类），urllib 默认会读注册表里的
    代理设置、把发往 127.0.0.1 的请求也送进代理——体检目标是本机面板，走代理
    既会把「连不上」错报成「端口上有别的服务」，还把请求漏给了不相干的进程。
    """
    url = 'http://127.0.0.1:%d%s' % (port, path)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace'))


def fmt_dur(sec):
    sec = int(sec)
    if sec < 60:
        return '%d 秒' % sec
    if sec < 3600:
        return '%d 分 %d 秒' % (sec // 60, sec % 60)
    return '%d 小时 %d 分' % (sec // 3600, sec % 3600 // 60)


def sec_channels(hw):
    lines = []
    bad = 0
    channels = hw.get('channels') if isinstance(hw, dict) else None
    if not channels:
        return ['所有通道：接口没有报通道列表（字段缺失，无法判断）'], 1
    for ch in channels:
        name = ch.get('label') or ch.get('name') or '?'
        if ch.get('alive'):
            lines.append('  %s：在线' % name)
        else:
            bad += 1
            detail = ch.get('detail') or {}
            reason = detail.get('reason') or detail.get('state') or '未给出原因'
            lines.append('  %s：不在线（%s）' % (name, reason))
    head = '所有通道在线' if bad == 0 else '%d/%d 条通道不在线' % (bad, len(channels))
    return ['通道：' + head] + lines, bad


def sec_freshness(hw, ping):
    problems = 0
    lines = []
    state = hw.get('state') if isinstance(hw, dict) else None
    oem = (state or {}).get('oem') or {}
    ts = oem.get('ts')
    now = time.time()
    if not _is_num(ts):
        lines.append('新鲜度：没有 OEM 状态快照（OEM 通道没连上或尚未收到回复，'
                     '「面板读数 vs 硬件生效」无从判起）')
        problems += 1
    else:
        age = now - float(ts)
        if age <= 60:
            lines.append('新鲜度：OEM 读数 %s前更新（新鲜）——这一刻读数没变，'
                         '就是硬件真的没变' % fmt_dur(age))
        else:
            lines.append('新鲜度：OEM 读数已 %s没更新（陈旧）——多半是面板没去问，'
                         '不是硬件没变；先看通道在不在线' % fmt_dur(age))
            problems += 1
    uptime = ping.get('uptime_s') if isinstance(ping, dict) else None
    if _is_num(uptime):
        lines.append('  面板已连续运行 %s' % fmt_dur(uptime))
    tick = ping.get('tick_age_s') if isinstance(ping, dict) else None
    if _is_num(tick):
        if tick <= 10:
            lines.append('  调度节拍 %s前走过（正常）' % fmt_dur(tick))
        else:
            lines.append('  调度节拍已 %s没走（面板卡死或正忙，守护进程该出手了）'
                         % fmt_dur(tick))
            problems += 1
    # 「最近一次动作生效用了多久」：接口里没有记录「动作下发 → 读数翻转」耗时的
    # 字段，只有 oem.ts 这个快照时间戳可用——缺这一项，如实说明，不为它改 app/。
    lines.append('  说明：面板暂未单独记录「动作生效耗时」字段，上面以 OEM 快照'
                 '新鲜度代替（这正是判断读数慢还是硬件慢的主判据）')
    return lines, problems


def sec_caps(hw):
    problems = 0
    caps = hw.get('caps') if isinstance(hw, dict) else None
    if not isinstance(caps, dict) or not caps:
        return ['能力表：接口没有报能力表（字段缺失，无法判断）'], 1
    groups = {'verified': [], 'blocked': [], 'unknown': []}
    for cap, info in sorted(caps.items()):
        state = (info or {}).get('state') or 'unknown'
        if state == 'verified':
            groups['verified'].append(cap)
        elif state == 'blocked':
            groups['blocked'].append(cap)
        else:
            # unknown / missing / unsupported 一律归「未验证/不可用」堆，带原词
            groups['unknown'].append('%s(%s)' % (cap, state))
    lines = ['能力：可用 %d 项 / 已封禁 %d 项 / 未验证或不可用 %d 项'
             % (len(groups['verified']), len(groups['blocked']), len(groups['unknown']))]
    lines.append('  可用：%s' % ('、'.join(groups['verified']) or '（无）'))
    lines.append('  已封禁（按设计锁住，不许从这里打开）：%s'
                 % ('、'.join(groups['blocked']) or '（无）'))
    lines.append('  未验证/不可用：%s' % ('、'.join(groups['unknown']) or '（无）'))
    return lines, problems


def sec_logs(logs):
    lines = []
    bad = 0
    rows = logs.get('lines') if isinstance(logs, dict) else None
    if not isinstance(rows, list):
        return ['日志：接口没有返回日志行（字段缺失，无法判断）'], 1
    hits = [ln for ln in rows if 'ERROR' in ln or 'Traceback' in ln]
    if not hits:
        return ['日志：最近 %d 行里没有 ERROR / Traceback' % len(rows)], 0
    bad = 1
    lines.append('日志：最近 %d 行里有 %d 行 ERROR / Traceback（最后 3 条）：'
                 % (len(rows), len(hits)))
    for ln in hits[-3:]:
        lines.append('  %s' % ln.strip())
    return lines, bad


def sec_health(health):
    """体检接口只取「不通过且非可选」的行，给总判定参考。"""
    rows = health.get('rows') if isinstance(health, dict) else None
    if not isinstance(rows, list):
        return '体检接口没有返回结果', 1
    fails = [r for r in rows if not r.get('ok') and not r.get('optional')]
    if not fails:
        return '体检 %d 项全部通过' % len(rows), 0
    names = '、'.join(str(r.get('name') or '?') for r in fails)
    return '体检 %d 项里有 %d 项不通过（%s）' % (len(rows), len(fails), names), 1


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def main(argv=None):
    parser = argparse.ArgumentParser(description='面板只读体检（全程 GET，可随时运行）')
    parser.add_argument('--port', type=int, default=8747, help='面板端口（默认 8747）')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

    try:
        ping = get(args.port, '/api/ping')
    except urllib.error.HTTPError:
        print('端口 %d 上有服务，但没有 /api/ping——不像本面板（版本过旧或端口被别的程序占了）。'
              % args.port)
        return 2
    except (urllib.error.URLError, OSError, ValueError):
        print('面板没在跑（127.0.0.1:%d 连不上）。'
              '先用 scripts\\启动面板.bat 把面板拉起来，再跑本脚本。' % args.port)
        return 2

    # 面板活着才继续；后面每个接口单独容错，缺什么就在结论里说缺什么
    hw = logs = health = None
    try:
        hw = get(args.port, '/api/hardware')
    except Exception as exc:                                  # noqa: BLE001
        print('（/api/hardware 拿不到：%s，下面按字段缺失处理）' % type(exc).__name__)
    try:
        logs = get(args.port, '/api/logs?n=200')
    except Exception as exc:                                  # noqa: BLE001
        print('（/api/logs 拿不到：%s，下面按字段缺失处理）' % type(exc).__name__)
    try:
        health = get(args.port, '/api/health')
    except Exception as exc:                                  # noqa: BLE001
        print('（/api/health 拿不到：%s，体检项按缺失处理）' % type(exc).__name__)

    hw = hw or {}
    logs = logs or {}
    health = health or {}
    problems = 0

    print('—— 面板体检（http://127.0.0.1:%d，只读）——\n' % args.port)
    lines, bad = sec_channels(hw)
    problems += bad
    print('\n'.join(lines))
    print()
    lines, bad = sec_freshness(hw, ping)
    problems += bad
    print('\n'.join(lines))
    print()
    lines, bad = sec_caps(hw)
    problems += bad
    print('\n'.join(lines))
    print()
    lines, bad = sec_logs(logs)
    problems += bad
    print('\n'.join(lines))
    print()
    health_line, bad = sec_health(health)
    problems += bad
    print('体检项：%s' % health_line)

    if problems == 0:
        print('\n总判定：面板一切正常——通道全在线、OEM 读数新鲜、无错误日志。'
              '如果某个开关「按了没反应」而读数又是新鲜的，那就是硬件侧没生效，'
              '不是面板卡了。')
    else:
        print('\n总判定：发现 %d 处需要留意的点（见上）。' % problems)
    return 0


if __name__ == '__main__':
    sys.exit(main())
