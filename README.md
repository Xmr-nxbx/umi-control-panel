# Umi Control Panel

机械革命 **Umi Pro 3（模具 GM5MG0Y，i7-10875H + RTX 3060 Laptop）** 的自研控制中心：
浏览器面板 + 托盘 + 登录自启，用来替代官方 Creator Center 的能耗档位切换，
并解决它解决不了的两件事——**「要么一直拉满、要么卡得没法用」** 和 **「息屏后亮屏，性能模式被自动关掉」**。

面板只监听 `127.0.0.1:8747`，无云端、无安装器、绿色运行。

---

## 1. 为什么要重做一个

| 现状 | 问题 |
| :--- | :--- |
| 官方 Creator Center | 只有 办公/造物者 两档，办公档功耗墙把机器压到不能用；造物者档一直高功耗高噪音 |
| 息屏/锁屏 | 系统会自动把硬件档降回办公档，亮屏后不会恢复，用户必须再按一次实体键 |
| OpenRevo | 功能对标（EC 直写 PL1/PL2/PL4、风扇曲线、RGB），但 release 只有 exe、仓库无源码；本机跑一段时间后 Tauri 界面僵死，重启后再也起不来，且它的 `takeover_oem` 会把 OEM 的 GCUBridge 服务停用禁用，导致「两条控制通道全断」 |
| 上一代 PowerPilot（Python） | 调度逻辑已验证有效，但绑死在 OEM MQTT 通道上；UI 是后端拼 HTML，不好维护，且没有托盘 |

本项目取三者之所长：**调度用已验证的策略，硬件层走 open-revo 同款 EC/ACPI 协议（自己实现），UI 独立成前端，常驻用托盘 + 登录自启。**

## 2. 现在的真实能力状态（不吹）

面板的「能力矩阵」卡会把下面这些状态原样呈现，不可用的开关直接置灰并写明原因：

| 能力 | 状态 | 说明 |
| :--- | :--- | :--- |
| 四档调度写 powercfg（方案 / EPP / turbo / min-max） | **写得进去，但本机不控频率** | 见下方「实测结论」。写入与注册表回读都正常，问题在固件层 |
| 实际 CPU 频率显示（PDH `% Processor Performance`） | **可用** | 面板右上角实时显示，档位到底有没有效果一眼可见 |
| 内置跑分对比（单线程 / 多线程 / 短任务延迟 / 内存） | **可用** | `main.py --bench compare` 或面板「跑分对比」卡，纯标准库，不下载任何软件 |
| 息屏掉档拦截、驻留期、亮屏缓冲 | **可用** | 纯软件层，见 `app/policy/scheduler.py`，19 条决策表测试覆盖 |
| CPU 温度 / 占用、GPU 温度/功耗/占用、内存、空闲、前台进程 | **可用** | PDH 热区 + nvidia-smi + Win32 API，**零内核驱动** |
| 硬件档位读写（EC） | **只读已打通** | `\\.\ACPIDriver` + `IOCTL_GPD_ACPI_ECREAD`，普通权限即可，见第 6 节 |
| 风扇转速 / 占空比 / 风扇模式 | **可用** | 实测 2093/2123 RPM、30%、Turbo_Mode；模式可写（自动/强冷/加速），需 `allow_write` |
| 电池电量 / 温度 / 循环 / 充电阈值 | **可用** | 电量与系统 API 互相印证（100% = 100%），阈值读到 80%/75% |
| 各模式出厂 PL 默认值、机型 ID | **可用（只读）** | 办公 35W / 均衡 60W / 省电档 75W；ProjectID=15 |
| 实时 PL1/PL2 写入、硬件档位切换 | **未生效 / 未确认** | 写进去会自清、性能无变化；档位寄存器语义矛盾，面板显示「未确认」不假装 |
| 风扇曲线 | **未验证** | 只有 EC 通道能提供；不做任何驱动穷举（见第 6 节） |
| OEM MQTT 兜底通道 | **对端已停** | GCUBridge 服务当前 `Stopped / Disabled`（被 OpenRevo takeover 干的），需要恢复服务才可用 |

### 实测结论：为什么「切了档却体会不出来」

2026-09-29 在这台 GM5MG0Y 上逐项验证（`tools/tier_effect_test.py`，全程只改用户态电源属性）：

| 设置 | 单线程频率 | 全核频率 | 单线程跑分 |
| :--- | :--- | :--- | :--- |
| 最大处理器状态 100% | 4222 MHz | 3332 MHz | 3.47 Mops/s |
| 最大处理器状态 50% | 4273 MHz | 3329 MHz | 3.70 Mops/s |
| **30% + 禁用睿频 + EPP=100 + 最低频率 5%** | 4283 MHz | 3316 MHz | 3.68 Mops/s |

四档跑分（`tools/out/bench-compare.txt`）同样：省电 100.1 分、均衡 100.0、流畅 101.3、性能 103.4，
频率区间 4246～4359 MHz，**只差 2.6%**。

结论：**这台机器的 CPU 频率由 BIOS/EC 接管，Windows 电源计划那一层压不住**
（睿频开关写进去、回读也对，但硬件不理）。所以：

- 光靠 powercfg 做不出 Creator Center 那种「模式」差异，这不是调度逻辑的问题；
- 要真正换挡，必须打通 EC（PL1/PL2、风扇）或恢复 OEM GCUBridge 通道；
- 面板不会假装有效：跑分卡片会现场算出这个结论并红字标注（`app/bench.py::power_verdict`）。

唯一能量出来的小差别是**短任务延迟**（降频后来一下活）：性能档 247 ms vs 省电档 269 ms，
约 8%——这正是「亮屏回来点东西卡一下」的量纲，也是软件层还能优化的部分。

> 兜底通道的 broker 身份（clientId / 用户名 / 口令）**不进仓库**：
> 需要时把 `mqtt_identity.example.json` 复制成 `data/mqtt_identity.json` 填写即可，
> `data/` 已在 `.gitignore` 里。没填身份时该通道会显示「需配置」并直接跳过连接，不会反复重试。

## 3. 快速开始

```bat
:: 1) 准备运行时（便携 Python 3.12，仓库里不入库）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_runtime.ps1

:: 2) 生成本机 EC 寄存器表（只需一次；只读 Creator Center 的元数据，不碰设备）
scripts\生成EC寄存器表.bat

:: 3) 启动面板（会自动开浏览器）
scripts\启动面板.bat

:: 4) 开机自启（HKCU Run，不需要管理员）
scripts\安装开机自启.bat

:: 自检 / 跑分 / 停止（停止会还原改过的电源设置）
scripts\EC只读自检.bat
scripts\跑分对比.bat
scripts\面板状态.bat
scripts\停止面板.bat
```

入口只有 `main.py`，四种模式：

| 命令 | 作用 |
| :--- | :--- |
| `runtime\python.exe main.py` | 常驻：面板 + 托盘 + 调度循环 |
| `main.py --bench compare` | 四档逐一对比跑分（约 2 分钟），面板在跑就交给面板执行 |
| `main.py --bench current` | 只测当前档位，约 15 秒 |
| `main.py --one-shot` | 采一次快照打 JSON，自检用，不改电源设置 |
| `main.py --stop` | 让运行中的实例优雅退出 |
| `main.py --ec-test` | EC **只读**自检：验证通道并打印寄存器读数（普通权限即可） |

## 4. 架构

```
main.py                     入口 + 启动期兜底（崩溃留 fatal-*.log，不闪退）
app/
  cli.py                    参数、单实例、端口顺延、生命周期
  daemon.py                 1 秒节拍：遥测 → 决策 → 执行 → 看门狗 → 硬件跟随
  config.py                 默认值 + 深合并 + 原子落盘；坏配置改名备份后照常启动
  singleton.py              内核命名互斥（不用锁文件，进程死了系统回收）
  policy/scheduler.py       四档自适应状态机（纯判定，可注入假时钟做单测）
  bench.py                  内置跑分：单线程/多进程多线程/短任务延迟/内存 + 相对基准指数
  sense/system.py           负载/空闲/内存/电源/前台进程（ctypes，无驱动）
  sense/thermal.py          CPU 热区温度（PDH）
  sense/clock.py            CPU 实际频率（PDH % Processor Performance × 标称频率）
  sense/gpu.py              GPU 遥测（nvidia-smi）
  act/power.py              powercfg 方案 + EPP + turbo + min/max，注册表回读校验
  act/hardware.py           通道总管：能力协商、切档、息屏掉档守护
  act/channels/ec_gpd.py    主通道：EC 直连（\.\ACPIDriver + IOCTL_GPD_ACPI_ECREAD/ECWRITE）
  act/channels/mqtt_gcu.py  兜底通道：OEM GCUBridge MQTT + 命令白名单
  act/channels/gcu_actions.json  只允许发送已在本机逆向字符串中确认存在的 Action
  server/httpd.py           标准库 ThreadingHTTPServer + REST + 静态白名单
  web/                      index.html / app.js / style.css（无构建步骤）
  tray/tray.py              Shell_NotifyIcon 托盘，图标颜色随档位变化（代码自绘 ICO）
tests/test_scheduler.py     19 个调度决策场景
scripts/                    setup_runtime.ps1、make_bats.py（bat 生成器）、10 个入口 bat（GBK+CRLF）
tools/                      全部离线只读的逆向与验证工具，产物落 tools/out（已 gitignore）
  gen_ec_map.py             从本机 Creator Center 生成 data/ec_map.local.json（不入仓库）
  oem_constant_dump.ps1     反射导出 OEM 程序集的常量与枚举（IOCTL 码、寄存器名、模式取值）
  oem_pinvoke_dump.ps1      反射导出 P/Invoke 与包装方法签名
  oem_il_dump.ps1           IL 字节 + 标记还原（GCUService 的 IL 被加密，此路不通，留作记录）
  pe_inspect.py             PE 体检：节表/导入/导出/.NET 判定（发现了 ReadEC/WriteEC 导出）
  ioctl_layout_probe.py     在驱动镜像里定位 IOCTL 常量、按 .pdata 还原函数边界、解 cmp 立即数
  ec_gpd_read_test.py       单次只读验证（带电量自校验）
  ec_write_test.py          可逆写验证（风扇模式 + 85°C 温度保险 + 自动还原）
  ec_pl_test.py             功耗墙写入的可逆实验（写→全核跑分→还原）
  tier_effect_test.py       逐项验证 Windows 电源旋钮在本机是否有效（结论：无效）
  freq_probe.py             PDH 频率计数器可用性探测
```

**设计原则**

1. **决策与执行分离**：`Scheduler.decide()` 是纯函数，`PowerExecutor.apply()` 才有副作用，所以调度能被单测覆盖。
2. **能力如实降级**：任何开关都先查能力表；通道没验证过就置灰 + 写明原因，绝不假装成功。
3. **命令白名单**：EC/OEM 相关动作必须在 `gcu_actions.json` 里能查到，查不到一律不发——没逆向清楚的东西不许往硬件上打。
4. **可恢复性优先**：不用锁文件、配置坏了自动退回默认值、端口占用自动顺延、启动期异常落 `fatal-*.log`——针对 open-revo「一次故障后永久起不来」的教训。

## 5. 调度策略（解决「要么拉满要么卡」）

四个执行档，各自绑定电源方案 + EPP + turbo 模式 + min/max processor state：

| 档 | 方案 | EPP | turbo | min AC | 说明 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 性能 perf | 高性能 | 0 | 2 激进 | 100 | 重负载 / 游戏进程前台 |
| 流畅 mid | 平衡 | 25 | 2 | 50 | 中等负载，不切方案不惊动 EC |
| 均衡 bal | 平衡 | 50 | 1 温和 | 25 | 日常默认，风扇不骤起 |
| 省电 eco | 平衡 | 85 | 1 | 5 | 空闲 + 低负载 |

关键规则（都有对应单测）：

- **升档快、降档慢**：进性能档要 CPU≥60%/GPU≥40% 持续 2.5s；退出必须 CPU<35% 且 GPU<15% 持续 8s，**且只退到流畅档**，不会一路滑到省电。
- **软退出**：长时间中等负载（CPU<55% 持续 40s）也会降到流畅档——防止开视频会议时一直拉满。
- **驻留期 300s**：面板/实体键/亮屏等人工意图触发的性能档，不会因为一时低负载就被冲掉。
- **亮屏缓冲 15s**：检测到「刚回到电脑前」（空闲 ≥5s 突然 <0.6s）保底给流畅档，杜绝亮屏后爬频卡顿那几秒。
- **温度趋势预判**：5 秒窗口升温 ≥4°C 且 ≥70°C 且有负载 → 立刻解锁性能档。重任务刚点火就把功耗墙放开，任务快进快出，风扇高转的总时长反而更短。
- **温度保护**：≥95°C 封顶到均衡档，≤90°C 解除。
- **看门狗**：别的软件把方案改了会抢回来，但带 3 秒冷却 + 锁内复查，避免和 OEM 服务互相抢方向盘（上一代实测过 7 秒拉锯）。

## 6. 硬件通道与安全红线

**⚠️ 本项目不允许对任何内核驱动做穷举/暴力 IOCTL 探测。** 上一轮开发中，
对 `\\.\ACPIDriver` 暴力发送 7000+ 组 IOCTL 的行为导致过一次蓝屏（DRIVER_POWER_STATE_FAILURE）。

### 6.1 已经打通的门（2026-09-29 实测确认）

| 目标 | 普通权限实测 | 结论 |
| :--- | :--- | :--- |
| `\\.\ACPI`（微软文档化 AML 求值） | **打不开**，err=2 | 用户态无入口，此路不通 |
| `\\.\ACPIDriver`（UWACPIDriver.sys，RUNNING/DEMAND_START） | **可读写打开** | 唯一的 EC 门，不需要管理员 |

它不认微软的 `IOCTL_ACPI_ASYNC_EVAL_METHOD(0x32C004)`（发过去只会把缓冲原样回显），
而是自己实现了一族 `IOCTL_GPD_*`（设备类型 **0x9C40**，和微软的 0x32 完全不同）：

| 功能 | IOCTL | 输入 | 输出 |
| :--- | :--- | :--- | :--- |
| 读 EC | `0x9C40A488` | 4 字节地址 | 4 字节值 |
| 写 EC | `0x9C40A48C` | 4 字节地址 + 1 字节值（共 8 字节） | 4 字节状态 |

**证据链（全部离线只读，可复现）**：

1. IOCTL 码来自 `GCUService.exe`（.NET）的常量表，
   用 `tools/oem_constant_dump.ps1` 反射读出（`IOCTL_GPD_ACPI_ECREAD=0x9C40A488` 等 27 个）；
2. 这些码在**正在运行的** `UWACPIDriver.sys` 分发链里逐个确认存在
   （`tools/ioctl_layout_probe.py`：`cmp dword [rsp+24h], 9C40A488h; je …`）；
3. 缓冲布局是把驱动的 ECREAD/ECWRITE 处理函数字节级读出来的：
   它自己拼 `'AeiC'+'ECRR'`（写是 `'ECRW'`，参数个数 2）、Length=0x28，
   从调用者输入缓冲 memcpy 4 字节地址（写再从 +4 拷 1 字节值），
   然后才用 0x32C004 转交 ACPI.sys，最后校验 `'AeoB'`；
4. **实测自校验**：读电量寄存器返回 100，与 `GetSystemPowerStatus` 的 100% 一致
   （`tools/ec_gpd_read_test.py`，单次、只读、不重试）；
5. **实测写生效且可逆**：风扇模式字节 Turbo_Mode(0x10) → Normal_Mode(0x00)，
   20 秒内主风扇 2436 → 2105 RPM、占空比 35% → 30%，随后原值写回并回读一致
   （`tools/ec_write_test.py`，带 85°C 温度保险，超温立即还原）。

寄存器「名字 → 地址」表**不在仓库里**：由 `tools/gen_ec_map.py` 在每台机器上
从本机安装的 Creator Center 现场生成到 `data/ec_map.local.json`（已 gitignore）。
代码里只出现名字，一个地址数字都不写死——换机型也能用，且不涉及分发 OEM 私有定义。

### 6.2 已经读到 / 还读不懂的

| 项目 | 状态 |
| :--- | :--- |
| 主/副风扇转速、左右占空比、风扇模式字节 | **可用**（实测 2093/2123 RPM、30%、Turbo_Mode） |
| 电池电量、电池温度、循环次数、充电阈值 80%/75% | **可用**（电量与系统 API 互相印证） |
| 各模式出厂 PL 默认值（办公 35W / 均衡 60W / 省电档 75W）、TCC offset | **可用**（只读） |
| 机型标识 ProjectID=15、ModuleID=54 | **可用** |
| 风扇模式写入（自动 / 强冷 / 加速） | **可用**，需 `allow_write=true` |
| 硬件档位（办公/均衡/狂暴）寄存器 | **未确认**：`MAFAN_CONTROL_BYTE` 说 Turbo_Mode、`MyFanCCI_Mode_Index` 说 0，两者矛盾，面板如实显示「未确认」，不猜 |
| 实时 PL1/PL2 写入 | **未生效**：写 `ADDR_PL1_SETTING_VALUE=35` 回读为 35 但 1 秒内自清 0，全核跑分与频率毫无变化（对照 20.71 → 21.22 Mops/s）。说明它是请求寄存器而非状态寄存器，或需要 TRIGGER/STATUS 握手时序 |

### 6.3 规矩（不可协商）

1. 禁止对任何内核驱动做穷举/循环探测；只发**已从 OEM 代码里确认**的 IOCTL 码；
2. 新的写操作必须满足三条才允许下发：语义有 OEM 枚举/常量表佐证、可逆（先存原值、测完写回并回读）、
   带温度保险（超温立即还原）；
3. 写 EC 默认关闭，需 `config.hardware.ec.allow_write = true`；
   即使打开，档位/PL 这类语义未确认的寄存器仍然**拒绝写入**（代码里硬拦，不是配置项）；
4. 连续 5 次失败即熔断关闭句柄，60 秒后才复查，不空转打驱动；
5. 兜底通道的 broker 凭据不入仓库（见第 2 节的 `data/mqtt_identity.json`）。

## 7. 运行方式（目标是"不用盯着"）

- 开机自启：`HKCU\...\Run\UmiControlPanel` → `UmiPanel.exe main.py --supervise --no-browser`；
- 守护模式 `--supervise`：子进程异常退出会自动拉起（实测强杀后 6 秒恢复）；
  面板里点「停止面板服务」属于正常退出（code=0），守护**不会**复活它；
  10 分钟内异常退出超过 5 次则停止自动重启，避免启动即崩时空转刷屏；
- 单实例用内核命名互斥 `Global\UmiControlPanel`，进程死了由系统回收，
  不存在"残留锁导致再也起不来"——这正是 open-revo 栽过的坑；
- 端口被占自动顺延 10 个并把实际端口写进 `data/port`；配置损坏则改名备份后用默认值继续跑。

## 8. 已知问题 / 待办

- [x] EC 只读通道：已打通并自校验（电量与系统 API 一致），风扇/电池/PL 默认值/机型 ID 全部可读
- [x] EC 写通道：布局已确认，风扇模式写入实测生效且可逆还原
- [ ] **档位切换（办公/均衡/狂暴）**：还没找到生效路径。`PL*_SETTING_VALUE` 写入会自清、
      全核性能无变化；`MAFAN_CONTROL_BYTE` 与 `MyFanCCI_Mode_Index` 语义矛盾。
      下一步只做静态分析（CreatorCenter.exe 的方法名/IL、TRIGGER+STATUS 握手时序），
      确认前不下发任何猜测性写入
- [ ] 风扇曲线读写：寄存器已定位（`ADDR_L*_PWM_DEFAULT_MYFAN2/3`），语义未确认，暂不写
- [ ] 电池保养三档（长效/平衡/健康）：阈值可读（80%/75%），写入语义未确认，**故意不做**，不编造
- [ ] 兜底通道：需恢复 GCUBridge 服务（`scripts\启用造物者档控制.bat`，会弹 UAC）
- [ ] 托盘图标的桌面可见性需本机确认（沙箱内截不到图）
- [ ] 实体「造物者模式」按键：按键直连 EC 不走键盘通道，
      现在可以靠轮询 `MAFAN_CONTROL_BYTE` / `MyFanCCI_Mode_Index` 的变化来捕获事件

## 9. 协议与许可参考

EC/ACPI 交互的设计思路参考社区项目 [OpenRevo](https://github.com/faintonce/open-revo)（MIT，Copyright (c) 2026 faintonce）。
本项目为独立实现，不含其源码或二进制；厂商私有寄存器映射与机型表不在本仓库分发范围内。
本仓库采用 **MIT License**（见 `LICENSE`），与 OpenRevo 保持一致。

机械革命/同方（Uniwill）相关硬件行为来自本机实测与离线静态分析记录，
不保证适用于其它模具；在其他机型上开启 EC 写入前，请先只做只读验证。
