# -*- coding: utf-8 -*-
"""16 点风扇曲线的校验与 EC 写序列计划生成（纯函数、零 I/O）。

    from app import fanrules
    errors, warnings = fanrules.validate('CPU', points)
    plan = fanrules.build_plan('CPU', points)      # 校验不过会直接拒绝

这是 #31（风扇曲线写入）的前置件：把「用户曲线 → EC 字节序列」做成纯计算，
**只生成计划，不执行**。执行要等一次机主在场的可逆验证，走带闸门的写入路径
（目前锁死），见 notes/verification-log.md #31 与 notes/hardware-channels.md 6.11。

本模块的硬约束（由 tests/test_fan_rules.py 的结构断言钉住）：
不许 import ctypes、不许 import app.act 下任何东西、不许开句柄 / 发 IOCTL、
不许存在名为 write/apply/send/commit 的函数。计划只是数据。

地址布局必须与读侧 `app/act/channels/ec_gpd.py::_read_curve_table` 逐字一致——
那份布局是厂商反编译源码 + ROM 固件本体 + 本机活体三方对上的结论
（notes/hardware-channels.md 6.11 第一、六、十四节），别处不许出现第二套算法：

    升温：点 k（1..15）→ base + k - 1
          （读侧 addrs = [base+i for i in range(15)]，即 base+0x00..0x0E；
            点 0 的 up_t 恒为 0 且没有存储位置，所以计划里只有 15 条升温写。
            base+0x0F 是 0xFF 哨兵位、base+0x10 厂商从不碰——
            这两个地址**不许出现在任何计划里**，出现在计划里等于改了别的语义。）
    降温：点 k（0..14）→ base + 0x11 + k
          （读侧 addrs = [base+0x11+i for i in range(15)]，即 base+0x11..0x1F；
            点 15 没有自己的格子，厂商读的就是 base+0x1F（和点 14 同一个地址），
            所以点 14/15 的 down_t 只能合成一次写，以点 14 的值为准。）
    占空比：点 k（0..15）→ base + 0x20 + k
          （读侧 addrs = [base+0x20+i for i in range(16)]；
            寄存器里存的是「百分比 ×2」，0xC8=200 即 100%。）

合计 15 + 15 + 16 = 46 次数据写，与读侧 `assert out['reads'] == 96`（46×2 + 4 个
语义位）对得上。GPU 表的最后三个占空比槽（0x0F5D/0x0F5E/0x0F5F = 点 13/14/15）
被厂商的 RefreshDefaultFanTableAll 借去当信箱（收 EC 回给它的默认表），不是数据，
所以 GPU 的计划是 43 条数据而不是 46 条。
"""

# 与 ec_gpd.FAN_TABLE_BASE 同源，但本模块不许 import app.act，所以抄在这里；
# 改地址必须两边一起改，tests/test_fan_rules.py 会逐字节对照。
FAN_TABLE_BASE = {'CPU': 0x0F00, 'GPU': 0x0F30}
FAN_TABLE_POINTS = 16
SENTINEL = 0xFF
# GPU 表最后三个占空比槽是厂商信箱（ec_gpd.GPU_DUTY_MAILBOX），同上抄写。
GPU_DUTY_MAILBOX = (13, 14, 15)

# 写表括号：bit2 = ENABLE_UNIVERSAL_FAN_CTRL（DSDT 名 AP_OEM_6 / WMS0 的高位）。
# 厂商每次写风扇表都先清 bit2、写完整表再置回（MyFanTableCtrl.SetFanControlByRamFan1p5，
# notes/hardware-channels.md 6.11 第十三节 ⑦），照抄。默认 0x07 是 2026-09-30 本机实测静息值，
# 调用方要拿当下的真实值传进来，本模块绝不自己读设备。
BRACKET_ADDR = 0x07C6


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(which, points):
    """校验一张 16 点曲线，返回 (errors, warnings)。

    points 是读侧 fan_curve() 交出来的形状：16 个 {up_t, down_t, duty_pct}。
    尾部哨兵的约定（厂商默认表就这么存，本机活体同）：只有前 N 个点是真阈值，
    从第一个 up_t == 0xFF 的点起到点 15 全是 0xFF（「到此为止」，不是温度），
    这些格子照原样写进计划，不许重编号或压缩。

    errors 非空 = 不许生成计划（写下去要么被 EC 拒、要么「成功」但没效果）。
    warnings 只是提醒，不拦截。
    """
    errors = []
    warnings = []
    if which not in FAN_TABLE_BASE:
        return (['表名必须是 CPU 或 GPU，收到 %r' % (which,)], warnings)
    if not isinstance(points, (list, tuple)) or len(points) != FAN_TABLE_POINTS:
        return (['点数必须是 %d，收到 %s 个' % (
            FAN_TABLE_POINTS, len(points) if isinstance(points, (list, tuple)) else '非列表')],
                warnings)

    ups = [p.get('up_t') for p in points]
    downs = [p.get('down_t') for p in points]
    duties = [p.get('duty_pct') for p in points]

    # ---- 数据完整性：读侧没读全的点（None）不许进计划 ----
    # GPU 信箱槽的 duty_pct 本来就是 None，那是正常形状，不算缺失。
    for k in range(FAN_TABLE_POINTS):
        mailbox = which == 'GPU' and k in GPU_DUTY_MAILBOX
        if ups[k] is None and k > 0:
            errors.append('点 %d 的 up_t 缺失（读侧没读全），不许生成计划' % k)
        if downs[k] is None:
            errors.append('点 %d 的 down_t 缺失（读侧没读全），不许生成计划' % k)
        if duties[k] is None and not mailbox:
            errors.append('点 %d 的 duty_pct 缺失（读侧没读全），不许生成计划' % k)

    # ---- 点 0 的两条铁律 ----
    # 点 0 占空比非 0 时 EC 整表拒绝并悄悄退回内置曲线（同家族机型实测，
    # 是最阴的失败模式：写「成功」了、读回来也像写进去了，实际没生效）。
    if duties[0] != 0:
        errors.append('点 0 的占空比必须是 0%%，收到 %r' % (duties[0],))
    # 那一格根本没有存储位置（厂商代码里 UpT[0] 直接赋 0），非 0 说明上游数据就是错的。
    if ups[0] != 0:
        errors.append('点 0 的 up_t 必须是 0（没有存储位置），收到 %r' % (ups[0],))

    # ---- 已用点 / 哨兵分界 ----
    used = FAN_TABLE_POINTS
    for k in range(FAN_TABLE_POINTS):
        if ups[k] == SENTINEL:
            used = k
            break
    if used < 2:
        errors.append('已用点至少 2 个（点 0 起到第一个 0xFF 哨兵前），现在只有 %d 个' % used)
    # 哨兵必须连续到点 15：中途夹一个非 0xFF，EC 会在中途停住，后面的点等于没写。
    for k in range(used + 1, FAN_TABLE_POINTS):
        if ups[k] != SENTINEL:
            errors.append('点 %d 的 up_t 不是 0xFF：哨兵从点 %d 开始就必须一路到底，'
                          '中间夹真温度 EC 会在中途停住' % (k, used))

    # ---- 已用点的硬规则 ----
    # 升温阈值严格单调：不单调的表 EC 同样「收下但不照跑」。
    last_up = None
    for k in range(used):
        if ups[k] is None:
            continue
        if last_up is not None and ups[k] <= last_up:
            errors.append('点 %d 的 up_t=%r 没有比前一点（%r）高：已用点必须严格单调递增'
                          % (k, ups[k], last_up))
        last_up = ups[k]
    # 占空比单调不降；duty_pct 为 None 的点跳过（GPU 信箱槽读侧本来就不给值，
    # 把 None 当 0 算会冤枉好表）。
    last_duty = None
    for k in range(used):
        if duties[k] is None:
            continue
        if last_duty is not None and duties[k] < last_duty:
            errors.append('点 %d 的占空比 %r 低于前一点（%r）：已用点必须单调不降'
                          % (k, duties[k], last_duty))
        last_duty = duties[k]

    # ---- 值域 ----
    # 温度：0-254。255 只允许出现在哨兵语义的位置（up_t 的尾部、down_t 的「到此为止」），
    # 别的越界值写下去就是错码。
    for k in range(used):
        if _is_num(ups[k]) and not (0 <= ups[k] <= 254):
            errors.append('点 %d 的 up_t=%r 越界（已用点阈值必须落在 0-254）' % (k, ups[k]))
    for k in range(FAN_TABLE_POINTS):
        if _is_num(downs[k]) and downs[k] != SENTINEL and not (0 <= downs[k] <= 254):
            errors.append('点 %d 的 down_t=%r 越界（0-254，255 视为哨兵）' % (k, downs[k]))
    for k in range(FAN_TABLE_POINTS):
        if _is_num(duties[k]) and not (0 <= duties[k] <= 100):
            errors.append('点 %d 的占空比 %r 越界（0-100%%）' % (k, duties[k]))

    # ---- 警告（生成计划但要说出来） ----
    for k in range(1, used):
        if _is_num(ups[k]) and _is_num(ups[k - 1]) and ups[k] - ups[k - 1] < 2:
            warnings.append('点 %d 与点 %d 的温差只有 %s °C（<2），EC 平滑可能跟不上，'
                            '风扇会来回追' % (k - 1, k, ups[k] - ups[k - 1]))
    # 点 0 恒为 0%（上面的铁律），所以这里看的是点 1 起：一过第一个阈值就贴着
    # 上限转，风扇曲线名存实亡，纯噪音档。
    real_duties = [d for k, d in enumerate(duties) if k > 0 and _is_num(d)]
    if real_duties and all(d >= 90 for d in real_duties):
        warnings.append('点 1 起的占空比全部不低于 90%%：风扇基本贴着上限转，注意噪音')
    # down_t 的确切语义项目里还没弄明白（notes/verification-log.md「别拿它算东西」那条），
    # 所以这里只做一致性提醒、绝不当错误拦。基线表里 down 与 up 的差在 ±3 °C，
    # 差出 10 °C 以上的多半是填错了列。
    for k in range(1, used):
        if (_is_num(downs[k]) and downs[k] != SENTINEL and _is_num(ups[k])
                and abs(downs[k] - ups[k]) > 10):
            warnings.append('点 %d 的 down_t(%r) 与 up_t(%r) 相差超过 10 °C：'
                            'down_t 语义未明，请核对是否填错' % (k, downs[k], ups[k]))
    if downs[14] != downs[15]:
        warnings.append('点 14 与点 15 的 down_t 不一致（%r / %r）：两点共用同一格，'
                        '计划只会写一次，以点 14 的值为准' % (downs[14], downs[15]))
    if which == 'GPU':
        warnings.append('GPU 表点 13/14/15 的占空比槽（0x0F5D-0x0F5F）是厂商信箱、'
                        '不属于我们，计划里没有这三格，给出的值会被丢弃')
    return (errors, warnings)


def build_plan(which, points, bracket_byte=0x07):
    """把校验过的曲线转成 (EC地址, 原始字节) 的有序写序列（含首尾括号），纯数据。

    首尾各一条 0x07C6「括号」：先清 bit2（& ~0x04）、写完整表、再置回 bit2（| 0x04）。
    bracket_byte 由调用方传入（写之前要先真读一次 0x07C6），默认 0x07 是
    2026-09-30 本机实测静息值——本模块不读设备，所以只能这样兜。
    """
    errors, _ = validate(which, points)
    if errors:
        raise ValueError('曲线未通过校验，不许生成计划：%s' % '；'.join(errors))
    base = FAN_TABLE_BASE[which]
    plan = [(BRACKET_ADDR, int(bracket_byte) & ~0x04)]
    # 升温 15 条：点 k 落 base+k-1（逐字对照读侧 [base+i for i in range(15)]）。
    # 尾部的 0xFF 哨兵是有内容的格子，照原样写，不许压缩重编号。
    for k in range(1, FAN_TABLE_POINTS):
        plan.append((base + k - 1, int(points[k]['up_t'])))
    # 降温 15 条：点 k（0..14）落 base+0x11+k（对照读侧 [base+0x11+i for i in range(15)]）。
    # 点 14 落在 base+0x1F，点 15 寄生同一格，所以这里天然只有一条写。
    for k in range(FAN_TABLE_POINTS - 1):
        plan.append((base + 0x11 + k, int(points[k]['down_t'])))
    # 占空比：点 k 落 base+0x20+k，值 = 百分比 ×2 取整（对照读侧 round(raw/2.0, 1)）。
    # GPU 的信箱三格（点 13/14/15 → base+0x2D/0x2E/0x2F）跳过，那是厂商的地盘。
    for k in range(FAN_TABLE_POINTS):
        if which == 'GPU' and k in GPU_DUTY_MAILBOX:
            continue
        plan.append((base + 0x20 + k, int(round(points[k]['duty_pct'] * 2))))
    plan.append((BRACKET_ADDR, int(bracket_byte) | 0x04))
    return plan
