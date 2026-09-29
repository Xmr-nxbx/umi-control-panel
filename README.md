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
| 内置跑分对比（全核/单核 zstd 压缩 + CoreMark + 短任务延迟） | **可用** | `main.py --bench compare` 或面板「跑分对比」卡；zstd 与 CoreMark 均为开源工具，见下 |
| 息屏掉档拦截、驻留期、亮屏缓冲、换挡防抖 | **可用** | 纯软件层 + Windows 会话解锁事件，见 `app/policy/scheduler.py`，22 条决策表测试覆盖 |
| CPU 温度 / 占用、GPU 温度/功耗/占用、内存、空闲、前台进程 | **可用** | PDH 热区 + nvidia-smi + Win32 API，**零内核驱动** |
| 历史曲线（温度 / 实际频率 / 占用 / 风扇转速） | **可用** | 5 秒一点、窗口 30 分钟、档位切换点画成竖线；纯 canvas，断网也能看 |
| 调度性格（安静 / 标准 / 性能） | **可用** | 三个按钮代替 12 个阈值数字；换档即时生效，重启后保持 |
| 一键诊断 | **可用** | 面板「复制诊断信息」= 13 项体检结论 + 最近 40 行日志；剪贴板不可用时自动改成下载 |
| EC 直连通道 | **已打通** | `\\.\ACPIDriver` + `IOCTL_GPD_ACPI_ECREAD/ECWRITE`，普通权限即可，见第 6 节 |
| 硬件模式（实体键那个字节） | **可读 + 可写** | 就是实体键写的同一个字节，也是本机硬件模式的总开关（PL1、风扇 PWM 表、TGP 都跟着它走，见 6.2/6.4）。按键现在会真的切控制意图（全亮=锁定性能、半亮=自适应、不亮=锁定省电），并在屏幕上方弹一条提示 |
| 屏幕提示（OSD） | **可用** | 只在「人动手」时弹：网页/托盘点击、实体按键；自适应自己换档不弹（打扰）。画不出来自动退化成只记日志 |
| OEM 那套档位（office/balance/turbo） | **本机没有** | 机主确认 Creator Center 界面上不存在办公/均衡/狂暴；GCUBridge 报的 `OperatingMode` 取值也不在 OEM 枚举里，所以面板一律显示「未知」并附原始值，那三个假的档位按钮已撤掉（见 6.6） |
| 风扇转速 / 占空比 | **可用** | 性能档实测 4123 RPM / 自适应 3663 RPM / 省电档 3019 RPM |
| 电池电量 / 温度 / 循环 | **可用** | 电量与系统 API 互相印证（100% = 100%）、循环 83 次、电池温度 24.4°C |
| 电池充电档（平衡 / 健康 / 长效） | **可读，语义已确认** | EC `ADDR_AP_OEM_BYTE4` 高半字节 1/2/0，与 GCUBridge 的 `BALANCEDMODE`/`HEALTHYMODE`/`PERFORMANCEDMODE` 逐条对齐（见 6.8）。**写入未做可逆验证**；「三档各等于百分之多少」这个问题**不成立**——这个 EC 家族按充电**电压**封顶，代码里根本没有百分比（见 6.11 第三节） |
| Win 键锁定 | **可读可写，已做完可逆验证** | EC `ADDR_STAUTS_BYTE` 0/1，与 `WINKEY_LOCK`/`WINKEY_UNLOCK` 三次点击三次跳变对齐（见 6.8）；2026-09-30 又走通了「下发 → EC 变化 → 两条通道回读 → 还原」整条链（见 6.9），面板上有按钮 |
| 充电阈值寄存器 | **恒为 0，原因已查明（答案换过一次）** | 早先按「百分比门控没启用」解释（见 6.10）；离线核对 EC 反汇编后发现那套门控模型来自**别的板子**——判据是 `0x742` bit2，本机读到 2（bit2=0），机制不在场。这个家族真正的封顶是充电**电压** `0x0522/0x0523`，而它 host 写不住（实测 2000 次连读一次都没抓到写入值，<101µs 被 EC 夺回，见 6.11 第三节）。所以读到 0 的意思是「本机没有百分比这个概念」，不是「有限制但没开」。门控字节仍属永久禁区（6.3 第 8 条） |
| 各模式出厂 PL 默认值、机型 ID | **可用（只读）** | 办公 35W / 均衡 60W / 省电档 75W；ProjectID=15 |
| 实时 PL1/PL2 写入 | **未生效，已找到原因** | 写进去会自清、性能无变化（见 6.2）；离线核对 OEM 代码后确认那一组寄存器是 MyFan3 一代机型的落点，本机是 CML 平台。改功耗墙走风扇字节，不裸写 PL |
| 风扇曲线（16 点表） | **可读，已实测** | 布局取自同源机型反编译出的 `SetEcFanTable`/`GetEcFanTable`，本机只读转储两轮（每轮 96 次读、**0 次写**）解出 CPU/GPU 各 16 点，面板上点一下就读（见 6.11 第八节）。**写入未验证**：可逆验证要机主在场，能力表里 `fan.curve.write` 一直是 `blocked`，面板不给按钮 |
| OEM GCUBridge 通道（MQTT） | **已连通，只读在用** | 服务恢复后 `127.0.0.1:13688` 可连；一条 `GETSTATUS` 就拿到全部开关状态（Win 锁、触摸板、灯条、键盘背光、独显直连…，见 6.6）。**写通道待验证**：没做过可逆验证之前面板不点亮 |

### 实测结论：为什么「切了档却体会不出来」

2026-09-29 在这台 GM5MG0Y 上逐项验证（`tools/tier_effect_test.py`，全程只改用户态电源属性）：

| 设置 | 单线程频率 | 全核频率 | 单线程跑分 |
| :--- | :--- | :--- | :--- |
| 最大处理器状态 100% | 4222 MHz | 3332 MHz | 3.47 Mops/s |
| 最大处理器状态 50% | 4273 MHz | 3329 MHz | 3.70 Mops/s |
| **30% + 禁用睿频 + EPP=100 + 最低频率 5%** | 4283 MHz | 3316 MHz | 3.68 Mops/s |

四档跑分（`tools/out/bench-compare.txt`，**用的是已退役的纯 Python 负载**，新负载的数字见下一节）
同样：省电 100.1 分、均衡 100.0、流畅 101.3、性能 103.4，频率区间 4246～4359 MHz，**只差 2.6%**。
两代负载、两次独立实测给出同一个结论，所以这不是测量方法的问题。

结论：**这台机器的 CPU 频率由 BIOS/EC 接管，Windows 电源计划那一层压不住**
（睿频开关写进去、回读也对，但硬件不理）。所以：

- 光靠 powercfg 做不出 Creator Center 那种「模式」差异，这不是调度逻辑的问题；
- 要真正换挡，必须打通 EC（PL1/PL2、风扇）或恢复 OEM GCUBridge 通道；
- 面板不会假装有效：跑分卡片会现场算出这个结论并红字标注（`app/bench.py::power_verdict`）。

唯一能量出来的小差别是**短任务延迟**（降频后来一下活）：性能档 247 ms vs 省电档 269 ms，
约 8%——这正是「亮屏回来点东西卡一下」的量纲，也是软件层还能优化的部分。

### 2026-09-29 晚：跑分负载换成开源工具（zstd + CoreMark）

上面表格里的 Mops/s 来自**已退役**的纯 Python 负载，只作历史留档。机主反馈那一版
「耗时长、进度条跑不到头、看不懂、甚至省电比性能分高」，四条全中，于是负载整体换掉：

| 模块 | 工具 | 量什么 | 单次时长 |
| :--- | :--- | :--- | :--- |
| 全核吞吐 | 官方 zstd v1.5.7 `-b9 -T0` | 多核压缩 MB/s | 约 7 秒 |
| 单核吞吐 | 官方 zstd `-b12 -T1` | 单核压缩 MB/s | 约 8 秒 |
| 核心计算 | EEMBC CoreMark（官方源码现编） | 迭代/秒 | 约 12 秒 |
| 短任务延迟 | 内置 burst | 冷启动一下活的毫秒数 | 约 5 秒 |

两个外部工具都不进仓库（`tools/bin` 已 gitignore）：zstd 从 GitHub release 取官方二进制，
CoreMark 从 EEMBC 官方仓库取源码、用本机 gcc 现场编（`scripts\编译CoreMark.bat`），
编不出来就跳过这一项并按剩下三项重新归一化，**不会让跑分失败**。

CoreMark 有一条官方硬规矩：单次跑不满 10 秒不给分数。所以标定只取它打印的
`Iterations/Sec`（短跑也给），正式跑目标 12 秒；万一某档比标定时快太多没吐分，
迭代数加半补跑一次。进度条由守护进程 0.5 秒一跳按「已完成档数 + 档内百分比」推进，
并给剩余时间 ETA，不会再出现「跑到 4% 不动」的情况。

### 2026-09-29 晚：四档实测（新负载，均衡档 = 100 分）

全程 167 秒，四档各约 30 秒，跑分期间没有任何一档触发温度保护：

| 档位 | 总分 | 全核压缩 | 单核压缩 | CoreMark | 短任务 | 实测频率 | 最高温 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 省电 | 95.9 | 188 MB/s | 41.4 | 28532 | 280 ms | 4347 MHz | 78°C |
| 均衡 | 100.0 | 204 MB/s | 41.3 | 29309 | 267 ms | 4372 MHz | 81°C |
| 流畅 | 96.3 | 185 MB/s | 39.9 | 28614 | 264 ms | 4365 MHz | 80°C |
| 性能 | 99.4 | 199 MB/s | 42.0 | 29248 | 270 ms | 4429 MHz | 82°C |

**结论照实写进面板了（红色「实测警告」）**：性能档只比省电档快 5.9%，四档频率
全挤在 4347～4429 MHz（跨度 1.9%），说明 **BIOS/EC 接管了频率，powercfg 那一层
压不住**——档位分数在测量噪声里排不出稳定顺序，所以面板不再宣称「哪档更快」。

> 2026-09-30 补：这段实验只证明了「powercfg 那一层没用」，它本身没错，但它当时
> 得出的另一半结论（「性能墙落在 EC 的某个还没找到的功耗墙寄存器上」）已经被
> 全表观察推翻——墙就是**风扇字节**这个硬件模式总开关，见 6.2 / 6.4 / 6.6。

### 2026-09-29 晚：模式词统一 + 屏幕提示（OSD）

> 本节讲词表和 OSD 本身；实体键三态各自代表什么档，2026-09-30 按全表观察改正过，
> 以本节下面那张表为准。

机主反馈「网页一套词、弹窗一套词，对不上号」，现在**全项目只有一套模式名**：
省电 / 均衡 / 流畅 / 性能。控制意图不再叫「办公 / 狂暴」，而是直接说锁哪一档：

| 意图 | 名字 | 含义 |
| :--- | :--- | :--- |
| `auto` | 自适应 | 按负载和温度自动换档，标题显示的是当前实际那一档 |
| `office` | 锁定省电 | 一直用省电模式，不自动切换 |
| `balance` | 锁定均衡 | 一直用均衡模式 |
| `turbo` | 锁定性能 | 一直用性能模式 |

词表只有一处定义（`app/policy/scheduler.py` 的 `TIER_LABELS` / `INTENT_NAMES`），
网页、托盘菜单、OSD、日志都从它取；`tests/test_fankey.py` 里有一条用例专门盯着
「弹窗标题必须和意图名是同一句话」，改词漏一处就会红。

实体「造物者模式」键以前只改 EC 风扇字节、标题和意图都不动，机主以为按键坏了。
现在三态会真的切意图，并且**只在人动手时弹屏幕提示**：

| 按键灯 | 落到哪个字节 | 意图跟着变成 | OSD 大字 | 实际差别（实测） |
| :--- | :--- | :--- | :--- | :--- |
| 全亮 | `Turbo_Mode` | 锁定性能 | 性能模式 | PL1 75W、风扇强冷，满载最快也最吵 |
| 半亮 | `Normal_Mode` | 自适应 | 自适应模式 | 风扇和功耗墙都交回 EC 自动 |
| 不亮 | `User_Fan_HiMode` | 锁定省电 | 省电模式 | **PL1 砍到 10W**、风扇走自定义曲线，满载慢 23~26% |

> 「不亮」这一态曾经被写成「只接管风扇、电源模式不变」，于是面板照旧显示自适应，
> 机主看到的状态和机器实际跑的档对不上。2026-09-30 的全表观察证实它会连着把
> `PL1_SETTING_VALUE` 从 75 改到 10，是**硬件模式**换档，不是单纯的风扇档（见 6.2）。

自适应自己换档**一律不弹**——干活干到一半屏幕上跳东西比没提示更烦。
OSD 是自绘的分层窗口（`app/tray/osd.py`，无第三方依赖），右下角出现、2.2 秒自动消失、
不抢焦点也不进 Alt-Tab；`config.json` 里 `tray.osd` 设 `false` 可整体关掉。
网页上的「风扇字节 → 模式名」也不在 JS 里另抄一份，而是由后端从
`app/tray/fankey.py` 经 `/api/state` 的 `meta.fan_mode_words` 下发，两边不可能分叉。

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
scripts\一键体检.bat
scripts\EC只读自检.bat
scripts\跑分对比.bat
scripts\下载跑分工具.bat
scripts\编译CoreMark.bat
scripts\面板状态.bat
scripts\停止面板.bat

:: 实测硬件：按键三态满载对比（约 7 分钟，会停面板、测完自动重启）
scripts\造物者三态实测.bat

:: 找功能落点：只读观察 EC 寄存器变化。跑起来后去操作 Creator Center，
:: 结果存 tools\out\ec-watch-all.txt —— 这是唯一零风险拿到剩余语义的办法
scripts\观察EC变化.bat        &rem 21 个语义寄存器，0.5s 一轮，抓按键这类瞬时事件
scripts\观察EC全表.bat        &rem 125 项全表，2s 一轮，破灯效/阈值/曲线

:: 找功能落点（更全）：一次同时抓 OEM 命令通道 + EC 全表，用来破键盘背光/灯条/
:: Win 锁/触摸板/电池养护这些开关。会先停面板（避免抢 broker 身份），完事自动重启
scripts\观察OEM通道.bat       &rem 产物：tools\out\mqtt-watch.txt + ec-watch-all.txt
```

入口只有 `main.py`，四种模式：

| 命令 | 作用 |
| :--- | :--- |
| `runtime\python.exe main.py` | 常驻：面板 + 托盘 + 调度循环 |
| `main.py --bench compare` | 四档逐一对比跑分（约 3 分钟），面板在跑就交给面板执行 |
| `main.py --bench current` | 只测当前档位，约 15 秒 |
| `main.py --one-shot` | 采一次快照打 JSON，自检用，不改电源设置 |
| `main.py --stop` | 让运行中的实例优雅退出 |
| `main.py --health` | 一键体检：自启/运行时/面板/传感器/EC 通道逐项检查 |
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
  bench.py                  内置跑分：zstd 全核/单核压缩 + CoreMark + 短任务延迟 + 相对基准指数
  sense/system.py           负载/空闲/内存/电源/前台进程（ctypes，无驱动）
  sense/thermal.py          CPU 热区温度（PDH）
  sense/clock.py            CPU 实际频率（PDH % Processor Performance × 标称频率）
  sense/gpu.py              GPU 遥测（nvidia-smi）
  act/power.py              powercfg 方案 + EPP + turbo + min/max，注册表回读校验
  act/hardware.py           通道总管：能力协商、切档、息屏掉档守护
  act/channels/base.py      通道基类 + 能力名 + 风扇字节→硬件模式映射（EC 与 UI 共用一处）
  act/channels/ec_gpd.py    主通道：EC 直连（\.\ACPIDriver + IOCTL_GPD_ACPI_ECREAD/ECWRITE）
                            + 16 点风扇表按需只读转储（fan_curve()，一个写都不发）
  act/channels/mqtt_gcu.py  兜底通道：OEM GCUBridge MQTT + 命令白名单
  act/channels/gcu_actions.json  只允许发送已在本机逆向字符串中确认存在的 Action
  server/httpd.py           标准库 ThreadingHTTPServer + REST + 静态白名单
  web/                      index.html / app.js / style.css（无构建步骤）
  history.py                遥测历史环形缓冲（5 秒一点、保留 30 分钟、60 秒落盘，重启接上）
  tray/tray.py              Shell_NotifyIcon 托盘 + 会话锁定/解锁通知（同一个消息窗口收事件）
  tray/osd.py               屏幕提示：color-key 分层窗口，自己带消息循环线程，画不出就退化
  tray/fankey.py            屏幕提示文案：全项目唯一一套模式词（省电/均衡/流畅/性能）
tests/test_scheduler.py     25 个调度决策场景（含换挡防抖两条、温度趋势预判三条）
tests/test_bench_score.py   11 个跑分算分场景（方向、归一化、zstd 解析、老成绩整批丢弃）
tests/test_fankey.py        14 个屏幕提示场景（一套模式词、三态不张冠李戴、实体键映射到哪个意图）
tests/test_history.py       7 个历史缓冲场景（含假时钟与「坏文件不拖垮启动」）
tests/test_supervise_guard.py 8 个守护卡死判定场景（含「健康时不许误杀」）
tests/test_tray_session.py  6 个会话事件分发场景（不锁屏幕也能验证解锁那条路）
tests/test_config_profile.py 6 个调度性格读写场景（盯「补丁漏进 config.json」那个 bug）
tests/test_singleton_port.py 6 个单实例与端口场景（盯 2026-09-30 那个双实例并跑）
tests/test_oem_status.py     7 个 GCUBridge 状态解析场景（盯 UNLOCK 被 endswith 判成 LOCK 那个坑）
tests/test_battery_winlock.py 7 个解码场景（把 6.8 那张表的字节值逐条钉住）
tests/test_write_gates.py     10 个写入闸门场景（跨站 Origin 一律 403、allow_write=false 一律拒发）
tests/test_fan_curve.py       12 个风扇表解码场景（拿本机实测基线钉住布局、三个坑与「只读」自检）
tests/test_rom_fv.py          12 个固件卷解析场景（合成镜像，钉住 DataOffset 差 4、LZMA_Alone 13 字节头与错位重对齐）
tests/test_rom_ec_fantable.py 26 个 EC 默认风扇表场景（合成镜像，钉住 48 字节记录布局、合理性判据与相位过滤）
scripts/                    setup_runtime.ps1、make_bats.py（bat 生成器）、17 个入口 bat（GBK+CRLF）
tools/                      逆向与验证工具，产物落 tools/out（已 gitignore）。默认只读；
                            带写的那些都自带「存原值→温度保险→还原回读」，脚本自检 writes 计数
  gen_ec_map.py             从本机 Creator Center 生成 data/ec_map.local.json（不入仓库）
  oem_constant_dump.ps1     反射导出 OEM 程序集的常量与枚举（IOCTL 码、寄存器名、模式取值）
  oem_pinvoke_dump.ps1      反射导出 P/Invoke 与包装方法签名
  oem_il_dump.ps1           IL 字节 + 标记还原（GCUService 的 IL 被加密，此路不通，留作记录）
  pe_inspect.py             PE 体检：节表/导入/导出/.NET 判定（发现了 ReadEC/WriteEC 导出）
  ioctl_layout_probe.py     在驱动镜像里定位 IOCTL 常量、按 .pdata 还原函数边界、解 cmp 立即数
  ec_gpd_read_test.py       单次只读验证（带电量自校验）
  ec_write_test.py          可逆写验证（风扇模式 + 85°C 温度保险 + 自动还原）
  ec_pl_test.py             功耗墙写入的可逆实验（写→全核跑分→还原，结论：不生效）
  ec_mode_probe.py          快照全表 125 个寄存器 → 写一个候选 → 差分 → 还原
  ec_watch.py               只读变化观察器：21 项语义寄存器（0.5s 一轮）或 125 项全表（2s 一轮）
  ec_fantable_dump.py       只读转储活着的 16 点风扇表（CPU+GPU）与 10 个语义寄存器；
                            只发 ECREAD，自检 writes==0，非 0 直接退出码 3；跑前要停面板
  mqtt_watch.py             GCUBridge 命令观察器：只订阅，最多发一条 GETSTATUS，掉线自动重连
  ec_mode_bench.py          造物者三态满载实测（正反序各一遍，写前存原值、测完还原、97°C 保险）
  tier_effect_test.py       逐项验证 Windows 电源旋钮在本机是否有效（结论：无效）
  freq_probe.py             PDH 频率计数器可用性探测
  fetch_bench_tools.py      取官方 zstd 二进制到 tools/bin（不进仓库）
  build_coremark.py         从 EEMBC 官方源码现编 coremark.exe，编完必须自检出分数才算成功
  wmi_scan.ps1              只读扫本机 ACPI/WMI 设备与 root\wmi 类（普通权限看不见 GUID 类，见 6.5）
  wmi_guid_probe.ps1        只读探测同方 MIFS 那个 GUID 在不在本机（需管理员权限才有意义）
  rom_fv_dump.py            只读解开 UEFI ROM 的固件卷（_FVH/FFS/段 + 自带 lzma 解压），
                            找机型名、OEM 模块名、厂商字符串表；不刷写、不碰任何设备，见 6.11 第九节
  rom_ec_fantable.py        只读解出 ROM 里各份 EC 镜像自带的**默认风扇表**（48 字节定长记录），
                            并可与 ec_fantable_dump.py 的活表对照，见 6.11 第十节
```

**设计原则**

1. **决策与执行分离**：`Scheduler.decide()` 是纯函数，`PowerExecutor.apply()` 才有副作用，所以调度能被单测覆盖。
2. **能力如实降级**：任何开关都先查能力表；通道没验证过就置灰 + 写明原因，绝不假装成功。
3. **命令白名单**：EC/OEM 相关动作必须在 `gcu_actions.json` 里能查到，查不到一律不发——没逆向清楚的东西不许往硬件上打。
4. **可恢复性优先**：不用锁文件、配置坏了自动退回默认值、端口占用自动顺延、启动期异常落 `fatal-*.log`——针对 open-revo「一次故障后永久起不来」的教训。

## 5. 调度策略（解决「要么拉满要么卡」）

四个执行档，全部写在**同一个**「平衡」方案里（EPP + turbo 模式 + min/max processor state）：

| 档 | 方案 | EPP | turbo | min AC | 说明 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 性能 perf | 平衡 | 0 | 2 激进 | 100 | 重负载 / 游戏进程前台，同时把风扇推到强冷 |
| 流畅 mid | 平衡 | 25 | 2 | 50 | 中等负载 |
| 均衡 bal | 平衡 | 50 | 1 温和 | 20 | 日常默认，风扇不骤起 |
| 省电 eco | 平衡 | 85 | 0 | 5 | 空闲 + 低负载 |

> 2026-09-29 起四档不再单独 `/setactive 高性能`：实测四档吞吐本来就在噪声内，
> 换方案只带来「Windows 电源计划一直在变」这种观感，还顺改掉硬盘睡眠、PCIe 省电这些无关设置。

关键规则（都有对应单测）：

**调度性格**：面板上给的是「安静优先 / 标准 / 性能优先」三个按钮，而不是 12 个阈值数字——
机主不需要理解 `cpu_perf=60` 是什么意思。性格是**只读叠层**：运行时把 `SCHED_PROFILES`
里的增量盖到 scheduler 上，落盘的 config.json 只存用户自己的值，
所以换回标准档一定能干净地回到默认阈值。
（上一版就是这里出的错：补丁原地写进 `cfg.data`，换回标准后旧阈值还留在内存里，
下一次保存把 `cpu_perf=70` 落进了文件——面板显示标准档，跑的却是安静档。）

- **升档快、降档慢**：进性能档要 CPU≥60%/GPU≥40% 持续 2.5s；退出必须 CPU<35% 且 GPU<15% 持续 8s，**且只退到流畅档**，不会一路滑到省电。
- **换挡防抖**：两次降档之间至少隔 45s；刚从哪一档掉下来，20s 内不许原样爬回去。
  升到**更高**的目标档永远放行，所以游戏刚开、任务刚点火仍然立刻给满。
  这条是被实测逼出来的：没有它时性能/流畅每 10~30 秒互切一次，连切 14 分钟。
- **软退出**：长时间中等负载（CPU<55% 持续 40s）也会降到流畅档——防止开视频会议时一直拉满。
- **驻留期 300s**：面板/实体键/亮屏等人工意图触发的性能档，不会因为一时低负载就被冲掉。
- **亮屏缓冲 15s**：人回到电脑前先给流畅档，杜绝亮屏后爬频卡顿那几秒。
  信号有两个：Windows 的**会话解锁事件**（`WTSRegisterSessionNotification`，确定、零延迟），
  以及「空闲 ≥5s 突然 <0.6s」的推断（`--no-tray` 时只剩这一个）。
  缓冲期内即使负载还没起来也会从均衡抬到流畅，且**不受换挡防抖限制**——
  防抖是掐传感器噪声的，人工事件等 20 秒才给劲就白设计了。
- **温度趋势预判**：5 秒窗口升温 ≥4°C 且 ≥70°C 且有负载 → 立刻解锁性能档。重任务刚点火就把功耗墙放开，任务快进快出，风扇高转的总时长反而更短。
- **温度保护**：≥95°C 封顶到均衡档，≤90°C 解除。
- **风扇让位于人**：实体键按过一次后 900 秒内不自动跟随；当前值是「自定义曲线」（0x80 位）时**永远**不自动覆盖；面板点按钮同样获得这段优先窗口。
- **看门狗**：别的软件把设置改了会补写，但带 3 秒冷却 + 锁内复查，避免和 OEM 服务互相抢方向盘（上一代实测过 7 秒拉锯）。每 120 秒复核一次，值没变时既不写注册表也不产生日志。

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
| 电池电量、电池温度、循环次数 | **可用**（电量与系统 API 互相印证：100% = 100%，循环 83 次） |
| 充电阈值 `ADDR_BATTERY_CHARGE_LIMIT_UP/DOWN` | **读到 0**：早先记过 80%/75%，现在两次实测都是 0 且无法复现。和 `ADDR_RGBKB_LEVEL_R`、`ADDR_MYFAN2_L1_PWM`、`ADDR_SINGLEKBL_ENABLE` 是同一类——**用户没设过就是 0**，所以「读到 0」不等于功能不可用，但也说明这些是 RAM 里的用户配置，写入语义没确认前一律不动 |
| 各模式出厂 PL 默认值、TCC offset | **可用**（只读）：OEM 常量表里写着 35W / 60W / 75W 三档。但这三个数是**静态常量**，不代表本机当前在跑哪一档——实测硬件模式的 PL1 是 75W（性能）/ 10W（省电），10W 这个值在常量表里根本没有，所以别拿这张表当「当前档位」读 |
| 机型标识 ProjectID=15、ModuleID=54 | **可用** |
| 硬件模式写入（自适应 / 性能 / 省电 / 风扇加速） | **可用**，需 `allow_write=true`。写的就是实体键那个字节，取值只允许 OEM 枚举 `MyFanCTLByteFlag` 里的名字，写完立刻回读校验 |
| 造物者模式按键字节 `ADDR_MAFAN_CONTROL_BYTE` | **已确认，而且它就是硬件模式总开关**：2026-09-30 全表差分抓到，按一次键这个字节 `Turbo_Mode(0x10) ↔ User_Fan_HiMode(0xA0)` 的同时，`PL1_SETTING_VALUE` 75↔10、`MYFAN2_L1/L4_PWM`、`L2_PWM_DEFAULT_MYFAN3`、`DynamicBoost_MaxinumTGP`、`ConfigurableTGP_DynamicBoost_CTRL_BYTE` 整组跟着换。LED 对应关系由机主现场确认：**全亮=性能、半亮=自适应、不亮=省电** |
| 硬件档位落在哪个寄存器 | **已确认（2026-09-30）**：不存在单独的「档位寄存器」，档位就是上面那个风扇字节，功耗墙是它的结果。早先那条「按键只动风扇、PL 纹丝不动」的记录是**在 GCUBridge 服务被停用的状态下测的**——那时按键只改了风扇字节，没人去套用整套配置；服务恢复后同一个键就变成了两态循环并带着功耗墙一起走（这一点是从两份日志的差异推出来的，不是直接观察到的因果） |
| 实时 PL1/PL2 写入 | **未生效**：写 `ADDR_PL1_SETTING_VALUE=35` 回读为 35 但 1 秒内自清 0，全核跑分与频率毫无变化（对照 20.71 → 21.22 Mops/s）。离线核对 OEM 代码后有了原因：那一组 PL 寄存器（0x783-0x785）是 **MyFan3 一代机型**的落点，本机是 CML 平台，写了不认。要改功耗墙就走风扇字节（已验证）或 OEM 的 MQTT 通道，**不裸写 PL** |

### 6.3 规矩（不可协商）

1. 禁止对任何内核驱动做穷举/循环探测；只发**已从 OEM 代码里确认**的 IOCTL 码；
2. 新的写操作必须满足三条才允许下发：语义有 OEM 枚举/常量表佐证、可逆（先存原值、测完写回并回读）、
   带温度保险（超温立即还原）；
3. 写 EC 默认关闭，需 `config.hardware.ec.allow_write = true`；
   即使打开，档位/PL 这类语义未确认的寄存器仍然**拒绝写入**（代码里硬拦，不是配置项）；
4. 连续 5 次失败即熔断关闭句柄，60 秒后才复查，不空转打驱动；
5. 兜底通道的 broker 凭据不入仓库（见第 2 节的 `data/mqtt_identity.json`）；
6. 只读轮询也要限速：观察器每轮的读请求数 = 寄存器数 ÷ 轮询间隔，
   全表模式（125 项）固定 2 秒一轮（≈62 次/秒），和 21 项 × 0.5 秒是同一个量级——
   实测这个速率跑了 4000+ 次读没有任何异常。不许为了快而把间隔调小。
   **按需转储也归这条管**：风扇表一轮 96 项、间隔 0.03 秒（≈33 次/秒）、用时 2.9 秒，
   结果缓存 10 秒，而且只有点了按钮才读，不进 2 秒轮询。
7. HTTP 上的每个写接口都要过两道闸：**来源闸**（只收本机面板页面发起的请求）+
   **配置闸**（`allow_write`）。两道都得在**执行层**里查，不能只在某一个接口上查——
   漏一个入口就等于开了一个不设防的硬件写口（见 6.9）。
8. **永久禁区**（不是"待验证"，是不做）：
   - 不写充电门控字节 `0x7C3` / `0x770` / `0x87F`。上游 Linux 驱动因为
     **2020 年前后的机型开充电限制会把电池永久搞坏**，直接把强开路径封死了
     （CVE-2026-64143，*platform/x86: uniwill-laptop: Do not enable the charging
     limit even when forced*）。本机正是那一代。要改充电档只走 OEM 自己的
     `BatteryProtection/Control` 动作，而且要先经机主同意。
   - 不刷 BIOS、不写 UEFI NVRAM（`UEFI_Firmware.dll` 的 `WriteUefi` 一律不碰）。
     GM5MG0Y 是 AMI 板，公开记录里**降级被拒、改版刷入失败、有人警告会黑屏变砖**。
   - 不写 UEFI 变量。`UniWillVariable {9f33f85c-13ca-4fd1-9c4a-96217722c593}`
     是 NV+BS+RT 的**运行时可写**变量，OS 里写 `0x33` 就能让 BIOS 下次开机把
     隐藏的内存超频菜单放出来（外部资料在 GM7MG7P 上实机验证过）。正因为它这么
     容易写，才更要一律不碰：那个菜单里有 VDDQ 1.10–1.65 V 的电压项。
   - 不写 `0x078C`（键盘背光状态镜像）。它看着像个开关，其实 EC 固件自己会从
     内部 `0x0826` 生成 bits5-7，Fn+F6/F7 热键也写它，厂商服务另有一条
     「把亮度镜像回 EC」的路径。三个写者共用一个字节，我们插进去就是抢。
     本机灯效走 ITE 8291 的 USB HID，压根不经过 EC（见 6.11）。
   - 不写 `0x07D0`。它带 `DO-NOT-WRITE-BLIND`，而且存在 **CLOBBER HAZARD**：
     DSDT 的 `T1WR 0x1173` 分支会把 GPU 功率 `Arg1*8` 写进**同一个物理字节**，
     和充电百分比的刻度直接冲突（55% = `0x37`，TGP 最大 = `0xF8`）。
     必须先读回确认当前是谁在用，才谈得上写。

### 6.4 造物者模式按键三态实测（2026-09-29 测，2026-09-30 补上原因）

`tools/ec_watch.py` 当时抓到：按一次实体键，`ADDR_MAFAN_CONTROL_BYTE` 在
`Normal_Mode(0x00) → User_Fan_Mode(0x80) → Turbo_Mode(0x10)` 上循环，
其它 20 个语义寄存器（含 PL1/PL2/PL4、`MyFanCCI_Mode_Index`）全程不变。

**那次观察是在 GCUBridge 服务被停用的状态下做的，所以只看到了半件事。**
2026-09-30 服务恢复后重跑全表（`logs/观察EC全表`），同一个键变成两态循环
`Turbo_Mode(0x10) ↔ User_Fan_HiMode(0xA0)`，而且**功耗墙跟着一起走**：
`PL1_SETTING_VALUE` 75↔10、`MYFAN2_L1_PWM` 3↔7、`MYFAN2_L4_PWM` 5↔15、
`L2_PWM_DEFAULT_MYFAN3` 0↔255、`DynamicBoost_MaxinumTGP` 5↔15、
`ConfigurableTGP_DynamicBoost_CTRL_BYTE` 3↔7、`AP_OEM_BYTE6` 4→0→7→3。
也就是说这个字节是硬件模式的总开关，风扇曲线和功耗墙都是它的结果。
下面那张表量到的差距，因此不只是「风扇转速不同」，而是整套硬件配置不同。

那三态到底差多少？`tools/ec_mode_bench.py`：每态写入后稳定 8 秒，
再全核满载 25 秒（16 个进程），旁路采频率 / 温度 / 转速，测完把原值写回并回读。

| 状态 | 多线程吞吐 | 平均频率 | 风扇均速 | 满载最高温 |
| :--- | :--- | :--- | :--- | :--- |
| Normal_Mode（自适应，LED 半亮） | 19.71 / 19.97 Mops/s | 3096 / 3101 MHz | 3663 / 3603 RPM | 83.1 / 78.1 °C |
| Turbo_Mode（性能，LED 全亮） | **21.51 / 21.04**（+9.1% / +5.4%） | 3461 / 3472 MHz | 4123 / 3766 RPM | 78.1 / 80.1 °C |
| User_Fan（省电，LED 不亮） | **15.15 / 14.68**（−23.1% / −26.5%） | 2319 / 2333 MHz | 3019 / 2687 RPM | 67.1 / 72.1 °C |

（两组数字是正序与反序各跑一遍：`ec_mode_bench.py` 与 `ec_mode_bench.py reverse`。
同一态两次相差 ≤1.5%，说明差别来自档位本身，不是测量顺序或机器冷热。
表里的 Mops/s 来自已退役的纯 Python 负载，绝对值不要和现在的 zstd/CoreMark 成绩比。）

三条能直接用的结论：

1. 只改这一个字节就能拉开 6%～26% 的满载吞吐 —— 这台机器的性能墙在 **EC 侧**，
   不在 Windows 电源计划那一侧（和 6.2、第 2 节的实测互相印证）；
2. 「省电」这一态慢四分之一的原因是**功耗墙被压到 10W**，不是风扇不够：
   它的温度反而最低（67°C），频率却只有 2320 MHz 上下 —— 典型的被功率/电流限住。
   所以面板的自动跟随永远不会选它，只有用户按实体键或明确点按钮才会进这一态，
   而且一旦进来，面板就**不再自动覆盖**（`fan_user_owned()` + 900 秒优先窗口）；
3. 性能比自适应快 5%～9%、转速高 160～460 RPM —— 代价是噪音。所以面板只在性能档写 Turbo，
   并且给了防抖与优先窗口，不会为了几个百分点每分钟把风扇拨来拨去。

复现：`scripts\造物者三态实测.bat`（会先停面板、测完自动重启面板，全程约 7 分钟，风扇很吵）。

### 6.5 「走 WMI 而不是裸 EC」这条线索的核查结果（2026-09-29）

社区建议参考 Linux 的 `tongfang-mifs-wmi`：同方模具的性能模式/风扇/键盘背光/触摸板锁
是通过 **WMI GUID 方法**暴露的，用户态就能调，不用碰驱动 IOCTL。上游源码里那个 GUID 是
`B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B`（探测纯靠 GUID，无 DMI 白名单），命令格式是
32 字节缓冲：`[0]=0xFA 读 / 0xFB 写`，`[1]=访问类型`，`[3]=功能号`，`[4..]` 是参数；
功能号 8=性能档位、9=独显直连、10/16/17/18=灯效与键盘背光、11=Fn 锁、12=触摸板锁、
13=风扇转速、20=强冷开关、21=PWM 上限、22=CPU 温度。**如果这台机器上有它，
我们要的所有功能都有一条 OEM 自己背书的路，一个 EC 写操作都不用猜。**

核查结论：**这台机器的模具是 Uniwill，不是同方（Tongfang）**——两家是不同的代工厂，
机械革命都用过。证据：本机 EC 驱动 `UWACPIDriver.sys`（UW = UniWill）、设备节点
`ACPI\INOU0000`（名字就叫 ACPIDriver）、触摸板 `ACPI\UNIW0001`、
OEM 服务目录 `C:\Program Files\OEM\CreatorCenter\UniwillService\`。
所以「同方平台的 WMI 接口」在这台机器上不一定存在。

本机探测结果**不作数**：`tools/wmi_scan.ps1` 与 `tools/wmi_guid_probe.ps1`（都只读、
只列类名方法名、不调用任何方法）在普通权限下连微软自己的 WMI 映射 GUID
`05901221-D566-11D1-B2F0-00A0C9062910` 都查不到，说明这个权限级别根本看不见任何
GUID 类——所以「查不到 B60BFB48」既不能证明它不在，也不能证明它在。
要下定论只有一条路：**用管理员权限再跑一次 `tools\wmi_guid_probe.ps1`**（只读，不写硬件）。

在那之前，不基于这条线索写任何代码。

### 6.6 OEM GCUBridge 通道：已经连上，而且它比 EC 更好问话（2026-09-30）

GCUBridge 服务恢复后（`scripts\启用造物者档控制.bat`），`127.0.0.1:13688` 这个
本机 broker 就活了。`tools/mqtt_watch.py`（**只订阅，最多发一条 `GETSTATUS` 问状态，
不发任何控制命令**）连上去问一句，OEM 自己就把所有开关的当前状态全报了出来：

| 字段 | 当前值 | 说明 |
| :--- | :--- | :--- |
| `WinKey` | `WINKEY_STATUS_LOCK` | **Win 键此刻是锁着的** |
| `TouchpadToggle` | `TOUCHPAD_TOGGLE_ON` | 触摸板开着 |
| `LightBar` | `LIGHTBAR_STATUS_ON` | 灯条开着 |
| `SingleColorKBBL` | `SINGLE_COLOR_KBBL_STATUS_ON` | 键盘背光是**单色**款，不是 RGB |
| `UsbCharger` | `USB_CHARGER_STATUS_OFF` | USB 关机充电关着 |
| `OSD` | `OSD_HIDDEN_OFF` | OEM 的屏幕提示是显示的 |
| `FnKey` / `NumPad` | `FNKEY_UNLOCK` / `NUMPAD_UNLOCK` | 都没锁 |
| `DiscreteGpuDirectConnectionSwitch` | `..._TOGGLE_ON`，`Support` | 独显直连说「支持且已开」 |
| `DGpu` | `NV_CTRL_PANEL_AUTOSELECT` | 但这里又说「自动选择」——两者对不上，待观察 |
| `AcRecoverySwitch_Support` | `NotSupport` | 插电恢复本机不支持 |
| `DisplayMode` + 各组亮度/色温 | `DISPLAY_STANDARD_MODE`，标准/游戏/视频/阅读/自定义五组 | 显示模式是独立于性能档的一套东西 |

为什么这条路比裸 EC 强：**报文体里的 Action 名和参数就是 OEM 自己的词汇**，
不用猜寄存器；而且 `Tray/Status` 里的 `OperatingMode` 还能反过来校验我们的判断。

同时暴露了一个我们此前编错的地方：`OperatingMode` 的取值以前被映射成
`1→均衡、2→均衡、3→狂暴`，那是照抄别家的猜测，和 OEM 自己的枚举
`OperatingMode{Office:0, Turbo:2}` 直接矛盾，而实测报的正是没定义的值。
现在只认枚举里有的（0=Office、2=Turbo），其余一律 `unknown` 并把原始值暴露在通道状态里；
`mode.write` 也从「连上就算可用」降级成**待验证**——按 6.3 的规矩，
没做过「写 → 观察改了什么 → 还原」的可逆验证之前，面板不点亮这个开关
（所以那三个假的「办公/均衡/狂暴」按钮已经从界面上撤掉了）。

复现：`scripts\观察OEM通道.bat`（先停面板，240 秒里同时抓 MQTT 命令与 EC 全表，
结束后自动重启面板）。**必须停面板**：MQTT 的规矩是同一个 clientId 只允许一条连接，
后连上的会把先连上的顶掉；另外 GCUBridge 还会周期性检查「clientId 同名进程在不在」，
不在就踢线（约 26 秒一次），所以观察器要独占这个身份。

### 6.7 灯效、Win 锁、电池三档、触摸板：第二次观察问出了什么（2026-09-30 00:27）

机主按清单点了一遍 Creator Center，两份报告在 `logs/观察2`（EC 全表）和
`tools/out/mqtt-watch.txt`（OEM 命令）。**MQTT 那半只录到 00:28:32 就断了**——
原因不是 broker，是我们自己：另一个脚本在观察途中又把面板拉了起来，
两个实例抢同一个 clientId，观察器被顶掉线（这个 bug 见 7 节，已修）。
所以下面标「待复核」的几条，是靠 EC 差分 + 已抓到的那 60 秒命令撑着的。

| 功能 | 结论 | 证据 | 置信度 |
| :--- | :--- | :--- | :--- |
| 键盘背光 / 灯条 | **不在 EC 上**，走 GCUBridge 的 `Keyboard/Ctrl`：`{"function":"SetPower","light":"3","speed":"2"}`；状态从 `Keyboard/Status`、`HidLightbar/Status` 读（`solution=ITE`、`type=FourZone` / `MEZone_Lighbar`，`ACBrightness=3`、`DCBrightness=0`） | 机主点背光和灯条时 EC 全表一个字节都没动；`LIGHTBAR_CONTROL_BYTE`、`RGBKB_LEVEL_R/G/B`、`SINGLEKBL_ENABLE` 在灯亮着的时候全是 0 | **已确认**（阴性结论，两边都指向同一条路） |
| 触摸板 | 触摸板上有一处**物理拨动开关**，`Setting/Status` 里的 `TouchpadToggle` 是它的读数；但 OEM 字符串表里确实有 `TOUCHPAD_TOGGLE_ON/OFF`，所以软件那条路也存在 | 机主 2026-09-30 确认实体开关；`cc-strings-all.txt`、`gcu-allfields.txt` 都有这两个动作名 | 已确认（两条路会不会互相盖没验证过，所以面板只读不写） |
| Win 键锁定 | 候选：`ADDR_STAUTS_BYTE`（1896）1→0，且此后没再变 | 机主只点了一次 Win 锁（00:29:19），EC 就这一个字节翻了；同一时刻 `Setting/Status` 报 `WinKey=WINKEY_STATUS_LOCK` | **已确认**（6.8 用三次连点复核过了） |
| 「平衡 / 健康 / 长效」三档 | 候选：`ADDR_AP_OEM_BYTE4`（1958）高半字节 0→1→2→0，低半字节 9 不动 | 机主连点三次电源模式（00:30:03 / 00:30:09 / 00:30:17），只有这个字节跟着走 | **已确认**（6.8 抓到了命令名） |
| 这三档是什么 | **不是性能档，是电池充电三档**：open-revo 的说明写得很明白——「长效模式 (100%)、日常均衡 (80%)、工作站长寿养护 (60%)」 | 本机 Creator Center 没有办公/均衡/狂暴；`powercfg -list` 只有系统自带那一个「平衡」方案，所以它也不是 Windows 电源计划 | 档位已确认，**百分比仍待实测**（`CHARGE_LIMIT_UP/DOWN` 全程 0，阈值不在这张 EC 表里执行；open-revo 那组数是别的机型的说法，不能直接搬） |

另外这 60 秒里 OEM 自己交代了两件有用的事：

- **`Fan/Status` 把边界写死了**：`CPU_PL1Minimum=10`、`CPU_PL1Maximum=120`、`CPU_PL4Maximum=165`、
  `GPU_ConfigurableTGP` 80~115、`GPU_DynamicBoost` 5~15、`GPU_TargetTemperature` 75~87。
  这正好和实体键「不亮」那态的 PL1=10 对上——10W 是 OEM 自己允许的下限，不是我们猜的。
  以后任何写入都拿这组数当护栏。
- **OEM 自己就是这么写功耗墙的**：`Fan/Control {"Action":"SET_OPERATING_MODE_DETAIL","PL1":"75","PL2":"75","PL4":"75"}`。
  也就是说改 PL1 的正路是走 GCUBridge，而不是裸写 `PL1_SETTING_VALUE`
  （后者实测写完自清零，见 6.2）。这条要做可逆验证之后才允许面板用。

顺手排掉一条线索：机主给的官方主板驱动包（`01-Chipset`）是 **Intel Chipset Device Software**，
74 个 INF + 27 个 CAT，除安装器外没有任何二进制，也不含 `INOU`/`EC`/`GPD`/`ACPIDriver` 字样，
对 EC 语义没有帮助。顺着它的 `PCI\VEN_8086&DEV_06F9`（PCH 热子系统）查下去，
本机**没有 Intel DPTF/ESIF 那一层**：没有 `INT3400` 热管理主控（只有 `INT3450` GPIO 控制器）、
没有 `esif_lf.exe`、没有 DPTF 服务。所以「用 Intel 官方接口调功耗墙」这条路在这台机器上
不存在，功耗与散热策略完全在 OEM 的 EC + GCUService 手里——这也解释了为什么
powercfg 那一层压不住频率（2 节）。

### 6.8 第三次观察：Win 锁与电池三档定案（2026-09-30 01:16）

6.7 里那两条「待复核」这次补齐了。修好双实例 bug 之后观察器再没掉线
（`掉线重连 0 次`），240 秒全程都在，两份报告的时间戳可以逐条对齐：
`logs/观察3`（EC 全表，2 秒一轮）+ `tools/out/mqtt-watch.txt`（OEM 命令）。
机主的操作顺序是：**Win 锁连点三次**（起始是未锁），然后**电源模式点「平衡 → 健康 → 长效」**。

| 时刻 | GCUBridge 收到的命令 | EC 同时的变化 |
| :--- | :--- | :--- |
| 01:16:02 | `Setting/Control {"Action":"WINKEY_LOCK"}` | 01:16:03 `ADDR_STAUTS_BYTE` 0 → 1 |
| 01:16:12 | `Setting/Control {"Action":"WINKEY_UNLOCK"}` | 01:16:13 `ADDR_STAUTS_BYTE` 1 → 0 |
| 01:16:19 | `Setting/Control {"Action":"WINKEY_LOCK"}` | 01:16:21 `ADDR_STAUTS_BYTE` 0 → 1（同时 `ADDR_TRIGGER_BYTE` 0→1→0 闪了一下） |
| 01:16:30 | `BatteryProtection/Control {"Action":"BALANCEDMODE"}` | 01:16:31 `ADDR_AP_OEM_BYTE4` 0x09 → 0x19 |
| 01:16:43 | `BatteryProtection/Control {"Action":"HEALTHYMODE"}` | 01:16:43 `ADDR_AP_OEM_BYTE4` 0x19 → 0x29 |
| 01:17:01 | `BatteryProtection/Control {"Action":"PERFORMANCEDMODE"}` | 01:17:02 `ADDR_AP_OEM_BYTE4` 0x29 → 0x09 |

定案的四条：

1. **`ADDR_STAUTS_BYTE`（1896）就是 Win 键锁定状态**，0=没锁、1=锁着。三次点击三次跳变，
   方向逐条对得上，不再是单样本。只见过 0/1，所以解码函数遇到别的值返回 `None`（面板写「未知」）。
2. **`ADDR_AP_OEM_BYTE4`（1958）高半字节就是电池充电档位**，低半字节恒为 9：
   `0x09`=长效（`PERFORMANCEDMODE`，也是开机默认）、`0x19`=平衡（`BALANCEDMODE`）、
   `0x29`=健康（`HEALTHYMODE`）。注意 OEM 内部把界面上的「长效」叫 `PERFORMANCEDMODE`，
   那个英文名只进日志，界面一律用 Creator Center 的原话。
3. **`ADDR_TRIGGER_BYTE` 是写入握手的脉冲位**：点 Win 锁时它 0→1→0 闪一下。
   只见过脉冲、没见过稳定值，所以只监听、不解释。
4. **这三档仍然没给出充电百分比**：`CHARGE_LIMIT_UP/DOWN` 全程读 0，
   切档时 `PL1_SETTING_VALUE`(75)、`MAFAN_CONTROL_BYTE`(0x10) 一个都没动——
   也就是说电池档和性能档是**正交**的两件事，切电池档不会影响功耗墙。

代码落点：解码函数在 `app/act/channels/base.py`（`battery_mode_of_oem_byte4` /
`win_locked_of_status_byte`），界面词表 `BATTERY_MODE_LABELS` 经 `meta.battery_mode_labels`
下发，前端不自己写死；三个动作名进了 `gcu_actions.json` 白名单（cap `battery.limit`，
能力仍是 `unknown`，面板不显示写按钮，等做过可逆验证再点亮）。
回归测试 `tests/test_battery_winlock.py` 直接把上表那串字节值钉住了。

同时这次抓全了一条 `Setting/Status`，把面板「OEM 开关状态」那一卡片填实了：
`WinKey=WINKEY_STATUS_UNLOCK`、`TouchpadToggle=TOUCHPAD_TOGGLE_ON`、`LightBar=LIGHTBAR_STATUS_ON`、
`SingleColorKBBL=SINGLE_COLOR_KBBL_STATUS_ON`、`UsbCharger=USB_CHARGER_STATUS_OFF`、
`OSD=OSD_HIDDEN_OFF`、`FnKey=FNKEY_UNLOCK`、`NumPad=NUMPAD_UNLOCK`、
`FnWith1HotkeySwitch_Status=FN_WITH1_HOTKEY_TOGGLE_ON`、`DisplayFeatureStatus=DISPLAY_FEATURE_STATUS_ON`、
`DisplayMode=DISPLAY_STANDARD_MODE`、`AcRecoverySwitch_Support=NotSupport`。
两个字段要留个心眼，面板照实并列显示、不合并：

- `Keyboard/Status.powerStatus="Off"` 而 `Setting/Status.SingleColorKBBL=..._ON`——
  背光的真开关是前者（灯确实是灭的），后者说的是「单色背光」这个功能，两件事。
- `DiscreteGpuDirectConnectionSwitch_Status=..._ON` 而 `DGpu=NV_CTRL_PANEL_AUTOSELECT`——
  6.7 记的「互相矛盾」这次改了说法：更像是**两层不同的东西**（直连开关 vs NVIDIA 控制面板的
  输出偏好）。旁证来自机主给的 Hackintosh 工程（见下），dGPU 的电源是挂在 ACPI 的
  `\_SB.PCI0.PEG0.PEGP._OFF/_ON` 上的，跟这个字段不是一回事。没验证前两个值都报出来。

顺带核了机主给的另一个资料：`UmiPro3-Hackintosh`（OpenCore 0.8.1 工程）。
它确认了本机就是**同方 Tongfang GM5MG0Y**（i7-10875H + UHD630 + ALC274 + AX201 + RTL8125），
并且致谢里指向 `kirainmoe/tongfang-utility`、`tongfang-macos`——和之前那条 `tongfang-mifs-wmi`
线索是同一个生态。工程本身对 EC 语义**没有直接帮助**：19 个 SSDT 全是通用热补丁
（DDGPU、USTP、PNLF、PS2Map、PTSWAK 之类），没有任何 EC 操作区/字段定义，
`config.plist` 里也只有三条睡眠相关的改名补丁。唯一的收获是上面那条 dGPU 走 ACPI 的旁证，
以及 `SSDT-RMCF-PS2Map` 只屏蔽了 PrtScn/Pause——说明**实体「造物者模式」键不是 PS/2 键**，
和它直连 EC 的实测结果一致。

### 6.9 接上写通道之前先自查，结果真查出一个口子（2026-09-30）

准备做第一次可逆写验证（6.3 第 2 条）之前先把写入口捋了一遍，发现两处漏闸，
都在**能改硬件状态**的路径上，都已修好并由 `tests/test_write_gates.py` 钉住：

| 口子 | 后果 | 修法 |
| --- | --- | --- |
| `MqttChannel.send_action()` 只查白名单、**不查 `allow_write`** | `/api/action` 成了不设防的硬件写入口：即使配置里 `allow_write=false`，白名单里的 OEM 命令照样能发下去 | 函数开头补 `if not self.allow_write: return False, ...`（和 `set_mode()` 一样，EC 侧本来就有这道闸） |
| `do_POST` **完全不校验来源** | 面板只绑 127.0.0.1 挡不住 CSRF：机主浏览器里随便一个网页都能往 `http://127.0.0.1:8747/api/action` 发 POST，下发 OEM 命令，或者 `/api/shutdown` 把面板关掉 | `is_local_same_origin(headers)`：带了 Origin/Referer 就必须与本次 Host 完全一致；Host 本身必须是回环地址（顺带挡 DNS rebinding）；不满足直接 403 |

来源闸对**脚本零影响**：curl 和我们自己的工具都不发 Origin/Referer，照常放行
（这也是 `main.py --stop` 还能用的原因）。真正的浏览器跨站请求 Origin 必然是对方站点，一律挡掉。

教训是这条：白名单管的是「**这个命令 OEM 认不认**」，配置开关管的是「**这台机器允不允许写**」，
来源校验管的是「**这个请求是不是本机面板发来的**」——三件事，谁也不能替谁。
以后新增任何 POST 接口，先问这三个问题，别等写完再补。

**顺带把第一次可逆写验证做完了**（本来是下一步计划，被这次自查提前触发）：
用 curl 验闸门时误把 `WINKEY_UNLOCK` 当探针发了出去，而本机 `allow_write` 是**开着**的，
命令真的下去了——面板日志 01:43:16 记下 `ADDR_STAUTS_BYTE: 1 → 0`，
也就是说机主原本锁着的 Win 键被解开了；随后下发 `WINKEY_LOCK`，EC 回读 1、
`Setting/Status` 回读 `WINKEY_STATUS_LOCK`，两条通道一致，状态已还原。
这一次意外等于把 6.3 第 2 条要求的整条链走通了：**MQTT 下发 → GCUService →
EC 寄存器变化 → 两条通道回读一致 → 写回还原**，而且证明写入到 EC 生效有约 6 秒延迟。
据此 `winkey.write` 从 unknown 升为 **verified**，面板「OEM 开关状态」卡片
第一次出现了按钮（只有这一个）。教训也记下来了：**验闸门只能用不在白名单里的假动作名**，
拿真命令当探针，等于拿机主的硬件当探针。

### 6.10 拿外部逆向资料交叉核对，并且当场只读复测了一遍（2026-09-30）

机主提示"多找找 umi pro 3 bios 等关键字"之后做了一轮外部资料检索。**GM5MG0Y 本板的
DSDT / EC 转储在公网上不存在**（GitHub 搜 `GM5MG0Y` 零结果），BIOS 那条路也是死的：
它是 AMI 板，bios-mods 上的解锁请求没人接，有人用 AFUWIN64 刷改版报
`cannot update bios with different platform name`，WinRAID 那边**连降级都被拒**。
所以别再去搜 BIOS 了，这条记在这里就是为了不用再搜一遍。

有用的是**同家族近亲板**的三份实测资料，它们的寄存器地址和我们从本机
`GCUService.exe` 反射出来的常量表**逐个对得上**——这比任何单一来源都可信：

| 地址 | 我们自己的 OEM 常量名 | 外部资料说它是什么 | 本机只读实测（Turbo 档、AC、电量 100%） |
| --- | --- | --- | --- |
| 0x741 | `ADDR_AP_OEM_BYTE` / `ADDR_FAN_ALERT_BYTE` | bit0 = ap_exist；EC 用它当 **PL 清零闸** | **1**（bit0 已置 → EC 不清 PL） |
| 0x706 | 不在我们的表里 | ~~自定义模式要写 0x41~~ 实为每轮减一的倒计时器 | **0** |
| 0x726 | 不在我们的表里 | ~~bit7 = 自定义模式~~ 全镜像零引用（上游名 AC_AUTO_BOOT bit3） | **0** |
| 0x727 | 不在我们的表里 | ~~bit5/6 = 自定义模式~~ 全部引用点为 0 | **0** |
| 0x7C5 | `ADDR_AP_OEM_BYTE5` | ~~bit7 不置 EC 会忽略风扇表~~ 实为 **CPU/GPU 分表**开关（默认 false） | **0x80**（本机置着） |
| 0x7C6 | `ADDR_AP_OEM_BYTE6` | bit2 = 写表期间拉低的**括号**，不是激活位 | **0x04** |
| 0x783 | `ADDR_PL1_SETTING_VALUE` | PL1，可写且持久 | **75**（与面板读数一致） |
| 0x78C | `ADDR_SINGLEKBL_ENABLE` / `ADDR_AP_OEM_BYTE2` | 单色键盘背光：开 0x01 / 关 0x03 | **0**（两个值都不是） |
| 0x7B9 | `ADDR_BATTERY_CHARGE_LIMIT_UP` | EC 充电环每秒读的**实时**上限 | **0** |
| 0x87F | 不在我们的表里 | **存储**的充电上限，要求 1–100 | **0xFF**（未设） |
| 0x7C3 | 不在我们的表里 | 充电限制门控 | **7** |
| 0x770 | 不在我们的表里 | ROMID[0]，门控要求 = 4 或 5 | **0xFF** |

从这张表能落下四条结论，其中两条**修正了我们自己早先的判断**：

1. ~~**「裸写 PL 自清零」的原因很可能不是"寄存器是别代机型的"**~~ —— **这条已在 6.11 撤回。**
   当时按 `uniwill-laptop-mr` 的实测推断，0x783/0x784/0x785 可写且持久的前提是
   先置齐「自定义模式激活链」`0x741 bit0 → 0x706=0x41 → 0x726 bit7 → 0x727 bit5/6 →
   0x7C5 bit7 → 0x7C6 bit2`。拿到 GM7MG7P 的 EC 反汇编之后逐环节核对，
   **这条链不存在**：`0x0706` 是个每轮减一的倒计时器，`0x0726` 全镜像零引用、
   `0x0727` 全零，厂商服务侧对这三个地址**零命中**。真正的机制见 6.11 第 2 条。
2. **`CHARGE_LIMIT_UP` 读到 0 现在有机制解释了**：EC 的门控条件是
   `0x7C3` 或 `0x770` 等于 4/5，否则要求存储上限 `0x87F` 落在 1–100。
   本机 `0x770=0xFF`、`0x87F=0xFF`，两个条件都不满足 → 实时上限就是 0。
   也就是说这个 0 不是"读不到"，是"充电限制根本没启用"。
3. **电池三档的百分比有了两份独立旁证，但本机仍未实测**：`open-revo` 明写
   长效 100% / 日常均衡 80% / 工作站长寿养护 60%；另一份无界 14XA 的实测给出
   `0x7A6` = 0x08 长效 / 0x18 均衡 / 0x28 工作站，即 **bits[5:4]=0/1/2 + bit3 恒置**——
   和我们抓到的 0x09 / 0x19 / 0x29 只差 bit0，档位顺序完全一致。
   结论：我们的**解码是对的**（顺序 0=长效、1=平衡、2=健康），
   百分比是**别人机器上的数**，面板继续只显示档位名、不显示百分比。
4. **键盘背光那条阴性结论维持不变**：`0x78C` 在我们的表里确实叫 `SINGLEKBL_ENABLE`，
   但本机灯灭着的时候它读 0，既不是外部的"开 0x01"也不是"关 0x03"。
   本机是 FourZone / ITE 方案，真实通路仍然是 `Keyboard/Ctrl {"function":"SetPower"}`。

另外三条**线索**（都还没验证，别当成能力）：

- **免驱动的 EC 通道**：有 DSDT 里带 `\_SB.INOU.ECRR(addr)` / `ECRW(addr,val)`，
  也就是不装 OEM 驱动、直接调 AML 方法就能读写 EC。可以作为 `UWACPIDriver` 不在时的备胎。
- **WMI 邮箱**：`uniwill-laptop` 用 GUID `ABBC0F6F-8EA1-11D1-00A0-C90629100000` 的
  `AcpiTest_MULong`（GetULong=1/SetULong=2/FireULong=3/GetSetULong=4/GetButton=5，
  Data = 16bit addr + 16bit data + 16bit op，读 op=0x0100，`0xFEFEFEFE` 表示超时），
  Linux 侧靠它做出了 `charge_control_end_threshold`。
- **`tongfang-mifs-wmi` 这条线索可以关掉了**：它是纯 GUID 匹配、没有 DMI 机型表，
  命令集里**不含风扇曲线写入、不含 PL1/2/4、不含充电阈值**（只有模式/GPU/键盘类型/
  Fn 锁/触摸板锁/风扇转速/RGB/温度/功耗这几类），对本项目最想要的三件事一件都帮不上。

还有一处**外部资料自己打架**的：风扇表布局。一份说是 16 点分块，另一份说是每风扇
48 字节的 (up, down, duty) **交错三元组**。**这一处已经裁定**——直接读厂商服务
`FanTable_Manager1p5.SetEcFanTable` 的源码（GM7MG7P 仓库里已解密的 v3.1.39.0），
分块说是对的，交错说是错的，而且降温阈值的基址两份都没说准：

| | 升温阈值 UpT | 降温阈值 DownT | 占空比 Duty |
| --- | --- | --- | --- |
| CPU 16 点 | `0x0F00 + i` | `0x0F11 + i` | `0x0F20 + i` |
| GPU 16 点 | `0x0F30 + j` | `0x0F41 + j` | `0x0F50 + j` |

三个细节容易踩：① DownT 的基址是 `0x0F10`/`0x0F40` **再加 1**，`0x0F10`/`0x0F40`
本身厂商从来不写；② `i=15` 那一格 UpT 恒写 `0xFF` 当哨兵，`i<15` 写的是
**下一个点**的 UpT（`CPU[i+1].UpT`），也就是整张表错位一格存；③ 占空比确实是
`百分比 × 2`，但 GPU 的 `0x0F5D/0x0F5E/0x0F5F` 被 `RefreshDefaultFanTableAll`
借去当信箱了（写 `0x0F5F`=模式、`0x0F5D=0xFD`、`0x0F5E=0xC9`，然后 500 ms 轮询），
所以最后三格占空比槽**不能当普通数据写**——这一点第二份资料说对了。

### 6.11 完整复现 GM7MG7P 的逆向资料：对上了身份，也推翻了我们两条结论（2026-09-30）

机主找到 [`ElDavoo/tongfang-gm7mg7p-re`](https://github.com/ElDavoo/tongfang-gm7mg7p-re)
（泰坦 X8 Pro / GM7MG7P，i7-10875H + RTX 3070，同方代工），说"很有价值，如果可以，
完整复现吧"，并明确"不要刷固件，我是想你或许能逆向"。于是把它整份拉下来只做离线阅读
（7906 个文件：EC 侧 2710 个反编译函数、45481/45624 条指令通过重新汇编校验，
Windows 侧 375 个解密后的 C# 文件），**没有执行仓库里任何脚本、没有反汇编、
没有碰机器**。结论分四块。

#### 一、身份对上了，而且不是"近亲"，是同一份代码基

| 证据 | 他们（GM7MG7P） | 我们（GM5MG0Y） | 判定 |
| --- | --- | --- | --- |
| EC `0x0740` PROJECT_ID | `0x0F` = PROJECT_ID_CML_GAMING | **实测 `0x0F`** | 相同 |
| EC 固件标识 | `ITE EC-V14.6` | 客服 ROM 里五份 EC 镜像都是同串 | 相同（但见下方更正） |
| ITE8850 PD 镜像 `0x20000-0x2FFFF` | sha `30fe7fb81745` | **逐字节相同** | 相同 |
| 四个 bank 跳板 `0x1100/0x1114/0x1128/0x113C` | `c0087411c0e0c082` | **逐字节相同** | 相同 |
| `ECSpec.cs` 常量表（175 项，含 109 个 `ADDR_`） | v3.1.6.0 / v3.1.39.0 / v3.9.18.0 三版**逐字节相同** | 我们从本机 `GCUService.exe` 反射出 125 项 | **122 项同名同址，零处地址冲突** |
| `MyFanCTLByteFlag` 枚举 | Normal 0x00 / Turbo 0x10 / FanBoost 0x40 / User 0x80 / Level1-5 0x81-0x85 / HiMode 0xA0 | 实测三态与之吻合 | 相同 |
| EC 代码段本体 | `GMxMGxx_11.800` | 客服 ROM 里切出的 `GMxMGxxN109MRO06` 前 256 KiB | ~~31.39% 字节不同~~ **这格证据作废**，见第十节② |

**更正（2026-09-30 晚）**：最后那格当初写成"31.39% 不同 → 同一源、不同 build"，是错的用法。
客服 ROM 里其实装着**五份** EC 镜像，其中一份与 `GMxMGxx_11.800` **逐字节相同**（0.00%）；
31% 只是这个 ROM 里"不同板子"之间的正常距离（任意两份不同板的镜像都在 30~32%）。
"同一源"的判定不变，但它靠的是 ITE8850 PD 镜像与四个 bank 跳板**逐字节相同**那两格，
不是这个百分比。同样地，上表第 2 行的 `ITE EC-V14.6` 是**这份 GM7MG0M 的 ROM** 里的串，
不是从本机 EC 读出来的——本机固件版本至今没有独立取过。完整矩阵见第十节②。

那 3 个对不上的名字是 `APP_Normal_Mode`=0x000、`APP_LightBar_Mode`=0x001、
`APP_ImageProjectionLight_Mode`=0x002——它们是**应用模式号不是 EC 地址**，
只是恰好和他们的 `OSD_CAPSLOCK`/`OSD_NUMLOCK` 撞了名字，不算冲突。

**为什么这对我们有用**：厂商服务**不按 DMI 选代码路径**（DMI 精确匹配表是这个仓库
自己为 Linux 上游写的，不是厂商的东西）。它只看三样——EC `0x0740` 的 PROJECT_ID、
`0x078E` bit6（`IsSuportRamFan1p5`）、注册表 `HKLM\SOFTWARE\OEM\GamingCenter2\CustomizeTarget`。
PROJECT_ID 15 不在 `CommercialProjectIDs` 里，所以只要 CustomizeTarget 不是 42(NV)/11(MCJ)，
本机跑的就是和他们**同一对类**（`MyFanManager_RamFan1p5` + `FanTable_Manager1p5`）。
也就是说那 375 个已解密的 C# 文件对我们大概率直接可读。仓库里 `MECHREVO` 只以
`Customize.cs` 的枚举值出现（`Mechrevo=17`、`Mechrevo_COML=4`、`Mechrevo_Creator=2048`），
`GM5MG0Y` / `Umi Pro 3` 全库零命中。

#### 二、PL 自清零的真机制：EC 根本不会替你设 PL

这条**推翻了我们自己写在 6.10 的结论**。他们对 EC 镜像做了完整的写点普查：

- `0x0783/0x0784/0x0785` 在整个主镜像里**各只有 5 个引用点：4 读 1 写**。
  唯一的写在 `0xA833`，反汇编长这样：

  ```
  0xa82b  mov dptr,#0x0741   ; AP_OEM
  0xa82e  movx a,@dptr
  0xa82f  jb   acc.0,0xa843  ; "AP 存在" -> 别动 PL
  0xa832  clr  a
  0xa833  mov dptr,#0x0783 / movx @dptr,a   ; PL1 = 0
  0xa837  mov dptr,#0x0784 / movx @dptr,a   ; PL2 = 0
  0xa83f  mov dptr,#0x0785 / movx @dptr,a   ; PL4 = 0
  ```

  本机 `0x741 = 1`（bit0 已置），**这条清零分支在我们机器上不该跑**。
- 每档的默认值块 `0x0730-0x0737` / `0x07A7-0x07AA`（共 12 字节）在整份镜像里
  **一个读点都没有**。也就是说 EC 从不把"某一档的默认 PL"搬进 PL 寄存器——
  这些字节是 EC **发布给 host 去取**的，厂商的 `SetUserProfile` 先读它们、
  再**自己**把 PL 写下去。
- 34 个 `0x0751` 模式位分支臂里，**没有一臂**把默认块搬进 PL，也没有一臂加载风扇表。

**所以对我们最直接的推论是**：写 `0x0751`（档位字节）指望 PL 跟着变，
静态证据上**没有任何东西会跟着变**；PL 必须由 host 写。而 host 侧受支持的写法就是
`Fan/Control {"Action":"SET_OPERATING_MODE_DETAIL","PL1":..,"PL2":..,"PL4":..}`
→ `SetPL1/2/4Value` → `EcCtrl.Write(1923/1924/1925)` = `0x783/0x784/0x785`。
这正好是待办 #27 那条路，而**不是**去凑什么激活链。

⚠️ 但那条路上有个坑，他们查出来了：**服务端对 PL 完全不做边界校验**。
`Convert.ToInt32` 之后直接 `(byte)` 截断写入，`_SmartApcTable.PL1/PL2/PL4`
（该机 120/120/165）和 `CpuPL1Minimum=10` **只作为 Maximum/Minimum 发给 UI**，
不参与 clamp（对比 `GpuFeatures.SetGpuConfigurableTGPTarget` 是有下界保护的）。
发 300 进去会被静默截成 `0x2C`=44 W。**约束全在客户端**，所以我们下发前
必须先拿 `Fan/Status` 的 OEM 上下限自己夹一遍——这也正是 6.7 里把
「OEM 允许范围」显示出来的用意。

#### 三、电池：这个 EC 家族**不按百分比封顶**，待办 #26 可以关掉

我们一直想实测"平衡/健康/长效分别是百分之多少"。他们的逐指令手译说明这个问法本身就不成立：

- 真正的封顶是 **EC 内部的充电电压上限 `0x0522/0x0523`**，由 bank0 `0xB158`
  的 `charge_target_update` 周期重算，公式
  `target = 0x030E(电池请求 17400 mV) − derating(mV/cell) × cells`。
  derating 取「年龄档」和「档位地板」的较大值：Stationary ≥ 200、Balanced ≥ 100、
  High capacity 0；年龄档按循环数 150/250/350/450/550 → 50/100/150/200/250 mV/cell。
- `0x0522` **host 写不住**：他们连做三次，2000 次连读里捕获到写入值 **0 次**，
  同一次对 `0x07B9=0x5A` 的对照写却保持住了 → 写路径是通的，`0x0522` 在 **<101 µs**
  内被 EC 夺回。
- **EC 代码里不存在任何百分比**（他们的 Linux patch 文档明说）。仓库里唯一出现过的
  百分比数字来自**别的板子**（MECHREVO 无界 14XA「Balanced ~80%、Health ~60%」），
  并标注为"他们固件的，本机未观测"。
- 那套「每秒读 `0x07B9`、被 `0x07C3`/`0x0770` 门控、回落到 `0x087F`」的百分比模型
  来自 w568w 对 14XA 的逆向，**在同项目板上他们判定"这套机制在本图里没找到"**：
  `0x087F` 无直接引用；`0x0742` 唯一的写点只置 bit1、从不置 bit2。
  判据是只读 `0x0742` 看 bit2——**我们早就读到 `0x742 = 2`，bit2 = 0**，
  和他们的读数一致。所以百分比门控在本机确实不在场。
- 三档编码得到独立印证：`0x07A6` bits[5:4] = `00` High capacity（Standard，
  对应 `PERFORMANCEDMODE`，我们的"长效"）、`01` Balanced（Long_Life，
  `BALANCEDMODE`，"平衡"）、`10` Stationary（Trickle，`HEALTHYMODE`，"健康"）。
  低半字节本机恒为 `0x09`，含义未定。厂商只做一次读改写
  （`BatteryProtection2.SetHealthProtectionHigh/Middle/Low`），
  且**服务退出会强制回 High capacity**。

结论：面板继续**只显示档位名、永不显示百分比**，而且这不是"还没测"，
是"这个 EC 家族没有百分比这个概念"。另外注意：若电池已进入 250 mV/cell 的年龄档，
三档在电压上**不会有可观测差别**——所以"测不出差别"和仓库结论是自洽的。

#### 四、灯效走 USB HID，不在 EC 上——我们那条阴性结论终于有了解释

6.7/6.10 里我们记录过：切灯效时 EC 全表纹丝不动，`0x78C` 灯灭时读 0，
既不是外部的"开 0x01"也不是"关 0x03"。原因查明了：

- ITE 8291 是 **USB HID 设备，不是 I2C、也不归 EC 管**。两个 HID 接口：
  `048D:CE00`（键盘，usage page `0xFF12`，4-zone）与 `048D:6005`（灯条，`0xFF03`）。
- 协议是 HID `SET_REPORT` Feature，9 字节
  `[0, opcode, Control, Effect, Speed, Light, ColorIndex, Direction, Save]`；
  `0x08` = 灯效、`0x09` = 亮度、`0x14` = 调色板 `Index,R,G,B`、`0x80` = 读固件版本。
  Linux 的 `ite_8291_lb` 用 8 字节 `HIDIOCSFEATURE`。
- 亮度 5 档编码 `0/8/22/36/50`，速度 5 档 `10/7/5/3/1` → 我们抓到的
  `light=3, speed=2` 就是 36 / 5。关灯 = `(1,0,0,0,0,0,0)`。
- EC 侧的灯条寄存器 `0x0748-0x074B` 在固件中**零引用**，实机写入无任何可见效果。
  `0x078C` 只是个**状态镜像**：EC 固件自己从内部 `0x0826` 生成 bits5-7，
  厂商服务 `SetBrightness` 时会把亮度 RMW 进 bits5-7 作镜像，Fn+F6/F7 热键走 WMI 177/178。
  **三个写者共用一个字节**，所以 6.3 第 8 条把它列进禁区。

这条给了一個新可能：灯效可以在**纯用户态**做（枚举 HID → `SET_REPORT`），
不碰 EC、不碰驱动、天然可逆。但它是新的写通道，要按 6.3 第 2 条重新走一遍验证，
本轮没做。

#### 五、两件**没做**的事，以及为什么

- **没有解密我们本机的 `GCUService.exe`**。他们的方法是**内存转储**而非静态破解
  ConfuserEx：服务运行时 `<Module>.cctor` 已把 IL 解密，直接 `ReadProcessMemory`
  读进程镜像，再用磁盘文件当模板把 IAT/reloc/CLI header/metadata 补回去
  （anti-dump 会抹掉 `BSJB` 和流名）。校验方式是数 invalid body：
  磁盘 3759 个 → dump 后 **0** 个。**但这需要管理员权限（SeDebugPrivilege）**，
  机主不在场，不擅自提权。命令已经抄在下面，等机主点头再跑：

  ```
  tasklist | findstr /i GCU          # 先确认真实进程名，机械革命可能改过
  pip install pefile dnfile
  python windows\tools\dotnet_dump.py --name GCUService.exe --out GCUService.dumped.exe --report
  python windows\tools\dotnet_bodies.py "<原exe>" GCUService.dumped.exe   # invalid 应从数千降到 0
  ilspycmd 9.1.0.7988 -p -o out -r "<安装目录>\MyControlCenter" GCUService.dumped.exe
  python windows\tools\ec_callsites.py out                               # 得到本机自己的寄存器表
  ```

  已知失败模式：他们只在 **3.1.39.0** 上成功过，文档明说同一流程未在
  3.1.6.0 / 3.9.18.0 上试过（不同 build、混淆强度不同，3.1.6.0 反而更重）。
  我们本机装的版本号要先确认，成败以第 4 步的 invalid 计数为准，别猜。
- **没有刷任何固件、没有运行 `AFUWINx64.EXE` / `F.bat` / 任何 EFI 工具、
  没有写任何 UEFI 变量**。客服包里那个 `GMxMGxxN109MRO06.ROM`（13,631,488 字节）
  只做了字节级离线阅读。顺带确认一件事：**EC 固件就在这个 ROM 里**——
  `F.bat` 的刷机命令带 `/e`（EC 更新）标志，ROM 偏移 `0x50` 处能读到 `ITE EC-V14.6`、
  `0x20040` 处能读到 `ITE8850`，前 256 KiB 就是完整 EC 镜像
  （已切出到 `tools/out/ec_from_mro06.bin`，gitignore 掉了）。
  仓库的 `vendor/bios-1.09/BIOS_1.09.zip` 里 `GM7MG7P/GMxMGxxN109A08.ROM`
  **也是 13,631,488 字节**，只是他们那份把 EC 镜像 `GMxMGxx_11.800` 单独用
  `ecflash.nsh` + `IFUX64.efi` 在 UEFI shell 里 dump 出来了。
  `N109` = BIOS 1.09，`MRO` 是机械革命的构建后缀，`A08` 是另一变体。

**当时写着"仍然没解开的"那条，已经解开了**：ROM 内部的机型名找到了，47 个 `_FVH`
里那 19 个真卷和 33 段 LZMA 全部解开，答案是 **`Taitan Series GM7MG0M`**——
这份客服 ROM 根本不是本机（GM5MG0Y）的。过程与全部产物见 6.11 第九节。

#### 六、当场只读复核：前面那套推理的两个前提，在本机都成立

上面的结论大多来自"同 PROJECT_ID 所以代码路径相同"这个推理。推理里有两个前提
是**可以在本机直接只读验证**的，所以当场验了，没让它停在推理上：

**① `0x078E` bit6（`IsSuportRamFan1p5`）= 1。** 为此把 `ADDR_SUPPORT_BYTE6`
加进了 `ec_gpd.py` 的低频只读组（60 秒一轮，多 1 次读，总速率从 11.0 升到约 11.4 次/秒，
远低于 6.3 第 6 条的上限）。实测：

```
0x78E = 108 = 0x6C = 0110 1100     → bit6 = 1
```

厂商服务选风扇表实现类的三个判据，现在两个已在本机确认：
PROJECT_ID `0x0740 = 15`（不在 `CommercialProjectIDs` 里）✓、`0x078E` bit6 = 1 ✓，
第三个是注册表 `CustomizeTarget`（本机没读，注册表值不在 EC 里）。
按 `MyFanCtrl.cs` / `MyFanTableCtrl.cs` 的判定树，这两个条件成立就会走
`MyFanManager_RamFan1p5` + `FanTable_Manager1p5`——**正是 GM7MG7P 那份完整解密过的
那一对类**。所以 6.10 末尾那张风扇表偏移表对本机是**可用的**，不是"同代机型大概通用"。
顺带一提 bit3 也置着，与他们记录的「bank0 `0xB12C` 无条件置 `0x078E` bit3」一致。

**② 两个 ITE HID 设备都在，状态 OK。** 灯效那条路（第四节）的前提是本机真有这两个
USB 接口，`Get-PnpDevice` 只读枚举的结果：

| VID/PID | 他们说的角色 | 本机 |
| --- | --- | --- |
| `VID_048D&PID_CE00` | 键盘（usage page `0xFF12`，4-zone） | 在，MI_00 + MI_01 两个接口，Status OK |
| `VID_048D&PID_6005` | 灯条（usage page `0xFF03`） | 在，MI_00 + MI_01 两个接口，Status OK |

**没做写入**——按 6.3 第 2 条，新的写通道要先凑齐"语义佐证 + 可逆 + 温度保险"，
本轮只把"设备在不在"这个前提钉死了。

#### 七、一处**新观察**，暂时没解释：PL 会跟着档位变，但按他们的镜像 EC 不该做这件事

复核时面板正处在自适应档、机器空闲，实测：

| 时刻 | 档位字节 `0x0751` | PL1 `0x0783` |
| --- | --- | --- |
| 早先（Turbo） | `0x10` (Turbo_Mode) | **75** |
| 现在（自适应落到省电） | `0xA0` (User_Fan_HiMode) | **10** |

面板自己**从不写 PL**（`SET_OPERATING_MODE_DETAIL` 还没进白名单），
而第二节又说 EC 主镜像里 `0x0783-0x0785` 只有一个被 `0x0741` bit0 门控的写点、
且本机 bit0 已置。那 PL 是谁改的？最可能是**一直在跑的 `GCUService`**：
它看到档位字节变了，就按自己的 profile 把 PL 重新写一遍——这正是他们记录的
`SetUserProfile`「先读 EC 发布的默认值、再自己把 PL 写下去」的行为。

如果这个解释成立，那 6.2 里记的**"裸写 PL 会自清零"就有了更简单的答案**：
不是 EC 拒绝，也不是缺什么激活链，是**厂商服务在我们写完之后又盖了一遍**。
这反过来加强了第二节的结论：改功耗墙只能走服务自己的 `SET_OPERATING_MODE_DETAIL`，
绕过服务直接写 EC 是白写。

⚠️ 但这只是**最可能的解释，没有证实**：本机 EC 与他们那份差 31%，不能排除我们的
build 里 EC 自己会跟着改。要证实得在服务的 profile 不动的情况下单独改档位字节看 PL——
那是一次写操作，得等机主在场。

另外顺手记两个从这次快照里读到、**尚未采信**的数：
`ADDR_EC_BT1CycleCount` = **83 次循环**（按他们的降额表，150 次以下年龄降额为 0，
所以本机三档的差异应当是**可观测**的，这点和"电池老了就看不出差别"的顾虑相反）；
`defaults` 组读出 GAMING PL1/PL2/PL4 = 60/60/165、OFFICE = 35/35/165、
BATTERYSAVER PL1/PL2 = 75/75 ——**这组数先别用**：BATTERYSAVER 的 PL1 反而比 GAMING 高，
而且他们明确警告过这份常量表是多平台复用、命名有冲突
（同一地址在不同板子上是不同东西），所以这几个名字很可能是错位的。

#### 八、把本机**活着的风扇表**只读转储出来了（`tools/ec_fantable_dump.py`）

前面都是"他们的镜像怎么说"。这一节是**本机实测**：新写了个只读转储工具
（`tools/ec_fantable_dump.py`，只发 ECREAD，脚本自己会检查 `writes == 0`，
不是 0 就退出码 3 炸出来），停面板 → 转储 → 重启面板，共 **118 次读、0 次写**，
间隔 0.05 秒（20 次/秒，低于 6.3 第 6 条上限）。转储时机器空闲、自适应档落在省电
（`0x0751 = 0xA0` User_Fan_HiMode、PL1 = 10 W）。

**表布局在本机得到证实**——按 6.10 那张偏移表解码出来是一张形状完全合理的曲线：

| 点 | CPU 升温 | CPU 降温 | CPU 占空比 | GPU 升温 | GPU 降温 | GPU 占空比 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0 | 48 | 0% | 0 | 48 | 0% |
| 1 | 53 | 50 | 66% | 52 | 50 | 76% |
| 2 | 57 | 60 | 99% | 53 | 57 | 100% |
| 3 | 59 | 62 | 99% | 56 | 59 | 100% |
| 4 | 61 | 64 | 99% | 58 | 61 | 100% |
| 5 | 63 | 68 | 99% | 60 | 64 | 100% |
| 6 | 67 | 70 | 99% | 63 | 66 | 100% |
| 7 | 69 | 72 | 99% | 65 | 68 | 100% |
| 8 | 71 | 255 | 99% | 67 | 255 | 100% |
| 9–15 | 255 | 255 | 99% | 255 | 255 | 100% |

三件事对上了：① 第 15 点的升温阈值确实读到 **`0xFF` 哨兵**，和厂商代码里
`i=15` 恒写 `byte.MaxValue` 完全一致；② 第 9 点往后全是 `255`，即"未使用的点填 255"，
和 `UserFanTables/*.json` 里 OEM 自己的样例同一个写法；③ 占空比原始值全是偶数
（198、200），符合"百分比 × 2"。

有一处**看着像解码错了、其实不是**：降温阈值比升温阈值还高（点 2 是升温 57 / 降温 60）。
这不是我们错位了一格——OEM 自己发布的 `M1T1.json` 里同样是
`UpT 54 / DownT 57`、`UpT 60 / DownT 64`，**是厂商的命名习惯如此**。
解码口径以 `GetEcFanTable` 的读回代码为准（`3840+i-1` 取 UpT、`3856+i+1` 取 DownT、
`3872+i` 取 Duty 再除 2），与本工具的解码逐行一致。

**顺带把每档默认值块也读出来了，而且它纠正了我们反射表里的一个名字**：

| 地址 | 读到的三个值 | 我们反射表里的名字 | 更可能的真实身份 |
| --- | --- | --- | --- |
| `0x0730-0x0733` | 60 / 60 / 165 / 1 | `GAMING_PL1/PL2/PL4` | Gaming ✓ |
| `0x0734-0x0737` | 35 / 35 / 165 / 1 | `OFFICE_PL1/PL2` | Office ✓ |
| `0x07A7-0x07AA` | **75 / 75 / 165** / 1 | `BATTERYSAVER_PL1/PL2` | **很可能是 Turbo，不是省电** |

理由：本机在 **Turbo 档实测 PL1 = 75 W**（6.4 就记过 PL1 75↔10 的摆动），
而 `0x07A7` 这块正好是 75/75/165；反过来省电档实测 PL1 = **10 W**，
和 75 差得远，倒正好等于厂商的 `CpuPL1Minimum = 10`。
所以这第三块更像 Turbo 的默认值。**这是有证据的推断，不是证实**——
6.11 第七节说过这份常量表多平台复用、命名会冲突，要用它当护栏之前得先确认。

另外记两个读到但**还没解释**的：`0x0626 = 0`、`0x0627 = 0`
（他们的 bank1 `0x9432` USER 臂用 `0x0627` 低半字节索引两张 CODE 表，本机两个都是 0）；
`0x07C6` 这次读到 **`0x07`**，而 6.10 那次是 `0x04`——bit2 一直置着（写表的括号），
但 bit0/bit1 这次也置上了，含义未解。

⚠️ **一个必须说清的局限**：`0x0F00-0x0F5F` 是**host 写入区**。按他们的逆向，
EC 在 USER 模式下是从**自己 CODE 里的两张表**选一张用（按 `0x0751` bit7 和 `0x0627` 索引），
不一定就是这块 RAM。所以这份转储准确的说法是"**Creator Center 最后一次编进 RAM 的表**"，
不等于"此刻正在管风扇的表"。要区分这两者，得在**不同档位**各转储一次看它变不变——
那需要一次档位写入，按 6.3 第 2 条等机主在场再做，本轮没做。

**这一节的东西已经进面板了**：`GET /api/fan-curve`，只读，按钮触发，**不进 2 秒轮询**。
`EcChannel.fan_curve()` 按上面那套布局自己算地址，一轮 92 个表地址 + 4 个语义位
= **96 次读**，限速约 30 次/秒、用时 2.9 秒，结果缓存 10 秒（并发请求拿旧缓存）。
能力表因此拆成两条：`fan.curve` 读通一轮之后才置 verified，`fan.curve.write` 一直是
`blocked`（写表的可逆验证要机主在场），面板不给任何写入按钮。解码逻辑由
`tests/test_fan_curve.py` 的 12 个用例钉住，用的基线就是本机那次真实转储的字节值。

接进去的过程里实测到两件值得单独记的事：

1. **同一进程里和轮询线程并发读是安全的，跨进程不安全。** 独立工具
   `tools/ec_fantable_dump.py` 要求先停面板（两个进程各持一份驱动句柄时读会互相打乱）；
   但进程内 `EcGpd._ioctl` 每次调用都持锁，面板一边 2 秒轮询、一边转储 96 个寄存器，
   两轮实测 `incomplete = 0`，解出的表与停机时那份**逐点一致**。所以这个接口不需要停机。
2. **`dev.reads` 是整个通道共用的计数器，不能拿它做差当「本轮读了多少次」。**
   第一版就是这么写的，报出来 **140**（自己 96 + 同一窗口里轮询线程的 44）。
   现在改成本轮自己数，报 96。写入那一侧也说清楚了：本函数一个写都不发，
   但自动跟随可能在同一窗口里改风扇字节，那种情况 `writes_during > 0`，
   面板会照实写「这不是同一时刻的快照」，不假装是原子读到的。

#### 九、把客服 ROM 的固件卷解开了：这份 ROM 不是本机的，但顺带挖出厂商自己写的一整套命令面（2026-09-30）

工具 `tools/rom_fv_dump.py`：**只 open 一个磁盘文件**，不刷写、不碰 EC、不碰 UEFI 变量、
不调用任何驱动。12 个用例 `tests/test_rom_fv.py` 用合成镜像钉住（不需要真 ROM 就能跑）。

**没有 UEFITool 也能解**，Python 自带的 `lzma` 就够。两个坑都是在这份 ROM 上真栽过才修对的：

1. GUID_DEFINED 段的 `DataOffset` 是**从段头算起**的，而代码里的 data 已经去掉了 4 字节
   Size/Type，所以要减 4。忘了减，LZMA 试 74 次**全失败**，而且失败得毫无提示。
2. LZMA 段是**标准 LZMA_Alone 13 字节头**（props + dict + 8 字节长度 + 裸流），
   不是"只有 props"。`5d 00 00 00 01` + `10 e0 99 00 00 00 00 00` 声明 0x99E010，
   解出来正好 10,084,368 字节。`dict_size` 不夹范围（0xFFFFFFFF）会让解压器直接 Internal error。

还有两条对齐/校验规则，少一条就会静默丢文件：47 个 `_FVH` 里只有 **19 个是真卷**
（其余是压缩数据里的巧合，卷头合法性检查不能省）；从空闲区错位读出的假文件头会让
长度冲出卷尾，这时必须**退回按 8 字节重新对齐**，不能把假头当真文件收下——
收下就等于把后面所有真文件一起吞掉。

解开后的规模：19 个顶层卷 + 3 个内层卷、**651 个 FFS 文件、33 段 LZMA 共 12.0 MiB、
356 个模块名**。

**① 内部机型名找到了：`Taitan Series GM7MG0M`——这份客服 ROM 不是本机的。**
SMBIOS 字符串池（DXE 卷里 `daf4bf89-ce71-4917-b522-c89d32fbc59f` 的 FREEFORM_GUID 段，+0x49 起）：

```
American Megatrends Inc. / N.1.09MRO06 / 03/16/2021          ← Type 0
MECHREVO / Taitan Series GM7MG0M / Standard / Standard / 0001 / CML   ← Type 1
MECHREVO / GM7MG0M / Standard / Standard                     ← Type 2 主板
MECHREVO / Standard / Standard / Standard                    ← Type 3 机箱
```

**这是泰坦（Taitan）系列 GM7MG0M 的 ROM，不是本机 GM5MG0Y（无界 Umi Pro 3）的。**
机主当初那句"用起来不太对劲"是对的；`cannot update bios with different platform name`
卡的就是这个字段——AMI 刷写时比对 SMBIOS 的产品名/主板名。同为 CML 平台、同为
13,631,488 字节、EC 侧又高度同源（6.11 第一节），所以读起来处处像，**但不能刷**。
这条从"待查"变成定案：不刷，永久（6.3 第 8 条）。

**② 顺带挖出厂商自己写在 BIOS 里的一整套 UEFI shell 命令面。** 下面每一条都是 ROM 里的
原始字符串，不是我推的；它把 Windows 侧的能力名和固件侧的实现一一对上了。

- **APCtrl 功能位**（`Current APCtrl Configuration: 0x%04X 0x%02X 0x%02X 0x%02X`）：
  `/AP` 飞行模式、`/GS` GPU 切换、`/OC` 超频、`/MK` 宏键、`/SK` 快捷键、
  **`/WK` Win Key Lock**、`/BL` 呼吸灯、**`/FB` FanBoost**、**`/SM` Silent Mode**、
  `/UC` USB 充电、`/RGBKB` RGB 键盘、`/RGBLG` RGB Logo、`/CHINA`（中国区键盘背光默认开）、
  `/PB` Power battery。
  → 我们唯一验证过的写操作 **Win 锁**，在固件侧叫 `/WK`，是 APCtrl 的一个**功能位**，
  不是 EC 里的独立开关；`FanBoost` / `Silent Mode` 说明档位语义在 BIOS 侧本来就有名字。
- **SW Board ID**：`BIT0: LCD Q-key`、`BIT1: EC Battery Boost`。设置项帮助原文：
  `1, FAN BOOST: Q-KEY will be defined as enable/disable fan boost function;
   2, Q-KEY will be defined as mode switch function, that between office mode and gaming mode`
  → **Q 键（造物者键）的行为本身是 BIOS 可配的**。这正好解释 6.4 里机主观察到的
  "按几次之后只剩全亮/不亮两种状态"——那一下切的是 Q 键的**定义**，不是档位。
- **Notebook Type / Mode / Table**，存在独立变量 `OemMyfanOption` 里：
  Type = `Standard` / `Commercial`；Mode 是 8 个枚举
  （`FanOfficeMode`、`FanGamingMode`、`SwitchOfficeMode`、`SwitchGamingMode`、
  `SwitchTable1Office`、`SwitchTable1Gaming`、`SwitchTable2Office`、`SwitchTable2Gaming`）；
  `/Table : Set Myfan3 Mode`。
  → 出现 **Table1 / Table2 两套风扇表**。这直接给待办 #31 那个悬着的问题
  （"换个档位再转储一次，看 `0x0F00-0x0F5F` 会不会跟着变"）提供了固件侧旁证：
  EC 很可能有两套表在切，我们读到的那一套不一定是**当前生效**的那套——
  和面板上那句 note 说的一致。
  → 也印证了 6.2 那条老结论：PL 寄存器 `0x783-0x785` 是 **MyFan3 一代**的落点，
  厂商自己的命令就叫 `Set Myfan3 Mode` / `Set Myfan3 Table`。
- **键盘灯（LEDKB）**：`/GetStatus` 读当前设置，`/SetData <8 字节>`，格式串 `%02X`×8 与 ×9。
  厂商自己给的样例：
  `/SetData 0x08 0x03 0x0A 0x05 0x32 0x04 0x00 0xAF` 与
  `/SetData 0x08 0x03 0x0A 0x05 0x32 0x04 0x00 0x00`。
  → 与我们从 HID 逆出的 9 字节 Feature Report
  （`[报告ID, opcode, Control, Effect, Speed, Light, ColorIndex, Direction, Save]`）**同形**，
  而且第 5 个数据字节 `0x32 = 50` 正是实测亮度五档 `0/8/22/36/50` 的顶档。
  这是待办 #29 目前最硬的一份旁证——**厂商自己写的样例值**。
- **USB 灯条（USB Light Bar）**：三条命令 `1AH / 14H / 08H`，样例
  `/SetMode 0x1A 0x05 0x01 0x14 0x00 0x00 0x00 0x00`、
  `/SetMode 0x14 0x00 0x01 0xff 0xff 0xff 0x00 0x00`、
  `/SetMode 0x08 0x02 0x03 0x05 0x24 0x00 0x00 0x00`。
  → 首字节即 opcode；`0x14` 那条第 4-6 字节 `ff ff ff` = RGB 白；
  `0x08` 那条第 5 字节 `0x24 = 36` 又是亮度档之一。
- **RGBKB 颜色等级**：`/Set <6-digits>: 000000 ~ 505050`，
  `Sample1: /Set 494847 => Set Red:49 Green:48 Blue:47`，`Current RGB Configuration: R:%d, G:%d, B:%d`。
  → **每通道 0-50**，与 EC 侧 `RGBKB_LEVEL_R/G/B` 三个寄存器名、以及亮度五档的 0-50 刻度对上。
- **适配器功率表**：`330W / 230W / 180W / 150W / 120W / 90W / 65W / 40W`，`/Type` 读、`/Set` 写。
  → 待办 #27（功耗墙）多一条线索：**BIOS 认适配器瓦数**，PL 很可能按它缩放。
- **`/SetKBL`**：`The KB Board ID: Four light=1, Single light=2, common light=3`、
  `Sample: /SetKBL 1 => Set KB Four Light Board ID`、`KB language type is 0x%02X`、
  `This command is not applicable for this keyboard`、`(Your setting will be applied after restart.)`。
  → 键盘背光板只有三种：**四区 / 单色 / 普通**，与 GCUBridge 报回的
  `solution=ITE, type=FourZone` 对上。
- **`OemTdr`**：`/SetTDR: Set OemTDR value from 0 to 255`，存在 `UniWillVariable` 里，打印 `%03d`。
  语义没查出来，只记存在，**不猜**。

**③ OEM DXE/SMM 模块清单**（356 个模块名里属于 OEM 的那批），每个都是 Windows 侧某项能力的
固件对家：`OemACPIDriverDxe` / `OemACPIDriverSmm` / `OemACPIDriverHookDxe`
（就是我们 EC 通道用的 `\\.\ACPIDriver` 的固件侧）、`OemPowerModeDxe`、`OemTurboModeDxe`、
`OemKbLightDxe`、`OemUsbLightBarDxe`、`OemKbLightSupportDxe`、`OemQkeyDxe`、
`OemSWBoardIDDxe`、`OemDgpuBoardIDDxe`、`OemApControlDxe`、`OemDDSSupportDxe`、
`OemOcDxe` / `OemOcPei` / `DxeOverClock` / `OverclockInterface` / `OverClockSmiHandler` /
`PeiOverClock`、`OemUniWillVariableDxe`、`OemVariableHookDxe`、`OemGlobalNvsDxe` /
`OemGlobalNvsSmm`、`OemServiceDxe` / `OemServiceSmm`、`OemManufactureModeDxe`、
`OemACRecoveryDxe` / `OemACRecoveryPei`、`OemDisplayModeDxe`、`OemNetworkDxe`、
`OemI2cDevices`、`OemHddHeadParkSmm`、`OemHooks` / `OemHooksPei` / `OemHooksSmm`、`EcPs2Kbd`。
（导出的 25 个模块段体在 `tools/out/rom-mods/`；它们本体是被剥过字符串的桩，
2-7 KiB，字符串都在共享的命令面里。）

**④ 一件让 6.3 第 8 条红线更硬的事**：把导出的 25 个 OEM 模块挨个抽字符串，
**10 个引用同一个 `UniWillVariable`**——`OemPowerModeDxe`、`OemTurboModeDxe`、
`OemKbLightDxe`、`OemUsbLightBarDxe`、`OemOcDxe`、`OemServiceDxe`、`OemACRecoveryDxe`、
`OemDgpuBoardIDDxe`、`OemDisplayModeDxe`，加上 `OemUniWillVariableDxe` 自己。
也就是电源模式、Turbo、键盘背光、USB 灯条、超频、AC 恢复、dGPU 板 ID、显示模式、OEM 服务
**九个子系统共用这一个运行时可写变量做持久化**。原来不写它的理由是"里面有 VDDQ 电压项、
写 `0x33` 会放出内存超频菜单"，现在多一条更朴素的理由：**写它就是同时改九个子系统，
其中任何一个的读-改-写都能把我们盖掉，而且盖掉之后我们无从察觉。**

ROM 里还躺着一个 `MeUnlock` 命令面（`/Set 1 => Unlock ME region (only one time)`，关机后生效）。
它同样走 UEFI 变量，落在 6.3 第 8 条"不写 UEFI 变量"里，**不碰**，只记一笔。

**⑤ 没做的事**：没刷写、没运行 `AFUWINx64.EXE` / `F.bat`、没进 UEFI shell、没写任何变量、
没碰 ME 区、没执行 ROM 里的任何代码。产物全在 `tools/out/`（gitignore）：
`rom-fv-report.txt`、`rom-fv-find.txt`、`rom-fv-modules.txt`、`dxe_fv.bin`（10 MiB 解出的 DXE 卷）、
`rom-mods/*.pe32`（25 个）。

#### 十、ROM 里装着五份 EC 固件，每份都自带一整套默认风扇表——本机现在跑的不是厂商默认曲线（2026-09-30）

工具 `tools/rom_ec_fantable.py`（26 个用例 `tests/test_rom_ec_fantable.py`，合成镜像，不需要真 ROM）。
同样**只 open 磁盘文件**，不刷写、不碰 EC、不发 IOCTL。
它和第八节那个 `tools/ec_fantable_dump.py` 是**两个脚本、两条方向**：后者只发 ECREAD、读的是
本机活着的寄存器，前者读的是固件镜像里的出厂默认值；`--live` 把后者的产物喂给前者做对照。

**① 这份客服 ROM 是个「一包多板」的 EC 合集。** 里面认得出**五份** ITE EC 镜像
（判据是镜像里有 `ITE EC-V` 标识串，五份都是 `ITE EC-V14.6`）：

| 位置 | 形态 | 与参考仓库 `GMxMGxx_11.800` 的字节差异 |
| --- | --- | --- |
| `0x000000..0x040000` | 在任何固件卷**之外**，ROM 头部 | 31.39% |
| `0x43CEA0` | FV@0x193000 里的 FREEFORM 段，GUID `207f94a8-238e-11e8-…` | **0.00%（逐字节相同）** |
| `0x47CEC0` | 同上，GUID `207f989a-238e-11e8-…` | 30.88% |
| `0x4BCEE0` | 同上，GUID `207f9a0c-238e-11e8-…` | 31.60% |
| `0x4FCF00` | 同上，GUID `b3396d33-08a2-4ad0-…` | 10.64% |

第二份**就是**参考仓库那份 EC 固件本体（sha256 前 16 位同为 `158d1c6416426939`）。
也就是说 GM7MG7P 那套逐指令反汇编，对的是这个 ROM 里真实存在的一块。
机主当初那句"客服面对 30 系列都给同一个 bios 文件"，机制就在这儿：**一个 ROM 里塞了
好几块板的 EC**，刷写时按板子挑一份。

**② 顺手把 6.11 第一节那格结论改准。** 原表写"EC 代码段本体 31.39% 字节不同 →
同一源、不同 build"，比的是参考 EC 对 **ROM 头部**那份。现在五份互相全比了一遍：

```
头部 ↔ 207f94a8  31.39%      207f94a8 ↔ 207f989a  30.88%
头部 ↔ 207f989a  31.72%      207f94a8 ↔ 207f9a0c  31.60%
头部 ↔ 207f9a0c  32.45%      207f989a ↔ 207f9a0c  20.13%
头部 ↔ b3396d33  31.17%      207f94a8 ↔ b3396d33  10.64%
```

**31% 是同一个 ROM 里「不同板子」之间的正常距离，它什么也证明不了。**
"同一源"这个判定还是成立的，但靠的是 ITE8850 PD 镜像逐字节相同 + 四个 bank 跳板
逐字节相同那两格，不是这个百分比。10.64%（`207f94a8` ↔ `b3396d33`）才是"近亲"的样子。

**③ EC 的默认风扇表能离线读出来，而且它反过来独立证实了表布局。**
每份 EC 镜像里都有一批 **48 字节的定长记录**：`UpT[16] + DownT[16] + Duty[16]`，
字段与 EC 寄存器 `0x0F00`(CPU) / `0x0F30`(GPU) **逐一对应**——
`UpT[0]` 是点 1 的升温阈值（点 0 不存）、`DownT[0]` 是那个从不读的字节（真表里恒 `0x00`）、
`DownT[1..15]` 才是点 0..14、`Duty` 存的是**百分比 × 2**。
记录按 48 字节等距排布、相位一致（这几份镜像里全是 `偏移 % 48 == 2`）。

到 6.10/6.11 为止，表布局的唯一来源是厂商 `FanTable_Manager1p5.SetEcFanTable` 的**解密 C#**；
现在**固件本体也这么说**，两条独立证据对上了。

**④ 本机的温度点，和 ROM 里四份 EC 的默认表逐字节相同；占空比完全不同。**

| | 升温阈值 | 降温阈值 | 占空比 |
| --- | --- | --- | --- |
| 本机活表 CPU | 53,57,59,61,63,67,69,71 | 48,50,60,62,64,68,70,72 | **0,66,99,99,…%** |
| ROM 默认 CPU | 53,57,59,61,63,67,69,71 | 48,50,60,62,64,68,70,72 | 0,30,30,35,45,48,50,55,55,…% |
| 本机活表 GPU | 52,53,56,58,60,63,65,67 | 48,50,57,59,61,64,66,68 | **0,76,100,100,…%** |
| ROM 默认 GPU | 52,53,56,58,60,63,65,67 | 48,50,57,59,61,64,66,68 | 0,30,30,35,45,48,50,55,55,…% |

四份 FREEFORM EC（`207f94a8`/`207f989a`/`207f9a0c`/`b3396d33`）**都**含这两组温度点
（各 15~16 套表里的一套），偏移不同、内容相同。
ROM 头部那份（也就是 GM7MG0M 自己的）温度点是**另一组**
（`50,54,62,70,76,81,85,87` / `48,52,55,60,65,68,72,76,88,92` 等 9 套），跟本机对不上。

**占空比 132(=66%) 和 198(=99%) 这两个字节，五份镜像里一个都没有。**
厂商默认在这组温度点上最高只给 **55%**；要到 100% 得温度点伸到 83 °C 以上
（同一份镜像里另有 `UpT …67,75,80,83 → Duty …50,55,70,85,100%` 那几套"热表"）。

**这有两种解释，现在还分不开**：
（甲）host 侧服务写过一条更凶的曲线，盖掉了固件默认；
（乙）本机（GM5MG0Y）自己的 EC 默认就是这样，而**它不在这五份里**——
这个 ROM 是 GM7MG0M 的，四份 FREEFORM 是它带的近亲板。

分清只需要 6.11 第八节末尾那件便宜事：**换个硬件模式再转储一次**。
跟着模式变 → 是甲（host 写的）；纹丝不动 → 是乙（本板固件默认）。
这一步要写档位，按 6.3 第 2 条等机主在场（待办 #31）。

**⑤ 别把"厂商默认 55%"当成可以照抄的安静曲线。** 那是**别的板子**在**别的散热模组**上的
默认值；本机 10875H + RTX 3060 在 71 °C 只给 55% 大概率压不住。这一节的用途是
（i）独立钉死表布局，（ii）证明本机曲线不是我们改坏的、也不是固件原样，
（iii）给 #31 提供一份**厂商自己写的**参照形状（阶梯 0/30/35/45/48/50/55…），
不是拿来直接下发的数值。按 6.3 第 2、3 条，写入前必须自己夹边界 + 存原值 + 85 °C 保险。

**⑥ 没做的事**：没刷写、没写 EC、没执行镜像里任何代码。
按第 9 节的规矩，从 ROM 里切出来的镜像与解出的表**不进仓库**，
仓库里只有那个只读提取器 `tools/rom_ec_fantable.py`（+ `tests/test_rom_ec_fantable.py`）；
探索期的脚本与产物留在 `tools/out/`（gitignore）：
`rom_grep.py`、`rom_map.py`、`rom_fantable.py`、`rom_ec_blobs.py`、`rom_ec_compare.py`、
`rom_ec_tables.py`、`fantable_rom_search.py`。

## 7. 运行方式（目标是"不用盯着"）

- 开机自启：`HKCU\...\Run\UmiControlPanel` → `UmiPanel.exe main.py --supervise --no-browser`；
- 守护模式 `--supervise`：子进程异常退出会自动拉起（实测强杀后 6 秒恢复）；
  面板里点「停止面板服务」属于正常退出（code=0），守护**不会**复活它；
  10 分钟内异常退出超过 5 次则停止自动重启，避免启动即崩时空转刷屏；
- **卡死也算故障**：守护每 5 秒问一次 `/api/ping`，判据不是「端口通不通」而是
  「调度节拍多久没走」（`tick_age_s`，超过 90 秒判卡死 → 杀掉重拉）。
  这是针对 open-revo 那个死法设计的：它进程还在、界面无响应，从此再也起不来。
  节拍判定同时覆盖两种情况——整个进程挂住（请求超时）和 HTTP 线程还活着但
  调度线程被拖住（比如 EC 请求不返回）；后者光看端口是查不出来的。
  8 条判定单测里专门有一条「连续 30 次正常应答不得误杀」。
- 单实例用内核命名互斥 `Global\UmiControlPanel`，进程死了由系统回收，
  不存在"残留锁导致再也起不来"——这正是 open-revo 栽过的坑。
  **但这个锁曾经形同虚设**：`ctypes.windll` 不会把 last error 抄进 ctypes 自己的
  线程局部存储，`get_last_error()` 拿到的是残值，`ERROR_ALREADY_EXISTS` 永远判不出来。
  2026-09-30 实测两个实例并排跑了十几分钟（改用 `WinDLL(..., use_last_error=True)` 修好，
  `tests/test_singleton_port.py` 盯着）；
- 端口被占自动顺延 10 个并把实际端口写进 `data/port`；配置损坏则改名备份后用默认值继续跑。
  顺延那条路以前也走不到：`HTTPServer` 默认开 `SO_REUSEADDR`，而 **Windows 上它的语义是
  「允许绑到别人正在监听的端口」**，所以第二个实例不报错、直接和第一个共用 8747，
  请求随机落到谁身上。现在关掉了复用，端口被占就真的会顺延；
  顺延结果**不再回写 config.json**（只写 `data/port`），否则几次快速重启后端口会一路漂到 8777。

## 8. 已知问题 / 待办

- [x] EC 只读通道：已打通并自校验（电量与系统 API 一致），风扇/电池/PL 默认值/机型 ID 全部可读
- [x] EC 写通道：布局已确认，硬件模式字节写入实测生效且可逆还原
- [x] 实体「造物者模式」按键落点：确认就是 `MAFAN_CONTROL_BYTE`，而且它是**硬件模式的总开关**
      （PL1 75W↔10W、风扇 PWM 表、TGP 都跟着走，见 6.2/6.4），三态的真实差别已量化
- [x] 面板与按键不再抢方向盘：按键优先窗口 + 用户档永不自动覆盖 + 换挡防抖
- [x] 实体键真的会切模式：全亮→锁定性能、半亮→自适应、不亮→锁定省电，
      并按「只有人动手才弹」的规矩给屏幕提示（`app/tray/osd.py`，自适应换档一律不弹）
- [x] 全项目一套模式词（省电/均衡/流畅/性能），意图叫「自适应 / 锁定某模式」，
      办公/狂暴两个词删掉；词表只在 `scheduler.py` 定义，网页/托盘/弹窗都从这里取，用例盯着不许分叉
- [x] 跑分负载换成开源工具（官方 zstd + 本机现编 CoreMark），进度条走到底并给 ETA，
      四档实测 167 秒跑完（结果见第 2 节：**powercfg 那一层压不住频率**，面板如实弹警告）
- [x] GCUBridge 服务已恢复，MQTT 通道连上并用于只读（见 6.6）
- [x] **灯效不在 EC 上**：机主点背光/灯条时全表纹丝不动，`LIGHTBAR_CONTROL_BYTE`、
      `RGBKB_LEVEL_R/G/B`、`SINGLEKBL_ENABLE` 在灯亮着时全是 0；真实通路是
      `Keyboard/Ctrl {"function":"SetPower","light":"3","speed":"2"}`（ITE / FourZone），见 6.7
- [x] 触摸板上有**实体拨动开关**，`TouchpadToggle` 是它的读数；OEM 另有
      `TOUCHPAD_TOGGLE_ON/OFF`，但两条路会不会互相盖没验证过——面板只读不写
- [x] 官方主板驱动包（`01-Chipset`）查过了：Intel Chipset Device Software，纯 INF/CAT，
      不含 EC 相关任何东西；顺带确认本机**没有 Intel DPTF/ESIF**，功耗墙只能走 OEM 那条路
- [x] 双实例并跑的 bug：互斥锁因 `ctypes.windll` 拿不到 last error 而失效 +
      `SO_REUSEADDR` 在 Windows 上允许二次绑端口，两个实例共用 8747 还互踢 MQTT 身份。
      两处都已修，`tests/test_singleton_port.py` 6 条盯着（见 7 节）
- [x] **第三次观察跑完（240 秒没掉线）**：`ADDR_STAUTS_BYTE` = Win 键锁定、
      `ADDR_AP_OEM_BYTE4` 高半字节 = 电池充电档，两条都与 GCUBridge 的命令逐条对齐，
      已从「候选」升级为「已确认」（见 6.8，`tests/test_battery_winlock.py` 钉住）
- [x] 机主给的 `UmiPro3-Hackintosh`（OpenCore 工程）核过了：确认本机是同方 GM5MG0Y，
      但 19 个 SSDT 全是通用热补丁、没有 EC 字段定义，对本项目只有 dGPU 走 ACPI 这一条旁证（见 6.8）
- [x] **电池三档的百分比：问法本身不成立，这条可以关掉**（见 6.11 第三节）。
      原先打算「切到某一档、把电充到停、看停在百分之几」，但 EC 反汇编说明
      这个家族的封顶是**充电电压** `0x0522/0x0523`（按循环数/温度老化降额，
      200/100/0 mV/cell 三档地板），**代码里不存在任何百分比**；
      `0x0522` 还 host 写不住（<101 µs 被 EC 夺回）。
      那套「`0x07C3`/`0x0770` 门控 → `0x087F` 存储上限 → 每秒读 `0x07B9`」的
      百分比模型来自别的板子（无界 14XA），判据是 `0x0742` bit2——
      **本机读到 `0x742 = 2`，bit2 = 0**，与他们同项目板的读数一致，机制不在场。
      三档编码 `0x07A6` bits[5:4] = 00/01/10 已获独立印证。
      面板**永久只显示档位名、不显示百分比**，这不是"还没测"而是"没有这个量"。
      附带结论：电池老化到 250 mV/cell 档之后，三档在电压上不会有可观测差别，
      所以早先"切档看不出变化"是自洽的，不用再当 bug 查
- [x] **MQTT 写通道的第一次可逆验证做完了**（Win 键锁定）：`WINKEY_UNLOCK` 下发后
      EC `ADDR_STAUTS_BYTE` 1→0，`WINKEY_LOCK` 写回后又回到 1，EC 直读与 `Setting/Status`
      两条通道一致，写入到生效约 6 秒延迟。`winkey.write` 已升为 verified，
      面板第一次给出了 OEM 写按钮（见 6.9）
- [ ] **`OPERATING_*_MODE` 的可逆验证还没做**：动作名来自 OEM 自己的动作表，
      但本机还没做过「写 → 观察哪个寄存器/跑分变了 → 还原」。
      验证之前 `mode.write` 保持「待验证」，面板不点亮那组按钮（6.3 第 2 条）。
      既然 Win 锁那条链已经走通，这一步只差一次带跑分对照的实测。
      **固件侧新增旁证**（见 6.11 第九节②）：BIOS 自己的 `/Mode` 枚举是
      `FanOfficeMode`/`FanGamingMode`/`SwitchOfficeMode`/`SwitchGamingMode`，
      APCtrl 另有 `/FB FanBoost` 与 `/SM Silent Mode` 两个功能位——
      说明"档位"在固件里是**风扇模式 + 开关模式**两件独立的事，
      和仓库记的 `OperatingMode` `{Office:0, Gaming:1, Turbo:2}` 不是同一根轴，
      验证时要连 `0x0751` 的 bit 一起看，别只对一个整数
- [x] **PL 自清零的原因查明了，而且和我们原先的猜测相反**（见 6.11 第二节）。
      原先归因于"自定义模式激活链没置齐"——**那条链不存在**，是外部资料的误传
      （`0x0706` 是倒计时器，`0x0726`/`0x0727` 在 EC 镜像里零引用，厂商服务侧零命中）。
      真实机制：EC 主镜像里 `0x0783-0x0785` **各只有 1 个写点**（`0xA833`），
      且被 `0x0741` bit0 门控——bit0 清零才把三个 PL 归零；本机 `0x741 = 1`，
      这条分支不该跑。更要紧的是每档默认值块 `0x0730-0x0737`/`0x07A7-0x07AA`
      在整份镜像里**一个读点都没有**，34 个 `0x0751` 分支臂也没有一臂把默认值搬进 PL。
      **结论：写档位字节永远不会带动 PL，PL 必须由 host 自己写**——
      所以改功耗墙只有 `SET_OPERATING_MODE_DETAIL` 一条路，不用再惦记激活链
- [ ] 改功耗墙的正路已经看到了：`Fan/Control {"Action":"SET_OPERATING_MODE_DETAIL","PL1","PL2","PL4"}`
      → `SetPL1/2/4Value` → `EcCtrl.Write(0x783/0x784/0x785)`，
      而且 `Fan/Status` 给出了 OEM 自己的边界（PL1 10~120W、PL4 ≤165W、TGP 80~115、
      目标温度 75~87°C）。**但 6.11 查出服务端对这三个值完全不做边界校验**：
      `Convert.ToInt32` 后直接 `(byte)` 截断，`_SmartApcTable` 与 `CpuPL1Minimum`
      只发给 UI 当 Maximum/Minimum、不参与 clamp（发 300 会静默变成 44W）。
      所以护栏**必须由我们自己夹**，下发前拿 `Fan/Status` 的上下限过一遍，
      再走可逆验证（存原值 → 写 → 回读 → 还原 + 85°C 温度保险），验证前不进白名单。
      **固件侧新增线索**（见 6.11 第九节②）：BIOS 里有一张适配器功率表
      `330W/230W/180W/150W/120W/90W/65W/40W`，`/Type` 读 `/Set` 写——
      **固件认适配器瓦数**，PL 很可能按它缩放，夹上限时该把它一起算进来。
      另注：PL1/PL2/PL4 是三个独立 `if`，其余 GPU/TCC/电压项全是 `else-if` 链，
      **一条消息只能改一个非 PL 项**
      （注意 `Fan/Status` 不是随时都有：本机现在只在被 GETSTATUS 问到时才报，
      所以「OEM 允许范围」这一行经常显示「未报」，不是 bug）
- [ ] `OperatingMode` 的真实取值：`Tray/Status` 报的值不在 OEM 枚举 `{Office:0, Turbo:2}` 里，
      现在一律按「未知」处理并把原始值暴露在通道状态里，等一次点击观察把它对上
- [ ] 这些开关的**写入**都还没验证，面板一律只读：USB 关机充电、OSD、Fn/NumPad 锁、
      Fn+F1 快捷键、色彩管理、显示模式、独显直连。状态读数是齐的（见 6.8 那条完整
      `Setting/Status`），命令名也在 OEM 字符串表里，缺的还是「下发 → 回读 → 还原」那一次实测
- [ ] 键盘背光的写入：命令已知（`Keyboard/Ctrl {"function":"SetPower","light","speed"}`），
      状态也已知（`Keyboard/Status.powerStatus` 才是真开关，`SingleColorKBBL` 是另一件事），
      同样等一次可逆验证。「灯效不在 EC 上」这条阴性结论现在**有了完整解释**（见 6.11 第四节）：
      ITE 8291 是 USB HID 设备（`048D:CE00` 键盘 / `048D:6005` 灯条），
      EC 侧灯条寄存器 `0x0748-0x074B` 零引用、写了也没效果
- [ ] **新通道候选：灯效走纯用户态 USB HID**。协议已完整逆出——`SET_REPORT` Feature，
      9 字节 `[0, opcode, Control, Effect, Speed, Light, ColorIndex, Direction, Save]`，
      `0x08` 灯效 / `0x09` 亮度 / `0x14` 调色板 / `0x80` 读固件版本；
      亮度 5 档 `0/8/22/36/50`、速度 5 档 `10/7/5/3/1`，关灯 `(1,0,0,0,0,0,0)`。
      好处是不碰 EC、不碰内核驱动、天然可逆；但它是**新的写通道**，
      得按 6.3 第 2 条重新走一遍「语义佐证 + 可逆 + 温度保险」，本轮没做。
      **语义佐证这一条已经拿到最硬的一份证据了**（见 6.11 第九节②）：厂商自己写在 BIOS
      命令面里的样例值，与 HID Feature Report 同形、同刻度——
      LEDKB `/SetData 0x08 0x03 0x0A 0x05 0x32 0x04 0x00 0xAF`（第 5 字节 `0x32 = 50`
      正是实测亮度五档 `0/8/22/36/50` 的顶档）、USB Light Bar 三条 opcode `1AH/14H/08H`
      配样例（`0x14` 那条第 4-6 字节 `ff ff ff` = RGB 白，`0x08` 那条第 5 字节 `0x24 = 36`
      又是亮度档之一）、RGBKB `/Set <6-digits>: 000000 ~ 505050`（**每通道 0-50**）。
      还差的是「可逆」那半：Feature Report 大多没有读回，可逆性得换个方式论证
      （先存一份已知的良好设置，写完能还原成它）。
      **前提已只读钉死**：`Get-PnpDevice` 枚举确认本机 `VID_048D&PID_CE00`（键盘）
      与 `VID_048D&PID_6005`（灯条）都在、各有 MI_00/MI_01 两个接口、Status OK
      （见 6.11 第六节）
- [ ] **一处没解释的新观察**：PL 会跟着档位变（Turbo 时 PL1=75、自适应落到省电时 PL1=10），
      但面板从不写 PL，而按他们的镜像 EC 也不该写（唯一的写点被 `0x0741` bit0 门控，
      本机 bit0 已置）。最可能是**一直在跑的 `GCUService` 看到档位变了就重刷 profile**——
      若成立，6.2 记的"裸写 PL 自清零"就有了更简单的答案：不是 EC 拒绝，是服务盖回去了。
      **未证实**（本机自己的 EC 固件至今没有独立取过；客服 ROM 里那五份都不是本板的，
      见 6.11 第十节），要证实得在服务不动的情况下单独改档位字节，
      那是一次写操作，等机主在场。见 6.11 第七节
- [ ] **本机 `GCUService.exe` 的解密**（能拿到我们自己的完整寄存器表，价值最高的一步）。
      方法已抄在 6.11 第五节：内存转储而非静态破解 ConfuserEx，只 `ReadProcessMemory`，
      不写目标进程、不发 IOCTL。**但需要管理员权限（SeDebugPrivilege），机主不在场就没跑。**
      已知风险：他们只在 3.1.39.0 上成功过，别的 build 混淆强度不同；
      成败以 `dotnet_bodies.py` 的 invalid 计数为准
- [x] 风扇曲线**读取**：已落地并实测（`EcChannel.fan_curve()` + `GET /api/fan-curve`
      + 面板卡片 + `tests/test_fan_curve.py` 12 个用例，见 6.11 第八节）。
      两轮各 96 次读、**0 次写**、2.9 秒、`incomplete=0`，与停机转储逐点一致；
      `fan.curve` 已置 verified，另有 `fan.curve.write` 恒为 blocked。
- [ ] 风扇曲线**写入**：**表布局已裁定**（见 6.10 末尾那张表，来源是厂商
      `FanTable_Manager1p5.SetEcFanTable` 的解密源码，不再是两份资料打架），
      而且**它适用于本机这件事已经只读证实了**：厂商选实现类的三个判据里，
      `0x0740` PROJECT_ID = 15 ✓、`0x078E` bit6 `IsSuportRamFan1p5` = 1 ✓
      （为此把 `ADDR_SUPPORT_BYTE6` 加进了低频只读组，见 6.11 第六节），
      只剩注册表 `CustomizeTarget` 没读。
      完整写序列是 `SetFanTableSetting` → `SetEcFanTable` 写
      CPU `0x0F00+i`/`0x0F11+i`/`0x0F20+i` 与 GPU `0x0F30+j`/`0x0F41+j`/`0x0F50+j`，
      Duty 存 `百分比×2`，外层用 `0x07C6` bit2 拉低/置高把整段括起来。
      **`0x0741` 和 `0x07C5` bit7 都不在这个序列里**（后者是 CPU/GPU 分表开关，
      默认 false、实测恒 0；本机却读 `0x80`，这点差异要留意）。
      `0x0F5D-0x0F5F` 被「问 EC 要默认表」的信箱借用，不能当占空比槽写。
      还缺的实测是「写单个 PWM 会不会被 EC 覆盖」——他们那边有旁证说
      **单写 `0x0751` 不会让 EC 自动加载表或 PL**，方向上一致，但本机没测过，暂不写。
      写入之前还有一件更便宜的事该先做：**在不同档位各转储一次**，看 `0x0F00-0x0F5F`
      会不会跟着变——这才能区分「host 写入区」和「此刻真正在管风扇的表」
      （6.11 第八节末尾那个局限）。需要一次档位写入，按 6.3 第 2 条等机主在场。
      **固件侧给这个前提加了旁证**（见 6.11 第九节②）：BIOS 的 `/Mode` 枚举里
      `SwitchTable1Office`/`SwitchTable1Gaming`/`SwitchTable2Office`/`SwitchTable2Gaming`
      四个值明摆着**存在 Table1 / Table2 两套风扇表**，命令名还叫 `Set Myfan3 Table`。
      所以我们读到的那 92 个字节很可能只是其中一套，另一套在别处——
      换档转储这件事从"值得做"升级成"必须先做"，否则写入可能落在没生效的那套表上
- [ ] `ADDR_MYFAN2_L1~L5_PWM` 与 `User_Fan_Level1~5(0x81~0x85)` 看着是一对，
      全表观察也抓到按硬件模式时 `MYFAN2_L1/L4_PWM` 会跟着换（3↔7、5↔15）。
      EC 侧真正吃「用户风扇模式」的是 **`0x0751` bit7(USER)**：bank1 `0x9432` 的
      USER 臂按 bit7 在两张 CODE 表之间选，用 **`0x0627` 低半字节**做索引，
      写 `0x0626`/`0x0627`/`0x0895`。这两个档位状态字节很可能就是早先
      「激活链」传说里 `0x0726`/`0x0727` 的讹传来源
- [ ] **同方 WMI 接口在不在本机**：查不到也否不掉——普通权限连微软自己的
      GUID 类都看不见（见 6.5）。需要用管理员权限跑一次 `tools\wmi_guid_probe.ps1`（只读）。
      优先级已经降低：GCUBridge 这条路通了之后，大部分功能都有 OEM 背书，不用赌 WMI。
      另外查清了两件事（见 6.10）：`tongfang-mifs-wmi` 那条线索**可以关掉了**
      （命令集里没有风扇曲线、没有 PL、没有充电阈值）；真正带
      `charge_control_end_threshold` 的是另一个 GUID `ABBC0F6F-…` 的 `AcpiTest_MULong` 邮箱
- [x] 两道写入闸门补齐（见 6.9）：`send_action` 之前不查 `allow_write`、
      `do_POST` 之前不校验来源，等于任何网页都能 CSRF 本机面板下发 OEM 命令。
      现已修好并由 `tests/test_write_gates.py` 10 条盯着
- [x] 外部逆向资料交叉核对完毕（见 6.10）：BIOS 那条路是死的（无解锁版、刷写被拒、
      降级被拒），GM5MG0Y 的 DSDT/EC 转储公网不存在；但同家族近亲板的三份实测
      与我们的 OEM 常量表逐个地址对得上，并当场只读复测了 12 个地址
- [x] **客服 ROM 的固件卷解开了，内部机型名是 `Taitan Series GM7MG0M`——它不是本机的**
      （见 6.11 第九节）。工具 `tools/rom_fv_dump.py` + 12 个合成镜像用例
      `tests/test_rom_fv.py`，只 open 磁盘文件，Python 自带 `lzma` 就够，不需要 UEFITool。
      规模：19 个顶层卷 + 3 个内层卷、651 个 FFS 文件、33 段 LZMA 共 12.0 MiB、356 个模块名。
      `cannot update bios with different platform name` 卡的就是 SMBIOS 的产品名/主板名。
      **这条从"待查"变成定案：不刷，永久**（6.3 第 8 条）。
      顺带挖出厂商自己写在 BIOS 里的一整套 UEFI shell 命令面，已分别补进
      待办 #25（档位）/#27（功耗墙）/#29（灯效 HID）/#31（风扇曲线写入）四条里
- [x] **同一份 ROM 里装着五份 EC 镜像，每份都自带一整套默认风扇表——本机现在跑的
      不是厂商默认曲线**（见 6.11 第十节）。工具 `tools/rom_ec_fantable.py`
      + 26 个合成镜像用例 `tests/test_rom_ec_fantable.py`，只 open 磁盘文件。
      三条结论：（i）表布局**第二次被独立证实**——固件本体里也是
      `UpT[16]+DownT[16]+Duty[16]` 的 48 字节定长记录、Duty 存百分比×2、
      `+0x10` 那个字节同样从不读，此前唯一来源是解密的 C#；
      （ii）本机活表的**温度点**与四份 EC 的默认表逐字节相同，**占空比**完全不同
      （活表 CPU 从点 1 起就是 66%、点 2 起 99%；厂商默认这组点上最高只给 55%，
      且 `132`/`198` 这两个字节五份镜像里一个都没有）；
      （iii）6.11 第一节"31.39% 不同"那格**证据作废**——ROM 里有一份与参考
      `GMxMGxx_11.800` 逐字节相同，31% 只是同一 ROM 内不同板子之间的正常距离。
      甲（host 服务盖写过）/ 乙（本板 EC 默认就凶、且不在这五份里）现在还分不开，
      分清只要「换个硬件模式再转储一次」，已并入 #31 的必做前提。
      **注意别把厂商那 55% 当安静曲线照抄**——那是别的板子配别的散热模组的默认值
- [ ] 独显直连（MUX）：`DiscreteGpuDirectConnectionSwitch_Status=ON / Support`，
      而 `DGpu=NV_CTRL_PANEL_AUTOSELECT`。现在倾向于这是**两层不同的东西**
      （直连开关 vs NVIDIA 控制面板的输出偏好），旁证是 Hackintosh 工程里 dGPU 的电源
      挂在 ACPI `\_SB.PCI0.PEG0.PEGP._OFF/_ON` 上（见 6.8）。面板两个值都报，等一次点击观察
- [ ] 托盘图标的桌面可见性需本机确认（沙箱内截不到图）

## 9. 协议与许可参考

EC/ACPI 交互的设计思路参考社区项目 [OpenRevo](https://github.com/faintonce/open-revo)（MIT，Copyright (c) 2026 faintonce）。
本项目为独立实现，不含其源码或二进制；厂商私有寄存器映射与机型表不在本仓库分发范围内。
本仓库采用 **MIT License**（见 `LICENSE`），与 OpenRevo 保持一致。

机械革命/同方（Uniwill）相关硬件行为来自本机实测与离线静态分析记录，
不保证适用于其它模具；在其他机型上开启 EC 写入前，请先只做只读验证。

6.11 那轮"完整复现"用到的主资料，价值远高于其它来源，因为它对的是**同一块代工板**
（PROJECT_ID 同为 `0x0F`、PD 镜像逐字节相同、`ECSpec` 常量表 122/125 同名同址）：
[ElDavoo/tongfang-gm7mg7p-re](https://github.com/ElDavoo/tongfang-gm7mg7p-re)。
本仓库只**离线阅读**它的文档与反编译产物，未执行其中任何脚本，未再分发其内容。

6.10 那轮交叉核对引用到的社区资料（均为第三方机型上的实测，本机只当线索用）：
[Terabinaryte/uniwill-laptop-mr](https://github.com/Terabinaryte/uniwill-laptop-mr)（PL、风扇表；
**它提出的"自定义模式激活链"已被 6.11 推翻**，引用时注意）、
[roj234/mechrevo_ec_api](https://github.com/roj234/mechrevo_ec_api)（同法反射 GCUService 得到的寄存器表与 NVRAM 结构）、
[losewayy/uniwill-ec-charge-limit](https://github.com/losewayy/uniwill-ec-charge-limit)（H2RAM 窗口模型、IOCTL 表、充电限制的坑）、
[w568w 的无界 14XA 逆向记录](https://gist.github.com/w568w/b2fc5f9d1f4dff13efe751abec27b396)（`0x7A6` 三档编码、`\_SB.INOU.ECRR/ECRW`；
它那套充电百分比门控模型在**本板不在场**，判据见 6.11 第三节）、
以及内核文档 [uniwill-laptop WMI device](https://docs.kernel.org/wmi/devices/uniwill-laptop.html)
与 [CVE-2026-64143](https://nvd.nist.gov/vuln/detail/CVE-2026-64143)（6.3 第 8 条那条禁令的依据：
*platform/x86: uniwill-laptop: Do not enable the charging limit even when forced*，
原文即"on some older models (~2020) the battery charging limit can permanently damage the battery"）。
