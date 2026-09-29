"""硬件通道抽象：统一能力表 + 优雅降级。

规则：面板上任何开关，只有在某个通道声明「已验证支持」时才允许点亮；
查不到、没验证过的能力一律标记为 unknown，UI 显示为不可用并说明原因。
绝不向用户假装能控制我们其实控制不了的东西。
"""

CAP_MODE_READ = 'mode.read'          # 读 OEM 上报的档位（office/balance/turbo）
CAP_MODE_WRITE = 'mode.write'        # 下发 OEM 档位
CAP_PL_READ = 'power_limit.read'     # 读 PL1/PL2/PL4
CAP_PL_WRITE = 'power_limit.write'
CAP_FAN_RPM = 'fan.rpm'              # 风扇实时转速
CAP_FAN_MODE = 'fan.mode'            # 风扇字节（OEM 枚举 MyFanCTLByteFlag），同时是硬件模式总开关
CAP_FAN_CURVE = 'fan.curve'          # 风扇曲线读写
CAP_TEMP_EC = 'ec.temp'              # EC 侧温度传感器
CAP_BATTERY_LIMIT = 'battery.limit'
CAP_DGPU = 'gpu.mux'
CAP_RGB = 'lighting.rgb'

ALL_CAPS = (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_PL_WRITE,
            CAP_FAN_RPM, CAP_FAN_MODE, CAP_FAN_CURVE, CAP_TEMP_EC, CAP_BATTERY_LIMIT,
            CAP_DGPU, CAP_RGB)

CAP_LABELS = {
    # 措辞跟着实测走（2026-09-30 全表观察，README 6.2）：本机的硬件模式总开关是
    # EC 风扇字节，功耗墙 PL1 是它的结果；OEM 那套 office/balance/turbo 是 GCUBridge
    # 才认的说法，机主确认 Creator Center 界面上根本没有这三档，所以两件事分开命名。
    CAP_MODE_READ: '读取 OEM 上报档位（office/balance/turbo）',
    CAP_MODE_WRITE: '下发 OEM 档位',
    CAP_PL_READ: '读取功耗墙 PL1/PL2/PL4',
    CAP_PL_WRITE: '写入功耗墙',
    CAP_FAN_RPM: '风扇转速',
    CAP_FAN_MODE: '造物者模式按键（硬件模式总开关）',
    CAP_FAN_CURVE: '风扇曲线',
    CAP_TEMP_EC: 'EC 温度传感器',
    CAP_BATTERY_LIMIT: '电池充电阈值',
    CAP_DGPU: '独显直连 MUX',
    CAP_RGB: '键盘/灯带 RGB',
}

MODES = ('office', 'balance', 'turbo')
MODE_LABELS = {'office': '办公', 'balance': '均衡', 'turbo': '狂暴', 'unknown': '未知'}

# EC 风扇字节（OEM 枚举 MyFanCTLByteFlag 的名字）→ 硬件模式。
# 取值一律用调度那套四档词（app.policy.scheduler.TIER_LABELS），不另造第三套说法。
# 依据是 2026-09-30 的全表差分：按一次键，这个字节 0x10↔0xA0 的同时
# PL1_SETTING_VALUE 75↔10、MYFAN2_L1/L4_PWM、DynamicBoost_MaxinumTGP 整组跟着换，
# 满载实测差 23~26%。也就是说它是硬件模式的总开关，功耗墙是它的结果。
HW_MODE_BY_FAN_FLAG = {'Turbo_Mode': 'perf', 'Normal_Mode': 'auto'}
USER_FAN_PREFIX = 'User_Fan'          # 0x80 位：Mode/HiMode/Level1~5，全是低功耗档


def hw_mode_of_fan_flag(flag):
    """风扇字节 → 硬件模式；认不出来返回 None（上层照实写「未知」，不猜）。"""
    if flag and flag.startswith(USER_FAN_PREFIX):
        return 'eco'
    return HW_MODE_BY_FAN_FLAG.get(flag)


class Channel:
    name = 'base'
    label = '抽象通道'

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.caps = {}            # cap -> 'verified' | 'unknown' | 'unsupported'
        self.detail = {}
        self.alive = False

    # --- 子类要实现 ---
    def probe(self):
        raise NotImplementedError

    def tick(self):
        pass

    def read(self):
        return {}

    def set_mode(self, mode):
        raise NotImplementedError

    def close(self):
        pass

    # --- 公共 ---
    def can(self, cap):
        return self.caps.get(cap) == 'verified'

    def status(self):
        return {'name': self.name, 'label': self.label, 'alive': self.alive,
                'caps': {k: v for k, v in self.caps.items()},
                'detail': self.detail}
