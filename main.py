"""Umi Control Panel 启动入口。

用法：
    runtime\\python.exe main.py                常驻（面板 + 托盘 + 自启由脚本安装）
    runtime\\python.exe main.py --one-shot     打印一次状态快照，自检用
    runtime\\python.exe main.py --probe-acpi   只读枚举 ACPI 命名空间（需管理员）
    runtime\\python.exe main.py --stop         让正在运行的实例优雅退出
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.cli import main  # noqa: E402

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:                            # noqa: BLE001
        # 启动期崩了也要留下现场，并且让窗口停住，别一闪而过
        import traceback
        from datetime import datetime
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, 'fatal-%s.log' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        with open(path, 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        print('[致命错误] 详情见 %s' % path)
        traceback.print_exc()
        try:
            input('按回车退出…')
        except (EOFError, OSError):
            pass
        raise SystemExit(1)
