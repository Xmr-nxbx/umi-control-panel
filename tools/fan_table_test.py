# -*- coding: utf-8 -*-
"""风扇表写入的在场可逆验证（#31）。

    runtime\\python.exe tools\\fan_table_test.py --which=CPU --raise=15        # 只打计划
    runtime\\python.exe tools\\fan_table_test.py --which=CPU --raise=15 --write  # 机主在场、耳朵有空

为什么默认不写：这条路径一旦写错是**听得见**的，而且 EC 有两种静默失败——
（1）表不合法时它**整块拒绝**并退回内置曲线，读数照样能读回我们写的值；
（2）写的时候和厂商服务抢同一个档位，服务切档会把整张表重写。
所以判据不是「回读相等」，而是**转速在 30~40 秒的平滑之后真的跟着新占空比走**。

两条 EC 硬规则由 `app/fanrules.validate` 把关（用例见 tests/test_fan_rules.py）：
点 0 的占空比必须是 0 %、占空比必须单调不减。违反任一条 EC 会丢掉整张表。
地址布局同样由它负责：点 k 的升温在 `base+k-1`（点 0 没有存储位）、
降温在 `base+0x11+k`（点 15 与 14 共用 `base+0x1F`）、占空比在 `base+0x20+k`（原始值 = %×2），
GPU 点 13/14/15 的占空比槽是厂商信箱、不写。括号 `0x07C6` bit2 写表前清、写完置。

安全边界：
  * 只写计划里那些地址：风扇表区间 + `0x07C6`，别的地址一律拒（禁写清单靠这个结构性白名单兜）；
  * 温度 ≥85°C 或失败连续 5 次 → 立刻停手并还原；
  * 必须接电源（`power_status()['on_ac']`）；
  * 跑之前停面板：两个进程同时开 EC 句柄写同一张表没有意义，而且面板每 45 秒的
    MQTT 追问会让日志里混进「不是本面板写的」噪音；
  * 写完一定还原，还原后一定再采一轮转速做对照。
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.fanrules as fanrules                          # noqa: E402
from app.act.channels.ec_gpd import FAN_TABLE_BASE       # noqa: E402
from app.act.channels.ec_gpd import EcChannel            # noqa: E402
from app.act.channels.mqtt_gcu import MqttChannel        # noqa: E402
from app.bench import Bench                              # noqa: E402
from app.config import Config                            # noqa: E402
from app.logx import Log                                 # noqa: E402
from app.paths import discover_panel_port                # noqa: E402
from app.sense.clock import ClockSense                   # noqa: E402
from app.sense.gpu import GpuSense                       # noqa: E402
from app.sense.system import power_status                # noqa: E402
from app.sense.thermal import Thermal                    # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'fan-table-test.txt')
TEMP_GUARD_C = 85.0
GAP_S = 0.05            # 每字节之间的节流：EC 是慢设备，狂写会丢字节
OBSERVE_S = 45.0        # EC 平滑 30~40 秒，短了量不到
LOAD_S = 40.0           # 只给 CPU 表用：0xA0 档实测点 1 触发温度 53 °C、空载只有 47 °C
MODE_WAIT_S = 40.0      # 切档实测 24 秒生效，留到 40 秒
DUTY_MATCH_PCT = 3.0    # 实测占空与表里某个档位差多少以内，算「落在表上」
EC_DUTY_FLOOR = 30.0    # 固件常量地板 0x3C：目标为 0 且退避计数没归零时强制给 30 %
                        # （逆向仓库 ec/decompiled/bank0/8C46.asm:99-109）。空转就读到它，
                        # 目标持续为 0 约 180 秒后退避计数归零，才允许风扇真停转。
FAIL_BREAK = 5
BASE_ADDR = dict(FAN_TABLE_BASE)
RPM_KEY = {'CPU': 'fan_rpm', 'GPU': 'fan2_rpm'}
DUTY_KEY = {'CPU': 'fan_duty_l', 'GPU': 'fan_duty_r'}   # 左右各一路：测 GPU 表看右路


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


def panel_alive(port=None):
    port = discover_panel_port(port)
    try:
        socket.create_connection(('127.0.0.1', port), timeout=1.0).close()
        return True
    except ConnectionRefusedError:
        return False


def in_table_range(which, addr):
    """结构性白名单：只允许那张表的区间和括号字节，其余一律 False。

    区间上界取 `base+0x2F`（点 15 的占空比槽）——写宽一点就会越界：CPU 的 `base+0x3F`
    正好盖到 GPU 表的头几格，两张表同时被写就是两场实验混在一起。
    """
    base = BASE_ADDR[which]
    if addr == fanrules.BRACKET_ADDR:
        return True
    return base <= addr <= base + 0x2F


def modified(points, delta):
    """把点 1 以后的占空比整体平移 delta（上封 100、下封 0，两个方向都要能测）。

    整体平移是为了保住「单调不减」这条硬规则：原表单调，逐点加同一个数再截顶仍然单调。
    点 0 必须留在 0 %（另一条硬规则），温度阈值一个都不动——动它们等于改触发点，
    量出来的差别就分不清是「风扇更猛」还是「更早升档」。
    """
    out = []
    for p in points:
        q = dict(p)
        if p['id'] != 0 and p.get('duty_pct') is not None:
            q['duty_pct'] = max(0.0, min(100.0, round(p['duty_pct'] + delta, 1)))
        out.append(q)
    return out


def apply_plan(ec, plan, rep, which):
    """按计划逐字节写，返回 (成功数, 失败数, 被拒的字节数)。写之前就按白名单挡住。"""
    ok = bad = refused = 0
    for addr, value in plan:
        if not in_table_range(which, addr):
            rep.out('  [拒写] 0x%04X 不在风扇表区间/括号里，白名单挡住' % addr)
            refused += 1
            continue
        if ec.dev.write(addr, value) is None:
            bad += 1
            rep.out('  [失败] 0x%04X：%s（连失 %d 次）'
                    % (addr, ec.dev.error, ec.dev.fail_streak))
            if ec.dev.fail_streak >= FAIL_BREAK:
                rep.out('  [熔断] 连续失败 %d 次，停手，不再写后面的字节' % ec.dev.fail_streak)
                return ok, bad, refused
        else:
            ok += 1
        time.sleep(GAP_S)
    return ok, bad, refused


def verify_written(ec, plan, rep):
    """回读每个数据字节。注意：这只证明「字节写进去了」，不证明 EC 采纳了这张表。"""
    mism = []
    for addr, value in plan:
        if addr == fanrules.BRACKET_ADDR:
            continue
        got = ec.dev.read(addr)
        time.sleep(GAP_S)
        if got is None or (got & 0xFF) != value:
            mism.append((addr, value, got))
    rep.out('  回读：%d 个数据字节，%d 个不一致%s' % (
        len(plan) - 2, len(mism), '' if not mism else '（%s）' % ', '.join(
            '0x%04X 期望 %s 读到 %s' % m for m in mism[:6])))
    return mism


def active_point(points, temp):
    """当前温度落在哪个点：占空比取「最后一个 up_t ≤ 温度」的点。"""
    hit = None
    for p in points:
        if p.get('up_t') in (None, 255) or p.get('duty_pct') is None:
            continue
        if temp is not None and p['up_t'] <= temp:
            hit = p
    return hit


def sample(ec, thermal, seconds, rep, which, label, ref=None, bench=None, load_s=0.0,
           read_temp=None):
    """采一轮转速/占空/温度。判据就看这里的 rpm 序列。

    `ref` 给一张表就能把「这张表在当前温度下要求的占空比」打在旁边——没有参照的话
    占空比涨了也不知道是不是我们要的那张表在起作用。旁边还顺手记下两者的**差**：
    差得多就说明 EC 此刻压根不看这张表（0xA0 手动档就是这样），写下去白写。
    `load_s` 用 zstd 全核负载把温度顶过点 1 的阈值：不加载的话机器停在点 0，
    而点 0 的占空比按硬规则必须是 0 %，整轮实验量不到任何东西。
    `read_temp` 是温度来源：改 GPU 表就得看 GPU 的温度，CPU 温度管不着 GPU 那张表。
    """
    if bench is not None and load_s > 0:
        threading.Thread(target=lambda: bench.load(target_s=load_s), daemon=True).start()
        rep.out('  [%s] 已起 %.0f 秒全核负载，把温度顶过表里的触发点' % (label, load_s))
    rpms, duties, temps, devs = [], [], [], []
    temp_of = read_temp if read_temp else (lambda: thermal.read()[0])
    end = time.time() + seconds
    while time.time() < end:
        ec._last_fast = 0.0
        ec.tick()
        snap = ec.read()
        temp = temp_of()
        rpm, duty = snap.get(RPM_KEY[which]), snap.get(DUTY_KEY[which])
        if rpm:
            rpms.append(rpm)
        if duty is not None:
            duties.append(duty)
        if temp:
            temps.append(temp)
        want = active_point(ref, temp) if ref else None
        if want and duty is not None and want['duty_pct'] is not None:
            devs.append(abs(duty - want['duty_pct']))
        rep.out('  [%s] 转速=%s RPM 实测占空=%s%% 表里该=%s%%（点%s）温度=%s°C' % (
            label, rpm, duty, want and want['duty_pct'], want and want['id'], temp))
        if temp and temp >= TEMP_GUARD_C:
            rep.out('  [温度保险] %s°C ≥ %.0f°C，停止采样' % (temp, TEMP_GUARD_C))
            break
        time.sleep(2.0)
    return {'rpm_max': max(rpms) if rpms else None,
            'rpm_avg': round(sum(rpms) / len(rpms)) if rpms else None,
            'duty_max': max(duties) if duties else None,
            'duty_vals': sorted(set(duties)),
            'dev_min': round(min(devs), 1) if devs else None,
            'dev_max': round(max(devs), 1) if devs else None,
            'temp_max': max(temps) if temps else None, 'n': len(rpms)}


def wait_stable(ec, rep, which, label, timeout_s=150.0, stable_n=3):
    """等 EC 把占空调到稳态再判读，返回稳定后的占空比。

    23:07 那一轮实测：EC 改占空是**缓慢爬升/下降**的，还原之后 30 %→44.5 %→78 %
    走了约 3 分钟。50 秒的观察窗只看到半路，于是把「还在路上」误判成「没回到位」。
    判据是连续 stable_n 次读数不变（每 5 秒一次），超时也照样返回最后一个值。
    """
    run, last, t0 = 0, None, time.time()
    while time.time() - t0 < timeout_s:
        ec._last_fast = 0.0
        ec.tick()
        snap = ec.read()
        duty, rpm = snap.get(DUTY_KEY[which]), snap.get(RPM_KEY[which])
        run = run + 1 if duty == last else 1
        last = duty
        if run >= stable_n and duty is not None:
            rep.out('  [%s] 占空稳在 %s%%（%s RPM），等了 %.0f 秒'
                    % (label, duty, rpm, time.time() - t0))
            return duty
        time.sleep(5.0)
    rep.out('  [%s] 等了 %.0f 秒占空还在动（最后 %s%%），按这个值判读'
            % (label, timeout_s, last))
    return last


def hold_mode(ec, cfg, log, rep, res):
    """实验期间自己握住 MQTT 连接，并把档位锁在 turbo（EC 的 `0x0751` 应为 `0x10`）。

    两件事都是实测逼出来的，不是保险起见：
    ① `0xA0`（User_Fan_HiMode，造物者的手动风扇档）下 EC **根本不看这张温度表**——
       实测 CPU 47~50 °C 时占空 53~56 %、转速 3500~3690 RPM，而表里点 0 按硬规则是 0 %。
       在那一档写表，量不到任何东西。
    ② 工具的 MQTT 一断开，服务会把它自己存的档位重新下发（实测断开约 30 秒后
       `0x10`/75 W → `0xA0`/60 W）。所以这条连接不能「发完就撤」，得从握手握到收尾。
    """
    mq = MqttChannel(cfg, log)
    res['mqtt'] = mq
    mq.start()
    for _ in range(30):
        if mq.alive:
            break
        time.sleep(0.5)
    if not mq.alive:
        rep.out('[停止] MQTT 连不上 broker（面板必须已经停掉，否则身份互踢）：'
                '%s' % (mq.detail.get('reason') or '未知'))
        return False
    ok, msg = mq.set_mode('turbo')
    rep.out('  下发 turbo：%s｜%s' % ('已接受' if ok else '被拒', msg))
    if not ok:
        return False
    rep.out('  等档位生效（实测 9~26 秒，最长等 %.0f 秒）……' % MODE_WAIT_S)
    end = time.time() + MODE_WAIT_S
    while time.time() < end:
        ec._last_fast = 0.0
        ec.tick()
        byte = ec.read().get('fan_ctl_byte')
        if byte == 0x10:
            rep.out('  已生效：风扇模式字节 0x%02X，连接保持到实验结束' % byte)
            return True
        time.sleep(2.0)
    rep.out('[停止] 等了 %.0f 秒字节还没变成 0x10，EC 现在看的还是那张手动档的表，'
            '写了也白写。**一个字节都没写。**' % MODE_WAIT_S)
    return False


def main(argv):
    low = [a.lower() for a in argv]
    write = '--write' in low
    which = next((a.split('=', 1)[1].upper() for a in argv if a.startswith('--which=')), 'CPU')
    delta = next((float(a.split('=', 1)[1]) for a in argv if a.startswith('--raise=')), 15.0)
    observe = next((float(a.split('=', 1)[1]) for a in argv if a.startswith('--observe=')),
                   OBSERVE_S)
    load_s = next((float(a.split('=', 1)[1]) for a in argv if a.startswith('--load=')), LOAD_S)
    rep = Report()
    res = {'ec': None, 'thermal': None, 'clock': None, 'mqtt': None, 'bench': None}
    rep.out('风扇表写入可逆验证  %s｜表=%s 占空平移=%+.0f%% 观察=%.0fs 负载=%.0fs 模式=%s' % (
        time.strftime('%Y-%m-%d %H:%M:%S'), which, delta, observe, load_s,
        '真下发' if write else '演练（不发）'))
    try:
        if which not in BASE_ADDR:
            rep.out('[停止] 只认 CPU/GPU 两张表，给的是 %s' % which)
            return 2
        if not write:
            rep.out('[提示] 演练模式不会写任何东西。')
        try:
            port = discover_panel_port()
            alive = panel_alive(port)
        except (OSError, ValueError) as exc:
            rep.out('[停止] 无法确认面板已停，拒绝继续：%s' % exc)
            return 3
        if alive:
            rep.out('[停止] 面板还在端口 %d 上跑，请先手动停止面板，避免并发操作。' % port)
            return 3
        cfg, err, _bad = Config.load()
        if err:
            rep.out('[停止] 配置读取失败，拒绝继续：%s' % err)
            return 3
        ps = power_status()
        if not ps.get('on_ac'):
            rep.out('[停止] 现在靠电池（电量 %s%%）。改风扇表要接电源，'
                    '免得电池侧的散热策略和这次实验混在一起。' % ps.get('battery_pct'))
            return 3

        log = Log('fan-table-test')
        ec, thermal = EcChannel(cfg, log), Thermal()
        res['ec'], res['thermal'] = ec, thermal
        # 负载是判据的一部分，不是可选项：不加载温度顶不过点 1 的触发点，风扇根本不转
        clock = ClockSense() if load_s > 0 else None
        res['clock'] = clock
        bench = Bench(log=log, thermal=thermal, clock=clock) if load_s > 0 else None
        res['bench'] = bench      # 被闸门中断时也要收掉 zstd 子进程，否则留孤儿烧 CPU
        detail = ec.probe()
        rep.out('EC 通道=%s allow_write=%s' % (detail.get('state'), ec.allow_write))
        if not ec.alive or not ec.allow_write:
            rep.out('[停止] EC 不可用或写入未开启，没什么可验的。')
            return 1

        # 顺序有讲究：切档时服务的 SetUserProfile 会把**整块表**重灌一遍，
        # 所以现表必须在档位稳住之后读，否则读到的表在实验中途就被盖掉了。
        if write:
            if not hold_mode(ec, cfg, log, rep, res):
                return 1
        else:
            rep.out('  演练模式不切档（切档本身就是硬件写入），按当前档位读。')

        gpu = GpuSense() if which == 'GPU' else None
        if gpu is not None and not gpu.bin:
            rep.out('[停止] 找不到 nvidia-smi，测 GPU 表必须有 GPU 温度（%s）。'
                    '改用 --which=CPU，或先确认驱动可用。' % gpu.error)
            return 1
        # 改哪张表就用那张表的温度：CPU 温度到 90 °C 也不会让 GPU 那张表升一档
        read_temp = ((lambda: (gpu.read() or {}).get('temp_c')) if gpu is not None
                     else (lambda: thermal.read()[0]))

        curve = ec.fan_curve()
        if not curve.get('ok'):
            rep.out('[停止] 现表没读全：%s' % curve.get('reason'))
            return 1
        points = curve['tables'][which]
        ctx = curve['context']
        bracket = ec.dev.read(fanrules.BRACKET_ADDR)
        temp0 = read_temp()
        rep.out('现况：风扇模式字节=0x%02X(%s) 括号 0x07C6=%s 分表=%s 转速=%s RPM %s温度=%s°C' % (
            ctx.get('fan_ctl_byte') or 0, ctx.get('fan_mode_flag'),
            '读不到' if bracket is None else '0x%02X' % bracket,
            ctx.get('split_tables'), ec.read().get(RPM_KEY[which]), which, temp0))
        if bracket is None:
            rep.out('[停止] 括号字节 0x07C6 没读到，不能拿默认值凑——夹子开错方向会抢服务的写表窗口。')
            return 1
        temp = read_temp()
        act = active_point(points, temp)
        rep.out('当前 %s 温度 %s°C 落在点 %s（占空 %s%%）' % (which, temp, act and act['id'],
                                                        act and act['duty_pct']))

        errors, warnings = fanrules.validate(which, points)
        rep.out('现表自检：错误 %d 条，警告 %s' % (len(errors), warnings or '无'))
        if errors:
            rep.out('[停止] OEM 现在这张表自己就过不了校验，说明我们对表的理解有偏差，'
                    '先别写。错误：%s' % errors[:4])
            return 1

        target = modified(points, delta)
        terrs, twarn = fanrules.validate(which, target)
        rep.out('新表自检：错误 %d 条，警告 %s' % (len(terrs), twarn or '无'))
        if terrs:
            rep.out('[停止] 改完的表不合法（EC 会整块丢掉这种表）：%s' % terrs[:4])
            return 1
        plan = fanrules.build_plan(which, target, bracket_byte=bracket)
        back = fanrules.build_plan(which, points, bracket_byte=bracket)
        rep.out('')
        rep.out('--- 计划 ---')
        rep.out('  写表 %d 条（含首尾括号各 1 条），还原 %d 条' % (len(plan), len(back)))
        rep.out('  括号：0x07C6 %s → %s（先清 bit2 再写表，写完置回）' % (
            '0x%02X' % bracket, '0x%02X' % (bracket & ~0x04)))
        changed = [(p.get('id'), p.get('duty_pct'), t.get('duty_pct'))
                   for p, t in zip(points, target) if p.get('duty_pct') != t.get('duty_pct')]
        rep.out('  占空比变化 %d 个点：例：%s' % (
            len(changed), '、'.join('点%s %s%%→%s%%' % c for c in changed[:5])))
        if not write:
            rep.out('')
            rep.out('[演练] 没加 --write，一个字节都没写。机主在场、能听风扇，再加 --write。')
            return 0

        if temp and temp >= TEMP_GUARD_C:
            rep.out('[停止] 已经 %.0f°C，不在这里开始写表。' % temp)
            return 1

        rep.out('')
        rep.out('=== 基线采样（%.0f 秒，带 %.0f 秒全核负载，先知道现在多吵）===' % (observe, load_s))
        base = sample(ec, thermal, observe, rep, which, '基线',
                      ref=points, bench=bench, load_s=load_s, read_temp=read_temp)

        # 触发闸：判据要用 **EC 自己给出的占空**，不是我们读到的温度。
        # 22:44 实测：我们读 50.1 °C（点 1 的升温阈值是 53 °C），EC 却给到 78 %。
        # 原因不是「EC 的温度源和我们不同」——逆向仓库证实它查表只吃 0x043E/0x044F，
        # 和 coretemp 只差 1 °C；真正的原因是**滞回**：判定是「温度 > 升温阈值就升档，
        # 温度 < 降温阈值才降档，中间保持」，而本机点 1 的降温阈值正好是 50 °C，
        # 所以 49~50 °C 会一直停在点 1 给 78 %。拿我们的温度当闸必然误判「风扇还没转」。
        trig = next((p['up_t'] for p in points
                     if p.get('id') and (p.get('duty_pct') or 0) > 0
                     and p.get('up_t') is not None), None)
        if not base.get('duty_max'):
            rep.out('[停止] 基线里 EC 给的占空一直是 %s%%（我们读到最高 %s °C，'
                    '表里点 1 的升温阈值是 %s °C）——风扇停在点 0 没转，这一轮写了也量不到东西。'
                    '**一个字节都没写。**' % (base.get('duty_max'), base['temp_max'], trig))
            return 1
        rep.out('  风扇已经在转：EC 给到 %s%%（我们读到最高 %s °C，点 1 升温阈值 %s °C；'
                '停在上一档是滞回，不是温度源不同）'
                % (base['duty_max'], base['temp_max'], trig))

        # 第二道闸，量的是「EC 此刻到底在不在按这张表给占空」。
        # 判据是**实测占空有没有落在表里的档位集合上**，不是「和表里该点差多少」：
        # 拿我们的温度去查表比差值会误杀（滞回 + 每 3 tick 走 0.5 % 的斜坡会产生
        # 44.5/49.5/56.5 这种表里没有的半步中间态）。而 0xA0 手动档的特征是给出
        # 90 %/87 % 这种既不在表里、也不是地板值的数。
        # 允许值里必须带上 30 %：那是**固件常量地板 0x3C**（目标为 0 且退避计数未归零时
        # 强制给 30 %），空转时就会读到它，不带上会把正常状态误判成「没在查表」。
        table_duties = sorted({round(p['duty_pct']) for p in points
                               if p.get('duty_pct') is not None} | {round(EC_DUTY_FLOOR)})
        seen = base.get('duty_vals') or []
        on_table = [d for d in seen
                    if any(abs(d - t) <= DUTY_MATCH_PCT for t in table_duties)]
        if not on_table:
            rep.out('[停止] 基线里实测占空 %s%% 一个都没落在表里的档位 %s%%（容差 ±%.0f）上——'
                    'EC 没在按这张表给占空（手动档就是这样），写下去只是改一块没人读的内存。'
                    '**一个字节都没写。**'
                    % ('/'.join(str(d) for d in seen) or '读不到',
                       '/'.join(str(t) for t in table_duties), DUTY_MATCH_PCT))
            return 1
        rep.out('  EC 在按这张表给占空：实测 %s%% 里有 %s%% 落在表档位 %s%% 上'
                '（与「我们的温度查表」的偏差 最小 %.1f / 最大 %.1f，仅供参考——'
                '滞回和每 3 tick 走 0.5 %% 的斜坡都会让这个偏差看起来很大）'
                % ('/'.join(str(d) for d in seen), '/'.join(str(d) for d in on_table),
                   '/'.join(str(t) for t in table_duties),
                   base.get('dev_min'), base.get('dev_max')))

        rep.out('')
        rep.out('=== 写入新表 ===')
        # 写之前也要等稳态：EC 可能还在上一轮的爬升途中，拿半路的值当基线会误判方向
        duty_base = wait_stable(ec, rep, which, '基线')
        ok, bad, refused = apply_plan(ec, plan, rep, which)
        rep.out('  写了 %d 个字节、失败 %d、白名单挡住 %d' % (ok, bad, refused))
        verify_written(ec, plan, rep)
        if bad or refused:
            rep.out('  [注意] 这一轮没写全，仍然先还原，再判读。')

        rep.out('')
        rep.out('=== 生效后：先等占空爬到稳态，再采样 ===')
        duty_after = wait_stable(ec, rep, which, '新表')
        after = sample(ec, thermal, observe, rep, which, '新表',
                       ref=target, bench=bench, load_s=load_s, read_temp=read_temp)

        rep.out('')
        rep.out('=== 还原（写回现表原值）===')
        ok2, bad2, refused2 = apply_plan(ec, back, rep, which)
        rep.out('  还原写 %d 个字节、失败 %d、挡住 %d' % (ok2, bad2, refused2))
        verify_written(ec, back, rep)
        # 还原之后同样要等：EC 往回爬也是几分钟量级，不等就会误报「没回到位」
        duty_back = wait_stable(ec, rep, which, '还原后')
        restored = sample(ec, thermal, observe, rep, which, '还原后',
                          ref=points, bench=bench, load_s=load_s, read_temp=read_temp)

        rep.out('')
        rep.out('--- 判读 ---')
        rep.out('  占空（都等到稳态）基线 %s%% → 新表 %s%% → 还原后 %s%%' % (
            duty_base, duty_after, duty_back))
        rep.out('  转速 基线 %s → 新表 %s → 还原后 %s RPM（平均）' % (
            base['rpm_avg'], after['rpm_avg'], restored['rpm_avg']))
        rep.out('  温度 基线 %s → 新表 %s → 还原后 %s °C（最高）' % (
            base['temp_max'], after['temp_max'], restored['temp_max']))
        # 主判据是**稳态占空**往我们要的方向动了没有，转速只是旁证。
        # 用占空而不是转速：EC 调占空是几分钟量级的缓慢爬升，转速跟着它走，
        # 但转速还受温度影响；占空是 EC 直接从表里算出来的那个量。
        want_up = delta > 0
        ok_all = (duty_base is not None and duty_after is not None
                  and duty_back is not None)
        moved = ok_all and abs(duty_after - duty_base) >= 5 and (
            (duty_after > duty_base) if want_up else (duty_after < duty_base))
        back_ok = ok_all and abs(duty_back - duty_base) <= 5
        if bad or refused:
            rep.out('  结论：没写全，判据不成立。不补写、不重试第二轮，先看失败在哪几个地址。')
        elif not ok_all:
            rep.out('  结论：占空比没读全（%s→%s→%s），判据不成立，凭这个下不了结论。'
                    % (duty_base, duty_after, duty_back))
        elif moved and back_ok:
            rep.out('  结论：占空比从 %s%% %s到了 %s%%，还原后回到 %s%% —— '
                    '**风扇表写入这条路是通的，而且可逆**。'
                    % (duty_base, '抬' if want_up else '压', duty_after, duty_back))
        elif moved:
            rep.out('  结论：占空比动了（%s%%→%s%%）但**还原后没回到位**（现在 %s%%）——'
                    '写入生效、可逆性不成立。这是必须人工处理的状态：切一次档位让服务的 '
                    'SetUserProfile 重灌整张表，再回读确认。'
                    % (duty_base, duty_after, duty_back))
        else:
            rep.out('  结论：字节写进去了、回读也一致，但稳态占空没动（%s%%→%s%%）——'
                    'EC 很可能整块丢了这张表（硬规则违反了会静默退回内置曲线），'
                    '或者这个档位下它不采纳外部表。不猜，下一步只做静态分析。'
                    % (duty_base, duty_after))
        rep.out('  档位停在 turbo（0x0751=0x10）：这一轮是我们握着 MQTT 切的，'
                '工具退出后服务会按它存的档位继续管。要交还给 OEM 就用面板或实体键切一次，'
                '服务的 SetUserProfile 会重灌整张表。')
        rep.out('  本进程 EC 读 %d 次、写 %d 次' % (ec.dev.reads, ec.dev.writes))
        return 0
    finally:
        for key in ('bench', 'mqtt', 'clock', 'thermal', 'ec'):
            try:
                if res[key] is not None:
                    res[key].close()
            except Exception:                       # noqa: BLE001
                pass
        rep.save()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
