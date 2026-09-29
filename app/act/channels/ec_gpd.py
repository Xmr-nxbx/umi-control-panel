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
data/ec_map.local.json。本文件只按名字取地址，一个数字都不写死。

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

from app.act.channels.base import (CAP_BATTERY_LIMIT, CAP_FAN_CURVE, CAP_FAN_MODE,
                                   CAP_FAN_RPM, CAP_MODE_READ, CAP_MODE_WRITE,
                                   CAP_PL_READ, CAP_PL_WRITE, CAP_TEMP_EC, MODES,
                                   hw_mode_of_fan_flag)
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
              'ADDR_TRIGGER_BYTE', 'ADDR_STAUTS_BYTE')),
    ('battery', ('ecBt1RSOC', 'ecBt1Temperature')),
)
SLOW_GROUPS = (
    ('identity', ('ADDR_PROJECT_ID_BYTE', 'ADDR_ModuleID', 'ADDR_SUPPORT_BYTE1',
                  'ADDR_SUPPORT_BYTE2', 'ADDR_BIOS_INFO_3_BYTE', 'ADDR_EC_BIOS_INFO5',
                  'ADDR_OEMSERVICE_PROJECT_ID_BYTE')),
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
# 还没确认、但已经有实测线索的两个字节（README 6.7）：
#   ADDR_STAUTS_BYTE   Win 键锁定？2026-09-30 机主点了一次 Win 锁，它 1→0 且没再变回去，
#                      同一时刻 Setting/Status 里 WinKey=WINKEY_STATUS_LOCK。样本只有一次，
#                      等 MQTT 那边的 WINKEY_LOCK/UNLOCK 命令对上号才算确认。
#   ADDR_AP_OEM_BYTE4  电池那三档（平衡/健康/长效）？高半字节 0x0?→0x1?→0x2?→0x0?
#                      正好跟着机主连点三次电源模式走，低半字节 9 不动。
#                      open-revo 说这三档是充电阈值（长效 100% / 均衡 80% / 养护 60%），
#                      但 CHARGE_LIMIT_UP/DOWN 全程是 0，所以阈值不在 EC 这张表里执行。
# 反过来，已经排除的：灯效不在 EC 上。LIGHTBAR_CONTROL_BYTE、RGBKB_LEVEL_R/G/B、
# SINGLEKBL_ENABLE 在灯明明亮着的时候全是 0，机主点背光/灯条时全表也一个都没动；
# 走的是 GCUBridge 的 Keyboard/Ctrl（{"function":"SetPower","light":"3","speed":"2"}，
# 控制器 solution=ITE、type=FourZone）。触摸板是实体开关，不用软件管。


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
                      CAP_FAN_MODE, CAP_FAN_CURVE, CAP_TEMP_EC, CAP_BATTERY_LIMIT,
                      CAP_PL_WRITE)}
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
                CAP_BATTERY_LIMIT: 'verified', CAP_FAN_CURVE: 'unknown',
                # mode.read/write 说的是 OEM 那套 office/balance/turbo：EC 侧没有这个
                # 概念（机主确认 Creator Center 界面上也没有），只有 GCUBridge 认。
                # 本机真正的硬件模式走风扇字节，见 hw_mode_of_fan_flag / derived['hw_mode']。
                CAP_MODE_READ: 'unsupported', CAP_MODE_WRITE: 'blocked',
                CAP_PL_WRITE: 'blocked',
            })
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
