# 任务说明（交给 workbuddy）

分支：`workbuddy/assist`（从 `main` 切出，基线 commit `4beb879`）。
这个仓库是 MECHREVO 同方系笔记本（Umi Pro 3 / 主板 GM5MG0Y）的**自制控制面板**，
用来替代厂商的 Creator Center：本地 HTTP 面板 + 托盘，只绑 `127.0.0.1:8747`。

**这个项目在真机上跑，控制的是硬件（风扇、功耗墙、键盘灯）。**
下面「禁区」那一段不是风格建议，是硬约束，违反了会造成不可逆损伤。
三条任务全部是**纯软件、零硬件写入**的活，做完能省下我大量的机械劳动。

做完请：只在自己的分支上小步提交（中文 commit message，一 commit 一件事），
**不要 push、不要碰 `main`、不要合并**——机主会通知我，由我 review 后合。

---

## 0. 先读这两份，再动手

- `notes/hardware-channels.md`：研究结论（很长，按需 grep，别通读）。第 6.11 节是按时间追加的观察记录。
- `notes/verification-log.md`：待办清单，本文的任务二、任务三分别对应里面 #31 和"面板显示慢 vs 硬件生效慢"那两条。

代码风格看现有文件就够了，两条要守：
注释**只写"为什么"**（厂商行为的坑、实测踩过的雷），不写代码在做什么；
中文注释、中文用户可见文案；不要引入任何新依赖。

## 1. 环境（照做，不然跑不起来）

- Python 只有一个能用：`runtime/python.exe`（仓库自带，`.gitignore` 掉了）。
  系统里的 `python` 是微软商店的占位 stub，直接执行会静默失败。
- **没有 pytest**。每个测试文件自成一体，这样跑：
  `runtime/python.exe tests/test_fan_curve.py`（成功的输出形如 `12/12 通过`，失败返回非 0）。
  测试文件顶部有 `sys.path.insert(0, <repo根>)`，新测试请沿用同样的写法。
- Windows 控制台默认 GBK，打印中文前先设 `PYTHONIOENCODING=utf-8`。
- 如果机主给你配的是 Git Bash：`tasklist /FI`、`cmd //c` 这类会被路径转换搞坏，能用 Python 就用 Python。
- 面板日志在 `data/umi-control-panel.log`（不是 `logs/`）。
- **`data/config.json` 里有明文 MQTT 密码：不许 cat、不许贴进对话、不许打进任何输出。**
- 机主的面板**此刻正在运行**（`UmiPanel.exe`，占着 8747 和 EC 句柄）。
  不要杀进程、不要重启、不要动开机自启；跑测试和只读 GET 都没问题。

## 2. 禁区（这一节比任务本身重要）

1. **不许新增或调用任何硬件写路径**。不许碰这些文件：
   `app/act/hardware.py`、`app/act/channels/*.py`、`app/server/httpd.py`、`app/cli.py`、`app/daemon.py`、
   `app/web/*`、`tools/ec_write_test.py`、`tools/ec_pl_test.py`、`tools/kb_power_test.py`、
   `tools/driver_ioctl_scan.py`、`tools/ioctl_layout_probe.py`。
   需要读它们的代码来理解语义？可以。**改动不行。**
2. **不许执行任何 EC / ACPI / IOCTL / UEFI 写入**，包括"只是试一下"。具体永久禁写的东西：
   充电门控字节 `0x07C3`/`0x0770`/`0x087F`（有真实 CVE，会永久损伤电池）、
   BIOS 刷写、UEFI 变量与 NVRAM（含 `UniWillVariable{9f33f85c-…}`）、`0x078C`、`0x07D0`。
3. **不许发任何 OEM 写命令**（MQTT `Fan/Control`、`Keyboard/Ctrl`、`Tray/Ctrl` 里带动作的那些）。
   只读动作 `GETSTATUS` 也别由你的代码去发——面板自己会发。
4. **不许跑第三方仓库来的安装脚本**（`pnputil`、`sc create`、装驱动、替换 OEM 组件），
   哪怕它来自 README 里致谢的那几个参考仓库。
5. **不许把凭据、厂商私有寄存器映射表、`tools/out/` 里的逆向产物提交进仓库**
   （`tools/out/`、`data/`、`/runtime/`、`*.log`、`*.dmp` 都已经在 `.gitignore` 里，别去改 `.gitignore`）。
6. **`notes/verification-log.md` 里那条没勾的「闸门漏洞：`send_action` 不查 cap 状态」是我自己留着做的，
   别去补**。它要同时判断"执行层加双查"和"`tools/` 里那几次首次验证怎么合法绕过"，
   方向搞反一次就把闸门削弱了——这正是这个项目最不能错的地方。
   同理，看到任何"这里是不是该加个写开关/加个按钮"的冲动，都请忍住，写成注释里的建议就好。
7. 任何一条任务把你带到"这一步得真写硬件才能往下走"——**停下来，在代码里留 TODO 注释写清楚卡在哪，
   别绕过去**。#27/#31 那两次在场验证是我自己要在机主面前做的。

---

## 任务一：`run_tests.py` —— 一条命令跑完全部测试

现状：**19 个**测试文件各要手动敲一遍，我每次改动都得重复这个劳动。
（2026-09-30 基线全绿，各文件输出的最后一行形如 `12/12 通过`，
也有 `通过 72 / 失败 0`、`结果：6/6 通过` 这几种写法——别按单一格式解析，认"非 0 退出码"为准。）

要求，仓库根新建 `run_tests.py`：

- 自动发现 `tests/test_*.py`，用**当前解释器同一个 python**（`sys.executable`）逐个跑为子进程，
  捕获 stdout/stderr，逐个打印 `文件名 / 结论 / 用时`，最后打印总计。
- 每个文件本身已经会打印 `N/M 通过`，请把它原样带出来（失败详情要能直接看），
  并在汇总里区分三种结果：**通过 / 失败（用例没过）/ 崩了（非 0 且没有 `通过` 那行，或超时）**。
- 支持 `--only 关键词`（子串匹配文件名）和 `--timeout 秒`（默认 60，单文件卡住不许拖死整轮）。
- **有失败就返回非 0**，全过返回 0。
- 不要动 `tests/` 里任何现有文件，不要引入依赖，不要并发跑（有测试会开句柄，串行更安全）。
- 顶部写一段用法注释：`runtime\python.exe run_tests.py`。

验收：`runtime/python.exe run_tests.py` 输出 19/19 全过，退出码 0；
临时把某个断言改错再跑，退出码非 0 且你能一眼指出是哪个文件哪条用例。
**（试完请 `git checkout -- ` 还原，别把改坏的断言留在提交里。）**

## 任务二：`app/fanrules.py` —— 风扇曲线的校验与写序列计划生成（纯函数，零 I/O）

背景：16 点风扇表**读取**已经落地（`EcChannel.fan_curve()`，只读，实测过）。
**写入**还锁着，等一次机主在场的可逆验证。验证要用的"把用户曲线变成 EC 字节序列"这一步
目前不存在。你把它做出来，但**只做到生成计划为止，不执行**。

先读 `app/act/channels/ec_gpd.py` 里 `FAN_TABLE_BASE` 上方那段注释和 `_read_curve_table()`，
表布局以那里为准（那是从厂商反编译源码 + ROM 固件本体 + 本机活体三方对上的）。要点：

- CPU 表基址 `0x0F00`，GPU 表基址 `0x0F30`，每表 16 个点。地址换算**逐字抄 `_read_curve_table` 的
  那三行**（`ec_gpd.py:713-715` 与 `:727-729`），一句话版本：
  - 升温：点 0 的 `up_t` 恒为 0 且**没有存储位置**（不许为它发写），点 `k`（1..15）在 `base + k - 1`，
    即 `base+0x00 … base+0x0E` 共 15 格；`base+0x0F` 是 `0xFF` 哨兵位、`base+0x10` 厂商从不碰，
    **这两个地址不许出现在计划里**。
  - 降温：点 `k`（0..14）在 `base + 0x11 + k`；点 15 没有自己的格子，厂商读的就是 `base+0x1F`
    （和点 14 同一个地址），所以点 14/15 的 `down_t` 只能合成一次写。
  - 占空比：点 `k`（0..15）在 `base + 0x20 + k`，共 16 格。
  - 合计 **15 + 15 + 16 = 46 次写**，和读侧那句 `assert out['reads'] == 96`（46×2 + 4 个语义位）对得上。
- 占空比存的是 **百分比 × 2**（`0xC8`=200 → 100 %），所以原始值域 0-200、即百分比 0-100。
- **GPU 表的最后三个占空比槽（`0x0F5D/0x0F5E/0x0F5F` = 点 13/14/15）被厂商借去当信箱了，不是数据**
  （`GPU_DUTY_MAILBOX`，厂商在那儿收 EC 回给它的默认表）。处理方式：**`build_plan` 对 GPU 表跳过那三格**
  （所以 GPU 的计划是 43 条数据而不是 46 条），`validate` 出一句警告说明"那三格被丢弃、不属于我们"。
  读侧拿到的 `duty_pct` 在那三点上是 `None`（`mailbox: True`），别把 `None` 当 0 算进曲线。

要做两个函数（签名你可以定，语义必须照下面）：

1. `validate(which, points) -> (errors, warnings)`
   `which` 是 `'CPU'` 或 `'GPU'`，`points` 是 **16 个** `{up_t, down_t, duty_pct}`
   （读侧 `fan_curve()` 交出来的就是这个形状，直接能用）。
   先讲清楚**尾部哨兵**，不然后面的规则没法判：本机活体表只有前 9 个点是真阈值，
   点 9..15 的 `up_t` 全是 `0xFF`（"到此为止"的意思，不是温度），
   厂商默认表就是这么存的。所以：
   - 连续的一段"已用点"在最前面，从第一个 `up_t == 0xFF` 的点起**到点 15 必须全是 `0xFF`**；
     中间夹了一个非 0xFF 的值算硬错误（EC 会在中途停住，后面的点等于没写）。
   - 已用点至少 2 个；已用点内部**严格单调递增**。
   - 尾部这些 `0xFF` 要照原样写进计划（它们是有内容的格子，`base+0x08 … base+0x0E` 在读侧就摆着 255），
     **不许自作主张重编号或压缩**。
   **硬错误**（有任一条就不许生成计划）：
   - 点 0 的占空比不是 0 %（同家族机型实测：不满足时 EC 整表拒绝并悄悄退回内置曲线）；
   - 点 0 的 `up_t` 不是 0（那一格根本没有存储位置，非 0 说明上游数据就是错的）；
   - 已用点的升温阈值非严格单调；尾部哨兵不连续；已用点占空比不是**单调不降**
     （后两条与第一条同源：写"成功"但没效果，是最坑的失败模式，必须下发前挡住）；
     单调比较请**跳过 `duty_pct is None` 的点**（那是 GPU 信箱槽，读侧本来就不给值）；
   - 温度越界（除尾部那段 `0xFF` 哨兵外，阈值必须落在 0-254）；占空比越界（<0 或 >100）；
   - 点数不等于 16；`which` 不是 CPU/GPU；
   **警告**（生成计划但要说出来）：相邻已用点温差 < 2 °C（EC 平滑跟不上）、
   全表占空比都 ≥ 90 %（噪音）、`down_t` 与 `up_t` 的关系异常、
   GPU 表点 13/14/15 的占空比被丢弃（信箱槽）。
   ⚠️ `down_t` 的确切语义项目里还没弄明白（见 `notes/verification-log.md` 里"别拿它算东西"那条），
   所以**只许做一致性提醒，不许当错误拦**。
2. `build_plan(which, points, bracket_byte=0x07) -> list[tuple[int, int]]`
   返回 `(EC地址, 原始字节)` 的有序列表，地址按上面那三条规则算，**占空比 = 百分比 ×2 取整**。
   列表首尾各加一条"括号"：`0x07C6` 先清 bit2、写完整表后置回 bit2
   （厂商自己每次写表都这么括，见 `notes/hardware-channels.md` 6.11 第十三节 ③）。
   括号那两条拿传进来的 `bracket_byte` 做位运算得到 `& ~0x04` 和 `| 0x04` 两个值，
   **不许自己去读设备**——默认 0x07 是 2026-09-30 本机实测值。

硬性要求：这个模块**不许 `import ctypes`、不许 import `app.act` 下面任何东西、
不许开句柄、不许发 IOCTL、不许有名为 `write`/`apply`/`send`/`commit` 的函数**。
它是纯计算，"计划"只是数据。请把这条写成一条结构断言放进测试里（读源码文本检查就行）。

配套 `tests/test_fan_rules.py`，风格照 `tests/test_fan_curve.py`（`@case` 装饰器 + `main()`）。
至少要覆盖：从 `tests/test_fan_curve.py` 抄来的那张 CPU 实测基线表判为合法（尾部 0xFF 那种）、
点 0 非 0 % 被拦、非单调被拦、哨兵中途断开被拦、温度/占空比越界被拦、点数不对被拦、
GPU 表点 13/14/15 那三格**不在计划里**且有一条警告、`down_t` 异常只出警告不出错误；
`build_plan` 的地址逐字节写死几条对照（**注意整体错位一格这个坑**）：
点 1 的升温值落在 `0x0F00`、点 2 落在 `0x0F01`、点 15 落在 `0x0F0E`，
点 0 的降温在 `0x0F11`、点 14 与点 15 共用 `0x0F1F`，
CPU 点 0 占空比 `0x0F20`、GPU 点 0 占空比 `0x0F50`、GPU 点 12 占空比 `0x0F5C`（点 13 起是信箱）；
首尾两条是 `0x07C6` 的清位/置位、中间 CPU 46 条 / GPU 43 条数据、占空比按 ×2 编码；
以及上面那条"不许有写路径"的断言。

## 任务三：`tools/selfcheck.py` —— 只读体检脚本（把"慢在哪一段"变成一条命令）

背景：本次排障得到的最重要一条经验——**"面板读数翻得慢"和"硬件真的生效慢"是两件事**，
判据是 OEM 状态快照的时间戳（`/api/hardware` 里的 `state.oem.ts`，
就是 `/api/state` 里 `hardware.oem.ts` 那个字段——我们最后一次拿到 OEM 状态回复的时间）的新鲜度：
读数陈旧 = 我们压根没去问；读数新鲜但值没变 = 硬件真的没变。
现在这个判断只能在脑子里做，请把它做成脚本。

要求：

- 只用 **GET**，目标 `http://127.0.0.1:8747`（端口做成 `--port` 参数，别写死第三处）。
  用到的接口：`/api/ping`、`/api/health`、`/api/hardware`、`/api/logs?n=200`。
  **一个 POST 都不许发**（这个仓库里所有会改状态的动作都挂在 POST 上）。
  `/api/hardware` 现在的形状（2026-09-30 实测）：
  `channels[] = {name, alive, …}` 三条（`ec` / `mqtt` / `hid_light`）、
  `state.oem = {win_key_locked, …, ts}`（`ts` 是最后一次拿到 OEM 状态回复的 Unix 秒）、
  `caps = {能力名: {state, channel}}`。字段以接口现报为准，缺什么就在结论里说缺什么，别改 `app/` 去凑。
- 输出四段，一段一行结论，最后给**一句总判定**，面向"机主不是开发者"这种读者，
  中文，别贴 JSON 原文：
  1. **活着的通道**：`/api/hardware` 的 `channels[]` 逐个报 alive 与否、不在线时把 `detail` 里的原因带出来。
  2. **新鲜度**：`state.oem.ts` 距今多少秒（这是判据的主角）、面板 `uptime_s`；
     并给出"最近一次动作生效用了多久"这种可观察数字（拿 `state`/`channels` 里现有的字段算，
     **不许为了这个去改 `app/`**，字段不够就在结论里说明缺什么）。
  3. **能力表**：`caps` 按 `verified / blocked / unknown` 三堆列，每堆一行。
  4. **日志尾部**：`ERROR`/`Traceback` 计数与最后几条，纯文本原样。
- 面板没在跑时（连不上 8747）要**优雅**：一句"面板没在跑"加退出码 2，别抛栈。
- 顶上一段用法注释，并注明"只读，可随时运行"。
- 不许读 `data/` 下任何文件（尤其 `config.json`），数据全部走 HTTP 接口。

验收：面板在跑时一条命令出四段结论；`--port 1` 这类连不上时给优雅提示。

---

## 我 review 时会逐条跑的东西（照这个交，能一次过）

1. `git log --oneline main..workbuddy/assist` —— 提交是不是小步、信息是不是说清了每步做了什么。
2. `git diff --stat main..workbuddy/assist` —— **必须只出现这些路径**：
   `run_tests.py`、`app/fanrules.py`、`tests/test_fan_rules.py`、`tools/selfcheck.py`，
   加上这份 `TASKS-workbuddy.md` 本身（合并时我会决定留进 `docs/` 还是删掉）。
   出现 `app/act/`、`app/server/`、`app/web/`、`.gitignore`、`data/` 里的任何改动，
   这条我会直接退回。
3. `runtime/python.exe run_tests.py` —— 20 个文件（原 19 + 你新增的）全绿。
4. 我会自己读 `app/fanrules.py` 的**每一个地址常量**，和 `ec_gpd.py` 的 `_read_curve_table`
   逐条核对——这是任务二唯一可能出错且出错最贵的地方，请在注释里写清每条地址的推导依据。
5. `runtime/python.exe tools/selfcheck.py` 的实际输出我会看，确认没有一个 POST、没有泄出配置内容。
6. 全仓 grep 一遍 `ctypes`、`CreateFile`、`DeviceIoControl`、`paho`：新文件里应当一个都没有。

## 有卡住的地方

在 commit message 里写 `WIP:` 开头 + 一句话说明卡在哪，然后继续做别的任务，
不要自己扩大范围，也不要为了让测试变绿而放宽断言。

相关：`notes/hardware-channels.md` 6.11 第二十节（任务三的判据出处）、
`notes/verification-log.md` #31（任务二的完整背景）。
