"""桌面入口；日常双击 Umi Control Panel.vbs 无控制台启动。"""
import argparse
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    from app.desktop.controller import DesktopController, set_autostart
    parser = argparse.ArgumentParser(description='Umi 桌面启动中心')
    parser.add_argument('--autostart', choices=('on', 'off'))
    args = parser.parse_args(argv)
    if args.autostart:
        set_autostart(args.autostart == 'on')
        print('登录自启已%s。' % ('开启（静默）' if args.autostart == 'on' else '关闭'))
        return 0
    controller = DesktopController()
    from app.singleton import SingleInstance
    from app.desktop.window import DesktopWindow
    from app.desktop import native as n
    instance = SingleInstance('UmiControlPanelDesktop')
    try:
        if not instance.acquire():
            n.signature(n.u, 'FindWindowW', n.w.HWND, n.w.LPCWSTR, n.w.LPCWSTR)
            hwnd = n.u.FindWindowW('UmiDesktopCenter', None)
            if hwnd:
                n.u.ShowWindow(hwnd, 9)
                n.signature(n.u, 'SetForegroundWindow', n.w.BOOL, n.w.HWND)(hwnd)
            return 0
        return DesktopWindow(controller).run()
    finally:
        instance.release()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        from pathlib import Path
        root = Path(__file__).resolve().parent
        detail = traceback.format_exc()
        path = root / 'data' / 'desktop-error.log'
        try:
            path.parent.mkdir(exist_ok=True)
            path.write_text(detail, encoding='utf-8')
            message = '桌面入口无法启动，详情已保存到：\n%s\n\n可用 scripts 中的运行时准备工具修复。' % path
        except OSError:
            message = '桌面入口无法启动，且无法写入日志。\n请检查项目目录权限与便携运行时。'
        ctypes.windll.user32.MessageBoxW(None, message, 'Umi Control Panel', 0x10)
        raise SystemExit(1)
