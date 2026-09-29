# -*- coding: utf-8 -*-
"""遥测历史：固定容量的环形缓冲，给面板画曲线用。

设计上的三个约束：
  * 不依赖任何第三方库，也不引前端图表库（面板必须断网可用）；
  * 采样间隔固定，重启后从 data/history.json 接上——守护模式会不定期重启，
    曲线断成 20 分钟一段会让人看不出「下午到底烫不烫」；
  * 只存画曲线需要的标量，档位变化单独存（用于在时间轴上标竖线）。
"""
import json
import os
import threading
import time

from app.paths import data_path

HISTORY_NAME = 'history.json'
SAMPLE_S = 5.0
KEEP_S = 30 * 60.0          # 屏幕上保留 30 分钟
SAVE_EVERY_S = 60.0
MAX_SAMPLES = int(KEEP_S / SAMPLE_S) + 4
FIELDS = ('cpu_pct', 'gpu_pct', 'cpu_temp', 'cpu_mhz', 'fan_rpm', 'fan2_rpm',
          'fan_duty_l', 'battery_pct_ec')


class History:
    def __init__(self, log=None, path=None, keep_s=KEEP_S, sample_s=SAMPLE_S):
        self.log = log
        self.path = path or data_path(HISTORY_NAME)
        self.sample_s = float(sample_s)
        self.keep_s = float(keep_s)
        self.maxlen = int(self.keep_s / self.sample_s) + 4
        self.samples = []
        self.marks = []
        self._last_sample = 0.0
        self._last_save = 0.0
        self._last_tier = None
        self._lock = threading.Lock()

    # ---------- 采集 ----------
    def record(self, snap, tier=None, throttle=False, now=None):
        """每拍调一次；内部自己按 sample_s 节流。"""
        now = now if now is not None else time.time()
        self.load_if_idle(now)
        if now - self._last_sample < self.sample_s:
            return False
        self._last_sample = now
        sensor = snap.get('sensor') or snap
        hw = snap.get('hardware') or {}
        row = {'t': int(now)}
        for key in FIELDS:
            value = sensor.get(key) if key in sensor else hw.get(key)
            row[key] = round(value, 1) if isinstance(value, (int, float)) else None
        row['tier'] = tier
        row['th'] = 1 if throttle else 0
        first = not self.samples
        with self._lock:
            self.samples.append(row)
            del self.samples[:-self.maxlen]
            if first:
                # 窗口里的第一个点不是「变化」，只当作基准，否则曲线开头总有一根假竖线
                self._last_tier = tier
            elif tier and tier != self._last_tier:
                self.marks.append({'t': row['t'], 'tier': tier})
                del self.marks[:-64]
                self._last_tier = tier
        if now - self._last_save >= SAVE_EVERY_S:
            self._last_save = now
            self._save()
        return True

    # ---------- 持久化 ----------
    def load(self, now=None):
        """启动时接上上次的曲线；文件坏了就从头开始，不阻塞启动。

        now 可以注入：时间窗按墙上时钟算，单测用假时钟（1970 年）会被整批滤掉。
        """
        if not os.path.exists(self.path):
            return 0
        cutoff = (now if now is not None else time.time()) - self.keep_s
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = json.load(f)
            rows = [r for r in raw.get('samples', [])
                    if isinstance(r, dict) and r.get('t', 0) >= cutoff]
            marks = [m for m in raw.get('marks', [])
                     if isinstance(m, dict) and m.get('t', 0) >= cutoff]
        except (OSError, ValueError):
            return 0
        rows.sort(key=lambda r: r.get('t', 0))
        with self._lock:
            self.samples = rows[-self.maxlen:]
            self.marks = marks[-64:]
            self._last_tier = self.samples[-1].get('tier') if self.samples else None
        return len(self.samples)

    def load_if_idle(self, now=None):
        """没人往里写太久说明是刚醒来的老进程，补一次盘避免曲线停在停止前。"""
        now = now if now is not None else time.time()
        if self.samples and now - self.samples[-1]['t'] > 5 * 60:
            self.load()
        elif not self.samples:
            self.load()

    def _save(self):
        with self._lock:
            payload = {'samples': list(self.samples), 'marks': list(self.marks),
                       'saved_at': int(time.time())}
        tmp = self.path + '.tmp'
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, separators=(',', ':'))
            os.replace(tmp, self.path)
        except OSError as exc:
            if self.log:
                self.log.warn('[历史] 落盘失败（不影响运行）：%r' % (exc,))

    def close(self):
        self._save()

    # ---------- 输出 ----------
    def view(self, minutes=None):
        with self._lock:
            rows = list(self.samples)
            marks = list(self.marks)
        if minutes:
            cutoff = time.time() - minutes * 60
            rows = [r for r in rows if r['t'] >= cutoff]
        return {'samples': rows, 'marks': marks,
                'sample_s': self.sample_s, 'window_s': int(self.keep_s),
                'fields': list(FIELDS), 'count': len(rows)}
