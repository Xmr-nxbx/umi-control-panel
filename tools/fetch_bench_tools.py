# -*- coding: utf-8 -*-
"""把跑分要用的开源负载准备好：zstd.exe + 固定种子的基准输入文件。

    runtime\\python.exe tools\\fetch_bench_tools.py

面板里点跑分时也会自动做这件事，这个脚本只是让用户能手动补一次、看清缺了什么。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bench import ensure_input, ensure_zstd, zstd_missing      # noqa: E402


def main():
    ok, detail = ensure_zstd()
    print('zstd：%s（%s）' % ('就绪' if ok else '不可用', detail))
    if not ok:
        return 2
    size = ensure_input()
    print('基准输入：%.1f MB（固定种子，每次跑的内容完全一样）' % (size / 1048576.0))
    print('缺什么随时可以用这个脚本补：%s' % (zstd_missing() or '目前不缺东西'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
