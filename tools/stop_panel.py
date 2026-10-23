# -*- coding: utf-8 -*-
"""手动优雅停止面板；仅确认命名实例消失后才允许后续实验。"""
from http.client import HTTPConnection, HTTPException
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.paths import discover_panel_port, install_fingerprint  # noqa: E402

STOP_TIMEOUT = 15.0
HTTP_TIMEOUT = 3.0
POLL_INTERVAL = 0.25


def _new_instance():
    from app.singleton import SingleInstance
    return SingleInstance('UmiControlPanel')


def _instance_absent():
    inst = _new_instance()
    try:
        return inst.acquire()
    finally:
        # acquire 为 False 也持有句柄，不释放会把旧实例误判为仍在运行。
        inst.release()


def _service_owned(port, timeout):
    """拒绝仅凭端口文件推测归属：端口上必须是当前目录启动的新版服务。"""
    connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request('GET', '/api/state',
                           headers={'Origin': 'http://127.0.0.1:%d' % port})
        with connection.getresponse() as response:
            if response.status != 200:
                raise OSError('状态接口返回 HTTP %d' % response.status)
            body = json.loads(response.read(65536).decode('utf-8'))
    finally:
        connection.close()
    meta = body.get('meta') if isinstance(body, dict) else None
    if not isinstance(meta, dict) or meta.get('install_id') != install_fingerprint():
        raise OSError('该端口上的服务不属于当前项目目录，已拒绝停止')
    return True


def _request_shutdown(port, timeout):
    # 直连回环地址，不使用代理、不跟随重定向；保留同源校验。
    connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request(
            'POST', '/api/shutdown', body=b'{}',
            headers={'Content-Type': 'application/json',
                     'Origin': 'http://127.0.0.1:%d' % port})
        with connection.getresponse() as response:
            if not 200 <= response.status < 300:
                raise OSError('停止请求被拒绝：HTTP %d' % response.status)
            # 不读响应体；HTTP 接受请求不等于服务已经退出。
    finally:
        connection.close()


def stop_panel():
    try:
        if _instance_absent():
            print('未发现运行中的面板实例。')
            return 0
        port = discover_panel_port()
        timeout = min(HTTP_TIMEOUT, STOP_TIMEOUT)
        _service_owned(port, timeout)
        deadline = time.monotonic() + STOP_TIMEOUT
        try:
            _request_shutdown(port, timeout)
        except (OSError, HTTPException) as exc:
            print('停止请求未完成：%s；仍将检查实例是否退出。' % exc)
        while True:
            if _instance_absent():
                print('已确认面板实例退出。')
                return 0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print('等待超时，无法确认面板已停止。请在面板中选择停止后重试。')
                return 1
            time.sleep(min(POLL_INTERVAL, remaining))
    except Exception as exc:
        # 检测或端口读取失败不能当作「没有实例」。
        print('无法确认面板已停止：%s。已取消后续操作。' % exc)
        return 1


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print('此停止工具不接受参数，请直接运行 scripts\\停止面板.bat。')
        return 2
    return stop_panel()


if __name__ == '__main__':
    sys.exit(main())
