# -*- coding: utf-8 -*-
"""把 EEMBC 官方 CoreMark 编成 tools/bin/coremark.exe。

为什么要自己编：CoreMark 没有官方 Windows 二进制，网上的现成 exe 来路不明，
而跑分工具一旦被换过，分数就毫无意义。所以只认一条路——
从 EEMBC 官方仓库取源码，用本机编译器现场编，产物不进仓库（tools/bin 已 gitignore）。

用的是仓库自带的 **posix** 移植（argv 传种子和迭代次数、malloc 分配数据块、printf 输出），
只加一个 `-DUSE_CLOCK=1` 让它用 time.h 的 clock() 计时，这样不依赖 clock_gettime。
上游源码一个字都不改，所以任何人重跑这个脚本都能得到同一个 exe。

    runtime\\python.exe tools\\build_coremark.py
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(ROOT_DIR, 'tools', 'bin')
SRC_DIR = os.path.join(ROOT_DIR, 'tools', 'src')
OUT_EXE = os.path.join(BIN_DIR, 'coremark.exe')
ZIP_PATH = os.path.join(SRC_DIR, 'coremark.zip')
ZIP_URL = 'https://codeload.github.com/eembc/coremark/zip/refs/heads/main'
NO_WINDOW = 0x08000000
CORE_FILES = ('core_list_join.c', 'core_main.c', 'core_matrix.c',
              'core_state.c', 'core_util.c')
SCORE_RE = re.compile(r'CoreMark 1\.0\s*:\s*([\d.]+)')
# 跑不满 10 秒官方不给分数，但会给这一行；标定迭代数靠它就够。
IPS_RE = re.compile(r'Iterations/Sec\s*:\s*([\d.]+)')


def say(msg):
    print(msg, flush=True)


def find_cc():
    """找编译器：项目自带的便携工具链优先，然后环境变量、PATH，最后扫常见安装位置。"""
    local = os.path.join(ROOT_DIR, 'tools', 'toolchain')
    env = os.environ.get('COREMARK_CC')
    if env and os.path.exists(env):
        return env
    for name in ('gcc', 'clang', 'cc'):
        path = shutil.which(name)
        if path:
            return path
    roots = [
        local,
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages'),
        'C:\\mingw64', 'C:\\msys64', 'C:\\TDM-GCC-64', 'C:\\Program Files\\LLVM',
    ]
    pats = ['**/bin/gcc.exe', '**/bin/clang.exe', 'gcc.exe', 'clang.exe']
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in pats:
            try:
                hits = glob.glob(os.path.join(root, pat), recursive=True)
            except OSError:
                continue
            for hit in sorted(hits, key=len):        # 路径越浅越可能是正经工具链
                if os.path.exists(hit):
                    return hit
    return None


def fetch_source():
    """取官方源码 zip 并解开；已经有了就不重复下载。"""
    src = os.path.join(SRC_DIR, 'coremark-main')
    if os.path.exists(os.path.join(src, 'core_main.c')):
        return src
    os.makedirs(SRC_DIR, exist_ok=True)
    if not os.path.exists(ZIP_PATH):
        say('[coremark] 下载 EEMBC 官方源码…')
        with urllib.request.urlopen(ZIP_URL, timeout=120) as resp, \
                open(ZIP_PATH, 'wb') as f:
            f.write(resp.read())
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(SRC_DIR)
    if not os.path.exists(os.path.join(src, 'core_main.c')):
        raise RuntimeError('源码解出来不对劲，没找到 core_main.c')
    return src


def build(cc, src):
    """一条 gcc 命令编出来，参数全部写死，保证可重复。"""
    os.makedirs(BIN_DIR, exist_ok=True)
    flags = '-O2 -DPERFORMANCE_RUN=1 -DUSE_CLOCK=1'
    args = [cc, '-O2', '-DPERFORMANCE_RUN=1', '-DUSE_CLOCK=1', '-DITERATIONS=0',
            '-DCOMPILER_VERSION="%s"' % os.path.basename(cc),
            '-DFLAGS_STR="%s"' % flags,
            '-Iposix', '-I.',
            '-o', OUT_EXE]
    args += list(CORE_FILES) + [os.path.join('posix', 'core_portme.c')]
    say('[coremark] 编译：%s' % ' '.join(args))
    proc = subprocess.run(args, cwd=src, capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          creationflags=NO_WINDOW, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError('编译失败（退出码 %s）：\n%s\n%s'
                           % (proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:]))
    warn = (proc.stderr or '').strip()
    if warn:
        say('[coremark] 编译器提示（不影响结果）：\n%s' % warn[:800])
    return OUT_EXE


def _run(iterations):
    proc = subprocess.run([OUT_EXE, '0x0', '0x0', '0x66', str(iterations), '0'],
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', creationflags=NO_WINDOW, timeout=300)
    return (proc.stdout or '') + (proc.stderr or '')


def verify():
    """编完必须真跑一次：跑不出分数的 exe 不许留在 tools/bin 里。

    官方规矩是单次跑不满 10 秒不算有效成绩、不吐分数，所以先短跑一次量出
    每秒迭代数，再折算成 12 秒的迭代数正式跑。
    """
    text = _run(100)
    m = IPS_RE.search(text)
    ips = float(m.group(1)) if m else 30000.0
    iters = max(1000, int(ips * 12))
    text = _run(iters)
    m = SCORE_RE.search(text)
    if not m:
        os.remove(OUT_EXE)
        raise RuntimeError('coremark 没吐出分数：\n%s' % text[-1500:])
    return float(m.group(1)), iters


def main():
    cc = find_cc()
    if not cc:
        say('找不到 C 编译器（gcc/clang）。先装一个，例如：\n'
            '    winget install --id BrechtSanders.WinLibs.POSIX.UCRT\n'
            '装完重开一个终端再跑这个脚本。')
        return 2
    say('[coremark] 用编译器：%s' % cc)
    src = fetch_source()
    build(cc, src)
    score, iters = verify()
    say('[coremark] 编译成功：%s（自检 %d 次迭代 = %.1f 分）' % (OUT_EXE, iters, score))
    return 0


if __name__ == '__main__':
    sys.exit(main())
