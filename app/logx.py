"""日志：文件滚动 + 内存环形缓冲（供面板 /api/log 读取）。"""
import os
import threading
import time
from datetime import datetime

from app.paths import data_path

_MAX_LINES = 600
_MAX_BYTES = 4 * 1024 * 1024


class Log:
    def __init__(self, name='umi-control-panel'):
        self.path = data_path(name + '.log')
        self._lock = threading.Lock()
        self._buf = []
        self._fh = None
        self._open()

    def _open(self):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > _MAX_BYTES:
                os.replace(self.path, self.path + '.1')
            self._fh = open(self.path, 'a', encoding='utf-8', buffering=1)
        except OSError:
            self._fh = None

    def _emit(self, level, msg):
        line = '%s [%s] %s' % (datetime.now().strftime('%m-%d %H:%M:%S'), level, msg)
        with self._lock:
            self._buf.append(line)
            if len(self._buf) > _MAX_LINES:
                del self._buf[:-_MAX_LINES]
            print(line, flush=True)
            if self._fh:
                try:
                    self._fh.write(line + '\n')
                except OSError:
                    pass

    def info(self, msg):
        self._emit('INFO', msg)

    warn = info

    def error(self, msg):
        self._emit('ERR ', msg)

    def tail(self, n=80):
        with self._lock:
            return list(self._buf[-n:])

    def close(self):
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


class Heartbeat:
    """把重复的同内容日志折叠成一行，避免 1 秒循环刷屏。"""

    def __init__(self, log, every_s=60.0):
        self.log = log
        self.every_s = every_s
        self._last = {}

    def say(self, key, msg):
        now = time.time()
        prev = self._last.get(key)
        if prev is None or (msg != prev[0]) or now - prev[1] >= self.every_s:
            self._last[key] = (msg, now)
            self.log.info(msg)
