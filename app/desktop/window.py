"""原生桌面启动中心，网络请求与进程动作不占用窗口线程。"""
import ctypes as c
from ctypes import wintypes as w
import queue
import threading

from app import __version__
from app.desktop import native as n
from app.desktop.appearance import PALETTE as P, load_icon

WM_RESULT = 0x8008
STYLE = 0x00CA0000


class DesktopWindow:
    def __init__(self, controller):
        self.controller = controller
        self.hwnd = None
        self.controls = {}
        self.buttons = {}
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.closed = threading.Event()
        self.snapshot = {}
        self.busy = False
        self.refreshing = False
        self.developer = False
        self.message = ''
        self.scale = 1.0
        self.icons = {}
        self._tier = None
        self._proc = n.PROC(self._callback)
        self.class_name = 'UmiDesktopCenter'

    def px(self, value):
        return round(value * self.scale)

    def rect(self, x, y, width, height):
        return w.RECT(self.px(x), self.px(y), self.px(x + width), self.px(y + height))

    def create(self, show=5):
        try:
            n.signature(n.u, 'SetProcessDpiAwarenessContext', w.BOOL, c.c_void_p)(-4)
            n.signature(n.u, 'GetDpiForSystem', w.UINT)
            self.scale = n.u.GetDpiForSystem() / 96
        except AttributeError:
            pass
        work = w.RECT()
        n.u.SystemParametersInfoW(48, 0, c.byref(work), 0)
        self.scale = min(self.scale, max(.75, (work.bottom - work.top - 60) / 746))
        self.face = n.font(self.px(14))
        self.heading = n.font(self.px(25), True)
        self.small = n.font(self.px(12))
        self.brush = n.g.CreateSolidBrush(n.color(P['bg']))
        instance = n.k.GetModuleHandleW(None)
        self.icons['unknown'] = load_icon('unknown', self.px(48))
        cls = n.WindowClass(0, self._proc, 0, 0, instance, self.icons['unknown'],
                            n.u.LoadCursorW(None, 32512), self.brush, None, self.class_name)
        if not n.u.RegisterClassW(c.byref(cls)):
            raise OSError(c.get_last_error(), '桌面窗口注册失败')
        bounds = self.rect(0, 0, 600, 648)
        n.u.AdjustWindowRectEx(c.byref(bounds), STYLE, False, 0)
        width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
        x = work.left + max(0, (work.right - work.left - width) // 2)
        y = work.top + max(0, (work.bottom - work.top - height) // 2)
        self.hwnd = n.u.CreateWindowExW(0, self.class_name, 'Umi Control Panel · 启动中心',
                                      STYLE, x, y, width, height, None, None, instance, None)
        if not self.hwnd:
            raise OSError(c.get_last_error(), '桌面窗口创建失败')
        try:
            dwm = c.WinDLL('dwmapi')
            n.signature(dwm, 'DwmSetWindowAttribute', c.c_long, w.HWND, w.DWORD, c.c_void_p, w.DWORD)
            dark = c.c_int(1)
            dwm.DwmSetWindowAttribute(self.hwnd, 20, c.byref(dark), c.sizeof(dark))
        except OSError:
            pass
        self._label('brand', 'UMI CONTROL PANEL', 92, 28, 470, 30, self.heading)
        self._label('tagline', '本地控制中心  /  桌面入口', 94, 66, 440, 22, self.small)
        self._label('connection', '正在连接本机服务', 40, 124, 510, 24, self.small)
        self._label('mode', '读取状态…', 40, 154, 510, 40, self.heading)
        self._label('detail', '关闭此窗口不会停止面板；服务独立在后台运行。', 40, 202, 520, 25, self.small)
        self._button('primary', '等待连接', 40, 242, 520, 44, primary=True)
        self._label('preferences', '桌面偏好', 28, 320, 500, 24)
        self._button('osd', 'Umi 屏幕提示 · 读取中', 28, 354, 266, 42)
        self._button('preview', '预览提示', 306, 354, 266, 42)
        self._button('autostart', '登录后静默启动 · 读取中', 28, 408, 266, 42)
        self._button('shortcut', '创建快捷方式', 306, 408, 266, 42)
        self._label('scope', '只控制 Umi 提示，不改变官方软件的 OSD 开关。', 28, 464, 542, 22, self.small)
        self._label('feedback', '', 28, 499, 542, 54, self.small)
        self._button('developer', '开发者模式 · 展开', 28, 568, 266, 38)
        self._button('logs', '查看日志与本机配置', 306, 568, 266, 38)
        self._label('footer', 'v%s  ·  本地运行  ·  无云端账户' % __version__, 28, 618, 542, 20, self.small)
        self._button('console', '打开 CMD 终端', 28, 624, 170, 40)
        self._button('map', '初始化本机映射', 214, 624, 170, 40)
        self._button('scripts', '高级脚本目录', 400, 624, 172, 40)
        self._label('devnote', '终端以普通权限打开，不自动执行调试指令。\n配置可能含口令；硬件测试和重启请先阅读 README。', 28, 678, 542, 44, self.small)
        for name in ('console', 'map', 'scripts', 'devnote'):
            n.u.ShowWindow(self.controls[name], 0)
        for name in ('primary', 'osd', 'preview', 'autostart'):
            n.u.EnableWindow(self.controls[name], False)
        self._worker = threading.Thread(target=self._work, name='desktop-io', daemon=True)
        self._worker.start()
        n.u.SetTimer(self.hwnd, 1, 2000, None)
        n.u.ShowWindow(self.hwnd, show)
        self.refresh()
        return self.hwnd

    def _label(self, name, text, x, y, width, height, face=None):
        self._control(name, 'STATIC', text, x, y, width, height, 0, face)

    def _button(self, name, text, x, y, width, height, primary=False):
        self._control(name, 'BUTTON', text, x, y, width, height, 0x0001000B)
        self.buttons[self.controls[name]] = (name, primary)

    def _control(self, name, kind, text, x, y, width, height, style, face=None):
        ident = 100 + len(self.controls)
        handle = n.u.CreateWindowExW(0, kind, text, 0x50000000 | style,
                                     self.px(x), self.px(y), self.px(width), self.px(height),
                                     self.hwnd, ident, n.k.GetModuleHandleW(None), None)
        if not handle:
            raise OSError(c.get_last_error(), '控件创建失败：' + name)
        self.controls[name] = handle
        n.u.SendMessageW(handle, 0x0030, face or self.face, 1)

    def _set(self, name, text):
        n.u.SetWindowTextW(self.controls[name], text)

    def refresh(self):
        if not self.refreshing and not self.busy and not self.closed.is_set():
            self.refreshing = True
            self.jobs.put(('refresh', None))

    def _work(self):
        while not self.closed.is_set():
            item = self.jobs.get()
            if item is None:
                return
            name, action = item
            try:
                value = self.controller.snapshot() if name == 'refresh' else action()
                result = (name, True, value)
            except Exception as exc:
                result = (name, False, str(exc))
            self.results.put(result)
            if not self.closed.is_set():
                n.u.PostMessageW(self.hwnd, WM_RESULT, 0, 0)

    def _apply_results(self):
        while not self.results.empty():
            name, ok, value = self.results.get_nowait()
            if name == 'refresh':
                self.refreshing = False
                if ok:
                    self.snapshot = value
                else:
                    self.snapshot = {}
                    self.message = '读取失败：' + value
            else:
                self.busy = False
                self.message = value if ok else '未完成：' + value
                self.refresh()
        self._render()

    def _render(self):
        st = self.snapshot
        online, starting = st.get('online'), st.get('starting')
        self._set('connection', '服务已连接' if online else ('正在后台启动' if starting else '服务未连接'))
        self._set('mode', (st.get('label', '未知') + '模式') if online else ('等待服务就绪…' if starting else '准备好，再出发'))
        self._set('primary', '打开完整控制台' if online else ('启动中，请稍候…' if starting else '启动面板服务'))
        detail = '完整的风扇、功耗与灯光控制在浏览器面板中。'
        if online and not st.get('healthy'):
            detail = '连接存在，但调度节拍未就绪或已延迟，请查看日志。'
        if not online:
            detail = '启动后常驻托盘；关闭这个窗口不会停止服务。'
        self._set('detail', detail)
        osd = st.get('osd')
        self._set('osd', 'Umi 屏幕提示 · ' + ('开启' if osd else '关闭') if osd is not None else 'Umi 屏幕提示 · 暂不可用')
        auto = st.get('autostart') or {}
        auto_text = '更新旧版自启为静默启动' if auto.get('enabled') and not auto.get('current') else '登录后静默启动 · ' + ('开启' if auto.get('enabled') else '关闭')
        self._set('autostart', auto_text)
        message = self.message or st.get('error') or st.get('autostart_error') or ''
        if not message and online and osd is None:
            message = '运行中的后端较旧；下次正常重启后可使用新的 OSD 设置。'
        if not message and not st.get('mapped'):
            message = '首次使用：在开发者模式中初始化本机映射；未知机型不要开启硬件写入。'
        self._set('feedback', message)
        for name in ('primary', 'osd', 'preview', 'autostart', 'shortcut', 'logs', 'console', 'map', 'scripts'):
            enabled = not self.busy
            if name == 'primary':
                enabled = enabled and bool(st) and not starting
            if name in ('osd', 'preview'):
                enabled = enabled and online and osd is not None
                if name == 'preview':
                    enabled = enabled and osd and st.get('osd_available', False)
            if name == 'autostart':
                enabled = enabled and bool(st) and not st.get('autostart_error')
            n.u.EnableWindow(self.controls[name], enabled)
        tier = st.get('tier') if online else 'unknown'
        if tier != self._tier:
            self._tier = tier
            if tier not in self.icons:
                self.icons[tier] = load_icon(tier, self.px(48))
            n.u.SendMessageW(self.hwnd, 0x0080, 1, self.icons[tier])
            n.u.SendMessageW(self.hwnd, 0x0080, 0, self.icons[tier])
        n.u.InvalidateRect(self.hwnd, None, False)

    def _toggle_developer(self):
        self.developer = not self.developer
        self._set('developer', '开发者模式 · ' + ('收起' if self.developer else '展开'))
        for name in ('console', 'map', 'scripts', 'devnote'):
            n.u.ShowWindow(self.controls[name], 5 if self.developer else 0)
        n.u.ShowWindow(self.controls['footer'], 0 if self.developer else 5)
        bounds = self.rect(0, 0, 600, 746 if self.developer else 648)
        n.u.AdjustWindowRectEx(c.byref(bounds), STYLE, False, 0)
        work, current = w.RECT(), w.RECT()
        n.u.SystemParametersInfoW(48, 0, c.byref(work), 0)
        n.signature(n.u, 'GetWindowRect', w.BOOL, w.HWND, c.POINTER(w.RECT))
        n.u.GetWindowRect(self.hwnd, c.byref(current))
        height = bounds.bottom - bounds.top
        y = max(work.top, min(current.top, work.bottom - height))
        n.u.SetWindowPos(self.hwnd, None, current.left, y, bounds.right - bounds.left,
                        height, 0x0014)
        n.u.InvalidateRect(self.hwnd, None, True)

    def command(self, name):
        if name == 'developer':
            self._toggle_developer()
            return
        if self.busy:
            return
        st = self.snapshot
        if name in ('osd', 'preview') and st.get('osd') is None:
            return
        actions = {
            'primary': self.controller.open_panel if st.get('online') else self.controller.start,
            'osd': lambda: self.controller.set_osd(not st.get('osd')),
            'preview': self.controller.preview_osd,
            'autostart': lambda: self.controller.toggle_autostart(not (st.get('autostart') or {}).get('current')),
            'shortcut': self.controller.shortcut,
            'logs': lambda: self.controller.open_folder('data'),
            'console': self.controller.console,
            'map': self.controller.prepare_map,
            'scripts': lambda: self.controller.open_folder('scripts'),
        }
        action = actions.get(name)
        if not action:
            return
        if name == 'map' and n.u.MessageBoxW(self.hwnd,
                '即将打开 CMD，读取本机已安装的 OEM 元数据并生成映射文件。\n不会写 EC，也不会停止面板。继续？',
                '初始化本机映射', 0x124) != 6:
            return
        self.busy = True
        self.message = '正在处理…'
        self._render()
        self.jobs.put((name, action))

    def _paint(self):
        ps = c.create_string_buffer(88)
        dc = n.u.BeginPaint(self.hwnd, ps)
        try:
            rect = w.RECT()
            n.u.GetClientRect(self.hwnd, c.byref(rect))
            n.u.FillRect(dc, c.byref(rect), self.brush)
            n.round_rect(dc, self.rect(28, 110, 544, 192), P['surface'], self.px(18))
            icon = self.icons.get(self._tier) or self.icons['unknown']
            n.u.DrawIconEx(dc, self.px(28), self.px(32), icon, self.px(48), self.px(48), 0, None, 3)
        finally:
            n.u.EndPaint(self.hwnd, ps)

    def _draw_button(self, item):
        name, primary = self.buttons.get(item.hwnd, ('', False))
        pressed, disabled, focused = item.state & 1, item.state & 4, item.state & 16
        fill = P['accent'] if primary and not disabled else P['surface']
        ink = P['bg'] if primary and not disabled else (P['muted'] if disabled else P['text'])
        if pressed:
            fill = P['border']
            ink = P['text']
        n.u.FillRect(item.dc, c.byref(item.rect), self.surface_brush if primary else self.brush)
        n.round_rect(item.dc, item.rect, P['accent'] if focused else P['border'], self.px(10))
        inner = w.RECT(item.rect.left + 1, item.rect.top + 1, item.rect.right - 1, item.rect.bottom - 1)
        n.round_rect(item.dc, inner, fill, self.px(10))
        buf = c.create_unicode_buffer(200)
        n.u.GetWindowTextW(item.hwnd, buf, 200)
        n.text(item.dc, buf.value, inner, self.face, ink, 0x25)
        if focused:
            focus = w.RECT(inner.left + 4, inner.top + 4, inner.right - 4, inner.bottom - 4)
            n.u.DrawFocusRect(item.dc, c.byref(focus))

    def _callback(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x0010:
                n.u.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:
                self.closed.set()
                self.jobs.put(None)
                n.u.KillTimer(hwnd, 1)
                n.u.PostQuitMessage(0)
                return 0
            if msg == 0x0111 and lp in self.buttons:
                self.command(self.buttons[lp][0])
                return 0
            if msg == 0x0113:
                self.refresh()
                return 0
            if msg == WM_RESULT:
                self._apply_results()
                return 0
            if msg == 0x002B:
                self._draw_button(c.cast(lp, c.POINTER(n.DrawItem)).contents)
                return 1
            if msg == 0x0138:
                name = next((key for key, value in self.controls.items() if value == lp), '')
                fill = P['surface'] if name in ('connection', 'mode', 'detail') else P['bg']
                n.g.SetTextColor(wp, n.color(P['text'] if name in ('brand', 'mode', 'preferences') else P['muted']))
                n.g.SetBkColor(wp, n.color(fill))
                if name in ('connection', 'mode', 'detail'):
                    return self.surface_brush
                return self.brush
            if msg == 0x000F:
                self._paint()
                return 0
            if msg == 0x0014:
                return 1
        except Exception as exc:
            self.message = '界面操作失败：%s' % exc
            if 'feedback' in self.controls:
                self._set('feedback', self.message)
        return n.u.DefWindowProcW(hwnd, msg, wp, lp)

    def run(self, show=5):
        self.surface_brush = n.g.CreateSolidBrush(n.color(P['surface']))
        try:
            self.create(show)
            msg = w.MSG()
            while n.u.GetMessageW(c.byref(msg), None, 0, 0) > 0:
                if not n.u.IsDialogMessageW(self.hwnd, c.byref(msg)):
                    n.u.TranslateMessage(c.byref(msg))
                    n.u.DispatchMessageW(c.byref(msg))
        finally:
            self.closed.set()
            self.jobs.put(None)
            for handle in self.icons.values():
                if handle:
                    n.u.DestroyIcon(handle)
            for name in ('face', 'heading', 'small', 'brush', 'surface_brush'):
                handle = getattr(self, name, None)
                if handle:
                    n.g.DeleteObject(handle)
            n.u.UnregisterClassW(self.class_name, n.k.GetModuleHandleW(None))
        return 0
