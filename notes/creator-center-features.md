# Creator Center 功能地图与 MQTT 动作清单（静态逆向）

> 2026-10-20。纯静态：只读 `tools/out/gm7mg7p-re/windows/decompiled/v3.1.39.0/GCUService/`
> （同系机型逆向仓库里的 v3.1.39.0 反编译 C#，本机那份 IL 是加密的）与同仓库的
> `windows/mqtt-protocol.md`。**没有连接 broker、没有读写 EC**。
> 路径都相对 `tools/out/gm7mg7p-re/`；该目录在 `.gitignore` 里，仓库外没有留副本。

这份文件回答两件事：原厂服务到底认哪些命令；其中哪些我们**可以安全地**接进面板。
风扇状态机与 EC 侧时序在 `notes/hardware-channels.md` 6.11 第二十四节，这里不重复。

## 1. 本机走哪一套实现

- `MyFanTableCtrl` 构造时按 `EcCtrl.IsSuportRamFan1p5()` 与 ProjectId 选管理器
  （`windows/.../MyControlCenter.MyFan.FanTable/MyFanTableCtrl.cs:18-35`）。
  本机 `Fan/Status` 会报 `FAN_TableName`（`M3T1`/`M2T1`），且 EC 表在 `0x0F00` 页，
  走的是 **`MyFanManager_RamFan1p5` + `FanTable_Manager1p5`**。
- 服务订阅 `Fan/Control`（`MyControlCenter/App.cs:199`），`Fan/Control` 的 `Receive`
  在 `MyControlCenter.MyFan/MyFanManager_RamFan1p5.cs:248-351`。
- 订阅清单（18 条）见 `MyControlCenter/MqttClientCtrl.cs:48-63`。应答主题不是统一的
  `*/Status`：风扇曲线走 **`Fan/Table`**（`FanTable_Manager1p5.cs:284`、`:419`）。

## 2. `Fan/Control` 的动作（本机实际生效的那套）

| Action | 参数 | 作用 | 结论 |
|---|---|---|---|
| `GETSTATUS` | 无 | 回 `Fan/Status`；只有问这个主题才回，问 `Setting/Control` 问不到 | 只读，已在用 |
| `DEFAULT` | 无 | `UserSet_Default`：恢复默认档/表，会重灌 EC 表 | 写，不接 |
| `FAN_BOOST_ON/OFF` | 无 | 只改 `0x0751` bit6，不重写表 | 写，未验证 |
| `OPERATING_GAMING_MODE` | `ProfileIndex`(可选 0-4) | 切档；会写 NVRAM `PowerMode`、重灌表、改 PL | 已接，`mode.write` verified |
| `OPERATING_OFFICE_MODE` | 同上 | 同上 | 已接 |
| `OPERATING_TURBO_MODE` | 同上 | 同上 | 已接 |
| `SET_OPERATING_MODE_DETAIL` | PL1/PL2/PL4/TccOffset/GPU 偏移/TGP/DynamicBoost/FanSwitchSpeed | 改当前档的细节 | 已接 PL1/PL2/PL4（`power_limit.write` verified）；TCC/风扇切换速度未接 |
| `RESTORE_OPERATING_MODE_DETAIL` | 无 | 恢复当前档细节 | 写，不接 |
| `GET_FAN_SPEED_CURVE_SETTING` | 无 | 把**内存里当前表**发到 `Fan/Table` | 只读，准备接 |
| `SET_FAN_SPEED_CURVE_SETTING` | `Name`+`Type`+`T0..T15` | 改占空、落盘 JSON、直接写 EC 表 | #46 的目标，验证前不点亮 |
| `SET_FAN_CONTROL_RESPECTIVE` | `Name`+`FanControlRespective`(bool) | CPU/GPU 风扇是否独立控制 | 写，未验证 |
| `RESTORE_FAN_SPEED_CURVE_SETTING` | `Name`+`Type` | 用**出厂默认表**覆盖该表（不是"撤销上一次"） | 写，不接 |
| `RESTORE_FAN_SPEED_CURVE_SETTING_ALL` | 无 | 15 张表全部回默认 | 写，不接 |
| `SET_DEFAULT_NOTIFY_OFF` | 无 | 关"恢复默认"提示 | 写，不接 |
| `SET_FAN_SAFETY_PROTECT_NOTIFY_OFF` | 无 | 关高温保护提示 | 写，不接 |
| `SET_FAN_MODE_TABLE` | `FanMode`+`DelayTime` | 仅 Intel 管理器有；本机这套没有 | 不适用 |
| `TestModeSwitch` | 无 | 调试用模式切换 | **绝不接** |

> `GetFanTable` 返回的是**内存副本**（`FanTable_Manager1p5.cs:280-285`），不是 EC 实时值。
> 它和 EC 直读的差异要单独核对；服务只有在启动/恢复/切档等时机才从 EC 回读。

## 3. 风扇曲线（#46）的完整契约

### 3.1 报文

```json
{"Action":"GET_FAN_SPEED_CURVE_SETTING"}
```

应答在 `Fan/Table`，形状是 `FanTable1p5`：
`{Activated, Name, FanControlRespective, CpuTemp_DefaultMaxLevel, GpuTemp_DefaultMaxLevel,
CPU:[{ID,UpT,DownT,Duty} ×16], GPU:[... ×16]}`（`FanTable1p5.cs:3-57`）。
`Duty` 是**百分数 0-100**；写 EC 时服务自己乘 2（`FanTable_Manager1p5.cs:599-621`）。

```json
{"Action":"SET_FAN_SPEED_CURVE_SETTING","Name":"M3T1","Type":"CPU",
 "T0":30,"T1":30,"T2":35,"T3":40,"T4":45,"T5":50,"T6":55,"T7":60,
 "T8":65,"T9":70,"T10":75,"T11":80,"T12":85,"T13":90,"T14":95,"T15":100}
```

- `Name` 必须来自 `Fan/Status` 的 `FAN_TableName`，不能写死；`Type` 只认 `CPU`/`GPU`。
- **一次必须给齐 16 个 T 值**；缺一个 `Convert.ToByte(null)` 抛异常，被 `catch` 吞掉，
  表现是"什么都没发生"（`FanTable_Manager1p5.cs:687-737`）。
- **只能改 Duty**。`UpT/DownT` 不在这条报文的字段里。
- 服务做完三件事：`SetFanTableActivated(name,true)` → 写 `UserFanTables\<Name>.json`
  → `SetEcFanTable_Cpu/Gpu` 写 47 字节（`FanTable_Manager1p5.cs:599-621`、`:687-737`）。
- **不走 bracket**：这条路径不清 `0x07C6` bit2。它和 OEM 自己拖滑块的行为一致；
  写的是整张表（47 字节），中途被 EC 读到一瞬半张表的概率与 OEM 相同。

### 3.2 门禁与边界

- **高温保护**：`0x07A5` bit3 = 1 时，服务只落盘 JSON、**不写 EC**
  （`MyFanManager_RamFan1p5.cs:327-336`、`:2194-2203`）。
  这比"写失败"更危险：面板以为改了，等保护解除/切档重灌时值才突然生效。
  ⇒ 我们的写入必须先读这个位，置位时**拒绝下发**，并在 UI 上说为什么。
- **30 % 地板**：EC 会把"非零但 < 30 %"的目标钳到 30 %；0 % 要等 180 秒退避计数归零
  才真停转（见 6.11 二十四）。UI 不要让用户以为设 10 % 就是 10 %。
- **慢**：EC 斜坡每 tick 0.5 %（约 5 %/s），大改一条曲线要几十秒才听得出；
  给"已下发 / 已生效"两段反馈，不要让人连点。
- **恢复**：`RESTORE_FAN_SPEED_CURVE_SETTING` 恢复的是**出厂默认表**，不是用户原表。
  我们自己的恢复动作必须把 GET 到的原值原样 SET 回去；验证脚本也要用这条。

### 3.3 能力拆分

| 能力 | 含义 | 现状 |
|---|---|---|
| `fan.curve` | 读曲线（EC 直读已有；再加 MQTT `Fan/Table` 一条旁证） | EC 侧 verified；MQTT 侧未接 |
| `fan.curve.write` | 通过 OEM 服务写 Duty | `blocked`，验证前不点亮 |
| `fan.curve.thresholds` | 改 UpT/DownT | **不做**：要停服务改 JSON，且重启才装载 |

### 3.4 验证结果（2026-10-20，本机）

- **读**：`tools/fan_curve_mqtt_probe.py` 拿到 `Fan/Table`，`M3T1` 的 CPU/GPU 16 点与
  EC 直读**逐点一致**（GPU 13-15 是 EC 侧信箱槽，EC 不报占空；OEM 表照实报 100%）。
  服务返回的是内存副本，不是 EC 实时值。
- **写**：`tools/fan_curve_mqtt_write_test.py --write` 做了单点可逆验证——
  只改 CPU 点 15（`UpT=255`，永远查不到）的占空，EC `0x0F2F` 由 200 → 180；
  另外 15 个 Duty 与全部 UpT/DownT 一个没变；把原 16 点写回后，EC 与 OEM 表逐点零差异。
  没做负载、没停面板、没有风扇/温度变化。
- **判定**：`fan.curve.write` 升 `verified`（MQTT 通道）。写入只改 Duty，不改阈值；
  高温保护 `FAN_SafetyProtect≠0` 时服务只落盘不写 EC，所以面板在置位时**拒发**。
- **面板**：`GET /api/fan-curve?source=mqtt` 只读；`POST /api/fan-curve`
  `{"type":"CPU","duties":[16 个 0-100]}` 写入，五条闸在 `MqttChannel.set_fan_curve`。
  前端「风扇曲线」卡新增 OEM 表编辑器（CPU/GPU 切换、16 个输入框、下发/还原）。
  2026-10-20 用**不改值**的 POST 走了一遍 HTTP 全链路，回读一致。

## 4. 其它值得接的

### 4.1 TCC 偏移（安全、可逆、有现成读取位）

- 开关 + 值分两条消息，**顺序不能反**：
  `SET_OPERATING_MODE_DETAIL {"CpuTccOffset":5}` 然后 `{"CpuTccOffsetSwitch":1}`
  （`MyFanManager_RamFan1p5.cs:768-790`）。
- EC 落点 `0x0786 = value | 0x80`；关闭写 `0x0786 = 0`（`:2054-2066`）。
- 本机当前 `0x0786` 读到 `0x00`（#36），所以现在那个 5 °C 不生效。
- 边界由 `Fan/Status` 的 `CPU_TccOffsetMaximum` 报（本机 5？——接之前先读一次确认）。
- 注意：它属于 `SET_OPERATING_MODE_DETAIL`，和功耗墙同一族，切档会被服务重算。

### 4.2 风扇切换速度

- `SET_OPERATING_MODE_DETAIL {"FanSwitchSpeed":300}` + `{"FanSwitchSpeedEnabled":1}`；
  关闭发 `{"FanSwitchSpeedEnabled":0}`（`MyFanManager_RamFan1p5.cs:753-761`）。
- EC `0x0787 = (ms/100) | 0x80`；**< 100 ms 被静默忽略**，关闭写 0（`:2068-2082`）。
- 它控制的正是 EC 斜坡限速器的 R5（见 6.11 二十四），语义清楚、风险低。
  但"300 ms"到底让风扇更快还是更慢，要在场听一次再写文案。

### 4.3 系统监视（纯读）

- `System/Control {"Action":"System_ON"}` / `System_OFF` 开关监视（`Define/ServCMD.cs:384`、
  `GCUService.WCFService/WCFService.cs:77-81`）。这是纯读，不碰 EC。
- 面板已经有 CPU/GPU/内存/磁盘遥测，接它收益不大；除非要拿 OEM 的磁盘/内存型号。

### 4.4 键盘灯效 / 灯条

- `Keyboard/Ctrl`：`SetAurora`、`Init`、`DeInit`、`SetPower`、`SetEffectALL`、`SetColor`、
  `SetLightingLevel`、`SaveLightingLevel`、`SetPowerSaving`、`GetEffect`、
  `GetLightingLevel`、`GetFirmwareVersion`（`MyRGBKeyboard/RGBKeyboard.cs:451-607`）。
- 已接的只有 `SetPower`（背光开关）。灯效要逐条定义报文形状并做在场可逆验证；
  用户没点名要，先不动。

## 5. 红线：看到也不要接

| 功能 | 为什么 |
|---|---|
| CPU 核心电压偏移 | 服务端直接写 EC/固件，属于超频，错了会死机 |
| 内存超频 | `NvramVariable.SetFwVars("MemoryOverClockSwitch")`，UEFI 变量 |
| 独显直连 / 显示模式 | `SetFwVars("OemDisplayMode")` → `WriteUefi`，重启生效 |
| Fn 锁 / AC 恢复 | `SetFwVars("FnKeyStatus"/"ACRecoveryStatus")`，UEFI 变量 |
| `OPERATING_*` 的 NVRAM 部分 | 我们已接档位，但**不复制**它的 `PowerMode` 写；那条要 3 秒 UEFI 延迟 |
| 一键清理 | 清空回收站不可逆 |
| `TestModeSwitch` / `Service/Close` | 调试/关服务入口 |
| 任何 BIOS/EC 固件刷写 | 客服 ROM 是 GM7MG0M，不是本机 |

`NvramVariable.SetFwVars` → `WriteUefi` 的证据：`MyControlCenter/NvramVariable.cs:62,173`；
调用点 `MySettingManager.cs:593,633,666`、`MyFanManager_RamFan1p5.cs:245-248`。

## 6. 下一步（按顺序）

1. ~~`GET_FAN_SPEED_CURVE_SETTING` 接进 MQTT 通道~~（2026-10-20 完成：订阅 `Fan/Table`、
   只读动作、`GET /api/fan-curve?source=mqtt`，已与 EC 直读逐点对照）。
2. ~~首次可逆验证~~（2026-10-20 完成：单点哨兵、字节级可逆，见 3.4）。
3. ~~`fan.curve.write` 升 verified、面板 UI~~（2026-10-20 完成：OEM 表编辑器，
   支持 CPU/GPU、下发/还原；不改阈值）。
4. TCC 偏移与风扇切换速度另开小步，各自验证后再点亮。TCC 的开关+值必须**两条消息、先值后开关**；
   风扇切换速度 <100 ms 会被服务静默忽略。
