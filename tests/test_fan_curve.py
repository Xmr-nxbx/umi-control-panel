# -*- coding: utf-8 -*-
"""16 点风扇表的解码测试（只读，不碰硬件）。

    runtime\\python.exe tests\\test_fan_curve.py

表布局来自同源机型反编译出的 FanTable_Manager1p5.SetEcFanTable / GetEcFanTable
（notes/hardware-channels.md 6.11 第一节：0x740 PROJECT_ID 与 0x78E bit6 两个前提都在本机只读复核过）。
基线数据是 **2026-09-30 03:10:52 本机那次只读转储的真实读数**
（tools/ec_fantable_dump.py，118 次读 0 次写；那份产物在 tools/out 不入仓库，
所以把当时的字节值抄在这里钉住）。以后谁改了地址算法，跑一遍就知道有没有算歪。

这里全程用假设备：一个 EC 读都不发。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels import ec_gpd                     # noqa: E402
from app.act.channels.base import CAP_FAN_CURVE, CAP_FAN_CURVE_WRITE   # noqa: E402
from app.act.channels.ec_gpd import EcChannel, FAN_KEY  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---- 本机实测基线（2026-09-30 03:10:52，硬件模式 = User_Fan_HiMode，即低功耗那一态）----
# 注意 0x0F0F/0x0F3F 也在下面这份内存里：厂商代码**从不读**它们，
# 所以有一个用例专门盯着「我们的实现也不许读」。
CPU_RAW = {
    0x0F00: 53, 0x0F01: 57, 0x0F02: 59, 0x0F03: 61, 0x0F04: 63, 0x0F05: 67,
    0x0F06: 69, 0x0F07: 71, 0x0F08: 255, 0x0F09: 255, 0x0F0A: 255, 0x0F0B: 255,
    0x0F0C: 255, 0x0F0D: 255, 0x0F0E: 255, 0x0F0F: 255,
    0x0F11: 48, 0x0F12: 50, 0x0F13: 60, 0x0F14: 62, 0x0F15: 64, 0x0F16: 68,
    0x0F17: 70, 0x0F18: 72, 0x0F19: 255, 0x0F1A: 255, 0x0F1B: 255, 0x0F1C: 255,
    0x0F1D: 255, 0x0F1E: 255, 0x0F1F: 255,
    0x0F20: 0, 0x0F21: 132, 0x0F22: 198, 0x0F23: 198, 0x0F24: 198, 0x0F25: 198,
    0x0F26: 198, 0x0F27: 198, 0x0F28: 198, 0x0F29: 198, 0x0F2A: 198, 0x0F2B: 198,
    0x0F2C: 198, 0x0F2D: 198, 0x0F2E: 198, 0x0F2F: 198,
}
GPU_RAW = {
    0x0F30: 52, 0x0F31: 53, 0x0F32: 56, 0x0F33: 58, 0x0F34: 60, 0x0F35: 63,
    0x0F36: 65, 0x0F37: 67, 0x0F38: 255, 0x0F39: 255, 0x0F3A: 255, 0x0F3B: 255,
    0x0F3C: 255, 0x0F3D: 255, 0x0F3E: 255, 0x0F3F: 255,
    0x0F41: 48, 0x0F42: 50, 0x0F43: 57, 0x0F44: 59, 0x0F45: 61, 0x0F46: 64,
    0x0F47: 66, 0x0F48: 68, 0x0F49: 255, 0x0F4A: 255, 0x0F4B: 255, 0x0F4C: 255,
    0x0F4D: 255, 0x0F4E: 255, 0x0F4F: 255,
    0x0F50: 0, 0x0F51: 152, 0x0F52: 200, 0x0F53: 200, 0x0F54: 200, 0x0F55: 200,
    0x0F56: 200, 0x0F57: 200, 0x0F58: 200, 0x0F59: 200, 0x0F5A: 200, 0x0F5B: 200,
    0x0F5C: 200, 0x0F5D: 200, 0x0F5E: 200, 0x0F5F: 200,
}
CTX_RAW = {0x0751: 0xA0, 0x0741: 0x01, 0x07C5: 0x80, 0x07C6: 0x07}
MEM = dict(CPU_RAW)
MEM.update(GPU_RAW)
MEM.update(CTX_RAW)

FAN_ENUM = {'Normal_Mode': 0x00, 'Turbo_Mode': 0x10,
            'User_Fan_Mode': 0x80, 'User_Fan_HiMode': 0xA0}


class FakeCfg:
    def get(self, *keys, default=None):
        return default


class FakeLog:
    def info(self, *a):
        pass

    def warn(self, *a):
        pass

    def error(self, *a):
        pass


class FakeDev:
    """只实现通道用到的那几个成员；一个真 IOCTL 都不发。"""

    def __init__(self, mem=None):
        self.mem = dict(MEM if mem is None else mem)
        self.reads = 0
        self.writes = 0
        self.asked = []
        self.fail = set()          # 这些地址读失败（返回 None）
        self.sneaky_write = False  # 模拟转储期间别的线程在写 EC（自动跟随改风扇字节）

    def read(self, addr):
        self.reads += 1
        self.asked.append(addr)
        if self.sneaky_write:
            self.writes += 1
        if addr in self.fail:
            return None
        return self.mem.get(addr)

    def write(self, addr, value):
        self.writes += 1
        return True


def channel(mem=None, alive=True):
    ch = EcChannel(FakeCfg(), FakeLog())
    ch.dev = FakeDev(mem)
    ch.alive = alive
    ch.registers = {FAN_KEY: 0x0751}
    ch.map = {'enums': {'MyFanCTLByteFlag': dict(FAN_ENUM)}}
    # probe() 跑完才是这套能力状态；测试里不跑 probe（它会去开真设备），照结果预置
    ch.caps[CAP_FAN_CURVE_WRITE] = 'blocked'
    return ch


# 测试里不许真等：一轮 96 次读 × 0.03 秒 = 3 秒，够跑几十遍用例了
ec_gpd.FAN_CURVE_GAP_S = 0.0


@case('实测基线：CPU 表逐点解对（升温/降温/占空比）')
def _():
    points, bad, n = channel()._read_curve_table('CPU', 0x0F00)
    assert bad == 0, bad
    assert n == 46, n
    got = [(p['up_t'], p['down_t'], p['duty_pct']) for p in points]
    want = [(0, 48, 0.0), (53, 50, 66.0), (57, 60, 99.0), (59, 62, 99.0),
            (61, 64, 99.0), (63, 68, 99.0), (67, 70, 99.0), (69, 72, 99.0),
            (71, 255, 99.0)]
    assert got[:9] == want, got[:9]


@case('实测基线：GPU 表逐点解对')
def _():
    points, bad, n = channel()._read_curve_table('GPU', 0x0F30)
    assert bad == 0, bad
    assert n == 46, n
    got = [(p['up_t'], p['down_t'], p['duty_pct']) for p in points]
    want = [(0, 48, 0.0), (52, 50, 76.0), (53, 57, 100.0), (56, 59, 100.0),
            (58, 61, 100.0), (60, 64, 100.0), (63, 66, 100.0), (65, 68, 100.0),
            (67, 255, 100.0)]
    assert got[:9] == want, got[:9]


@case('厂商从不读的 base+0x0F / base+0x10，我们也不读')
def _():
    ch = channel()
    ch._read_curve_table('CPU', 0x0F00)
    asked = set(ch.dev.asked)
    for never in (0x0F0F, 0x0F10):
        assert never not in asked, '0x%04X 不该被读' % never
    # 升温 15 + 降温 15 + 占空比 16 = 46，多一个地址都不许读
    assert len(asked) == 46, len(asked)
    assert ch.dev.writes == 0


@case('第 15 点的 0xFF 是哨兵，标出来但不当成温度')
def _():
    points, _, _ = channel()._read_curve_table('CPU', 0x0F00)
    assert points[15]['up_t'] == 255
    assert points[15]['sentinel'] is True
    assert points[8]['sentinel'] is False


@case('降温点：第 15 点与第 14 点同值（厂商读的是同一个地址 0x0F1F）')
def _():
    points, _, _ = channel()._read_curve_table('CPU', 0x0F00)
    assert points[14]['down_t'] == points[15]['down_t'] == 255
    assert points[15]['down_t'] is not None


@case('占空比是「百分比 ×2」，奇数原始值也要能解')
def _():
    mem = dict(MEM)
    mem[0x0F22] = 199          # 故意给个奇数，看会不会被整除吃掉
    points, _, _ = channel(mem)._read_curve_table('CPU', 0x0F00)
    assert points[2]['duty_pct'] == 99.5, points[2]['duty_pct']
    assert points[1]['duty_pct'] == 66.0


@case('GPU 表最后三格是厂商信箱，不当数据报出去')
def _():
    points, _, _ = channel()._read_curve_table('GPU', 0x0F30)
    for k in (13, 14, 15):
        assert points[k]['mailbox'] is True, k
        assert points[k]['duty_pct'] is None
        assert points[k]['duty_raw'] is None
    assert points[12]['mailbox'] is False
    cpu, _, _ = channel()._read_curve_table('CPU', 0x0F00)
    assert all(p['mailbox'] is False for p in cpu)      # 信箱只在 GPU 表


@case('读失败的点标成 incomplete，不抛异常也不猜值')
def _():
    ch = channel()
    ch.dev.fail = {0x0F13, 0x0F25}
    points, bad, _ = ch._read_curve_table('CPU', 0x0F00)
    assert bad == 2, bad
    assert points[2]['complete'] is False and points[2]['down_t'] is None
    assert points[5]['complete'] is False and points[5]['duty_pct'] is None
    assert points[1]['complete'] is True


@case('通道不在线时不发任何读，如实说原因')
def _():
    ch = channel(alive=False)
    ch.detail = {'state': 'blocked', 'reason': '驱动没在跑'}
    out = ch.fan_curve()
    assert out['ok'] is False and out['cached'] is False
    assert out['reason'] == '驱动没在跑'
    assert ch.dev.reads == 0


@case('读通之后：fan.curve 点成可用，fan.curve.write 一动不动')
def _():
    ch = channel()
    out = ch.fan_curve()
    assert out['ok'] is True, out['reason']
    assert out['reason'] == '', out['reason']
    assert out['writes'] == 0                 # 这个入口自己一个写都不发
    assert out['writes_during'] == 0
    # 92 个表地址 + 风扇字节 + 三个语义位。轮询线程同时在读，所以这个数必须自己数，
    # 拿通道计数器做差会虚高（实测 140）。
    assert out['reads'] == 96, out['reads']
    assert ch.caps[CAP_FAN_CURVE] == 'verified'
    assert ch.caps[CAP_FAN_CURVE_WRITE] == 'blocked'
    ctx = out['context']
    assert ctx['fan_mode_flag'] == 'User_Fan_HiMode', ctx
    assert ctx['hw_mode'] == 'eco', ctx
    assert ctx['ap_exist'] is True and ctx['split_tables'] is True
    assert ctx['bracket'] is True       # 1 = 没人在写表


@case('十秒内重复请求走缓存，不再打 EC')
def _():
    ch = channel()
    first = ch.fan_curve()
    reads = ch.dev.reads
    second = ch.fan_curve()
    assert second['cached'] is True
    assert ch.dev.reads == reads
    assert second['tables'] == first['tables']


@case('本轮期间通道另有写入：照实说这不是同一时刻的快照')
def _():
    ch = channel()
    ch.dev.sneaky_write = True      # 模拟自动跟随在转储期间改了风扇字节
    out = ch.fan_curve()
    assert out['writes'] == 0
    assert out['writes_during'] > 0
    assert '不是同一时刻的快照' in out['reason'], out['reason']
    assert out['ok'] is True        # 数据本身是完整的，只是不原子，不谎报失败


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print('  ok  %s' % name)
        except AssertionError as exc:
            bad += 1
            print('  FAIL %s -> %r' % (name, exc))
        except Exception as exc:                          # noqa: BLE001
            bad += 1
            print('  ERR  %s -> %s: %s' % (name, type(exc).__name__, exc))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
