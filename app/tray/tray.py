"""托盘：ctypes Shell_NotifyIcon，图标颜色随档位变化。

图标不依赖任何外部图片：用 32bpp DIB 手画一个圆角方块（不同档位不同主色），
既避免分发厂商版权素材，也保证缺文件时托盘一定能出来。
"""
import ctypes
import ctypes.wintypes as wt
import struct
import threading

from app.policy.scheduler import TIER_LABELS

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
s32 = ctypes.windll.shell32          # Shell_NotifyIconW 在 shell32，不在 user32

# LRESULT/LPARAM 在 x64 上是 64 位。不声明 argtypes 的话 ctypes 按 c_int 传参，
# DefWindowProcW 遇到大 lparam 会抛 OverflowError——异常发生在窗口回调里，
# 只会打到 stderr，表现就是「右键菜单没反应」这种查不出原因的毛病。
LRESULT = ctypes.c_ssize_t
u32.DefWindowProcW.restype = LRESULT
u32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
WM_USER = 0x0400
WM_LBUTTONDBLCLK = 0x0203
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
MF_SEPARATOR = 0x0010
MF_CHECKED = 0x0008
MF_GRAYED = 0x0001
LR_LOADFROMFILE = 0x0010
IMAGE_ICON = 1

WM_TRAY = WM_USER + 1
MENU_OPEN = 0x1001
MENU_TURBO = 0x1010
MENU_BALANCE = 0x1011
MENU_OFFICE = 0x1012
MENU_AUTO = 0x1013
MENU_QUIT = 0x1999
MENU_FAN_NORMAL = 0x1020
MENU_FAN_TURBO = 0x1021
MENU_FAN_BOOST = 0x1022

# 风扇模式取值必须是 OEM 枚举 MyFanCTLByteFlag 里的名字，别的不下发
FAN_ITEMS = ((MENU_FAN_NORMAL, 'Normal_Mode', '风扇：自动'),
             (MENU_FAN_TURBO, 'Turbo_Mode', '风扇：强冷'),
             (MENU_FAN_BOOST, 'FanBoost_Mode', '风扇：加速'))

TIER_COLORS = {'perf': (255, 93, 108), 'mid': (255, 182, 72),
               'bal': (53, 224, 216), 'eco': (74, 222, 128)}


class _ICONINFO(ctypes.Structure):
    _fields_ = [('fIcon', wt.BOOL), ('hbmMask', wt.HBITMAP), ('hbmColor', wt.HBITMAP),
                ('xHotspot', wt.WORD), ('yHotspot', wt.WORD)]


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('hWnd', wt.HWND), ('uID', ctypes.c_uint),
                ('uFlags', ctypes.c_uint), ('uCallbackMessage', ctypes.c_uint),
                ('hIcon', wt.HICON), ('szTip', ctypes.c_wchar * 128),
                ('dwState', ctypes.c_uint), ('dwStateMask', ctypes.c_uint),
                ('guidItem', ctypes.c_byte * 16), ('hIconReserved', ctypes.c_void_p)]


class _MSG(ctypes.Structure):
    _fields_ = [('hwnd', wt.HWND), ('message', ctypes.c_uint),
                ('wParam', wt.WPARAM), ('lParam', wt.LPARAM),
                ('time', ctypes.c_uint), ('pt', wt.POINT)]


def _make_ico_bytes(size, rgb, dim):
    """生成 size×size 的 32bpp 图标（BGRA 自下而上）+ AND 掩码。"""
    head = struct.pack('<HHH', 0, 1, 1)
    bmp_off = 6 + 16
    px = bytearray()
    for y in range(size):
        for x in range(size):
            edge = min(x, y, size - 1 - x, size - 1 - y)
            r, g, b = rgb if edge >= 3 else dim
            px += struct.pack('<BBBB', b, g, r, 255)
    mask_rows = (size + 31) // 32
    and_mask = bytes(mask_rows * 4 * size)
    ih = struct.pack('<IllHHIIllll', 40, size, size * 2, 1, 32, 0,
                     len(px), 0, 0, 0, 0)
    ico_dir = struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32,
                          len(ih) + len(px) + len(and_mask), bmp_off)
    return head + ico_dir + ih + bytes(px) + and_mask


class Tray:
    def __init__(self, cfg, log, on_open, on_intent, on_quit, on_mode, on_fan=None):
        self.cfg = cfg
        self.log = log
        self.on_open = on_open
        self.on_intent = on_intent
        self.on_quit = on_quit
        self.on_mode = on_mode
        self.on_fan = on_fan
        self._fan = None
        self.hwnd = None
        self.icon = None
        self._thread = None
        self._tied = {}
        self.current_tip = ''

    def start(self):
        self._thread = threading.Thread(target=self._run, name='tray', daemon=True)
        self._thread.start()

    def _register_and_create(self):
        WNDCLASS = type('WNDCLASS', (ctypes.Structure,), {'_fields_': [
            ('style', ctypes.c_uint), ('lpfnWndProc', ctypes.c_void_p),
            ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
            ('hInstance', ctypes.c_void_p), ('hIcon', ctypes.c_void_p),
            ('hCursor', ctypes.c_void_p), ('hbrBackground', ctypes.c_void_p),
            ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR)]})

        @ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint,
                            wt.WPARAM, wt.LPARAM)
        def wnd_proc(hwnd, msg, wparam, lparam):
            return self._wnd_proc(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = wnd_proc
        hinst = k32.GetModuleHandleW(None)
        cls = WNDCLASS(0, ctypes.cast(wnd_proc, ctypes.c_void_p), 0, 0, hinst,
                       None, None, None, None, 'UmiControlPanelTray')
        if not u32.RegisterClassW(ctypes.byref(cls)):
            err = k32.GetLastError()
            if err != 1410:                       # 1410 = 类已注册
                self.log.error('托盘窗口类注册失败 err=%s' % err)
                return False
        self.hwnd = u32.CreateWindowExW(0, 'UmiControlPanelTray', 'Umi Control Panel',
                                        0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            self.log.error('托盘窗口创建失败')
            return False
        self._update_icon('bal', '均衡')
        if getattr(self, '_added', False):
            self.log.info('[托盘] 已挂载，图标颜色随档位变化')
        return getattr(self, '_added', False)

    def _make_icon(self, tier):
        rgb = TIER_COLORS.get(tier, (120, 140, 170))
        dim = tuple(max(0, int(c * 0.45)) for c in rgb)
        blob = _make_ico_bytes(32, rgb, dim)
        tmp = __import__('os').path.join(__import__('tempfile').gettempdir(),
                                         'umi-tray-%s.ico' % tier)
        with open(tmp, 'wb') as f:
            f.write(blob)
        handle = u32.LoadImageW(None, tmp, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if not handle:
            handle = u32.LoadIconW(None, ctypes.c_wchar_p(32512))  # IDI_APPLICATION
        self._tied.setdefault(tier, handle)
        return handle

    def _update_icon(self, tier, label):
        icon = self._make_icon(tier)
        if not icon:
            return
        self.icon = icon
        data = _NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = icon
        tip = 'Umi 控制台 · %s' % label
        data.szTip = tip[:63]
        flag = NIM_ADD if not getattr(self, '_added', False) else NIM_MODIFY
        if not s32.Shell_NotifyIconW(flag, ctypes.byref(data)):
            self.log.error('托盘图标添加失败')
            return
        self._added = True
        self.current_tip = tip

    def update_state(self, tier, label, intent=None, fan=None):
        if intent:
            self._intent = intent
        if fan:
            self._fan = fan
        if not self.hwnd:
            return
        if label != self.current_tip:
            self._update_icon(tier, label)

    def _menu(self):
        hmenu = u32.CreatePopupMenu()
        u32.AppendMenuW(hmenu, MF_STRING, MENU_OPEN, '打开控制面板')
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        for mid, name in ((MENU_AUTO, '自适应'), (MENU_OFFICE, '办公'),
                          (MENU_BALANCE, '均衡'), (MENU_TURBO, '狂暴')):
            checked = MF_CHECKED if self._intent == name else 0
            u32.AppendMenuW(hmenu, MF_STRING | checked, mid, name)
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        for mid, flag, text in FAN_ITEMS:
            state = MF_STRING | (MF_CHECKED if self._fan == flag else 0)
            if self.on_fan is None:
                state |= MF_GRAYED
            u32.AppendMenuW(hmenu, state, mid, text)
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(hmenu, MF_STRING, MENU_QUIT, '退出（还原电源设置）')
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(self.hwnd)
        cmd = u32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                 pt.x, pt.y, 0, self.hwnd, None)
        u32.DestroyMenu(hmenu)
        return cmd

    def _on_command(self, cmd):
        if cmd == MENU_OPEN:
            self.on_open()
        elif cmd == MENU_QUIT:
            self.on_quit()
        elif cmd in (MENU_AUTO, MENU_OFFICE, MENU_BALANCE, MENU_TURBO):
            intent = {MENU_AUTO: 'auto', MENU_OFFICE: 'office',
                      MENU_BALANCE: 'balance', MENU_TURBO: 'turbo'}[cmd]
            self._intent = intent
            self.on_intent(intent)
        elif cmd in dict((mid, flag) for mid, flag, _ in FAN_ITEMS):
            flag = dict((mid, flag) for mid, flag, _ in FAN_ITEMS)[cmd]
            if self.on_fan is not None:
                self.on_fan(flag)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam == WM_LBUTTONUP or lparam == WM_LBUTTONDBLCLK:
                self.on_open()
            elif lparam == WM_RBUTTONUP:
                self._intent = getattr(self, '_intent', None)
                cmd = self._menu()
                if cmd:
                    self._on_command(cmd)
            return 0
        if msg == WM_DESTROY:
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        # 线程里抛出的异常不会冒泡到主线程，只会打到 stderr——进程隐藏跑的时候
        # 就等于「悄无声息地坏掉」。托盘图标曾经就是这样没了：一行无效代码抛
        # AttributeError，日志里什么都没有。所以这里必须自己兜住并写日志。
        try:
            if not self._register_and_create():
                return
            msg = _MSG()
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
            self.remove()
        except Exception as exc:                           # noqa: BLE001
            import traceback
            self.log.error('[托盘] 线程异常退出：%r | %s' % (
                exc, traceback.format_exc().replace('\n', ' / ')))

    def remove(self):
        if getattr(self, '_added', False) and self.hwnd:
            data = _NOTIFYICONDATA()
            data.cbSize = ctypes.sizeof(data)
            data.hWnd = self.hwnd
            data.uID = 1
            s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            self._added = False
