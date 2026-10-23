"""原生桌面启动中心，网络请求与进程动作不占用窗口线程。"""
import ctypes as c
from ctypes import wintypes as w
import queue
import threading

from app import __version__
from app.desktop import native as n
from app.desktop.appearance import PALETTE as P, load_icon
from app.desktop.controller import DEBUG_SCRIPTS

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
        self.view = 'home'
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
        self.scale = min(self.scale, max(.75, (work.bottom - work.top - 60) / 648))
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
        self._button('stop', '停止面板', 306, 408, 266, 42)
        self._button('maintenance', '调试与维护', 28, 462, 266, 38)
        self._button('logs', '打开日志与配置', 306, 462, 266, 38)
        self._label('scope', '只控制 Umi 提示；关闭窗口不会停止服务。', 28, 508, 542, 20, self.small)
        self._label('feedback', '', 28, 534, 542, 58, self.small)
        self._label('footer', 'v%s  ·  本地运行  ·  无云端账户' % __version__, 28, 618, 542, 20, self.small)
        self._button('back', '返回', 470, 28, 100, 30)
        self._label('maint-title', '调试与维护', 40, 116, 420, 34, self.heading)
        self._label('maint-sub', '调试脚本会打开可见窗口；标“停面板”的脚本会先停止服务。', 28, 154, 542, 20, self.small)
        self._label('maint-runtime', '运行环境：读取中…', 28, 184, 338, 22, self.small)
        self._button('runtime-install', '安装运行环境', 372, 178, 198, 34)
        self._label('scripts-label', '调试脚本', 28, 224, 300, 20)
        self._script_button('script-panel-state', 'panel-state', 28, 250)
        self._script_button('script-health', 'health', 306, 250)
        self._script_button('script-ec-test', 'ec-test', 28, 292)
        self._script_button('script-gen-map', 'gen-map', 306, 292)
        self._script_button('script-watch-ec', 'watch-ec', 28, 334)
        self._script_button('script-watch-change', 'watch-change', 306, 334)
        self._script_button('script-watch-oem', 'watch-oem', 28, 376)
        self._script_button('script-capture-cc', 'capture-cc', 306, 376)
        self._script_button('script-fan-three', 'fan-three', 28, 418)
        self._label('script-note', '标“停面板”的脚本不会自动重启；写硬件的脚本会尝试还原。请先阅读窗口提示。',
                   28, 458, 542, 34, self.small)
        self._button('maint-data', '打开日志与配置', 28, 500, 256, 34)
        self._button('maint-scripts', '打开脚本目录', 306, 500, 256, 34)
        self._label('maint-feedback', '', 28, 542, 542, 58, self.small)
        self._label('maint-footer', '运行环境安装位置：项目 runtime\\ 目录', 28, 614, 542, 20, self.small)
        for name in ('back', 'maint-title', 'maint-sub', 'maint-runtime', 'runtime-install',
                     'scripts-label', 'script-note', 'maint-data', 'maint-scripts',
                     'maint-feedback', 'maint-footer') + tuple(
                         'script-' + item[0] for item in DEBUG_SCRIPTS):
            n.u.ShowWindow(self.controls[name], 0)
        for name in ('primary', 'osd', 'preview', 'autostart', 'stop'):
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

    def _script_button(self, name, key, x, y):
        script = next(item for item in DEBUG_SCRIPTS if item[0] == key)
        suffix = {'stop': ' · 停面板', 'write': ' · 写硬件'}.get(script[3], '')
        self._button(name, script[1] + suffix, x, y, 256, 34)

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
        runtime = st.get('runtime') or {}
        if not message and runtime and not runtime.get('ready'):
            message = '项目 runtime 未就绪：打开“调试与维护”，可安装到项目 runtime 目录。'
        if not message and not st.get('mapped'):
            message = '首次使用：在“调试与维护”生成 EC 映射；未知机型不要开启硬件写入。'
        self._set('feedback', message)
        self._set('stop', '停止面板' if online else '停止面板 · 未连接')
        for name in ('primary', 'osd', 'preview', 'autostart', 'stop', 'maintenance', 'logs'):
            enabled = not self.busy
            if name == 'primary':
                enabled = enabled and bool(st) and not starting
            if name in ('osd', 'preview'):
                enabled = enabled and online and osd is not None
                if name == 'preview':
                    enabled = enabled and osd and st.get('osd_available', False)
            if name == 'autostart':
                enabled = enabled and bool(st) and not st.get('autostart_error')
            if name == 'stop':
                enabled = enabled and online
            n.u.EnableWindow(self.controls[name], enabled)
        tier = st.get('tier') if online else 'unknown'
        if tier != self._tier:
            self._tier = tier
            if tier not in self.icons:
                self.icons[tier] = load_icon(tier, self.px(48))
            n.u.SendMessageW(self.hwnd, 0x0080, 1, self.icons[tier])
            n.u.SendMessageW(self.hwnd, 0x0080, 0, self.icons[tier])
        if self.view == 'maintenance':
            self._render_maintenance()
        n.u.InvalidateRect(self.hwnd, None, False)

    HOME_CONTROLS = ('brand', 'tagline', 'connection', 'mode', 'detail', 'primary',
                     'preferences', 'osd', 'preview', 'autostart', 'stop',
                     'maintenance', 'logs', 'scope', 'feedback', 'footer')

    def _show_home(self):
        self.view = 'home'
        for name in self.HOME_CONTROLS:
            n.u.ShowWindow(self.controls[name], 5)
        for name in self._maintenance_controls():
            n.u.ShowWindow(self.controls[name], 0)

    def _show_maintenance(self):
        self.view = 'maintenance'
        for name in self.HOME_CONTROLS:
            n.u.ShowWindow(self.controls[name], 0)
        for name in self._maintenance_controls():
            n.u.ShowWindow(self.controls[name], 5)
        self._render_maintenance()

    @staticmethod
    def _maintenance_controls():
        return ('back', 'maint-title', 'maint-sub', 'maint-runtime', 'runtime-install',
                'scripts-label', 'script-note', 'maint-data', 'maint-scripts',
                'maint-feedback', 'maint-footer') + tuple(
                    'script-' + item[0] for item in DEBUG_SCRIPTS)

    def _render_maintenance(self):
        runtime = self.snapshot.get('runtime') or {}
        missing = [label for key, label in (('python', 'python.exe'),
                                            ('panel_alias', 'UmiPanel.exe'),
                                            ('anchor_alias', 'OcTool.exe')) if not runtime.get(key)]
        if not runtime:
            runtime_text = '运行环境：读取中…'
        elif runtime.get('ready'):
            runtime_text = '运行环境：已就绪（项目 runtime 目录）'
        else:
            runtime_text = '运行环境：缺少 ' + ' / '.join(missing)
        self._set('maint-runtime', runtime_text)
        self._set('runtime-install', '项目 runtime 已就绪' if runtime.get('ready') else '安装到项目 runtime')
        message = self.message or ''
        if not message:
            message = ('运行环境会安装到项目 runtime\\ 目录；调试脚本只从 scripts 固定清单启动。')
        self._set('maint-feedback', message)
        for name in ('back', 'maint-data', 'maint-scripts') + tuple(
                'script-' + item[0] for item in DEBUG_SCRIPTS):
            n.u.EnableWindow(self.controls[name], not self.busy)
        n.u.EnableWindow(self.controls['runtime-install'],
                         not self.busy and bool(runtime) and not runtime.get('ready'))

    def command(self, name):
        if name == 'maintenance':
            self.message = ''
            self._show_maintenance()
            return
        if name == 'back':
            if not self.busy:
                self._show_home()
            return
        if self.busy:
            return
        if name.startswith('script-'):
            key = name[len('script-'):]
            script = next((item for item in DEBUG_SCRIPTS if item[0] == key), None)
            if script is None:
                return
            if script[3] == 'stop' and n.u.MessageBoxW(
                    self.hwnd,
                    '“%s”会先停止面板，脚本结束后不会自动拉起。\n'
                    '请确认已阅读脚本窗口中的操作说明。继续？' % script[1],
                    '运行调试脚本', 0x124) != 6:
                return
            if script[3] == 'write' and n.u.MessageBoxW(
                    self.hwnd,
                    '“%s”会停止面板、写风扇模式并尝试还原，约 6 分钟。\n'
                    '仅在明确需要现场调试时继续。' % script[1],
                    '运行硬件调试脚本', 0x124) != 6:
                return
            self._run_action(name, lambda: self.controller.run_debug_script(key))
            return
        if name == 'runtime-install':
            if n.u.MessageBoxW(
                    self.hwnd,
                    '将检查并补齐项目 runtime\\ 目录中的 Python 与进程别名。\n'
                    '缺少本机 Python 时可能联网下载官方 embeddable 包，不请求管理员权限。继续？',
                    '安装运行环境', 0x124) != 6:
                return
            self._run_action(name, self.controller.install_runtime,
                             '正在安装运行环境，首次可能需要下载…')
            return
        if name == 'stop':
            if n.u.MessageBoxW(
                    self.hwnd,
                    '将优雅停止面板并还原改过的电源设置；脚本或托盘也随之退出。\n'
                    '停止后可用上方按钮重新启动。继续？',
                    '停止面板', 0x124) != 6:
                return
            self._run_action(name, self.controller.stop_service, '正在请求面板停止…')
            return
        st = self.snapshot
        if name in ('osd', 'preview') and st.get('osd') is None:
            return
        actions = {
            'primary': self.controller.open_panel if st.get('online') else self.controller.start,
            'osd': lambda: self.controller.set_osd(not st.get('osd')),
            'preview': self.controller.preview_osd,
            'autostart': lambda: self.controller.toggle_autostart(not (st.get('autostart') or {}).get('current')),
            'logs': lambda: self.controller.open_folder('data'),
            'maint-data': lambda: self.controller.open_folder('data'),
            'maint-scripts': lambda: self.controller.open_folder('scripts'),
        }
        action = actions.get(name)
        if action:
            self._run_action(name, action)

    def _run_action(self, name, action, message='正在处理…'):
        self.busy = True
        self.message = message
        self._render()
        self.jobs.put((name, action))

    def _paint(self):
        ps = c.create_string_buffer(88)
        dc = n.u.BeginPaint(self.hwnd, ps)
        try:
            rect = w.RECT()
            n.u.GetClientRect(self.hwnd, c.byref(rect))
            n.u.FillRect(dc, c.byref(rect), self.brush)
            if self.view == 'home':
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
