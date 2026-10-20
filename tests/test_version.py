# -*- coding: utf-8 -*-
"""版本号只有一个来源，别处必须引用它，不许各写各的。

    runtime\\python.exe tests\\test_version.py

打过 v0.1.0 tag 之后最怕的就是「tag 说 0.1.0、代码里还写着 0.1」。
所以把版本钉死在 app/__init__.py 的 __version__ 一处，HTTP Server 头、
/api/state 的 meta.version、面板页脚、README 全部从它派生。
这里逐条盯住这些派生点，漏改任何一处（写死数字、或干脆没接上）就红。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import __version__   # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


@case('__version__ 是 x.y.z 三段，不是 0.1 那种两段')
def t_shape():
    assert re.fullmatch(r'\d+\.\d+\.\d+', __version__), __version__


@case('HTTP Server 头从 __version__ 拼，不写死数字')
def t_httpd():
    src = read('app', 'server', 'httpd.py')
    assert "'UmiControlPanel/' + __version__" in src, 'Server 头没引用 __version__'
    assert not re.search(r"UmiControlPanel/\d", src), 'Server 头里还写死了版本号'


@case('/api/state 的 meta 带上 version')
def t_state_meta():
    src = read('app', 'daemon.py')
    assert "'version': __version__" in src, 'daemon 的 meta 没把版本暴露出去'


@case('面板页脚读 meta.version，不写死在 HTML 里')
def t_footer():
    js = read('app', 'web', 'app.js')
    assert "'foot-version'" in js, '页脚没接版本'
    assert 'v' in js and 'meta' in js
    # HTML 里那个 span 不能写死具体版本号，否则改了 __version__ 它不会跟着动
    html = read('app', 'web', 'index.html')
    assert re.search(r'id="foot-version">[^<]*本地运行', html), '页脚 span 结构变了'
    assert not re.search(r'id="foot-version">[^<]*\d+\.\d+', html), '页脚写死了版本号'


@case('README 写的版本和 __version__ 一致')
def t_readme():
    md = read('README.md')
    assert 'v%s' % __version__ in md, 'README 没标 v%s' % __version__


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print('  ok  %s' % name)
        except AssertionError as exc:
            bad += 1
            print('  FAIL %s -> %r' % (name, exc))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
