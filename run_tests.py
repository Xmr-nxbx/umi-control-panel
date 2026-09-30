# -*- coding: utf-8 -*-
"""一条命令跑完全部测试。

用法：

    runtime\\python.exe run_tests.py
    runtime\\python.exe run_tests.py --only fan          # 只跑文件名含 fan 的
    runtime\\python.exe run_tests.py --timeout 120       # 单文件超时改 120 秒

行为约定（和 tests/ 里各文件的自测写法对齐）：
- 自动发现 tests/test_*.py，用**当前解释器同一个 python**（sys.executable）
  逐个跑成子进程，串行（有测试会开句柄，并发不安全）。
- 每个文件自己会打印「N/M 通过」那一行并给出退出码，这里不重复解析用例数，
  只认退出码 + 那行文本，结论分三档：
      通过  = 退出码 0
      失败  = 退出码非 0，但跑到了计数行（用例没全过）
      崩了  = 退出码非 0 且连计数行都没打出来（import 错 / 语法错），或超时
- 有失败就返回非 0；全过返回 0。
"""
import argparse
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(REPO, 'tests')


def discover(only):
    if not os.path.isdir(TESTS_DIR):
        return []
    names = sorted(n for n in os.listdir(TESTS_DIR)
                   if n.startswith('test_') and n.endswith('.py')
                   and (not only or only in n))
    return [os.path.join(TESTS_DIR, n) for n in names]


def last_pass_line(text):
    """各文件计数行的写法不统一（`12/12 通过` / `通过 72 / 失败 0` / `结果：6/6 通过`），
    所以只认「最后一行同时含数字和『通过』」这一条软规则，绝不按单一格式解析。"""
    for line in reversed(text.splitlines()):
        if '通过' in line and any(c.isdigit() for c in line):
            return line.strip()
    return None


def run_one(path, timeout):
    started = time.time()
    env = dict(os.environ)
    # Windows 控制台默认 GBK：子进程输出统一按 UTF-8 收，父进程同样按 UTF-8 打
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        proc = subprocess.run(
            [sys.executable, path], cwd=REPO, env=env,
            capture_output=True, timeout=timeout)
        out = proc.stdout.decode('utf-8', errors='replace')
        err = proc.stderr.decode('utf-8', errors='replace')
        elapsed = time.time() - started
        if proc.returncode == 0:
            verdict = '通过'
        elif last_pass_line(out) is not None:
            verdict = '失败'
        else:
            verdict = '崩了'
        return verdict, elapsed, out, err
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - started
        out = (exc.stdout or b'').decode('utf-8', errors='replace') if exc.stdout else ''
        err = (exc.stderr or b'').decode('utf-8', errors='replace') if exc.stderr else ''
        return '崩了', elapsed, out, err + ('\n[超时] %d 秒内没有跑完\n' % timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(description='逐个串行运行 tests/ 下的全部测试')
    parser.add_argument('--only', default='', help='只跑文件名里含这个子串的测试')
    parser.add_argument('--timeout', type=int, default=60,
                        help='单个文件的超时秒数（默认 60）')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

    paths = discover(args.only.strip())
    if not paths:
        print('没有发现任何测试文件%s。' % (('（--only %s）' % args.only) if args.only else ''))
        return 1

    print('共 %d 个测试文件，解释器：%s，单文件超时 %d 秒\n' % (len(paths), sys.executable, args.timeout))
    counts = {'通过': 0, '失败': 0, '崩了': 0}
    for path in paths:
        name = os.path.basename(path)
        verdict, elapsed, out, err = run_one(path, args.timeout)
        counts[verdict] += 1
        print('%s / %s / %.1f 秒' % (name, verdict, elapsed))
        if verdict == '通过':
            line = last_pass_line(out)
            if line:
                print('    %s' % line)
        else:
            # 失败和崩了的输出原样带出来，别让排查的人再跑一遍
            for tag, text in (('stdout', out), ('stderr', err)):
                if text.strip():
                    print('    ---- %s ----' % tag)
                    for ln in text.splitlines():
                        print('    %s' % ln)
        print()

    total = len(paths)
    bad = counts['失败'] + counts['崩了']
    print('总计：%d 个文件，通过 %d / 失败 %d / 崩了 %d' % (
        total, counts['通过'], counts['失败'], counts['崩了']))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
