"""硬件通道抽象：统一能力表 + 优雅降级。

规则：面板上任何开关，只有在某个通道声明「已验证支持」时才允许点亮；
查不到、没验证过的能力一律标记为 unknown，UI 显示为不可用并说明原因。
绝不向用户假装能控制我们其实控制不了的东西。
"""
# 唯一的一套模式词定义在调度层，这里引用而不是再抄一份（scheduler 只 import deque，
# 不依赖本模块，没有循环）。
from app.policy.scheduler import TIER_LABELS

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
CAP_RGB_WRITE = 'lighting.rgb.write'   # 写灯效：协议已逆出，但可逆验证要机主在场

ALL_CAPS = (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_PL_WRITE,
            CAP_FAN_RPM, CAP_FAN_MODE, CAP_FAN_CURVE, CAP_FAN_CURVE_WRITE,
            CAP_TEMP_EC, CAP_BATTERY_LIMIT,
            CAP_BATTERY_MODE_WRITE, CAP_WINKEY_WRITE, CAP_DGPU, CAP_RGB, CAP_RGB_WRITE)

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
    # 措辞必须说清「找到了设备」不等于「能控制」：灯效的真实通路是 USB HID，
    # 设备枚举是只读的、随时可验，而写下去要发 Feature Report，还没做过可逆验证。
    CAP_RGB: '键盘/灯带设备已就位（USB HID 枚举）',
    CAP_RGB_WRITE: '写灯效（协议已逆出，未做可逆验证）',
}

MODES = ('office', 'balance', 'turbo')
# 措辞：机主明确说过本机 Creator Center 界面上没有「办公/均衡/狂暴」这三档，
# 这三个词不许当本机模式用（OEM 的 office/balance/turbo 只属于 GCUBridge 词汇）。
# 所以标签从调度那套四档词（TIER_LABELS：省电/均衡/流畅/性能）派生，
# 只加一个「OEM 硬件档」的来源后缀，免得和调度档位混淆 —— 全项目不许另造第三套说法。
_MODE_TIER = {'office': 'eco', 'balance': 'bal', 'turbo': 'perf'}
MODE_LABELS = dict([(m, '%s（OEM 硬件档）' % TIER_LABELS[t])
                    for m, t in _MODE_TIER.items()])
MODE_LABELS['unknown'] = '未知'
# 只在本机做过「写 → 观察 EC 真变了 → 还原」的档位才允许上按钮。
# 2026-09-30 12:19-12:24 实测：TURBO→PL1 10→75、OFFICE→PL1 75→10、再 TURBO→75，
# 两个方向都改到了 EC 的 `0x0783`，都能还原。Gaming/balance 那一档**没验过**，
# 而且 OEM 枚举里 OperatingMode 只认得到 0 和 2，先不放按钮。
MODE_VERIFIED = ('office', 'turbo')

# EC 风扇字节（OEM 枚举 MyFanCTLByteFlag 的名字）→ 硬件模式。
# ⚠️ 2026-09-30 12:19 实测把这个方向**纠正过来了**：这个字节是**服务的输出，不是输入**。
# 早先根据全表差分（按一次键，`0x0751` 0x10↔0xA0 的同时 PL1 75↔10）判成
# 「它是硬件模式总开关，功耗墙是它的结果」——**因果搞反了**。真实机制是：
# 按键 → EC 抬一个 WMI 事件（`176 = OSD_FanModeSwitch`）→ 服务的 `ModeSwitchChanged()`
# 从它自己内部的模式推进 → `SetUserProfile` 一次性写 PL、风扇表**和这个字节**。
# 所以我们**写**这个字节只会点亮 LED、把指示灯和服务的真实状态弄得不一致，
# PL 一个字都不动（实测：写完 100 秒、52 个采样点，PL1 死守 10 W）。
# 下面这张表因此只用于**读**（把服务写出来的字节认回模式），不用于写。
HW_MODE_BY_FAN_FLAG = {'Turbo_Mode': 'perf', 'Normal_Mode': 'auto'}
USER_FAN_PREFIX = 'User_Fan'          # 0x80 位：Mode/HiMode/Level1~5，全是低功耗档


def hw_mode_of_fan_flag(flag):
    """风扇字节 → 硬件模式；认不出来返回 None（上层照实写「未知」，不猜）。"""
    if flag and flag.startswith(USER_FAN_PREFIX):
        return 'eco'
    return HW_MODE_BY_FAN_FLAG.get(flag)


# EC 0x0786：CPU 的 TCC 偏移。这一个字节有两个 OEM 名字，两边都出自厂商自己的东西——
# 本机 Creator Center 的 ECSpec 叫它 ADDR_L1_PWM_DEFAULT_MYFAN3（风扇 PWM 默认值），
# 而客服 ROM 里那份 DSDT 的 ECMG 字段表把它拆成 APTC(bit0-6) + APTN(bit7)，
# 厂商服务侧的 SetCpuTccOffset 也是按后一种语义写的：用户开 TCC 偏移就写 offset|0x80，
# 不开就写 0。两边不一致时认 DSDT + 服务这一对，理由是它们互相印证，
# 而 ECSpec 那个名字属于 MyFan3 一代、在这一段地址上是过期的（同一段里
# 0x0743-0x0747 被 ECSpec 叫 MYFAN2_L1~L5_PWM，被 DSDT 叫 Dynamic Boost 那一组）。
# 为什么要读它：0x07D8-0x07DA 那三个「每档一个 TCC 偏移默认值」（本机 5/5/5）
# **只有 APTN 置位时才生效**。不看这一位就断言降频点是 TjMax-5，是拿默认值当现值。
TCC_ENABLE_BIT = 0x80
TCC_OFFSET_MASK = 0x7F


def tcc_offset_of(raw):
    """0x0786 → (使能位, 偏移 °C)。读不到就是 (None, None)，不猜。"""
    if raw is None:
        return None, None
    return bool(raw & TCC_ENABLE_BIT), raw & TCC_OFFSET_MASK


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
