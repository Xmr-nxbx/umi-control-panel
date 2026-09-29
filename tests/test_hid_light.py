# -*- coding: utf-8 -*-
"""灯效通道（ITE 8291 USB HID）的用例：全程不碰任何真实设备。

    runtime\\python.exe tests\\test_hid_light.py

编码器的判据不是我自己想的，是**厂商写在 BIOS 命令面里的样例字节**
（README 6.11 第九节：`LEDKB /SetData 0x08 0x03 0x0A 0x05 0x32 0x04 0x00 0xAF`、
`USBLB /SetMode 0x1A …` / `0x14 …` / `0x08 …`）。能逐字节复现它们，编码就是对的。

通道那几条用例把「只读」这件事钉死：探测只允许改能力表，
`read()` 必须返回空表，而且这个类**不许出现任何写方法**。
"""
import ctypes
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels import hid_ite8291 as H                       # noqa: E402
from app.act.channels.base import CAP_RGB, CAP_RGB_WRITE            # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print('%s %s%s' % ('  ok  ' if cond else ' FAIL ', name,
                       ('  ← ' + extra) if extra and not cond else ''))


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError:
        return True
    except Exception:                                              # noqa: BLE001
        return False
    return False


class FakeCfg:
    def get(self, *keys, default=None):
        return default


class FakeLog:
    def __init__(self):
        self.lines = []

    def info(self, msg, *a):
        self.lines.append(msg)

    def warn(self, msg, *a):
        self.lines.append(msg)

    def error(self, msg, *a):
        self.lines.append(msg)


def dev_info(dev_id, feature_len=9, usage_page=None, label='设备'):
    """造一份 HidEnumerator.describe() 形状的返回值。"""
    vid, pid = (H.KB_VID, H.KB_PID) if dev_id == 'kb' else (H.LB_VID, H.LB_PID)
    return {'path': '\\\\?\\hid#vid_%04x&pid_%04x#fake' % (vid, pid),
            'vid': vid, 'pid': pid, 'version': 1, 'product': 'fake',
            'usage_page': usage_page if usage_page is not None else
            (H.KB_USAGE_PAGE if dev_id == 'kb' else H.LB_USAGE_PAGE),
            'usage': 1, 'feature_len': feature_len, 'id': dev_id, 'label': label}


# 本机 2026-09-30 只读枚举的真实形状：每个 ITE 设备两个集合，
# MI_00 是 page 0xFF89 / Feature 17 字节（认不出来，不碰），
# MI_01 才是 page 0xFF12（键盘）/ 0xFF03（灯条）、Feature 9 字节。
# 故意把 MI_00 排在前面——挑选逻辑必须跳过它，而不是"拿第一个"。
REAL_SHAPE = (
    ('\\\\?\\hid#vid_048d&pid_ce00&mi_00#kb0', H.KB_VID, H.KB_PID, 0xFF89, 17),
    ('\\\\?\\hid#vid_048d&pid_ce00&mi_01#kb1', H.KB_VID, H.KB_PID, H.KB_USAGE_PAGE, 9),
    ('\\\\?\\hid#vid_048d&pid_6005&mi_00#lb0', H.LB_VID, H.LB_PID, 0xFF89, 17),
    ('\\\\?\\hid#vid_048d&pid_6005&mi_01#lb1', H.LB_VID, H.LB_PID, H.LB_USAGE_PAGE, 9),
)


def raw_enum(table=REAL_SHAPE):
    """把枚举器的两个底层方法换成假的，好在真逻辑上跑 find_targets。"""
    e = H.HidEnumerator()
    e.interface_paths = lambda: [r[0] for r in table]

    def describe(path):
        for p, vid, pid, page, flen in table:
            if p == path:
                return {'path': p, 'vid': vid, 'pid': pid, 'version': 2,
                        'product': 'ITE Device(8291)', 'usage_page': page,
                        'usage': 1, 'feature_len': flen}
        return None

    e.describe = describe
    return e


def fake_channel(found=None, boom=None):
    """造一个通道，把枚举器换成假的——一个真句柄都不开。"""
    ch = H.HidLightChannel(FakeCfg(), FakeLog())

    class FakeEnum:
        def find_targets(self):
            if boom:
                raise boom
            return found

    ch.enum = FakeEnum()
    return ch


BOTH = {'kb': dev_info('kb', label='键盘四区背光'),
        'lightbar': dev_info('lightbar', label='USB 灯条'),
        'ite_interfaces': [dev_info('kb'), dev_info('lightbar')]}


def test_vendor_kb_samples():
    for s in H.VENDOR_KB_SAMPLES:
        got = H.build_report(*s)
        check('复现厂商 LEDKB 样例 %s' % (s,), got == bytes((0,) + s), repr(got))
    check('报告长度恒 9 字节', all(len(H.build_report(*s)) == H.REPORT_LEN
                                 for s in H.VENDOR_KB_SAMPLES))
    check('首字节是报告 ID 0', all(H.build_report(*s)[0] == 0 for s in H.VENDOR_KB_SAMPLES))
    # 厂商样例第 5 个数据字节 0x32=50 正是亮度顶档，第 4 个 0x05 正是速度中档
    check('样例里的 Light=0x32 就是亮度顶档',
          H.VENDOR_KB_SAMPLES[0][4] == H.BRIGHTNESS_LEVELS[4] == 50)
    check('样例里的 Speed=0x05 就是速度第三档',
          H.VENDOR_KB_SAMPLES[0][3] == H.SPEED_LEVELS[2] == 5)
    check('两条样例只差 Save 字节',
          H.VENDOR_KB_SAMPLES[0][:7] == H.VENDOR_KB_SAMPLES[1][:7]
          and H.VENDOR_KB_SAMPLES[0][7] != H.VENDOR_KB_SAMPLES[1][7])


def test_vendor_lb_samples():
    for s in H.VENDOR_LB_SAMPLES:
        got = H.build_report(*s)
        check('复现厂商 USBLB 样例 %s' % (s,), got == bytes((0,) + s), repr(got))
    palette = H.VENDOR_LB_SAMPLES[1]
    check('palette_report 能拼出 0x14 那条白色',
          H.palette_report(palette[2], palette[3], palette[4], palette[5])
          == bytes((0,) + palette))
    check('0x08 那条的 Light=0x24 是亮度第四档',
          H.VENDOR_LB_SAMPLES[2][4] == H.BRIGHTNESS_LEVELS[3] == 36)
    check('三条灯条命令的 opcode 各自对上常量',
          (H.VENDOR_LB_SAMPLES[0][0], H.VENDOR_LB_SAMPLES[1][0], H.VENDOR_LB_SAMPLES[2][0])
          == (H.LB_OP_MODE, H.LB_OP_PALETTE, H.LB_OP_EFFECT))


def test_levels():
    check('亮度五档编码 0/8/22/36/50',
          [H.brightness_byte(i) for i in range(5)] == [0, 8, 22, 36, 50])
    check('速度五档编码 10/7/5/3/1',
          [H.speed_byte(i) for i in range(5)] == [10, 7, 5, 3, 1])
    check('亮度档位越界报错不夹', raises(H.brightness_byte, 5))
    check('亮度档位负数报错', raises(H.brightness_byte, -1))
    check('速度档位越界报错', raises(H.speed_byte, 5))
    check('档位号不是数字也报错', raises(H.speed_byte, 'x'))


def test_range_checks():
    check('opcode 超 255 报错', raises(H.build_report, 256))
    check('opcode 负数报错', raises(H.build_report, -1))
    check('Control 超界报错', raises(H.build_report, H.OP_EFFECT, 999))
    check('调色板索引超界报错', raises(H.palette_report, 300, 0, 0, 0))
    check('调色板 R 超界报错', raises(H.palette_report, 1, 256, 0, 0))
    check('RGBKB 每通道 0-50（厂商样例 494847）',
          H.rgbkb_levels(49, 48, 47) == (49, 48, 47))
    check('RGBKB 顶值 505050 收', H.rgbkb_levels(50, 50, 50) == (50, 50, 50))
    check('RGBKB 全 0 收', H.rgbkb_levels(0, 0, 0) == (0, 0, 0))
    check('RGBKB 51 拒', raises(H.rgbkb_levels, 51, 0, 0))
    check('RGBKB 负数拒', raises(H.rgbkb_levels, 0, -1, 0))


def test_parse_roundtrip():
    rep = H.build_report(H.OP_EFFECT, 3, 10, 5, 50, 4, 0, 0xAF)
    got = H.parse_report(rep)
    check('拆回来的字段名齐',
          set(got) == {'report_id', 'opcode', 'control', 'effect', 'speed',
                       'light', 'color_index', 'direction', 'save'})
    check('拆回来的值对得上', got['opcode'] == H.OP_EFFECT and got['light'] == 50
          and got['save'] == 0xAF)
    check('长度不对就报错', raises(H.parse_report, b'\x00' * 8))


def test_probe_found():
    ch = fake_channel(dict(BOTH))
    ch.probe()
    check('两个设备都在 → alive', ch.alive)
    check('两个设备都在 → lighting.rgb verified', ch.caps[CAP_RGB] == 'verified')
    check('写能力仍然是 blocked', ch.caps[CAP_RGB_WRITE] == 'blocked')
    check('状态 ok', ch.detail['state'] == 'ok', ch.detail['state'])
    check('找到的是哪两个说清楚', ch.detail['found'] == ['键盘四区背光', 'USB 灯条'])
    check('报告长度是设备自己报的 9', ch.detail['feature_len'] == {'kb': 9, 'lightbar': 9})
    check('探测会写一行日志', any('灯效' in ln for ln in ch.log.lines))


def test_probe_missing():
    ch = fake_channel({'kb': None, 'lightbar': None, 'ite_interfaces': []})
    ch.probe()
    check('一个都没找到 → 不 alive', not ch.alive)
    check('一个都没找到 → unsupported', ch.caps[CAP_RGB] == 'unsupported')
    check('一个都没找到 → 照实说 missing', ch.detail['state'] == 'missing')
    check('missing 的理由里带上要找的 VID:PID', '048D' in ch.detail['reason'].upper())

    only_kb = {'kb': dev_info('kb', label='键盘四区背光'), 'lightbar': None,
               'ite_interfaces': [dev_info('kb')]}
    ch2 = fake_channel(only_kb)
    ch2.probe()
    check('只找到键盘也算设备就位', ch2.alive and ch2.caps[CAP_RGB] == 'verified')
    check('只找到键盘时 found 只列它一个', ch2.detail['found'] == ['键盘四区背光'])


def test_probe_mismatch():
    ch = fake_channel({'kb': dev_info('kb', feature_len=8, label='键盘四区背光'),
                       'lightbar': dev_info('lightbar', label='USB 灯条'),
                       'ite_interfaces': []})
    ch.probe()
    check('报告长度不是 9 → 状态标 mismatch', ch.detail['state'] == 'mismatch')
    check('mismatch 的理由里点出长度', '8' in ch.detail['reason'], ch.detail['reason'])
    check('长度不符也照样算设备在（这是事实）', ch.caps[CAP_RGB] == 'verified')


def test_find_targets_picks_by_page():
    """挑选必须按 (VID, PID, usage page) 三元组——只认 VID/PID 会先撞上
    MI_00 那个 17 字节的集合，然后把 9 字节报告发进错误的口。"""
    found = raw_enum().find_targets()
    check('键盘挑中的是 0xFF12 那个集合',
          found['kb'] and found['kb']['usage_page'] == H.KB_USAGE_PAGE)
    check('键盘挑中的 Feature 长度是 9 不是 17', found['kb']['feature_len'] == 9)
    check('灯条挑中的是 0xFF03 那个集合',
          found['lightbar'] and found['lightbar']['usage_page'] == H.LB_USAGE_PAGE)
    check('灯条挑中的 Feature 长度是 9 不是 17', found['lightbar']['feature_len'] == 9)
    check('挑中的路径是 MI_01', found['kb']['path'].endswith('kb1')
          and found['lightbar']['path'].endswith('lb1'))
    check('ITE 接口一共 4 个都记下来', len(found['ite_interfaces']) == 4)
    check('认不出的那两个集合单列出来', len(found['other_collections']) == 2
          and all(i['feature_len'] == 17 for i in found['other_collections']))

    # 只有 MI_00（page 不对）时，宁可不认，也不能拿 17 字节的集合顶替
    only_mi00 = tuple(r for r in REAL_SHAPE if r[3] == 0xFF89)
    found2 = raw_enum(only_mi00).find_targets()
    check('只有 page 0xFF89 时不认成键盘', found2['kb'] is None)
    check('只有 page 0xFF89 时不认成灯条', found2['lightbar'] is None)
    check('这种情况两个都进 other_collections', len(found2['other_collections']) == 2)

    # 一个 ITE 设备都没有
    found3 = raw_enum(()).find_targets()
    check('没有 ITE 接口时全是 None', found3['kb'] is None and found3['lightbar'] is None
          and found3['ite_interfaces'] == [])


def test_probe_exception():
    ch = fake_channel(boom=OSError('枚举炸了'))
    ch.probe()
    check('枚举异常不冒出去', not ch.alive)
    check('枚举异常 → unsupported + error', ch.caps[CAP_RGB] == 'unsupported'
          and ch.detail['state'] == 'error')


def test_read_is_empty():
    ch = fake_channel(dict(BOTH))
    ch.probe()
    check('read() 返回空表（不往快照里塞猜出来的灯效状态）', ch.read() == {})
    check('tick() 不做事也不炸', ch.tick() is None)


def test_no_write_path():
    """这个通道**不许**有写方法。灯效写入要按 6.3 第 2 条先做可逆验证，
    在那之前代码里就不该存在能发 Feature Report 的入口——留着迟早被误用。"""
    ch = fake_channel(dict(BOTH))
    ch.probe()
    bad = [n for n in dir(ch)
           if re.match(r'^(set_|write|send|apply|emit|put_)', n) and callable(getattr(ch, n))]
    check('通道上没有任何写入口', not bad, ','.join(bad))
    bad_mod = [n for n in dir(H)
               if re.match(r'^(send|write|apply)_', n) and callable(getattr(H, n))]
    check('模块里也没有写函数', not bad_mod, ','.join(bad_mod))
    check('探测之后写能力没被偷偷点亮', ch.caps[CAP_RGB_WRITE] == 'blocked')
    check('blocked 的理由写明要机主在场', '机主' in ch.detail['write_reason'])


def test_status_and_helpers():
    ch = fake_channel(dict(BOTH))
    ch.probe()
    st = ch.status()
    check('status 报的是通道名', st['name'] == 'hid_light' and st['alive'])
    check('status 带上亮度/速度档位表',
          st['detail']['brightness_levels'] == [0, 8, 22, 36, 50]
          and st['detail']['speed_levels'] == [10, 7, 5, 3, 1])
    check('status 带上报告长度', st['detail']['report_len'] == 9)
    info = ch.lighting_info()
    check('lighting_info 给出设备与能力', set(info) == {'devices', 'detail', 'caps'})
    check('cbSize 跟着指针宽度走（64 位 8、32 位 6）',
          H._detail_cb_size() == (8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6))


def main():
    for fn in (test_vendor_kb_samples, test_vendor_lb_samples, test_levels,
               test_range_checks, test_parse_roundtrip, test_find_targets_picks_by_page,
               test_probe_found, test_probe_missing, test_probe_mismatch,
               test_probe_exception, test_read_is_empty, test_no_write_path,
               test_status_and_helpers):
        print('\n[%s]' % fn.__name__)
        fn()
    print('\n通过 %d / 失败 %d' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  FAIL %s' % f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
