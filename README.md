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
| 五档自适应调度（powercfg 方案 / EPP / turbo / min-max） | **可用** | 非管理员即可写，已在本机实测生效并注册表回读校验 |
| 息屏掉档拦截、驻留期、亮屏缓冲 | **可用** | 纯软件层，见 `app/policy/scheduler.py`，19 条决策表测试覆盖 |
| CPU 温度 / 占用、GPU 温度/功耗/占用、内存、空闲、前台进程 | **可用** | PDH 热区 + nvidia-smi + Win32 API，**零内核驱动** |
| 硬件档位读写（EC） | **需管理员** | `\\.\ACPI` 打不开（err=2）。跑一次 `scripts\ACPI只读探测.bat` 即可定位方法路径 |
| 风扇转速 / 风扇曲线 | **未验证** | 只有 EC 通道能提供；不做任何驱动穷举（见第 6 节） |
| OEM MQTT 兜底通道 | **对端已停** | GCUBridge 服务当前 `Stopped / Disabled`（被 OpenRevo takeover 干的），需要恢复服务才可用 |

> 兜底通道的 broker 身份（clientId / 用户名 / 口令）**不进仓库**：
> 需要时把 `mqtt_identity.example.json` 复制成 `data/mqtt_identity.json` 填写即可，
> `data/` 已在 `.gitignore` 里。没填身份时该通道会显示「需配置」并直接跳过连接，不会反复重试。

## 3. 快速开始

```bat
:: 1) 准备运行时（便携 Python 3.12，仓库里不入库）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_runtime.ps1

:: 2) 启动面板（会自动开浏览器）
scripts\启动面板.bat

:: 3) 开机自启（HKCU Run，不需要管理员）
scripts\安装开机自启.bat

:: 自检 / 停止（停止会还原改过的电源设置）
scripts\面板状态.bat
scripts\停止面板.bat
```

入口只有 `main.py`，四种模式：

| 命令 | 作用 |
| :--- | :--- |
| `runtime\python.exe main.py` | 常驻：面板 + 托盘 + 调度循环 |
| `main.py --one-shot` | 采一次快照打 JSON，自检用，不改电源设置 |
| `main.py --stop` | 让运行中的实例优雅退出 |
| `main.py --probe-acpi` | **只读**枚举 ACPI 命名空间（需管理员），用于 EC 逆向 |

## 4. 架构

```
main.py                     入口 + 启动期兜底（崩溃留 fatal-*.log，不闪退）
app/
  cli.py                    参数、单实例、端口顺延、生命周期
  daemon.py                 1 秒节拍：遥测 → 决策 → 执行 → 看门狗 → 硬件跟随
  config.py                 默认值 + 深合并 + 原子落盘；坏配置改名备份后照常启动
  singleton.py              内核命名互斥（不用锁文件，进程死了系统回收）
  policy/scheduler.py       四档自适应状态机（纯判定，可注入假时钟做单测）
  sense/system.py           负载/空闲/内存/电源/前台进程（ctypes，无驱动）
  sense/thermal.py          CPU 热区温度（PDH）
  sense/gpu.py              GPU 遥测（nvidia-smi）
  act/power.py              powercfg 方案 + EPP + turbo + min/max，注册表回读校验
  act/hardware.py           通道总管：能力协商、切档、息屏掉档守护
  act/channels/ec_acpi.py   主通道：EC 直连（文档化 ACPI AML 求值）
  act/channels/mqtt_gcu.py  兜底通道：OEM GCUBridge MQTT + 命令白名单
  act/channels/gcu_actions.json  只允许发送已在本机逆向字符串中确认存在的 Action
  server/httpd.py           标准库 ThreadingHTTPServer + REST + 静态白名单
  web/                      index.html / app.js / style.css（无构建步骤）
  tray/tray.py              Shell_NotifyIcon 托盘，图标颜色随档位变化（代码自绘 ICO）
tests/test_scheduler.py     19 个调度决策场景
scripts/                    setup_runtime.ps1、make_bats.py（bat 生成器）、6 个入口 bat
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
因此：

- EC 交互只走 **微软文档化接口**：`IOCTL_ACPI_ASYNC_EVAL_METHOD`（0x32C004）+
  `ACPI_EVALUATE_INPUT`/`ACPI_OUTBUFFER`（签名 `AeiC`/`AeoB`）。
  静态分析显示 OEM 驱动 UWACPIDriver.sys 本身也只是把请求转成这个 IOCTL 交给 ACPI.sys，
  由 BIOS 的 AML 方法真正读写 EC——我们直接调同一层，绕开第三方驱动的私有 IOCTL。
- 只读枚举（`IOCTL_ACPI_ENUM_DEVICES`）先行，定位 BIOS 里的 EC 方法完整路径。
- 拿到路径后**第一次实测只发单次、完整缓冲的 ECRR 读**；失败即停手回报，绝不循环重试。
- 写 EC（档位/PL1/风扇）需要 `config.hardware.ec.allow_write = true` 才可能生效。

已知的候选方法名（来自 open-revo 二进制 `.rdata` 的静态分析）：`ECRR`（读）、`ECRW`（写）、`SMRW`。
请求缓冲要点：`AeiC` + 方法名 + 入参长度 + 出参长度 + 数据；响应校验头是 `AeoB`。

## 7. 已知问题 / 待办

- [ ] EC 通道需要管理员：`\\.\ACPI` 非管理员打不开 → 用 `scripts\ACPI只读探测.bat` 提权跑一次，把 `tools\out\acpi-namespace.txt` 贴回来定位方法路径
- [ ] 风扇转速/曲线：只能靠 EC 读，未验证前面板显示「不可控」
- [ ] 电池保养三档（长效/平衡/健康）：命令未逆向成功，**故意不做**，不编造
- [ ] 兜底通道需要恢复 GCUBridge 服务（当前 Stopped/Disabled），恢复前该卡片区始终灰
- [ ] 托盘图标在真实桌面上的人工确认（沙箱内无法截图验证）
- [ ] 实体「造物者模式」按键：按键直连 EC 不走键盘通道，事件源需靠 EC 读或 MQTT 兜底捕获

## 8. 协议与许可参考

EC/ACPI 交互的设计思路参考社区项目 [OpenRevo](https://github.com/faintonce/open-revo)（MIT，Copyright (c) 2026 faintonce）。
本项目为独立实现，不含其源码或二进制；厂商私有寄存器映射与机型表不在本仓库分发范围内。
本仓库采用 **MIT License**（见 `LICENSE`），与 OpenRevo 保持一致。

机械革命/同方（Uniwill）相关硬件行为来自本机实测与离线静态分析记录，
不保证适用于其它模具；在其他机型上开启 EC 写入前，请先只做只读验证。
