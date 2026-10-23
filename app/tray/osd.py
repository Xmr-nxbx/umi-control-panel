# -*- coding: utf-8 -*-
"""Native, non-activating OSD; all window and GDI work stays on its thread."""
import ctypes
import threading
import time
from ctypes import wintypes

from app.desktop.appearance import PALETTE, TIER_COLORS, load_icon, normalize_tier

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
HANDLE = ctypes.c_void_p
LRESULT = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', ctypes.c_uint), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', HANDLE), ('hIcon', HANDLE), ('hCursor', HANDLE),
                ('hbrBackground', HANDLE), ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [('hdc', HANDLE), ('fErase', wintypes.BOOL),
                ('rcPaint', wintypes.RECT), ('fRestore', wintypes.BOOL),
                ('fIncUpdate', wintypes.BOOL), ('rgbReserved', ctypes.c_byte * 32)]


def _bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


I, U, B, P = ctypes.c_int, ctypes.c_uint, wintypes.BOOL, ctypes.c_void_p
_bind(kernel32, 'GetModuleHandleW', HANDLE, wintypes.LPCWSTR)
_bind(kernel32, 'GetCurrentThreadId', wintypes.DWORD)
_bind(user32, 'DefWindowProcW', LRESULT, HANDLE, U, wintypes.WPARAM, wintypes.LPARAM)
_bind(user32, 'RegisterClassW', wintypes.ATOM, P)
_bind(user32, 'UnregisterClassW', B, wintypes.LPCWSTR, HANDLE)
_bind(user32, 'CreateWindowExW', HANDLE, wintypes.DWORD, wintypes.LPCWSTR,
      wintypes.LPCWSTR, wintypes.DWORD, I, I, I, I, HANDLE, HANDLE, HANDLE, P)
_bind(user32, 'DestroyWindow', B, HANDLE)
_bind(user32, 'PostMessageW', B, HANDLE, U, wintypes.WPARAM, wintypes.LPARAM)
_bind(user32, 'PostThreadMessageW', B, wintypes.DWORD, U, wintypes.WPARAM, wintypes.LPARAM)
_bind(user32, 'GetMessageW', I, P, HANDLE, U, U)
_bind(user32, 'TranslateMessage', B, P)
_bind(user32, 'DispatchMessageW', LRESULT, P)
_bind(user32, 'PostQuitMessage', None, I)
_bind(user32, 'SetWindowPos', B, HANDLE, HANDLE, I, I, I, I, U)
_bind(user32, 'ShowWindow', B, HANDLE, I)
_bind(user32, 'SetTimer', UINT_PTR, HANDLE, UINT_PTR, U, P)
_bind(user32, 'KillTimer', B, HANDLE, UINT_PTR)
_bind(user32, 'SetLayeredWindowAttributes', B, HANDLE, wintypes.COLORREF, ctypes.c_ubyte, U)
_bind(user32, 'InvalidateRect', B, HANDLE, P, B)
_bind(user32, 'BeginPaint', HANDLE, HANDLE, P)
_bind(user32, 'EndPaint', B, HANDLE, P)
_bind(user32, 'FillRect', I, HANDLE, P, HANDLE)
_bind(user32, 'DrawTextW', I, HANDLE, wintypes.LPCWSTR, I, P, U)
_bind(user32, 'DrawIconEx', B, HANDLE, I, I, HANDLE, I, I, U, HANDLE, U)
_bind(user32, 'DestroyIcon', B, HANDLE)
_bind(user32, 'GetDC', HANDLE, HANDLE)
_bind(user32, 'ReleaseDC', I, HANDLE, HANDLE)
_bind(user32, 'GetForegroundWindow', HANDLE)
_bind(user32, 'MonitorFromWindow', HANDLE, HANDLE, wintypes.DWORD)
_bind(user32, 'GetMonitorInfoW', B, HANDLE, P)
_bind(user32, 'SystemParametersInfoW', B, U, U, P, U)
_bind(gdi32, 'CreateSolidBrush', HANDLE, wintypes.COLORREF)
_bind(gdi32, 'CreateRoundRectRgn', HANDLE, I, I, I, I, I, I)
_bind(gdi32, 'FillRgn', B, HANDLE, HANDLE, HANDLE)
_bind(gdi32, 'CreateFontW', HANDLE, I, I, I, I, I, U, U, U, U, U, U, U, U, wintypes.LPCWSTR)
_bind(gdi32, 'SelectObject', HANDLE, HANDLE, HANDLE)
_bind(gdi32, 'DeleteObject', B, HANDLE)
_bind(gdi32, 'SetBkMode', I, HANDLE, I)
_bind(gdi32, 'SetTextColor', wintypes.COLORREF, HANDLE, wintypes.COLORREF)
_bind(gdi32, 'CreateCompatibleDC', HANDLE, HANDLE)
_bind(gdi32, 'DeleteDC', B, HANDLE)
_bind(gdi32, 'CreateCompatibleBitmap', HANDLE, HANDLE, I, I)
_bind(gdi32, 'BitBlt', B, HANDLE, I, I, I, I, HANDLE, I, I, wintypes.DWORD)

_set_thread_dpi = (_bind(user32, 'SetThreadDpiAwarenessContext', HANDLE, HANDLE)
                   if hasattr(user32, 'SetThreadDpiAwarenessContext') else None)
_get_dpi = (_bind(user32, 'GetDpiForWindow', U, HANDLE)
            if hasattr(user32, 'GetDpiForWindow') else None)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WM_DESTROY, WM_PAINT, WM_CLOSE, WM_QUIT = 0x0002, 0x000F, 0x0010, 0x0012
WM_ERASEBKGND, WM_MOUSEACTIVATE, WM_NCHITTEST = 0x0014, 0x0021, 0x0084
WM_TIMER, WM_DISPLAYCHANGE, WM_DPICHANGED = 0x0113, 0x007E, 0x02E0
WM_APP_SHOW, WM_APP_HIDE, WM_APP_CLOSE = 0x8001, 0x8002, 0x8003
DT_WORDBREAK, DT_SINGLELINE = 0x0010, 0x0020
DT_CALCRECT, DT_NOPREFIX, DT_END_ELLIPSIS = 0x0400, 0x0800, 0x8000
COLOR_KEY = 0x00FF00FF
TIMER_ID = 1
FONT = 'Microsoft YaHei UI'
LAYOUT = {'width': 440, 'height': 112, 'margin': 20, 'radius': 16,
          'icon': 48, 'text_left': 88, 'heading_top': 17, 'title_top': 34,
          'sub_top': 64, 'heading_size': 10, 'title_size': 22, 'sub_size': 13}


def _color(value):
    rgb = tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(value, str) else value
    return rgb[0] | rgb[1] << 8 | rgb[2] << 16


def _infer_tier(title, tier):
    if tier is not None:
        return normalize_tier(tier)
    # Only exact, known mode titles carry an inferred tier; adaptive is unknown.
    labels = {'省电模式': 'eco', '均衡模式': 'bal', '流畅模式': 'mid', '性能模式': 'perf',
              'eco': 'eco', 'balanced': 'bal', 'smooth': 'mid', 'performance': 'perf'}
    return labels.get(title.strip().lower(), 'unknown')


class Osd:
    CLASS_NAME = 'UmiPanelOsd'

    def __init__(self, log=None):
        self.log = log
        self._lock = threading.RLock()
        self._th = self._hwnd = self._thread_id = None
        self._ok = self._closed = False
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._generation = 0
        self._pending = None
        self._payload = ('', '', 2200, 'unknown')
        self._proc = None
        self._hinst = None
        self._registered = False
        self._class_name = '%s.%x' % (self.CLASS_NAME, id(self))
        self._fonts = {}
        self._icon = self._icon_key = None
        self._scale = 1.0
        self._width, self._height = LAYOUT['width'], LAYOUT['height']
        self._deadline = 0.0
        self._duration_ms = 2200
        self._visible = self._positioning = False

    @property
    def available(self):
        with self._lock:
            return self._ok and not self._closed

    def start(self):
        with self._lock:
            if self._closed:
                return False
            if self._th is None:
                self._th = threading.Thread(target=self._run, name='osd', daemon=True)
                try:
                    self._th.start()
                except RuntimeError as exc:
                    self._closed = True
                    self._ready.set()
                    self._stopped.set()
                    self._report(exc)
                    return False
        if not self._ready.wait(3.0):
            self.close()
            return False
        return self.available

    def show(self, title, sub='', ms=2200, tier=None):
        try:
            duration = max(1, min(0x7FFFFFFF, int(ms)))
        except (TypeError, ValueError, OverflowError):
            return False
        title, sub = str(title or ''), str(sub or '')
        if not self.start():
            return False
        with self._lock:
            if not self._ok or self._closed or not self._hwnd:
                return False
            self._generation += 1
            self._pending = (title, sub, duration, _infer_tier(title, tier))
            return bool(user32.PostMessageW(self._hwnd, WM_APP_SHOW, self._generation, 0))

    def hide(self):
        with self._lock:
            if self._closed:
                return False
            self._generation += 1
            self._pending = None
            return (bool(user32.PostMessageW(self._hwnd, WM_APP_HIDE, self._generation, 0))
                    if self._hwnd else True)

    def close(self):
        """Request owner-thread teardown without blocking the caller."""
        with self._lock:
            self._closed, self._ok = True, False
            self._pending = None
            if not self._hwnd:
                return True
            if user32.PostMessageW(self._hwnd, WM_APP_CLOSE, 0, 0):
                return True
            return bool(self._thread_id and user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0))

    def _report(self, exc):
        if self.log:
            self.log.info('[OSD] 屏幕提示不可用（不影响控制）：%r' % (exc,))

    def _run(self):
        previous_dpi = None
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            if _set_thread_dpi:
                previous_dpi = _set_thread_dpi(HANDLE(-4)) or _set_thread_dpi(HANDLE(-3))
            self._create()
            with self._lock:
                self._ok = not self._closed
            self._ready.set()
            if self._closed:
                return
            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if not result:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._report(exc)
        finally:
            with self._lock:
                self._ok = False
                hwnd, self._hwnd = self._hwnd, None
            if hwnd:
                user32.KillTimer(hwnd, TIMER_ID)
                user32.DestroyWindow(hwnd)
            self._release_artwork()
            if self._registered:
                user32.UnregisterClassW(self._class_name, self._hinst)
                self._registered = False
            if previous_dpi:
                _set_thread_dpi(previous_dpi)
            self._ready.set()
            self._stopped.set()

    def _create(self):
        self._hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wnd_proc)
        wc = _WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance = self._proc, self._hinst
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._registered = True
        ex = (WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW |
              WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)
        hwnd = user32.CreateWindowExW(ex, self._class_name, 'Umi OSD', WS_POPUP,
                                     0, 0, self._width, self._height, None, None, self._hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        with self._lock:
            self._hwnd = hwnd
        if not user32.SetLayeredWindowAttributes(hwnd, COLOR_KEY, 255, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def _px(self, value):
        return max(1, round(value * self._scale))

    def _font(self, size, weight=400):
        key = (self._px(size), weight)
        if key not in self._fonts:
            font = gdi32.CreateFontW(-key[0], 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, FONT)
            if not font:
                raise ctypes.WinError(ctypes.get_last_error())
            self._fonts[key] = font
        return self._fonts[key]

    def _release_artwork(self):
        if self._icon:
            user32.DestroyIcon(self._icon)
            self._icon = self._icon_key = None
        for font in self._fonts.values():
            gdi32.DeleteObject(font)
        self._fonts.clear()

    def _work_area(self):
        foreground = user32.GetForegroundWindow()
        monitor = user32.MonitorFromWindow(foreground or self._hwnd, 2)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.rcWork
        rect = wintypes.RECT()
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect

    def _position(self, hwnd):
        if self._positioning:
            return
        self._positioning = True
        try:
            work = self._work_area()
            # Move the hidden surface first so its own DPI reflects the target monitor.
            if not user32.SetWindowPos(hwnd, None, work.left, work.top, 0, 0,
                                      SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE):
                raise ctypes.WinError(ctypes.get_last_error())
            scale = ((_get_dpi(hwnd) or 96) / 96) if _get_dpi else 1.0
            if scale != self._scale:
                self._release_artwork()
                self._scale = scale
            margin = self._px(LAYOUT['margin'])
            work_w, work_h = work.right - work.left, work.bottom - work.top
            self._width = max(1, min(self._px(LAYOUT['width']), work_w - 2 * margin))
            text_width = max(1, self._width - self._px(LAYOUT['text_left']) - margin)
            hdc = user32.GetDC(hwnd)
            if not hdc:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                rect = wintypes.RECT(0, 0, text_width, 0)
                self._text(hdc, self._payload[1], rect, LAYOUT['sub_size'], PALETTE['muted'],
                           DT_WORDBREAK | DT_CALCRECT)
                desired = max(self._px(LAYOUT['height']),
                              self._px(LAYOUT['sub_top']) + rect.bottom + margin)
                self._height = max(1, min(desired, work_h - 2 * margin))
            finally:
                user32.ReleaseDC(hwnd, hdc)
            x = work.left + max(0, (work_w - self._width) // 2)
            y_offset = min(max(margin, round(work_h * .12)), max(0, work_h - self._height))
            y = work.top + y_offset
            if not user32.SetWindowPos(hwnd, HANDLE(-1), x, y, self._width, self._height, SWP_NOACTIVATE):
                raise ctypes.WinError(ctypes.get_last_error())
            icon_key = (self._payload[3], min(256, self._px(LAYOUT['icon'])))
            if icon_key != self._icon_key:
                icon = load_icon(*icon_key)
                if self._icon:
                    user32.DestroyIcon(self._icon)
                self._icon, self._icon_key = icon, icon_key
        finally:
            self._positioning = False

    def _hide_window(self, hwnd):
        user32.KillTimer(hwnd, TIMER_ID)
        self._deadline, self._visible = 0.0, False
        user32.ShowWindow(hwnd, SW_HIDE)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            return self._dispatch(hwnd, msg, wparam, lparam)
        except Exception as exc:
            with self._lock:
                self._ok = False
            self._hide_window(hwnd)
            user32.PostQuitMessage(1)
            self._report(exc)
            return 0

    def _dispatch(self, hwnd, msg, wparam, lparam):
        if msg in (WM_APP_SHOW, WM_APP_HIDE):
            with self._lock:
                if self._closed or wparam != self._generation:
                    return 0
                payload = self._pending
            if msg == WM_APP_HIDE:
                self._hide_window(hwnd)
            elif payload is not None:
                self._payload = payload
                self._hide_window(hwnd)
                self._position(hwnd)
                with self._lock:
                    if self._closed or wparam != self._generation:
                        return 0
                    self._duration_ms = payload[2]
                    self._deadline = time.monotonic() + self._duration_ms / 1000
                    if not user32.SetTimer(hwnd, TIMER_ID, min(16, self._duration_ms), None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    self._visible = True
                    user32.InvalidateRect(hwnd, None, False)
                    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            return 0
        if msg == WM_TIMER and wparam == TIMER_ID:
            if self._visible:
                if time.monotonic() >= self._deadline:
                    self._hide_window(hwnd)
                else:
                    user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg in (WM_CLOSE, WM_APP_CLOSE):
            self._hide_window(hwnd)
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            with self._lock:
                self._ok = False
                self._hwnd = None
            user32.KillTimer(hwnd, TIMER_ID)
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_MOUSEACTIVATE:
            return 3  # MA_NOACTIVATE
        if msg == WM_NCHITTEST:
            return -1  # HTTRANSPARENT
        if msg in (WM_DPICHANGED, WM_DISPLAYCHANGE) and self._visible:
            self._position(hwnd)
            user32.InvalidateRect(hwnd, None, False)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _text(self, hdc, text, rect, size, color, flags=DT_SINGLELINE | DT_END_ELLIPSIS, weight=400):
        font = self._font(size, weight)
        old = gdi32.SelectObject(hdc, font)
        try:
            gdi32.SetTextColor(hdc, _color(color))
            user32.DrawTextW(hdc, text, -1, ctypes.byref(rect), flags | DT_NOPREFIX)
        finally:
            gdi32.SelectObject(hdc, old)

    def _round(self, hdc, box, radius, color):
        region = brush = None
        try:
            left, top, right, bottom = box
            region = gdi32.CreateRoundRectRgn(left, top, right + 1, bottom + 1, radius * 2, radius * 2)
            brush = gdi32.CreateSolidBrush(_color(color))
            if not region or not brush or not gdi32.FillRgn(hdc, region, brush):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if brush:
                gdi32.DeleteObject(brush)
            if region:
                gdi32.DeleteObject(region)

    def _paint(self, hwnd):
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        buffer = bitmap = old = None
        try:
            buffer = gdi32.CreateCompatibleDC(hdc)
            bitmap = gdi32.CreateCompatibleBitmap(hdc, self._width, self._height)
            if not buffer or not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            old = gdi32.SelectObject(buffer, bitmap)
            self._draw(buffer)
            if not gdi32.BitBlt(hdc, 0, 0, self._width, self._height, buffer, 0, 0, 0x00CC0020):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if old:
                gdi32.SelectObject(buffer, old)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if buffer:
                gdi32.DeleteDC(buffer)
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _draw(self, hdc):
        w, h = self._width, self._height
        rect = wintypes.RECT(0, 0, w, h)
        key = gdi32.CreateSolidBrush(COLOR_KEY)
        if not key:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            user32.FillRect(hdc, ctypes.byref(rect), key)
        finally:
            gdi32.DeleteObject(key)
        radius, inset = self._px(LAYOUT['radius']), self._px(1)
        self._round(hdc, (0, 0, w, h), radius, PALETTE['border'])
        self._round(hdc, (inset, inset, w - inset, h - inset), radius - inset, PALETTE['bg'])
        gdi32.SetBkMode(hdc, 1)
        margin, left = self._px(LAYOUT['margin']), self._px(LAYOUT['text_left'])
        icon_size = self._px(LAYOUT['icon'])
        if self._icon:
            if not user32.DrawIconEx(hdc, margin, (h - icon_size) // 2, self._icon,
                                     icon_size, icon_size, 0, None, 3):
                raise ctypes.WinError(ctypes.get_last_error())
        self._text(hdc, 'UMI CONTROL PANEL',
                   wintypes.RECT(left, self._px(LAYOUT['heading_top']), w - margin, self._px(33)),
                   LAYOUT['heading_size'], PALETTE['muted'], weight=600)
        self._text(hdc, self._payload[0],
                   wintypes.RECT(left, self._px(LAYOUT['title_top']), w - margin, self._px(63)),
                   LAYOUT['title_size'], PALETTE['text'], weight=600)
        self._text(hdc, self._payload[1],
                   wintypes.RECT(left, self._px(LAYOUT['sub_top']), w - margin, h - margin),
                   LAYOUT['sub_size'], PALETTE['muted'], DT_WORDBREAK | DT_END_ELLIPSIS)
        y, thickness = h - self._px(9), self._px(2)
        self._round(hdc, (margin, y, w - margin, y + thickness), self._px(1), PALETTE['border'])
        fraction = max(0.0, min(1.0, (self._deadline - time.monotonic()) * 1000 / self._duration_ms))
        length = round((w - margin * 2) * fraction)
        if length > 0:
            self._round(hdc, (margin, y, margin + length, y + thickness), self._px(1),
                        TIER_COLORS[self._payload[3]])
