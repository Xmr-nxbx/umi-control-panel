"""桌面动作通过本地服务执行；不构造硬件通道。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from app.paths import ROOT_DIR

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'UmiControlPanel'
NO_WINDOW = 0x08000000
NEW_CONSOLE = 0x00000010

DEBUG_SCRIPTS = (
    ('panel-state', '面板状态', '面板状态.bat', ''),
    ('health', '一键体检', '一键体检.bat', ''),
    ('ec-test', 'EC 只读自检', 'EC只读自检.bat', ''),
    ('gen-map', '生成 EC 映射', '生成EC寄存器表.bat', ''),
    ('watch-ec', '观察 EC 全表', '观察EC全表.bat', 'stop'),
    ('watch-change', '观察 EC 变化', '观察EC变化.bat', 'stop'),
    ('watch-oem', '观察 OEM 通道', '观察OEM通道.bat', 'stop'),
    ('capture-cc', '抓取 CC 配置', '抓CreatorCenter配置.bat', ''),
    ('fan-three', '造物者三态实测', '造物者三态实测.bat', 'write'),
)


def autostart_command(root=ROOT_DIR):
    host = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'wscript.exe')
    return subprocess.list2cmdline([host, str(Path(root) / 'Umi Control Panel.vbs'), '/background'])


def autostart_status(root=ROOT_DIR):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
    except FileNotFoundError:
        return {'enabled': False, 'current': False}
    return {'enabled': True, 'current': value == autostart_command(root)}


def set_autostart(enabled, root=ROOT_DIR):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command(root))
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


class DesktopController:
    def __init__(self, root=ROOT_DIR):
        self.root = Path(root)
        self._starting = None
        self._started_at = 0
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def port(self):
        try:
            value = int((self.root / 'data' / 'port').read_text(encoding='utf-8').strip())
            if 1 <= value <= 65535:
                return value
        except (OSError, ValueError):
            pass
        return 8747

    def request(self, path, body=None):
        origin = 'http://127.0.0.1:%d' % self.port()
        data = None if body is None else json.dumps(body).encode('utf-8')
        req = urllib.request.Request(origin + path, data=data,
                                     headers={'Content-Type': 'application/json', 'Origin': origin})
        try:
            with self._http.open(req, timeout=5) as response:
                result = json.loads(response.read(1024 * 1024).decode('utf-8'))
                if not isinstance(result, dict):
                    raise ValueError('服务响应格式无效')
                return result
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise RuntimeError('运行中的服务版本较旧，请在方便时正常重启面板。') from exc
            try:
                body = json.loads(exc.read(65536))
                detail = body.get('detail') if isinstance(body, dict) else None
            except (ValueError, OSError):
                detail = None
            raise RuntimeError(detail or '服务拒绝操作（HTTP %s）' % exc.code) from exc

    def snapshot(self):
        result = {'online': False, 'tier': None, 'label': '尚未连接', 'osd': None,
                  'osd_available': False, 'version': '', 'starting': False,
                  'error': '', 'autostart_error': '', 'runtime': self.runtime_info()}
        try:
            result['autostart'] = autostart_status(str(self.root))
        except OSError:
            result['autostart'] = {'enabled': False, 'current': False}
            result['autostart_error'] = '无法读取登录自启设置'
        result['mapped'] = (self.root / 'data' / 'ec_map.local.json').is_file()
        try:
            state = self.request('/api/state')
            if not isinstance(state.get('meta'), dict) or 'version' not in state['meta']:
                raise ValueError('不是 Umi 面板服务')
            age = state.get('tick_age_s')
            result.update(online=True, tier=state.get('tier'),
                          label=state.get('tier_label') or '等待档位',
                          version=state['meta']['version'],
                          healthy=age is not None and age < 30)
            if state['meta'].get('desktop_preferences'):
                prefs = self.request('/api/desktop')
                if type(prefs.get('osd')) is not bool or type(prefs.get('available')) is not bool:
                    raise ValueError('桌面偏好格式无效')
                result.update(osd=prefs['osd'], osd_available=prefs['available'])
                if not prefs['available']:
                    result['error'] = '当前桌面提示不可用；可保存偏好，暂不能预览。'
        except (OSError, ValueError, RuntimeError) as exc:
            if result['online']:
                result['error'] = str(exc)
        if self._starting is not None:
            code = self._starting.poll()
            if result['online']:
                self._starting = None
            elif code is not None:
                result['error'] = '启动进程已退出（%s），请查看本机日志。' % code
                self._starting = None
            elif time.monotonic() - self._started_at > 100:
                result['error'] = '启动超时；未强制终止进程，请在诊断中查看日志。'
            else:
                result['starting'] = True
        return result

    def start(self):
        if self.snapshot()['online']:
            return '面板已在运行，没有重复启动。'
        if self._starting is not None and self._starting.poll() is None:
            return '仍在等待启动，请勿重复点击。'
        exe = self.root / 'runtime' / 'UmiPanel.exe'
        if not exe.is_file():
            exe = self.root / 'runtime' / 'python.exe'
        if not exe.is_file():
            raise RuntimeError('缺少便携运行时，请先准备运行时。')
        self._starting = subprocess.Popen(
            [str(exe), str(self.root / 'main.py'), '--supervise', '--no-browser'],
            cwd=str(self.root), creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._started_at = time.monotonic()
        return '正在后台启动；连接成功后即可打开完整控制台。'

    def open_panel(self):
        if not self.snapshot()['online']:
            raise RuntimeError('面板尚未连接，请先启动并等待就绪。')
        webbrowser.open('http://127.0.0.1:%d/' % self.port())
        return '已在默认浏览器打开完整控制台。'

    def set_osd(self, enabled):
        result = self.request('/api/desktop', {'osd': enabled})
        if result.get('ok') is not True:
            raise RuntimeError(result.get('detail') or '服务未确认设置，请核对状态后重试。')
        return result.get('detail') or 'Umi 屏幕提示已%s，不影响 Creator Center 的提示。' % ('开启' if enabled else '关闭')

    def preview_osd(self):
        result = self.request('/api/desktop/preview', {})
        if result.get('ok') is not True:
            raise RuntimeError(result.get('detail') or '服务未确认预览，请稍后重试。')
        return '已请求样式预览；没有切换性能或发送硬件命令。'

    def toggle_autostart(self, enabled):
        set_autostart(enabled, str(self.root))
        return '已%s登录后静默启动；没有停止当前服务。' % ('开启' if enabled else '关闭')

    def open_folder(self, kind):
        if kind not in ('data', 'scripts'):
            raise ValueError('不支持的目录')
        folder = self.root / kind
        folder.mkdir(exist_ok=True)
        os.startfile(str(folder))
        return '已打开本机目录；配置和日志可能含隐私，请勿整包公开。'

    def runtime_info(self):
        runtime = self.root / 'runtime'
        python = (runtime / 'python.exe').is_file()
        panel = (runtime / 'UmiPanel.exe').is_file()
        anchor = (runtime / 'OcTool.exe').is_file()
        return {'ready': python and panel and anchor,
                'python': python, 'panel_alias': panel, 'anchor_alias': anchor}

    def install_runtime(self):
        info = self.runtime_info()
        if info['ready']:
            return '项目 runtime 目录已就绪，无需安装。'
        candidates = [self.root / 'runtime' / 'python.exe', Path(sys.executable)]
        interpreter = next((path for path in candidates if path.is_file()), None)
        if interpreter is None:
            raise RuntimeError('找不到可用的 Python；请先在项目 runtime 目录准备运行环境')
        try:
            process = subprocess.run(
                [str(interpreter), str(self.root / 'tools' / 'setup_runtime.py')],
                cwd=str(self.root), creationflags=NO_WINDOW,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', timeout=900)
        except subprocess.TimeoutExpired:
            raise RuntimeError('运行环境安装超时；请检查网络后重试') from None
        if process.returncode:
            output = ((process.stdout or '') + (process.stderr or '')).strip()
            raise RuntimeError('运行环境安装失败：%s' % (output[-600:] or '没有输出'))
        if not self.runtime_info()['ready']:
            raise RuntimeError('安装命令已结束，但 runtime 目录仍不完整；请查看日志')
        return '运行环境已安装到项目 runtime 目录，没有请求管理员权限。'

    def stop_service(self):
        if not self.snapshot()['online']:
            raise RuntimeError('面板尚未连接，无需停止。')
        result = self.request('/api/shutdown', {})
        if result.get('ok') is not True:
            raise RuntimeError(result.get('detail') or '服务未确认停止请求')
        return '已请求停止面板；电源设置会按配置还原，稍后可用启动按钮重新运行。'

    def run_debug_script(self, key):
        script = next((item for item in DEBUG_SCRIPTS if item[0] == key), None)
        if script is None:
            raise ValueError('不支持的调试脚本')
        path = self.root / 'scripts' / script[2]
        if not path.is_file():
            raise RuntimeError('缺少脚本：scripts\\%s' % script[2])
        cmd = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cmd.exe'
        subprocess.Popen([str(cmd), '/d', '/c', 'call', str(path)], cwd=str(self.root),
                         creationflags=NEW_CONSOLE)
        return '已打开“%s”的可见窗口；请按窗口提示操作。' % script[1]
