"""桌面窗口使用的 Win32 声明与绘制原语。"""
import ctypes as c
from ctypes import wintypes as w

u = c.WinDLL('user32', use_last_error=True)
g = c.WinDLL('gdi32', use_last_error=True)
k = c.WinDLL('kernel32', use_last_error=True)
H = c.c_void_p
PROC = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WindowClass(c.Structure):
    _fields_ = [('style', w.UINT), ('proc', PROC), ('clsExtra', c.c_int), ('winExtra', c.c_int),
                ('instance', H), ('icon', H), ('cursor', H), ('background', H),
                ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]


class DrawItem(c.Structure):
    _fields_ = [('type', w.UINT), ('id', w.UINT), ('item_id', w.UINT),
                ('action', w.UINT), ('state', w.UINT), ('hwnd', w.HWND),
                ('dc', H), ('rect', w.RECT), ('data', c.c_size_t)]


def signature(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype = result
    fn.argtypes = list(args)
    return fn


signature(k, 'GetModuleHandleW', H, w.LPCWSTR)
signature(u, 'RegisterClassW', w.ATOM, c.POINTER(WindowClass))
signature(u, 'UnregisterClassW', w.BOOL, w.LPCWSTR, H)
signature(u, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
          c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, H, H, H)
signature(u, 'DefWindowProcW', c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
signature(u, 'SendMessageW', c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
signature(u, 'PostMessageW', w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
signature(u, 'SetWindowTextW', w.BOOL, w.HWND, w.LPCWSTR)
signature(u, 'GetWindowTextW', c.c_int, w.HWND, w.LPWSTR, c.c_int)
signature(u, 'EnableWindow', w.BOOL, w.HWND, w.BOOL)
signature(u, 'ShowWindow', w.BOOL, w.HWND, c.c_int)
signature(u, 'DestroyWindow', w.BOOL, w.HWND)
signature(u, 'DestroyIcon', w.BOOL, H)
signature(u, 'SetFocus', w.HWND, w.HWND)
signature(u, 'LoadCursorW', H, H, H)
signature(u, 'SetTimer', c.c_size_t, w.HWND, c.c_size_t, w.UINT, H)
signature(u, 'KillTimer', w.BOOL, w.HWND, c.c_size_t)
signature(u, 'InvalidateRect', w.BOOL, w.HWND, c.POINTER(w.RECT), w.BOOL)
signature(u, 'GetClientRect', w.BOOL, w.HWND, c.POINTER(w.RECT))
signature(u, 'AdjustWindowRectEx', w.BOOL, c.POINTER(w.RECT), w.DWORD, w.BOOL, w.DWORD)
signature(u, 'SetWindowPos', w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
signature(u, 'GetMessageW', c.c_int, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
signature(u, 'PostQuitMessage', None, c.c_int)
signature(u, 'SystemParametersInfoW', w.BOOL, w.UINT, w.UINT, H, w.UINT)
signature(u, 'TranslateMessage', w.BOOL, c.POINTER(w.MSG))
signature(u, 'DispatchMessageW', c.c_ssize_t, c.POINTER(w.MSG))
signature(u, 'IsDialogMessageW', w.BOOL, w.HWND, c.POINTER(w.MSG))
signature(u, 'BeginPaint', H, w.HWND, H)
signature(u, 'EndPaint', w.BOOL, w.HWND, H)
signature(u, 'FillRect', c.c_int, H, c.POINTER(w.RECT), H)
signature(u, 'DrawTextW', c.c_int, H, w.LPCWSTR, c.c_int, c.POINTER(w.RECT), w.UINT)
signature(u, 'DrawFocusRect', w.BOOL, H, c.POINTER(w.RECT))
signature(u, 'DrawIconEx', w.BOOL, H, c.c_int, c.c_int, H, c.c_int, c.c_int, w.UINT, H, w.UINT)
signature(u, 'MessageBoxW', c.c_int, w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT)
signature(g, 'CreateSolidBrush', H, w.COLORREF)
signature(g, 'CreateRoundRectRgn', H, c.c_int, c.c_int, c.c_int, c.c_int, c.c_int, c.c_int)
signature(g, 'FillRgn', w.BOOL, H, H, H)
signature(g, 'DeleteObject', w.BOOL, H)
signature(g, 'SelectObject', H, H, H)
signature(g, 'SetTextColor', w.COLORREF, H, w.COLORREF)
signature(g, 'SetBkColor', w.COLORREF, H, w.COLORREF)
signature(g, 'SetBkMode', c.c_int, H, c.c_int)
signature(g, 'CreateFontW', H, *([c.c_int] * 5 + [w.DWORD] * 8 + [w.LPCWSTR]))


def color(value):
    r, green, b = (int(value.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
    return r | green << 8 | b << 16


def font(size, bold=False):
    return g.CreateFontW(-size, 0, 0, 0, 600 if bold else 400, 0, 0, 0,
                         1, 0, 0, 5, 0, 'Microsoft YaHei UI')


def round_rect(dc, rect, fill, radius=10):
    brush = g.CreateSolidBrush(color(fill))
    region = g.CreateRoundRectRgn(rect.left, rect.top, rect.right + 1, rect.bottom + 1, radius, radius)
    g.FillRgn(dc, region, brush)
    g.DeleteObject(region)
    g.DeleteObject(brush)


def text(dc, value, rect, face, fill, flags=0x24):
    old = g.SelectObject(dc, face)
    g.SetBkMode(dc, 1)
    g.SetTextColor(dc, color(fill))
    u.DrawTextW(dc, value, -1, c.byref(rect), flags | 0x800)
    g.SelectObject(dc, old)
