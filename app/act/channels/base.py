"""硬件通道抽象：统一能力表 + 优雅降级。

规则：面板上任何开关，只有在某个通道声明「已验证支持」时才允许点亮；
查不到、没验证过的能力一律标记为 unknown，UI 显示为不可用并说明原因。
绝不向用户假装能控制我们其实控制不了的东西。
"""

CAP_MODE_READ = 'mode.read'          # 读当前硬件档位
CAP_MODE_WRITE = 'mode.write'        # 切档（办公/均衡/狂暴）
CAP_PL_READ = 'power_limit.read'     # 读 PL1/PL2/PL4
CAP_PL_WRITE = 'power_limit.write'
CAP_FAN_RPM = 'fan.rpm'              # 风扇实时转速
CAP_FAN_CURVE = 'fan.curve'          # 风扇曲线读写
CAP_TEMP_EC = 'ec.temp'              # EC 侧温度传感器
CAP_BATTERY_LIMIT = 'battery.limit'
CAP_DGPU = 'gpu.mux'
CAP_RGB = 'lighting.rgb'

ALL_CAPS = (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_PL_WRITE,
            CAP_FAN_RPM, CAP_FAN_CURVE, CAP_TEMP_EC, CAP_BATTERY_LIMIT,
            CAP_DGPU, CAP_RGB)

CAP_LABELS = {
    CAP_MODE_READ: '读取硬件档位',
    CAP_MODE_WRITE: '切换硬件档位',
    CAP_PL_READ: '读取功耗墙 PL1/PL2/PL4',
    CAP_PL_WRITE: '写入功耗墙',
    CAP_FAN_RPM: '风扇转速',
    CAP_FAN_CURVE: '风扇曲线',
    CAP_TEMP_EC: 'EC 温度传感器',
    CAP_BATTERY_LIMIT: '电池充电阈值',
    CAP_DGPU: '独显直连 MUX',
    CAP_RGB: '键盘/灯带 RGB',
}

MODES = ('office', 'balance', 'turbo')
MODE_LABELS = {'office': '办公', 'balance': '均衡', 'turbo': '狂暴', 'unknown': '未知'}


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
