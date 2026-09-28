r"""EC 只读验证：单次、完整缓冲、只发读方法（ECRR）。

目的只有一个：确认我们拼的 ACPI_EVALUATE_INPUT 布局对不对、回包是不是 AeoB。
这是离线静态分析（驱动代码段里的 AeiC/AeoB/ECRR 常量）之后唯一允许的一次实测。

硬性约束：
  * 只调用一次 DeviceIoControl（EcDevice 内有熔断计数）；
  * 只使用读方法 ECRR，绝不发 ECRW/SMRW；
  * 失败不再重试，把原始字节写进 tools\out\ec-read-test.txt 供离线分析。

用法：runtime\\python.exe tools\\ec_read_test.py [EC地址] [长度]
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.ec_acpi import (EcDevice, IOCTL_EVAL_ASYNC, IOCTL_GENERIC_BUFFERED,
                                      parse_output)

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'tools', 'out')


def hexdump(data, limit=96):
    out = []
    for i in range(0, min(len(data), limit), 16):
        row = data[i:i + 16]
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
        out.append('  %04X  %-47s  %s' % (i, ' '.join('%02X' % b for b in row), asc))
    if len(data) > limit:
        out.append('  ... 共 %d 字节' % len(data))
    return '\n'.join(out) or '  (空)'


def main():
    addr = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x0
    length = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    dev = EcDevice()
    lines = ['EC 只读验证  %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
             '地址=0x%04X 长度=%d' % (addr, length),
             '安全约束：本进程最多发送 1 次请求，只使用读方法 ECRR',
             '']
    if not dev.open():
        lines.append('[失败] %s' % dev.open_error)
        lines.append('说明：设备打不开时不要继续，先解决权限或设备存在性问题。')
        return _finish(lines)
    lines.append('设备：%s 已打开（只开句柄，未发请求）' % dev.device)
    lines.append('')

    parsed, err = dev.request_read(addr, length, ioctl=IOCTL_EVAL_ASYNC, max_attempts=1)
    if parsed is None:
        lines.append('[失败] IOCTL=0x%08X → %s' % (IOCTL_EVAL_ASYNC, err))
        lines.append('')
        lines.append('按安全规则停手：不换码、不重试、不改缓冲长度瞎试。')
        lines.append('下一步应回到离线分析（把驱动镜像交给我做定向反汇编）。')
    else:
        lines.append('[有回包] IOCTL=0x%08X，返回 %d 字节' % (IOCTL_EVAL_ASYNC, parsed['size']))
        lines.append('  签名=%r 版本=%s 参数个数=%s 状态=%s' % (
            parsed['sig'], parsed.get('version'), parsed.get('arg_count'),
            parsed.get('status')))
        lines.append('  ok=%s' % parsed['ok'])
        lines.append('  原始回包：')
        lines.append(hexdump(dev.last_raw))
        if parsed['args']:
            for n, arg in enumerate(parsed['args']):
                lines.append('  参数%d（%d 字节）：' % (n, len(arg)))
                lines.append(hexdump(arg))
        if not parsed['ok']:
            lines.append('')
            lines.append('判读：签名不是 AeoB → 请求布局仍需离线校准；已用完本次配额，不再发。')
    return _finish(lines)


def _finish(lines):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, 'ec-read-test.txt')
    text = '\n'.join(lines)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                     # noqa: BLE001
        pass
    print(text)
    print('\n[report] %s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
