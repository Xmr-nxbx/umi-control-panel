# -*- coding: utf-8 -*-
"""Desktop bootstrap contract. No GUI, real launch, registry, setup or hardware IO.

On Windows, cscript parses the actual generated VBScript with every external
object/dialog replaced by in-memory fakes. Temporary files contain only this test
harness, never a runtime or application configuration.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('desktop_make_bats', ROOT / 'scripts' / 'make_bats.py')
GEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GEN)
ENTRY = 'Umi Control Panel.vbs'
FAKE_ROOT = r'C:\space 中文 & folder\Umi Control Panel'
CSCRIPT = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cscript.exe'

# No COM objects are created here. In particular, Run/Save only report their inputs.
VBS_FAKES = r'''
Dim Host, runtimeKind, postSetupRuntime, setupExit, dialogAnswer, argValues
Dim factoryFailure, launchFailure, entryPresent, setupPresent
Dim currentFolder
Set Host = New FakeHost
Set Host.Arguments = New FakeArguments

Class FakeArguments
    Public Count
    Public Default Property Get Item(index)
        Item = argValues(index)
    End Property
End Class

Class FakeHost
    Public Arguments, ScriptFullName
    Public Sub Quit(code)
        WScript.Quit code
    End Sub
End Class

Function TestCreateObject(name)
    If factoryFailure Then Err.Raise 70, "fake", "Access denied by test"
    Select Case name
        Case "WScript.Shell": Set TestCreateObject = New FakeShell
        Case "Scripting.FileSystemObject": Set TestCreateObject = New FakeFSO
        Case Else: WScript.Quit 99
    End Select
End Function

Class FakeFSO
    Public Function GetParentFolderName(path)
        GetParentFolderName = Left(path, InStrRev(path, "\") - 1)
    End Function
    Public Function BuildPath(parent, child)
        BuildPath = parent & "\" & child
    End Function
    Public Function GetSpecialFolder(index)
        If index <> 0 Then WScript.Quit 99
        GetSpecialFolder = "C:\Windows"
    End Function
    Public Function FileExists(path)
        Dim name
        name = Mid(path, InStrRev(path, "\") + 1)
        Select Case LCase(name)
            Case "umipanel.exe": FileExists = (runtimeKind = 1)
            Case "python.exe": FileExists = (runtimeKind = 2)
            Case "desktop.py", "main.py", "umi control panel.vbs": FileExists = entryPresent
            Case Else
                If Right(name, 4) <> ".bat" Then WScript.Quit 99
                FileExists = setupPresent
        End Select
    End Function
End Class

Class FakeShell
    Public Property Let CurrentDirectory(value)
        currentFolder = value
        WScript.StdOut.WriteLine "CWD" & vbTab & value
    End Property
    Public Function Run(command, style, wait)
        WScript.StdOut.WriteLine "RUN" & vbTab & command & vbTab & style & vbTab & wait
        If launchFailure Then Err.Raise 70, "fake", "Launch denied by test"
        Run = 0
        If style = 1 Then
            runtimeKind = postSetupRuntime
            Run = setupExit
        End If
    End Function
End Class

Function TestMsgBox(message, buttons, title)
    WScript.StdOut.WriteLine "DIALOG" & vbTab & buttons & vbTab & title & vbTab & Replace(message, vbCrLf, " | ")
    TestMsgBox = dialogAnswer
End Function
'''


def vbs_string(value):
    return '"' + value.replace('"', '""') + '"'


def quote(value):
    return '"' + value + '"'


class GeneratedFilesTest(unittest.TestCase):
    def test_generated_batches_are_current_gbk_crlf(self):
        for name, body in GEN.BATS.items():
            with self.subTest(name=name):
                data = (ROOT / 'scripts' / name).read_bytes()
                self.assertEqual(data, GEN.render_bat(body))
                self.assertEqual(data.decode('gbk').encode('gbk'), data)
                self.assertNotIn(b'\n', data.replace(b'\r\n', b''))
                self.assertNotIn(b'@@', data)
                self.assertFalse(data.startswith(b'\xef\xbb\xbf'))

    def test_vbs_is_generated_utf16le_bom_crlf(self):
        for name, body in GEN.VBS.items():
            with self.subTest(name=name):
                data = (ROOT / name).read_bytes()
                self.assertEqual(data, GEN.render_vbs(body))
                self.assertTrue(data.startswith(b'\xff\xfe'))
                text = data[2:].decode('utf-16-le')
                self.assertEqual(b'\xff\xfe' + text.encode('utf-16-le'), data)
                self.assertNotIn('\ufeff', text)
                self.assertNotIn('\ufffe', text)
                self.assertNotIn('\n', text.replace('\r\n', ''))
                self.assertNotIn('\r', text.replace('\r\n', ''))
                self.assertEqual(text, body.replace('\r\n', '\n').replace('\n', '\r\n'))
                self.assertTrue(text.startswith('Option Explicit\r\n'))
                self.assertRegex(text, r'[\u4e00-\u9fff]')

    def test_all_batches_fail_closed_when_root_is_unavailable(self):
        for name, body in GEN.BATS.items():
            with self.subTest(name=name):
                changes = [line for line in body.splitlines() if line.startswith('cd /d ')]
                self.assertEqual(changes, ['cd /d "%~dp0.." || exit /b 1'])

    def test_only_manual_ec_map_batch_forwards_explicit_paths(self):
        body = GEN.BATS['生成EC寄存器表.bat']
        for exe in ('UmiPanel.exe', 'python.exe'):
            self.assertIn('"runtime\\' + exe + '" "tools\\gen_ec_map.py" %*', body)
        self.assertEqual(body.count('%*'), 2)
        for name, other in GEN.BATS.items():
            if name != '生成EC寄存器表.bat':
                self.assertNotIn('%*', other, name)

    def test_stop_batches_abort_before_experiments_on_unverified_shutdown(self):
        expected = ('停止面板.bat', '观察EC全表.bat', '观察OEM通道.bat', '造物者三态实测.bat')
        self.assertEqual({name for name, body in GEN.BATS.items() if '@@STOP@@' in body}, set(expected))
        for name in expected:
            with self.subTest(name=name):
                body = GEN.render_bat(GEN.BATS[name]).decode('gbk')
                self.assertIn('"%EXE%" "tools\\stop_panel.py"\r\nif errorlevel 1 (', body)
                gate = body.split('"tools\\stop_panel.py"', 1)[1].split(')', 1)[0]
                self.assertIn('exit /b 1', gate)
                for forbidden in ('netstat', 'taskkill', 'terminate', 'main.py" --stop'):
                    self.assertNotIn(forbidden, body.lower())

    def test_autostart_only_calls_fixed_desktop_cli(self):
        for name, action in (('安装开机自启.bat', 'on'), ('卸载开机自启.bat', 'off')):
            body = GEN.BATS[name]
            self.assertIn('"%EXE%" "%CD%\\desktop.py" --autostart ' + action, body)
            self.assertIn('runtime\\UmiPanel.exe', body)
            self.assertIn('runtime\\python.exe', body)
            self.assertIn('set "RESULT=%ERRORLEVEL%"', body)
            self.assertIn('exit /b %RESULT%', body)
            for forbidden in ('reg add', 'reg delete', 'main.py', '%*', '%1'):
                self.assertNotIn(forbidden, body)

    def test_debug_console_and_scripts_remain_explicit(self):
        body = GEN.BATS['调试命令行.bat']
        self.assertIn('cd /d "%~dp0.." || exit /b 1', body)
        self.assertIn('"%SystemRoot%\\System32\\cmd.exe" /d /v:off /k', body)
        for forbidden in ('main.py', 'tools\\', 'runtime\\', 'sc ', 'net ', '%*'):
            self.assertNotIn(forbidden, body)
        self.assertNotIn('配置连接.bat', GEN.BATS)
        for name in ('准备运行时.bat', '生成EC寄存器表.bat', 'EC只读自检.bat',
                     '观察EC全表.bat', '观察OEM通道.bat', '造物者三态实测.bat'):
            self.assertIn(name, GEN.BATS)
            self.assertTrue((ROOT / 'scripts' / name).is_file())
        self.assertFalse((ROOT / 'scripts' / 'developer').exists())

    def test_bootstrap_does_not_bypass_gates_or_install_implicitly(self):
        for body in GEN.VBS.values():
            lower = body.lower()
            for forbidden in ('regwrite', 'regdelete', 'runas', 'shellexecute',
                              'allow_write', 'mqtt_identity', 'config.json',
                              'powershell', 'getobject', 'execute(', 'eval('):
                self.assertNotIn(forbidden, lower)
        entry = GEN.VBS[ENTRY]
        self.assertIn('result = shell.Run(command, 0, False)', entry)
        self.assertLess(entry.index('If background Then WScript.Quit 1'), entry.index('answer = MsgBox'))
        self.assertLess(entry.index('If answer <> vbYes Then WScript.Quit 1'), entry.index('result = shell.Run('))
        self.assertIn('vbDefaultButton2', entry)
        self.assertNotIn('desktop_shortcut.vbs', entry)

    def test_runtime_guidance_uses_daily_entry_only(self):
        source = (ROOT / 'tools' / 'setup_runtime.py').read_text(encoding='utf-8')
        self.assertIn('双击根目录 Umi Control Panel.vbs', source)
        self.assertNotIn('desktop_shortcut', source)
        self.assertEqual(GEN.VBS.keys(), {ENTRY})


@unittest.skipUnless(os.name == 'nt' and CSCRIPT.is_file(), 'Windows cscript required for mocked VBScript')
class BootstrapBehaviorTest(unittest.TestCase):
    def run_vbs(self, name=ENTRY, args=(), runtime=1, after_setup=0, setup_exit=0,
                answer=7, factory_failure=False, launch_failure=False,
                entry_present=True, setup_present=True):
        source = (ROOT / name).read_text(encoding='utf-16')
        # The only real WSH calls in the harness are console output and exit.
        source = source.replace('Option Explicit\n', '', 1)
        source = source.replace('WScript.Arguments', 'Host.Arguments')
        source = source.replace('WScript.ScriptFullName', 'Host.ScriptFullName')
        source = source.replace('WScript.Quit', 'Host.Quit')
        source = source.replace('CreateObject(', 'TestCreateObject(').replace('MsgBox', 'TestMsgBox')
        self.assertNotIn('CreateObject(', source.replace('TestCreateObject(', ''))
        self.assertNotIn('MsgBox', source.replace('TestMsgBox', ''))
        self.assertNotIn('WScript.', source.replace('"WScript.Shell"', ''))
        values = {
            'Host.ScriptFullName': FAKE_ROOT + '\\' + name.replace('/', '\\'),
            'Host.Arguments.Count': len(args), 'runtimeKind': runtime,
            'postSetupRuntime': after_setup, 'setupExit': setup_exit,
            'dialogAnswer': answer, 'factoryFailure': factory_failure,
            'launchFailure': launch_failure, 'entryPresent': entry_present,
            'setupPresent': setup_present,
        }
        assignments = '\n'.join(key + ' = ' + (vbs_string(value) if isinstance(value, str)
                                                else str(value)) for key, value in values.items())
        assignments += '\nargValues = Array(' + ', '.join(map(vbs_string, args)) + ')\n'
        harness = 'Option Explicit\n' + VBS_FAKES + assignments + source
        with tempfile.TemporaryDirectory(prefix='umi-vbs-test-') as directory:
            script = Path(directory) / 'mocked_bootstrap.vbs'
            script.write_bytes(GEN.render_vbs(harness))
            result = subprocess.run([str(CSCRIPT), '//nologo', '//U', str(script)],
                                    capture_output=True, timeout=15, creationflags=0x08000000)
        stdout = result.stdout.decode('utf-16-le').lstrip('\ufeff')
        stderr = result.stderr.decode('utf-16-le').lstrip('\ufeff')
        self.assertEqual(stderr, '', stderr)
        self.assertNotIn('Microsoft VBScript', stdout, stdout)
        return result.returncode, [line.split('\t') for line in stdout.splitlines()]

    def events(self, rows, kind):
        return [row[1:] for row in rows if row[0] == kind]

    def test_daily_alias_and_fallback_quote_absolute_unicode_paths(self):
        for runtime, exe in ((1, 'UmiPanel.exe'), (2, 'python.exe')):
            with self.subTest(exe=exe):
                code, rows = self.run_vbs(runtime=runtime)
                self.assertEqual(code, 0, rows)
                expected = quote('runtime\\' + exe) + ' ' + quote('desktop.py')
                self.assertEqual(self.events(rows, 'RUN'), [[expected, '0', 'False']])
                self.assertNotIn(FAKE_ROOT, self.events(rows, 'RUN')[0][0])
                self.assertEqual(self.events(rows, 'CWD'), [[FAKE_ROOT]])
                self.assertEqual(self.events(rows, 'DIALOG'), [])

    def test_background_is_fixed_service_command_without_gui(self):
        code, rows = self.run_vbs(args=('/background',))
        self.assertEqual(code, 0, rows)
        expected = quote('runtime\\UmiPanel.exe') + ' ' + quote('main.py')
        self.assertEqual(self.events(rows, 'RUN'), [[expected + ' --supervise --no-browser', '0', 'False']])
        self.assertNotIn(FAKE_ROOT, self.events(rows, 'RUN')[0][0])
        self.assertEqual(self.events(rows, 'DIALOG'), [])

    def test_strict_flags_never_forward_user_input(self):
        for args in (('/background', '& calc'), ('/background & calc',), ('--background',),
                     ('/background', '/background'), ('"x"',), ('--autostart', 'on')):
            with self.subTest(args=args):
                code, rows = self.run_vbs(args=args)
                self.assertEqual((code, rows), (2, []))

    def test_background_missing_runtime_has_no_setup_or_dialog(self):
        code, rows = self.run_vbs(args=('/background',), runtime=0)
        self.assertEqual(code, 1)
        self.assertEqual(self.events(rows, 'RUN'), [])
        self.assertEqual(self.events(rows, 'DIALOG'), [])

    def test_missing_runtime_defaults_to_no_and_explains_setup(self):
        code, rows = self.run_vbs(runtime=0)
        self.assertEqual(code, 1)
        self.assertEqual(self.events(rows, 'RUN'), [])
        dialogs = self.events(rows, 'DIALOG')
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0][0], str(4 + 32 + 256))
        self.assertEqual(dialogs[0][1], 'Umi Control Panel - 首次使用')
        self.assertIn('scripts\\准备运行时.bat', dialogs[0][2])
        self.assertIn('python.org', dialogs[0][2])
        self.assertIn('首次使用需要准备 Python 运行环境', dialogs[0][2])
        self.assertIn('可见的命令窗口', dialogs[0][2])
        self.assertIn('不请求管理员权限', dialogs[0][2])
        self.assertIn('按任意键关闭后会自动打开面板', dialogs[0][2])
        self.assertIn('选择“否”则不作任何更改', dialogs[0][2])

    def test_consent_runs_only_visible_fixed_setup_then_verifies_runtime(self):
        for runtime in (1, 2):
            code, rows = self.run_vbs(runtime=0, answer=6, after_setup=runtime)
            self.assertEqual(code, 0, rows)
            runs = self.events(rows, 'RUN')
            self.assertEqual(runs[0], ['"C:\\Windows\\System32\\cmd.exe" /d /v:off /c ""scripts\\准备运行时.bat""',
                                       '1', 'True'])
            self.assertEqual(runs[1][1:], ['0', 'False'])
            self.assertTrue(runs[1][0].endswith(quote('desktop.py')))
            self.assertNotIn(FAKE_ROOT, runs[1][0])

    def test_failed_or_incomplete_setup_reports_error_and_never_launches(self):
        for after_setup, setup_exit in ((0, 0), (1, 1)):
            code, rows = self.run_vbs(runtime=0, answer=6, after_setup=after_setup, setup_exit=setup_exit)
            self.assertEqual(code, 1, rows)
            self.assertEqual(len(self.events(rows, 'RUN')), 1)
            self.assertIn('初始化未完成', self.events(rows, 'DIALOG')[-1][2])
            self.assertIn('退出码：' + str(setup_exit), self.events(rows, 'DIALOG')[-1][2])

    def test_bootstrap_failures_are_readable_but_background_stays_silent(self):
        for failure, message in (({'factory_failure': True}, '无法使用 Windows 脚本服务'),
                                 ({'launch_failure': True}, '无法启动面板'),
                                 ({'entry_present': False}, '缺少启动脚本')):
            for background in (False, True):
                with self.subTest(failure=failure, background=background):
                    code, rows = self.run_vbs(args=('/background',) if background else (), **failure)
                    self.assertEqual(code, 1, rows)
                    dialogs = self.events(rows, 'DIALOG')
                    self.assertEqual(len(dialogs), 0 if background else 1)
                    if not background:
                        self.assertEqual(dialogs[0][1], 'Umi Control Panel - 启动失败')
                        self.assertIn(message, dialogs[0][2])

    def test_missing_setup_script_cannot_run_anything(self):
        code, rows = self.run_vbs(runtime=0, setup_present=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.events(rows, 'RUN'), [])
        self.assertIn('缺少初始化脚本', self.events(rows, 'DIALOG')[0][2])

class WriteGateRegressionTest(unittest.TestCase):
    def test_service_write_gates_remain_required(self):
        # No start/probe/connect: only the in-memory command queue is exercised.
        from app.act.channels.mqtt_gcu import MqttChannel
        for allowed, verified in ((False, True), (True, False)):
            cfg = Mock()
            def get(*keys, default=None):
                if keys == ('hardware', 'mqtt'):
                    return {'enabled': True, 'client_id': 'test', 'username': 'test'}
                if keys == ('hardware', 'ec', 'allow_write'):
                    return allowed
                return default
            cfg.get.side_effect = get
            channel = MqttChannel(cfg, Mock())
            channel.alive = True
            if verified:
                channel.caps['winkey.write'] = 'verified'
            ok, reason = channel.send_action('WINKEY_LOCK')
            self.assertFalse(ok, reason)
            self.assertTrue(channel._outq.empty())


if __name__ == '__main__':
    unittest.main(verbosity=2)
