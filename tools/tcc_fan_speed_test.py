# -*- coding: utf-8 -*-
"""TCC 偏移 / 风扇换挡间隔的首次可逆验证（默认只读预演）。

    runtime\\python.exe tools\\tcc_fan_speed_test.py            # 只读，打印当前值
    runtime\\python.exe tools\\tcc_fan_speed_test.py --write    # 真下发，逐项还原

纸上预期（本机 2026-10-20 抓包，Fan/Status 原文）：
  * CPU_TccOffset=5、CPU_TccOffsetSwitch=0、CPU_TccOffsetMaximum=5；
  * FAN_FanSwitchSpeed=300、FAN_FanSwitchSpeedEnabled=0；
  * ⚠️ 服务收到「值」会**无条件**写使能位：`CpuTccOffset` → EC 0x0786=value|0x80，
    `FanSwitchSpeed` → EC 0x0787=(ms/100)|0x80，哪怕开关是 0。所以值必须在开关之前发，
    且还原时最后一步必须是「开关=0」；本脚本最后会直读 EC 两个字节确认都是 0x00。
  * TCC 报文：{"Action":"SET_OPERATING_MODE_DETAIL","CpuTccOffset":"N"}，
    使能位：{"Action":"SET_OPERATING_MODE_DETAIL","CpuTccOffsetSwitch":"1"|"0"}；
  * 风扇换挡报文：{"Action":"SET_OPERATING_MODE_DETAIL","FanSwitchSpeed":"N"}，
    使能位：{"Action":"SET_OPERATING_MODE_DETAIL","FanSwitchSpeedEnabled":"1"|"0"}。

验证顺序（全程可逆，不碰 PL/档位/风扇曲线）：
  1. TCC 值改成 0（不开开关；即使被意外应用也是 0 °C 偏移，不改变散热）；
  2. 开关置 1，读 Fan/Status 必须报 值=0、开关=1；
  3. 开关置 0、值恢复 5；
  4. 风扇换挡间隔改成 500 ms，开关置 1，读回 500/1；
  5. 开关置 0、间隔恢复 300。
任何一步失败都会在 finally 里把开关关掉、值写回基线。

安全闸：只认 Fan/Status 里 OEM 自己报的 TCC 上限；值域/100ms 步进在通道代码里
另有一道，但本脚本直接发 MQTT 原始报文，所以在这里再查一遍。
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import socket
import sys
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.act.channels.mqtt_gcu import MqttClient   # noqa: E402
from app.config import Config                      # noqa: E402

TOPIC = 'Fan/Control'
GET = {'Action': 'GETSTATUS'}
DEVICE = r'\\.\ACPIDriver'
IOCTL_ECREAD = 0x9C40A488
INVALID_HANDLE = ctypes.c_void_p(-1).value


def _ec_read(addr):
    """单次只读 EC（IOCTL_GPD_ACPI_ECREAD），用来核对两个使能位真的落到了 0。"""
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.DeviceIoControl.restype = wt.BOOL
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                    ctypes.c_void_p, wt.DWORD,
                                    ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    handle = k32.CreateFileW(DEVICE, 0x80000000 | 0x40000000, 3, None, 3, 0, None)
    if handle == INVALID_HANDLE or handle is None:
        raise OSError('打不开 %s（错误 %d）' % (DEVICE, ctypes.get_last_error()))
    try:
        inbuf = ctypes.c_uint32(addr)
        outbuf = ctypes.c_uint32(0)
        returned = wt.DWORD(0)
        ok = k32.DeviceIoControl(handle, IOCTL_ECREAD, ctypes.byref(inbuf), 4,
                                 ctypes.byref(outbuf), 4, ctypes.byref(returned), None)
        if not ok:
            raise OSError('ECREAD 0x%X 失败（错误 %d）' % (addr, ctypes.get_last_error()))
        return outbuf.value & 0xFF
    finally:
        k32.CloseHandle(handle)


def _num(status, key):
    try:
        return int(str(status[key]))
    except (KeyError, TypeError, ValueError):
        return None


def _roundtrip(ident, messages, wait_s=2.5, attempts=3):
    """短连接：发命令 + GETSTATUS，收到 Fan/Status 就断开。

    面板用同一个 clientId，会和我们互踢；所以每次只连一小会儿，失败重试。
    """
    last = None
    for _ in range(attempts):
        cli = MqttClient('127.0.0.1', 13688, ident['client_id'],
                         ident['username'], ident['password'])
        try:
            cli.connect(timeout=2.5)
            cli.subscribe(['Fan/Status'])
            cli.sock.settimeout(0.4)
            for msg in list(messages) + [GET]:
                cli.publish(TOPIC, json.dumps(msg, ensure_ascii=False))
            end = time.time() + wait_s
            while time.time() < end:
                try:
                    code, body = cli.read_packet()
                except socket.timeout:
                    continue
                if code & 0xF0 != 0x30 or len(body) < 2:
                    continue
                tlen = int.from_bytes(body[:2], 'big')
                topic = body[2:2 + tlen].decode('utf-8', 'replace')
                if topic != 'Fan/Status':
                    continue
                try:
                    data = json.loads(body[2 + tlen:].decode('utf-8', 'replace') or '{}')
                except ValueError:
                    continue
                if isinstance(data, dict) and data:
                    return data
        except (OSError, ConnectionError) as exc:      # noqa: PERF203
            last = exc
            time.sleep(0.8)
        finally:
            cli.close()
    raise ConnectionError('连 broker 三次都没拿到 Fan/Status：%r' % (last,))


def _wait(ident, predicate, timeout=8.0):
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        last = _roundtrip(ident, [])
        if predicate(last):
            return True, last
        time.sleep(0.8)
    return False, last


def _baseline(status):
    return {
        'tcc_value': _num(status, 'CPU_TccOffset'),
        'tcc_on': _num(status, 'CPU_TccOffsetSwitch'),
        'tcc_max': _num(status, 'CPU_TccOffsetMaximum'),
        'speed_ms': _num(status, 'FAN_FanSwitchSpeed'),
        'speed_on': _num(status, 'FAN_FanSwitchSpeedEnabled'),
    }


def _show(tag, status):
    b = _baseline(status)
    print('%s：TCC 值=%s 开关=%s 上限=%s；换挡=%s ms 开关=%s' % (
        tag, b['tcc_value'], b['tcc_on'], b['tcc_max'], b['speed_ms'], b['speed_on']))
    return b


def _send(ident, **fields):
    msg = {'Action': 'SET_OPERATING_MODE_DETAIL'}
    msg.update(fields)
    status = _roundtrip(ident, [msg])
    return status


def main(argv=None):
    ap = argparse.ArgumentParser(description='TCC 偏移与风扇换挡间隔的首次可逆验证')
    ap.add_argument('--write', action='store_true', help='真的下发（默认只读）')
    args = ap.parse_args(argv)

    cfg, err, _ = Config.load()
    if err:
        print('配置读取失败：%s' % err)
        return 2
    mqtt_cfg = cfg.get('hardware', 'mqtt', default={}) or {}
    ident = {k: mqtt_cfg.get(k) for k in ('client_id', 'username', 'password')}
    if not all(ident.values()):
        print('没有 broker 身份（data\\mqtt_identity.json），无法继续。')
        return 2

    print('== 1. 读基线 ==')
    try:
        base_status = _roundtrip(ident, [])
    except Exception as exc:                            # noqa: BLE001
        print('读 Fan/Status 失败：%r' % (exc,))
        return 3
    base = _show('基线', base_status)
    if None in (base['tcc_value'], base['tcc_on'], base['tcc_max'],
                base['speed_ms'], base['speed_on']):
        print('Fan/Status 缺字段，拒绝继续（本机应报 TCC 5/0/5、换挡 300/0）。')
        return 3
    if base['tcc_on'] != 0 or base['speed_on'] != 0:
        print('基线不是「两个开关都关」；为了不叠加别人的设置，先手动确认再跑。')
        return 3
    if base['speed_ms'] % 100:
        print('当前换挡间隔不是 100 的整数倍，和 EC 落点公式对不上，拒绝继续。')
        return 3

    print('\n== 2. 计划 ==')
    print('TCC：值 %d→0，开关 0→1→0，值恢复 %d（只影响几秒，且 0 °C 偏移不改变散热）'
          % (base['tcc_value'], base['tcc_value']))
    print('换挡：间隔 %d→500 ms，开关 0→1→0，间隔恢复 %d'
          % (base['speed_ms'], base['speed_ms']))
    if not args.write:
        print('\n只读预演结束。要真下发请加 --write。')
        return 0

    touched = {'tcc': False, 'speed': False}
    try:
        print('\n== 3. TCC 值 → 0 ==')
        # 服务收到值就写 EC 使能位，所以从这条开始就必须由 finally 兜底。
        touched['tcc'] = True
        st = _send(ident, CpuTccOffset='0')
        _show('下发后', st)
        print('\n== 4. TCC 开关 → 1 ==')
        st = _send(ident, CpuTccOffsetSwitch='1')
        ok, st = _wait(ident, lambda s: _num(s, 'CPU_TccOffsetSwitch') == 1
                       and _num(s, 'CPU_TccOffset') == 0)
        _show('回读', st)
        if not ok:
            print('FAIL：TCC 开关没有回读成 1 / 值没有回读成 0。')
            return 4

        print('\n== 5. TCC 值恢复 → 再关开关（顺序不能反）==')
        # 服务收到值会写 value|0x80，所以先把值恢复，最后一步才是开关 0。
        _send(ident, CpuTccOffset=str(base['tcc_value']))
        st = _send(ident, CpuTccOffsetSwitch='0')
        touched['tcc'] = False
        ok, st = _wait(ident, lambda s: _num(s, 'CPU_TccOffsetSwitch') == 0
                       and _num(s, 'CPU_TccOffset') == base['tcc_value'])
        _show('回读', st)
        if not ok:
            print('FAIL：TCC 开关没有回读成 0 / 值没有回到基线。')
            return 4

        print('\n== 6. 风扇换挡间隔 → 500 ms，开关 → 1 ==')
        touched['speed'] = True
        _send(ident, FanSwitchSpeed='500')
        st = _send(ident, FanSwitchSpeedEnabled='1')
        ok, st = _wait(ident, lambda s: _num(s, 'FAN_FanSwitchSpeedEnabled') == 1
                       and _num(s, 'FAN_FanSwitchSpeed') == 500)
        _show('回读', st)
        if not ok:
            print('FAIL：换挡间隔没有回读成 500/1。')
            return 4

        print('\n== 7. 换挡间隔恢复 → 再关开关（顺序不能反）==')
        _send(ident, FanSwitchSpeed=str(base['speed_ms']))
        _send(ident, FanSwitchSpeedEnabled='0')
        touched['speed'] = False
        ok, st = _wait(ident, lambda s: _num(s, 'FAN_FanSwitchSpeedEnabled') == 0
                       and _num(s, 'FAN_FanSwitchSpeed') == base['speed_ms'])
        _show('回读', st)
        if not ok:
            print('FAIL：换挡开关没有回读成 0 / 间隔没有回到基线。')
            return 4

        print('\n== 8. 最终核对（Fan/Status + EC 直读）==')
        ok, st = _wait(ident, lambda s: (_num(s, 'CPU_TccOffsetSwitch') == 0
                                         and _num(s, 'CPU_TccOffset') == base['tcc_value']
                                         and _num(s, 'FAN_FanSwitchSpeedEnabled') == 0
                                         and _num(s, 'FAN_FanSwitchSpeed') == base['speed_ms']))
        _show('最终', st)
        try:
            ec786, ec787 = _ec_read(0x0786), _ec_read(0x0787)
        except Exception as exc:                        # noqa: BLE001
            print('EC 直读失败：%r；Fan/Status 已回到基线，但没能独立核对 EC。' % (exc,))
            return 5
        print('EC 直读：0x0786=0x%02X（应为 0x00），0x0787=0x%02X（应为 0x00）' % (ec786, ec787))
        if not ok:
            print('FAIL：最终 Fan/Status 没有回到基线，请用 Creator Center / 面板再核对。')
            return 5
        if ec786 != 0 or ec787 != 0:
            print('FAIL：Fan/Status 说关了，但 EC 使能位还在——服务没有真正落 EC，拒绝判通过。')
            return 5
        print('\n通过：TCC 值与开关、风扇换挡间隔与开关都能下发并回读；'
              '最终 Fan/Status 与 EC 0x0786/0x0787 都回到基线。')
        return 0
    finally:
        if touched['tcc'] or touched['speed']:
            print('\n[保险] 异常退出，正在恢复值并把开关关掉…')
            try:
                if touched['tcc']:
                    _send(ident, CpuTccOffset=str(base['tcc_value']))
                    _send(ident, CpuTccOffsetSwitch='0')
                if touched['speed']:
                    _send(ident, FanSwitchSpeed=str(base['speed_ms']))
                    _send(ident, FanSwitchSpeedEnabled='0')
                print('[保险] 已下发还原（开关最后发），请用面板回读再核对一次。')
            except Exception as exc:                    # noqa: BLE001
                print('[保险] 还原失败：%r —— 请立即在 Creator Center 里手动关闭！' % (exc,))


if __name__ == '__main__':
    sys.exit(main())
