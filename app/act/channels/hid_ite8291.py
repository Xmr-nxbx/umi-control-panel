# -*- coding: utf-8 -*-
"""灯效通道：ITE 8291 是 **USB HID 设备**，不在 EC 上（README 6.11 第四节）。

6.7/6.10 那两轮观察里，机主点背光和灯条时 EC 全表一个字节都没动，
`LIGHTBAR_CONTROL_BYTE`、`RGBKB_LEVEL_R/G/B`、`SINGLEKBL_ENABLE` 在灯亮着时全是 0——
当时只能记一条阴性结论。原因后来查明了：真实通路是 HID `SET_REPORT` Feature。

这个文件目前只做两件**不改变任何硬件状态**的事：

  1. **发现**：枚举 HID 接口，找出键盘四区背光（`048D:CE00`，usage page `0xFF12`）
     与 USB 灯条（`048D:6005`，usage page `0xFF03`）。开句柄时
     `dwDesiredAccess=0`——只问属性，不读不写；再借 `HidP_GetCaps` 读出
     `FeatureReportByteLength`，用它**实测校验**下面那个 9 字节的假设。
  2. **编码**：把语义值编成 Feature Report 的字节。纯函数，不发任何东西，
     由 `tests/test_hid_light.py` 拿厂商自己写在 BIOS 里的样例值逐字节钉住。

**写入没有实现**，和 `fan.curve.write` 一样只把能力标成 `blocked`：
按 6.3 第 2 条，新的写通道要先满足「语义有佐证 / 可逆 / 有保险」才允许下发，
可逆那半现在有了着落（固件侧 `LEDKB /GetStatus`、`USBLB /GetMode` 证明这两个设备
都能读回当前设置，见 6.11 第九节②-补），但**读回本身也要先发一次报告才验得出来**，
所以整套得等机主在场一次做完。灯效没有温度风险，缺的只是"能不能读回来"。

灯效枚举（呼吸/波浪那些 effect 编号）**没有查到**，所以这里不提供 effect 名字表，
也不猜。已经拿到的语义只有三件：亮度 5 档 `0/8/22/36/50`、速度 5 档 `10/7/5/3/1`、
RGBKB 每通道 0-50 的**单一静态颜色**（BIOS 侧 `/Set 000000~505050`，不是灯效引擎）。
"""
import ctypes

from app.act.channels.base import CAP_RGB, CAP_RGB_WRITE

# --- 协议常量（来源见每条注释，全部是实测或厂商原文，没有推测值）---
REPORT_LEN = 9          # [报告ID, opcode, Control, Effect, Speed, Light, ColorIndex, Direction, Save]
REPORT_ID = 0

OP_EFFECT = 0x08        # 灯效/开关（厂商 LEDKB 样例首字节就是它）
OP_BRIGHTNESS = 0x09    # 亮度
OP_PALETTE = 0x14       # 调色板：Index,R,G,B
OP_FIRMWARE = 0x80      # 读固件版本

LB_OP_MODE = 0x1A       # 灯条三条命令之一（USBLB /SetMode 的 1AH）
LB_OP_PALETTE = 0x14    # 14H：第 4-6 字节是 R,G,B
LB_OP_EFFECT = 0x08     # 08H

# 亮度 5 档 / 速度 5 档的字节值。来源：6.11 第四节实测 + 厂商样例交叉印证——
# LEDKB 样例第 5 字节 0x32=50 是亮度顶档，USBLB 样例第 5 字节 0x24=36 是第四档。
BRIGHTNESS_LEVELS = (0, 8, 22, 36, 50)
SPEED_LEVELS = (10, 7, 5, 3, 1)
LEVELS = 5

RGBKB_CHANNEL_MAX = 50  # `/Set <6-digits>: 000000 ~ 505050`，每通道 0-50

# 两个目标设备。VID 048D = Integrated Technology Express（ITE）。
# **必须按 (VID, PID, usage page) 三元组认，不能只认 VID/PID**——本机实测（2026-09-30，
# 只读枚举）每个 ITE 设备都暴露**两个**集合：
#   048D:CE00 MI_00  page 0xFF89  Feature 17 字节      ← 语义未知，不碰
#   048D:CE00 MI_01  page 0xFF12  Feature  9 字节      ← 键盘四区背光，就是它
#   048D:6005 MI_00  page 0xFF89  Feature 17 字节      ← 语义未知，不碰
#   048D:6005 MI_01  page 0xFF03  Feature  9 字节      ← USB 灯条，就是它
# 产品名两边都是 `ITE Device(8291)`。只按 VID/PID 挑会先撞上 MI_00 那个 17 字节的集合，
# 然后把 9 字节的报告发进去——所以这一条不是洁癖，是防错。
# 顺带：0xFF12/0xFF03 两个集合的 FeatureReportByteLength **实测就是 9**，
# 上面那个 9 字节布局不再只是从文档推出来的假设。
KB_VID, KB_PID = 0x048D, 0xCE00     # 键盘四区背光
LB_VID, LB_PID = 0x048D, 0x6005     # USB 灯条
KB_USAGE_PAGE = 0xFF12
LB_USAGE_PAGE = 0xFF03
OTHER_USAGE_PAGE = 0xFF89           # 两个设备各自另一个集合，Feature 17 字节，语义未知

TARGETS = (
    {'id': 'kb', 'label': '键盘四区背光',
     'vid': KB_VID, 'pid': KB_PID, 'usage_page': KB_USAGE_PAGE},
    {'id': 'lightbar', 'label': 'USB 灯条',
     'vid': LB_VID, 'pid': LB_PID, 'usage_page': LB_USAGE_PAGE},
)
TARGET_BY_TRIPLE = dict(((t['vid'], t['pid'], t['usage_page']), t) for t in TARGETS)

# 厂商自己写在 BIOS 命令面里的样例字节（README 6.11 第九节）。
# 这五组是编码器唯一的判据：能逐字节复现它们，编码就是对的。
VENDOR_KB_SAMPLES = (
    (0x08, 0x03, 0x0A, 0x05, 0x32, 0x04, 0x00, 0xAF),
    (0x08, 0x03, 0x0A, 0x05, 0x32, 0x04, 0x00, 0x00),
)
VENDOR_LB_SAMPLES = (
    (0x1A, 0x05, 0x01, 0x14, 0x00, 0x00, 0x00, 0x00),
    (0x14, 0x00, 0x01, 0xFF, 0xFF, 0xFF, 0x00, 0x00),
    (0x08, 0x02, 0x03, 0x05, 0x24, 0x00, 0x00, 0x00),
)


# ---------------- 编码（纯函数，不发任何东西）----------------

def _byte(name, value):
    v = int(value)
    if not 0 <= v <= 255:
        raise ValueError('%s 超出 0-255：%r' % (name, value))
    return v


def build_report(opcode, control=0, effect=0, speed=0, light=0,
                 color_index=0, direction=0, save=0):
    """按 9 字节布局拼一份 Feature Report（首字节是报告 ID，恒 0）。

    八个数据字节的位次直接对得上厂商两条命令的样例：
    LEDKB `/SetData 0x08 0x03 0x0A 0x05 0x32 0x04 0x00 0xAF` 与
    USBLB `/SetMode 0x08 0x02 0x03 0x05 0x24 0x00 0x00 0x00`——
    首字节 opcode、第 5 字节是亮度档的字节值，两处都吻合。
    """
    body = (_byte('opcode', opcode), _byte('Control', control), _byte('Effect', effect),
            _byte('Speed', speed), _byte('Light', light), _byte('ColorIndex', color_index),
            _byte('Direction', direction), _byte('Save', save))
    return bytes((REPORT_ID,) + body)


def level_byte(table, level, what):
    """档位号（0-4）→ 设备认的字节值。越界直接报错，不夹也不绕。"""
    idx = int(level)
    if not 0 <= idx < len(table):
        raise ValueError('%s档位只能是 0-%d，收到 %r' % (what, len(table) - 1, level))
    return table[idx]


def brightness_byte(level):
    return level_byte(BRIGHTNESS_LEVELS, level, '亮度')


def speed_byte(level):
    return level_byte(SPEED_LEVELS, level, '速度')


def palette_report(index, r, g, b):
    """调色板：把颜色写进第 index 格。

    字段复用是按厂商灯条样例 `0x14 0x00 0x01 0xff 0xff 0xff 0x00 0x00` 对出来的——
    Control=0、Effect=索引、Speed/Light/ColorIndex 依次是 R/G/B。
    """
    return build_report(OP_PALETTE, 0, _byte('index', index),
                        _byte('R', r), _byte('G', g), _byte('B', b))


def rgbkb_levels(r, g, b):
    """RGBKB 的单一静态颜色，每通道 0-50。返回三元组，越界报错。

    注意这不是灯效：BIOS 侧对"RGB 键盘"的全部想象就是这一个颜色
    （`Current RGB Configuration: R:%d, G:%d, B:%d`）。
    """
    out = []
    for name, v in (('Red', r), ('Green', g), ('Blue', b)):
        iv = int(v)
        if not 0 <= iv <= RGBKB_CHANNEL_MAX:
            raise ValueError('%s 只能在 0-%d，收到 %r' % (name, RGBKB_CHANNEL_MAX, v))
        out.append(iv)
    return tuple(out)


def parse_report(data):
    """把一份 9 字节报告拆回字段名 → 值，方便日志和测试对读。"""
    raw = bytes(data)
    if len(raw) != REPORT_LEN:
        raise ValueError('报告长度必须是 %d 字节，收到 %d' % (REPORT_LEN, len(raw)))
    names = ('report_id', 'opcode', 'control', 'effect', 'speed', 'light',
             'color_index', 'direction', 'save')
    return dict(zip(names, raw))


# ---------------- 发现（只读：dwDesiredAccess=0，不发任何报告）----------------

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
ERROR_INSUFFICIENT_BUFFER = 122
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3


class _GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_ulong), ('InterfaceClassGuid', _GUID),
                ('Flags', ctypes.c_ulong), ('Reserved', ctypes.POINTER(ctypes.c_ulong))]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Size', ctypes.c_ulong), ('VendorID', ctypes.c_ushort),
                ('ProductID', ctypes.c_ushort), ('VersionNumber', ctypes.c_ushort)]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [('Usage', ctypes.c_ushort), ('UsagePage', ctypes.c_ushort),
                ('InputReportByteLength', ctypes.c_ushort),
                ('OutputReportByteLength', ctypes.c_ushort),
                ('FeatureReportByteLength', ctypes.c_ushort),
                ('Reserved', ctypes.c_ushort * 10),
                ('NumberLinkCollectionNodes', ctypes.c_ushort),
                ('NumberInputButtonCaps', ctypes.c_ushort),
                ('NumberInputValueCaps', ctypes.c_ushort),
                ('NumberInputDataIndices', ctypes.c_ushort),
                ('NumberOutputButtonCaps', ctypes.c_ushort),
                ('NumberOutputValueCaps', ctypes.c_ushort),
                ('NumberOutputDataIndices', ctypes.c_ushort),
                ('NumberFeatureButtonCaps', ctypes.c_ushort),
                ('NumberFeatureValueCaps', ctypes.c_ushort),
                ('NumberFeatureDataIndices', ctypes.c_ushort)]


def _detail_cb_size():
    """SP_DEVICE_INTERFACE_DETAIL_DATA_W 的 cbSize：64 位 8、32 位 6（结构体按 2 字节对齐）。"""
    return 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


class HidEnumerator:
    """只读的 HID 接口枚举器。

    每次 `describe()` 都是 open(access=0) → 问属性 → 关掉，句柄不过夜、不缓存，
    也不发任何 Feature Report。这样即使键盘正被系统独占也不影响它工作。
    """

    def __init__(self, log=None):
        self.log = log
        self.error = None
        self._hid = None
        self._setup = None
        self._k32 = None

    def _api(self):
        """取三个 DLL 并声明原型。

        原型必须声明：ctypes 默认把返回值当 32 位 int，64 位上 HANDLE 会被截断——
        第一版就是这么静默拿到 0 个接口的（`SetupDiGetClassDevsW` 的返回值被砍半，
        与 INVALID_HANDLE_VALUE 比又不等，于是"看起来成功"却一个都枚举不出来）。
        """
        if self._hid is None:
            hid = ctypes.windll.hid
            setup = ctypes.windll.setupapi
            k32 = ctypes.windll.kernel32
            BOOL = ctypes.c_long
            HANDLE = ctypes.c_void_p
            hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
            hid.HidD_GetHidGuid.restype = None
            hid.HidD_GetAttributes.argtypes = [HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
            hid.HidD_GetAttributes.restype = BOOL
            hid.HidD_GetProductString.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_ulong]
            hid.HidD_GetProductString.restype = BOOL
            hid.HidD_GetPreparsedData.argtypes = [HANDLE, ctypes.POINTER(ctypes.c_void_p)]
            hid.HidD_GetPreparsedData.restype = BOOL
            hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
            hid.HidD_FreePreparsedData.restype = BOOL
            hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
            hid.HidP_GetCaps.restype = ctypes.c_long
            setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), ctypes.c_wchar_p,
                                                   HANDLE, ctypes.c_ulong]
            setup.SetupDiGetClassDevsW.restype = HANDLE
            setup.SetupDiEnumDeviceInterfaces.argtypes = [
                HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.c_ulong,
                ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
            setup.SetupDiEnumDeviceInterfaces.restype = BOOL
            setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
                HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
                ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
            setup.SetupDiGetDeviceInterfaceDetailW.restype = BOOL
            setup.SetupDiDestroyDeviceInfoList.argtypes = [HANDLE]
            setup.SetupDiDestroyDeviceInfoList.restype = BOOL
            k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                        HANDLE]
            k32.CreateFileW.restype = HANDLE
            k32.CloseHandle.argtypes = [HANDLE]
            k32.CloseHandle.restype = BOOL
            self._hid, self._setup, self._k32 = hid, setup, k32
        return self._hid, self._setup, self._k32

    def interface_paths(self):
        """列出 HID 类下所有在册接口路径。失败返回空表并把原因记在 self.error。"""
        hid, setup, k32 = self._api()
        guid = _GUID()
        hid.HidD_GetHidGuid(ctypes.byref(guid))
        hdev = setup.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                          DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
        if not hdev or hdev == INVALID_HANDLE_VALUE:
            self.error = 'SetupDiGetClassDevs 失败（GetLastError=%d）' % k32.GetLastError()
            return []
        out = []
        try:
            index = 0
            while True:
                ifdata = _SP_DEVICE_INTERFACE_DATA()
                ifdata.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
                ok = setup.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid),
                                                       index, ctypes.byref(ifdata))
                if not ok:
                    break                     # ERROR_NO_MORE_ITEMS，正常收尾
                index += 1
                need = ctypes.c_ulong(0)
                setup.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifdata),
                                                       None, 0, ctypes.byref(need), None)
                if need.value < 8:
                    continue
                buf = ctypes.create_string_buffer(need.value)
                ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0] = _detail_cb_size()
                if setup.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifdata), buf,
                                                          need, ctypes.byref(need), None):
                    out.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
        finally:
            setup.SetupDiDestroyDeviceInfoList(hdev)
        return out

    def describe(self, path):
        """问一个接口的 VID/PID/产品名/Feature 报告长度。失败返回 None。"""
        hid, _, k32 = self._api()
        handle = k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        if not handle or handle == INVALID_HANDLE_VALUE:
            return None                       # 被系统独占的键盘口打不开，属正常，不报错
        try:
            attr = _HIDD_ATTRIBUTES()
            attr.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
            if not hid.HidD_GetAttributes(handle, ctypes.byref(attr)):
                return None
            name = ctypes.create_unicode_buffer(128)
            if not hid.HidD_GetProductString(handle, name, ctypes.sizeof(name)):
                name.value = ''               # 问不到产品名不影响判定，别留半截垃圾
            info = {'path': path, 'vid': attr.VendorID, 'pid': attr.ProductID,
                    'version': attr.VersionNumber, 'product': name.value,
                    'usage_page': None, 'usage': None, 'feature_len': None}
            ppd = ctypes.c_void_p()
            if hid.HidD_GetPreparsedData(handle, ctypes.byref(ppd)):
                try:
                    caps = _HIDP_CAPS()
                    if hid.HidP_GetCaps(ppd, ctypes.byref(caps)) == 0x00110000:  # HIDP_STATUS_SUCCESS
                        info['usage_page'] = caps.UsagePage
                        info['usage'] = caps.Usage
                        info['feature_len'] = caps.FeatureReportByteLength
                finally:
                    hid.HidD_FreePreparsedData(ppd)
            return info
        finally:
            k32.CloseHandle(handle)

    def find_targets(self):
        """按 (VID, PID, usage page) 三元组认设备。

        返回 {'kb': info|None, 'lightbar': info|None, 'ite_interfaces': [...],
              'other_collections': [...]}。`other_collections` 是同一批 ITE 设备上
        那些**我们认不出来也不碰**的集合（本机实测是 page 0xFF89、Feature 17 字节），
        单列出来是为了照实记录它们存在，不是留着以后试。
        """
        found = {'kb': None, 'lightbar': None}
        ite = []
        others = []
        for path in self.interface_paths():
            info = self.describe(path)
            if not info or info['vid'] != KB_VID:
                continue
            ite.append(info)
            meta = TARGET_BY_TRIPLE.get((info['vid'], info['pid'], info['usage_page']))
            if meta is None:
                others.append(info)
            elif found[meta['id']] is None:
                info = dict(info)
                info['id'] = meta['id']
                info['label'] = meta['label']
                found[meta['id']] = info
        found['ite_interfaces'] = ite
        found['other_collections'] = others
        return found


class HidLightChannel:
    """灯效通道：目前只报"设备在不在、报告长度对不对"，一个字节都不往设备上写。"""

    name = 'hid_light'
    label = '灯效（ITE 8291 USB HID）'

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.enum = HidEnumerator(log)
        self.caps = {CAP_RGB: 'unknown', CAP_RGB_WRITE: 'blocked'}
        self.detail = {'state': 'unknown', 'reason': '尚未探测',
                       'write_reason': '新写通道，按 6.3 第 2 条要先做一次可逆验证（机主在场）'}
        self.alive = False
        self.devices = {}
        self.ite_interfaces = []
        self.other_collections = []

    def probe(self):
        """只读探测：找两个设备，并用 FeatureReportByteLength 校验 9 字节这个假设。"""
        try:
            found = self.enum.find_targets()
        except Exception as exc:                              # noqa: BLE001
            self.caps[CAP_RGB] = 'unsupported'
            self.detail.update({'state': 'error', 'reason': 'HID 枚举异常：%r' % (exc,)})
            self.alive = False
            return
        self.ite_interfaces = found.get('ite_interfaces') or []
        self.other_collections = found.get('other_collections') or []
        self.devices = {k: found.get(k) for k in ('kb', 'lightbar')}
        present = [v['label'] for v in self.devices.values() if v]
        if not present:
            self.caps[CAP_RGB] = 'unsupported'
            self.detail.update({
                'state': 'missing',
                'reason': '没找到灯效集合（要 %04X:%04X page 0x%04X 或 %04X:%04X page 0x%04X）；'
                          '本机 ITE 接口 %d 个，其中认不出的集合 %d 个'
                          % (KB_VID, KB_PID, KB_USAGE_PAGE, LB_VID, LB_PID, LB_USAGE_PAGE,
                             len(self.ite_interfaces), len(self.other_collections)),
                'found': [], 'feature_len': {}, 'usage_page': {},
                'ite_interfaces': len(self.ite_interfaces),
                'other_collections': len(self.other_collections)})
            self.alive = False
            return

        # 报告长度是设备自己报的，不是我们假设的——对不上就照实说，不硬套协议。
        lens = {k: v.get('feature_len') for k, v in self.devices.items() if v}
        pages = {k: v.get('usage_page') for k, v in self.devices.items() if v}
        mismatch = ['%s=%s' % (k, n) for k, n in lens.items() if n not in (None, REPORT_LEN)]
        self.alive = True
        self.caps[CAP_RGB] = 'verified'
        self.detail.update({
            'state': 'mismatch' if mismatch else 'ok',
            'reason': ('Feature 报告长度不是 %d 字节：%s' % (REPORT_LEN, ','.join(mismatch)))
                      if mismatch else
                      ('%s在位，Feature 报告 %d 字节（设备自报，与协议假设一致）'
                       % ('/'.join(present), REPORT_LEN)),
            'found': present,
            'feature_len': lens,
            'usage_page': {k: (None if v is None else '0x%04X' % v) for k, v in pages.items()},
            'ite_interfaces': len(self.ite_interfaces),
            'other_collections': len(self.other_collections),
        })
        if self.log:
            self.log.info('[灯效] %s' % self.detail['reason'])

    def tick(self):
        pass

    def read(self):
        """灯效状态**读不到**：这两个设备的当前设置要发一次报告才问得出来，
        而那已经是写通道的一部分。所以这里什么字段都不往快照里塞，
        面板上的灯条状态继续走 GCUBridge（`HidLightbar/Status`）。"""
        return {}

    def lighting_info(self):
        """给面板/自检工具用的只读设备清单。"""
        return {'devices': dict(self.devices), 'detail': dict(self.detail),
                'caps': dict(self.caps)}

    def status(self):
        detail = dict(self.detail)
        detail.update({'kb': bool(self.devices.get('kb')),
                       'lightbar': bool(self.devices.get('lightbar')),
                       'report_len': REPORT_LEN,
                       'brightness_levels': list(BRIGHTNESS_LEVELS),
                       'speed_levels': list(SPEED_LEVELS)})
        return {'name': self.name, 'label': self.label, 'alive': self.alive,
                'caps': dict(self.caps), 'detail': detail}

    def close(self):
        self.alive = False
