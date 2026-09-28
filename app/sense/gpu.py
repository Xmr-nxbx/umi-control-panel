"""GPU 遥测：nvidia-smi 查询。找不到就整体降级，不影响其它功能。"""
import os
import shutil
import subprocess
import time

SEARCH = ['nvidia-smi.exe',
          r'C:\Windows\System32\nvidia-smi.exe',
          r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe']

FIELDS = ('utilization.gpu', 'utilization.memory', 'temperature.gpu',
          'power.draw', 'power.limit', 'fan.speed', 'name')
NO_WINDOW = 0x08000000


def _find():
    for cand in SEARCH:
        p = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if p:
            return p
    return None


def _num(text):
    try:
        return float(text.strip().split()[0])
    except (ValueError, IndexError):
        return None


class GpuSense:
    def __init__(self):
        self.bin = _find()
        self.error = None if self.bin else '未找到 nvidia-smi'
        self.last = None
        self.last_ts = 0.0

    def read(self, min_interval=1.0):
        now = time.time()
        if self.last and now - self.last_ts < min_interval:
            return self.last
        if not self.bin:
            return None
        q = ','.join(FIELDS)
        try:
            out = subprocess.run(
                [self.bin, '--query-gpu=' + q, '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=4,
                creationflags=NO_WINDOW).stdout.strip()
        except Exception as exc:                          # noqa: BLE001
            self.error = repr(exc)
            return self.last
        if not out:
            return self.last
        row = out.splitlines()[0]
        parts = [x.strip() for x in row.split(',')]
        if len(parts) < 6:
            return self.last
        data = {
            'util_pct': _num(parts[0]), 'mem_util_pct': _num(parts[1]),
            'temp_c': _num(parts[2]), 'power_w': _num(parts[3]),
            'power_limit_w': _num(parts[4]),
            'fan_pct': _num(parts[5]) if parts[5].upper() != 'N/A' else None,
            'name': parts[6] if len(parts) > 6 else 'NVIDIA GPU',
            'ts': now,
        }
        self.last, self.last_ts, self.error = data, now, None
        return data
