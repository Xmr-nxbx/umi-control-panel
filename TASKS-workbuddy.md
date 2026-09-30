# 交接：给 workbuddy 的三条活（2026-09-30 第二批）

基线：`main` = `bcf5b9c`。分支：`workbuddy/assist2`（我已建好指针，你自己 `git switch`）。
上一条交接（`run_tests.py` / `app/fanrules.py` / `tools/selfcheck.py`）已合并，文档已删；
这次沿用同一套规矩：**三条活、文件互不重叠、净 diff 只许出现约定文件**。

目的不是追进度，是降低我（主 agent）的思考量。所以：**照抄下面的口径，别自由发挥**；
拿不准就停下来在交付说明里写问题，不要自己拍一个方向。

---

## 0. 铁律（违反任何一条，整批退回）

### 0.1 硬件与面板

1. **面板正在跑，不许停它。** 不许调 `/api/shutdown`，不许杀 `UmiPanel.exe` / `python.exe`。
   理由不是洁癖：面板一停，OEM 的 GCUService 会把功耗墙压回它自己存的 10 W 地板，
   i7-10875H 在 10 W 下全核只有约 0.8 GHz，机主会**当场**感觉到机器卡死
   （2026-09-30 20:0x 就是这么发生的，机主原话「电脑现在超级卡」）。
2. **不许跑 `tools/` 下任何会连 EC 或 MQTT 的脚本**：`ec_*.py`、`mqtt_watch.py`、
   `pl_detail_test.py`、`fan_table_test.py`、`tier_effect_test.py`、`acpi_probe.py`、
   `driver_ioctl_scan.py`、`hid_light_probe.py`、`kb_power_test.py` 一律不跑。
   你这三条活**全部是纯代码 + 单元测试**，一次硬件访问都不需要。
   唯一允许跑的工具是 `tools/selfcheck.py`（全程 GET，只读）。
3. **不许读 `data/config.json`**（里面有明文 MQTT 口令），不许把任何口令写进代码、
   测试、注释或交付说明。测试里要 broker 身份就造假数据。
4. 五条永久禁写字节，连提都不要提：`0x07C3`、`0x0770`、`0x087F`（写了会永久损坏电池，
   CVE-2026-64143）、`0x078C`、`0x07D0`（= `DBD1`）。不刷 BIOS，不写 UEFI 变量 / NVRAM。
5. **不许改写入闸门**：`app/act/channels/*.py` 里的能力（cap）判断、动作白名单、
   `send_action`、`allow_write` 相关逻辑，一行都不许动。唯一例外见任务 1 里点名的那一行。
6. **不许为了让测试变绿而放宽断言。** 这是我最担心的失败模式。断言写不出来说明实现有问题，
   不是断言有问题。

### 0.2 环境（踩过才写下来的）

7. 只能用 `runtime\python.exe`。裸 `python` 是 WindowsApps 的存根，**会静默什么都不做**
   （不报错、不输出），你会以为测试跑过了。
8. 没有 pytest。每个 `tests/test_*.py` 自己就能跑：
   `runtime\python.exe tests\test_history.py`。聚合跑用 `runtime\python.exe run_tests.py`
   （当前 20 个文件，你交付后应该是 22 个）。
9. 输出中文，控制台可能是 GBK 乱码：跑之前设 `PYTHONIOENCODING=utf-8`。
10. Git Bash 会把 `/s`、`/c`、`tasklist /FI` 这类参数当路径吃掉。要用 PowerShell 就整条
    `powershell.exe -NoProfile -Command "..."`。
11. 注释写中文，风格照现有文件：**只写「为什么」**（约束、实测结论、坑），
    不写「这行在干什么」。别写多段文档字符串。

### 0.3 本次会话的新实测结论（写代码时你需要知道的背景）

12. 同一台机器，风扇模式字节 `0xA0`(User_Fan_HiMode) 下 PL1=60 W，全核 zstd 只有
    1233~1554 MB/s @ **1554 MHz**（峰 2377）；下发 `OPERATING_TURBO_MODE` 后 PL1=75、
    字节变 `0x10`(Turbo_Mode)，全核 **3094~3317 MHz**（峰 4169）、1702~1775 MB/s，温度 60~67 °C。
13. 下发后约 **30 秒**（第 6 次采样），PL1 自己回到 60、字节回到 `0xA0` ——
    服务按它自己存的档位把墙重算了。
14. 所以：**频率不只被功耗墙管着**，档位在墙之外还另有一层限制。这条还没查清，
    是我留着的活，你别碰。任务 1 和任务 3 存在的意义就是让机主能**看见** 12~13 这两件事。

---

## 任务 1：功耗墙只读上面板（读数行 + 历史曲线 + OEM 边界）

**为什么**：`power_limit.write` 已经在 2026-09-30 19:51 做完可逆验证升到 `verified`
（PL1 10→60→10，EC 直读 2 秒内跟上），但面板上**完全看不到**这三个值。
机主遇到卡顿时无法自己判断「现在墙是多少、OEM 允许多少、是不是被服务改回去了」。
本任务只做**只读展示**，不做任何写入 UI（写入 UI 我留着，理由见结论 13）。

**允许改的文件（就这 5 个，多一个都退回）**

| 文件 | 允许做什么 |
|---|---|
| `app/history.py` | `FIELDS` 末尾加一个键 |
| `app/web/app.js` | 加一条读数行、一组新曲线、两处 `drawChart` 调用 |
| `app/web/index.html` | 加一个 canvas 和它的小标题 |
| `tests/test_history.py` | 追加用例（不许改已有用例） |
| `app/act/channels/mqtt_gcu.py` | **只许加一行**，见下 |

新增文件只许有 `tests/test_pl_readonly.py` 一个。

**实现要点（照抄）**

1. `app/history.py:22` 的 `FIELDS` 末尾加 `'pl1_setting'`。
   键名照 `app/act/hardware.py:29-36` 的 `STATE_KEYS` 抄，**不许自己造名**。
   `record()` 已经会先查 `sensor` 再查 `hardware`、缺读数写 `None`，你不用改 `record()`。
2. `app/act/channels/mqtt_gcu.py`：在**已有的** `self.detail['pl_write_reason'] = (`（约 245 行）
   那段赋值**之后**，加一行
   ```python
   self.detail['pl_bounds'] = self.pl_limits()      # 只读展示用：OEM 自己报的六个边界
   ```
   `pl_limits()` 只读通道缓存的报文，不发包、不碰硬件。
   **这个文件别的一行都不许动。**
3. `app/web/app.js:713` 的 `SERIES` 里**新增一组**（不要塞进 `temp` 或 `load`：
   60 W 和 1500 MHz 差 25 倍，同轴会把墙压成一条直线）：
   ```js
   wall: [
     { key: 'pl1_setting', label: 'PL1 功耗墙', axis: 'L', unit: ' W', color: '#c084fc' },
   ],
   ```
   然后在 `app.js:848-849` 和 `app.js:924-925` 那**两处** `drawChart(...)` 旁边各加一行
   `drawChart('chart-wall', SERIES.wall, d)` / `drawChart('chart-wall', SERIES.wall, histData)`
   （变量名照那两处上下文里已有的写法）。
4. `app/web/index.html`：照现有 `chart-temp` / `chart-load` 两个 canvas 的写法，
   加一个 `id="chart-wall"` 的 canvas 和一行小标题「功耗墙（EC 直读）」。
5. 读数行：照 `app.js:255` 那条 `hwRow('风扇转速', ...)` 的写法，加
   ```js
   hwRow('功耗墙 PL1/PL2/PL4', hw.pl1_setting != null
         ? ([hw.pl1_setting, hw.pl2_setting, hw.pl4_setting]
              .map((v) => (v == null ? '—' : v + ' W')).join(' / '))
         : '未知', verified('power_limit.write')),
   ```
6. OEM 边界：从 `/api/hardware` 的 `channels[]` 里找**带 `detail.pl_bounds` 的那一条**
   （别硬编码通道名）：
   ```js
   const ch = (hw.channels || []).find((c) => (c.detail || {}).pl_bounds);
   const bounds = ch ? ch.detail.pl_bounds : null;
   ```
   拿到就显示成「PL1 允许 10~120 W」；**`bounds` 为 `null` 或缺半边就整块不显示**，
   不许显示成 `0~255`、不许显示成空区间、不许编一个默认值。
7. 说明文字用我给的原文，**不许自己改写**（机主不是开发者，这句话是给他看的）：
   > 这三个值是 EC 直读的当前墙，由 OEM 服务按档位下发；面板改过之后，服务可能在几十秒内改回去。

**测试**

8. `tests/test_history.py` 追加一条用例：`FIELDS` 含 `'pl1_setting'`；
   `record({'hardware': {'pl1_setting': 60}})` 出来的行 `pl1_setting == 60.0`；
   缺这个键时是 `None` 而**不是 `0`**（0 会被画成「墙被压到 0」，是假话）。
9. 新增 `tests/test_pl_readonly.py`：照 `tests/test_pl_guard.py` 的 `inspect.getsource` 风格，
   断言 ① `mqtt_gcu.py` 源码里有 `self.detail['pl_bounds']`；
   ② `pl_bounds` **不出现在** `set_power_limits` 和 `send_action` 的函数体里
   （它是展示字段，不许变成写路径的一部分）；
   ③ `app/server/httpd.py` 源码里**没有**新增 POST 路由
   （数一下 `do_POST` 里 `elif path ==` 的条数，与基线一致）。

**验收**：`run_tests.py` 全绿；`git diff --stat main..workbuddy/assist2 -- app/server/httpd.py`
必须是空的（这个文件你根本不该碰）。

---

## 任务 2：给 `tools/fan_table_test.py` 的纯函数补单元测试

**为什么**：#31（风扇曲线写入实测）还差机主在场的那一次。这个工具是那一次实验的**载体**，
判据错一次就白烧一次机会，而它的纯函数现在**一条测试都没有**。
你的活是给已经写好的判据上保险，**不是改判据**。

**允许新增的文件：`tests/test_fan_table_tool.py`（只此一个）。**

**`tools/fan_table_test.py` 本体一行都不许改**——包括你以为的笔误、包括你觉得判据不够严。
发现问题只许写进交付说明，我来决定改不改。
（这个文件目前在仓库里是**未跟踪**状态，我还没提交；你可以直接 import 它写测试，
合并时我会把它一起提交。所以你的 diff 里只许出现 `tests/test_fan_table_tool.py`。）

**怎么 import**：`tools/` 不是包（没有 `__init__.py`），**不许**写 `import tools.fan_table_test`。
用 `importlib.util.spec_from_file_location('fan_table_test', <仓库根>/tools/fan_table_test.py)`
加载；仓库根从 `tests/` 的 `__file__` 往上**一层**（`dirname(dirname(abspath(__file__)))`）。
该模块在 import 时只做常量和 `sys.path` 处理，不连硬件，可以放心 import；
它会连带 import `app.act.channels.ec_gpd`（里面有 `ctypes`），这是**正常的**，
不算违反 0.1 第 2 条——铁律禁的是「跑」和「连」，不是 import。

**要钉住的三件事（函数签名照文件里已有的抄）**

1. `in_table_range(which, addr)` —— 写表时的地址白名单。上界是 `base + 0x2F`（点 15 的占空比槽），
   **不是** `base + 0x3F`：CPU 表 base=`0x0F00`，`0x0F30` 正好是 GPU 表的头，
   写宽一格就是两张表同时被改、两场实验混在一起。逐条断言：
   - CPU：`0x0F00` True、`0x0F2F` True、`0x0F30` **False**
   - GPU：`0x0F30` True、`0x0F5F` True、`0x0F60` **False**
   - `fanrules.BRACKET_ADDR`（`0x07C6`）True
   - `0x0783`（PL1，功耗墙区）**False**
2. `modified(points, delta)` —— 生成「整体抬高」的新表。要断言：
   - 点 0 的 `duty_pct` **恒为 `0.0`**（EC 硬规则：首点不是 0 % 就整块表被丢、退回内置曲线）
   - 其余点都抬高，且**封顶 100**
   - 结果过 `fanrules.validate('CPU', 新表)` **零错误**（单调不减是第二条硬规则）
   - `delta=0` 时与原表逐点相同
   - 不许改到入参 `points`（原地修改会让「还原」步骤拿着已被改脏的数据）
3. `active_point(points, temp)` —— 温度落在哪个点上。要断言：
   温度**正好等于**某点 `up_t` 时取该点（不是下一个）；低于点 0 的 `up_t` 时取点 0；
   高于最后一点的 `up_t` 时取最后一点。

**测试数据**：16 个点自己造一份最小可用的（`id` / `up_t` / `down_t` / `duty_pct`），
或者照 `tests/test_fan_rules.py` 里已有的实测基线造。**不许**去读真硬件拿数据。

---

## 任务 3：`tools/selfcheck.py` 加一条体检项「功耗墙现在是多少」

**为什么**：机主说「电脑超级卡」的时候，现有 13 条体检**全绿**——因为没有任何一条看功耗墙。
根因是面板停着的那段时间 GCUService 把 PL1 压回了 10 W。这条体检要能让「一眼看出机器为什么卡」。

**允许改的文件**：`tools/selfcheck.py`（新增一个 `sec_*` 函数 + 在 `main()` 里挂上去）。
新增文件只许有 `tests/test_selfcheck_wall.py` 一个。

**实现要点**

1. 照现有 `sec_channels(hw)` / `sec_caps(hw)` 的形状写 `sec_power_wall(hw)`，
   **返回 `(lines, problems)` 二元组**（这是本文件所有 `sec_*` 的统一约定）。
2. 数据只从 `hw['state']` 取：`pl1_setting`、`mode`、`hw_mode`。
   **不许**新增 HTTP 端点，**不许**直连 EC / MQTT，**不许**调 `app/` 下的通道类。
   用文件里已有的 `_is_num()` 判数字（它把 `bool` 排除在外，别自己写 `isinstance`）。
3. 三档结论，**只报现象不猜原因**（这是本项目的措辞纪律）：
   - `pl1_setting` 不是数字 →
     `功耗墙：EC 没报到 PL1（通道不在线或寄存器表里没有这一项）`，算 `problems` 里**可选**的那类，
     不许当成故障（EC 掉线时 `sec_channels` 已经报过了，重复报会淹掉真问题）。
   - `pl1_setting <= 15` →
     `功耗墙：PL1=<值> W，低到会把全核压在 1 GHz 上下（机器会明显卡）；这通常是 OEM 服务按它自己存的档位下发的`
   - 其余 → `功耗墙：PL1=<值> W（档位=<mode 或 hw_mode，取到的那个>）`
4. **不许**在输出里给「修复命令」，更不许自己去改墙。机主要的是一句人话的现状，不是一个建议。

**测试**：`tests/test_selfcheck_wall.py` 用假 `hw` 字典覆盖上面三档 + 两个边界
（`pl1_setting` 是 `True`/`False` 时必须走「没报到」那一档，因为 `_is_num` 排除 bool；
`hw` 是 `{}` 或 `state` 缺失时不许抛异常）。
导入方式同任务 2（`importlib` 加载 `tools/selfcheck.py`）。
注意：`selfcheck.py` 的 `main()` 会真发 HTTP，所以测试**只测 `sec_power_wall`**，不许调 `main()`。

---

## 交付方式

1. 分支 `workbuddy/assist2`（基线 `bcf5b9c`）。**切分支前先确认面板已停**：
   切工作树会把 `main.py` / `app/` 换成另一份，正在跑的面板会读到不一致的文件。
   你不许自己停面板（铁律 1）——所以要切分支，先在交付说明里写一句让我来切。
2. **一条任务一个提交**，提交信息中文，照 `git log --oneline -6` 的风格：
   一句话说清「为什么」，不是「改了什么」。
3. `git add` **只加你被允许的文件**。**不许 `git add -A` / `git add .`**：
   仓库根有未跟踪的 `.workbuddy/`（你自己的目录）和 `tools/fan_table_test.py`（我的，未提交）。
4. **不许 push。**
5. 交付说明写在仓库根 `HANDOFF-RESULT.md`（我合并时会删掉），内容：
   每条任务的实测命令与输出摘要、你自己发现的疑点、以及任何你**没做**的事和原因。
6. 交付前自己跑一遍：
   ```
   set PYTHONIOENCODING=utf-8
   runtime\python.exe run_tests.py
   runtime\python.exe tools\selfcheck.py
   ```
   两条都要贴输出摘要。`selfcheck.py` 只跑一次就够，别反复打面板的接口。

---

## 我留着做的（别顺手做，做了也退回）

- `send_action` 不查 cap 状态那条闸门漏洞：要同时判断「执行层加双查」和
  「`tools/` 首次验证怎么合法绕过」，方向反一次就把闸门削弱了。
- #31 风扇表写入实测（要机主在场听风扇，耳朵是判据的一部分）。
- #30 / #44 GCUService 内存转储（要机主授权提权）。
- 功耗墙的**写入** UI：先解决「服务 30 秒改回去」和「档位除了墙还改了什么」再说。
- 退出时是否把档位交还 OEM 服务（未决，机主还没表态）。

## 我合并时会查什么

- `git diff --stat main..workbuddy/assist2` 只出现约定的文件；
  出现 `app/server/httpd.py`、`app/act/hardware.py`、`app/act/power.py`、`app/policy/`、
  `.gitignore`、`data/` 任一改动 → 直接退回。
- `git diff main..workbuddy/assist2 -- app/act/` 只允许 `mqtt_gcu.py` 那一行 `pl_bounds`。
- 你新增的文件里 grep 到 `ctypes` / `CreateFile` / `DeviceIoControl` / `paho` /
  `socket.socket` / `requests` 任一个 → 退回（`selfcheck.py` 已有的 `urllib` 不算，那是它本来的）。
- 测试文件我自己通读一遍，并且自己造一个失败用例，确认你的断言**真的会红**。
- `run_tests.py` 22 个文件全绿、退出码 0。
