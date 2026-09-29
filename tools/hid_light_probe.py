# -*- coding: utf-8 -*-
r"""只读：把本机 ITE 8291 灯效设备的 HID 集合列出来，并核对协议假设。

    runtime\python.exe tools\hid_light_probe.py

**一个字节都不往设备上发**：开句柄时 dwDesiredAccess=0（只问属性，不读不写），
问完立刻关，不调用 HidD_SetFeature / HidD_GetFeature，也不碰 EC、不碰驱动。
所以它和面板可以同时跑，不像 tools\ec_fantable_dump.py 那样要先停面板。

它核对三件事：
  1. 两个灯效集合在不在——键盘四区背光 048D:CE00 page 0xFF12、
     USB 灯条 048D:6005 page 0xFF03；
  2. 它们自报的 FeatureReportByteLength 是不是 9（协议假设的字节数）；
  3. 同一批设备上还有哪些**我们认不出来**的集合（本机是两个 page 0xFF89、
     Feature 17 字节的），列出来只为记录，不去试探。

顺带把厂商写在 BIOS 命令面里的样例报告用我们的编码器复现一遍——
能逐字节对上，说明编码没错；这一步同样只是打印，不发。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.hid_ite8291 import (  # noqa: E402
    BRIGHTNESS_LEVELS, REPORT_LEN, SPEED_LEVELS, HidEnumerator,
    VENDOR_KB_SAMPLES, VENDOR_LB_SAMPLES, build_report)


def main():
    enum = HidEnumerator()
    paths = enum.interface_paths()
    print('HID 在册接口 %d 个%s' % (len(paths),
                                  ('（枚举失败：%s）' % enum.error) if enum.error else ''))
    if not paths:
        return 1

    ite = []
    for path in paths:
        info = enum.describe(path)
        if info and info['vid'] == 0x048D:
            ite.append(info)
    print('其中 ITE（VID 048D）%d 个：' % len(ite))
    for i in ite:
        page = i['usage_page']
        print('  %04X:%04X  page=%s  usage=%s  Feature=%s 字节  ver=%d  %s'
              % (i['vid'], i['pid'],
                 ('0x%04X' % page) if page is not None else '?',
                 i['usage'], i['feature_len'], i['version'], i['product'] or '(无产品名)'))
        print('      %s' % i['path'])

    found = enum.find_targets()
    print()
    for key in ('kb', 'lightbar'):
        dev = found.get(key)
        if dev:
            ok = dev['feature_len'] == REPORT_LEN
            print('%-9s 找到  page=0x%04X  Feature=%s 字节  %s'
                  % (key, dev['usage_page'], dev['feature_len'],
                     '与协议假设一致' if ok else '**与协议假设的 %d 字节不符**' % REPORT_LEN))
        else:
            print('%-9s 没找到' % key)
    others = found.get('other_collections') or []
    if others:
        print('另有 %d 个认不出来的 ITE 集合（不碰）：%s'
              % (len(others), ', '.join('page=0x%04X/Feature=%s' % (o['usage_page'],
                                                                    o['feature_len'])
                                        for o in others)))

    print('\n亮度五档字节值 %s / 速度五档 %s' % (list(BRIGHTNESS_LEVELS), list(SPEED_LEVELS)))
    print('厂商样例复现（只打印，不发）：')
    bad = 0
    for tag, samples in (('LEDKB', VENDOR_KB_SAMPLES), ('USBLB', VENDOR_LB_SAMPLES)):
        for s in samples:
            got = build_report(*s)
            want = bytes((0,) + s)
            bad += got != want
            print('  %s %s  → %s  %s'
                  % (tag, ' '.join('%02X' % b for b in s),
                     ' '.join('%02X' % b for b in got), '一致' if got == want else '**不一致**'))
    print('\n写通道状态：blocked（新写通道要先做一次可逆验证，见 README 6.3 第 2 条）')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
