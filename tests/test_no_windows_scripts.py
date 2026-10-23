# -*- coding: utf-8 -*-
"""项目自身不再依赖 PowerShell / .ps1（用户明确要求，避免执行策略问题）。"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'.git', '_local', 'runtime', 'out', 'bin', 'src', 'toolchain', 'logs',
             'data', '__pycache__'}
SCAN_DIRS = ('app', 'scripts', 'tests', 'tools', 'notes')
SCAN_FILES = ('main.py', 'run_tests.py', 'README.md')
TEXT_EXT = ('.py', '.bat', '.cmd', '.md')
INVOKE = re.compile(r'powershell(?:\.exe)?\s+-|Start' + '-Process', re.IGNORECASE)


def walk_files():
    for base in SCAN_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                yield os.path.join(dirpath, name)
    for name in SCAN_FILES:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            yield path


class NoWindowsScriptsTest(unittest.TestCase):
    def test_no_ps1_files(self):
        offenders = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.lower().endswith('.ps1'):
                    offenders.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
        self.assertEqual(offenders, [], '仍存在 .ps1：%s' % offenders)

    def test_no_powershell_invocation(self):
        offenders = []
        for path in walk_files():
            if not path.lower().endswith(TEXT_EXT):
                continue
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    for lineno, line in enumerate(f, 1):
                        if INVOKE.search(line):
                            offenders.append('%s:%d' % (os.path.relpath(path, ROOT), lineno))
            except OSError:
                continue
        self.assertEqual(offenders, [], '仍在调用 PowerShell：%s' % offenders)


if __name__ == '__main__':
    unittest.main()
