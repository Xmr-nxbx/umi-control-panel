# Umi Control Panel

机械革命 **Umi Pro 3**（模具 GM5MG0Y，i7-10875H + RTX 3060 Laptop）的本地控制中心，
用来替代官方 Creator Center 的能耗档位控制。浏览器面板 + 托盘 + 开机自启，
绿色运行、无云端、无安装器，面板只监听 `127.0.0.1:8747`。

它解决的是官方工具留下的两个日常问题：

- **要么一直拉满、要么卡得没法用**—— Creator Center 只有两档，中间没有过渡；
- **息屏再亮屏，性能模式被自动关掉**，必须再按一次实体键才能恢复。

## 1. 功能

**自适应调度**

- 四个执行档：省电 / 均衡 / 流畅 / 性能，按 CPU、GPU 负载和温度自动切换；
- 升档快、降档慢，带换挡防抖，不会在两个档之间来回跳；
- 亮屏缓冲 + 息屏掉档拦截：人回到电脑前先给一档过渡，杜绝亮屏后爬频卡顿那几秒；
- 温度趋势预判：重任务刚点火就提前放开功耗墙，让它快进快出，风扇高转的总时长反而更短；
- 温度保护：超过阈值自动封顶到均衡档；
- 「安静优先 / 标准 / 性能优先」三个按钮代替 12 个阈值数字，换档即时生效、重启后保持。

**硬件档位**

- 读取并切换 OEM 的硬件档（锁定省电 / 锁定性能），走 OEM 自己的服务接口；
- 实体「造物者模式」按键会被识别，并只在**人为操作**时弹一条屏幕提示（自适应自己换档不弹）；
- Win 键锁定开关；
- 按实体键之后的一段时间内，面板不自动覆盖你手动按出来的曲线。

**遥测与可视化**

- CPU 温度 / 实际频率 / 占用，GPU 温度 / 功耗 / 占用，内存、空闲、前台进程；
- 风扇转速与占空比、电池电量 / 温度 / 循环次数；
- 历史曲线（温度、实际频率、占用、转速）：5 秒一点、窗口 30 分钟，档位切换点画成竖线，纯 canvas、无外部依赖；
- 16 点风扇曲线只读查看（CPU / GPU 各一张表）。

**验证与运维**

- 内置跑分：官方 zstd 全核 / 单核压缩 + CoreMark + 短任务延迟，四档逐一对比，约 3 分钟；
- 一键诊断：13 项体检结论 + 最近 40 行日志，一键复制到剪贴板；
- 能力矩阵：每一项硬件能力的真实状态（可用 / 只读 / 未验证）原样显示，未验证的开关一律置灰并写明原因；
- 守护模式：子进程异常退出自动拉起，界面卡死也会被检出并重启，单实例互斥、端口占用自动顺延。

## 2. 快速开始

```bat
:: 1) 准备运行时（便携 Python 3.12，不入库）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_runtime.ps1

:: 2) 生成本机 EC 寄存器表（只需一次，读取本机已装的 Creator Center 元数据）
scripts\生成EC寄存器表.bat

:: 3) 启动面板（会自动开浏览器）
scripts\启动面板.bat

:: 4) 开机自启（HKCU Run，不需要管理员）
scripts\安装开机自启.bat

:: 其它入口
scripts\一键体检.bat        &rem 逐项检查自启/运行时/面板/传感器/通道
scripts\EC只读自检.bat      &rem 只读验证 EC 通道并打印寄存器读数
scripts\跑分对比.bat        &rem 四档对比跑分
scripts\面板状态.bat / 停止面板.bat / 卸载开机自启.bat
```

OEM 兜底通道的身份（clientId / 用户名 / 口令）不进仓库：
把 `mqtt_identity.example.json` 复制成 `data/mqtt_identity.json` 填好即可，`data/` 已在 `.gitignore` 里。
没填时该通道显示「需配置」并跳过连接，不会反复重试。

## 3. 命令行入口

统一入口是 `main.py`：

| 命令 | 作用 |
| :--- | :--- |
| `runtime\python.exe main.py` | 常驻：面板 + 托盘 + 调度循环 |
| `main.py --supervise --no-browser` | 守护模式（开机自启用的就是这个） |
| `main.py --bench compare` | 四档逐一对比跑分（约 3 分钟） |
| `main.py --bench current` | 只测当前档位，约 15 秒 |
| `main.py --one-shot` | 采一次快照打 JSON，自检用，不改任何设置 |
| `main.py --health` | 一键体检 |
| `main.py --ec-test` | EC **只读**自检（普通权限即可） |
| `main.py --stop` | 让运行中的实例优雅退出（会还原改过的电源设置） |

## 4. 配置

配置文件是 `data/config.json`（首次启动自动生成），损坏时自动改名备份并用默认值继续跑。常用的几项：

| 键 | 默认 | 说明 |
| :--- | :--- | :--- |
| `intent` | `auto` | 面板上选的控制意图：`auto` 自适应，或 `office` / `balance` / `turbo` 锁定某档 |
| `scheduler.profile` | `standard` | 调度性格：`quiet` / `standard` / `perf` |
| `scheduler.*` | 见 `app/config.py` | 负载阈值、驻留期、防抖、温度保护等 |
| `tray.enabled` / `tray.osd` | `true` | 托盘；按实体键时的屏幕提示 |
| `hardware.ec.enabled` | `true` | EC 只读通道 |
| `hardware.ec.allow_write` | `false` | **所有硬件写入的总开关**，默认关闭 |
| `hardware.ec.respect_external_s` | `900` | 按过实体键之后多久内不自动跟随 |
| `hardware.sync_ec_mode` | `false` | 自适应时是否联动 OEM 硬件档 |
| `hardware.mqtt.*` | `127.0.0.1:13688` | OEM 兜底通道地址 |
| `power.restore_on_exit` | `true` | 退出时还原电源设置 |
| `server.port` | `8747` | 被占用时自动顺延，实际端口写进 `data/port` |

## 5. 工作原理

```
main.py                     入口 + 启动期兜底
app/
  cli.py                    参数、单实例、端口顺延、生命周期
  daemon.py                 1 秒节拍：遥测 → 决策 → 执行 → 看门狗 → 硬件跟随
  config.py                 默认值 + 深合并 + 原子落盘
  policy/scheduler.py       四档自适应状态机（纯判定，可注入假时钟做单测）
  bench.py                  跑分：zstd / CoreMark / 短任务延迟 + 相对基准指数
  sense/                    遥测：负载与内存(ctypes)、CPU 温度与频率(PDH)、GPU(nvidia-smi)
  act/power.py              powercfg 方案 + EPP + turbo + min/max，注册表回读校验
  act/hardware.py           通道总管：能力协商、切档、息屏掉档守护
  act/channels/             ec_gpd.py（EC 直连，主通道）
                            mqtt_gcu.py（OEM GCUBridge，兜底通道 + 命令白名单）
                            hid_ite8291.py（灯效设备只读枚举 + 报告编码）
  server/httpd.py           标准库 ThreadingHTTPServer + REST + 静态白名单
  web/                      index.html / app.js / style.css（无构建步骤）
  history.py                遥测历史环形缓冲（重启后接上）
  tray/                     托盘、屏幕提示、按键文案
tools/                      只读观察与验证工具，产物落 tools/out（已 gitignore）
tests/                      见下一节
```

设计原则：**决策与执行分离**（调度是纯函数，副作用集中在执行层，所以调度能被单测覆盖）、
**能力如实降级**（通道没验证过就置灰 + 写明原因，不假装成功）、
**命令白名单**（没在 OEM 代码里确认存在的动作一律不发）、
**可恢复性优先**（不用锁文件、坏配置自动退回默认、启动期异常落 `fatal-*.log`）。

## 6. 关于硬件写入

- 所有硬件写入默认关闭，需要显式打开 `hardware.ec.allow_write`；
- 打开之后还有一道能力闸：**只做「语义已确认 + 可逆 + 带温度保险」的写入**，
  未验证过的寄存器在代码层面直接拒绝，配置开也不写；
- HTTP 上的写接口有两道检查：只接受本机面板页面发起的请求，且必须过配置闸；
- 连续失败会熔断关闭句柄，冷却后才复查；只读轮询固定限速；
- 涉及电池充电限制、固件刷写、UEFI 变量的操作**本项目不做**。

各寄存器的实测过程、结论来源与未验证项记录在 `notes/`，供后续开发和排障参考。

## 7. 开发

```bat
:: 逐个跑测试（每个文件自带用例计数，全绿才提交）
for %f in (tests\test_*.py) do runtime\python.exe %f
```

17 个测试文件覆盖调度决策、跑分算分、屏幕提示文案、历史缓冲、守护卡死判定、
会话事件、配置读写、单实例与端口、OEM 状态解析、寄存器解码、写入闸门、
风扇表解码、灯效编码、档位通道。改文案或改阈值时留意有几条用例专门盯着
「网页 / 托盘 / 弹窗用同一套模式词」，漏改一处就会红。

## 8. 路线图

- 精细功耗墙（`SET_OPERATING_MODE_DETAIL`）：走可逆验证后开放，服务端不做边界校验，下发前自己夹上下限；
- 风扇曲线写入：先补齐「换档再转储一次」这个前提，确认写的是正在生效的那张表；
- 键盘背光 / 灯条：走纯用户态 USB HID，协议已实现编码，缺一次可逆验证；
- 电池充电档写入、显示模式、独显直连等剩余开关：状态已能读，写入各需一次验证；
- 自适应与硬件档联动（`sync_ec_mode`）：依赖上面几项验证完成后默认开启；
- 界面：托盘图标与视觉细节。

## 9. 参考与致谢

EC/ACPI 交互的设计思路参考社区项目 [OpenRevo](https://github.com/faintonce/open-revo)（MIT），
本项目为独立实现，不含其源码或二进制。硬件行为记录部分参考了
[tongfang-gm7mg7p-re](https://github.com/ElDavoo/tongfang-gm7mg7p-re)、
[uniwill-laptop-mr](https://github.com/Terabinaryte/uniwill-laptop-mr)、
[mechrevo_ec_api](https://github.com/roj234/mechrevo_ec_api) 等社区逆向成果，
以及内核文档中的 [uniwill-laptop WMI 设备说明](https://docs.kernel.org/wmi/devices/uniwill-laptop.html)。
均为离线阅读，未执行其中的脚本；厂商私有映射与机型表不在本仓库分发范围内。

机械革命/同方（Uniwill）相关硬件结论来自本机实测与离线静态分析，
不保证适用于其它模具；在其它机型上开启写入前，请先只做只读验证。

## 10. 许可

MIT License，见 `LICENSE`。
