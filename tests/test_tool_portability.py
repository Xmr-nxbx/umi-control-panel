# -*- coding: utf-8 -*-
"""路径/端口回归：AST 隔离硬件依赖，文件、网络、进程全部 mock。

只编译被测源码的定义/常量/安全导入，不执行工具入口或导入硬件通道。
运行：python tests/test_tool_portability.py
"""
import ast
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, MagicMock, mock_open, patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from app import paths
from app.config import DEFAULTS


def forbidden(*args, **kwargs):
    raise AssertionError('unexpected real I/O or hardware dependency')


class FakeConfig:
    def __init__(self, port=9102, mqtt=None):
        self.data = {'server': {'port': port}, 'hardware': {'mqtt': mqtt or {}}}

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def isolated_module(relative):
    filename = os.path.join(REPO, *relative.split('/'))
    with open(filename, encoding='utf-8-sig') as fh:
        tree = ast.parse(fh.read(), filename)
    compile(tree, filename, 'exec')  # 完整源码只做语法验证，不执行。
    body = []
    safe_imports = {'argparse', 'ctypes', 'json', 'os', 'random', 're', 'socket',
                    'struct', 'subprocess', 'sys', 'threading', 'time', 'urllib', 'zipfile'}
    for node in tree.body:
        if isinstance(node, ast.Import):
            if all(a.name.split('.')[0] in safe_imports for a in node.names):
                body.append(node)
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'app.paths':
                body.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign)):
            body.append(node)
    module = types.ModuleType(relative)
    module.__file__ = filename
    module.Config = types.SimpleNamespace(load=Mock(side_effect=forbidden))
    module.FAN_TABLE_BASE = {'CPU': 0x0F00, 'GPU': 0x0F30}
    module.fanrules = Mock()
    for name in ('Log', 'EcChannel', 'Thermal', 'ClockSense', 'GpuSense', 'Bench',
                 'MqttChannel', 'MqttClient', 'power_status', '_Monitor'):
        setattr(module, name, Mock(side_effect=forbidden))
    module.dotnet_probe = types.SimpleNamespace(run=Mock(side_effect=forbidden))
    exec(compile(ast.Module(body=body, type_ignores=[]), filename, 'exec'), module.__dict__)
    return module


class SafeTest(unittest.TestCase):
    def setUp(self):
        # Each test must replace these guards explicitly to simulate the boundary.
        for target in ('builtins.open', 'socket.create_connection', 'socket.socket',
                       'urllib.request.urlopen', 'urllib.request.build_opener',
                       'subprocess.run', 'subprocess.Popen', 'os.makedirs',
                       'os.replace', 'os.remove', 'threading.Thread.start'):
            self.enterContext(patch(target, side_effect=forbidden))
        self.enterContext(patch('sys.stdout', new=io.StringIO()))
        self.enterContext(patch('sys.stderr', new=io.StringIO()))


class PortDiscoveryTests(SafeTest):
    def test_explicit_wins_without_any_io(self):
        for value in (1, 65535, '9101'):
            self.assertEqual(paths.discover_panel_port(value), int(value))

    def test_invalid_explicit_never_falls_back(self):
        for value in (0, 65536, -1, True, 9101.0, '', 'bad', '1.5', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                paths.discover_panel_port(value)

    def test_port_file_wins_over_loaded_configuration(self):
        with patch('builtins.open', mock_open(read_data='9201\n')) as opened:
            self.assertEqual(paths.discover_panel_port(cfg=FakeConfig(9102)), 9201)
        opened.assert_called_once_with(os.path.join(paths.DATA_DIR, 'port'), encoding='utf-8-sig')

    def test_missing_port_uses_loaded_config(self):
        with patch('builtins.open', side_effect=FileNotFoundError) as opened:
            self.assertEqual(paths.discover_panel_port(cfg=FakeConfig('9202')), 9202)
        self.assertEqual(opened.call_count, 1)

    def test_missing_port_reads_only_config_not_identity(self):
        opened = mock_open(read_data=json.dumps({'server': {'port': 9303}}))

        def read(path, *args, **kwargs):
            if path == os.path.join(paths.DATA_DIR, 'port'):
                raise FileNotFoundError
            self.assertEqual(path, os.path.join(paths.DATA_DIR, 'config.json'))
            return opened(path, *args, **kwargs)

        with patch('builtins.open', side_effect=read):
            self.assertEqual(paths.discover_panel_port(), 9303)
        opened.assert_called_once_with(os.path.join(paths.DATA_DIR, 'config.json'), encoding='utf-8-sig')

    def test_missing_files_use_project_default_without_writes(self):
        with patch('builtins.open', side_effect=FileNotFoundError):
            self.assertEqual(paths.discover_panel_port(), DEFAULTS['server']['port'])

    def test_corrupt_or_unreadable_port_does_not_fall_back(self):
        for value in ('', 'bad', '0', '65536', '1.5', '9501' + ' ' * 28 + 'junk'):
            with self.subTest(value=value), patch('builtins.open', mock_open(read_data=value)):
                with self.assertRaises(ValueError):
                    paths.discover_panel_port(cfg=FakeConfig())
        for error in (PermissionError, IsADirectoryError, OSError):
            with patch('builtins.open', side_effect=error), self.assertRaises(OSError):
                paths.discover_panel_port(cfg=FakeConfig())

    def test_corrupt_config_is_not_defaulted(self):
        for value in ('{', '[]', 'null', '{"server":null}', '{"server":{"port":0}}'):
            opened = mock_open(read_data=value)
            with self.subTest(value=value), patch('builtins.open', side_effect=[
                    FileNotFoundError(), opened.return_value]):
                with self.assertRaises(ValueError):
                    paths.discover_panel_port()
        with patch('builtins.open', side_effect=[FileNotFoundError(), PermissionError()]):
            with self.assertRaises(PermissionError):
                paths.discover_panel_port()

    def test_missing_server_port_uses_default(self):
        opened = mock_open(read_data='{}')
        with patch('builtins.open', side_effect=[FileNotFoundError(), opened.return_value]):
            self.assertEqual(paths.discover_panel_port(), DEFAULTS['server']['port'])

    def test_local_mqtt_config_validation(self):
        for host in ('127.0.0.1', 'localhost', '::1'):
            self.assertEqual(paths.local_mqtt_endpoint({'host': host, 'port': '14001'}), (host, 14001))
        self.assertEqual(paths.local_mqtt_endpoint({}), ('127.0.0.1', 13688))
        for host in ('example.org', '192.168.1.2', '0.0.0.0', '', None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                paths.local_mqtt_endpoint({'host': host})
        for port in (0, 65536, None, True, 1.2, 'bad'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                paths.local_mqtt_endpoint({'port': port})


# Read only source files, before installing the I/O guards. No tool is imported/executed.
TOOLS = {name: isolated_module('tools/' + name + '.py') for name in (
    'fan_table_test', 'pl_detail_test', 'kb_power_test', 'ec_mode_bench',
    'ask_latency_probe', 'fan_curve_mqtt_write_test', 'fan_curve_mqtt_probe',
    'selfcheck', 'lighting_effect_test', 'tcc_fan_speed_test', 'gen_ec_map')}
BENCH = isolated_module('app/bench.py')
GUARDS = ('fan_table_test', 'pl_detail_test', 'kb_power_test', 'ec_mode_bench')


def guard_args(name):
    return ['--PL1=60'] if name == 'pl_detail_test' else []


class PanelGuardTests(SafeTest):
    def guard_context(self, module):
        stack = contextlib.ExitStack()
        if hasattr(module, 'Report'):
            stack.enter_context(patch.object(module, 'Report', return_value=Mock()))
        return stack

    def test_guards_use_recorded_port_and_close_connection(self):
        for name in GUARDS:
            module = TOOLS[name]
            with self.subTest(tool=name), self.guard_context(module):
                with patch('builtins.open', mock_open(read_data='9501')), \
                        patch('socket.create_connection', return_value=MagicMock()) as connect:
                    self.assertNotEqual(module.main(guard_args(name)), 0)
                    self.assertEqual(connect.call_args.args[0], ('127.0.0.1', 9501))
                    self.assertTrue(connect.return_value.close.called or
                                    connect.return_value.__exit__.called)

    def test_guards_use_config_port_when_record_missing(self):
        for name in GUARDS:
            module = TOOLS[name]
            opened = mock_open(read_data='{"server":{"port":9502}}')
            with self.subTest(tool=name), self.guard_context(module), \
                    patch('builtins.open', side_effect=[FileNotFoundError(), opened.return_value]), \
                    patch('socket.create_connection', return_value=MagicMock()) as connect:
                self.assertNotEqual(module.main(guard_args(name)), 0)
                self.assertEqual(connect.call_args.args[0], ('127.0.0.1', 9502))

    def test_invalid_discovery_never_reaches_socket_or_hardware(self):
        for name in GUARDS:
            module = TOOLS[name]
            with self.subTest(tool=name), self.guard_context(module), \
                    patch('builtins.open', mock_open(read_data='broken')):
                self.assertNotEqual(module.main(guard_args(name)), 0)

    def test_probe_uncertainty_is_refused(self):
        for name in GUARDS:
            module = TOOLS[name]
            for error in (TimeoutError, PermissionError, OSError):
                with self.subTest(tool=name, error=error), self.guard_context(module), \
                        patch('builtins.open', mock_open(read_data='9501')), \
                        patch('socket.create_connection', side_effect=error):
                    self.assertNotEqual(module.main(guard_args(name)), 0)

    def test_config_load_failure_after_refused_connection_is_blocked(self):
        for name in GUARDS:
            module = TOOLS[name]
            with self.subTest(tool=name), self.guard_context(module), \
                    patch('builtins.open', mock_open(read_data='9501')), \
                    patch('socket.create_connection', side_effect=ConnectionRefusedError), \
                    patch.object(module.Config, 'load', return_value=(FakeConfig(), 'mock parse error', None)):
                self.assertNotEqual(module.main(guard_args(name)), 0)

    def test_only_connection_refused_means_not_running(self):
        for name in GUARDS:
            module = TOOLS[name]
            fn = module._port_open if name == 'ec_mode_bench' else module.panel_alive
            with self.subTest(tool=name), patch('socket.create_connection', side_effect=ConnectionRefusedError):
                self.assertFalse(fn(9501))


class HttpToolTests(SafeTest):
    def test_http_tools_follow_priority(self):
        for name in ('ask_latency_probe', 'selfcheck', 'fan_curve_mqtt_write_test', 'fan_curve_mqtt_probe'):
            module = TOOLS[name]
            for source, argv, expected in (
                    ('explicit', ['--port', '9601'], 9601),
                    ('record', [], 9602), ('config', [], 9603)):
                with self.subTest(tool=name, source=source), contextlib.ExitStack() as stack:
                    if source == 'record':
                        stack.enter_context(patch('builtins.open', mock_open(read_data='9602')))
                    elif source == 'config':
                        opened = mock_open(read_data='{"server":{"port":9603}}')
                        stack.enter_context(patch('builtins.open', side_effect=[
                            FileNotFoundError(), opened.return_value]))
                    cfg = FakeConfig(9603, {'client_id': 'mock-id', 'username': 'mock-user',
                                           'password': 'mock-secret', 'host': 'localhost', 'port': 14001})
                    stack.enter_context(patch.object(module, 'Config', types.SimpleNamespace(
                        load=Mock(return_value=(cfg, None, None))), create=True))
                    expected_base = 'http://127.0.0.1:%d' % expected
                    if name == 'ask_latency_probe':
                        state = stack.enter_context(patch.object(module, 'kb_state', return_value=None))
                        self.assertEqual(module.main(argv), 1)
                        self.assertEqual(module.BASE, expected_base)
                        state.assert_called_once_with()
                    elif name == 'selfcheck':
                        get = stack.enter_context(patch.object(module, 'get', return_value={}))
                        self.assertEqual(module.main(argv), 0)
                        self.assertEqual([c.args[0] for c in get.call_args_list], [expected] * 4)
                    elif name == 'fan_curve_mqtt_write_test':
                        stack.enter_context(patch.object(module, '_http_json', return_value={'ok': False}))
                        self.assertEqual(module.main(argv), 3)
                        self.assertEqual(module.PANEL, expected_base)
                    else:
                        ch = Mock(client_id='mock-id', username='mock-user', port=14001)
                        ch.fan_curve.return_value = {'ok': True, 'tables': {}}
                        stack.enter_context(patch.object(module, 'MqttChannel', return_value=ch))
                        stack.enter_context(patch.object(module, 'Log', return_value=Mock()))
                        stack.enter_context(patch.object(module, '_wait_alive', return_value=True))
                        ec = stack.enter_context(patch.object(module, '_ec_curve', return_value={'ok': False}))
                        self.assertEqual(module.main(argv), 0)
                        ec.assert_called_once_with(expected_base)

    def test_invalid_explicit_ports_do_not_issue_requests(self):
        for name in ('ask_latency_probe', 'selfcheck', 'fan_curve_mqtt_write_test', 'fan_curve_mqtt_probe'):
            module = TOOLS[name]
            cfg = FakeConfig(mqtt={'client_id': 'mock', 'username': 'mock', 'password': 'mock'})
            with self.subTest(tool=name), patch.object(module, 'Config', types.SimpleNamespace(
                    load=Mock(return_value=(cfg, None, None))), create=True):
                self.assertEqual(module.main(['--port', '0']), 2)

    def test_http_urls_and_origin_use_discovered_base(self):
        module = TOOLS['ask_latency_probe']
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with patch.object(module, 'BASE', 'http://127.0.0.1:9605'), \
                patch('urllib.request.urlopen', return_value=response) as urlopen:
            module.get('/api/state')
            module.post('/api/action', {})
        for request in urlopen.call_args_list:
            self.assertTrue(request.args[0].full_url.startswith('http://127.0.0.1:9605/'))
            self.assertEqual(request.args[0].get_header('Origin'), 'http://127.0.0.1:9605')

    def test_no_ec_probe_does_not_discover_panel(self):
        module = TOOLS['fan_curve_mqtt_probe']
        cfg = FakeConfig()
        ch = Mock(client_id='mock', username='mock', port=14001)
        ch.fan_curve.return_value = {'ok': True, 'tables': {}}
        with patch.object(module.Config, 'load', return_value=(cfg, None, None)), \
                patch.object(module, 'MqttChannel', return_value=ch), \
                patch.object(module, 'Log', return_value=Mock()), \
                patch.object(module, '_wait_alive', return_value=True), \
                patch.object(module, 'discover_panel_port', side_effect=forbidden):
            self.assertEqual(module.main(['--no-ec']), 0)


class MqttToolTests(SafeTest):
    def cfg(self, host='localhost', port=14007):
        return FakeConfig(mqtt={'host': host, 'port': port, 'client_id': 'mock-id',
                                'username': 'mock-user', 'password': 'mock-secret'})

    def test_loaded_host_and_port_reach_each_client_constructor(self):
        for name in ('lighting_effect_test', 'fan_curve_mqtt_write_test', 'tcc_fan_speed_test'):
            module = TOOLS[name]
            cfg = self.cfg()
            with self.subTest(tool=name), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(module.Config, 'load', return_value=(cfg, None, None)))
                client = stack.enter_context(patch.object(module, 'MqttClient', return_value=MagicMock()))
                stack.enter_context(patch('time.sleep', return_value=None))
                if name == 'lighting_effect_test':
                    ident, err = module._load_identity()
                    self.assertIsNone(err)
                    module._connect(ident, [])
                elif name == 'fan_curve_mqtt_write_test':
                    stack.enter_context(patch.object(module, '_http_json', return_value={'ok': True}))
                    read = stack.enter_context(patch.object(module, '_mqtt_read', return_value=(None, None)))
                    self.assertEqual(module.main(['--port', '9601']), 3)
                    ident = read.call_args.args[0]
                    module._mqtt_read_once(ident, wait_s=0)
                    module._mqtt_send(ident, {'Action': 'MOCK'})
                else:
                    roundtrip = stack.enter_context(patch.object(module, '_roundtrip', return_value={}))
                    self.assertEqual(module.main([]), 3)
                    ident = roundtrip.call_args.args[0]
                    # Call original definition with only a synthetic Fan/Status packet.
                    original = self.original_roundtrip
                    topic = b'Fan/Status'
                    body = len(topic).to_bytes(2, 'big') + topic + b'{"mock":true}'
                    client.return_value.read_packet.return_value = (0x30, body)
                    self.assertEqual(original(ident, []), {'mock': True})
                self.assertGreater(client.call_count, 0)
                for args in client.call_args_list:
                    self.assertEqual(args.args[:2], ('localhost', 14007))

    original_roundtrip = staticmethod(TOOLS['tcc_fan_speed_test']._roundtrip)

    def test_remote_or_invalid_mqtt_endpoints_never_connect(self):
        for name in ('lighting_effect_test', 'fan_curve_mqtt_write_test', 'tcc_fan_speed_test'):
            module = TOOLS[name]
            for cfg in (self.cfg(host='192.0.2.1'), self.cfg(port=0)):
                with self.subTest(tool=name), patch.object(module.Config, 'load', return_value=(cfg, None, None)):
                    if name == 'lighting_effect_test':
                        ident, err = module._load_identity()
                        self.assertIsNone(ident)
                        self.assertTrue(err)
                    else:
                        args = ['--port', '9601'] if name == 'fan_curve_mqtt_write_test' else []
                        self.assertEqual(module.main(args), 2)

    def test_capture_arguments_are_required_before_config_or_io(self):
        module = TOOLS['lighting_effect_test']
        for args in ([], ['--restore-capture', 'mock.jsonl'], ['--test-capture', 'mock.jsonl']):
            with self.subTest(args=args), self.assertRaises(SystemExit) as result:
                module.main(args)
            self.assertEqual(result.exception.code, 2)
        self.assertFalse(hasattr(module, 'RESTORE_CAPTURE'))
        self.assertFalse(hasattr(module, 'TEST_CAPTURE'))

    def test_explicit_capture_paths_are_used_without_autoselection(self):
        module = TOOLS['lighting_effect_test']
        restore, test = os.path.join(REPO, 'mock-restore.jsonl'), os.path.join(REPO, 'mock-test.jsonl')
        with patch.object(module, '_load_identity', return_value=({}, None)), \
                patch.object(module, 'load_restore', return_value=(None, None)) as load_restore, \
                patch.object(module, 'load_effect', return_value=None) as load_effect:
            self.assertEqual(module.main(['--restore-capture', restore, '--test-capture', test]), 2)
        load_restore.assert_called_once_with(restore)
        load_effect.assert_called_once_with(test, 'Wave')


class RuntimePathTests(SafeTest):
    def test_oem_candidates_use_environment_and_explicit_path_first(self):
        module = TOOLS['gen_ec_map']
        env = {'ProgramW6432': 'D:/Program Files', 'ProgramFiles': 'E:/Apps',
               'ProgramFiles(x86)': 'F:/Apps (x86)'}
        with patch.dict(os.environ, env, clear=True):
            candidates = module.gcu_candidates()
            self.assertEqual(len(candidates), 3)
            for candidate, root in zip(candidates, env.values()):
                self.assertEqual(candidate, os.path.join(root, 'OEM', 'CreatorCenter',
                                                         'UniwillService', 'MyControlCenter', 'GCUService.exe'))
            with patch('os.path.exists', return_value=True) as exists:
                self.assertEqual(module.find_gcu('G:/OEM/GCUService.exe'), 'G:/OEM/GCUService.exe')
                exists.assert_called_once_with('G:/OEM/GCUService.exe')
            with patch('os.path.exists', side_effect=lambda p: p == candidates[-1]):
                self.assertEqual(module.find_gcu(), candidates[-1])
        with patch.dict(os.environ, {}, clear=True), patch('os.path.exists', side_effect=forbidden):
            self.assertEqual(module.gcu_candidates(), [])
            self.assertIsNone(module.find_gcu())

    def test_duplicate_programfiles_roots_are_not_repeated(self):
        module = TOOLS['gen_ec_map']
        with patch.dict(os.environ, {'ProgramW6432': 'D:/Apps', 'ProgramFiles': 'D:/Apps'}, clear=True):
            self.assertEqual(len(module.gcu_candidates()), 1)

    def test_coremark_uses_current_interpreter_without_building(self):
        proc = types.SimpleNamespace(returncode=0, stdout='mock built', stderr='')
        with patch('os.path.exists', side_effect=[False, True, True]), \
                patch('subprocess.run', return_value=proc) as run, \
                patch.object(sys, 'executable', 'D:/Portable Python/python.exe'):
            self.assertTrue(BENCH.build_coremark()[0])
        self.assertEqual(run.call_args.args[0], ['D:/Portable Python/python.exe', BENCH.COREMARK_BUILD])

    def test_coremark_existing_or_missing_script_never_spawns(self):
        with patch('os.path.exists', return_value=True):
            self.assertTrue(BENCH.build_coremark()[0])
        with patch('os.path.exists', return_value=False):
            self.assertFalse(BENCH.build_coremark()[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
