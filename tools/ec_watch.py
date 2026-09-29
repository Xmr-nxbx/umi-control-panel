# -*- coding: utf-8 -*-
"""EC 变化观察器：只读，轮询寄存器，谁变了立刻打出来。

典型用法——找出某个功能到底落在哪个寄存器上：
  1. 运行本工具（默认 90 秒）；
  2. 期间按一次实体「造物者模式」键，或在 Creator Center 里切一次模式；
  3. 屏幕上出现变化的那个寄存器就是答案。

两种范围：
  默认        只盯 21 个「语义已知」的寄存器，0.5 秒一轮，抓按键这类瞬时事件；
  加 all      整张寄存器表（本机 125 项）全盯，2 秒一轮，用来破 Creator Center
              的其它功能（充电阈值、键盘灯、风扇曲线）。读得多但一发都不写。

用法：runtime\\python.exe tools\\ec_watch.py [秒数] [all]

只发 ECREAD，不写任何东西，不改电源设置，普通权限即可。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.ec_gpd import WATCH_REGS, EcChannel  # noqa: E402
from app.config import Config  # noqa: E402
from app.logx import Log  # noqa: E402

POLL_S = 0.5
POLL_ALL_S = 2.0


def main(argv):
    seconds = next((float(a) for a in argv if a.replace('.', '').isdigit()), 90.0)
    all_mode = 'all' in [a.lower() for a in argv]
    poll_s = POLL_ALL_S if all_mode else POLL_S
    report = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out',
                          'ec-watch-all.txt' if all_mode else 'ec-watch.txt')
    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    cfg, _e, _b = Config.load()
    ch = EcChannel(cfg, Log('ec-watch'))
    detail = ch.probe()
    out('EC 变化观察器  %s（观察 %.0f 秒，每 %.1f 秒轮询一次，只读%s）'
        % (time.strftime('%Y-%m-%d %H:%M:%S'), seconds, poll_s,
           '，全表模式' if all_mode else ''))
    out('通道状态：%s' % detail.get('reason'))
    if not ch.alive:
        return _finish(lines, ch, report, 1)

    if all_mode:
        watch = sorted(n for n in ch.registers if ch.addr(n) is not None)
    else:
        watch = [n for n in WATCH_REGS if ch.addr(n) is not None]
    out('监听 %d 个%s寄存器%s' % (
        len(watch), '全部' if all_mode else '语义已知',
        '' if all_mode else '：%s' % ', '.join(watch)))
    prev = {}
    for name in watch:
        value = ch.dev.read(int(ch.addr(name)))
        if value is not None:
            prev[name] = value & 0xFF
    out('\n初始值（非 0 的才列，全 0 的有 %d 个）：' % sum(1 for v in prev.values() if not v))
    for name in watch:
        if prev.get(name):
            out('    %-46s = %3d  (0x%02X)' % (name, prev[name], prev[name]))

    out('\n现在开始观察——请在这段时间里去操作要研究的那个功能：')
    out('  · 全表模式：打开 Creator Center，依次点「办公/均衡/狂暴」「键盘灯」「电池养护」'
        '「风扇自定义」这些开关，每点一下停两三秒；')
    out('  · 默认模式：按实体「造物者模式」键。')
    hits = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(poll_s)
        for name in watch:
            value = ch.dev.read(int(ch.addr(name)))
            if value is None:
                continue
            value &= 0xFF
            old = prev.get(name)
            prev[name] = value
            if old is None or old == value:
                continue
            hits.setdefault(name, []).append((old, value))
            out('  [%s] 变化 %-44s %3d → %3d   (0x%02X → 0x%02X)%s' % (
                time.strftime('%H:%M:%S'), name, old, value, old, value,
                '' if name in WATCH_REGS else '   ← 这不在语义表里，是新发现'))
    out('\n观察结束：捕捉到 %d 次变化，发送 %d 次只读请求。' % (
        sum(len(v) for v in hits.values()), ch.dev.reads))
    if hits:
        out('\n=== 汇总：这段时间里只有这些寄存器动过 ===')
        for name, seq in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            vals = sorted({v for _o, v in seq} | {seq[0][0]})
            out('  %-46s 变 %2d 次，取值 %s%s' % (
                name, len(seq), ['0x%02X' % v for v in vals],
                '' if name in WATCH_REGS else '   ← 新发现，值得进寄存器语义表'))
    else:
        out('没有任何变化。可能原因：这段时间没操作过；或该功能根本不经过 EC'
            '（比如纯软件层的灯效）。')
    return _finish(lines, ch, report, 0)


def _finish(lines, ch, report, code):
    ch.close()
    try:
        os.makedirs(os.path.dirname(report), exist_ok=True)
        with open(report, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
