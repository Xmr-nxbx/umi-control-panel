# -*- coding: utf-8 -*-
"""精细功耗墙（`SET_OPERATING_MODE_DETAIL`）的在场可逆验证。

    runtime\\python.exe tools\\pl_detail_test.py --PL1=60            # 只打计划，一个字都不发
    runtime\\python.exe tools\\pl_detail_test.py --PL1=60 --write     # 机主在场时才加 --write

**这条路径的来历**（notes/hardware-channels.md 6.11 十二/十五）：能改功耗墙的只有
OEM 自己那条 `Fan/Control {"Action":"SET_OPERATING_MODE_DETAIL","PL1":".."}`。
裸写 EC `0x0783-0x0785` 会被服务盖回去，`0x0751` 是服务的输出不是输入。
而服务端对这三个值**一个字节都不校验**（`Convert.ToInt32` 之后直接 `(byte)` 截断，
发 300 静默变成 44 W），所以护栏在 `MqttChannel.set_power_limits` 里。本脚本只调用它，
不重复实现也不绕过——护栏被绕过才是这次验证真正要防的事故。

跑之前必须停面板（同一个 clientId 只许一条 MQTT 连接，后到的顶掉先到的，两边互踢就
什么都测不出来）。脚本会先探测 127.0.0.1:8747 有没有人应答，有人应答就不动手。

判据是 **EC 直读那个设置字节真的变了**，不是「命令发出去了」：档位命令实测 9~26 秒才落到
EC，所以默认等各 60 秒。还原也走同一条命令发回原值；但 `0x0783-0x0785` 还有 ACPI 侧写者
（`T1WR 0x81/0x82/0x84`），**还原值可能本来就不是原值**，报告里说清这一点，不做
「读回来一样 = 一定没人动过」那种乐观解释。

安全边界：
  * 温度 ≥85°C 立刻中止采样并转入还原；累计发满 5 次就不再发第 6 次；
  * 只发 `SET_PL_DETAIL` 和 `GETSTATUS`。**不发档位命令**（它会整块重写风扇表）、
    不碰充电门控、不发 IOCTL、EC 侧全程只读（结尾会把 `dev.writes` 打出来自查）；
  * 默认 dry-run，`--write` 才发；
  * 首次验证能越过第三道闸（cap 必须 verified）是因为 `send_action` 那道判断防的是
    「不许从 HTTP 发没验过的动作」。这里把 flag 打在**本进程的通道对象**上，面板进程
    一点没受影响，执行层那道闸的语义也没被削弱。
"""
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels.base import CAP_PL_WRITE        # noqa: E402
from app.act.channels.ec_gpd import EcChannel         # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel     # noqa: E402
from app.config import Config                         # noqa: E402
from app.logx import Log                              # noqa: E402
from app.sense.clock import ClockSense                # noqa: E402
from app.sense.thermal import Thermal                 # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'pl-detail-test.txt')
PANEL_ADDR = ('127.0.0.1', 8747)
TEMP_GUARD_C = 85.0
MAX_SENDS = 5
POLL_S = 2.0
# (PL 名, EC 侧读它的寄存器名, Fan/Status 侧对应字段)
PL_KEYS = (('PL1', 'ADDR_PL1_SETTING_VALUE', 'CPU_PL1'),
           ('PL2', 'ADDR_PL2_SETTING_VALUE', 'CPU_PL2'),
           ('PL4', 'ADDR_PL4_SETTING_VALUE', 'CPU_PL4'))
EC_FIELD = {'PL1': 'pl1_setting', 'PL2': 'pl2_setting', 'PL4': 'pl4_setting'}
FAN_FIELD = dict((n, f) for n, _, f in PL_KEYS)


def panel_alive():
    try:
        socket.create_connection(PANEL_ADDR, timeout=1.0).close()
        return True
    except OSError:
        return False


class Report:
    def __init__(self):
        self.lines = []

    def out(self, text=''):
        line = '%s %s' % (time.strftime('%H:%M:%S'), text) if text else ''
        print(line)
        self.lines.append(line)

    def save(self):
        try:
            os.makedirs(os.path.dirname(REPORT), exist_ok=True)
            with open(REPORT, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.lines) + '\n')
        except OSError:
            pass
        print('\n报告：%s' % REPORT)


def ec_snapshot(ec, rep=None):
    """强制做一次快读，返回快照。EC 是本次验证唯一的裁判，不能拿缓存值糊弄。"""
    ec._last_fast = 0.0
    ec.tick()
    snap = ec.read()
    if rep:
        rep.out('  EC 直读：%s｜风扇 %s RPM、占空 %s%%' % (
            ' '.join('%s=%s' % (n, snap.get(EC_FIELD[n])) for n in EC_FIELD),
            snap.get('fan_rpm'), snap.get('fan_duty_l')))
    return snap


def fan_status(mqtt):
    return (mqtt.payloads().get('Fan/Status') or {}).get('data') or {}


def wait_for(rep, mqtt, ec, thermal, targets, timeout_s, label):
    """等 EC 读数变成 targets 里的值。返回 (命中名列表, 末次快照, 是否温度中止)。"""
    hit, aborted, snap = [], False, ec.read()
    end = time.time() + timeout_s
    while time.time() < end:
        time.sleep(POLL_S)
        mqtt.tick()
        snap = ec_snapshot(ec)
        temp, _ = thermal.read()
        fan = fan_status(mqtt)
        for name, expect in targets.items():
            got = snap.get(EC_FIELD[name])
            if str(got) == str(expect) and name not in hit:
                hit.append(name)
                rep.out('  [%s] %s → EC 直读 %s；Fan/Status %s=%s；温度 %s°C' % (
                    label, name, got, FAN_FIELD[name], fan.get(FAN_FIELD[name]), temp))
        if temp and temp >= TEMP_GUARD_C:
            rep.out('  [温度保险] %s°C ≥ %.0f°C，停止采样并转入还原' % (temp, TEMP_GUARD_C))
            aborted = True
            break
        if len(hit) == len(targets):
            break
    return hit, snap, aborted


def parse(argv):
    want, bad = {}, None
    for arg in argv:
        for name, _, _ in PL_KEYS:
            if arg.startswith('--%s=' % name):
                try:
                    want[name] = int(arg.split('=', 1)[1])
                except ValueError:
                    bad = '%s 不是整数' % arg
    return want, bad


def main(argv):
    low = [a.lower() for a in argv]
    write = '--write' in low
    restore = '--no-restore' not in low
    timeout_s = next((float(a.split('=', 1)[1]) for a in argv
                      if a.startswith('--seconds=')), 60.0)
    hold_s = next((float(a.split('=', 1)[1]) for a in argv if a.startswith('--hold=')), 15.0)
    want, bad = parse(argv)
    rep, sends = Report(), 0
    res = {'thermal': None, 'clock': None, 'mqtt': None, 'ec': None}

    def done(code):
        return code

    rep.out('功耗墙 SET_OPERATING_MODE_DETAIL 验证  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    rep.out('模式=%s｜目标=%s｜还原=%s｜等待生效 %.0fs' % (
        '真下发' if write else '演练（不发）', want or '未给值',
        '是' if restore else '否（--no-restore）', timeout_s))
    try:
        if bad:
            rep.out('[停止] %s' % bad)
            return done(2)
        if not want:
            rep.out('[停止] 至少给一个 --PL1=/--PL2=/--PL4=。不给值就发命令没有意义。')
            return done(2)
        if '--no-restore' in low and not write:
            rep.out('[停止] --no-restore 只在 --write 时才有意义，演练本来就不发。')
            return done(2)
        if panel_alive():
            rep.out('[停止] 面板还在 127.0.0.1:8747 上跑，会和本脚本抢同一个 MQTT clientId、互相踢线。')
            rep.out('        先停：curl -s -X POST -H "Origin: http://127.0.0.1:8747" '
                    'http://127.0.0.1:8747/api/shutdown')
            return done(3)

        cfg, _err, _bad = Config.load()
        log = Log('pl-detail-test')
        mqtt, ec = MqttChannel(cfg, log), EcChannel(cfg, log)
        res['mqtt'], res['ec'] = mqtt, ec
        thermal, clock = Thermal(), ClockSense()
        res['thermal'], res['clock'] = thermal, clock

        detail = ec.probe()
        rep.out('EC 通道=%s｜MQTT enabled=%s broker=%s｜allow_write=%s' % (
            detail.get('state'), mqtt.enabled, mqtt.detail.get('broker'), mqtt.allow_write))
        if not ec.alive:
            rep.out('[停止] EC 直连不可用就没有独立裁判：只靠 Fan/Status 自己报自己写的值不算验证。')
            return done(1)
        if not mqtt.allow_write:
            rep.out('[停止] config.hardware.ec.allow_write=false，第一道闸本来就该拦住。')
            return done(1)
        mqtt.start()
        end = time.time() + 12.0
        while not mqtt.alive and time.time() < end:
            time.sleep(0.5)
        if not mqtt.alive:
            rep.out('[停止] 连不上 GCUBridge（%s）。GCUService 没起就先去起它，别改配置硬试。'
                    % mqtt.detail.get('reason'))
            return done(1)
        rep.out('MQTT 已连通，等 Fan/Status 报边界（它被问到才报，不是周期广播）…')
        end = time.time() + 25.0
        while not fan_status(mqtt) and time.time() < end:
            mqtt.request_status()
            mqtt.tick()
            time.sleep(POLL_S)
        fan = fan_status(mqtt)
        if not fan:
            rep.out('[停止] 25 秒拿不到 Fan/Status。拿不到 OEM 边界就没有护栏，没护栏不发——'
                    '这是设计，不是故障。')
            return done(1)
        lim = mqtt.pl_limits()
        rep.out('OEM 报的边界：%s' % (lim or '字段不全'))
        rep.out('Fan/Status 里带 PL 的字段：%s' % json.dumps(
            dict((k, v) for k, v in fan.items() if 'pl' in k.lower()), ensure_ascii=False))

        before = ec_snapshot(ec, rep)
        orig = dict((name, before.get(EC_FIELD[name])) for name in want)
        rep.out('EC 原值：%s｜寄存器 %s' % (
            orig, ' '.join('%s=0x%04X' % (n, ec.addr(a)) for n, a, _ in PL_KEYS if n in want)))
        rep.out('当前档位：Tray/Status OperatingMode=%s，面板解成 %s（只记录，不发档位命令）' % (
            mqtt.detail.get('operating_mode_raw'), mqtt.read().get('mode')))

        rep.out('')
        rep.out('--- 会发出去的东西 ---')
        rep.out('  主题 Fan/Control：{"Action":"SET_OPERATING_MODE_DETAIL", %s}' %
                ', '.join('"%s":"%s"' % (k, v) for k, v in sorted(want.items())))
        if not write:
            rep.out('[演练] 没加 --write，一个字都没发。机主在场、看到上面这行数字认可，再加 --write。')
            return done(0)

        # 见文件头「安全边界」最后一条：这个 flag 只属于本进程的这一次在场验证。
        mqtt.caps[CAP_PL_WRITE] = 'verified'
        # 形参是 pl1/pl2/pl4（小写），报文里的键由白名单条目决定，这里只做一次换算
        ok, msg = mqtt.set_power_limits(**dict((k.lower(), v) for k, v in want.items()))
        sends += 1
        rep.out('')
        rep.out('=== 下发目标值（已下发）===')
        rep.out('  %s｜%s' % (msg, '命令进队列' if ok else '被护栏拒绝'))
        if not ok:
            rep.out('[停止] 护栏拒发就是本次验证的结论之一：它按设计工作了，不绕过。')
            return done(1)
        hit, after, aborted = wait_for(rep, mqtt, ec, thermal, want, timeout_s, '生效')
        rep.out('  等待结束 EC=%s' % ' '.join('%s=%s' % (n, after.get(EC_FIELD[n])) for n in want))
        rep.out('  判读：%s' % ('全部命中 → SET_PL_DETAIL 确实能改墙'
                                if len(hit) == len(want) else
                                '只有 %s 动了；没动的只报现象、不猜原因' % (hit or '没有')))

        if not restore:
            rep.out('')
            rep.out('[注意] --no-restore：墙留在 %s，没有还原。下次切档服务会自己重算。' % want)
            return done(0)

        if hold_s > 0 and not aborted:
            rep.out('')
            rep.out('保持 %.0f 秒，看服务会不会自己把它改回去…' % hold_s)
            time.sleep(hold_s)
            ec_snapshot(ec, rep)
        rep.out('')
        rep.out('=== 还原（发回 EC 读到的原值 %s）===' % orig)
        back = dict((k, int(v)) for k, v in orig.items() if v is not None)
        if not back:
            rep.out('[警告] 原值一个都没读到，**不凭空造还原值**。请手动按一次档位键让服务重算。')
            return done(1)
        if len(back) != len(orig):
            rep.out('[警告] 原值里有读不到的：%s，只还原读到的 %s' % (
                [k for k, v in orig.items() if v is None], sorted(back)))
        if sends >= MAX_SENDS:
            rep.out('[熔断] 已发满 %d 次，不再发第 %d 次。请手动切一次档位复位。' % (MAX_SENDS, sends + 1))
            return done(1)
        ok2, msg2 = mqtt.set_power_limits(**dict((k.lower(), v) for k, v in back.items()))
        sends += 1
        rep.out('  %s｜%s' % (msg2, '命令进队列' if ok2 else '被拒绝'))
        got, rest, _ = wait_for(rep, mqtt, ec, thermal, back, timeout_s, '还原')
        rep.out('  判读：%s' % ('EC 回到原值，整条链路可逆'
                                if len(got) == len(back) else
                                'EC=%s 没回到 %s。注意 0x0783-0x0785 另有 ACPI 侧写者，'
                                '回不去不等于写坏了，也可能是服务按当前档位重算了默认墙' % (
                                    ' '.join('%s=%s' % (n, rest.get(EC_FIELD[n])) for n in back), back)))

        rep.out('')
        rep.out('--- 累计 ---')
        rep.out('  SET_PL_DETAIL 下发 %d 次；EC 读 %d 次、写 %d 次（设计上应为 0）' % (
            sends, ec.dev.reads, ec.dev.writes))
        if ec.dev.writes:
            rep.out('  [异常] EC 侧出现过写操作，本脚本不该写任何东西，请把这条当真。')
        rep.out('  收尾读数：频率=%s MHz 温度=%s°C' % (clock.read(), thermal.read()[0]))
        return done(0)
    finally:
        for key in ('thermal', 'clock', 'mqtt', 'ec'):
            try:
                if res[key] is not None:
                    res[key].close()
            except Exception:                   # noqa: BLE001
                pass
        rep.save()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
