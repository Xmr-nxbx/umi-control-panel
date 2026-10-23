# -*- coding: utf-8 -*-
"""生成本机 EC 寄存器表 → data/ec_map.local.json（已 gitignore，不进仓库）。

为什么要有这一步：EC 的地址表属于 OEM 私有定义，README 第 8 节承诺不在仓库里分发。
所以仓库里只有「按名字取地址」的代码，名字→地址的对应关系由每台机器
从自己安装的 Creator Center 现场生成。换机型也能用：生成的是本机那份表。

流程（全程只读，不加载执行 OEM 代码、不碰设备）：
  1. 找到 GCUService.exe（.NET 程序集，元数据里有常量与枚举）；
  2. 调 tools/dotnet_probe.py（C# 反射工具）导出常量；
  3. 整理成 {registers, enums, ioctls} 写进 data/ec_map.local.json。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.paths import data_path  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dotnet_probe  # noqa: E402

OUT_NAME = 'ec_map.local.json'

REGISTER_TYPE = 'Define.ECSpec'
IOCTL_TYPE = 'MyECIO.AcpiCtrl'
ENUM_TYPES = ('Define.ECSpec+MyFanCTLByteFlag', 'Define.ECSpec+MyFan2SpeedByteFlag',
              'Define.GCU2_Define+OperatingMode', 'Define.IntelDefine+SysPowerModeIndex',
              'Define.ProjectID', 'Define.PowerPlan')


def gcu_candidates():
    roots = dict.fromkeys(os.environ.get(key) for key in
                         ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'))
    return [os.path.join(root, 'OEM', 'CreatorCenter', 'UniwillService',
                        'MyControlCenter', 'GCUService.exe') for root in roots if root]


def find_gcu(explicit=None):
    for p in ([explicit] if explicit else []) + gcu_candidates():
        if p and os.path.exists(p):
            return p
    return None


def run_dump(gcu, tmp_json):
    text = dotnet_probe.run(['constants', '-Path', gcu, '-OutJson', tmp_json], timeout=300)
    if not os.path.exists(tmp_json):
        raise RuntimeError('反射导出没有产出 JSON：%s' % text[-800:])
    return text


def build(gcu):
    tmp = data_path('_oem_constants.tmp.json')
    try:
        run_dump(gcu, tmp)
        with open(tmp, encoding='utf-8-sig') as f:
            raw = json.load(f)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    types = raw.get('types') or {}

    def values_of(name):
        node = types.get(name) or {}
        return node.get('values') or {}

    regs = {k: int(v) for k, v in values_of(REGISTER_TYPE).items() if isinstance(v, (int, float))}
    ioctls = {k: int(v) for k, v in values_of(IOCTL_TYPE).items()
              if k.startswith('IOCTL_') and isinstance(v, (int, float))}
    enums = {}
    for t in ENUM_TYPES:
        vals = values_of(t)
        if vals:
            enums[t.split('+')[-1]] = {k: int(v) for k, v in vals.items()
                                       if isinstance(v, (int, float))}
    # ECSpec 里混着「地址」和「模式取值」，按前缀分开，方便通道代码取用
    modes = {k: regs.pop(k) for k in list(regs) if k.startswith(('FAN_', 'TURBO_MODE', 'POWER_SETTING_MODE', 'QKEY_'))}
    return {'source': gcu, 'generated_by': 'tools/gen_ec_map.py',
            'registers': regs, 'mode_values': modes, 'enums': enums, 'ioctls': ioctls}


def main(argv):
    gcu = find_gcu(argv[0] if argv else None)
    if not gcu:
        print('找不到 GCUService.exe（Creator Center 的服务端）。')
        print('已找过：%s' % '；'.join(([argv[0]] if argv else []) + gcu_candidates()))
        print('可以用参数指定完整路径后重试。')
        return 1
    print('来源：%s' % gcu)
    try:
        table = build(gcu)
    except Exception as exc:                            # noqa: BLE001
        print('[失败] 生成寄存器表出错：%r' % (exc,))
        return 1
    path = data_path(OUT_NAME)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(table, f, ensure_ascii=False, indent=1, sort_keys=True)
    print('已写出：%s' % path)
    print('  寄存器地址 %d 个，模式取值 %d 个，枚举 %d 组，IOCTL %d 个' % (
        len(table['registers']), len(table['mode_values']), len(table['enums']),
        len(table['ioctls'])))
    for key in ('ADDR_EC_MAIN_FAN_RPM_BYTE1', 'ADDR_EC_MAIN_FAN_RPM_BYTE2',
                'ADDR_PL1_SETTING_VALUE', 'ADDR_PROJECT_ID_BYTE', 'ecBt1RSOC'):
        print('    %-32s = %s' % (key, table['registers'].get(key)))
    print('  IOCTL：' + ', '.join('%s=0x%X' % (k.replace('IOCTL_GPD_ACPI_', ''), v)
                                  for k, v in sorted(table['ioctls'].items())[:8]))
    print('\n这个文件在 data/ 下（已 gitignore），不会进仓库。')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
