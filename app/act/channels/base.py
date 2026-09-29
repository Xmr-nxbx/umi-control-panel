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
CAP_FAN_CURVE = 'fan.curve'          # 读 16 点风扇表（表布局见 ec_gpd.FAN_TABLE_*）
CAP_FAN_CURVE_WRITE = 'fan.curve.write'   # 写风扇表：布局已知，但可逆验证要机主在场
CAP_TEMP_EC = 'ec.temp'              # EC 侧温度传感器
CAP_BATTERY_LIMIT = 'battery.limit'
CAP_BATTERY_MODE_WRITE = 'battery.mode.write'   # 电池充电三档（平衡/健康/长效）
CAP_WINKEY_WRITE = 'winkey.write'               # Win 键锁定开关
CAP_DGPU = 'gpu.mux'
CAP_RGB = 'lighting.rgb'

ALL_CAPS = (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_PL_WRITE,
            CAP_FAN_RPM, CAP_FAN_MODE, CAP_FAN_CURVE, CAP_FAN_CURVE_WRITE,
            CAP_TEMP_EC, CAP_BATTERY_LIMIT,
            CAP_BATTERY_MODE_WRITE, CAP_WINKEY_WRITE, CAP_DGPU, CAP_RGB)

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
    CAP_FAN_CURVE: '读风扇曲线（16 点表）',
    CAP_FAN_CURVE_WRITE: '写风扇曲线（未做可逆验证）',
    CAP_TEMP_EC: 'EC 温度传感器',
    CAP_BATTERY_LIMIT: '电池充电阈值',
    CAP_BATTERY_MODE_WRITE: '切换电池充电档（平衡/健康/长效）',
    CAP_WINKEY_WRITE: 'Win 键锁定开关',
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


# 电池充电那三档。2026-09-30 01:16 观察3（logs/观察3 + tools/out/mqtt-watch.txt）两条通道
# 同时对上号了：机主按「平衡 → 健康 → 长效」点过去，GCUBridge 的 BatteryProtection/Control
# 依次收到 BALANCEDMODE / HEALTHYMODE / PERFORMANCEDMODE，EC 的 ADDR_AP_OEM_BYTE4 依次
# 0x09 → 0x19 → 0x29 → 0x09。低半字节恒为 9，高半字节就是档位。
# 界面词一律用 Creator Center 上的原话（平衡/健康/长效）——OEM 内部把「长效」叫
# PERFORMANCEDMODE，那个英文名只进日志，不上界面。
# 三档对应"百分之多少"这个问题**不成立**，不是还没测：GM7MG7P 的 EC 反汇编显示
# 这个家族的封顶是充电**电压** 0x0522/0x0523（按循环数/温度老化降额，
# Stationary≥200、Balanced≥100、High capacity 0 mV/cell），代码里不存在任何百分比；
# 0x0522 还 host 写不住（<101µs 被 EC 夺回）。那套「0x7C3/0x770 门控 → 0x87F 存储上限
# → 每秒读 0x7B9」的百分比模型来自别的板子（无界 14XA），判据是 0x742 bit2，
# 本机读到 0x742=2（bit2=0），机制不在场。见 README 6.11 第三节。
# 所以下面这张表只认档位名，永远不编百分比。
# 档位编码本身拿到了独立印证：0x07A6 的 bits[5:4] = 00 High capacity(Standard/长效)、
# 01 Balanced(Long_Life/平衡)、10 Stationary(Trickle/健康)，与观察3 抓到的
# 0x09/0x19/0x29 逐个吻合。另注：厂商服务退出时会强制把这一档拉回 High capacity。
BATTERY_MODE_BY_NIBBLE = {0: 'long', 1: 'balanced', 2: 'healthy'}
BATTERY_MODE_LABELS = {'balanced': '平衡', 'healthy': '健康', 'long': '长效'}
BATTERY_MODE_ACTION = {'balanced': 'BALANCEDMODE', 'healthy': 'HEALTHYMODE',
                       'long': 'PERFORMANCEDMODE'}
BATTERY_MODE_LOW_NIBBLE = 0x09


def battery_mode_of_oem_byte4(value):
    """ADDR_AP_OEM_BYTE4 → 电池档位 id；认不出来返回 None。

    只见过 0x09/0x19/0x29 三个值，所以低半字节不是 9 就当作「没见过的状态」，
    返回 None 让面板写「未知」——比拿半个证据去猜要安全。
    """
    if value is None or value & 0x0F != BATTERY_MODE_LOW_NIBBLE:
        return None
    return BATTERY_MODE_BY_NIBBLE.get(value >> 4)


def win_locked_of_status_byte(value):
    """ADDR_STAUTS_BYTE → Win 键是否锁定。

    2026-09-30 01:16 观察3：机主连点三次 Win 锁，这个字节 0→1→0→1，
    与同一时刻 MQTT 的 WINKEY_LOCK / WINKEY_UNLOCK / WINKEY_LOCK 一一对齐
    （01:16:02→03、01:16:12→13、01:16:19→21）。只见过 0 和 1，
    别的值说明这个字节还兼着别的意思，那就返回 None，不猜。
    """
    if value in (0, 1):
        return bool(value)
    return None


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
