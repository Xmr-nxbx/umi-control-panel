# -*- coding: utf-8 -*-
"""停止工具的纯 mock 测试：不连接服务、不读取本机配置、不创建真实互斥对象。"""
import ast
import importlib.util
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('mocked_stop_panel', ROOT / 'tools' / 'stop_panel.py')
STOP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STOP)


class StopPanelTest(unittest.TestCase):
    def setUp(self):
        self.guards = []
        for target in ('builtins.open', 'io.open', 'socket.socket', 'subprocess.Popen'):
            guard = self.enterContext(patch(target, side_effect=AssertionError('禁止真实 IO：' + target)))
            self.guards.append(guard)
        self.factory = self.enterContext(patch.object(STOP, '_new_instance'))
        self.port = self.enterContext(patch.object(STOP, 'discover_panel_port', return_value=9321))
        self.owned = self.enterContext(patch.object(STOP, '_service_owned', return_value=True))
        self.http = self.enterContext(patch.object(STOP, 'HTTPConnection'))
        self.connection = self.http.return_value
        self.response = self.connection.getresponse.return_value.__enter__.return_value
        self.response.status = 200
        self.output = self.enterContext(patch('sys.stdout', new_callable=io.StringIO))
        self.now = 0.0
        self.enterContext(patch.object(STOP.time, 'monotonic', side_effect=lambda: self.now))
        self.sleep = self.enterContext(patch.object(STOP.time, 'sleep', side_effect=self.advance))
        self.enterContext(patch.object(STOP, 'STOP_TIMEOUT', 2.0))
        self.instances = []
        self.states = []
        self.factory.side_effect = self.instance

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()
        for inst in self.instances:
            inst.release.assert_called_once_with()

    def advance(self, seconds):
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, STOP.POLL_INTERVAL)
        self.assertTrue(all(inst.release.called for inst in self.instances))
        self.now += seconds

    def instance(self):
        state = self.states.pop(0) if self.states else False
        inst = Mock()
        if isinstance(state, Exception):
            inst.acquire.side_effect = state
        else:
            inst.acquire.return_value = state
        self.instances.append(inst)
        return inst

    def test_no_instance_succeeds_without_port_read_or_http(self):
        self.states = [True]
        self.assertEqual(STOP.main([]), 0)
        self.port.assert_not_called()
        self.http.assert_not_called()
        self.sleep.assert_not_called()
        self.assertEqual(len(self.instances), 1)

    def test_shutdown_waits_for_named_instance_and_releases_every_probe(self):
        self.states = [False, False, False, True]
        self.assertEqual(STOP.main([]), 0)
        self.port.assert_called_once_with()
        self.http.assert_called_once_with('127.0.0.1', 9321, timeout=2.0)
        self.connection.request.assert_called_once_with(
            'POST', '/api/shutdown', body=b'{}',
            headers={'Content-Type': 'application/json', 'Origin': 'http://127.0.0.1:9321'})
        self.connection.close.assert_called_once_with()
        self.connection.getresponse.return_value.__exit__.assert_called_once()
        self.response.read.assert_not_called()
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(len(self.instances), 4)
        self.assertIn('已确认面板实例退出', self.output.getvalue())

    def test_http_accepted_but_instance_on_any_port_persists_is_failure(self):
        self.assertEqual(STOP.main([]), 1)
        self.assertEqual(self.now, 2.0)
        self.assertEqual(self.sleep.call_count, 8)
        self.connection.request.assert_called_once()
        self.assertIn('无法确认', self.output.getvalue())

    def test_failed_http_never_means_stopped_while_mutex_exists(self):
        for error in (OSError('connection refused'), TimeoutError('timeout')):
            with self.subTest(error=error):
                self.connection.request.side_effect = error
                self.assertEqual(STOP.main([]), 1)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_lost_http_reply_can_succeed_only_after_instance_disappears(self):
        self.states = [False, False, True]
        self.connection.getresponse.side_effect = OSError('reply lost')
        self.assertEqual(STOP.main([]), 0)
        self.assertEqual(len(self.instances), 3)
        self.sleep.assert_called_once_with(STOP.POLL_INTERVAL)
        self.connection.close.assert_called_once_with()

    def test_redirect_or_error_response_is_not_followed_or_trusted(self):
        for status in (301, 302, 307, 308, 403, 500):
            with self.subTest(status=status):
                self.response.status = status
                self.assertEqual(STOP.main([]), 1)
        self.assertEqual(self.connection.request.call_count, 6)
        self.assertTrue(all(call.args[:2] == ('POST', '/api/shutdown')
                            for call in self.connection.request.call_args_list))
        self.response.getheader.assert_not_called()
        self.assertIn('HTTP 403', self.output.getvalue())

    def test_foreign_or_unverified_service_is_never_stopped(self):
        self.owned.side_effect = OSError('该端口上的服务不属于当前项目目录，已拒绝停止')
        self.assertEqual(STOP.main([]), 1)
        self.connection.request.assert_not_called()
        self.assertIn('不属于当前项目目录', self.output.getvalue())

    def test_invalid_or_unreadable_port_aborts_without_http(self):
        for error in (ValueError('端口损坏'), PermissionError('端口不可读')):
            with self.subTest(error=error):
                self.port.side_effect = error
                self.assertEqual(STOP.main([]), 1)
        self.http.assert_not_called()
        self.sleep.assert_not_called()

    def test_mutex_detection_errors_fail_closed_and_release(self):
        self.states = [OSError('mutex unavailable')]
        self.assertEqual(STOP.main([]), 1)
        self.port.assert_not_called()
        self.http.assert_not_called()
        self.assertEqual(len(self.instances), 1)

    def test_detection_error_after_http_is_still_failure(self):
        self.states = [False, OSError('probe unavailable')]
        self.assertEqual(STOP.main([]), 1)
        self.connection.request.assert_called_once()
        self.assertEqual(len(self.instances), 2)

    def test_release_error_cannot_be_reported_as_stopped(self):
        inst = Mock()
        inst.acquire.return_value = True
        inst.release.side_effect = OSError('release failed')
        self.factory.side_effect = None
        self.factory.return_value = inst
        self.assertEqual(STOP.main([]), 1)
        inst.release.assert_called_once_with()
        self.http.assert_not_called()

    def test_active_detection_drops_handle_before_sending_shutdown(self):
        self.states = [False, True]
        def request(*args, **kwargs):
            self.assertEqual(len(self.instances), 1)
            self.instances[0].release.assert_called_once_with()
        self.connection.request.side_effect = request
        self.assertEqual(STOP.main([]), 0)

    def test_unknown_arguments_do_not_touch_any_instance(self):
        for args in (['--force'], ['--port', '9321'], ['& calc']):
            self.assertEqual(STOP.main(args), 2)
        self.factory.assert_not_called()
        self.port.assert_not_called()
        self.http.assert_not_called()


class StopPanelContractTest(unittest.TestCase):
    def test_factory_uses_panel_name_without_real_singleton_import(self):
        fake = types.ModuleType('app.singleton')
        fake.SingleInstance = Mock()
        with patch.dict(sys.modules, {'app.singleton': fake}):
            self.assertIs(STOP._new_instance(), fake.SingleInstance.return_value)
        fake.SingleInstance.assert_called_once_with('UmiControlPanel')

    def test_helper_imports_no_daemon_cli_or_process_control(self):
        source = (ROOT / 'tools' / 'stop_panel.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertEqual(set(imports), {'http.client', 'json', 'os', 'sys', 'time',
                                        'app.paths', 'app.singleton'})
        for forbidden in ('taskkill', 'terminate', 'kill(', 'Daemon', 'Config.load', 'urlopen',
                          'subprocess', 'mqtt', 'winreg', 'netstat'):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count('/api/'), 2)
        self.assertIn("'/api/state'", source)
        self.assertIn("'/api/shutdown'", source)


class PortDiscoveryMockTest(unittest.TestCase):
    def setUp(self):
        from app import paths
        self.paths = paths
        self.enterContext(patch.object(paths, 'DATA_DIR', r'C:\mock 中文 & folder\data'))
        self.files = {}
        self.open = self.enterContext(patch.object(paths, 'open', create=True, side_effect=self.read))
        self.mkdir = self.enterContext(patch.object(paths.os, 'makedirs', side_effect=AssertionError('禁止创建目录')))

    def tearDown(self):
        self.mkdir.assert_not_called()

    def read(self, path, **kwargs):
        self.assertEqual(os.path.dirname(path), self.paths.DATA_DIR)
        self.assertEqual(kwargs, {'encoding': 'utf-8-sig'})
        name = os.path.basename(path)
        self.assertIn(name, ('port', 'config.json'))
        value = self.files.get(name, FileNotFoundError())
        if isinstance(value, Exception):
            raise value
        return io.StringIO(value)

    def test_recorded_non_default_port_is_preferred_to_config(self):
        self.files = {'port': ' 19321\n', 'config.json': 'not needed'}
        self.assertEqual(STOP.discover_panel_port(), 19321)
        self.assertEqual(self.open.call_count, 1)

    def test_missing_port_uses_only_mocked_server_config(self):
        self.files = {'config.json': '{"server": {"port": 19001}}'}
        self.assertEqual(STOP.discover_panel_port(), 19001)
        self.assertEqual(self.open.call_count, 2)

    def test_missing_port_uses_supplied_config_without_loading_files(self):
        cfg = Mock()
        cfg.get.return_value = 19002
        self.assertEqual(STOP.discover_panel_port(cfg=cfg), 19002)
        cfg.get.assert_called_once_with('server', 'port', default=8747)
        self.assertEqual(self.open.call_count, 1)

    def test_missing_both_uses_default_port(self):
        self.assertEqual(STOP.discover_panel_port(), 8747)

    def test_bad_port_is_not_silently_replaced(self):
        for value in ('', 'no port', '0', '65536', '8747.5', PermissionError('unreadable')):
            with self.subTest(value=value):
                self.files = {'port': value}
                self.open.reset_mock()
                with self.assertRaises((ValueError, OSError)):
                    STOP.discover_panel_port()
                self.assertEqual(self.open.call_count, 1)

    def test_bad_config_is_not_silently_replaced(self):
        for value in ('{', '[]', '{"server": []}', '{"server": {"port": true}}',
                      '{"server": {"port": 0}}', '{"server": {"port": 8747.5}}',
                      PermissionError('unreadable')):
            with self.subTest(value=value):
                self.files = {'config.json': value}
                with self.assertRaises((ValueError, OSError)):
                    STOP.discover_panel_port()


if __name__ == '__main__':
    unittest.main(verbosity=2)
