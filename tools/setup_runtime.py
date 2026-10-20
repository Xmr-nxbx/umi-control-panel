# -*- coding: utf-8 -*-
"""准备便携 Python 运行时（纯 Python，幂等，可重复执行）。

用法：
  runtime\\python.exe tools\\setup_runtime.py
  python tools\\setup_runtime.py --source C:\\Python312
  python tools\\setup_runtime.py --check

优先从本机已有的 Python 目录复制；找不到就下载官方 embeddable 包。
不调用 PowerShell、不写注册表、不装任何东西。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, 'runtime')
EMBED_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
IMPORT_TEST = 'import sys, ctypes, socket, json, http.server, ctypes.wintypes, ssl'


def runtime_python():
    return os.path.join(RUNTIME, 'python.exe')


def validate():
    exe = runtime_python()
    if not os.path.exists(exe):
        return False, '缺 runtime\\python.exe'
    try:
        res = subprocess.run([exe, '-c', IMPORT_TEST], capture_output=True,
                             timeout=30, creationflags=0x08000000)
    except Exception as exc:                              # noqa: BLE001
        return False, '运行时启动失败：%r' % (exc,)
    if res.returncode != 0:
        tail = (res.stderr or b'').decode('utf-8', 'replace')[-500:]
        return False, '运行时导入自检失败：%s' % tail.strip()
    return True, '运行时可用'


def source_candidates(explicit):
    out = []
    for path in (explicit, os.environ.get('UMI_PYTHON_SRC'), r'C:\Python312'):
        if path:
            out.append(os.path.abspath(path))
    # 用系统 Python 跑本脚本时，它自己的安装目录也是候选
    if not getattr(sys, 'frozen', False):
        prefix = os.path.dirname(os.path.abspath(sys.executable))
        if prefix and not os.path.abspath(prefix).lower().startswith(RUNTIME.lower()):
            out.append(prefix)
    seen = set()
    result = []
    for path in out:
        key = path.lower()
        if key not in seen and os.path.isfile(os.path.join(path, 'python.exe')):
            seen.add(key)
            result.append(path)
    return result


def copy_tree(src, dst):
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        if name == '__pycache__':
            continue
        source = os.path.join(src, name)
        target = os.path.join(dst, name)
        if os.path.isdir(source):
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(source, target)


def download_embed(dest):
    os.makedirs(dest, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='umi-python-') as tmp:
        archive = os.path.join(tmp, 'python-embed.zip')
        print('没有找到本机 Python，正在下载官方 embeddable 包...')
        print('  %s' % EMBED_URL)
        request = urllib.request.Request(EMBED_URL, headers={'User-Agent': 'umi-control-panel'})
        with urllib.request.urlopen(request, timeout=60) as response, \
                open(archive, 'wb') as fh:
            shutil.copyfileobj(response, fh)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        extracted = [os.path.join(tmp, n) for n in os.listdir(tmp)
                     if os.path.isdir(os.path.join(tmp, n))]
        src = extracted[0] if extracted else tmp
        copy_tree(src, dest)


def create_aliases():
    exe = runtime_python()
    for name in ('UmiPanel.exe', 'OcTool.exe'):
        dst = os.path.join(RUNTIME, name)
        if not os.path.exists(dst):
            shutil.copy2(exe, dst)
            print('已生成 %s' % name)


def prepare(explicit_source=None):
    ok, reason = validate()
    if ok:
        print('运行时已存在：%s' % reason)
    else:
        os.makedirs(RUNTIME, exist_ok=True)
        sources = source_candidates(explicit_source)
        if sources:
            src = sources[0]
            print('从 %s 复制运行时...' % src)
            copy_tree(src, RUNTIME)
        else:
            download_embed(RUNTIME)
        ok, reason = validate()
        if not ok:
            raise SystemExit('运行时准备失败：%s' % reason)
        print('运行时准备完成：%s' % reason)
    create_aliases()
    ok, reason = validate()
    if not ok:
        raise SystemExit('运行时自检失败：%s' % reason)
    print('完成。启动面板：双击 scripts\\启动面板.bat')


def main(argv=None):
    parser = argparse.ArgumentParser(description='准备便携 Python 运行时（不调用 PowerShell）')
    parser.add_argument('--source', help='从指定 Python 目录复制（目录里要有 python.exe）')
    parser.add_argument('--check', action='store_true', help='只检查，不安装')
    args = parser.parse_args(argv)
    if args.check:
        ok, reason = validate()
        print(reason)
        return 0 if ok else 1
    prepare(args.source)
    return 0


if __name__ == '__main__':
    sys.exit(main())
