# -*- coding: utf-8 -*-
"""屏幕提示（OSD）：Creator Center 卸载后，按实体「造物者模式」键屏幕上一个字都不弹，
用户只能怀疑按键坏了。这里自己画一张顶上。

实现上的几个硬约束：
  * 必须 click-through（WS_EX_TRANSPARENT）：挡住鼠标点不到东西，比没有提示更烦人；
  * 必须自己带一个消息循环线程：ShowWindow/定时器都要在创建窗口的线程里被处理，
    从别的线程直接调会一直阻塞到超时；
  * 任何一步 Win32 调用失败就整体放弃，返回 False 让调用方退化成托盘气泡——
    提示是锦上添花，绝不能因为它把守护进程搞崩。
透明度用 color key（洋红当透明色）而不是逐像素 alpha：代码短一个数量级，
而这张窗口只存在两秒，没人会盯着它的圆角看有没有抗锯齿。
"""
import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# 这些声明不是洁癖：x64 上 HWND/HGDIOBJ/HDC 都是 64 位指针，ctypes 不声明
# restype 就按 c_int 截断，拿到的句柄是半截的；DefWindowProcW 不声明 argtypes
# 则会在大 lparam 上抛 OverflowError——异常发生在窗口回调里，只打到 stderr，
# 表现出来就是「提示窗画一半 / 不消失」这种查不出原因的毛病。
LRESULT = ctypes.c_ssize_t
HANDLE = ctypes.c_void_p
HDC = ctypes.c_void_p

kernel32.GetModuleHandleW.restype = HANDLE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, HANDLE]
user32.KillTimer.argtypes = [wintypes.HWND, wintypes.UINT]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                              ctypes.c_ubyte, wintypes.DWORD]
user32.LoadCursorW.restype = HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.BeginPaint.restype = HDC
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.FillRect.argtypes = [HDC, ctypes.c_void_p, HANDLE]
user32.DrawTextW.argtypes = [HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.c_void_p, wintypes.UINT]

gdi32.CreateSolidBrush.restype = HANDLE
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateRoundRectRgn.restype = HANDLE
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.FillRgn.argtypes = [HDC, HANDLE, HANDLE]
gdi32.CreateFontW.restype = HANDLE
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPCWSTR]
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = [HDC, HANDLE]
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.SetBkMode.argtypes = [HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [HDC, wintypes.COLORREF]

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY = 0x00000001
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
WM_PAINT = 0x000F
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_APP_SHOW = 0x8001
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
TRANSPARENT = 1
COLOR_KEY = 0x00FF00FF          # 洋红：BGR 顺序，画上去就等于「这块是透明的」
BG_COLOR = 0x00201C18           # 深色底，和面板一个调子
FG_COLOR = 0x00F5E6C8           # 暖白
ACCENT = 0x0040C8D8             # 青色，标题用
TIMER_ID = 1
FONT = 'Microsoft YaHei UI'

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', ctypes.c_uint), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class Osd:
    """一张两秒钟就消失的置顶提示。线程安全：show() 可以从任何线程调。"""

    CLASS_NAME = 'UmiPanelOsd'

    def __init__(self, log=None):
        self.log = log
        self._th = None
        self._hwnd = None
        self._ok = False
        self._ready = threading.Event()
        self._payload = ('', '')
        self._proc = None            # 必须留着引用，被 GC 掉窗口过程就成了野指针

    # ---------- 生命周期 ----------
    def start(self):
        if self._th is not None:
            return self._ok
        self._th = threading.Thread(target=self._run, name='osd', daemon=True)
        self._th.start()
        self._ready.wait(3.0)
        return self._ok

    @property
    def available(self):
        return self._ok

    def show(self, title, sub='', ms=2200):
        """弹一条提示。返回 False 表示这台机器上画不出来，调用方该走别的路。"""
        if not self.start():
            return False
        self._payload = (title or '', sub or '')
        # PostMessage 不阻塞：真正画图的活由 OSD 自己的线程做
        user32.PostMessageW(self._hwnd, WM_APP_SHOW, 0, ms)
        return True

    def _run(self):
        try:
            self._create()
        except Exception as exc:                        # noqa: BLE001
            self._ok = False
            if self.log:
                self.log.info('[OSD] 屏幕提示不可用（改用托盘气泡）：%r' % (exc,))
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wnd_proc)
        wc = _WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, 32512)     # IDC_ARROW
        wc.lpszClassName = self.CLASS_NAME
        if not user32.RegisterClassW(ctypes.addressof(wc)):
            err = ctypes.GetLastError()
            if err not in (0, 1410):                     # 1410 = 类已注册，能接着用
                raise OSError('RegisterClassW 失败：%s' % err)
        style = WS_POPUP | WS_VISIBLE
        ex = (WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
              | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)
        w, h = self._size()
        x = (user32.GetSystemMetrics(0) - w) // 2
        y = int(user32.GetSystemMetrics(1) * 0.14)
        self._hwnd = user32.CreateWindowExW(
            ex, self.CLASS_NAME, 'Umi OSD', style, x, y, w, h,
            None, None, hinst, None)
        if not self._hwnd:
            raise OSError('CreateWindowExW 失败：%s' % ctypes.GetLastError())
        user32.SetLayeredWindowAttributes(self._hwnd, COLOR_KEY, 0, LWA_COLORKEY)
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        user32.ShowWindow(self._hwnd, SW_HIDE)
        self._ok = True

    def _size(self):
        w = min(560, max(340, user32.GetSystemMetrics(0) - 120))
        return w, 96

    # ---------- 窗口过程 ----------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_SHOW:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            user32.ShowWindow(hwnd, 5)                    # SW_SHOW
            user32.InvalidateRect(hwnd, None, True)
            user32.UpdateWindow(hwnd)
            user32.SetTimer(hwnd, TIMER_ID, max(600, int(wparam or 2200)), None)
            return 0
        if msg == WM_TIMER:
            user32.KillTimer(hwnd, TIMER_ID)
            user32.ShowWindow(hwnd, SW_HIDE)
            return 0
        if msg == WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == WM_DESTROY:
            self._ok = False
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _paint(self, hwnd):
        title, sub = self._payload
        ps = ctypes.create_string_buffer(88)             # PAINTSTRUCT
        hdc = user32.BeginPaint(hwnd, ctypes.addressof(ps))
        if not hdc:
            return
        try:
            w, h = self._size()
            rect = _RECT(0, 0, w, h)
            key = gdi32.CreateSolidBrush(COLOR_KEY)
            user32.FillRect(hdc, ctypes.addressof(rect), key)
            gdi32.DeleteObject(key)
            # 圆角靠区域实现：区域外仍然是 color key，所以看起来就是透明圆角
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, 22, 22)
            bg = gdi32.CreateSolidBrush(BG_COLOR)
            gdi32.FillRgn(hdc, rgn, bg)
            gdi32.DeleteObject(bg)
            gdi32.DeleteObject(rgn)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            self._text(hdc, title, -30, 6, 46, ACCENT, bold=True)
            if sub:
                self._text(hdc, sub, -16, 52, 90, FG_COLOR)
        finally:
            user32.EndPaint(hwnd, ctypes.addressof(ps))

    def _text(self, hdc, text, height, top, bottom, color, bold=False):
        """top/bottom 都是窗口内的绝对 y 坐标，bottom 必须大于 top——
        DrawText 拿到空矩形会一声不响地什么都不画。"""
        font = gdi32.CreateFontW(
            height, 0, 0, 0, 700 if bold else 400, 0, 0, 0,
            134, 0, 0, 5, 0, FONT)                        # 134 = GB2312_CHARSET
        if not font:
            return
        old = gdi32.SelectObject(hdc, font)
        gdi32.SetTextColor(hdc, color)
        w, _ = self._size()
        rect = _RECT(16, top, w - 16, bottom)
        buf = ctypes.create_unicode_buffer(text)
        user32.DrawTextW(hdc, buf, -1, ctypes.addressof(rect),
                         DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)
