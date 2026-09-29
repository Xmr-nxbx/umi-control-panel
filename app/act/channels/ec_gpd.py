r"""EC 直连主通道：走 OEM 驱动的 IOCTL_GPD_ACPI_ECREAD，普通用户权限即可。

这条路线是本机逆向确认后定下来的，过程写在 README 第 6 节，关键结论：

  * 微软文档化的 `\\.\ACPI`（IOCTL_ACPI_ASYNC_EVAL_METHOD）非管理员打不开（err=2），
    所以「自己拼 AeiC/ECRR 结构发给 ACPI.sys」这条路在这台机器上走不通；
  * OEM 驱动 UWACPIDriver.sys 创建的是 `\\.\ACPIDriver`，**普通用户可读写打开**，
    它自己实现了 IOCTL_GPD_* 一族（设备类型 0x9C40，和微软的 0x32 完全不同）；
  * 驱动的 ECREAD 处理函数把调用者输入缓冲的**前 4 字节**当作地址，memcpy 进
    ACPI_METHOD_ARGUMENT（Type=0/DataLength=4），自己拼 'AeiC'+'ECRR'+Length 0x28+
    Version 1，再用 0x32C004 交给 ACPI.sys，最后校验 'AeoB' 响应；
  * 实测：读电量寄存器返回 100，与 GetSystemPowerStatus 的 100% 完全一致 → 布局证实。

寄存器「名字 → 地址」的对应表不在仓库里（OEM 私有定义，README 第 8 节的承诺），
由 tools/gen_ec_map.py 在每台机器上从本机安装的 Creator Center 生成到
data/ec_map.local.json。本文件只按名字取地址——**唯一的例外**是 16 点风扇表
（见 FAN_TABLE_BASE 那段注释：厂商代码里就是字面量，反射表拿不到）。

安全约束：
  * 只发确认过的 IOCTL 码，绝不穷举、绝不试错布局；
  * 连续失败即熔断，停止请求并按周期复查，不空转打驱动；
  * 写操作（切档/PL/风扇）默认关闭，需要 config.hardware.ec.allow_write=true，
    且 ECWRITE 的输入结构必须先逆向确认——没确认前直接拒绝，不猜。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import time

from app.act.channels.base import (CAP_BATTERY_LIMIT, CAP_FAN_CURVE,
                                   CAP_FAN_CURVE_WRITE, CAP_FAN_MODE,
                                   CAP_FAN_RPM, CAP_MODE_READ, CAP_MODE_WRITE,
                                   CAP_PL_READ, CAP_PL_WRITE, CAP_TEMP_EC, MODES,
                                   battery_mode_of_oem_byte4, hw_mode_of_fan_flag,
                                   win_locked_of_status_byte)
from app.paths import data_path
from app.sense.system import power_status

DEVICE = r'\\.\ACPIDriver'
MAP_NAME = 'ec_map.local.json'
IOCTL_ECREAD = 0x9C40A488
IOCTL_ECWRITE = 0x9C40A48C
BUF_SIZE = 4

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value

POLL_FAST_S = 2.0
POLL_SLOW_S = 60.0
FAIL_BREAKER = 5
REPROBE_S = 60.0

# 每 2 秒读一次的高频项（风扇/功耗/档位候选），以及每 60 秒读一次的低频项
FAST_GROUPS = (
    ('fan', ('ADDR_EC_MAIN_FAN_RPM_BYTE1', 'ADDR_EC_MAIN_FAN_RPM_BYTE2',
             'ADDR_EC_SECOND_FAN_RPM_BYTE1', 'ADDR_EC_SECOND_FAN_RPM_BYTE2',
             'ADDR_EC_MAIN_FAN_L_DUTY_BYTE', 'ADDR_EC_MAIN_FAN_R_DUTY_BYTE',
             'ADDR_MAFAN_CONTROL_BYTE', 'ADDR_FAN_ALERT_BYTE')),
    ('power', ('ADDR_PL1_SETTING_VALUE', 'ADDR_PL2_SETTING_VALUE', 'ADDR_PL4_SETTING_VALUE',
               'ADDR_CPU_VRM_CURRENT_LIMIT_BYTE', 'ADDR_CPU_VRM_MAXI_CURRENT_LIMIT_BYTE',
               'ADDR_COMPLEX_POWER_STATUS', 'ecPowSource')),
    ('mode', ('ADDR_MyFanCCI_Mode_Index', 'ADDR_SILENTMODE_STATUS_BYTE',
              'ADDR_TRIGGER_BYTE', 'ADDR_STAUTS_BYTE', 'ADDR_AP_OEM_BYTE4')),
    ('battery', ('ecBt1RSOC', 'ecBt1Temperature')),
)
SLOW_GROUPS = (
    # ADDR_SUPPORT_BYTE6(0x78E) bit6 = IsSuportRamFan1p5：厂商服务就是靠它决定
    # 走不走 MyFanManager_RamFan1p5 那套 16 点风扇表（另两个判据是 0x740 的
    # PROJECT_ID 与注册表 CustomizeTarget）。这一位为 0 的话，GM7MG7P 那份
    # 风扇表逆向对本机就不适用，所以必须能看到它，而不是靠推。
    ('identity', ('ADDR_PROJECT_ID_BYTE', 'ADDR_ModuleID', 'ADDR_SUPPORT_BYTE1',
                  'ADDR_SUPPORT_BYTE2', 'ADDR_SUPPORT_BYTE6', 'ADDR_BIOS_INFO_3_BYTE',
                  'ADDR_EC_BIOS_INFO5', 'ADDR_OEMSERVICE_PROJECT_ID_BYTE')),
    ('defaults', ('ADDR_GAMING_PL1_DEFAULT_VALUE', 'ADDR_GAMING_PL2_DEFAULT_VALUE',
                  'ADDR_GAMING_PL4_DEFAULT_VALUE', 'ADDR_OFFICE_PL1_DEFAULT_VALUE',
                  'ADDR_OFFICE_PL2_DEFAULT_VALUE', 'ADDR_OFFICE_PL4_DEFAULT_VALUE',
                  'ADDR_BATTERYSAVER_PL1_DEFAULT_VALUE', 'ADDR_BATTERYSAVER_PL2_DEFAULT_VALUE',
                  'ADDR_GAMING_TCC_OFFSET_DEFAULT_VALUE', 'ADDR_OFFICE_TCC_OFFSET_DEFAULT_VALUE',
                  'ADDR_TURBO_TCC_OFFSET_DEFAULT_VALUE')),
    ('battery_static', ('ADDR_EC_BT1CycleCount_BYTE1', 'ADDR_EC_BT1CycleCount_BYTE2',
                        'ADDR_BATTERY_CHARGE_LIMIT_UP', 'ADDR_BATTERY_CHARGE_LIMIT_DOWN')),
)

# 变化监听：只盯语义寄存器（风扇转速/占空比/温度这类每秒都在动的不进监听，否则刷爆日志）。
# 用途：实体「造物者模式」按键直连 EC，不走键盘通道，靠这个监听抓事件源；
# 也是找出「档位到底写在哪个寄存器」的零风险办法——按键前后对比即可。
WATCH_REGS = ('ADDR_MAFAN_CONTROL_BYTE', 'ADDR_MyFanCCI_Mode_Index', 'ADDR_TRIGGER_BYTE',
              'ADDR_TRIGGER_BYTE2', 'ADDR_STAUTS_BYTE', 'ADDR_SILENTMODE_STATUS_BYTE',
              'ADDR_COMPLEX_POWER_STATUS', 'ADDR_PL1_SETTING_VALUE', 'ADDR_PL2_SETTING_VALUE',
              'ADDR_PL4_SETTING_VALUE', 'ADDR_CPU_VRM_CURRENT_LIMIT_BYTE',
              'ADDR_CPU_VRM_MAXI_CURRENT_LIMIT_BYTE', 'ecPowSource',
              'ADDR_BATTERY_CHARGE_LIMIT_UP', 'ADDR_BATTERY_CHARGE_LIMIT_DOWN',
              'ADDR_FAN_ALERT_BYTE', 'ADDR_BATTERY_ALERT_BYTE', 'ADDR_SUPPORT_BYTE1',
              'ADDR_SUPPORT_BYTE2', 'ADDR_AP_OEM_BYTE', 'ADDR_AP_OEM_BYTE4',
              'ADDR_BIOS_OEM_BYTE')
WATCH_HISTORY = 60
# 实体「造物者模式」按键就是在这个字节上循环，而且它是**硬件模式总开关**：
#   全亮 Turbo_Mode(0x10) ↔ 不亮 User_Fan_HiMode(0xA0)，半亮 Normal_Mode(0x00)
# 2026-09-30 全表差分（logs/观察EC全表）实测：按一次键，它变的同时
#   PL1_SETTING_VALUE 75↔10、MYFAN2_L1/L4_PWM、DynamicBoost_MaxinumTGP、
#   ConfigurableTGP_DynamicBoost_CTRL_BYTE、AP_OEM_BYTE6 整组跟着换。
# （早前那次「按一次只有这一个字节变」是在 GCUBridge 停着的时候测的，别照抄。）
FAN_KEY = 'ADDR_MAFAN_CONTROL_BYTE'
# MyFanCTLByteFlag 里带这个位的全是「用户自己的曲线」：
# User_Fan_Mode=0x80、User_Fan_HiMode=0xA0、User_Fan_Level1~5=0x81~0x85
USER_FAN_BIT = 0x80
# 2026-09-30 01:16 观察3（logs/观察3 + tools/out/mqtt-watch.txt）把这两个字节钉死了，
# 两条通道的时间戳能一一对齐，不再是「候选」：
#   ADDR_STAUTS_BYTE   Win 键锁定：0=没锁，1=锁着。机主连点三次，MQTT 那边依次是
#                      WINKEY_LOCK / WINKEY_UNLOCK / WINKEY_LOCK，EC 这边依次
#                      0→1、1→0、0→1（01:16:02→03、12→13、19→21）。
#   ADDR_AP_OEM_BYTE4  电池充电那三档：高半字节就是档位，低半字节恒为 9。
#                      平衡 BALANCEDMODE→0x19、健康 HEALTHYMODE→0x29、
#                      长效 PERFORMANCEDMODE→0x09（也是开机默认）。
#                      解码在 channels.base，界面词用 Creator Center 的原话。
#   ADDR_TRIGGER_BYTE  写入握手的脉冲位：第三次点 Win 锁时它 0→1→0 闪了一下。
#                      只见过脉冲、没见过稳定值，所以只监听不解释。
# 已经弄清的：这三档**没有对应的充电百分比**。这个 EC 家族的封顶是充电电压
# 0x0522/0x0523（bank0 0xB158 周期重算，按循环数/温度老化降额），代码里不存在任何
# 百分比，而且 0x0522 host 写不住（实测 <101µs 被 EC 夺回）。CHARGE_LIMIT_UP/DOWN
# 全程读 0 也解释通了：百分比门控那套模型（0x7C3/0x770 → 0x87F → 每秒读 0x7B9）
# 来自别的板子，判据是 0x742 bit2，本机读 0x742=2 → bit2=0，机制不在场。
# 所以面板只报档位名、永不编百分比；open-revo 说的 100/80/60 是别的机型的说法。
# 反过来，已经排除的：灯效不在 EC 上。LIGHTBAR_CONTROL_BYTE、RGBKB_LEVEL_R/G/B、
# SINGLEKBL_ENABLE 在灯明明亮着的时候全是 0，机主点背光/灯条时全表也一个都没动；
# 走的是 GCUBridge 的 Keyboard/Ctrl（{"function":"SetPower","light":"3","speed":"2"}，
# 控制器 solution=ITE、type=FourZone）。**根因也查到了**：ITE 8291 是 USB HID 设备
# （048D:CE00 键盘 / 048D:6005 灯条），EC 侧灯条寄存器 0x0748-0x074B 在固件里零引用、
# 写了也没效果；SINGLEKBL_ENABLE(0x78C) 只是个状态镜像，EC 固件从内部 0x0826 生成
# bits5-7、厂商服务 SetBrightness 也 RMW 它、Fn+F6/F7 热键走 WMI 177/178——
# 三个写者共用一个字节，所以它进了 README 6.3 第 8 条的永久禁区。
# 触摸板上有实体拨动开关，OEM 也另有
# TOUCHPAD_TOGGLE_ON/OFF 命令——两条路会不会互相盖没验证过，面板只读。

# 16 点风扇表的布局。**这是本文件唯一一处写死地址**，因为这些地址不在 ECSpec 常量表里
# （厂商代码里就是字面量 3840/3856/3872），本机反射出来的寄存器表根本没有它们。
# 依据是 GM7MG7P 反编译出的 FanTable_Manager1p5.SetEcFanTable / GetEcFanTable
# ——那份代码与本机同源（0x740 PROJECT_ID=15、0x78E bit6=IsSuportRamFan1p5 都为 1，
# 两个前提都只读复核过，README 6.11 第一、六节）：
#   升温点 UpT[i]   = mem[base + i - 1]，i=1..15（base+0x0F 从不参与；UpT[0] 恒为 0）
#   降温点 DownT[i] = mem[base + 0x11 + i]，i=0..14（base+0x10 从不参与）；
#                     i=15 时厂商读的是 base+0x1F，也就是和 i=14 同一个地址
#   占空比 Duty[i]  = mem[base + 0x20 + i] / 2（寄存器里存的是「百分比 ×2」）
# 两个坑：第 15 点的升温值恒为 0xFF，是「到此为止」的哨兵不是温度；
# GPU 表最后三个占空比槽（0x0F5D/0x0F5E/0x0F5F）被 RefreshDefaultFanTableAll 借去
# 当信箱用了（0x0F5F=模式、0x0F5D=0xFD、0x0F5E=0xC9，500ms 轮询），所以那不是数据。
FAN_TABLE_BASE = (('CPU', 0x0F00), ('GPU', 0x0F30))
FAN_TABLE_POINTS = 16
FAN_TABLE_SENTINEL = 0xFF
GPU_DUTY_MAILBOX = (13, 14, 15)
# 按需读，不进轮询：一轮 92 个表地址 + 4 个语义位，约 30 次/秒
# （README 6.3 第 6 条的上限是 62）。
FAN_CURVE_GAP_S = 0.03
FAN_CURVE_MIN_S = 10.0            # 十秒内的重复请求直接回缓存，页面刷新不该打 EC
FAN_CURVE_NOTE = ('这是 Creator Center 最后一次编进 EC RAM 的表，不一定是此刻真正在管风扇的表'
                  '（EC 自己还有一套出厂曲线，切换条件没逆向出来）。只读，不写。')


def load_map(path=None):
    path = path or data_path(MAP_NAME)
    if not os.path.exists(path):
        return None, '本机寄存器表未生成：运行 scripts\\生成EC寄存器表.bat'
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        return None, '寄存器表读取失败：%r' % (exc,)
    regs = raw.get('registers') if isinstance(raw, dict) else None
    if not isinstance(regs, dict) or not regs:
        return None, '寄存器表内容为空，请重新生成'
    return raw, None


class EcGpd:
    r"""对 \\.\ACPIDriver 的最小封装：只发 ECREAD（和确认后的 ECWRITE）。"""

    def __init__(self, log=None):
        self.log = log
        self.handle = None
        self.error = None
        self.fail_streak = 0
        self.reads = 0
        self.writes = 0
        self._lock = threading.Lock()
        self._k32 = ctypes.windll.kernel32
        self._k32.CreateFileW.restype = wt.HANDLE
        self._k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                          wt.DWORD, wt.DWORD, wt.HANDLE]
        self._k32.DeviceIoControl.restype = wt.BOOL
        self._k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                              ctypes.c_void_p, wt.DWORD,
                                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]

    def open(self):
        with self._lock:
            if self.handle is not None:
                return True
            h = self._k32.CreateFileW(DEVICE, GENERIC_READ | GENERIC_WRITE,
                                      FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                      OPEN_EXISTING, 0, None)
            if h is None or h == INVALID_HANDLE:
                self.error = 'CreateFile 失败，Win32 错误 %d' % self._k32.GetLastError()
                return False
            self.handle = h
            self.error = None
            self.fail_streak = 0
            return True

    def close(self):
        with self._lock:
            if self.handle is not None:
                try:
                    self._k32.CloseHandle(self.handle)
                except Exception:                          # noqa: BLE001
                    pass
                self.handle = None

    def _ioctl(self, code, in_bytes, out_size=BUF_SIZE):
        if self.handle is None and not self.open():
            return None
        in_buf = (ctypes.c_ubyte * len(in_bytes))(*in_bytes)
        out_buf = ctypes.c_uint32(0)
        returned = wt.DWORD(0)
        with self._lock:
            if self.handle is None:
                return None
            ok = self._k32.DeviceIoControl(self.handle, code, ctypes.byref(in_buf),
                                           len(in_bytes), ctypes.byref(out_buf), out_size,
                                           ctypes.byref(returned), None)
        if not ok or returned.value < out_size:
            self.fail_streak += 1
            self.error = 'IOCTL 0x%08X 失败，Win32 错误 %d，返回 %d 字节' % (
                code, self._k32.GetLastError(), returned.value)
            if self.fail_streak >= FAIL_BREAKER:
                self.close()
            return None
        self.fail_streak = 0
        self.error = None
        return out_buf.value

    @staticmethod
    def _pack_addr(addr):
        """ECREAD 输入：4 字节地址。驱动会 memcpy 前 4 字节进 ACPI 参数。"""
        return bytes((addr & 0xFFFFFFFF).to_bytes(4, 'little'))

    @staticmethod
    def _pack_addr_value(addr, value):
        """ECWRITE 输入：8 字节 = 4 字节地址 + 1 字节值 + 3 字节填充。

        依据：驱动的 ECWRITE 处理函数建了 **2 个** ACPI 参数（'AeiC'+'ECRW'，
        参数字段 =2），第一个从 inBuf 拷 4 字节，第二个从 inBuf+4 拷 **1** 字节。
        """
        return bytes((addr & 0xFFFFFFFF).to_bytes(4, 'little')) + \
            bytes([(value & 0xFF)]) + bytes(3)

    def read(self, addr):
        value = self._ioctl(IOCTL_ECREAD, self._pack_addr(addr))
        if value is not None:
            self.reads += 1
        return value

    def write(self, addr, value):
        result = self._ioctl(IOCTL_ECWRITE, self._pack_addr_value(addr, value))
        if result is not None:
            self.writes += 1
        return result


class EcChannel:
    """把寄存器读数整理成面板能用的语义值。"""

    name = 'ec'
    label = 'EC 直连'

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.dev = EcGpd(log)
        self.caps = {c: 'unknown' for c in
                     (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_FAN_RPM,
                      CAP_FAN_MODE, CAP_FAN_CURVE, CAP_FAN_CURVE_WRITE, CAP_TEMP_EC,
                      CAP_BATTERY_LIMIT, CAP_PL_WRITE)}
        self.detail = {'device': DEVICE, 'ioctl_read': '0x%08X' % IOCTL_ECREAD,
                       'state': 'unknown', 'reason': '尚未探测'}
        self.alive = False
        self.registers = {}
        self.map = None
        self.mode_values = {}
        self.values = {}
        self.changes = []
        self._watch_prev = {}
        self._expect = {}
        self._last_fast = 0.0
        self._last_slow = 0.0
        self._last_reprobe = 0.0
        self._validated = False
        # 风扇表按需读：一轮 92 个地址要约 3 秒，所以缓存结果并用锁挡住并发重复读
        self._curve = None
        self._curve_at = 0.0
        self._curve_lock = threading.Lock()
        self.allow_write = bool(cfg.get('hardware', 'ec', 'allow_write', default=False))
        # 人工意图优先窗口：见 FAN_KEY / USER_FAN_BIT 的说明
        self.respect_s = float(cfg.get('hardware', 'ec', 'respect_external_s', default=900.0))
        self.hold_until = 0.0
        self.hold_by = None
        self.hold_last = None
        # 外部改风扇字节时的回调 fn(old, new)：由上层（cli）挂屏幕提示，
        # 通道本身不认识任何 UI，免得硬件层反过来依赖界面。
        # 注意回调跑在 EC 轮询线程里，必须不阻塞（屏幕提示用 PostMessage 就满足）。
        self.on_key = None

    # ---------- 人工意图优先 ----------
    def fan_lock_left(self, now=None):
        """还有多少秒不允许面板自动改风扇字节（0 = 可以自由跟随）。"""
        left = self.hold_until - (now or time.time())
        return round(left, 1) if left > 0 else 0.0

    def take_fan_control(self, who='面板'):
        """人工动作（按键或点按钮）之后的一阵别让自动跟随去抢方向盘。"""
        self.hold_until = time.time() + self.respect_s
        self.hold_by = who

    def fan_user_owned(self):
        """当前风扇字节是不是用户自己选的自定义曲线。

        这一条和优先窗口是两回事：窗口只在「运行中观察到按键」时生效，
        而重启后面板一上来读到的就是 0x80，照样会把它写掉——实测过。
        自定义曲线属于用户，只有用户点面板按钮才算交还控制权。
        """
        raw = (self.values.get('fan') or {}).get(FAN_KEY)
        return raw is not None and bool(raw & USER_FAN_BIT)

    # ---------- 地址表 ----------
    def addr(self, name):
        return (self.registers or {}).get(name)

    def _read_named(self, name):
        addr = self.addr(name)
        if addr is None:
            return None
        raw = self.dev.read(int(addr))
        return None if raw is None else (raw & 0xFF)

    # ---------- 探测 ----------
    def probe(self):
        now = time.time()
        self._last_reprobe = now
        self.map, err = load_map()
        if err:
            self._set_state('missing', err)
            self.alive = False
            return self.detail
        self.registers = self.map.get('registers') or {}
        self.mode_values = self.map.get('mode_values') or {}
        ioctls = self.map.get('ioctls') or {}
        if ioctls.get('IOCTL_GPD_ACPI_ECREAD'):
            self.detail['ioctl_read'] = '0x%08X' % ioctls['IOCTL_GPD_ACPI_ECREAD']
        if not self.dev.open():
            self._set_state('blocked', '%s 打不开：%s（驱动服务 UWACPIDriver 是否在跑？）'
                            % (DEVICE, self.dev.error))
            self.alive = False
            return self.detail
        # 自校验：EC 报的电量必须和系统 API 对得上，否则不声称「已验证」
        ec_soc = self._read_named('ecBt1RSOC')
        sys_ps = power_status()
        sys_soc = sys_ps.get('battery_pct')
        if ec_soc is None:
            self._set_state('blocked', '设备已打开，但读 EC 失败：%s' % self.dev.error)
            self.alive = False
            return self.detail
        self._validated = (sys_soc is not None and sys_soc >= 0 and abs(ec_soc - sys_soc) <= 2)
        self.alive = True
        if self._validated:
            fan_reg = self.addr(FAN_KEY)
            fan_enum = (self.map or {}).get('enums', {}).get('MyFanCTLByteFlag') or {}
            self.caps.update({
                CAP_FAN_RPM: 'verified', CAP_PL_READ: 'verified', CAP_TEMP_EC: 'verified',
                CAP_BATTERY_LIMIT: 'verified',
                # 读风扇表：布局是从同源机型的反编译代码里拿到的，但「读成功」要真读过
                # 一轮才算数，所以没读之前照实写 unknown，不因为「地址知道了」就点亮。
                CAP_FAN_CURVE: 'verified' if (self._curve or {}).get('ok') else 'unknown',
                CAP_FAN_CURVE_WRITE: 'blocked',
                # mode.read/write 说的是 OEM 那套 office/balance/turbo：EC 侧没有这个
                # 概念（机主确认 Creator Center 界面上也没有），只有 GCUBridge 认。
                # 本机真正的硬件模式走风扇字节，见 hw_mode_of_fan_flag / derived['hw_mode']。
                CAP_MODE_READ: 'unsupported', CAP_MODE_WRITE: 'blocked',
                CAP_PL_WRITE: 'blocked',
            })
            self.detail['fan_curve_reason'] = (
                '读已按同源机型的反编译布局实现（tools/ec_fantable_dump.py 先只读验证过一轮）；'
                '写表还不许动：可逆验证要机主在场（存原值→写→回读→超温还原），'
                '而且「EC 里哪张表此刻在管风扇」还没逆向清楚。')
            if fan_reg is None or not fan_enum:
                self.caps[CAP_FAN_MODE] = 'missing'
                self.detail['fan_mode_reason'] = '寄存器表里没有风扇模式寄存器或取值枚举'
            elif not self.allow_write:
                self.caps[CAP_FAN_MODE] = 'blocked'
                self.detail['fan_mode_reason'] = ('EC 写入未开启：'
                                                  'config.hardware.ec.allow_write=false')
            else:
                self.caps[CAP_FAN_MODE] = 'verified'
                self.detail['fan_mode_reason'] = ''
            self._set_state(
                'verified',
                'EC 只读已验证：EC 电量 %d%% 与系统 %d%% 一致。写入开关 allow_write=%s'
                % (ec_soc, sys_soc, 'true' if self.allow_write else 'false'))
            self.detail['write_reason'] = (
                '直接写 PL1_SETTING_VALUE 实测不生效（tools/ec_pl_test.py：写完自清零，'
                '那一组寄存器是 MyFan3 一代机型的落点）。本机功耗墙由风扇字节间接决定，'
                '要改就写 fan.mode，不裸写功耗墙。')
        else:
            self.caps.update({CAP_FAN_RPM: 'unknown', CAP_PL_READ: 'unknown',
                              CAP_MODE_READ: 'unknown', CAP_TEMP_EC: 'unknown'})
            self._set_state('unknown',
                            'EC 可读，但电量对不上（EC=%s%%，系统=%s%%），'
                            '先不当作已验证' % (ec_soc, sys_soc))
        return self.detail

    def _set_state(self, state, reason):
        self.detail.update({'state': state, 'reason': reason,
                            'reads': self.dev.reads, 'error': self.dev.error})

    # ---------- 轮询 ----------
    def tick(self):
        now = time.time()
        if not self.alive:
            if self.detail.get('state') in ('blocked', 'missing') and \
                    now - self._last_reprobe > REPROBE_S:
                self.probe()
            return
        if self.dev.handle is None and self.dev.fail_streak >= FAIL_BREAKER:
            if now - self._last_reprobe > REPROBE_S:
                self.probe()
            return
        if now - self._last_fast >= POLL_FAST_S:
            self._last_fast = now
            self._poll_groups(FAST_GROUPS)
        if now - self._last_slow >= POLL_SLOW_S:
            self._last_slow = now
            self._poll_groups(SLOW_GROUPS)

    def _poll_groups(self, groups):
        for group, names in groups:
            got = {}
            for name in names:
                value = self._read_named(name)
                if value is not None:
                    got[name] = value
            if got:
                self.values[group] = got
        self._watch()
        self._derive()

    def _watch(self):
        """盯语义寄存器的变化：实体按键、外部软件改档都会在这里现形。"""
        for name in WATCH_REGS:
            addr = self.addr(name)
            if addr is None:
                continue
            value = self.dev.read(int(addr))
            if value is None:
                continue
            value &= 0xFF
            old = self._watch_prev.get(name)
            self._watch_prev[name] = value
            if old is None or old == value:
                continue
            # 自己写的值会被下一次轮询看到，别把它当成「外部触发」报出来
            if self._expect.pop(name, None) == value:
                continue
            entry = {'ts': round(time.time(), 1), 'name': name, 'old': old, 'new': value}
            self.changes.append(entry)
            del self.changes[:-WATCH_HISTORY]
            if name == FAN_KEY:
                # 实体按键刚被按过：一段时间内把风扇交给用户，面板不再自动跟随。
                # 上一版没有这个让步，实测到按键改完 0 秒就被面板写回去。
                self.take_fan_control('实体按键')
                self.hold_last = {'old': old, 'new': value, 'ts': entry['ts']}
                if self.on_key:
                    try:
                        self.on_key(old, value)
                    except Exception as exc:               # noqa: BLE001
                        if self.log:
                            self.log.warn('[EC变化] 按键回调异常（忽略）：%r' % (exc,))
            if self.log:
                self.log.info('[EC变化] %s: %s → %s（不是本面板写的：实体按键或其它软件在改）'
                              % (name, old, value))

    def recent_changes(self, n=12):
        return list(self.changes[-n:])

    # ---------- 语义换算 ----------
    @staticmethod
    def _word(hi, lo):
        if hi is None or lo is None:
            return None
        return (hi << 8) | lo

    def _derive(self):
        fan = self.values.get('fan') or {}
        power = self.values.get('power') or {}
        mode = self.values.get('mode') or {}
        battery = self.values.get('battery') or {}
        static = self.values.get('battery_static') or {}
        ident = self.values.get('identity') or {}

        # 字节序是实测定的，不是猜的：
        #   风扇 BYTE1=9 BYTE2=129 → 高字节在前 = 2433 RPM（合理）；反过来 33033 不可能；
        #   循环次数 BYTE1=83 BYTE2=0 → 低字节在前 = 83 次（合理）；反过来 21248 次不可能。
        rpm = self._word(fan.get('ADDR_EC_MAIN_FAN_RPM_BYTE1'),
                         fan.get('ADDR_EC_MAIN_FAN_RPM_BYTE2'))
        rpm2 = self._word(fan.get('ADDR_EC_SECOND_FAN_RPM_BYTE1'),
                          fan.get('ADDR_EC_SECOND_FAN_RPM_BYTE2'))
        cycles = self._word(static.get('ADDR_EC_BT1CycleCount_BYTE2'),
                            static.get('ADDR_EC_BT1CycleCount_BYTE1'))
        ctl = fan.get(FAN_KEY)
        out = {
            'fan_rpm': rpm if self._plausible_rpm(rpm) else None,
            'fan_rpm_raw': rpm,
            'fan2_rpm': rpm2 if self._plausible_rpm(rpm2) else None,
            'fan2_rpm_raw': rpm2,
            'fan_duty_l': self._duty_pct(fan.get('ADDR_EC_MAIN_FAN_L_DUTY_BYTE')),
            'fan_duty_r': self._duty_pct(fan.get('ADDR_EC_MAIN_FAN_R_DUTY_BYTE')),
            'fan_ctl_byte': ctl,
            'fan_mode_flag': self.fan_flag_name(ctl),
            'fan_alert': fan.get('ADDR_FAN_ALERT_BYTE'),
            'pl1_setting': power.get('ADDR_PL1_SETTING_VALUE'),
            'pl2_setting': power.get('ADDR_PL2_SETTING_VALUE'),
            'pl4_setting': power.get('ADDR_PL4_SETTING_VALUE'),
            'vrm_limit': power.get('ADDR_CPU_VRM_CURRENT_LIMIT_BYTE'),
            'vrm_max_limit': power.get('ADDR_CPU_VRM_MAXI_CURRENT_LIMIT_BYTE'),
            'complex_power_status': power.get('ADDR_COMPLEX_POWER_STATUS'),
            'ec_power_source': power.get('ecPowSource'),
            'mode_index': mode.get('ADDR_MyFanCCI_Mode_Index'),
            'silent_mode': mode.get('ADDR_SILENTMODE_STATUS_BYTE'),
            'trigger_byte': mode.get('ADDR_TRIGGER_BYTE'),
            'status_byte': mode.get('ADDR_STAUTS_BYTE'),
            # 这两个解码的依据都是 2026-09-30 01:16 那次观察：EC 与 GCUBridge 两条通道
            # 的时间戳一一对齐（见 channels.base 里的注释），认不出来就是 None。
            'win_key_locked': win_locked_of_status_byte(mode.get('ADDR_STAUTS_BYTE')),
            'battery_mode_raw': mode.get('ADDR_AP_OEM_BYTE4'),
            'battery_mode': battery_mode_of_oem_byte4(mode.get('ADDR_AP_OEM_BYTE4')),
            'battery_pct_ec': battery.get('ecBt1RSOC'),
            'battery_temp_c': self._battery_temp(battery.get('ecBt1Temperature')),
            'battery_temp_raw': battery.get('ecBt1Temperature'),
            'battery_cycles': cycles,
            'charge_limit_up': static.get('ADDR_BATTERY_CHARGE_LIMIT_UP'),
            'charge_limit_down': static.get('ADDR_BATTERY_CHARGE_LIMIT_DOWN'),
            'project_id': ident.get('ADDR_PROJECT_ID_BYTE'),
            'module_id': ident.get('ADDR_ModuleID'),
            'source': self.name,
        }
        out['pl1'] = self._effective_pl(out, 1)
        out['pl2'] = self._effective_pl(out, 2)
        out['fan_boost'] = None if ctl is None else bool(ctl & 0x40)
        # 硬件模式：2026-09-30 的全表差分给了答案 —— 风扇字节就是总开关，
        # 按一次键它 0x10↔0xA0 的同时 PL1_SETTING_VALUE 75↔10、MYFAN2_L1/L4_PWM、
        # DynamicBoost_MaxinumTGP 整组跟着换（README 6.2）。映射表在 channels.base，
        # 取值用调度那套四档词，认不出来就是 None，面板照实写「未知」。
        out['hw_mode'] = hw_mode_of_fan_flag(out['fan_mode_flag'])
        # OEM 那套 office/balance/turbo 是 GCUBridge 的说法，本机 Creator Center
        # 界面上没有这三档（机主 2026-09-30 确认），EC 侧也不声称能读它。
        out['mode'] = None
        self.derived = out

    @staticmethod
    def _plausible_rpm(rpm):
        return rpm is not None and 0 <= rpm <= 12000

    @staticmethod
    def _duty_pct(raw):
        """占空字节是「百分比 ×2」：OEM 自己的 MyFan2SpeedByteFlag 枚举写着
        Speed50=100、Speed60=120、Speed70=140，实测空转读到 105/85 也对得上。"""
        if raw is None:
            return None
        return round(raw / 2.0, 1)

    @staticmethod
    def _battery_temp(raw):
        """实测 raw=244，按 0.1°C 解是 24.4°C（合理）；按 0.5°C-40 解是 82°C（电池早该报警）。"""
        if raw is None:
            return None
        return round(raw / 10.0, 1) if raw >= 100 else float(raw)

    def fan_flag_name(self, value):
        """用 OEM 自己的枚举名解 fan 控制字节，不自己发明叫法。"""
        if value is None:
            return None
        table = (self.map or {}).get('enums', {}).get('MyFanCTLByteFlag') or {}
        exact = [k for k, v in table.items() if v == value]
        if exact:
            return exact[0]
        bits = [k for k, v in table.items() if v and value & v == v]
        return '+'.join(sorted(bits)) if bits else None

    def _effective_pl(self, out, which):
        """当前生效的 PL：优先读「设置值」，为 0 时退回各模式的出厂默认值表。"""
        setting = out.get('pl%d_setting' % which)
        if setting:
            return setting
        defaults = self.values.get('defaults') or {}
        key = {1: 'ADDR_GAMING_PL1_DEFAULT_VALUE', 2: 'ADDR_GAMING_PL2_DEFAULT_VALUE'}.get(which)
        return defaults.get(key) if key else None

    # ---------- 风扇表（按需只读，一个写都不发） ----------
    def _bit(self, addr, bit):
        raw = self.dev.read(addr)
        time.sleep(FAN_CURVE_GAP_S)
        return None if raw is None else bool(raw & (1 << bit))

    def _read_curve_table(self, which, base):
        """读一张 16 点表，返回 (points, 没读全的点数, 本轮发出的读次数)。

        地址按 FAN_TABLE_BASE 上面那段注释的规则算，**不读厂商代码从不读的字节**
        （base+0x0F 和 base+0x10），也不做范围盲扫——兄弟板曾因为盲扫风扇转速寄存器
        把风扇扫停（README 6.11），所以这里一个多余的地址都不碰。
        """
        addrs = ([base + i for i in range(FAN_TABLE_POINTS - 1)]
                 + [base + 0x11 + i for i in range(FAN_TABLE_POINTS - 1)]
                 + [base + 0x20 + i for i in range(FAN_TABLE_POINTS)])
        mem = {}
        for addr in addrs:
            raw = self.dev.read(addr)
            time.sleep(FAN_CURVE_GAP_S)
            if raw is not None:
                mem[addr] = raw & 0xFF
        points = []
        incomplete = 0
        for k in range(FAN_TABLE_POINTS):
            # 厂商代码里 UpT[0] 直接赋 0，不从寄存器读；DownT[15] 读的是 base+0x1F，
            # 与 DownT[14] 同一个地址，所以最后一点的降温值不是「没有」。
            up = 0 if k == 0 else mem.get(base + k - 1)
            down = mem.get(base + (0x11 + k if k < FAN_TABLE_POINTS - 1 else 0x1F))
            duty = mem.get(base + 0x20 + k)
            mailbox = which == 'GPU' and k in GPU_DUTY_MAILBOX
            point = {'id': k, 'up_t': up, 'down_t': down,
                     'duty_raw': None if mailbox else duty,
                     'duty_pct': None if (mailbox or duty is None) else round(duty / 2.0, 1),
                     'sentinel': up == FAN_TABLE_SENTINEL,
                     'mailbox': mailbox}
            point['complete'] = (down is not None and (mailbox or duty is not None)
                                 and (k == 0 or up is not None))
            if not point['complete']:
                incomplete += 1
            points.append(point)
        return points, incomplete, len(addrs)

    def fan_curve(self):
        """按需转储 CPU/GPU 两张 16 点风扇表。只发 ECREAD。

        一轮 92 个表地址 + 4 个语义位、约 3 秒，所以结果缓存 FAN_CURVE_MIN_S 秒，
        并发请求拿旧缓存；这一条不进轮询，页面不点就不读 EC。
        """
        now = time.time()
        cached = self._curve
        if cached is not None and now - self._curve_at < FAN_CURVE_MIN_S:
            out = dict(cached)
            out.update({'cached': True, 'age_s': round(now - self._curve_at, 1)})
            return out
        if not self.alive:
            return {'ok': False, 'cached': False, 'state': self.detail.get('state'),
                    'reason': self.detail.get('reason') or 'EC 通道不可用'}
        if not self._curve_lock.acquire(False):
            if cached is not None:
                out = dict(cached)
                out.update({'cached': True, 'age_s': round(now - self._curve_at, 1)})
                return out
            return {'ok': False, 'cached': False, 'reason': '上一轮还在读（约 3 秒），稍后再试'}
        try:
            started = time.time()
            writes0 = self.dev.writes
            tables = {}
            incomplete = 0
            reads = 0
            for which, base in FAN_TABLE_BASE:
                points, miss, count = self._read_curve_table(which, base)
                tables[which] = points
                incomplete += miss
                reads += count
            ctl = self._read_named(FAN_KEY)
            reads += 1
            flag = self.fan_flag_name(ctl)
            # 这三个位的地址同样是厂商代码里的字面量，反射表里没有。
            # bracket（0x07C6 bit2）是「写表括号」：厂商写表前**清零**、写完**置一**，
            # 所以读到 1 才是「没人在写」，读到 0 说明这一刻正有写表在进行。
            context = {'fan_ctl_byte': ctl, 'fan_mode_flag': flag,
                       'hw_mode': hw_mode_of_fan_flag(flag),
                       'ap_exist': self._bit(0x0741, 0),
                       'split_tables': self._bit(0x07C5, 7),
                       'bracket': self._bit(0x07C6, 2)}
            reads += 3
            # dev.reads/dev.writes 是整个通道共用的计数器：轮询线程同一时刻也在读，
            # 所以「本轮读了多少次」必须自己数，不能拿计数器做差（实测会虚高 40 多次）。
            # 写入这一侧本函数一个都不发；但自动跟随可能在同一窗口里改风扇字节，
            # 那种情况下这份转储就不是同一时刻的快照了——照实说出来，不装作原子。
            writes_during = self.dev.writes - writes0
            notes = []
            if incomplete:
                notes.append('%d 个点没读全（EC 忙或 IOCTL 失败），下面是拿到的部分'
                             % incomplete)
            if writes_during:
                notes.append('本轮期间通道另有 %d 次写入（自动跟随在改风扇字节），'
                             '这份表不是同一时刻的快照' % writes_during)
            ok = incomplete == 0
            out = {'ok': ok, 'cached': False, 'reason': '；'.join(notes),
                   'ts': round(time.time(), 1),
                   'reads': reads, 'writes': 0, 'writes_during': writes_during,
                   'elapsed_s': round(time.time() - started, 1),
                   'points': FAN_TABLE_POINTS, 'incomplete': incomplete,
                   'gap_s': FAN_CURVE_GAP_S, 'tables': tables,
                   'context': context, 'note': FAN_CURVE_NOTE}
            self._curve = out
            self._curve_at = time.time()
            if ok:
                self.caps[CAP_FAN_CURVE] = 'verified'
            if self.log:
                self.log.info('[EC读] 风扇表转储：%d 次读、%d 次写、%.1f 秒，%s'
                              % (reads, writes_during, out['elapsed_s'],
                                 '完整' if ok and not notes else out['reason']))
            return dict(out)
        finally:
            self._curve_lock.release()

    # ---------- 对外 ----------
    def read(self):
        if not self.alive:
            return {}
        return dict(getattr(self, 'derived', {}))

    def raw_values(self):
        return {g: dict(v) for g, v in self.values.items()}

    def set_mode(self, mode):
        if mode not in MODES:
            return False, '未知档位：%s' % mode
        return False, ('档位寄存器语义未确认（MAFAN_CONTROL_BYTE=Turbo_Mode 与 '
                       'MyFanCCI_Mode_Index=0 互相矛盾），不做猜测性写入')

    # ---------- 写（默认关闭，需要 allow_write）----------
    def fan_modes(self):
        """OEM 枚举里可用的风扇模式名 → 值。"""
        return dict((self.map or {}).get('enums', {}).get('MyFanCTLByteFlag') or {})

    def set_fan_mode(self, flag_name, who='面板按钮'):
        """写风扇模式字节（Normal_Mode / Turbo_Mode / User_Fan_Mode …）。

        这个字节就是实体「造物者模式」按键写的同一个寄存器，取值直接来自
        OEM 自己的枚举 MyFanCTLByteFlag，写完立刻回读校验。
        人工点按钮算一次人工意图：随后一段时间内自动跟随不许再来抢方向盘。
        """
        if not self.allow_write:
            return False, 'EC 写入未开启（config.hardware.ec.allow_write=false）'
        addr = self.addr(FAN_KEY)
        if addr is None:
            return False, '寄存器表里没有 %s' % FAN_KEY
        table = self.fan_modes()
        if flag_name not in table:
            return False, '未知风扇模式：%s（可用：%s）' % (
                flag_name, '/'.join(sorted(table)) or '无')
        value = int(table[flag_name])
        self._expect[FAN_KEY] = value
        before = self._read_named(FAN_KEY)
        result = self.dev.write(int(addr), value)
        if result is None:
            return False, '写入失败：%s' % self.dev.error
        after = self._read_named(FAN_KEY)
        ok = (after == value)
        if ok and who:
            self.take_fan_control(who)
        msg = '风扇模式 %s(0x%02X) → %s(0x%02X)，回读 %s' % (
            self.fan_flag_name(before) or '?', before or 0, flag_name, value,
            ('0x%02X 一致' % after) if ok else ('%s 不一致' % after))
        if self.log:
            (self.log.info if ok else self.log.warn)('[EC写] ' + msg)
        self._last_fast = 0.0
        return ok, msg

    def write_register(self, name, value):
        """按名字写任意寄存器——仅供自检工具使用，面板不暴露这个入口。"""
        if not self.allow_write:
            return False, 'EC 写入未开启（config.hardware.ec.allow_write=false）'
        addr = self.addr(name)
        if addr is None:
            return False, '寄存器表里没有 %s' % name
        self._expect[name] = int(value) & 0xFF
        result = self.dev.write(int(addr), int(value))
        if result is None:
            return False, '写入失败：%s' % self.dev.error
        back = self._read_named(name)
        return back == (int(value) & 0xFF), '写入 %s=%s，回读 %s' % (name, value, back)

    def status(self):
        detail = dict(self.detail)
        detail.update({'reads': self.dev.reads, 'writes': self.dev.writes,
                       'handle_open': self.dev.handle is not None,
                       'validated': self._validated, 'fail_streak': self.dev.fail_streak,
                       'error': self.dev.error, 'allow_write': self.allow_write,
                       'registers': len(self.registers),
                       'fan_modes': sorted(self.fan_modes()),
                       'fan_lock_left': self.fan_lock_left(),
                       'fan_lock_by': self.hold_by if self.fan_lock_left() else None,
                       'fan_user_owned': self.fan_user_owned(),
                       'fan_hold_last': self.hold_last,
                       'changes': self.recent_changes()})
        return {'name': self.name, 'label': self.label, 'alive': self.alive,
                'caps': dict(self.caps), 'detail': detail}

    def close(self):
        self.dev.close()
        self.alive = False
