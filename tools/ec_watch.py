# -*- coding: utf-8 -*-
"""EC 变化观察器：只读，轮询语义寄存器，谁变了立刻打出来。

典型用法——找出「档位」到底落在哪个寄存器上：
  1. 运行本工具（默认 90 秒）；
  2. 期间按一次实体「造物者模式」键，或在 Creator Center 里切一次模式；
  3. 屏幕上出现变化的那个寄存器就是答案。

只发 ECREAD，不写任何东西，不改电源设置，普通权限即可。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.ec_gpd import WATCH_REGS, EcChannel  # noqa: E402
from app.config import Config  # noqa: E402
from app.logx import Log  # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'ec-watch.txt')
POLL_S = 0.5


def main(argv):
    seconds = float(argv[0]) if argv else 90.0
    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    cfg, _e, _b = Config.load()
    ch = EcChannel(cfg, Log('ec-watch'))
    detail = ch.probe()
    out('EC 变化观察器  %s（观察 %.0f 秒，每 %.1f 秒轮询一次，只读）'
        % (time.strftime('%Y-%m-%d %H:%M:%S'), seconds, POLL_S))
    out('通道状态：%s' % detail.get('reason'))
    if not ch.alive:
        return _finish(lines, ch, 1)

    watch = [n for n in WATCH_REGS if ch.addr(n) is not None]
    out('监听 %d 个语义寄存器：%s' % (len(watch), ', '.join(watch)))
    prev = {}
    for name in watch:
        value = ch.dev.read(int(ch.addr(name)))
        if value is not None:
            prev[name] = value & 0xFF
    out('\n初始值：')
    for name in watch:
        if name in prev:
            out('    %-46s = %3d  (0x%02X)' % (name, prev[name], prev[name]))

    out('\n现在开始观察——请在这段时间里按实体模式键，或切换一次 Creator Center 的模式：')
    changes = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(POLL_S)
        for name in watch:
            value = ch.dev.read(int(ch.addr(name)))
            if value is None:
                continue
            value &= 0xFF
            old = prev.get(name)
            prev[name] = value
            if old is None or old == value:
                continue
            changes += 1
            out('  [%s] 变化 %-44s %3d → %3d   (0x%02X → 0x%02X)' % (
                time.strftime('%H:%M:%S'), name, old, value, old, value))
    out('\n观察结束：共捕捉到 %d 次变化，发送 %d 次只读请求。' % (changes, ch.dev.reads))
    if not changes:
        out('没有任何变化。可能原因：这段时间没按过键；或该机型按键不经过这些寄存器'
            '（下一步可以把监听范围扩到整张寄存器表，仍然只读）。')
    return _finish(lines, ch, 0)


def _finish(lines, ch, code):
    ch.close()
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
