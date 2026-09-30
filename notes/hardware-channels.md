## 6. 硬件通道与写入原则

**⚠️ 本项目不允许对任何内核驱动做穷举/暴力 IOCTL 探测。** 上一轮开发中，
对 `\\.\ACPIDriver` 批量发送 7000+ 组 IOCTL 的行为导致过一次蓝屏（DRIVER_POWER_STATE_FAILURE）。

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
| **TCC 偏移的「当前值」**（EC `0x0786`，不是上面那三个默认值） | **已接上只读，本机实测 `0x00`**（2026-09-30 深夜，见 6.11 第十三节 ⑥ 与第十四节 ⑤）。这一字节 = `APTN`(bit7 使能) + `APTC`(bit0-6 偏移 °C)，进 60 秒低频组，面板状态里叫 `tcc_offset_enabled` / `tcc_offset_c`。**读到 `0x00` 意味着使能位没置、偏移 0 °C**，所以 `0x07D8-0x07DA` 那三个默认值（本机 5/5/5）**当前不生效**，降频点是 TjMax 不是 95 °C。"降频点 = TjMax−5 = 95 °C"是错的推论，`ec_mode_bench.py` 的 `TEMP_ABORT = 97.0` 落在可达区间内、**不动** |
| 机型标识 ProjectID=15、ModuleID=54 | **可用** |
| 硬件模式写入（自适应 / 性能 / 省电 / 风扇加速） | **走错了寄存器，2026-09-30 中午已改**（见 6.11 十五）。上一版这里写「可用，写的就是实体键那个字节」——**那个字节是服务的输出，裸写它只改键盘指示灯、改不动功耗墙**（实测 100 秒 52 个采样点 PL1 死守 10 W）。现在能改墙的只有 GCUBridge 的 `OPERATING_OFFICE/TURBO_MODE`，cap 已升 `verified`，生效约 24 秒 |
| 造物者模式按键字节 `ADDR_MAFAN_CONTROL_BYTE` | **读它是对的，写它是空的**（2026-09-30 中午更正，见 6.11 十五）。全表差分抓到按一次键这个字节 `Turbo_Mode(0x10) ↔ User_Fan_HiMode(0xA0)` 的同时 `PL1_SETTING_VALUE` 75↔10、风扇 PWM、`DynamicBoost_MaxinumTGP` 整组跟着换——**但那是"同时发生"，不是"它导致的"**：按键 → EC 抬 WMI 事件 `176=OSD_FanModeSwitch` → 服务的 `SetUserProfile` 一次性把 PL、风扇表和这个字节全写一遍。服务不轮询这个字节（`GetFanMode()` 零调用点），所以我们写进去它看不见。LED 对应关系仍由用户现场确认：**全亮=性能、半亮=自适应、不亮=省电**，这一行继续用于**读** |
| 硬件档位落在哪个寄存器 | **已确认（2026-09-30）**：不存在单独的「档位寄存器」，档位就是上面那个风扇字节，功耗墙是它的结果。早先那条「按键只动风扇、PL 纹丝不动」的记录是**在 GCUBridge 服务被停用的状态下测的**——那时按键只改了风扇字节，没人去套用整套配置；服务恢复后同一个键就变成了两态循环并带着功耗墙一起走（这一点是从两份日志的差异推出来的，不是直接观察到的因果） |
| 实时 PL1/PL2 写入 | **未生效，原因现在是实测证实的，不再是推测**（2026-09-30 中午，见 6.11 十五）。现象：写 `ADDR_PL1_SETTING_VALUE=35` 回读为 35，1 秒内自清，全核跑分与频率毫无变化（对照 20.71 → 21.22 Mops/s）。**早先那句"0x783-0x785 是 MyFan3 一代机型的落点、本机是 CML 平台写了不认"是错的**，两条反证：① 本机 DSDT 的 `ECMG` 里 `0x0783/0x0784/0x0785` 就叫 `APL1/APL2/APL4`，而且 `T1WR 0x81/0x82/0x84` 会往这三个字节写值——**平台不但认，ACPI 侧自己就是写者**；② 活体只读 `0x0783 = 0x0A`(10 W) 与面板 `pl1 = 10` 分毫不差。**盖掉我们的是 `GCUService`**：它的 `SetUserProfile` 是 PL 的唯一 host 写者，服务活着就会把墙按自己的 profile 刷回去。真机制见 6.11 十三：**EC 从不把每档默认值搬进 PL，PL 必须 host 自己写**。要改功耗墙**只有** OEM 的 `OPERATING_*_MODE`（已验，见 6.11 十五）和 `SET_OPERATING_MODE_DETAIL`（#27，未验），**裸写 PL 字节这条路永久放弃** |

### 6.3 写入原则

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
8. **禁写清单**（不是"待验证"，是不做）：
   - 不写充电门控字节 `0x7C3` / `0x770` / `0x87F`。上游 Linux 驱动因为
     **2020 年前后的机型开充电限制会把电池永久搞坏**，直接把强开路径封死了
     （CVE-2026-64143，*platform/x86: uniwill-laptop: Do not enable the charging
     limit even when forced*）。本机正是那一代。要改充电档只走 OEM 自己的
     `BatteryProtection/Control` 动作，而且要先经用户同意。
   - 不刷 BIOS、不写 UEFI NVRAM（`UEFI_Firmware.dll` 的 `WriteUefi` 一律不碰）。
     GM5MG0Y 是 AMI 板，公开记录里**降级被拒、改版刷入失败、有人警告会黑屏无法启动**。
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
     **这个字节叫什么，现在三方对齐了**（2026-09-30 深夜，见 6.11 十二 ③④）：
     本机 DSDT 的 `ECMG` 字段表把它命名为 **`DBD1`**（GPU Dynamic Boost），
     参考仓库标它 `DO-NOT-WRITE-BLIND`，本机 ROM 里的 `T1WR 0x1173` 分支明写着往它写。
     **常量表里那个 `ADDR_BATTERY_CHARGE_LIMIT_DOWN` 是过期名字，别照着它当充电下限用。**
   - 顺带一条**同族警告**，不在禁写清单里，但必须知道：`T1WR 0x81/0x82/0x84` 写的是
     `EC0_.APL1/APL2/APL4` = EC `0x0783/0x0784/0x0785`，也就是我们的
     `ADDR_PL1/PL2/PL4_SETTING_VALUE`。**DSDT/DPTF 那条路本身就是 PL 字节的活跃写者**，
     我们写进去的 PL 随时可能被 ACPI 侧覆盖。待办 #27 在验证通过前不进白名单，
     这是主要理由之一。

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

用户按清单点了一遍 Creator Center，两份报告在 `logs/观察2`（EC 全表）和
`tools/out/mqtt-watch.txt`（OEM 命令）。**MQTT 那半只录到 00:28:32 就断了**——
原因不是 broker，是我们自己：另一个脚本在观察途中又把面板拉了起来，
两个实例抢同一个 clientId，观察器被顶掉线（这个 bug 见 7 节，已修）。
所以下面标「待复核」的几条，是靠 EC 差分 + 已抓到的那 60 秒命令撑着的。

| 功能 | 结论 | 证据 | 置信度 |
| :--- | :--- | :--- | :--- |
| 键盘背光 / 灯条 | **不在 EC 上**，走 GCUBridge 的 `Keyboard/Ctrl`：`{"function":"SetPower","light":"3","speed":"2"}`；状态从 `Keyboard/Status`、`HidLightbar/Status` 读（`solution=ITE`、`type=FourZone` / `MEZone_Lighbar`，`ACBrightness=3`、`DCBrightness=0`） | 用户点背光和灯条时 EC 全表一个字节都没动；`LIGHTBAR_CONTROL_BYTE`、`RGBKB_LEVEL_R/G/B`、`SINGLEKBL_ENABLE` 在灯亮着的时候全是 0 | **已确认**（阴性结论，两边都指向同一条路） |
| 触摸板 | 触摸板上有一处**物理拨动开关**，`Setting/Status` 里的 `TouchpadToggle` 是它的读数；但 OEM 字符串表里确实有 `TOUCHPAD_TOGGLE_ON/OFF`，所以软件那条路也存在 | 用户 2026-09-30 确认实体开关；`cc-strings-all.txt`、`gcu-allfields.txt` 都有这两个动作名 | 已确认（两条路会不会互相盖没验证过，所以面板只读不写） |
| Win 键锁定 | 候选：`ADDR_STAUTS_BYTE`（1896）1→0，且此后没再变 | 用户只点了一次 Win 锁（00:29:19），EC 就这一个字节翻了；同一时刻 `Setting/Status` 报 `WinKey=WINKEY_STATUS_LOCK` | **已确认**（6.8 用三次连点复核过了） |
| 「平衡 / 健康 / 长效」三档 | 候选：`ADDR_AP_OEM_BYTE4`（1958）高半字节 0→1→2→0，低半字节 9 不动 | 用户连点三次电源模式（00:30:03 / 00:30:09 / 00:30:17），只有这个字节跟着走 | **已确认**（6.8 抓到了命令名） |
| 这三档是什么 | **不是性能档，是电池充电三档**：open-revo 的说明写得很明白——「长效模式 (100%)、日常均衡 (80%)、工作站长寿养护 (60%)」 | 本机 Creator Center 没有办公/均衡/狂暴；`powercfg -list` 只有系统自带那一个「平衡」方案，所以它也不是 Windows 电源计划 | 档位已确认，**百分比仍待实测**（`CHARGE_LIMIT_UP/DOWN` 全程 0，阈值不在这张 EC 表里执行；open-revo 那组数是别的机型的说法，不能直接搬） |

另外这 60 秒里 OEM 自己交代了两件有用的事：

- **`Fan/Status` 把边界写死了**：`CPU_PL1Minimum=10`、`CPU_PL1Maximum=120`、`CPU_PL4Maximum=165`、
  `GPU_ConfigurableTGP` 80~115、`GPU_DynamicBoost` 5~15、`GPU_TargetTemperature` 75~87。
  这正好和实体键「不亮」那态的 PL1=10 对上——10W 是 OEM 自己允许的下限，不是我们猜的。
  以后任何写入都拿这组数当护栏。
- **OEM 自己就是这么写功耗墙的**：`Fan/Control {"Action":"SET_OPERATING_MODE_DETAIL","PL1":"75","PL2":"75","PL4":"75"}`。
  也就是说改 PL1 的正路是走 GCUBridge，而不是裸写 `PL1_SETTING_VALUE`
  （后者实测写完自清零，见 6.2）。这条要做可逆验证之后才允许面板用。

顺手排掉一条线索：用户给的官方主板驱动包（`01-Chipset`）是 **Intel Chipset Device Software**，
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
用户的操作顺序是：**Win 锁连点三次**（起始是未锁），然后**电源模式点「平衡 → 健康 → 长效」**。

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
  输出偏好）。旁证来自用户给的 Hackintosh 工程（见下），dGPU 的电源是挂在 ACPI 的
  `\_SB.PCI0.PEG0.PEGP._OFF/_ON` 上的，跟这个字段不是一回事。没验证前两个值都报出来。

顺带核了用户给的另一个资料：`UmiPro3-Hackintosh`（OpenCore 0.8.1 工程）。
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
| `do_POST` **完全不校验来源** | 面板只绑 127.0.0.1 挡不住 CSRF：用户浏览器里随便一个网页都能往 `http://127.0.0.1:8747/api/action` 发 POST，下发 OEM 命令，或者 `/api/shutdown` 把面板关掉 | `is_local_same_origin(headers)`：带了 Origin/Referer 就必须与本次 Host 完全一致；Host 本身必须是回环地址（顺带挡 DNS rebinding）；不满足直接 403 |

来源闸对**脚本零影响**：curl 和我们自己的工具都不发 Origin/Referer，照常放行
（这也是 `main.py --stop` 还能用的原因）。真正的浏览器跨站请求 Origin 必然是对方站点，一律挡掉。

教训是这条：白名单管的是「**这个命令 OEM 认不认**」，配置开关管的是「**这台机器允不允许写**」，
来源校验管的是「**这个请求是不是本机面板发来的**」——三件事，谁也不能替谁。
以后新增任何 POST 接口，先问这三个问题，别等写完再补。

**顺带把第一次可逆写验证做完了**（本来是下一步计划，被这次自查提前触发）：
用 curl 验闸门时误把 `WINKEY_UNLOCK` 当探针发了出去，而本机 `allow_write` 是**开着**的，
命令真的下去了——面板日志 01:43:16 记下 `ADDR_STAUTS_BYTE: 1 → 0`，
也就是说用户原本锁着的 Win 键被解开了；随后下发 `WINKEY_LOCK`，EC 回读 1、
`Setting/Status` 回读 `WINKEY_STATUS_LOCK`，两条通道一致，状态已还原。
这一次意外等于把 6.3 第 2 条要求的整条链走通了：**MQTT 下发 → GCUService →
EC 寄存器变化 → 两条通道回读一致 → 写回还原**，而且证明写入到 EC 生效有约 6 秒延迟。
据此 `winkey.write` 从 unknown 升为 **verified**，面板「OEM 开关状态」卡片
第一次出现了按钮（只有这一个）。教训也记下来了：**验闸门只能用不在白名单里的假动作名**，
拿真命令当探针，等于拿用户的硬件当探针。

### 6.10 拿外部逆向资料交叉核对，并且当场只读复测了一遍（2026-09-30）

用户提示"多找找 umi pro 3 bios 等关键字"之后做了一轮外部资料检索。**GM5MG0Y 本板的
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

### 6.11 完整复现 GM7MG7P 的逆向资料：对上了身份，也推翻了我们三条结论（2026-09-30）

用户找到 [`ElDavoo/tongfang-gm7mg7p-re`](https://github.com/ElDavoo/tongfang-gm7mg7p-re)
（泰坦 X8 Pro / GM7MG7P，i7-10875H + RTX 3070，同方代工），说"很有价值，如果可以，
完整复现吧"，并明确"不要刷固件，我是想你或许能逆向"。于是把它整份拉下来只做离线阅读
（7906 个文件：EC 侧 2710 个反编译函数、45481/45624 条指令通过重新汇编校验，
Windows 侧 375 个解密后的 C# 文件），**没有执行仓库里任何脚本、没有反汇编、
没有碰机器**。

结论最初分四块（一到四），之后同一轮里又追加到十四块：五是两件**没做**的事，
六是当场只读复核，七是一处未解释的新观察，八是把本机活着的风扇表转储出来，
九到十一是客服 ROM 那条线（固件卷、五份 EC 镜像、字节模式法失败），
**十五是本轮最值钱的一块**——用户一句"面板切性能模式没反应、按实体键才拉得起来"
直接推翻本项目写了最久的一条核心结论：`0x0751` 不是硬件模式总开关，
**它是服务的输出**，裸写它只改键盘指示灯、改不动功耗墙（本机往返实测见其 ②）。
**十二和十三是前两轮最有价值的两块**——十二从本机 DSDT 里挖出了厂商命名的 EC 寄存器全表
（并**推翻了我们自己一条阴性结论**，标题里"三条"的第三条就是它），
十三是参考仓库 `registers.yaml` 对六个悬案的回答，其中一个直接改掉了代码。
**十四是给十二补的实测**：先从注册表只读导出本机活着的 DSDT，证明那张名字表
逐字段就是本机的（193/193 相同、0 处差异），再拿这条通道去读，
结果 `0x0Exx` 整段没有数据——十二节那 80 个新地址里有一大半**用不了**，
#37 因此缩了范围；同一轮里 #36 和 #38 都关掉了。

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
  **三个写者共用一个字节**，所以 6.3 第 8 条把它列入禁写清单。

这条给了一個新可能：灯效可以在**纯用户态**做（枚举 HID → `SET_REPORT`），
不碰 EC、不碰驱动、天然可逆。但它是新的写通道，要按 6.3 第 2 条重新走一遍验证，
本轮没做。

**实测补充：只读枚举了本机 18 个 HID 接口，上面那段里的两处推断现在改成实测（2026-09-30 深夜）**

`tools/hid_light_probe.py`（只读，可以和面板同时跑，不需要管理员）实测结果：

- **每个 ITE 设备暴露的是两个集合，不是一个。** 上面写"两个 HID 接口"是照着文档说的，
  实际上 `048D:CE00` 和 `048D:6005` 各自都有两个集合：

  | 设备 | 接口 | usage page | Feature 长度 | 认作 |
  |------|------|-----------|-------------|------|
  | `048D:CE00` | `MI_00` | `0xFF89` usage 16 | **17** 字节 | 认不出来，不碰 |
  | `048D:CE00` | `MI_01` | `0xFF12` usage 1 | 9 字节 | 键盘四区背光 |
  | `048D:6005` | `MI_00` | `0xFF89` usage 16 | **17** 字节 | 认不出来，不碰 |
  | `048D:6005` | `MI_01` | `0xFF03` usage 1 | 9 字节 | USB 灯条 |

  所以挑选目标**必须用 (VID, PID, usage page) 三元组**，不能只用 VID/PID：
  只用 VID/PID 会先撞上 `MI_00` 那个 17 字节的集合，然后把 9 字节的报告发进去——
  发到语义未知的接口上，后果未知。这条不是洁癖，是防错。代码里 `TARGET_BY_TRIPLE`
  就是这么做的，`tests/test_hid_light.py` 的 fixture 故意把 `MI_00` 排在前面钉住它。
- **9 字节这个长度不再是推断，是设备自己报的。** `HidP_GetCaps` 在两个灯效集合上都返回
  `FeatureReportByteLength == 9`，和协议假设对上了。此前它只靠 Linux 驱动和文档佐证。
- **两个 `0xFF89` / 17 字节的集合仍然认不出来**，登记在 `detail['other_collections']` 里但
  永不打开句柄。按 6.3 第 1 条，没有 OEM 代码佐证的接口不探测。
- **厂商那五条样例报告逐字节复现成功**（只打印、不发）：`LEDKB` 两条、`USBLB` 三条。
  顺带对上了编码——`0x32` = 50 = 亮度最高档，`0x05` = 速度第 3 档，`0x24` = 36 = 亮度第 4 档。
  编码器实现见 `app/act/channels/hid_ite8291.py`，只做了编码，**没有发送函数**。
- **可逆性这半边现在有厂商自己的证据了**：第九节 ②-补 里那两条 BIOS 命令
  （`LEDKB /GetStatus` 与 `USBLB /GetMode`）说明两个灯设备都能读回当前设置，
  所以"存原值 → 写 → 写回"是厂商认可的做法，不是我们发明的。真正下发仍然要用户在场。

#### 五、两件**没做**的事，以及为什么

- **没有解密我们本机的 `GCUService.exe`**。他们的方法是**内存转储**而非静态破解
  ConfuserEx：服务运行时 `<Module>.cctor` 已把 IL 解密，直接 `ReadProcessMemory`
  读进程镜像，再用磁盘文件当模板把 IAT/reloc/CLI header/metadata 补回去
  （anti-dump 会抹掉 `BSJB` 和流名）。校验方式是数 invalid body：
  磁盘 3759 个 → dump 后 **0** 个。**但这需要管理员权限（SeDebugPrivilege）**，
  用户不在场，不擅自提权。命令已经抄在下面，等用户点头再跑：

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

⚠️ 但这只是**最可能的解释，没有证实**：本机自己的 EC 固件至今没有独立取过，
客服 ROM 里那五份都不是本板的（见第十节），所以"EC 自己不会跟着改"这个前提
我们**没有证据**。要证实得在服务的 profile 不动的情况下单独改档位字节看 PL——
那是一次写操作，得等用户在场。

另外顺手记两个从这次快照里读到、**尚未采信**的数：
`ADDR_EC_BT1CycleCount` = **83 次循环**（按他们的降额表，150 次以下年龄降额为 0，
所以本机三档的差异应当是**可观测**的，这点和"电池老了就看不出差别"的顾虑相反）；
`defaults` 组读出 GAMING PL1/PL2/PL4 = 60/60/165、OFFICE = 35/35/165、
BATTERYSAVER PL1/PL2 = 75/75 ——~~**这组数先别用**~~
（**2026-09-30 深夜已解开，见 6.11 十四 ⑤(3)**：那个"BATTERYSAVER 的 PL1 反而比 GAMING 高"
的悖论，在把 `0x07A7-0x07AA` 这块**改叫 Turbo** 之后直接消失——
35(Office) < 60(Gaming) < 75(Turbo)，单调合理；而且本机在 `Turbo_Mode(0x10)` 下
实测 `PL1 = 75` 正好等于这块的值。所以**数可以用，名字用 Turbo，别用 BATTERYSAVER**。
当时怀疑"名字错位"是对的，只是错的是名字不是数）。

**②-补：那张常量表里还有一组我们一直没看过的东西——每档一个 TCC 偏移（2026-09-30 深夜）**

顺着固件里翻到的一个立即数回头看常量表，发现 `defaults` 家族其实是**两组**，
我们只用了 PL 那一组：

| 组 | 寄存器 | 本机实测 |
| --- | --- | --- |
| PL 默认值（4 个一档 × 3 档） | `0x0730-0x0733` GAMING、`0x0734-0x0737` OFFICE、`0x07A7-0x07AA` BATTERYSAVER，各为 PL1/PL2/PL4/D | 见上（60/60/165、35/35/165、75/75） |
| **TCC 偏移默认值（1 个一档 × 3 档）** | `0x07D8` GAMING、`0x07D9` OFFICE、`0x07DA` TURBO | **三个都读出 5** |

- **读通路早就通了**，不是新加的：`app/act/channels/ec_gpd.py` 的 `defaults` 组里
  这三个名字一直在（第 84-85 行），面板每轮都在读，`/api/hardware` 的
  `state.ec_raw.defaults` 里就有。此前只是没人看那三行。
- **两组的名字对不齐**：PL 那组是 GAMING / OFFICE / **BATTERYSAVER**，
  TCC 这组是 GAMING / OFFICE / **TURBO**。同一份常量表里两种命名并存，
  这正好是上面那句"命名有冲突"的实证——所以**这三个地址叫什么名字，可信度不高**。
- **值本身是可信的**（ECREAD 读出来的就是 5），但"5 是什么单位"没证实。
  如果按 Intel 的 TCC offset 语义（节流点 = TjMax − offset），10875H 的 TjMax=100 °C，
  那节流点就是 **95 °C**。⚠️ 这一步是**推断，没证实**：EC 里存着这个默认值，
  不等于有谁把它写进了 `MSR_IA32_TEMPERATURE_TARGET`。
  **但如果成立，我们自己的温度保险就有一条是摆设**：`tools/ec_mode_bench.py` 用的是
  **97 °C** 保险，比 95 °C 的硬件节流点还高，也就是说硬件会先动手、软件那条永远不触发。
  这不算危险（硬件保护在前），但意味着那个数字该往下挪。改之前要先证实 5 的单位，
  办法是只读的：跑满载看 CPU 到底稳在多少度（`sense/thermal.py` 已经在采）。
- **三档同为 5，所以 TCC 偏移不是本机区分档位的旋钮**。它和 §2 那条
  "四档跑分只差 2.6%" 是一致的：PL 默认值有 35/60/75 的差异，热节流点没有。

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
那需要一次档位写入，按 6.3 第 2 条等用户在场再做，本轮没做。

**这一节的东西已经进面板了**：`GET /api/fan-curve`，只读，按钮触发，**不进 2 秒轮询**。
`EcChannel.fan_curve()` 按上面那套布局自己算地址，一轮 92 个表地址 + 4 个语义位
= **96 次读**，限速约 30 次/秒、用时 2.9 秒，结果缓存 10 秒（并发请求拿旧缓存）。
能力表因此拆成两条：`fan.curve` 读通一轮之后才置 verified，`fan.curve.write` 一直是
`blocked`（写表的可逆验证要用户在场），面板不给任何写入按钮。解码逻辑由
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
用户当初那句"用起来不太对劲"是对的；`cannot update bios with different platform name`
卡的就是这个字段——AMI 刷写时比对 SMBIOS 的产品名/主板名。同为 CML 平台、同为
13,631,488 字节、EC 侧又高度同源（6.11 第一节），所以读起来处处像，**但不能刷**。
这条从"待查"变成定案：不做刷写（6.3 第 8 条）。

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
  → **Q 键（造物者键）的行为本身是 BIOS 可配的**。这正好解释 6.4 里用户观察到的
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

**②-补：又过了一遍命令面，捞到四条对 #29 直接有用的（2026-09-30 深夜）**

- **两个灯设备都有「读回」命令**——这是 #29 缺的那半「可逆」：
  `LEDKB /GetStatus : Display current keyboard light setting.`、
  `USBLB /GetMode : Display current USB light Bar setting.`，
  而且 USBLB 的回显是三条各 8 字节的格式串 `1AH : %02X×8` / `14H : %02X×8` / `08H : %02X×8`，
  正好一一对上它的三条写命令。**厂商自己就是「读回来 → 改 → 写回去」这么干的**，
  所以"先存一份当前设置、写完能还原成它"这条路在固件侧是走得通的，不是我们硬凑。
- **RGBKB 不是灯效引擎，就是一个静态颜色**：`/Set <6-digits>: 000000 ~ 505050`、
  `Sample1: /Set 494847 => Set Red:49 Green:48 Blue:47`、
  `Current RGB Configuration: R:%d, G:%d, B:%d`、`/Clear: Revert RGB KB default color level.`。
  BIOS 侧对"RGB 键盘"的全部想象就是**每通道 0-50 的一个颜色**；
  真正的灯效（呼吸/波浪那些）只能靠 `LEDKB /SetData` 那 8 个裸字节下去。
  → 面板上别照 Creator Center 那样列一堆灯效名，先把**亮度五档 + 单色**做对。
- **APCtrl 有 `/Clear : Revert SetApCtrl default configuration` 和 `/GetStatus`**，
  14 个功能位全部是 `1 => on / 0 => off` 的开关，取值只认 1 和 0
  （`Value of Param:/XX invalid. Value should be 1 or 0.`）。
  位序仍然**没解出来**，不猜（见待办 #35）。
- **`ColorCalibration`**：`/Set (1: Enable / 0: Disable)` + `/Get`，回显
  `Current Color Calibration Support Status: 0x%02x`。又一个"BIOS 侧存着、OS 侧读不到"的开关。

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

**④ 一件让 6.3 第 8 条写入原则更硬的事**：把导出的 25 个 OEM 模块挨个抽字符串，
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
用户当初那句"客服面对 30 系列都给同一个 bios 文件"，机制就在这儿：**一个 ROM 里塞了
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
这一步要写档位，按 6.3 第 2 条等用户在场（待办 #31）。

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

#### 十一、用字节模式找 EC 地址立即数这条路走不通——待办 #35 因此关掉（2026-09-30 深夜）

#35（APCtrl 那 14 个位的定义）原本挂着一个**便宜的前置判据**：先在
`OemApControlDxe.pe32` 里找 `8A 07` 这类 16 位 EC 地址立即数，
找到了才值得去装反汇编器，找不到就关掉。这一节记录**判据本身不成立**，
以及顺手撞见的一个真东西。

**① 判据不成立，原因是 x64 的两种编码在字节层面长得一样。**

- 粗判据（找所有 `NN 07 00 00`）在 25 个模块里命中 **285 处**。挨个看上下文，
  几乎全是 `48 8b 05 61 07 00 00` / `48 8d 0d dc 07 00 00` / `c6 85 40 07 00 00`
  这类——**RIP 相对位移**，指向模块自己 `.data` 里的全局变量，跟 EC 毫无关系。
  模块只有 2-7 KB，位移落在 0x700-0x7FF 再正常不过。
- 加一层过滤（要求前导字节是立即数类操作码）后还剩 **77 处**，仍然是假的：
  x64 里 `mod=00, rm=101` 的 ModRM 字节恰好就是 `05 0D 15 1D 25 2D 35 3D`，
  而 `81 /r` 那族的 ModRM 又正好覆盖 `B8..BF`——**我要用来区分的那两组字节，
  本身就是最常见的 RIP 相对 ModRM**。这个过滤在设计上就不可能成立。
- 还有一类纯对齐假象：`48 b8 07 00 00 00 00 00 00 00` 是 `movabs rax, 7`，
  粗判据把**操作码 `b8` 当成了地址低字节**，报出一个根本不存在的 `0x07B8`，
  而且它在多个模块里反复出现，看着特别像"证据"。
- 收紧到只认「操作码紧跟立即数」这一种形态后剩 **6 处**，人工核过：
  4 处是真的，2 处是 `40 88 bd 12 07 00 00`（`mov [rbp+0x712], al`，栈上位移）假象。

**结论：在 x64 固件上，不真反汇编就分不出 imm32 和 disp32。** 所以那个"便宜的前置判据"
不是"跑出来是阴性"，而是**根本跑不出结论**。

**② 抽出来的 PE32 里没有字符串，连符号锚点都没有。** ← **这条写错了，2026-09-30 深夜更正**

当时只看了**我们自己**用 `rom_fv_dump.py` 抽出来的那 25 个 PE32：可见字符串一共只有
`.data` / `.xdata` / 几段序言字节，第九节那套命令面帮助文本不在这些文件里。
"我们抽出来的那份没有锚点"是真的；**"没有锚点"是假的**——
锚点在参考仓库里现成放着，我当时没去翻：

| 参考仓库里有什么 | 规模 |
| --- | --- |
| `bios/decompiled/*.c` + 配套 `*.asm` | **77 个文件**，Ghidra 反编译的 C，覆盖 `OemGlobalNvsDxe` / `OemApControlDxe` / `OemHooks` / `OemKbLightDxe` / `OemUsbLightBarDxe` / `OemQkeyDxe` / `OemPowerModeDxe` / `OemOcDxe` / `Setup` / `DxeOverClock` …… |
| `bios/ifr/Setup.en-US.ifr.txt` | **2.47 MB** 的 IFR 转储：每个 Setup 问题的 question id、英文提示词、varstore 偏移 |
| `bios/decompiled/OemOcDxe.annotated.c` | 厂商无关的**人工标注版**，而且当场演示了怎么用上面两样："`Setup[0x7D7]` is question `0xEC6` in the IFR" |

也就是说：**既不需要装反汇编器，也不需要拿字符串对齐**——C 和 IFR 都有，
把 Setup 变量里某个字节偏移翻译成"这个位叫什么"的技术，参考仓库自己就跑通了一遍。

**那 #35 为什么还是关着？** 因为 `grep -i apctrl` 在那份 2.47 MB 的 IFR 里**零命中**：
APCtrl 根本不是 Setup 表单里的一个问题，它是 `OemApControlDxe` 自己那块存储。
所以锚点技术对 APCtrl 这一项**不适用**，不是"我没找到工具"，是"工具在这儿没有靶子"。
（顺带一个反面发现，记在这里以免以后有人照着做：`OemApControlDxe.c` 里读的是
`DAT_ff430004`…`DAT_ff43000a` 这类 **PCH MMIO**，写的是 `0x65/0x66/0x6d/0x6e` 这种小索引，
**不像**是 UEFI 变量。我原来给 #35 写的第三条理由"它存在 UEFI 变量里，所以 8(b) 永久禁止"
因此**没有我写的那么确定**。#35 仍然关着，但靠的是第一条理由——判据跑不出结论——
以及"重开它就得去碰 `0xFF430000` 的裸 MMIO，那是 6.3 第 1 条管的地方"。）

**③ 但撞见一个真的：`0x07D8` 作为函数实参出现在两个模块里。**

收紧后那 4 处真命中，全部是 `0x07D8`：

| 模块 | 字节 | 解码 |
| --- | --- | --- |
| `OemGlobalNvsDxe` | `48 c7 45 b8 d8 07 00 00` | `mov qword [rbp-0x48], 0x7D8` |
| `OemGlobalNvsDxe` | `b9 d8 07 00 00 e8 …` | `mov ecx, 0x7D8; call` ← 第 1 个实参 |
| `OemGlobalNvsDxe` | `ba d8 07 00 00 48 8b cb ff …` | `mov edx, 0x7D8; mov rcx, rbx; call` ← 第 2 个实参 |
| `OemHooks` | `41 b9 d8 07 00 00 ff 50 58` | `mov r9d, 0x7D8; call [rax+0x58]` ← 第 4 个实参 |

而 `0x07D8` = 2008 = 常量表里的 `ADDR_GAMING_TCC_OFFSET_DEFAULT_VALUE`（见第七节②-补）。

⚠️ **这个巧合不采信**，因为有两种读法分不开：
（甲）它确实是 EC 地址 `0x07D8`，固件把它读出来发布进 ACPI GlobalNvs；
（乙）它是 **GlobalNvs 区里的字节偏移**（2008 字节的 NVS 区完全说得通），
跟 EC 一点关系没有——注意这两处都在 `OemGlobalNvsDxe` 里，(乙) 一点都不牵强。
分清要反汇编 + 认协议 GUID，两样都没有。**所以只记"有这么个巧合"，不写进结论。**

**③-补 分清了，而且(甲)(乙)都不对——它是 `sizeof(SETUP_DATA)`。（2026-09-30 深夜）**

按 ② 的更正去读参考仓库的反编译 C，三处证据同向，不需要推：

1. **参考仓库自己标注了。** `bios/decompiled/OemOcDxe.annotated.c` 开头写着
   "CpuSetup (0x2BB bytes) and **Setup (0x7D8 bytes)**: `bios/ifr/Setup.en-US.ifr.txt`"，
   正文里写着 `SETUP_DATA Setup; /* 0x7D8, gSetupGuid ec87d643-... */`。
2. **GUID 对得上。** `OemGlobalNvsDxe.c:292-295` 就在 `0x7d8` 上面四行拼出
   `ec87d643 / 4bb5eba4 / 3e3fe5a1 / a90db236`——正是那个 `gSetupGuid`。
3. **调用形态对得上，而且是标准写法。** `OemGlobalNvsDxe.c:355` 是
   `table[?](u_Setup, &guid, 0, &local_60, lVar2)`，五参数，第四个是**传进去又被改写**的
   `local_60`（299 行刚赋成 `0x7d8`），紧接着 356 行判
   `lVar3 == -0x7ffffffffffffffb` = **`EFI_BUFFER_TOO_SMALL`**，
   然后 360 行 `FUN_00000d60(local_60)` 按真实大小重新 `AllocatePool`、362 行重试。
   这就是 UEFI 里"先拿一次尺寸、再分配、再取一次"的教科书写法，
   `0x7d8` 站在 **DataSize** 那个位置上。

`0x7d8` 作为尺寸出现在 **11 个模块**里（`Setup` / `OemHooks` / `OemHooksPei` /
`OemOcDxe` / `OemKbLightDxe` / `OemUsbLightBarDxe` / `OemDisplayModeDxe` /
`OemNetworkDxe` / `OemPowerModeDxe` / `OemQkeyDxe` / `DxeOverClock`），形态一致。
我表格里那四行也就有了准确说法：`mov r9d, 0x7d8` 那个"**第 4 个实参**"，
第 4 个实参就是 `GetVariable` 的 `DataSize`。

**结论：紧判据那 4 处"真命中"，真命中数是 0。** 字节模式法在 x64 固件上
不但分不出 imm32 和 disp32，连**唯一那组看着像 EC 地址的立即数**也是结构体尺寸。
这条路的阴性结果比我原来写的更干净——原来我还留了个"万一(甲)呢"的尾巴，现在没有了。

顺带一个**不能当收益用**的副产品：`OemGlobalNvsDxe.c:369-371` 把
`Setup[0x744] / Setup[0x745] / Setup[0x4A0]` 三个字节拷进它发布的 GlobalNvs，
`FUN_000004b8` 则以 `EfiACPIMemoryNVS` 分配 **0x112 = 274 字节**。
所以那块 NVS 是 274 字节，`0x7d8` 连"(乙) NVS 区里的偏移"都不是——
它就是 Setup 变量的长度。这三个偏移各自是什么问题，IFR 里有答案，
但 6.3 第 8 条(b) **永久禁止读写任何 UEFI 变量**，所以只记形状，不去查名字。

**④ `OemApControlDxe` 里一处真 EC 立即数都没有**：它在第二层过滤下命中的 5 处
全是 `48 8b 05 xx 07 00 00` 形态，即 RIP 相对取自己模块的全局变量。
这本身也不能当证据（地址可能来自 `.data` 里的表、或者是算出来的），
但它和 ①②③ 合起来，#35 的三条理由凑齐了：
判据跑不出结论、没有符号锚点、而且**就算解出位定义也用不上**——
APCtrl 存在 UEFI 变量里，6.3 第 8 条(b) 永久禁止读写任何 UEFI 变量。
唯一可能的收益（"那个 WORD 也许是 EC `SUPPORT_BYTE` 家族 `0x078A-0x078F` 的另一个视图"）
现在也有了反面线索：紧判据下没有任何模块引用 `0x078A-0x078F`。

**④-补 三条理由的当前状态（2026-09-30 深夜按 ②/③-补 复核后）：**

| 原来的理由 | 现在 |
| --- | --- |
| ① 判据跑不出结论（imm32 与 disp32 在字节层面同形） | **成立**，一字不改。这是 #35 关着的**唯一硬理由**。 |
| ② 没有符号锚点 | **半错**。锚点有（77 个反编译 C + 2.47 MB IFR），但对 APCtrl 无靶：IFR 里 `grep -i apctrl` 零命中。改成"锚点技术在这一项上没有对象"。 |
| ③ 解出来也用不上（APCtrl 在 UEFI 变量里 → 8(b)） | **不确定**。`OemApControlDxe.c` 读的是 `0xFF430004-0x0A` 的 PCH MMIO，不像 UEFI 变量。**不要再拿 8(b) 当这一条的依据。** |

所以 #35 的关闭理由从三条收缩成一条半：判据跑不出结论（硬），
加上"重开就得碰 `0xFF430000` 的裸 MMIO，那是第 1 条管的"（也够硬，但和原来写的不是一回事）。
**③-补 那个副产品反而把它钉得更死**：连唯一一组像 EC 地址的立即数都是结构体尺寸，
说明这个模块压根不在 EC 地址空间里办事。
`0x078A-0x078F` 没有任何模块引用这一条**继续成立**，未受影响。

**⑤ 没做的事**：没装反汇编器（没有 pip，也犯不上为一条用不上的知识引入新依赖）、
没执行任何模块代码、没写 EC、没碰 UEFI 变量。扫描脚本 `tools/out/apctrl_ec_imm.py`
留在 gitignore 里作记录——**它给出的那 285/77 两个数字是错的，别再用**。

**⑤-补**：更正 ②③④ 时**也没做**上面任何一件——只读了参考仓库已经提交好的
`.c` / `.ifr.txt` 文本，没跑 Ghidra、没装依赖、没读 `Setup` 变量本身、
没碰 `0xFF430000`。

#### 十二、**更正一条自己的阴性结论**：本机 DSDT 里有厂商命名的 EC 寄存器全表（`ECMG`，98 个字段）（2026-09-30 深夜）

**先说错的那条。** 我早前在 DSDT 上扫过一遍 OperationRegion，结论是：

> "这份 DSDT 的 EC 是邮箱式的（`CMDL`/`CMDH`/`DRDY`/`LDAT`/`HDAT` 那一套），
> 不是命名字段表；ACPI 侧没有名字可挖。"

**这是错的，而且错得很具体：我把 `opregions()` 的结果按 `space == 3` 过滤了。**
`space == 3` 是 `EmbeddedControl`。厂商把命名寄存器表放在一个
**`SystemMemory`** 区域里（`space == 0`），于是被我的过滤器整块丢掉，
剩下的就只有那三个 EmbeddedControl 区——看上去当然"只有邮箱"。

**实际有三个 EC 视图，不是一个。** DSDT 是 `tools/out/dsdt_mro06.aml`，
248,178 字节（去掉 36 字节 ACPI 头后 AML 体 248,142 字节），
里面有 **1580 个 MethodOp、635 个 Scope、214 个 Device、195 个 OperationRegion**。
挂在设备 `EC0_` 下面的 EC 相关区域是：

| 区域 | AML 偏移 | 空间 | 基址/长度 | 内容 |
| --- | --- | --- | --- | --- |
| **`ECMG`** | `0x03B1EF` | **SystemMemory** | `0xFE410000`, `0x10000` | **1 个 Field、flags `0x00`、98 个命名字段，偏移就是厂商 EC 扩展地址 `0x043E`…`0x0ECF`** |
| `ECMP` | `0x03B445` | EmbeddedControl | `0`, `255` | 标准 EC 空间 `0x00-0xFF`，1 个字段 |
| `ECXP` | `0x03B45F` | EmbeddedControl | `0`, `255` | 标准 EC 空间 `0x00-0xFF`，94 个字段 |

**`ECMG` 就是我们要找的东西：厂商自己在 ACPI 里给出的 EC 寄存器命名表。**
98 个字段里，**18 个落在本机 Creator Center 常量表也有的地址上，80 个是我们此前完全没有名字的**。

**① 那 18 个重合地址——这是让另外 80 个可用的理由。**

单看一份**别的机型**的 DSDT，字段名不能直接搬。但重合的这 18 个是**本机自己的常量表**
和它逐字节对上的，对上的地方语义一致，所以对不上的那 80 个才有资格按"同源、参考级"采信：

> **⚠️ 这个"理由"在第十四节被实测削掉了一大半，先看那条再往下读。**
> 两件事：
> ① **好消息**——本轮从 `HKLM\HARDWARE\ACPI\DSDT\ALASKA\A_M_I_\01072009` 只读导出了
> 本机**活着的** DSDT，与这份 ROM 表逐字段对比：193/193 同名同址同位宽、0 处差异。
> 所以"别的机型"这个前提本身可以撤掉，**这张名字表就是本机的**（第十四节 ①）。
> ② **坏消息**——名字对不等于读得到。拿这条通道实测，`0x0Exx` 整段**没有数据**
> （`CPUT` 在 CPU 47.1 °C 时读 `0x00`），而 `0x04xx-0x08xx`/`0x0Fxx` 全部正确。
> 所以下面 ② 里那七个温度点、`F1SH/F1SL`、`F1DC/F1CM`、`F2DC/F2CM`、
> `CCI/CTL/MGI/MGO` 一整块**在本机不可用，不许接进代码**（第十四节 ②）。
> **"18 个重合 ⇒ 另外 80 个可采信"这个推理是错的**：重合的 18 个全在 `0x04xx-0x08xx`，
> 它们能背书的是"名字来源可信"，**背书不了"地址在本机这条通道上有数据"**——
> 后者只能一个个读，而读过之后是"一半能用、一半是空的"。

| EC 地址 | AML 字段名（位宽） | 本机常量表里的名字 |
| --- | --- | --- |
| `0x0743` | `GNEN`(1) + `ECDC`(1) | `ADDR_ConfigurableTGP_DynamicBoost_CTRL_BYTE`、`ADDR_MYFAN2_L1_PWM` |
| `0x0744` | `CTVA`(8) | `ADDR_ConfigurableTGP_VALUE`、`ADDR_MYFAN2_L2_PWM` |
| `0x0745` | `DBCT`(8) | `ADDR_DynamicBoost_TotalProcessingPowerTarget_VALUE`、`ADDR_MYFAN2_L3_PWM` |
| `0x0746` | `MXDB`(8) | `ADDR_DynamicBoost_MaxinumTGP_VALUE`、`ADDR_MYFAN2_L4_PWM` |
| `0x0747` | `MIDB`(8) | `ADDR_MYFAN2_L5_PWM` |
| `0x074C` | `PDIN`(4) | `ADDR_OEMSERVICE_PROJECT_ID_BYTE` |
| `0x0783` | `APL1`(8) | `ADDR_PL1_SETTING_VALUE` |
| `0x0784` | `APL2`(8) | `ADDR_PL2_SETTING_VALUE` |
| `0x0785` | `APL4`(8) | `ADDR_PL4_SETTING_VALUE` |
| **`0x0786`** | **`APTC`(7) + `APTN`(1)** | `ADDR_L1_PWM_DEFAULT_MYFAN3` ← **冲突，见 ③** |
| `0x0788` | `CTWA`(8) | `ADDR_L3_PWM_DEFAULT_MYFAN3` |
| `0x07A4` | `GC6S`(1) | `ADDR_AP_BIOS_BYTE` |
| `0x07C5` | `WHMS`(1) | `ADDR_AP_OEM_BYTE5` |
| `0x07C6` | `WMS0`(2) | `ADDR_AP_OEM_BYTE6` |
| **`0x07D0`** | **`DBD1`(8)** | `ADDR_BATTERY_CHARGE_LIMIT_DOWN` ← **冲突，见 ③** |
| `0x07D3` | `GFID`(3) | `ADDR_ModuleID` |

**② 新的那 80 个地址里，最有用的一批**（全部**参考级、未在本机验证**）：

- **温度传感器**：`CPUT`(0x0E0D CPU)、`PCHT`(0x0E0E PCH)、`SN1T`…`SN5T`
  (0x0E10/0x0E12/0x0E14/0x0E16/0x0E18)。**七个温度点的地址，一次到位。**
- **风扇**：`FFAN`(0x0460, 4bit)、`SDAN`(0x0468, 4bit)、`F1SH`/`F1SL`(0x0E1C/0x0E1D，
  风扇 1 转速高/低字节)、`F1DC`(7bit)+`F1CM`(1bit)@0x0E8C、`F2DC`(7bit)+`F2CM`(1bit)@0x0E9D。
  后两个是"7 位占空比 + 1 位模式"的复合字节，是 #31 手动占空比的候选靶。
- **GPU 功率家族**：`PMAX`(0x07B3, 16bit)、`PBSS`(0x07B5, 16bit)、`PSRC`(0x07B7)、
  `VBNL`(0x07BA, 16bit)、`RBHF`(0x07BC, 16bit)、`CMPP`(0x07BE, 16bit)、
  `DBEN`(1)+`DBST`(1)@0x07C4、`DBD1`(0x07D0)、`DBD2`(0x07D1)、`DBAP`(0x07D5)、
  `DBSP`(0x07D6)、`CPUA`(0x07D4)、`CGCT`(0x07D7)。
- **MyFanCCI 那一组**：`CCI0-CCI3`(0x0EA4-0x0EA7)、`CTL0-CTL7`(0x0EA8-0x0EAF)、
  `MGI0-MGIF`(0x0EB0-0x0EBF)、`MGO0-MGOF`(0x0EC0-0x0ECF)。
- 其余：`CPTM`(0x043E)、`VGAT`(0x044F)、`DTTF`(0x07B8)、`AP01`/`AP02`/`AP10`
  (0x07C0/0x07C1/0x07C2)、`UVER`(0x0EA0, 16bit)、`RESV`(0x0EA2, 16bit)。

⚠️ **`0x07B8 = DTTF` 是真的 ECMG 字段**，和第十一节 ① 里那个
"`48 b8 07 00 00 00 00 00 00 00` 是 `movabs rax,7`、被误当成地址 `0x07B8`"的
**对齐假象毫无关系**。那个假象的结论不变，但从此不许拿 `DTTF` 去给它翻案。

**③ 三个长期命名冲突，就此定案。** 判据是**优先采信互相印证的一对来源**：
DSDT 字段名 + 厂商 Windows 服务的行为，压过 ECSpec 里 MyFan 世代的旧名字
（旧名字在同一段地址上已经被证明过期——`0x0743-0x0747` 被 ECSpec 叫
`MYFAN2_L1~L5_PWM`，被 DSDT 叫 Configurable TGP / Dynamic Boost 那一组）。

1. **`0x0786` = TCC 偏移**，不是 `ADDR_L1_PWM_DEFAULT_MYFAN3`。
   DSDT 拆成 `APTC`(bit0-6, 偏移 °C) + `APTN`(bit7, 使能)；厂商服务的
   `SetCpuTccOffset` 也按这个语义写（使能写 `offset|0x80`，不使能写 `0`）。详见第十三节。
2. **`0x0743-0x0747` = Configurable TGP / Dynamic Boost**，不是 `MYFAN2_L1~L5_PWM`。
3. **`0x07D0` = `DBD1`，GPU Dynamic Boost 的字节**，不是 `ADDR_BATTERY_CHARGE_LIMIT_DOWN`。
   **这正是 6.3 第 8 条(d) 禁止写它的原因**，现在从"参考仓库标了
   `DO-NOT-WRITE-BLIND`"升级成"我们知道它是谁在写、写什么"。

**④ `T1WR`：从我们自己的 ROM 里解出来的命令分发器，它把 8(d) 钉死了。**

`T1WR` 是本机 DSDT 里的一个 Method，881 字节，20 个比较分支。命令号集合：
`0x81 0x82 0x83 0x84 0x85 0x86 0x87 0x1171 0x1172 0x1173 0x2273 0x1175 0x1176`
（扫描器另外报了 `0x71/0x73/0x74/0x75/0x76/0x61`，**当扫描假象处理，不采信**）。
`0x83` 的分支体是**空的**——显式 no-op。手工解出来的两条：

```
T1WR(0x1171, Arg1):
    NPCFCTGP = 1; Local0 = 0; EC0_.CTWA = Arg1;
    Local0 = CTWA * 8; NPCFUOCT = Local0; Notify(NPCF, 0xC0)

T1WR(0x1173, Arg1, Arg2):
    NPCFDBAC = 0; Local0 = 0; Local1 = 0;
    Local0 = Arg1 * 8; Local1 = Arg2 * 8;
    EC0_.DBD1 = Local0; EC0_.DBD2 = Local1;
    NPCFAMAT = Local0; NPCFAMIT = Local1; Notify(NPCF, 0xC0)
```

`CTWA` = `0x0788`，`DBD1` = **`0x07D0`**，`DBD2` = `0x07D1`。所以
**`T1WR 0x1173` 把 GPU 功率 `Arg1*8` 写进 `0x07D0`**——三个独立来源现在同向：
参考仓库的 `DO-NOT-WRITE-BLIND` 标注、我们自己的全表差分、以及**本机 ROM 里的这段 AML**。

**⑤ #27 的踩雷路径有名有姓了。** `T1WR 0x81/0x82/0x84` 写的是
`EC0_.APL1/APL2/APL4` = EC **`0x0783/0x0784/0x0785`** =
本机常量表的 `ADDR_PL1/PL2/PL4_SETTING_VALUE`。
**也就是说 DSDT/DPTF 那条路本身就是 PL 字节的活跃写者**：
我们写进去的 PL 随时可能被 ACPI 侧覆盖。这不是"可能"，是 ROM 里明写着的三条分支。
#27 在验证通过之前不许进白名单，这一条是它的主要理由之一。

**顺带冒出一个新假设，明确标成假设，别当结论用。** 6.2 表里那条老观察是：
"写 `ADDR_PL1_SETTING_VALUE=35`，回读为 35，但**1 秒内自清 0**"，
当时归因于"`0x783-0x785` 是 MyFan3 一代机型的落点，本机是 CML 平台，写了不认"。
第二节又把 EC 侧的解释排掉了（EC 主镜像里这三个字节各只有 1 个写点，且被 `0x0741` bit0
门控，本机 bit0=1，那条分支不该跑）。
**现在多了第三个候选写者：DSDT。** 如果那次自清是 ACPI/DPTF 侧干的，
"平台不认这组地址"这个说法就未必成立——回读能读到 35，本身就说明地址是通的。
**为什么不顺着查下去**：分辨三者要再做一次受控写入并同步抓 DPTF 事件，
那是 6.3 第 2 条管的硬件写入，得用户在场；而且它不改变任何现有行为——
不管自清是谁干的，结论都是"**别裸写 PL**"。所以只记假设，不排期。

**⑥ 顺手否掉两件事。**

- **`ECRR`/`ECRW` 不是命令邮箱，是 MMIO 别名。**
  `ECRR(Arg0)` = `Add(0xFE410000, Arg0, Local0); Local1 = \_SB.PCI0.LPCB.MMRW; Return(Local1)`；
  `ECRW(Arg0, Arg1)` = `Store(MMRW, Local0); Local1 = Arg0; Store(Arg1, Local0)`。
  两者都在 `ECMG` 那个 `0xFE410000` 窗口上算地址。`INOU` 和 `FAN` 是 **Name 不是 Method**
  （方法命中数都是 0）。
- **DSDT 里那 4 处 `0x07D8` 形态的地址立即数，一个都不是 EC 地址。**
  全部是 `Store(0x07D9, OSYS)`（紧跟 ASCII `"2009\0"`）、`SPPS` 里的 `Stall(100)` 计时常量、
  `BRTN` 里一个叫 `"DIDX"` 的缓冲、以及 `SX` 里一段无关常量。
  按概率算本来就该这样：540 个常量 / 123 个不同取值落在 `0x0100-0x1FFF`，
  撞上 1.6 个是期望值。**别把这 4 处当证据用。**

**⑦ ⚠️ 地址空间撞车，写代码前必看。**

| 写法 | 是什么 | 能不能碰 |
| --- | --- | --- |
| EC 标准空间字节 **`0x7B`**（`ECMP` 里叫 `DEVS`） | 标准 ACPI EC 的第 123 字节 | 只读 |
| EC 扩展空间 **`0x07B0`** | 完全不同的地址 | — |
| EC 扩展空间 **`0x0770`** | **充电门控字节** | **永久禁止，6.3 第 8 条(a)** |
| EC 标准空间 `0x7B` 在 `ECMP` 里的样子 | `ReservedField 984 bits`（=123 字节）后跟 `DEVS 8 bits` | — |

三个数字长得像，落在**两个不同的地址空间**里，其中一个是禁写清单。
`ECMP`+`ECXP` 合起来给标准空间 `0x00-0xFF` 命名了 **95 个字段**
（`XIF0-XIFC`、`XST0-3`、`BLLV`、`XHPP`、`TCOS`@0x63、`TURB`@0x66、
`PL1L`@0x6A、`PL2L`@0x6B、`PL3L`@0x6E、`PL4L`@0x6F、`BRTS`@0x79、
`TOPD/WUSB/FGPT/WEBC/BLTH/DV3G/WLAN` **七个名字全挤在 0x7B**、
`LDAT`@0x8A、`HDAT`@0x8B、`RFLG/WFLG/BFLG/CFLG/DRDY`@0x8C、`CMDL`@0x8D、`CMDH`@0x8E、
`CYCN`、`BIF0-BIFC`、`BST0-3`、`ACIN/BTIN` ……）。
**`0x7B` 一个字节挂了七个名字**，这件事本身就说明标准空间的位定义有多挤，
动它之前必须逐位确认，不能按名字猜。

**⑧ 解码规则（这次是内部自洽推出来的，不是背的，记下来免得下次重推）。**

- `PkgLength` **把自己那几个长度字节算进去**，并且**不含 Else 分支**。
  在 `T1WR` 里验了三次：`If`@0x1F（pkglen `0x1b`=27）正好结束在 0x3B（`a1` 所在处）；
  `If`@0x3E（pkglen 5）正好结束在 0x44；`If`@0xB6（pkglen `4b 05` → `0x5B`=91）
  正好结束在 0x112。
- **`0x93` = `LEqual`**。证据是 `a0 05 93 68 0a 83 a1 4c 32` 这一串：
  **空的 then 分支**只有解释成 `If (Arg0 == 0x83) { /* 什么都不做 */ } Else { 后面整条链 }`
  才讲得通；按 `LNotEqual` 读，分支会整个反过来、变成不通的东西。
  这和 ④ 里 `0x83` 那条空分支是同一件事的两种看法。
- **`0x86` = `NotifyOp`**，两处独立印证：`Notify(NPCF, 0xC0)` 和
  `Notify(\_SB.PCI0.PEG0.PEGP, 0xC0)`——正是 NVIDIA 混合显卡那两条标准通知。
- `77 <op1> <op2> 00` = `Multiply(A,B)` 带 ZeroOp 目标，外面套 `70 … <target>` =
  `Store(Multiply(A,B), X)`。`Arg0-Arg6` = `0x68-0x6E`，`Local0-Local7` = `0x60-0x67`。

**⑨ 没做的事**：没执行任何 AML、没调用 `ECRR`/`ECRW`、没读写
`0xFE410000` 那个窗口、没碰 EC、没刷任何东西。
上面全部是对 `tools/out/dsdt_mro06.aml` 这个**已经躺在 gitignore 里的文件**做静态解码，
解码脚本（`dsdt_ecmg.py`、`dsdt_ecmg_xref.py`、`dsdt_ec_mailbox.py`、`dsdt_ec_ctx.py`、
`dsdt_ec_fields.py`、`dsdt_ec_addr.py`）按"ROM 衍生物不进仓库"的既有规矩留在 `tools/out/`。

#### 十三、参考仓库的 `registers.yaml` 回答了六个悬案，其中一个改掉了代码（2026-09-30 深夜）

第十二节的 `ECMG` 是**从本机 ROM 里挖出来的名字**；这一节是**别人在同一款 EC 上
做过实机验证的结果**，来源是 `ec/annotations/registers.yaml`（4827 行、160 条目、192 个地址）
及其配套注解。**两者都只读，都没跑硬件。**

**① `0x0751` 的编码：三方对齐，#25 的语义部分可以定了。**

`0x0751 = MANUAL_FAN_CTRL`。厂商 `MyFanManager_RamFan1p5.SetFanMode` 写的常量是
**Office `0xA0`、Gaming `0x00`、Turbo `0x10`**，外加 Fan Boost 开时置 **bit6**。
上游 `uniwill-laptop` 给的位名：`FAN_MODE_TURBO` bit4、`FAN_MODE_HIGH` bit5、
`FAN_MODE_BOOST` bit6、`FAN_MODE_USER` bit7，bit0-2 是风速档位；Office = USER|HIGH = `0xA0`。
**他们 2026-09-23 用六次 Fn 键切档做过实机验证。**

对上我们自己的三处观察，**三方一致**：

| 来源 | 结论 |
| --- | --- |
| 本机 `ec_gpd.py` 里的既有注释 | "全亮 Turbo_Mode(0x10) ↔ 不亮 User_Fan_HiMode(0xA0)，半亮 Normal_Mode(0x00)"，且 `PL1_SETTING_VALUE 75↔10` |
| 参考仓库 Fn 键实机抓包 | Office `0xA0` / Gaming `0x00` / Turbo `0x10` |
| 厂商服务 `SetFanMode` 常量 | 同上，另有 bit6 = Fan Boost |

`_derive` 里那句 `fan_boost = bool(ctl & 0x40)` 正好就是 bit6，早就写对了。

**② 但"写这个字节就能切档"是假的——他们的隔离实验证明了这点，和我们第二节的结论撞在一起。**

他们**只写 `0x0751`**，然后看：`0x0783-0x0787` 没动、`0x07C5/0x07C6` 没动、
`0x0743-0x0746` 没动、风扇表 `0x0F00-0x0F5F` 没动，那次写入静悄悄地留在那儿，什么都没发生。
他们的原话是：**"所以一个 Linux platform profile 必须自己把 PL 和风扇表都写一遍，
从 EC 的默认块里取；光写 `0x0751` 不行。"**

**这独立印证了本节第二条（"EC 根本不会替你设 PL"）**，而且解释了第七节那个
"新观察"：我们按实体键时看到 PL 跟着变，**不是 EC 干的，是 Windows 服务干的**。

**③ `0x075B/0x075C` 是"公布出来的占空比"，不是控制口——#31 别再往那儿写。**

`MAIN_FAN_L_DUTY` / `MAIN_FAN_R_DUTY`。注解里明写着
"**do not write this expecting the fan to turn**"。占空比换算是 **值 × 2，`0xC8`(200) = 100 %**：
`FanInfo.GetEcCpuFanDuty` 返回 `Data / 2`，EC 固件自己的上限就是存在 `0xBB22` 的那个 `0xC8` 字面量。

**本机实测数据正好验算通过**：`/api/hardware` 读到 `R_DUTY = 60` → 30 %，在 ×2 约定下合理。

**④ 转速计有两个位置，冲突未解，谁都不许挑一个用。**

> **本条已在同一轮的第十四节 ③④ 结掉**：`0x0E1C/0x0E1D` 落在 `0x0Exx` 那段
> **本机读不到数据**的区间里（同段 `CPUT` 在 CPU 47 °C 时读 0），
> 而本机常量表的 `0x0464/0x0465` + `0x046C/0x046B` 与厂商服务的
> `GetEcCpuFanRpm`/`GetEcGpuFanRpm` **一字不差**、活体读数也与面板分毫不差。
> 所以不是"二选一"，是"一处可用 + 一处死的"。主风扇恒 0 也查清了：
> 占空比 0 %、CPU 47 °C 低于曲线第一个转折点 53 °C，**该停就停，不是读错地方**。
> 下面保留原文，是为了记下"当时为什么不猜"。

- 厂商 Windows 服务：`GetEcCpuFanRpm` 读 **`0x0464/0x0465`**，`GetEcGpuFanRpm` 读 **`0x046C/0x046B`**。
- 本机 DSDT 的 `ECMG`：风扇 1 转速叫 **`F1SH`/`F1SL` @ `0x0E1C/0x0E1D`**。

**两套地址，都出自厂商自己的东西。** 我们代码现在读的是常量表给的
`ADDR_EC_MAIN_FAN_RPM_BYTE1/2`。**这个冲突记在案，不解，不猜。**

顺带把**字节序**用本机数据钉死了：`ec_raw` 里第二风扇读到 `BYTE1=8, BYTE2=15`，
高字节在前 = `0x080F` = **2063 RPM**（合理）；反过来是 33033（不可能）。
`ec_gpd.py` 里那段"字节序是实测定的"注释，因此又多了一条独立证据。

**⑤ `MODE_PL_DEFAULTS`：三个档的 PL 默认块，而且第四个字节确实会被写。**

Gaming `0x0730-0x0733`、Office `0x0734-0x0737`、**Turbo `0x07A7-0x07AA`**
（"ECSpec 把最后这块叫 BATTERYSAVER，3.1.39.0 的风扇管理器按 Turbo 读它"）。
他们实机读到 `3C 3C A5 01` / `23 23 A5 01` / `4B 4B A5 01`。
仓库里有一条**已提交的更正**说：每块第四个字节（D-state 那个）**是被写的**——
EC 侧 18 处引用**全是 store**，走 `copy_code_table_into_0730_07a7`（`bank0/94D0.asm`）。

**本机数据站在"Turbo"这一边**：我们实机读到 Turbo 档 `PL1 = 75`，
而 `ADDR_BATTERYSAVER_PL1_DEFAULT_VALUE = 75`——对上了 `0x07A7` 那块。
反过来，`0xA0` 档读到 `PL1 = 10`，**和三个默认块（35 / 60 / 75）哪个都不匹配**，
说明服务是从某个 profile 槽位写的，不是从 EC 默认块读的。

**⑥ `0x0786`：TCC 偏移的单位是 °C，而且它有个使能位——这一条改掉了代码。**

先说单位（#36 问的就是这个）：`MODE_TCC_OFFSET_DEFAULTS` = `0x07D8/0x07D9/0x07DA`，
注解写明是 "**CPU TCC offset defaults (degrees C)**"，他们 2026-09-23 实机读到 `05 05 05`，
和每个 UserPofiles 槽里的 TccOffset 5 一致。**本机 `ec_raw` 也读到 5/5/5，一模一样。**
EC 侧的播种程序是 `bank0,0x9334,seed_tcc_defaults_from_ba36`。

**但"默认 5 °C ⇒ 降频点 = TjMax-5 = 95 °C"这个推论是错的**，因为
`CPU_TCC_OFFSET (APTC/APTN) = 0x0786` 带一个使能位：
厂商"用户开启 TCC 偏移时写 `offset|0x80`，否则写 `0`（每个档的默认态；
**2026-09-23 整轮抓取里它一直是 `0x00`**）"。
使能位没置，那三个 5 就不生效，降频点还是 TjMax。

**所以我把猜测换成了测量**（**这是本次唯一的代码改动**）：

- `app/act/channels/base.py`：新增 `TCC_ENABLE_BIT = 0x80`、`TCC_OFFSET_MASK = 0x7F`、
  `tcc_offset_of(raw) -> (使能位, 偏移°C)`，读不到返回 `(None, None)`，**不猜成 0**。
- `app/act/channels/ec_gpd.py`：`SLOW_GROUPS` 新增第四组
  `('tcc', ('ADDR_L1_PWM_DEFAULT_MYFAN3',))`——**60 秒一轮，没进 2 秒高频组，
  6.3 第 6 条的限速预算一点没动**；`_derive` 输出 `tcc_offset_raw` /
  `tcc_offset_enabled` / `tcc_offset_c`。
- `app/act/hardware.py`：`STATE_KEYS` 收下 `tcc_offset_enabled` / `tcc_offset_c`；
  **`tcc_offset_raw` 按 `fan_rpm_raw`/`battery_temp_raw` 的先例留在 `ec_raw` 不进 `state`**。
- `tests/test_tcc_offset.py`：**新增，8/8 通过**。除了钉解码
  （`0x00→(False,0)`、`0x80→(True,0)`、`0x85→(True,5)`、`0x05→(False,5)`、
  `0xFF→(True,127)`、`None→(None,None)`），还钉了两条**结构**：
  `0x0786` 必须在 `SLOW_GROUPS` 而**不在** `FAST_GROUPS`；派生值进 `STATE_KEYS` 而原始字节不进。
- 全套 **16 个文件全绿**（原 15 + 新增 1）。

**#36 因此改了性质**：单位问题**已经离线答完**（°C），剩下的只是
"重启面板、把这个字节读出来"，**不需要跑负载**。
`tools/ec_mode_bench.py` 的 `TEMP_ABORT = 97.0` **一个字没动**——
在读到 `tcc_offset_enabled` 之前改它就是拿推论当测量，
而这正是本节 ⑥ 刚刚否掉的那类错误。`tools/ec_write_test.py` 的 `TEMP_GUARD_C = 85.0` 同样没动。

**⑦ `0x07C6` bit2 是风扇表写入的闸门——#31 的写协议有了。**

`AP_OEM_6 = 0x07C6`。**bit2 = `ENABLE_UNIVERSAL_FAN_CTRL`：厂商在每次写风扇表之前清掉它、
写完再置回去**（`MyFanTableCtrl.SetFanControlByRamFan1p5`），
实机上表现为每次切档时一个 **1-2 秒的凹陷**。bit0-1 = DSDT 里的 `WMS0`，
回读出来是 NVIDIA Whisper Mode。静息态实机读到 `0x04`。

**#31 将来真要写风扇表，必须先清 bit2、写完置回**，否则会和厂商服务抢同一个闸门。
这条现在记在待办里，**代码没有加任何写入**。

**⑧ 作用域提醒：PD 镜像的 `0x07D8` 不是 EC 的 `0x07D8`。**

`ec/firmware/GMxMGxx_11.800` 文件 `0x20000` 处那份 ITE8850-PD 镜像
是**另一个 8051 程序**，有自己独立的 XDATA 空间。`0x07D8` 的 34 处引用里
**33 处属于 PD 镜像，EC 侧只有 1 处**。两边只共用一个数字，别的什么都不共用。
（第十一节 ③-补 那个 `sizeof(SETUP_DATA)` 是**第三个**同样数字的巧合——
同一个 `0x07D8`，在 BIOS 侧是结构体尺寸、在 PD 镜像里是另一个程序的 XDATA、
在 EC 侧才是 TCC 偏移默认值。）

**⑨ 没做的事**：没写 EC、没写 UEFI 变量、没重启用户正在跑的面板、
没跑负载（跑负载会触发一次无人值守的 EC 风扇模式写入）、没发 HID 报文、
没把厂商那条 55 % 占空比曲线抄进任何预设、没动 `TEMP_ABORT`、
没为了核对而去读参考仓库的实机证据目录之外的任何东西。
本节全部是读**已提交的文本**，加上一处**只读**的代码改动。

#### 十四、把 ECMG 那套名字拿去实测：本机活表逐字段相同，但 `0x0Exx` 整段没有数据（2026-09-30 深夜）

第十二节那张名字表是**从客服 ROM 的 DSDT 里挖的**（`tools/out/dsdt_mro06.aml`，
内部机型名 `Taitan Series GM7MG0M`），不是本机活表——而本机是 GM5MG0Y。
所以在拿它去接任何代码之前，先补两件事：**证明这张表也是本机的**，以及**证明这些地址真读得到**。
第二件事的结果是"一半读得到、一半读不到"，直接改掉了 #37 的范围。

**① 出处补正：本机活着的 DSDT 和 ROM 那份，EC 字段映射逐字段相同。**

从 `HKLM\HARDWARE\ACPI\DSDT\ALASKA\A_M_I_\01072009\00000000` 只读导出本机活表
（`tools/out/live_acpi_dump.py`，纯 `winreg` 读，不提权、不发 IOCTL、不碰设备），
再和 ROM 那份逐字段对比（`tools/out/dsdt_ecmg_live.py`）：

| 对比项 | ROM（GM7MG0M） | 本机活表（GM5MG0Y） |
|---|---|---|
| AML 体长度 | 248,142 字节 | **248,113 字节**（差 29，不是同一个 build） |
| `ECMG` 区声明 | @AML `0x03B1EF` SystemMemory base `0xFE410000` len `0x10000` | **完全相同** |
| `ECMP` / `ECXP` 区声明 | @`0x03B445` / @`0x03B45F`，EmbeddedControl，len `0xFF` | **完全相同** |
| 字段总数 | 193 | 193 |
| 只在 ROM 有的名 | — | **0 个** |
| 只在本机有的名 | — | **0 个** |
| 同名但地址或位宽不同 | — | **0 个** |

⇒ 两份 DSDT 确实差 29 字节，但**差不在 EC 这一块**。第十二节那张名字表是本机自己的，
"从本机 DSDT 挖出厂商命名的 EC 寄存器全表"这个说法到这里才算站得住
（当时是从隔壁板的 ROM 挖的，只是结果一样——**运气好，不是方法对**）。

**② 但 `0x0Exx` 整段没有数据，所以那 80 个"新地址"里有一大半用不了。**

同一个进程、同一条通道、同一秒内的对照读数：

| 地址 | DSDT 名 | 读到 | 独立参照 | 判定 |
|---|---|---|---|---|
| `0x0751` | — | `0xA0` | 面板 `fan_mode_flag = User_Fan_HiMode` | ✓ 对 |
| `0x0783` | `APL1` | `0x0A`(10) | 面板 `pl1 = 10` | ✓ 对 |
| `0x07C5` | `WHMS` | `0x80` | 早前实测 `0x80` | ✓ 对 |
| `0x087F` | — | `0xFF` | 早前实测 `0xFF` | ✓ 对 |
| `0x046C`/`0x046B` | — | `0x08`/`0x0F` | 面板 `fan2_rpm = 2063` = `0x080F` | ✓ 对 |
| `0x075C` | — | `0x3C`(60) | 面板 `fan_duty_r = 30.0 %` | ✓ 对（×2） |
| **`0x0E0D`** | **`CPUT`** | **`0x00`** | **同一时刻 CPU 47.1 °C** | ✗ **不可能是真值** |
| `0x0E0E`/`0x0E10`/`0x0E12` | `PCHT`/`SN1T`/`SN2T` | `0x00` | 无 | ✗ 同段 |
| `0x0E1C`/`0x0E1D` | `F1SH`/`F1SL` | `0x00` | 副风扇在转 2063 RPM | ✗ 同段 |
| `0x0E8C`/`0x0E9D` | `F1DC`/`F2DC` | `0x00` | 无 | ✗ 同段 |
| `0x0EA4`/`0x0EB0`/`0x0EC0` | `CCI0`/`MGI0`/`MGO0` | `0x00` | 无 | ✗ 同段 |
| `0x0EA0`/`0x0EA8` | `UVER`/`CTL0` | `0x01`/`0x04` | 无 | 非零，但孤立 |

判读要点：**不是通道坏了**（同一次调用里 `0x07xx`/`0x08xx`/`0x04xx` 全部正确），
**也不是整段越界**（`0x0EA0` 和 `0x0EA8` 有非零值）。
决定性的一条是 `CPUT = 0x00` 而 CPU 实际 47.1 °C——温度不可能是 0，
所以这一段**没有数据**，不是"读不到会报错"。

最可能的解释（**未证实**）：ITE8850 的 EC RAM 分 bank（参考仓库记的四个 bank 跳板
`0x1100/0x1114/0x1128/0x113C`），`0x0Exx` 落在另一个 bank 上，
而 `IOCTL_GPD_ACPI_ECREAD` 只读当前选中的那个。验证这个要**写** bank 选择寄存器，
属 6.3 第 2 条，得用户在场，**没做**。

⇒ **后果（这条直接改了 #37 的范围）**：ECMG 里那 7 个温度传感器
（`CPUT`/`PCHT`/`SN1T`..`SN5T`）、`F1SH`/`F1SL` 转速计、
`F1DC`/`F1CM` + `F2DC`/`F2CM` 手动占空比、以及 `CCI0-3`/`CTL0-7`/`MGI0-F`/`MGO0-F`
那一整块，**在本机这条通道上全不可用**。#37 不能照原计划把 98 个字段全接上去，
只能接 `0x04xx-0x08xx` 里和常量表重合的那些 + `0x0Fxx` 风扇表。
特别是 #31 那两个"新候选手动占空比字节"（`0x0E8C`/`0x0E9D`）**作废**，别再去试。

**③ #38 的转速计冲突结了，而且是好结果：两处不是"二选一"，是"一处可用 + 一处死的"。**

本机常量表 `ADDR_EC_MAIN_FAN_RPM_BYTE1/2 = 0x0464/0x0465`、
`ADDR_EC_SECOND_FAN_RPM_BYTE1/2 = 0x046C/0x046B`，
**和参考仓库反编译出的 `GetEcCpuFanRpm`(`0x0464`/`0x0465`)、`GetEcGpuFanRpm`(`0x046C`/`0x046B`)
一字不差**。ECMG 的 `F1SH`/`F1SL`@`0x0E1C`/`0x0E1D` 是唯一的少数派，
而且正好落在上面那段死区里。所以不存在需要挑的冲突。

活体对照还顺带把字节序钉死了：`0x046C = 0x08`、`0x046B = 0x0F` → `0x080F` = 2063
= 面板 `fan2_rpm`，分毫不差 ⇒ **BYTE1 是高字节**。
（这条之前是从数值反推的，现在是地址级的证实。）

⚠️ **顺带纠正我自己一处误读**：早先记的"副风扇 bytes `8,15` → `0x080F` = 2063"里，
`0x080F` 是**读到的值**，不是地址。这次抽查我一度把它当地址去读（读到 `0xFF`），
纯属巧合，那次读数不作证据。

**④ 主风扇报 0 RPM 是真的，不是读错地方**——#38 的前提也一并解掉了。

三个独立读数互相咬合：`0x075B L_DUTY = 0x00` → 面板 `fan_duty_l = 0.0 %`；
CPU 风扇表第 0 点占空比 `0x0F20 = 0x00`、第 1 点 `0x0F21 = 0x84`(132 → 66 %)，
而第 1 点的升温阈值是 53 °C（**注意 UpT 整表错位一格**：`0x0F00` 存的是第 1 点的阈值 53，
第 0 点的阈值是 0——见下面 ⑤）。当时 CPU 47 °C，**还没到 53 °C 这个转折点**，
所以左风扇占空比 0 %、转速 0，该停就停。

⇒ 两个副产品：
- **×2 占空比约定在本机活体证实**（`0x075C = 60` ↔ 30 %），之前只有参考仓库的字面
  `0xC8` = 100 % 加一个间接对照；
- 参考仓库那个 **16 点分块布局第一次在本机活体上被验证**：UpT/DownT/Duty 三段基址
  `0x0F00`/`0x0F11`/`0x0F20`、GPU 侧 `0x0F30`/`0x0F41`/`0x0F50`。
  `up_t` 单调递增（0, 53, 57, 59, 61, 63, 67, 69, 71, 然后 `0xFF` 哨兵）、
  `duty` 单调不减（0 %, 66 %, 99 %, 99 %…）、量纲也对得上（阈值是 °C、占空比是 ×2 的百分比）。
  ⚠️ **但别把 `down_t` 简单当"回落迟滞"**：第 0/1 点它确实低于 `up_t`（48<53、50<53），
  **第 2 点起反而比 `up_t` 高 3 °C**（60>57、62>59、64>61…），GPU 表同样。
  这个语义我**没弄明白**，写进 #31 的待办里，不要在没弄明白前拿它算任何东西。

**⑤ 顺手把当前档位下的完整风扇表存成了基线，带出四件新事。**

`tools/ec_fantable_dump.py --json` → `tools/out/fantable_baseline_0xA0.json`，
**118 次读 / 0 次写**（脚本自带"真出现了写就炸"的断言，退出码 0 说明断言没触发）。
存档时 `0x0751 = 0xA0`（`User_Fan_HiMode`）。

**(1) UpT 整表错位一格，本机活体证实。** 参考仓库说 `i=15` 的 UpT 恒写 `0xFF` 当哨兵、
`i<15` 写的是 `CPU[i+1].UpT`。本机物理字节正是这样：`0x0F00 = 53`、`0x0F01 = 57`，
而解码后第 0 点 `up_t = 0`、第 1 点 = 53、第 2 点 = 57。
`tools/ec_fantable_dump.py` 已经把这一格错位处理掉了，
**手写读表的人别把 `0x0F00` 当第 0 点**（我自己在上面 ④ 里就先写错了一次，已改）。

**(2) GPU 表最后三格是信箱，本机活体证实。** `0x0F5D-0x0F5F`（GPU duty 第 13/14/15 槽）
在转储里被标成 `duty_slot_is_mailbox: True`，读出来是 200 但**不是数据**——
参考仓库说这三个字节被 `RefreshDefaultFanTableAll` 借去当信箱
（`0x0F5F`=模式、`0x0F5D=0xFD`、`0x0F5E=0xC9`，500 ms 轮询）。
**CPU 表没有这个现象**（13-15 槽是正常数据）。#31 将来真要写表，这三格必须跳过。

**(3) 每档 PL 默认块的名字之争，本机数据把天平压向"Turbo"了。**

| 块 | 地址 | PL1 | PL2 | PL4 | 第 4 字节 | ECSpec 叫它 | 3.1.39.0 风扇管理器叫它 |
|---|---|---|---|---|---|---|---|
| A | `0x0730-0x0733` | 60 | 60 | 165 | 1 | GAMING | Gaming |
| B | `0x0734-0x0737` | 35 | 35 | 165 | 1 | OFFICE | Office |
| C | `0x07A7-0x07AA` | **75** | **75** | 165 | 1 | **BATTERYSAVER** | **Turbo** |

之前一直卡着的那个悖论——"省电档 PL1 反而比游戏档高（75 > 60）"——
**在 C 块叫 Turbo 的前提下直接消失**：35(Office) < 60(Gaming) < 75(Turbo)，单调、合理。
而本机在 `Turbo_Mode(0x10)` 下实测 `PL1 = 75`，正好等于 C 块的值——
**这是独立于命名之争的一条实测支持**。
⇒ 备忘里那条"这几个名字很可能错位、先别用"应该改成：**用 Turbo，别用 BATTERYSAVER**。
第 4 字节三个块都是 `1`，与参考仓库那条 CORRECTION（"第 4 个 D-state 字节确实会被写"）一致。
⚠️ 仍要记住：**EC 从不把这三个块搬进 PL**（第十三节），它们是"存着的常量"不是"当前生效值"。
当前 `0xA0` 档下 `PL1 = 10`，**三个块里没有 10**——再次印证厂商服务是从 profile 槽位写的。

**(4) 几个顺手确认的、和一处没解释的差异。**

- `0x07C5 = 0x80`：bit7=1，**CPU/GPU 分表是开着的**。两张表内容确实不同
  （CPU 封顶 99 %、GPU 封顶 100 %，阈值整套不同），与 bit7 的语义自洽。
- `0x078E = 0x6C` → bit6=1 ✓ `IsSuportRamFan1p5`；`0x0741 = 1` ✓ PL 清零闸没开。
- `0x0626 = 0`、`0x0627 = 0`：**本机第一次读到这两个"疑似档位状态字节"**。
  `0xA0` 档下都是 0，光一个档位看不出规律，**要等其他档位下的读数才能判**，现在不下结论。
- ⚠️ `0x07C6 = 0x07`：bit2 置着（通用风扇控制开着，符合静息态），
  但 **bits0-1 = 3**，也就是 DSDT 里的 `WMS0`（NVIDIA Whisper Mode）两位都置了。
  参考仓库记的静息值是 `0x04`。**这是一处没解释的差异**，不猜、不改，记在案。

**⑥ #36 关掉，代码不用动。**

`0x0786 = 0x00` ⇒ `APTN` 使能位没置、偏移 0 °C，出厂那三个 5 °C 默认值不生效，
降频点是 TjMax（100 °C）不是 95 °C，所以 `tools/ec_mode_bench.py` 的
`TEMP_ABORT = 97.0` **落在可达区间内、是有意义的保险，不动**。
`tcc_offset_of` 已经把这个字节拆成 `tcc_offset_enabled = False` / `tcc_offset_c = 0`，
面板下次重启后会显示出来。

这次是用现成的 `tools/ec_gpd_read_test.py 1926` **单发一次只读**取到的，
**没有重启用户正在跑的面板**（面板已跑 223 分钟，仍是加 TCC 之前的代码；
`state` 里也确实没有 `tcc_offset_*`，反过来印证了这一点）。

**⑦ 读操作的限度对照**：一共 6 次调用、**158 次读、0 次写**
（4 次定点抽查共 39 个地址 + 1 次 `ec_gpd_read_test.py` 单发 + 1 次全表转储 118 次读），
单遍、不重试、不枚举，只发 `IOCTL_GPD_ACPI_ECREAD`(`0x9C40A488`，从 OEM 常量表取的)。
地址全部来自本机 DSDT 的 ECMG 命名字段、本机常量表、或 `ec_fantable_dump.py` 里
早就写死的 `FAN_TABLE_BASE`，**没有扫过任何地址**。
转储那次 118 项、间隔 0.05 秒（20 次/秒），比 6.3 第 6 条给按需转储定的
0.03 秒还慢，**限速预算一点没动**。第 1 条（禁穷举）和第 6 条（限速）都没碰。

**⑧ 没做的事**：没重启面板、没写 EC（0 次写，脚本断言可证）、没跑负载、
没试 bank 切换（要写寄存器）、没把 `0x0Exx` 的任何名字接进代码、没改 `TEMP_ABORT`、
没动 `0x07C6` 那个没解释的差异。

#### 十五、**本轮最重要的一条**：面板的「性能模式」按钮是空的——那个字节是服务的**输出**，不是输入（2026-09-30 中午，用户在场）

起因是用户一句话：**"早上我看见电脑的频率特别低，我在面板上切换性能模式 CPU 频率只有
0.8GHz，当我点击造物者按钮的时候，功耗才拉起来。"** 这句描述是准确的，
而且它推翻了本项目一条写了很久的核心结论。

**① 先说结论：`0x0751`（`ADDR_MAFAN_CONTROL_BYTE`）是服务的输出，不是输入。**

本项目此前写着「它是硬件模式总开关，功耗墙是它的结果」——**因果搞反了**。
真实机制（来自参考仓库 v3.1.39.0 反编译，逐条有行号）：

```
按实体键 → EC 抬一个 WMI 事件 176 = 0xB0 = OSD_FanModeSwitch   （WMIEC.cs:248-251、ECSpec.cs:503）
        → 服务 WMIHandleEvent case 176 → m_MyFan.ModeSwitchChanged()   （WMIEC.cs:47,127-130）
        → ModeSwitchChanged 按**服务自己内部的** m_Option.OperatingMode 推进下一档  （MGR:487）
        → SetUserProfile 一次性写：PL1/PL2/PL4 + 风扇表 + GPU 字节 + 这个档位字节  （MGR:1712）
```

三条关键细节：

- `MyFanManager_RamFan1p5` 里**没有任何 Timer / Sleep / 轮询循环**；
  `GetFanMode()`（读 `0x0751` 认模式）**定义了但零调用点**。⇒ 服务**不轮询**这个字节。
- `ModeSwitchChanged` 用的是服务内部的模式，**根本不读 `0x0751`**。
  ⇒ 外部写进去的值对它**不可见**。
- PL 只由 `SetPL1/2/4Value` 写（`MGR:2039-2052`），而它**只被 `SetUserProfile` 调用**，
  `SetUserProfile` 的上游全是服务自己发起的路径：按键事件、MQTT `OPERATING_*`、电源/启动。
  ⇒ **写 `0x0751` 和写 PL 之间那条唯一的连线，在服务的肚子里，外部碰不到。**

**② 本机活体实验（这就是判决，不是推论）。**

| 时刻 | 动作 | `0x0751` | PL1 `0x0783` | 结果 |
|---|---|---|---|---|
| 12:19:01 | 基线（用户按过一次键） | `0xA0` | **10 W** | 用户"能感觉到卡" |
| **12:19:15** | **面板写** `Turbo_Mode` | `0x10`（回读一致） | **10 W** | **灯亮了，墙没动** |
| 12:19:15→12:20:58 | 观察 100 秒 | 一直 `0x10` | **52 个采样点全部 10 W** | 一次没爬起来 |
| **12:21:34** | 发 MQTT `OPERATING_TURBO_MODE` | `0x10` | 12:21:58 → **75 W** | **约 24 秒后生效** |
| 12:22:28 | 发 MQTT `OPERATING_OFFICE_MODE` | **服务写成 `0xA0`** | → **10 W** | 反向也成立 |
| 12:24:20 | 再发 `OPERATING_TURBO_MODE` | → `0x10` | → **75 W** | **还原成功** |

三件事一次全证了：**裸写字节无效**、**MQTT 那条路真的能改墙**、
**而且 MQTT 生效时是服务自己去写那个字节**（最后一行是它把 `0x0751` 改回 `0x10` 的）。

用户的身体也是证据之一：他回报"刚才按键亮了，然后中途电脑一下卡了，
刚才又感觉性能突然上来了"——**这三下全部对应我下发的命令**，
说明 PL1 在 10 W ↔ 75 W 之间是真的有体感差别的。
（顺带修正一句：他说的"0.8 GHz"本身是**空闲正常值**，i7-10875H 空闲就在 800 MHz，
所以那半句不是 bug；有体感的是功耗墙。）

**③ 所以面板之前是"假按钮"，而且不止一处。**

- 前端那个按钮写着**「性能模式 · 功耗墙 75W、风扇强冷」**（`app/tray/fankey.py` 的
  `FAN_FLAG_TEXT`），走 `/api/fan-mode` → 裸写 `0x0751`。**墙一个字没动，灯倒是亮了。**
- 更糟的是自适应调度也这么干：`fan_follow_tier` 让调度器每次进出 `perf` 边界就写那个字节，
  于是**每换一次档，就把键盘那颗物理指示灯拨到一个和真实功耗墙不符的状态**。
  用户看到的"灯说性能、墙说省电"就是这么来的。
- 真正的档位按钮（`/api/mode` → `OPERATING_*_MODE`）此前因为 `mode.write` 是
  `unknown`/`blocked` 而**根本没渲染出来**——按 6.3 的规矩不点亮是对的，
  但它意味着**面板从头到尾没有任何一条能改功耗墙的路**。

**④ 改了什么（本轮已提交）。**

- `mqtt_gcu.py`：`mode.write` 升 `verified`，理由写进 `mode_write_reason`
  （含"生效约 24 秒延迟"和"墙只有这条路改得动"）；新增 `mode_options` 把**按钮清单交给后端**。
- `base.py`：`MODE_LABELS` 改成从调度词表派生（省电/均衡/性能 + 「OEM 硬件档」后缀）。
  **上一版写的是「办公/均衡/狂暴」**——用户明确说过本机 Creator Center 没有这三档，
  按钮一旦点亮就会把这三个词推到他面前，这条一起修掉了。
  新增 `MODE_VERIFIED = ('office', 'turbo')`：只放本机验过的档，`balance`/Gaming 没验过就不放。
- `ec_gpd.py`：`set_mode()` 的拒绝理由换成真实机制；
  `set_fan_mode()` **在代码里硬拦自动跟随**（`who` 为空直接拒），
  位置在取地址和 `dev.write` 之前。用户本机 `data/config.json` 里
  `fan_follow_tier` 是**显式 `true`**，改默认值管不到正在跑的机器，
  所以按 6.3 第 3 条的先例走"代码硬拦、不留配置后门"。
- `config.py`：`fan_follow_tier` 默认改 `False`，注释写清为什么。
- `app.js`：档位按钮改用后端 `mode_options`（上一版前端自己写死 `['office','balance','turbo']`，
  其中 `balance` 点了只会报「未知档位」，是个死按钮）；
  风扇按钮旁边加一句明说"只改指示灯和字节，改不动功耗墙"。
- 新增 `tests/test_mode_channel.py`（10 个用例，含"自动跟随必须被硬拦"和
  "不许再出现办公/狂暴两个词"）。**全套 17 个文件全绿。**

**⑤ 顺手挖出来的一个闸门漏洞（还没修，先记在案）。**

`hardware.send_action()`（`app/act/hardware.py:175`）只查动作白名单，
**不查这个动作对应的 cap 状态**。所以 `mode.write` 标着 `blocked` 的时候，
本机任何进程 POST `/api/action {"action":"OPERATING_TURBO_MODE"}` 照样能把命令发出去——
"验证之前不点亮"目前**只是 UI 层的效果**。本轮我就是这样把命令发出去的（用户在场、已还原）。
按 6.3 第 7 条"两道闸都得在执行层里查，漏一个入口就等于开了一个不设防的硬件写口"，
这条该补：**白名单 + cap 都得过**。已列进待办。

**⑥ 同一轮里还揪出并修掉了一个归属 bug（#39）。**

面板把"服务回写的档位字节"认成"有人按了实体键"。日志原文（12:59:12 我点了新按钮，
**没人碰键盘**）：

```
12:59:12 [硬件] 切档 性能（OEM 硬件档）（面板手动）：切档 -> turbo
12:59:39 [实体键] 风扇字节 160 → 16，控制意图跟着切到 锁定性能
12:59:39 [EC变化] ADDR_MAFAN_CONTROL_BYTE: 160 → 16（不是本面板写的：实体按键或其它软件在改）
12:59:39 [EC变化] ADDR_PL1_SETTING_VALUE: 10 → 75（不是本面板写的…）
```

两个根因：`hardware.ECHO_SUPPRESS_S = 8.0` 而服务延迟实测 **24~26 秒**（四次：
12:21:34→12:21:57、12:22:28→12:22:48、12:24:20→12:24:29、12:59:12→12:59:38）；
`ec_gpd._watch` 里"`0x0751` 变了又不是我写的"直接等价于"有人按键"。
后果不是无害的：**给用户弹一句假消息**（"你按了造物者键"），
并且**给自己下一把 900 秒的按键优先锁**，把自动跟随冻住——
将来 `sync_ec_mode` 会因此永远跑不起来。

修法：`EcChannel.expect_fan_key_change()` 开一个 45 秒的回声窗口（`FAN_KEY_ECHO_S`，
实测延迟留一倍余量），`hardware.set_mode` 成功下发后立刻打招呼；
窗口内的变化**照实记录**但不抢锁、不触发 `on_key`；`ECHO_SUPPRESS_S` 同步提到 45 秒。
用例钉了两头：窗口内不许误判（`t_echo_window_suppresses_key`），
**窗口过期后真实按键必须还认得出来**（`t_expired_window_still_detects_key`）——
后者是防止把功能直接关掉。`tests/test_mode_channel.py` 现在 **13 个用例**，全套 17 个文件全绿。
代价说清楚：如果用户在面板下发档位命令后 45 秒内真的按了键，那一次按键不会被认出来
（不会弹提示、也不会抢优先）。因为自动跟随已被硬拦、`sync_ec_mode` 默认关着，
这个窗口内没有东西会去覆盖用户的选择，所以判断这个代价可以接受。

**⑦ 还没做的**：没验 `OPERATING_GAMING_MODE`（`MODE_VERIFIED` 里没有它，所以不放按钮）；
`sync_ec_mode` 仍默认 `False`（现在它真的能改墙了，默认开着等于让面板擅自改用户功耗墙，
得用户点头）；没动 `SET_OPERATING_MODE_DETAIL`（#27）；没改 `TEMP_ABORT`；没跑负载。
**⑥ 的修复需要再重启一次面板才生效**——本轮已经重启过一次（12:58），
那次带的是 `6d88d18`，不含 #39，所以现在跑着的实例还会弹那句假消息。
（上一版的 ⑤ 那个 `send_action` 不查 cap 的闸门漏洞**仍未修**。）


6.11 那轮"完整复现"用到的主资料，价值远高于其它来源，因为它对的是**同一块代工板**
（PROJECT_ID 同为 `0x0F`、PD 镜像逐字节相同、`ECSpec` 常量表 122/125 同名同址）：
[ElDavoo/tongfang-gm7mg7p-re](https://github.com/ElDavoo/tongfang-gm7mg7p-re)。
本仓库只**离线阅读**它的文档与反编译产物，未执行其中任何脚本，未再分发其内容。
具体读到的是这几处（第十二、十三节全部出自这里）：
`ec/annotations/registers.yaml`（4827 行 / 160 条目 / 192 个地址，含实机验证记录）、
`ec/annotations/dsdt-ecmg-fields.csv`、`ec/annotations/pd-0x07d8-flow.md`、
`ec/annotations/ghidra-functions.csv`、
`bios/decompiled/*.c` 与配套 `*.asm`（**77 个文件**的 Ghidra 反编译产物，
第十一节 ③-补 的更正就是读 `OemGlobalNvsDxe.c` 读出来的）、
`bios/decompiled/OemOcDxe.annotated.c`（人工标注版，`sizeof(SETUP_DATA)` = `0x7D8`
和 `gSetupGuid ec87d643-…` 都写在这里）、以及 `bios/ifr/Setup.en-US.ifr.txt`
（2.47 MB 的 IFR 转储——**只用来 `grep -i apctrl` 确认零命中，没有据此查任何 Setup 项的名字**，
因为 6.3 第 8 条(b) 永久禁止读写 UEFI 变量）。

## 6.11 第十六节：换个硬件档再转储一次 —— 风扇表整块会跟着换，#31 的前提结了（2026-09-30 13:29）

第十节留下的「甲（host 服务盖写过）/ 乙（本板 EC 默认就凶）」那个分叉，
以及第八节末尾写的局限（只读过一份表，分不清「host 写入区」和「此刻真正在管风扇的表」），
靠一次换档转储就能分开。今天做完了。

**同一块 `0x0F00-0x0F5F`，在 `0x0751` 从 `0xA0` 变成 `0x10` 之后整块换掉了。**

| | `0xA0`（基线，13:00 前） | `0x10`（Turbo，13:29） |
| :--- | :--- | :--- |
| CPU `up_t` 前 6 点 | 0,53,57,59,61,63 | 0,53,57,59,61,63 |
| CPU `up_t` 点 6-10 | 67,69,71 之后 `0xFF` 哨兵 | 65,67,75,80,83（点多铺了 3 个） |
| CPU `duty_raw` | 0,132,198,198…（66 %、99 %） | 0,156,200,200…（**78 %、100 %**） |
| GPU `up_t` 前 6 点 | 0,52,53,56,58,60 | 0,50,50,52,54,56（整体提前 2 °C） |
| GPU `duty_raw` | 0,152,200…（76 %、100 %） | 0,152,200…（同） |
| 同时的 `0x0783/84/85` | 10,10,165 | **75,75,75** |

（两份原始数据：`tools/out/fantable_baseline_0xA0.json` 118 读 / 0 写，
`tools/out/fantable_turbo_0x10.json` 118 读 / 0 写。两次都是停面板跑的。）

**三条结论**：

1. **`0x0F00-0x0F5F` 就是"此刻真正在管风扇的那张表"**，不是某个备用副本 ——
   它跟着硬件档整体重写。#31 的必做前提（「写的是不是没生效的那套表」）到此有了答案：
   **写这张表会立刻生效，但下一次切档就会被服务盖掉**。
   所以面板如果做风扇曲线写入，必须写清这件事：自定义曲线只在**当前档内**有效，
   切档 = 覆盖。用户想长期保自定义曲线，只能停在同一档。
2. **甲成立、乙作废**：活表与厂商 ROM 默认表不同，是因为**服务按档位往这张表里灌了不同的表**，
   不是"本板 EC 默认更凶"。第十节 ③ 的那条提醒（别把厂商 55 % 当安静曲线照抄）依然有效。
3. **`0x10` 的表更激进而不是更保守**：CPU 从点 2 起直接 100 %、GPU 阈值整体提前 2 °C，
   和「Turbo = 强冷」的直觉一致；这也解释了为什么 Turbo 档风扇声明显。

**没做的**：`0x00`（Gaming / 半亮）那一份还没转储 —— 需要先把档位切到 `0x00`。
按 6.11 十五，`OPERATING_*_MODE` 里没有 Gaming 的可逆验证依据，
但实体键按一次就会走到 `Normal_Mode(0x00)`，**所以那一份要用实体键凑**（需要用户在场，
顺手还能验掉 #39 那条「按键认得出、自己发的命令不误判」）。
**但这条路实际上凑不出来**：GCUBridge 在跑时实体键只在**全亮/不亮**两态之间循环（本文件 6.4 就记过），
所以 `0x00` 只能靠 `OPERATING_GAMING_MODE` 那条**未验证**的写动作，或者停掉 GCUBridge 再按键。

## 6.11 第十七节：背光关着、灯条开着——把「`SingleColorKBBL` 不是背光开关」验实了（2026-09-30 13:50，用户在场）

面板上三个读数同时摆出来，用户对着实物确认了一遍：

| 读数 | 来源 | 值 | 实物 |
| :--- | :--- | :--- | :--- |
| 键盘背光 | EC `kb_backlight.on`（`0x078C` 只读镜像） | `False` | **灭** ✓ |
| 键盘背光 | OEM `Keyboard/Status.powerStatus` | `Off` | **灭** ✓（两条独立通道一致） |
| 背光亮度档 | `Keyboard/Status` 的 AC/DC | `4` / `0` | 关了还留着档位值，**亮度 ≠ 开关** |
| 灯条（海岸灯） | OEM `HidLightbar/Status` | `on`，亮度 4 | **亮** ✓ |
| 单色背光 | `Setting/Status.SingleColorKBBL` | `ON` | 灯是灭的 —— **这一条不是背光开关** |

这就是 6.6/6.7 里那句警告的正面证据：`SingleColorKBBL=ON` 说的是「单色背光这个功能支持/开着」，
拿它当背光开关就会在**灯灭着的时候显示"背光开"**。代码里两个值分开存、分开显示，不许合并。

顺带把「键盘亮度 AC=4」这条记下来：#29 做灯效写入验证时，
初始状态是**灭 + 亮度 4 档**，所以"写一次开、读回、再关回去"要同时把亮度档存下来，
别写完关掉却发现亮度档被改了。厂商 BIOS 命令面里给了读回命令（`LEDKB /GetStatus`，见第九节②-补），
两边都能读回来，所以这条路的「可逆」是厂商认可的。

## 6.11 第十八节：`Keyboard/Ctrl` 的确切形状不用猜——读 GCUService 的 MQTT 分发（2026-09-30 14:05）

要做 #29 就得知道报文到底长什么样。**先纠正一条早先写得太满的话**：
6.7 那张表里 `Keyboard/Ctrl {"function":"SetPower","light":"3","speed":"2"}`
标着「抓包确认」，其实我们自己的 `tools/out/mqtt-watch.txt` 里**只录到过自己发的那条 GETSTATUS**，
那个形状是从 OEM 字符串表拼出来的（`SetPower`、`light`、`speed` 这些字符串确实都在
`gcu-strings-all.txt` 里）。所以它是推断，不是观测——现在换成硬证据。

硬证据来自参考仓库里现成的反编译产物（离线读，没执行任何脚本）：
`windows/decompiled/v3.1.39.0/GCUService/MyRGBKeyboard/RGBKeyboard.cs` 的 `OnMqttMessage`：

```csharp
case "SetPower":
    RGBKB_PowerStatus rGBKB_PowerStatus = val["powerstatus"];
    if (!MyEcCtrl.Instance.IsChinaMode()) {         // 非中国模式才碰 EC
        byte Data = 0;
        MyEcCtrl.Instance.Read(GetType().Name, 1922, ref Data);   // 0x0782
        Data |= 0x40;
        MyEcCtrl.Instance.Write(GetType().Name, 1922, Data);
    }
    ... m_hidkeyboad?.SetPowerStatus(rGBKB_PowerStatus);
```

四条结论：

1. **字段是 `function` + `powerstatus`，不是 `Action`**。所以白名单条目得能声明
   自己的报文形状，代码里不许写死 `{"Action": ...}`——已经改成
   `spec.get('field','Action')` / `spec.get('value')` / `spec.get('args')`。
2. **取值是枚举序号**：`RGBKB_PowerStatus` = `Off=0, On=1, Lighting_off=2, Lighting_on=3,
   Welcome_off=4, Welcome_on=5`。我们只发 0/1，脚本里对 2~5 直接拒。
   状态侧同一份数据以两种形式回报：`powerStatus`（字符串 "On"/"Off"）和
   `powerstatus`（整数 1/0），别只认一个。
3. **这条命令会顺带写 EC `0x0782` bit6**，但只在 `IsChinaMode()` 为假时。
   也就是说背光开关的 EC 副作用是**服务自己写的**，不是我们写的——
   这正好解释了 6.7 那条「点背光时 EC 全表不动」：本机是中国模式，那条分支不跑。
4. `SetLightingLevel` 走的是另一个函数（`RGBKeyboard_SetEffectLight`），
   读 `data["mode"]` + `data["light"]`（亮度是 uint），**和 `SetPower` 不是一条命令**。
   所以「开背光」和「调亮度」要分开发，别指望 `SetPower` 顺带把亮度拉满。

**同一天补上的第三道闸**（这条是 6.3 第 7 条的收尾）：`send_action` 以前只查白名单，
现在多查一层「这个动作对应的 cap 是不是 verified」。之前那个缺口的真实后果是
`mode.write` 还标着 blocked 的时候，本机任何进程 POST `/api/action` 就能把
`OPERATING_TURBO_MODE` 打到硬件上——今天做验证时我就是这么发的，属于正当使用，
但这个口子本身不该存在。现在首次验证只能走 `tools/` 下的脚本（脚本自己读同一份白名单，
不在 HTTP 上开口子），`tests/test_write_gates.py` 13 条盯着：13/13。

**#29 的准备已经就绪、还没做**：`tools/kb_power_test.py` 是「读原值 → 开 → 回读 → 关回原值 → 回读」，
拿不到初始 `Keyboard/Status` 就一个命令都不发，还原失败退出码 3。
跑之前要**停面板**（抢同一个 broker 身份会互踢），所以要用户在场上才做得完——
他今天出门了，脚本先压着不发。

## 6.11 第十九节：厂商字段名对照做完了，只有 6 个对得上（#37 结掉，2026-09-30 14:15）

把 `app/act/channels/ec_gpd.py` 实际读的 45 个 `ADDR_*` 名字，逐个拿去和本机**活着的**
DSDT 里 `ECMG` 的字段表（`tools/out/dsdt_ecmg_live.txt`，第十四节那份 193/193 对账的产物）比地址。
**只有 6 个对得上**：

| 我们的名字 | 地址 | 厂商 `ECMG` 字段 | 说明 |
| :--- | :--- | :--- | :--- |
| `ADDR_PL1_SETTING_VALUE` | `0x0783` | `APL1(8)` | 第十四节已用；`T1WR 0x81` 的落点 |
| `ADDR_PL2_SETTING_VALUE` | `0x0784` | `APL2(8)` | 同上（`T1WR 0x82`） |
| `ADDR_PL4_SETTING_VALUE` | `0x0785` | `APL4(8)` | 同上（`T1WR 0x84`） |
| `ADDR_L1_PWM_DEFAULT_MYFAN3` | `0x0786` | `APTC(7)` + `APTN(1)` | 名字里根本没有 PWM/TCC 的影子：低 7 位是偏移、bit7 是使能。第十三节那个「单位是 °C、使能位实测 0」的结论就是靠这对字段立住的 |
| `ADDR_BATTERY_CHARGE_LIMIT_DOWN` | `0x07D0` | `DBD1(8)` | **我们这个名字是过期的**：它是 GPU Dynamic Boost，不是充电下限（6.3 第 8 条那条禁写的理由就在这） |
| `ADDR_OEMSERVICE_PROJECT_ID_BYTE` | `0x074C` | `PDIN(4)` | 只有 4 位，本机读到 15 = `0x0F`，正好塞满 —— 和 `0x0740` 那个 PROJECT_ID=15 是同一件事的两个视图 |

**剩下 39 个在 ECMG 里没有名字**，其中最有分量的两个恰好都在里面：

- `ADDR_MAFAN_CONTROL_BYTE`（`0x0751`，档位字节）——ACPI 不声明它；
- `ADDR_STAUTS_BYTE`（`0x0768`，Win 键锁定）——同样不声明。

这是个有用的**旁证**而不是结论：DSDT 不给这两个字节开字段，意味着 ACPI 侧没有现成的写者，
它们的写者基本只剩 OEM 服务和 EC 自己——和第十五节「`0x0751` 是服务的输出」互相咬合。
但"没声明"不等于"没人写"（`T1WR` 那类通用写口可以写任意地址），所以这条不当证据用。

**没做的和为什么**：这些别名**不写进代码注释**。仓库自己的承诺是
「厂商私有寄存器映射与机型表不在本仓库分发范围内」（README 第 9 节、`ec_gpd.py` 开头那条），
把 45 个名字和地址成对钉进源码就接近一张映射表了；对照表放这里，代码里只留名字。
`0x0Exx` 那一段照旧**不接进任何代码**（第十四节：名字在本机 DSDT 里存在，数据在这条通道上读不到）。

ECMG 里我们**没在读**、但名字看着有信息量的地址，留作以后的线索（同样只是线索，不是语义）：
`0x0743 ECDC/GNEN`、`0x0744 CTVA`、`0x0745 DBCT`、`0x0746 MXDB`、`0x0747 MIDB`、
`0x0788 CTWA`、`0x07A4 GC6S`、`0x07B3 PMAX`、`0x07B5 PBSS`、`0x07B7 PSRC`、`0x07B8 DTTF`、
`0x07BA VBNL`、`0x07BC RBHF`、`0x07BE CMPP`、`0x07C0 AP01`、`0x07C1 AP02`、`0x07C2 AP10`、
`0x07C4 DBEN/DBST`、`0x07C5 WHMS`、`0x07C6 WMS0`、`0x07D1 DBD2`、`0x07D3 GFID`、
`0x07D4 CPUA`、`0x07D5 DBAP`、`0x07D6 DBSP`、`0x07D7 CGCT`、`0x043E CPTM`、`0x044F VGAT`、
`0x0460 FFAN`、`0x0468 SDAN`。
注意 `0x07C5/0x07C6` 我们已经在读（风扇分表开关、写表括号），厂商叫 `WHMS`/`WMS0`——
**两边说法不一样这件事本身就该记下来**，别以为只有一个名字是对的。

