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
| 电池充电档（平衡 / 健康 / 长效） | **可读，语义已确认** | EC `ADDR_AP_OEM_BYTE4` 高半字节 1/2/0，与 GCUBridge 的 `BALANCEDMODE`/`HEALTHYMODE`/`PERFORMANCEDMODE` 逐条对齐（见 6.8）。**写入未验证**，具体充电百分比也还没实测 |
| Win 键锁定 | **可读，语义已确认** | EC `ADDR_STAUTS_BYTE` 0/1，与 `WINKEY_LOCK`/`WINKEY_UNLOCK` 三次点击三次跳变对齐（见 6.8）；两条通道现在互相印证同一个状态。写入未验证 |
| 充电阈值寄存器 | **读到 0，不在这条路上** | `CHARGE_LIMIT_UP/DOWN` 三次观察全是 0，切电池档时也不动 —— 阈值不由这张 EC 表执行，面板照实显示「未设限」，不做写入 |
| 各模式出厂 PL 默认值、机型 ID | **可用（只读）** | 办公 35W / 均衡 60W / 省电档 75W；ProjectID=15 |
| 实时 PL1/PL2 写入 | **未生效，已找到原因** | 写进去会自清、性能无变化（见 6.2）；离线核对 OEM 代码后确认那一组寄存器是 MyFan3 一代机型的落点，本机是 CML 平台。改功耗墙走风扇字节，不裸写 PL |
| 风扇曲线 | **未验证** | 只有 EC 通道能提供；不做任何驱动穷举（见第 6 节） |
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
scripts/                    setup_runtime.ps1、make_bats.py（bat 生成器）、17 个入口 bat（GBK+CRLF）
tools/                      全部离线只读的逆向与验证工具，产物落 tools/out（已 gitignore）
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
  mqtt_watch.py             GCUBridge 命令观察器：只订阅，最多发一条 GETSTATUS，掉线自动重连
  ec_mode_bench.py          造物者三态满载实测（正反序各一遍，写前存原值、测完还原、97°C 保险）
  tier_effect_test.py       逐项验证 Windows 电源旋钮在本机是否有效（结论：无效）
  freq_probe.py             PDH 频率计数器可用性探测
  fetch_bench_tools.py      取官方 zstd 二进制到 tools/bin（不进仓库）
  build_coremark.py         从 EEMBC 官方源码现编 coremark.exe，编完必须自检出分数才算成功
  wmi_scan.ps1              只读扫本机 ACPI/WMI 设备与 root\wmi 类（普通权限看不见 GUID 类，见 6.5）
  wmi_guid_probe.ps1        只读探测同方 MIFS 那个 GUID 在不在本机（需管理员权限才有意义）
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
- [ ] **电池三档对应的充电百分比还没实测**：`CHARGE_LIMIT_UP/DOWN` 全程 0，
      切档时 PL1 与风扇字节都不动（说明与性能档正交）。open-revo 的 100/80/60 是别的机型的说法，
      不能直接搬。要定这个数只能实测：切到某一档、把电充到停、看停在百分之几
- [ ] **MQTT 写通道的可逆验证**：`OPERATING_OFFICE_MODE / OPERATING_TURBO_MODE` 这些动作名
      来自 OEM 自己的动作表，但本机还没做过「写 → 观察哪个寄存器/跑分变了 → 还原」。
      验证之前 `mode.write` 保持「待验证」，面板不点亮那组按钮（6.3 第 2 条）。
      Win 锁是最安全的突破口：命令名（`WINKEY_LOCK/UNLOCK`）和 EC 落点都已确认，
      状态可逆、无温度风险，适合当「第一次自己下发写命令」的验证对象
- [ ] 改功耗墙的正路已经看到了：`Fan/Control {"Action":"SET_OPERATING_MODE_DETAIL","PL1","PL2","PL4"}`，
      而且 `Fan/Status` 给出了 OEM 自己的边界（PL1 10~120W、PL4 ≤165W、TGP 80~115、
      目标温度 75~87°C）。要做可逆验证 + 拿这组边界当护栏，验证前不进白名单
      （注意 `Fan/Status` 不是随时都有：本机现在只在被 GETSTATUS 问到时才报，
      所以「OEM 允许范围」这一行经常显示「未报」，不是 bug）
- [ ] `OperatingMode` 的真实取值：`Tray/Status` 报的值不在 OEM 枚举 `{Office:0, Turbo:2}` 里，
      现在一律按「未知」处理并把原始值暴露在通道状态里，等一次点击观察把它对上
- [ ] 这些开关的**写入**都还没验证，面板一律只读：USB 关机充电、OSD、Fn/NumPad 锁、
      Fn+F1 快捷键、色彩管理、显示模式、独显直连。状态读数是齐的（见 6.8 那条完整
      `Setting/Status`），命令名也在 OEM 字符串表里，缺的还是「下发 → 回读 → 还原」那一次实测
- [ ] 键盘背光的写入：命令已知（`Keyboard/Ctrl {"function":"SetPower","light","speed"}`），
      状态也已知（`Keyboard/Status.powerStatus` 才是真开关，`SingleColorKBBL` 是另一件事），
      同样等一次可逆验证
- [ ] 风扇曲线读写：`ADDR_MYFAN2_L1~L5_PWM` 与 `User_Fan_Level1~5(0x81~0x85)` 看着是一对，
      而且全表观察抓到按硬件模式时 `MYFAN2_L1/L4_PWM` 会跟着换（3↔7、5↔15），
      但「写单个 PWM 会不会被 EC 覆盖」没实测过，暂不写
- [ ] **同方 WMI 接口（`B60BFB48-…`）在不在本机**：查不到也否不掉——普通权限连微软自己的
      GUID 类都看不见（见 6.5）。需要用管理员权限跑一次 `tools\wmi_guid_probe.ps1`（只读）。
      优先级已经降低：GCUBridge 这条路通了之后，大部分功能都有 OEM 背书，不用赌 WMI
- [ ] 独显直连（MUX）：`DiscreteGpuDirectConnectionSwitch_Status=ON / Support`，
      但 `DGpu=NV_CTRL_PANEL_AUTOSELECT`，两个字段互相矛盾，等一次点击观察分辨
- [ ] 托盘图标的桌面可见性需本机确认（沙箱内截不到图）

## 9. 协议与许可参考

EC/ACPI 交互的设计思路参考社区项目 [OpenRevo](https://github.com/faintonce/open-revo)（MIT，Copyright (c) 2026 faintonce）。
本项目为独立实现，不含其源码或二进制；厂商私有寄存器映射与机型表不在本仓库分发范围内。
本仓库采用 **MIT License**（见 `LICENSE`），与 OpenRevo 保持一致。

机械革命/同方（Uniwill）相关硬件行为来自本机实测与离线静态分析记录，
不保证适用于其它模具；在其他机型上开启 EC 写入前，请先只做只读验证。
