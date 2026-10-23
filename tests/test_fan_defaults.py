# -*- coding: utf-8 -*-
import ast
import inspect
import io
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import fan_defaults

CASES = []
MODES = ('Gaming', 'Office', 'Turbo')
LIMIT = 64 * 1024


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def document(mode='Gaming'):
    offset = MODES.index(mode)
    tables = {}
    for which, temps, duties in (
        ('CPU', [0, 25, 50, 80], [0, 20 + offset, 60, 100]),
        ('GPU', [0, 50, 50, 90], [0, 35 + offset, 70, 100]),
    ):
        tables[which] = [
            {'ID': i, 'UpT': temps[i] if i < 4 else 255,
             'DownT': max(0, temps[i] - 5) if i < 4 else 255,
             'Duty': duties[i] if i < 4 else 100}
            for i in range(16)
        ]
    return {'Name': 'DefaultFanTable_' + mode, **tables}


@contextmanager
def fixture(data=None, mode='Gaming', raw=None):
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        path = directory / ('DefaultFanTable_' + mode + '.json')
        if raw is None:
            raw = json.dumps(document(mode) if data is None else data).encode('utf-8-sig')
        path.write_bytes(raw)
        with patch.object(fan_defaults, 'DEFAULT_TABLE_DIR', directory):
            yield path


def failed(result):
    assert set(result) == {'ok', 'reason'}, result
    assert result['ok'] is False, result
    assert isinstance(result['reason'], str), result
    assert any('\u4e00' <= c <= '\u9fff' for c in result['reason']), result


def rejected(data):
    with fixture(data):
        failed(fan_defaults.load_fan_defaults('M1T1'))


def normalized(rows):
    return [{'id': p['ID'], 'up_t': p['UpT'], 'down_t': p['DownT'],
             'duty_pct': p['Duty'], 'sentinel': p['UpT'] == 255, 'mailbox': False}
            for p in sorted(rows, key=lambda p: p['ID'])]


@case('三模式全部 15 槽只映射固定默认文件，双表独立且字段完整')
def _():
    for index, mode in enumerate(MODES, 1):
        data = document(mode)
        with fixture(data, mode) as path:
            for slot in range(1, 6):
                name = 'M%dT%d' % (index, slot)
                result = fan_defaults.load_fan_defaults(name)
                assert result == {'ok': True, 'name': name, 'source': path.name,
                                  'tables': {k: normalized(data[k]) for k in ('CPU', 'GPU')}}, result
                assert result['tables']['CPU'] != result['tables']['GPU']
                assert result['tables']['CPU'] is not result['tables']['GPU']
                assert all(p['mailbox'] is False for p in result['tables']['GPU'])


@case('CPU/GPU 的重复温度合法，GPU 两个 50 度不合并')
def _():
    data = document()
    data['CPU'][1]['UpT'] = 50
    with fixture(data):
        result = fan_defaults.load_fan_defaults('M1T1')
        assert result['ok'] is True, result
        for which in ('CPU', 'GPU'):
            assert [p['up_t'] for p in result['tables'][which]][1:3] == [50, 50]
            assert len(result['tables'][which]) == 16


@case('两张表各自按乱序 ID 归一化')
def _():
    data = document()
    data['CPU'].reverse()
    data['GPU'] = data['GPU'][7:] + data['GPU'][:7]
    with fixture(data):
        result = fan_defaults.load_fan_defaults('M1T1')
        assert result['ok'] is True, result
        for which in ('CPU', 'GPU'):
            assert result['tables'][which] == normalized(data[which]), result


@case('UTF-8 有无 BOM 均可读取')
def _():
    for encoding in ('utf-8', 'utf-8-sig'):
        data = document()
        data['Description'] = '合成测试数据'
        with fixture(raw=json.dumps(data, ensure_ascii=False).encode(encoding)):
            result = fan_defaults.load_fan_defaults('M1T1')
            assert result['ok'] is True, result


@case('文件缺失或目录不存在明确失败，不生成文件')
def _():
    with fixture() as path:
        path.unlink()
        failed(fan_defaults.load_fan_defaults('M1T1'))
        assert list(path.parent.iterdir()) == []
        missing = path.parent / 'missing'
        with patch.object(fan_defaults, 'DEFAULT_TABLE_DIR', missing):
            failed(fan_defaults.load_fan_defaults('M1T1'))
        assert not missing.exists()


@case('不可读文件、目录冒充文件和底层读取错误均失败')
def _():
    with fixture() as path:
        for error in (PermissionError('denied'), OSError('read error')):
            with patch.object(Path, 'open', side_effect=error):
                failed(fan_defaults.load_fan_defaults('M1T1'))
        path.unlink()
        path.mkdir()
        failed(fan_defaults.load_fan_defaults('M1T1'))


@case('坏 JSON、非法 UTF-8 和过深 JSON 均失败')
def _():
    for raw in (b'', b'{', b'{} trailing', b'\xff\xfe',
                b'[' * 2000 + b'0' + b']' * 2000,
                b'{"Name":' + b'9' * 5000 + b'}'):
        with fixture(raw=raw):
            failed(fan_defaults.load_fan_defaults('M1T1'))


@case('JSON 根必须是对象')
def _():
    for raw in (b'[]', b'null', b'false', b'1', b'"text"'):
        with fixture(raw=raw):
            failed(fan_defaults.load_fan_defaults('M1T1'))


@case('64 KiB 恰好允许，超出一个字节也拒绝')
def _():
    raw = json.dumps(document()).encode('utf-8-sig')
    raw += b' ' * (LIMIT - len(raw))
    with fixture(raw=raw):
        result = fan_defaults.load_fan_defaults('M1T1')
        assert result['ok'] is True, result
    for extra in (b' ', b' ' * LIMIT):
        with fixture(raw=raw + extra):
            failed(fan_defaults.load_fan_defaults('M1T1'))


@case('读取有界，不将超大文件整体读入内存')
def _():
    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= LIMIT + 1, size
            return super().read(size)

    with fixture(), patch.object(Path, 'open', return_value=BoundedReader(b' ' * (LIMIT * 2))):
        failed(fan_defaults.load_fan_defaults('M1T1'))


@case('Name 缺失、类型错误、槽名或其他模式均拒绝')
def _():
    for name in (None, True, 1, [], {}, 'M1T1', 'DefaultFanTable_Office',
                 'defaultfantable_gaming', 'DefaultFanTable_Gaming\n'):
        data = document()
        data['Name'] = name
        rejected(data)
    data = document()
    del data['Name']
    rejected(data)


@case('CPU/GPU 都必需，双表必须分别是 16 点数组')
def _():
    for which in ('CPU', 'GPU'):
        data = document()
        del data[which]
        rejected(data)
        for rows in (None, True, {}, 'rows', [], document()[which][:15],
                     document()[which] + [document()[which][0]]):
            data = document()
            data[which] = rows
            rejected(data)


@case('每个点必须是对象且四个必需字段完整')
def _():
    for which in ('CPU', 'GPU'):
        for row in (None, [], 1, True, 'row', {}):
            data = document()
            data[which][2] = row
            rejected(data)
        for field in ('ID', 'UpT', 'DownT', 'Duty'):
            data = document()
            del data[which][2][field]
            rejected(data)


@case('ID 必须唯一且为 0..15 的整数，禁止 bool 和隐式转换')
def _():
    for which in ('CPU', 'GPU'):
        for value in (-1, 16, 999, 1, True, False, 2.0, '2', None, [], {}):
            data = document()
            data[which][2]['ID'] = value
            rejected(data)


@case('温度只允许整数 0..100 或 255，禁止 bool')
def _():
    for which in ('CPU', 'GPU'):
        for field in ('UpT', 'DownT'):
            for value in (-1, 101, 254, 256, True, False, 50.0, '50', None, [], {}):
                data = document()
                data[which][2][field] = value
                rejected(data)


@case('Duty 只允许整数 0..100，禁止 bool')
def _():
    for which in ('CPU', 'GPU'):
        for value in (-1, 101, 255, True, False, 60.0, '60', None, [], {}):
            data = document()
            data[which][2]['Duty'] = value
            rejected(data)


@case('温度边界与 DownT 的 255 不改变 UpT 哨兵语义')
def _():
    data = document()
    for which in ('CPU', 'GPU'):
        data[which][0]['DownT'] = 100
        data[which][2]['DownT'] = 255
        data[which][3]['UpT'] = 100
        data[which][4]['DownT'] = 0
    with fixture(data):
        result = fan_defaults.load_fan_defaults('M1T1')
        assert result['ok'] is True, result
        for which in ('CPU', 'GPU'):
            assert result['tables'][which][2]['sentinel'] is False
            assert result['tables'][which][4]['sentinel'] is True


@case('首点 UpT 和 Duty 必须都是 0')
def _():
    for which in ('CPU', 'GPU'):
        for field in ('UpT', 'Duty'):
            data = document()
            data[which][0][field] = 1
            rejected(data)


@case('至少两个活跃点，全哨兵和单活跃点拒绝')
def _():
    for which in ('CPU', 'GPU'):
        for count in (0, 1):
            data = document()
            for row in data[which][count:]:
                row.update(UpT=255, Duty=100)
            rejected(data)


@case('恰好两个活跃点或全部 16 点活跃均合法')
def _():
    for count in (2, 16):
        data = document()
        for which in ('CPU', 'GPU'):
            data[which] = [
                {'ID': i, 'UpT': i * 6 if i < count else 255,
                 'DownT': 0, 'Duty': 0 if i == 0 else 100}
                for i in range(16)
            ]
        with fixture(data):
            result = fan_defaults.load_fan_defaults('M1T1')
            assert result['ok'] is True, result


@case('哨兵只能在连续尾部出现')
def _():
    for which in ('CPU', 'GPU'):
        for index in (4, 8, 15):
            data = document()
            data[which][index]['UpT'] = 95
            if index == 4:
                data[which][3]['UpT'] = 255
            rejected(data)


@case('活跃温度不得回落')
def _():
    for which in ('CPU', 'GPU'):
        data = document()
        data[which][2]['UpT'] = 20
        rejected(data)


@case('全表 Duty 不得回落，包括哨兵和 GPU 尾部')
def _():
    for which in ('CPU', 'GPU'):
        for index in (2, 4, 14, 15):
            data = document()
            data[which][index]['Duty'] = 1
            rejected(data)


@case('末活跃点必须为 100%，尾部 100% 不能代替')
def _():
    for which in ('CPU', 'GPU'):
        data = document()
        data[which][3]['Duty'] = 99
        rejected(data)


@case('非法槽名及路径穿越先拒绝，绝不打开任何文件')
def _():
    bad_names = (None, True, 1, [], {}, '', 'M0T1', 'M4T1', 'M1T0', 'M1T6',
                 'M1T10', 'M11T1', 'm1t1', ' M1T1', 'M1T1 ', 'M1T1\n',
                 'M1T1\x00', 'M１T１', 'M1T1.json', '../M1T1', '..\\M1T1',
                 'M1T1/../../secret', 'C:/M1T1', 'C:\\M1T1', '/M1T1',
                 '\\\\server\\share\\M1T1', 'DefaultFanTable_Gaming')
    with fixture(), patch.object(Path, 'open', side_effect=AssertionError('打开了文件')), \
            patch('builtins.open', side_effect=AssertionError('打开了文件')), \
            patch('io.open', side_effect=AssertionError('打开了文件')):
        for name in bad_names:
            failed(fan_defaults.load_fan_defaults(name))


@case('只以二进制只读方式访问指定文件，无旁路配置或缓存文件')
def _():
    original_open = io.open
    for index, mode in enumerate(MODES, 1):
        with fixture(mode=mode) as path:
            before = path.read_bytes()
            calls = []

            def guarded_open(file, mode='r', *args, **kwargs):
                assert Path(file) == path, file
                assert mode == 'rb', mode
                calls.append((file, mode))
                return original_open(file, mode, *args, **kwargs)

            with patch('io.open', side_effect=guarded_open), \
                    patch('builtins.open', side_effect=guarded_open):
                for slot in range(1, 6):
                    result = fan_defaults.load_fan_defaults('M%dT%d' % (index, slot))
                    assert result['ok'] is True, result
            assert len(calls) == 5, calls
            assert path.read_bytes() == before
            assert list(path.parent.iterdir()) == [path]


@case('不复用可变结果或文件缓存，改动和删除源文件立即生效')
def _():
    with fixture() as path:
        first = fan_defaults.load_fan_defaults('M1T1')
        first['tables']['CPU'][1]['duty_pct'] = 99
        second = fan_defaults.load_fan_defaults('M1T1')
        assert second['tables']['CPU'][1]['duty_pct'] == 20
        data = document()
        data['CPU'][1]['Duty'] = 25
        path.write_text(json.dumps(data), encoding='utf-8')
        third = fan_defaults.load_fan_defaults('M1T1')
        assert third['tables']['CPU'][1]['duty_pct'] == 25
        path.unlink()
        failed(fan_defaults.load_fan_defaults('M1T1'))
        assert list(path.parent.iterdir()) == []


@case('模块依赖仅限只读加载需要的标准库，不导入硬件或配置')
def _():
    tree = ast.parse(inspect.getsource(fan_defaults))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not node.level
            imports.add(node.module)
    assert imports <= {'json', 'os', 'pathlib', 're'}, imports


def main():
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print('  ok  %s' % name)
        except AssertionError as exc:
            bad += 1
            print('  FAIL %s -> %r' % (name, exc))
        except Exception as exc:
            bad += 1
            print('  ERR  %s -> %s: %s' % (name, type(exc).__name__, exc))
    print('%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
