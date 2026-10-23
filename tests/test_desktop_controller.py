import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.desktop.controller import DesktopController, autostart_command, set_autostart, autostart_status
from app.logx import Log


class DesktopControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='umi 空间 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.controller = DesktopController(self.root)

    def test_port_is_project_relative_and_bounded(self):
        port = self.root / 'data' / 'port'
        for text, expected in [('8799', 8799), ('0', 8747), ('65536', 8747), ('bad', 8747)]:
            port.write_text(text)
            self.assertEqual(self.controller.port(), expected)

    def test_no_system_proxy_for_local_api(self):
        from urllib.request import ProxyHandler
        self.assertFalse(any(isinstance(h, ProxyHandler) for h in self.controller._http.handlers))

    def test_osd_only_calls_preference_endpoint(self):
        with patch.object(self.controller, 'request', return_value={'ok': True}) as request:
            self.controller.set_osd(False)
            request.assert_called_once_with('/api/desktop', {'osd': False})

    def test_preview_never_changes_mode(self):
        with patch.object(self.controller, 'request', return_value={'ok': True}) as request:
            self.controller.preview_osd()
            request.assert_called_once_with('/api/desktop/preview', {})

    def test_start_hidden_absolute_paths(self):
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime' / 'UmiPanel.exe').touch()
        with patch.object(self.controller, 'snapshot', return_value={'online': False}), \
                patch('app.desktop.controller.subprocess.Popen') as popen:
            self.controller.start()
        args, kw = popen.call_args
        self.assertEqual(args[0], [str(self.root / 'runtime' / 'UmiPanel.exe'),
                                  str(self.root / 'main.py'), '--supervise', '--no-browser'])
        self.assertEqual(kw['creationflags'], 0x08000000)
        self.assertEqual(kw['cwd'], str(self.root))
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['stderr'], subprocess.DEVNULL)
        self.assertNotIn('shell', kw)

    def test_no_duplicate_start(self):
        with patch.object(self.controller, 'snapshot', return_value={'online': True}), \
                patch('app.desktop.controller.subprocess.Popen') as popen:
            self.controller.start()
            popen.assert_not_called()

    def test_no_repeat_pending_start(self):
        self.controller._starting = MagicMock()
        self.controller._starting.poll.return_value = None
        with patch.object(self.controller, 'snapshot', return_value={'online': False}), \
                patch('app.desktop.controller.subprocess.Popen') as popen:
            self.controller.start()
            popen.assert_not_called()

    def test_missing_runtime_does_not_download(self):
        with patch.object(self.controller, 'snapshot', return_value={'online': False}), \
                patch('app.desktop.controller.subprocess.Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, '运行时'):
                self.controller.start()
            popen.assert_not_called()

    def test_snapshot_does_not_open_config_or_connect_hardware(self):
        state = {'meta': {'version': 'test'}, 'tier': 'eco', 'tier_label': '省电', 'tick_age_s': 1}
        with patch('app.desktop.controller.autostart_status', return_value={'enabled': False}), \
                patch.object(self.controller, 'request', return_value=state) as request:
            result = self.controller.snapshot()
        self.assertTrue(result['online'])
        self.assertIsNone(result['osd'])
        request.assert_called_once_with('/api/state')

    def test_snapshot_new_backend_reads_osd_only(self):
        state = {'meta': {'version': 'test', 'desktop_preferences': True}, 'tier': 'bal'}
        with patch('app.desktop.controller.autostart_status', return_value={}), \
                patch.object(self.controller, 'request', side_effect=[state, {'osd': False, 'available': True}]) as request:
            result = self.controller.snapshot()
        self.assertFalse(result['osd'])
        self.assertEqual([c.args for c in request.call_args_list], [('/api/state',), ('/api/desktop',)])

    def test_unavailable_osd_can_save_preference_but_not_preview(self):
        state = {'meta': {'version': 'test', 'desktop_preferences': True}}
        with patch('app.desktop.controller.autostart_status', return_value={}), \
                patch.object(self.controller, 'request', side_effect=[state, {'osd': True, 'available': False}]):
            result = self.controller.snapshot()
        self.assertTrue(result['online'])
        self.assertTrue(result['osd'])
        self.assertFalse(result['osd_available'])
        self.assertIn('暂不能预览', result['error'])

    def test_failed_ack_is_not_reported_as_success(self):
        for result in ({}, {'ok': False}, {'ok': False, 'detail': '拒绝'}):
            with patch.object(self.controller, 'request', return_value=result):
                with self.assertRaises(RuntimeError):
                    self.controller.set_osd(False)
                with self.assertRaises(RuntimeError):
                    self.controller.preview_osd()

    def test_nonobject_success_body_rejected(self):
        with patch.object(self.controller._http, 'open') as open_request:
            open_request.return_value.__enter__.return_value.read.return_value = b'[]'
            with self.assertRaisesRegex(ValueError, '响应格式'):
                self.controller.request('/api/desktop')
            self.assertEqual(open_request.call_args.kwargs['timeout'], 5)

    def test_nonobject_http_error_body_has_safe_message(self):
        from urllib.error import HTTPError
        error = HTTPError('http://127.0.0.1:8747/api/desktop', 500, 'Error', {}, io.BytesIO(b'[]'))
        with patch.object(self.controller._http, 'open', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 500'):
                self.controller.request('/api/desktop')

    def test_missing_service_no_autostart_or_hardware(self):
        with patch('app.desktop.controller.autostart_status', return_value={}), \
                patch.object(self.controller, 'request', side_effect=OSError('offline')), \
                patch('app.desktop.controller.subprocess.Popen') as popen:
            self.assertFalse(self.controller.snapshot()['online'])
            popen.assert_not_called()

    def test_open_panel_requires_connection(self):
        with patch.object(self.controller, 'snapshot', return_value={'online': False}), \
                patch('app.desktop.controller.webbrowser.open') as browser:
            with self.assertRaises(RuntimeError):
                self.controller.open_panel()
            browser.assert_not_called()

    def test_autostart_command_quotes_movable_path(self):
        command = autostart_command(str(self.root))
        self.assertIn('"' + str(self.root / 'Umi Control Panel.vbs') + '"', command)
        self.assertTrue(command.endswith('/background'))
        self.assertNotIn('main.py', command)

    def test_autostart_only_writes_own_hkcu_value(self):
        import winreg
        with patch.object(winreg, 'CreateKeyEx') as create, patch.object(winreg, 'SetValueEx') as write:
            set_autostart(True, str(self.root))
            self.assertEqual(create.call_args.args[0], winreg.HKEY_CURRENT_USER)
            self.assertEqual(write.call_args.args[1], 'UmiControlPanel')
            self.assertEqual(write.call_args.args[-1], autostart_command(str(self.root)))
        with patch.object(winreg, 'CreateKeyEx'), patch.object(winreg, 'DeleteValue') as delete:
            set_autostart(False, str(self.root))
            self.assertEqual(delete.call_args.args[1], 'UmiControlPanel')

    def test_legacy_autostart_can_be_detected_without_leaking_command(self):
        import winreg
        with patch.object(winreg, 'OpenKey'), patch.object(winreg, 'QueryValueEx', return_value=('old console command', 1)):
            self.assertEqual(autostart_status(str(self.root)), {'enabled': True, 'current': False})

    def test_log_survives_closed_or_missing_console(self):
        with patch('app.logx.data_path', return_value=str(self.root / 'log.txt')):
            log = Log()
        try:
            with patch('builtins.print', side_effect=OSError('no console')):
                log.info('still written')
            self.assertIn('still written', (self.root / 'log.txt').read_text())
        finally:
            log.close()

    def test_developer_console_has_no_automatic_command(self):
        with patch('app.desktop.controller.subprocess.Popen') as popen:
            self.controller.console()
        args, kw = popen.call_args
        self.assertEqual(args[0][1:], ['/d', '/k'])
        self.assertEqual(kw['creationflags'], 0x10)
        self.assertNotIn('shell', kw)


class NativeLayoutTests(unittest.TestCase):
    def test_drawitemstruct_matches_windows_abi(self):
        import ctypes
        from app.desktop.native import DrawItem
        pointer = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(DrawItem.action.offset, 12)
        self.assertEqual(DrawItem.state.offset, 16)
        self.assertEqual(DrawItem.hwnd.offset, 24 if pointer == 8 else 20)
        self.assertEqual(DrawItem.dc.offset, 32 if pointer == 8 else 24)
        self.assertEqual(ctypes.sizeof(DrawItem), 64 if pointer == 8 else 48)


if __name__ == '__main__':
    unittest.main()
