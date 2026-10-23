# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path

DEFAULT_TABLE_DIR = (
    Path(os.environ.get('ProgramW6432') or os.environ.get('ProgramFiles') or 'C:/Program Files')
    / 'OEM' / 'CreatorCenter' / 'UniwillService' / 'MyControlCenter' / 'UserFanTables'
)
_DEFAULT_NAMES = {
    'M%dT%d' % (index, slot): 'DefaultFanTable_' + mode
    for index, mode in enumerate(('Gaming', 'Office', 'Turbo'), 1)
    for slot in range(1, 6)
}
_MAX_BYTES = 64 * 1024


def _normalize(rows, which):
    if not isinstance(rows, list) or len(rows) != 16:
        raise ValueError('%s 默认表必须有 16 个点' % which)
    points = []
    for row in rows:
        if not isinstance(row, dict) or any(
            type(row.get(field)) is not int for field in ('ID', 'UpT', 'DownT', 'Duty')
        ):
            raise ValueError('%s 默认表点字段必须是整数，不能缺失或使用布尔值' % which)
        pid, up, down, duty = (row[field] for field in ('ID', 'UpT', 'DownT', 'Duty'))
        if any(not (0 <= value <= 100 or value == 255) for value in (up, down)):
            raise ValueError('%s 默认表温度必须在 0..100 或为 255' % which)
        if not 0 <= duty <= 100:
            raise ValueError('%s 默认表占空比必须在 0..100' % which)
        points.append({'id': pid, 'up_t': up, 'down_t': down, 'duty_pct': duty,
                       'sentinel': up == 255, 'mailbox': False})
    points.sort(key=lambda point: point['id'])
    if [point['id'] for point in points] != list(range(16)):
        raise ValueError('%s 默认表 ID 必须为不重复的 0..15' % which)
    if points[0]['up_t'] != 0 or points[0]['duty_pct'] != 0:
        raise ValueError('%s 默认表首点温度和占空比必须为 0' % which)
    active = [point for point in points if not point['sentinel']]
    if len(active) < 2:
        raise ValueError('%s 默认表至少需要 2 个活跃点' % which)
    if any(not point['sentinel'] for point in points[len(active):]):
        raise ValueError('%s 默认表哨兵必须连续位于尾部' % which)
    if any(a['up_t'] > b['up_t'] for a, b in zip(active, active[1:])):
        raise ValueError('%s 默认表活跃点温度必须单调不降' % which)
    if any(a['duty_pct'] > b['duty_pct'] for a, b in zip(points, points[1:])):
        raise ValueError('%s 默认表全表占空比必须单调不降' % which)
    if active[-1]['duty_pct'] != 100:
        raise ValueError('%s 默认表末活跃点占空比必须为 100%%' % which)
    return points


def load_fan_defaults(name):
    if not isinstance(name, str) or name not in _DEFAULT_NAMES:
        return {'ok': False, 'reason': '非法风扇表名，仅允许 M1T1..M3T5 共 15 个槽位'}
    default_name = _DEFAULT_NAMES[name]
    source = default_name + '.json'
    try:
        with (DEFAULT_TABLE_DIR / source).open('rb') as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return {'ok': False, 'reason': '未找到本机 OEM 默认风扇表'}
    except OSError:
        return {'ok': False, 'reason': '无法读取本机 OEM 默认风扇表'}
    if len(raw) > _MAX_BYTES:
        return {'ok': False, 'reason': '本机 OEM 默认风扇表超过 64 KiB 上限'}
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, RecursionError):
        return {'ok': False, 'reason': '本机 OEM 默认风扇表不是有效的 UTF-8 JSON'}
    if not isinstance(data, dict):
        return {'ok': False, 'reason': '本机 OEM 默认风扇表 JSON 必须是对象'}
    if data.get('Name') != default_name:
        return {'ok': False, 'reason': '本机 OEM 默认风扇表 Name 与请求模式不符'}
    try:
        tables = {which: _normalize(data.get(which), which) for which in ('CPU', 'GPU')}
    except ValueError as exc:
        return {'ok': False, 'reason': str(exc)}
    return {'ok': True, 'name': name, 'source': source, 'tables': tables}
