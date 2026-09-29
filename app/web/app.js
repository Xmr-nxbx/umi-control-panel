'use strict';

// 意图 → 档位的对应关系写在这里，面板上照着念，别让机主去猜。
// 全项目只有一套模式词：省电/均衡/流畅/性能。意图就是「自适应 / 锁定某模式」，
// 不再另造「办公/狂暴」——机主反馈过两套词并存根本对不上号。
// tier=null 表示不锁档位（自适应），标题就显示调度器当前实际落在哪一档。
const INTENTS = [
  { id: 'auto', name: '自适应', tier: null,
    desc: '不锁模式：面板按负载和温度，自己在 省电/均衡/流畅/性能 之间换' },
  { id: 'office', name: '锁定省电', tier: 'eco',
    desc: '一直用省电模式：最凉、最省电、风扇最安静，性能最低' },
  { id: 'balance', name: '锁定均衡', tier: 'bal',
    desc: '一直用均衡模式：日常够用，不会自己乱跳' },
  { id: 'turbo', name: '锁定性能', tier: 'perf',
    desc: '一直用性能模式：功耗拉满，风扇最响' },
];
const INTENT_BY_ID = {};
INTENTS.forEach((it) => { INTENT_BY_ID[it.id] = it; });
const TIERS = { perf: '性能', mid: '流畅', bal: '均衡', eco: '省电' };
const BOOST_TEXT = { 0: '禁用', 1: '启用', 2: '激进', 3: '高效', 4: '高效激进', 5: '保证频率' };
// 风扇模式字节取值来自 OEM 自己的枚举（MyFanCTLByteFlag）。这个字节就是本机硬件
// 模式的总开关：按一次实体键，它 0x10↔0xA0 的同时 PL1_SETTING_VALUE 75↔10、
// 风扇 PWM 表、TGP 整组跟着换（README 6.2）。
// 中文词表不在这里存第二份——统一从后端 meta.fan_mode_words 拿，
// 机主报过「网页一套词、弹窗一套词，对不上号」。
const FAN_KEY_FLAGS = ['Normal_Mode', 'User_Fan_HiMode', 'Turbo_Mode'];

let META = { cap_labels: {}, mode_labels: {}, tier_labels: {}, fan_mode_words: {} };
const fanModeWord = (flag) => {
  const w = META.fan_mode_words || {};
  if (!flag) return null;
  if (w[flag]) return w[flag];
  return flag.indexOf('User_Fan') === 0 ? (w.User_Fan || null) : null;
};
const hwModeWord = (m) => (m === 'auto' ? '自适应' : (TIERS[m] || null));
let lastState = null;
let benchWasRunning = false;
let benchTimer = null;   // 跑分进行中改成 1 秒刷一次，进度条才看得出在动
let histData = { samples: [], marks: [] };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) } : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || ('HTTP ' + res.status));
  return data;
}

let toastTimer = null;
function toast(text, bad) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = text;
  el.classList.toggle('bad', !!bad);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

// ---------- 自动探测硬件通道 ----------
// 以前要点「重新探测通道」才更新，机主点一次看到「自检通过」就不敢再动了，
// 也不知道下一次该什么时候点。现在改成：页面看得见就定时探，切回来也探一次，全部带防抖。
// 防抖是必须的：一次探测要把 EC 的 125 个寄存器按 2 秒一轮的限速读一遍，
// 快速来回切标签页要是每次都触发，等于让 EC 白忙，日志也会被刷满。
const PROBE_DEBOUNCE_MS = 1500;    // 切回页面后等这么久才探，期间再切走就取消
const PROBE_MIN_GAP_MS = 90000;    // 两次探测最少隔这么久
const PROBE_EVERY_MS = 180000;     // 页面一直开着的话，每 3 分钟探一次
const VISIBLE_DEBOUNCE_MS = 400;   // 切回页面后补数据的防抖
let probeTimer = null;
let visibleTimer = null;
let probing = false;
let lastProbeAt = 0;

function scheduleProbe() {
  clearTimeout(probeTimer);
  if (document.hidden || probing) return;
  probeTimer = setTimeout(autoProbe, PROBE_DEBOUNCE_MS);
}

async function autoProbe(say) {
  if (probing || document.hidden) return;
  const now = Date.now();
  if (now - lastProbeAt < PROBE_MIN_GAP_MS) return;
  probing = true;
  lastProbeAt = now;
  try {
    const r = await api('/api/ec/probe');
    if (say) toast('通道自检：' + ((r.detail || {}).reason || '完成'));
  } catch (e) {
    // 自动探测失败不打扰人：卡片上本来就会写通道状态，只有手点时才弹提示
    if (say) toast('通道自检失败：' + e.message, true);
  }
  probing = false;
  poll();
}

function renderIntents() {
  $('intents').innerHTML = INTENTS.map((it) => `
    <div class="intent ${lastState && lastState.intent === it.id ? 'active' : ''}" data-id="${it.id}">
      <div class="name">${it.name}</div><div class="desc">${it.desc}</div>
    </div>`).join('');
  document.querySelectorAll('.intent').forEach((el) => {
    el.onclick = async () => {
      try { await api('/api/intent', { intent: el.dataset.id }); toast('意图已设为 ' + el.textContent.trim()); poll(); }
      catch (e) { toast('设置失败：' + e.message, true); }
    };
  });
}

function renderPills(s) {
  const pills = [];
  const chs = (s.channels || []);
  chs.forEach((c) => {
    const blocked = (c.detail || {}).state === 'blocked';
    const cls = c.alive ? 'ok' : (blocked ? 'warn' : (c.detail && c.detail.enabled === false ? 'warn' : 'bad'));
    const text = c.alive ? '在线' : (blocked ? '需管理员' : '不可用');
    pills.push(`<span class="pill ${cls}">${esc(c.label)}：${text}</span>`);
  });
  const ec = s.power_caps || {};
  pills.push(`<span class="pill ${ec.epp_supported ? 'ok' : 'warn'}">EPP ${ec.epp_supported ? '支持' : '不支持'}</span>`);
  pills.push(`<span class="pill ${s.admin ? 'ok' : ''}">${s.admin ? '管理员' : '普通权限'}</span>`);
  if (s.throttle) pills.push('<span class="pill bad">太热了，正在压性能</span>');
  if (s.dwell_left > 0) {
    pills.push(`<span class="pill warn">刚升上来，先稳住 ${Math.round(s.dwell_left)} 秒再考虑降档</span>`);
  }
  if (s.pending) {
    // 「防抖」是工程词，机主看不懂。这里说清楚：想降档，但要连续观察一段时间才真降。
    pills.push(`<span class="pill warn">准备换到 `
      + `${esc((META.tier_labels || {})[s.pending.tier] || s.pending.tier)}，`
      + `再观察 ${Math.round(s.pending.in_s)} 秒（免得来回跳档）</span>`);
  }
  if (s.resume_left > 0) {
    pills.push(`<span class="pill warn">刚解锁屏幕，${Math.round(s.resume_left)} 秒内不降档</span>`);
  }
  if (s.guard_hits) pills.push(`<span class="pill ok">已拦下 ${s.guard_hits} 次误降档</span>`);
  if (s.external_hits) pills.push(`<span class="pill warn">外部改过 ${s.external_hits} 次设置</span>`);
  $('pills').innerHTML = pills.join('');
}

function meter(label, value, unit, pct, hot) {
  const p = Math.max(0, Math.min(100, pct == null ? 0 : pct));
  return `<div class="meter"><div class="label"><span>${esc(label)}</span>
    <span class="value">${value == null ? '--' : esc(value) + esc(unit)}</span></div>
    <div class="bar"><div class="fill${hot ? ' hot' : ''}" style="width:${p}%"></div></div></div>`;
}

function renderMeters(s) {
  const x = s.sensor || {};
  const g = x.gpu || {};
  const mem = x.mem || {};
  const pw = x.power || {};
  const baseMhz = (s.power_caps || {}).cpu_base_mhz;
  const cells = [
    meter('CPU 占用', x.cpu_pct, '%', x.cpu_pct),
    meter('CPU 频率', x.cpu_mhz, ' MHz',
          x.cpu_mhz && baseMhz ? Math.min(100, x.cpu_mhz / (baseMhz * 2) * 100) : null),
    meter('GPU 占用', x.gpu_pct, '%', x.gpu_pct),
    meter('CPU 温度', x.cpu_temp, '°C', x.cpu_temp, x.cpu_temp > 90),
    meter('GPU 温度', g.temp_c, '°C', g.temp_c, g.temp_c > 85),
    meter('GPU 功耗', g.power_w, 'W', g.power_w && g.power_limit_w ? g.power_w / g.power_limit_w * 100 : null),
    meter('内存', mem.used_gb, 'GB', mem.pct),
    meter('电池', pw.battery_pct >= 0 ? pw.battery_pct : (pw.on_ac ? '接电源' : '电池'), pw.battery_pct >= 0 ? '%' : '', pw.battery_pct),
    meter('空闲时间', Math.round(x.idle_s || 0), 's', Math.min(100, (x.idle_s || 0) / 2)),
  ];
  cells.push(`<div class="meter"><div class="label"><span>前台程序</span>
    <span class="value">${esc(x.foreground || '—')}</span></div></div>`);
  cells.push(`<div class="meter"><div class="label"><span>电源方案</span>
    <span class="value">${esc(s.scheme || '—')}</span></div></div>`);
  $('meters').innerHTML = cells.join('');
}

// 第二个风扇（GPU 侧）空闲时会停，读数只有几十 RPM，直接写「停转」比写「60 RPM」清楚
function fmtRpm(v) {
  if (v == null) return '?';
  return v < 200 ? '停转' : (v + ' RPM');
}

// 跑分剩余时间：说「1 分 40 秒」比说「100 秒」好懂
function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

function hwRow(k, v, ok) {  // 文案一律由调用方给：ok 只决定灰不灰。以前这里硬写「不可控」，
  // 结果 EC 明明在线、只是档位语义没确认，也被显示成「不可控」。
  return `<div class="hw-row"><span class="k">${esc(k)}</span>
    <span class="v${ok ? '' : ' na'}">${esc(v)}</span></div>`;
}

// 电池充电那三档（平衡/健康/长效）。档位名由后端给（meta.battery_mode_labels），
// 前端只负责排版；认不出来就把原始字节亮出来，让人能自己去对，不编一个名字。
function battText(hw) {
  const raw = hw.battery_mode_raw;
  const hex = raw != null ? `0x${Number(raw).toString(16).toUpperCase().padStart(2, '0')}` : null;
  const word = (META.battery_mode_labels || {})[hw.battery_mode];
  if (word) return hex ? `${word}（EC ${hex}）` : word;
  if (hex) return `未知（EC ${hex}，不是见过的三个值）`;
  return '未知';
}

function renderHardware(s) {
  const caps = s.capabilities || {};
  const hw = s.hardware || {};
  const verified = (cap) => (caps[cap] || {}).state === 'verified';
  const ecCh = (s.channels || []).find((c) => c.name === 'ec') || {};
  const ecAlive = ecCh.alive === true;
  const keyFlag = FAN_KEY_FLAGS.indexOf(hw.fan_mode_flag) >= 0 ? hw.fan_mode_flag : null;
  // 2026-09-30 全表差分（tools/ec_watch.py all）：实体键写的这个字节是硬件模式总开关，
  // PL1 75W↔10W、风扇 PWM 表、TGP 都是它的结果。所以「硬件模式」这一行敢下结论了，
  // 取值认不出来时照实写「未知」，不猜。
  const keyText = fanModeWord(hw.fan_mode_flag) || hw.fan_mode_flag
    || (ecAlive ? '未知' : '不可读');
  const hwMode = hwModeWord(hw.hw_mode);
  const plNote = hw.pl1_setting ? '' : '（出厂默认）';
  const rows = [
    hwRow('造物者模式按键', keyText + (keyFlag ? '' : '（非按键三态取值）'), ecAlive),
    hwRow('硬件模式', hwMode || (ecAlive
          ? `未知（风扇字节 ${hw.fan_mode_flag || '?'}）` : '不可读'), !!hwMode),
    hwRow('风扇转速', hw.fan_rpm != null
          ? (fmtRpm(hw.fan_rpm) + ' / ' + fmtRpm(hw.fan2_rpm)) : '未知', verified('fan.rpm')),
    hwRow('风扇占空比', hw.fan_duty_l != null ? (hw.fan_duty_l + '% / ' + (hw.fan_duty_r != null ? hw.fan_duty_r + '%' : '?'))
          : '未知', hw.fan_duty_l != null),
    hwRow('PL1 / PL2' + plNote, hw.pl1 != null ? (hw.pl1 + 'W / ' + (hw.pl2 != null ? hw.pl2 + 'W' : '?'))
          : '未知', verified('power_limit.read')),
    hwRow('电池（EC）', hw.battery_pct_ec != null
          ? (hw.battery_pct_ec + '% · ' + (hw.battery_temp_c != null ? hw.battery_temp_c + '°C' : '?')
             + (hw.battery_cycles != null ? ' · ' + hw.battery_cycles + ' 次循环' : ''))
          : '未知', hw.battery_pct_ec != null),
    // 「未设限」这个说法是错的：本机根本没有百分比这套机制（封顶走充电电压，
    // 而那个寄存器 host 写不住）。这两个字节恒为 0，照实说清楚，别让人以为
    // 「打开某个开关就能设 80%」。（README 6.11 第三节）
    hwRow('充电阈值', hw.charge_limit_up
          ? (hw.charge_limit_up + '% / 回落 ' + (hw.charge_limit_down || '?') + '%')
          : '本机不按百分比设限（寄存器恒为 0）', hw.charge_limit_up != null),
    // 2026-09-30 观察3 两条通道对齐后确认的：这两行不再是「候选」，敢下结论了。
    hwRow('电池充电档位', battText(hw), hw.battery_mode != null),
    hwRow('Win 键锁定', hw.win_key_locked == null ? (ecAlive ? '未知' : '不可读')
          : (hw.win_key_locked ? '已锁定' : '未锁定'), hw.win_key_locked != null),
    hwRow('机型标识', hw.project_id != null ? ('ProjectID ' + hw.project_id
          + (hw.module_id != null ? ' · Module ' + hw.module_id : '')) : '未知',
          hw.project_id != null),
    hwRow('数据来源', hw.source || '无', !!hw.source),
  ];
  $('hw').innerHTML = rows.join('');

  const canFan = (caps['fan.mode'] || {}).state === 'verified';
  const available = Object.keys(s.fan_modes || {});
  // 只放实体按键真正会到的那几态（GCUBridge 在跑时是 0x10↔0xA0 两态循环，
  // 半亮那一态也留着，面板点得到）；User_Fan_Level1~5 是自定义曲线的子档，
  // 放上来只会让面板看起来比实际能控的东西多。
  const flags = FAN_KEY_FLAGS.filter((f) => available.indexOf(f) >= 0);
  const lockLeft = (ecCh.detail || {}).fan_lock_left || 0;
  const lockBy = (ecCh.detail || {}).fan_lock_by;
  const owned = !!(ecCh.detail || {}).fan_user_owned;
  const fanButtons = canFan ? flags.map((f) => `
    <button data-fan="${f}" class="${hw.fan_mode_flag === f ? 'primary' : ''}">${esc(fanModeWord(f) || f)}</button>`).join('') : '';
  const canWrite = (caps['mode.write'] || {}).state === 'verified';
  const modes = ['office', 'balance', 'turbo'];
  $('hw-buttons').innerHTML = fanButtons
    + (canWrite ? modes.map((m) => `
    <button data-mode="${m}">${esc((META.mode_labels || {})[m] || m)}</button>`).join('') : '')
    + `<button class="ghost" id="btn-refresh-hw">立即自检通道</button>`;
  document.querySelectorAll('#hw-buttons button[data-fan]').forEach((b) => {
    b.onclick = async () => {
      try { const r = await api('/api/fan-mode', { flag: b.dataset.fan }); toast(r.detail || '已下发'); }
      catch (e) { toast('下发失败：' + e.message, true); }
      poll();
    };
  });
  document.querySelectorAll('#hw-buttons button[data-mode]').forEach((b) => {
    b.onclick = async () => {
      try { const r = await api('/api/mode', { mode: b.dataset.mode }); toast(r.detail || '已下发'); }
      catch (e) { toast('下发失败：' + e.message, true); }
      poll();
    };
  });
  $('btn-refresh-hw').onclick = () => {
    // 手点就是「我现在就要知道结果」，所以绕开自动探测的最短间隔，并且要弹提示
    lastProbeAt = 0;
    autoProbe(true);
  };
  const mqCh = (s.channels || []).find((c) => c.name === 'mqtt') || {};
  const why = (c) => (c.detail || {}).reason || '未探测';
  const hints = [];
  if (lockLeft) hints.push(`${lockBy || '人工'}优先，${Math.round(lockLeft)} 秒内面板不自动改硬件模式`);
  else if (owned) hints.push('当前是低功耗档（自定义曲线），面板不会自动改（点上面的按钮可接管）');
  // 这一态不是「只改风扇」：全表差分抓到 PL1 被压到 10W，满载实测慢 23~26%（README 6.4）
  if ((hw.fan_mode_flag || '').indexOf('User_Fan') === 0) {
    hints.push('⚠ 这一态把功耗墙压到 10W，满载实测慢 23~26%，换来的是安静和低温');
  }
  if (!canWrite) hints.push('OEM 档位不可写：' + ((ecCh.detail || {}).write_reason || why(ecCh)));
  if (!canFan) hints.push('硬件模式不可写：' + ((ecCh.detail || {}).fan_mode_reason || why(ecCh)));
  $('hw-hint').textContent = hints.join('；');
  $('hw-hint').classList.toggle('err', !canWrite && !canFan);
}

// GCUBridge 报上来的开关状态。这一块**只读**：语义还没逐条验证过，
// 先让机主看得见「OEM 自己认为现在是什么状态」，再谈写（README 6.7）。
function onOff(v, onWord, offWord) {
  return v == null ? '未知' : (v ? onWord : offWord);
}

function renderOem(s) {
  const box = $('oem');
  if (!box) return;
  const hint = $('oem-hint');
  const mqCh = (s.channels || []).find((c) => c.name === 'mqtt') || {};
  const oem = (s.hardware || {}).oem;
  if (!mqCh.alive || !oem) {
    box.innerHTML = [hwRow('GCUBridge', '未连接', false),
      hwRow('OEM 状态', '拿不到（要 GCUService 在跑，且 clientId 没被别人占用）', false)].join('');
    if ($('oem-buttons')) $('oem-buttons').innerHTML = '';   // 通道掉了，按钮也得跟着收走
    hint.classList.add('err');
    hint.textContent = '原因：' + ((mqCh.detail || {}).reason || '未探测')
      + '。这一卡片只读，连上后会自动填。';
    return;
  }
  hint.classList.remove('err');

  const lim = oem.limits || {};
  const limText = lim.pl1_min != null
    ? `PL1 ${lim.pl1_min}~${lim.pl1_max}W · PL4 ≤${lim.pl4_max}W · TGP ${lim.tgp_min}~${lim.tgp_max} · Boost ${lim.boost_min}~${lim.boost_max} · GPU 目标温度 ${lim.gpu_temp_min}~${lim.gpu_temp_max}°C`
    : '未报';
  // 键盘背光：Keyboard/Status.powerStatus 才是真开关（实测报 Off 的时候灯确实是灭的），
  // Setting/Status.SingleColorKBBL 是另一件事（单色背光这个功能支不支持/开没开），
  // 两个都摆出来，不合并成一个会骗人的值。
  const kbPower = oem.kb_power_on == null ? '未知' : (oem.kb_power_on ? '开' : '关');
  const kbText = `${kbPower} · 亮度 ${oem.kb_brightness_ac != null ? oem.kb_brightness_ac : '?'} 档（电池 ${oem.kb_brightness_dc != null ? oem.kb_brightness_dc : '?'} 档）`
    + (oem.kb_effect != null ? ` · 灯效 ${oem.kb_effect} 速度 ${oem.kb_speed != null ? oem.kb_speed : '?'}` : '')
    + (oem.kb_controller ? ` · ${oem.kb_controller}` : '');
  const barText = oem.lightbar_on == null ? '未知'
    : ((oem.lightbar_on ? '开' : '关')
       + (oem.lightbar_brightness != null ? ` · 亮度 ${oem.lightbar_brightness}` : ''));
  // 2026-09-30 实测：开关字段说「直连已开」，DGpu 字段却是 NV_CTRL_PANEL_AUTOSELECT。
  // 更可能是两层不同的东西（直连开关 vs NVIDIA 控制面板的输出偏好），没验证前两个都报。
  const muxRaw = oem.dgpu_raw ? ` · NVIDIA 侧 ${oem.dgpu_raw}` : '';
  const muxText = oem.mux_support === false ? '本机不支持（OEM 报 NotSupport）'
    : (onOff(oem.mux_on, '已开（独显直连）', '关（混合输出）') + muxRaw);

  box.innerHTML = [
    hwRow('Win 键锁定', onOff(oem.win_key_locked, '已锁定', '未锁定')
          + '（与 EC 的 STAUTS_BYTE 已对上号）', oem.win_key_locked != null),
    hwRow('触摸板', onOff(oem.touchpad_on, '开', '关')
          + '（触摸板上还有个实体拨动开关；OEM 也有 TOUCHPAD_TOGGLE_ON/OFF，'
          + '但没验证过会不会被实体开关盖掉，所以面板不写）', oem.touchpad_on != null),
    hwRow('键盘背光', kbText, oem.kb_power_on != null),
    hwRow('单色背光功能', onOff(oem.kb_single_color_on, '开', '关'), oem.kb_single_color_on != null),
    hwRow('顶灯条', barText, oem.lightbar_on != null),
    hwRow('USB 关机充电', onOff(oem.usb_charger_on, '开', '关'), oem.usb_charger_on != null),
    hwRow('OSD 提示', oem.osd_hidden == null ? '未知' : (oem.osd_hidden ? '隐藏' : '显示'),
          oem.osd_hidden != null),
    hwRow('Fn 键 / NumPad', `${onOff(oem.fn_locked, '锁', '未锁')} / ${onOff(oem.numpad_locked, '锁', '未锁')}`,
          oem.fn_locked != null),
    hwRow('Fn+F1 快捷键', onOff(oem.fn_hotkey_on, '开', '关'), oem.fn_hotkey_on != null),
    hwRow('独显直连', muxText, oem.mux_on != null),
    hwRow('显示模式', (oem.display_mode || '未知')
          + ` · 色彩管理 ${onOff(oem.display_feature_on, '开', '关')}`, !!oem.display_mode),
    hwRow('OEM 档位读数', (oem.power_mode_raw != null ? `PowerMode ${oem.power_mode_raw}` : 'PowerMode 未知')
          + (oem.profile_name ? ` · ${oem.profile_name}` : '')
          + (oem.fan_table ? ` · 风扇表 ${oem.fan_table}` : ''), oem.power_mode_raw != null),
    hwRow('OEM 允许范围', limText, lim.pl1_min != null),
    hwRow('AC 恢复', oem.ac_recovery_support
          ? onOff(oem.ac_recovery_on, '开', '关') : '不支持（OEM 报 NotSupport）', true),
  ].join('');

  // Win 键锁定是目前唯一做完可逆验证的 OEM 写操作（2026-09-30 01:43：下发 UNLOCK
  // 后 EC 的 ADDR_STAUTS_BYTE 1→0，再下发 LOCK 又回到 1，EC 与 Setting/Status 两条
  // 通道读数一致），所以整张卡片只给它一个按钮，其余照旧只读。
  const btns = $('oem-buttons');
  if (btns) {
    const canWin = ((s.capabilities || {})['winkey.write'] || {}).state === 'verified'
      && oem.win_key_locked != null;
    btns.innerHTML = canWin
      ? `<button id="btn-winkey">${oem.win_key_locked ? '解锁 Win 键' : '锁定 Win 键'}</button>` : '';
    const b = $('btn-winkey');
    if (b) {
      b.onclick = async () => {
        const act = oem.win_key_locked ? 'WINKEY_UNLOCK' : 'WINKEY_LOCK';
        try { const r = await api('/api/action', { action: act }); toast(r.detail || '已下发'); }
        catch (e) { toast('下发失败：' + e.message, true); }
        poll();
      };
    }
  }

  hint.textContent = '数据来自 GCUBridge 的 Setting/Status · HidLightbar/Status · Fan/Status · Keyboard/Status'
    + '，全是 OEM 自己报的读数。只有做过「下发 → EC 回读 → 还原」可逆验证的开关才给按钮，'
    + '其余一律只读；认不出来的值照实写「未知」，不猜。'
    + '「OEM 允许范围」是 Fan/Status 里 OEM 自己写的上下限，以后任何写入都拿它当护栏。';
}

// ---------- 风扇曲线（EC 直读，只读） ----------
// 表布局来自同源机型反编译出的 SetEcFanTable/GetEcFanTable（README 6.11）：
// 升温点 mem[base+i-1]、降温点 mem[base+0x11+i]、占空比 mem[base+0x20+i]/2。
// 这块**按需**取数：一轮近百次读约 3 秒，不进 2 秒轮询，页面不点就不打 EC。
let curveData = null;
let curveLoading = false;

const CURVE_WORD = { CPU: 'CPU', GPU: 'GPU（独显）' };

function curveTable(which, points) {
  const rows = (points || []).map((p) => {
    const cls = p.mailbox ? 'mailbox' : (p.complete ? (p.sentinel ? 'sentinel' : '') : 'na');
    const duty = p.mailbox ? '信箱' : (p.duty_pct != null ? p.duty_pct + '%' : '—');
    const up = p.up_t != null ? (p.sentinel ? '0xFF 哨兵' : p.up_t) : '—';
    return `<tr class="${cls}"><td>${p.id}</td><td>${esc(up)}</td>
      <td>${p.down_t != null ? p.down_t : '—'}</td><td>${esc(duty)}</td></tr>`;
  }).join('');
  return `<table class="curve"><caption>${esc(CURVE_WORD[which] || which)}</caption>
    <thead><tr><th>点</th><th>升温 °C</th><th>降温 °C</th><th>占空比</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

const triText = (v, on, off) => (v == null ? '读不到' : (v ? on : off));

function renderFanCurve() {
  const box = $('fan-curve');
  const hint = $('fan-curve-hint');
  if (!box) return;
  if (!curveData) return;                       // 还没点过：留着 index.html 里那句说明
  if (!curveData.ok && !curveData.tables) {
    box.innerHTML = '';
    hint.classList.add('err');
    hint.textContent = '读不到：' + (curveData.reason || '未知原因');
    return;
  }
  hint.classList.remove('err');
  const t = curveData.tables || {};
  const ctx = curveData.context || {};
  box.innerHTML = `<div class="curve-ctx">`
    + hwRow('硬件模式', hwModeWord(ctx.hw_mode) || `未知（风扇字节 ${ctx.fan_mode_flag || '?'}）`,
            !!ctx.hw_mode)
    + hwRow('CPU/GPU 分表', triText(ctx.split_tables, '开（两张表都在用）', '关'),
            ctx.split_tables != null)
    + hwRow('写表括号位', triText(ctx.bracket, '正常（1）', '拉低中：有人正在写表'),
            ctx.bracket != null)
    + hwRow('AP 存在位', triText(ctx.ap_exist, '1（EC 不会清零功耗墙）', '0'),
            ctx.ap_exist != null)
    + `</div><div class="curve-wrap">${curveTable('CPU', t.CPU)}${curveTable('GPU', t.GPU)}</div>`;
  const bits = [`本轮读了 ${curveData.reads} 次、写了 ${curveData.writes} 次`,
                `用时 ${curveData.elapsed_s} 秒`];
  if (curveData.ts) bits.push(`读于 ${new Date(curveData.ts * 1000).toLocaleTimeString()}`);
  if (curveData.cached) bits.push(`这是 ${curveData.age_s} 秒前的缓存`);
  if (curveData.writes_during) {
    bits.push(`转储期间通道另有 ${curveData.writes_during} 次写入（自动跟随在改风扇字节），`
              + '所以这不是同一时刻的快照');
  }
  if (curveData.incomplete) bits.push(`有 ${curveData.incomplete} 个点没读全`);
  hint.textContent = bits.join(' · ') + '。'
    + '第 15 点的 0xFF 是「到此为止」的哨兵，不是温度；降温值比升温值高是厂商自己的写法，不是解码错。'
    + 'GPU 表最后三格被厂商借去当信箱（写表时用来传模式和握手字节），所以显示「信箱」而不是占空比。'
    + curveData.note;
}

async function loadFanCurve(say) {
  if (curveLoading) return;
  curveLoading = true;
  const btn = $('btn-fan-curve');
  btn.disabled = true;
  btn.textContent = '读取中（约 3 秒）…';
  try {
    curveData = await api('/api/fan-curve');
    if (say) {
      const head = `风扇表读到了：${curveData.reads} 次读、${curveData.writes} 次写、`
        + `${curveData.elapsed_s} 秒`;
      toast(curveData.ok
        ? (curveData.reason ? `${head}（${curveData.reason}）` : head)
        : ('读取不完整：' + (curveData.reason || '未知原因')), !curveData.ok);
    }
  } catch (e) {
    // 读失败不清掉上一次的结果：机主宁可看着旧表也不想看到卡片突然空掉。
    // 旧表有多旧，提示行里写着（缓存几秒前 / 什么时候读的）。
    if (say) toast('读取失败：' + e.message, true);
  }
  curveLoading = false;
  btn.disabled = false;
  btn.textContent = curveData ? '重新读取（约 3 秒）' : '读取曲线（约 3 秒）';
  renderFanCurve();
  poll();     // 读成功会把 fan.curve 能力点成「可用」，顺手刷新能力矩阵
}

function renderCaps(s) {
  const caps = s.capabilities || {};
  const labels = META.cap_labels || {};
  const shown = ['mode.read', 'mode.write', 'power_limit.read', 'power_limit.write',
                 'fan.rpm', 'fan.mode', 'fan.curve', 'fan.curve.write', 'ec.temp',
                 'battery.limit',
                 'battery.mode.write', 'winkey.write', 'gpu.mux', 'lighting.rgb'];
  const stateText = { verified: '可用', unknown: '待验证', blocked: '受限',
                      missing: '缺本机配置', unsupported: '不支持' };
  $('caps').innerHTML = shown.map((cap) => {
    const c = caps[cap] || { state: 'unsupported', channel: '-' };
    return `<div class="cap"><span>${esc(labels[cap] || cap)}</span>
      <span class="st ${c.state}">${stateText[c.state] || c.state} · ${esc(c.channel || '—')}</span></div>`;
  }).join('');
}

function renderTier(s) {
  const tierName = s ? (TIERS[s.tier] || s.tier) : '--';
  const it = INTENT_BY_ID[s && s.intent] || INTENT_BY_ID.auto;
  const locked = !!(s && s.intent && s.intent !== 'auto');
  // 标题必须同时说清「你选了什么」和「机器现在在哪一档」：
  // 之前只显示档位，机主选了自适应却看到标题一会儿流畅一会儿性能，以为按键坏了。
  $('tier-intent').textContent = s
    ? `控制意图：${it.name}${locked ? '' : '（不锁模式，按负载自动换）'}`
    : '控制意图：--';
  $('tier-now').textContent = locked ? `锁定 · ${tierName}` : tierName;
  $('tier-now').className = 'tier-now' + (locked ? ' locked' : '');
  const x = (s && s.sensor) || {};
  $('tier-clock').textContent = x.cpu_mhz ? (x.cpu_mhz + ' MHz') : '-- MHz';
  $('tier-reason').textContent = s ? (s.reason || '') : '等待数据…';
  $('live-dot').className = 'dot' + (s && Date.now() / 1000 - s.ts < 8 ? ' on' : '');
  $('foot-status').textContent = s
    ? `采样 ${new Date(s.ts * 1000).toLocaleTimeString()} · 已运行 ${Math.floor((s.uptime_s || 0) / 60)} 分钟 · 活动方案 ${(s.applied || {}).scheme ? (s.applied.scheme.slice(0, 8)) : '--'}`
    : '未取到数据';
  $('intent-hint').textContent = locked
    ? `已锁定「${TIERS[it.tier] || ''}」模式：不会自己换档，标题一直显示这一档；温度保护仍然生效。`
    : '自适应：升档快、降档慢。标题显示的是现在实际用的模式，会随负载变化——'
      + '进性能模式后有 300 秒驻留期，不会因为一时低负载就掉回去。';
}

async function poll() {
  if (document.hidden) return;   // 页面看不见就不发请求，切回来会立刻补一次
  try {
    const s = await api('/api/state');
    lastState = s;
    META = Object.assign(META, s.meta || {});
    renderTier(s); renderPills(s); renderIntents(); renderProfiles(s); renderMeters(s);
    renderHardware(s); renderOem(s); renderCaps(s);
    if ((s.bench || {}).running || benchWasRunning) loadBench();
    benchWasRunning = !!(s.bench || {}).running;
  } catch (e) {
    $('foot-status').textContent = '取数失败：' + e.message;
  }
}

function renderBench(v) {
  const job = (v && v.job) || {};
  const verdict = (v && v.verdict) || null;
  const vEl = $('bench-verdict');
  if (verdict && verdict.reason) {
    vEl.hidden = false;
    vEl.className = 'verdict ' + (verdict.effective ? 'ok' : 'bad');
    vEl.textContent = (verdict.effective ? '实测结论：' : '实测警告：') + verdict.reason;
  } else {
    vEl.hidden = true;
  }
  const running = !!job.running;
  const done = !running && job.step === '完成';
  clearTimeout(benchTimer);
  if (running) benchTimer = setTimeout(loadBench, 1000);
  $('bench-run').hidden = !(running || done);
  $('btn-bench-current').disabled = running;
  $('btn-bench-compare').disabled = running;
  if (running || done) {
    $('bench-fill').style.width = (done ? 100 : (job.pct || 0)) + '%';
    $('bench-step').textContent = done
      ? `完成（用了 ${fmtDur(job.elapsed_s || 0)}），成绩见下表`
      : `${job.pct || 0}% · ${job.step || ''}`;
    $('bench-eta').textContent = done
      ? '' : `已用 ${fmtDur(job.elapsed_s || 0)}${job.eta_s != null ? ` · 约剩 ${fmtDur(job.eta_s)}` : ''}`;
  }
  const rows = (v && v.tiers) || [];
  const base = (v && v.baseline) || {};
  if (!rows.length) {
    $('bench-table').innerHTML = '';
    $('bench-hint').textContent = '还没有跑分记录。点「四档逐一对比」，面板会依次锁到省电/均衡/流畅/性能档各测一次，'
      + '给出以均衡档为 100 的指数——差距一眼就能看出来。';
    return;
  }
  const now = lastState ? lastState.tier : null;
  // 分数是浮点原始值（CoreMark 能带 6 位小数），直接铺出来是 28531.883342 这种东西——
  // 看的人分不清哪一位有意义。按列各自定小数位，空值一律显示长破折号。
  const cell = (v, digits, unit) => (v == null || v === ''
    ? '—' : (typeof v === 'number' ? v.toFixed(digits == null ? 0 : digits) : esc(v)) + (unit || ''));
  $('bench-table').innerHTML = `<table class="bench"><thead><tr>
      <th>档位</th><th>总分</th>
      <th>全核压缩<br><small>MB/s</small></th>
      <th>单核压缩<br><small>MB/s</small></th>
      <th>核心计算<br><small>CoreMark</small></th>
      <th>短任务<br><small>ms</small></th>
      <th>实测频率</th><th>最高温</th><th>睿频</th><th>电源方案</th></tr></thead><tbody>
    ${rows.map((r) => {
      const rec = r.record || {}; const sc = r.score || {}; const pf = rec.profile || {};
      return `<tr class="${rec.tier === now ? 'now' : ''}">
        <td>${esc(r.label || r.tier)}${rec.tier === now ? ' <em>当前</em>' : ''}</td>
        <td class="score">${cell(sc.overall, 1)}</td>
        <td>${cell(rec.all_mb_s, 0)}</td><td>${cell(rec.core_mb_s, 1)}</td>
        <td>${cell(rec.coremark, 0)}</td>
        <td>${cell(rec.burst_ms, 0)}</td>
        <td>${cell(rec.clock_mhz, 0, ' MHz')}</td>
        <td>${cell(rec.temp_after_c, 0, '°C')}</td>
        <td>${esc(BOOST_TEXT[pf.boost] || '—')}</td><td>${esc(pf.scheme || '—')}</td></tr>`;
    }).join('')}</tbody></table>`;
  const baseLine = base.all_mb_s || base.core_mb_s || base.burst_ms
    ? `基准（=100 分）：全核 ${cell(base.all_mb_s, 0)} MB/s、单核 ${cell(base.core_mb_s, 1)} MB/s、`
      + `核心计算 ${cell(base.coremark, 0)}、短任务 ${cell(base.burst_ms, 0)} ms。`
    : '还没有基准分：跑一次「四档逐一对比」，面板会以均衡档为 100 分重新钉一个基准。';
  $('bench-hint').textContent = baseLine
    + '负载是开源工具的真实工作量：zstd 压缩（数据处理，全核/单核各一次）+ CoreMark（纯计算）+ 短任务延迟。'
    + '总分越高越快；「短任务」是降频后来一下活的耗时，直接对应亮屏回来点东西卡不卡。';
}

async function loadBench() {
  try { renderBench(await api('/api/bench')); } catch (e) { /* 服务未就绪时静默 */ }
}

async function startBench(mode) {
  const tip = mode === 'compare'
    ? '对比跑分会依次锁到省电/均衡/流畅/性能档，每档跑一遍 zstd 压缩 + CoreMark + 短任务延迟，'
      + '全程约 3 分钟（进度条会一直走并显示剩余时间），风扇会明显转起来；结束后自动回到当前档位。现在开始？'
    : '会在当前档位上跑一遍完整负载，约 40 秒。现在开始？';
  if (!confirm(tip)) return;
  try {
    await api('/api/bench', { mode });
    toast(mode === 'compare' ? '开始四档对比跑分' : '开始跑分');
  } catch (e) { toast('跑分启动失败：' + e.message, true); }
  loadBench();
}

// ---------- 历史曲线（纯 canvas，不引任何图表库：面板必须断网可用）----------
const TIER_COLOR = { perf: '#ff5d6c', mid: '#ffb648', bal: '#35e0d8', eco: '#4ade80' };
const SERIES = {
  temp: [
    { key: 'cpu_temp', label: 'CPU 温度', axis: 'L', unit: '°C', color: '#ff5d6c' },
    { key: 'cpu_mhz', label: '实际频率', axis: 'R', unit: 'MHz', color: '#ffb648' },
  ],
  load: [
    { key: 'cpu_pct', label: 'CPU 占用', axis: 'L', unit: '%', color: '#35e0d8', max: 100 },
    { key: 'gpu_pct', label: 'GPU 占用', axis: 'L', unit: '%', color: '#4a9df8', max: 100 },
    { key: 'fan_rpm', label: '风扇转速', axis: 'R', unit: ' RPM', color: '#4ade80' },
  ],
};

function niceMax(v, floor) {
  const x = Math.max(v || 0, floor || 1);
  const step = Math.pow(10, Math.floor(Math.log10(x)));
  return Math.ceil(x / (step / 2)) * (step / 2);
}

function drawChart(canvasId, series, data) {
  const cv = $(canvasId);
  if (!cv) return;
  const rows = data.samples || [];
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 900;
  const h = cv.clientHeight || 152;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const box = document.getElementById(canvasId.replace('chart-', 'legend-'));
  if (rows.length < 3) {
    g.fillStyle = '#7c8ba1'; g.font = '12px system-ui';
    g.fillText('正在积累数据（每 5 秒一个点，已采到 %d 个）…', 12, h / 2);
    if (box) box.innerHTML = '';
    return;
  }
  const padL = 40; const padR = 46; const padT = 10; const padB = 16;
  const iw = w - padL - padR; const ih = h - padT - padB;
  const t0 = rows[0].t; const t1 = rows[rows.length - 1].t || t0 + 1;
  const px = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * iw;
  const span = {};
  series.forEach((s) => {
    const vals = rows.map((r) => r[s.key]).filter((v) => v != null);
    if (!vals.length) { span[s.axis] = { lo: 0, hi: s.max || 10 }; return; }
    const hi = s.max || niceMax(Math.max(...vals), s.key.indexOf('temp') >= 0 ? 60 : 10);
    const lo = s.max ? 0 : Math.max(0, Math.floor(Math.min(...vals) / 10) * 10);
    span[s.axis] = span[s.axis] || { lo: Infinity, hi: -Infinity };
    span[s.axis].lo = Math.min(span[s.axis].lo, lo);
    span[s.axis].hi = Math.max(span[s.axis].hi, hi);
  });
  const py = (axis, v) => {
    const sc = span[axis] || { lo: 0, hi: 1 };
    const k = (v - sc.lo) / Math.max(1e-6, sc.hi - sc.lo);
    return padT + ih - k * ih;
  };
  // 网格 + 两侧刻度
  g.strokeStyle = '#232c3b'; g.fillStyle = '#7c8ba1'; g.font = '10px system-ui'; g.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = padT + (ih / 3) * i;
    g.beginPath(); g.moveTo(padL, y); g.lineTo(w - padR, y); g.stroke();
    const lf = span.L || { lo: 0, hi: 1 }; const rf = span.R || { lo: 0, hi: 1 };
    g.textAlign = 'right';
    g.fillText(Math.round(lf.hi - ((lf.hi - lf.lo) / 3) * i), padL - 5, y + 3);
    if (span.R) {
      g.textAlign = 'left';
      g.fillText(Math.round(rf.hi - ((rf.hi - rf.lo) / 3) * i), w - padR + 5, y + 3);
    }
  }
  // 档位切换点
  (data.marks || []).forEach((m) => {
    if (m.t < t0 || m.t > t1) return;
    const x = px(m.t);
    g.strokeStyle = TIER_COLOR[m.tier] || '#7c8ba1';
    g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x, padT); g.lineTo(x, padT + ih); g.stroke();
    g.setLineDash([]);
  });
  // 曲线
  series.forEach((s) => {
    g.strokeStyle = s.color; g.lineWidth = 1.6; g.beginPath();
    let pen = false; let last = null;
    rows.forEach((r) => {
      const v = r[s.key];
      if (v == null) { pen = false; return; }
      const x = px(r.t); const y = py(s.axis, v);
      if (!pen) { g.moveTo(x, y); pen = true; } else { g.lineTo(x, y); }
      last = v;
    });
    g.stroke();
    s._cur = last;
  });
  // 时间轴
  g.fillStyle = '#7c8ba1'; g.textAlign = 'left';
  g.fillText(new Date(t0 * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), padL, h - 4);
  g.textAlign = 'right';
  g.fillText(new Date(t1 * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), w - padR, h - 4);
  if (box) {
    box.innerHTML = series.map((s) => {
      const vals = rows.map((r) => r[s.key]).filter((v) => v != null);
      const lo = vals.length ? Math.min(...vals) : null;
      const hi = vals.length ? Math.max(...vals) : null;
      return `<span><i style="background:${s.color}"></i>${esc(s.label)} `
        + `<b>${s._cur == null ? '—' : Math.round(s._cur) + esc(s.unit)}</b> `
        + `<span style="opacity:.7">（区间 ${lo == null ? '—' : Math.round(lo) + esc(s.unit)}`
        + ` ~ ${hi == null ? '—' : Math.round(hi) + esc(s.unit)}）</span></span>`;
    }).join('');
  }
}

function renderProfiles(s) {
  const meta = (s.meta || {}).sched_profiles || {};
  const names = ['quiet', 'standard', 'performance'].filter((n) => meta[n]);
  const cur = s.sched_profile || 'standard';
  $('profile-buttons').innerHTML = names.map((n) => `
    <button data-profile="${n}" class="${n === cur ? 'primary' : ''}">${esc(meta[n].label)}</button>`).join('');
  document.querySelectorAll('#profile-buttons button').forEach((b) => {
    b.onclick = async () => {
      try {
        const r = await api('/api/sched-profile', { name: b.dataset.profile });
        toast(r.ok ? `${meta[b.dataset.profile].label}：${r.detail}` : `没改成：${r.detail}`, !r.ok);
      } catch (e) { toast('设置失败：' + e.message, true); }
      poll();
    };
  });
  const d = (meta[cur] || {}).desc;
  $('profile-desc').textContent = d || '';
  $('profile-desc').hidden = !d;
}

async function loadHistory() {
  if (document.hidden) return;
  try {
    const d = await api('/api/history');
    histData = d;
    drawChart('chart-temp', SERIES.temp, d);
    drawChart('chart-load', SERIES.load, d);
    const n = (d.samples || []).length;
    $('hist-range').textContent = n ? `最近 ${Math.round(((d.samples[d.samples.length - 1].t - d.samples[0].t) / 60) || 0)} 分钟 · ${n} 个点` : '暂无数据';
  } catch (e) { /* 服务未就绪时静默 */ }
}

async function loadLogs() {
  if (document.hidden) return;
  try {
    const d = await api('/api/logs?n=120');
    $('logs').textContent = (d.lines || []).join('\n') || '（暂无日志）';
    $('logs').scrollTop = $('logs').scrollHeight;
  } catch (e) {
    $('logs').textContent = '日志读取失败：' + e.message;
  }
}

$('btn-refresh-logs').onclick = loadLogs;
$('btn-diag').onclick = async () => {
  // 机主不用截图、也不用描述「我点了什么」：体检结果 + 最近日志一次拷走
  try {
    const h = await api('/api/health');
    const logs = await api('/api/logs?n=40');
    const text = `${h.text}\n\n—— 最近日志 ——\n${(logs.lines || []).join('\n')}`;
    let copied = false;
    try { await navigator.clipboard.writeText(text); copied = true; }
    catch (e) { /* http 页面或非安全上下文会被拒绝，下面退回手动选中文本 */ }
    if (!copied) {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand('copy');
      ta.remove();
    }
    toast(copied ? '诊断信息已复制，直接粘给助手就行' : '剪贴板不可用，已下载 umi-diag.txt');
    if (!copied) {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
      a.download = 'umi-diag.txt';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    }
  } catch (e) { toast('取诊断信息失败：' + e.message, true); }
};
$('btn-bench-current').onclick = () => startBench('current');
$('btn-bench-compare').onclick = () => startBench('compare');
// 风扇曲线不自动读：一轮近百次读约 3 秒，只有机主点了才打 EC
$('btn-fan-curve').onclick = () => loadFanCurve(true);
$('btn-stop').onclick = async () => {
  if (!confirm('确定停止面板服务？停止后自适应调度与掉档守护都会失效。')) return;
  try { await api('/api/shutdown', {}); toast('服务已停止'); }
  catch (e) { toast('停止失败：' + e.message, true); }
};

renderIntents();
poll();
loadLogs();
loadBench();
loadHistory();
setInterval(poll, 2000);
setInterval(loadLogs, 10000);
setInterval(loadHistory, 5000);
setInterval(scheduleProbe, PROBE_EVERY_MS);
// 切回这个页面时：立刻补一次数据，再顺带探一次通道（都带防抖）。
// 页面看不见时什么都不发——面板是常驻的，机主可能几天不看一眼，
// 没必要在后台一直打 HTTP 和读 EC 寄存器。
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  clearTimeout(visibleTimer);
  visibleTimer = setTimeout(() => { poll(); loadBench(); loadHistory(); scheduleProbe(); },
                            VISIBLE_DEBOUNCE_MS);
});
// 曲线是按像素画的，窗口宽度一变就要重画（不重新拉数据）
window.addEventListener('resize', () => {
  drawChart('chart-temp', SERIES.temp, histData);
  drawChart('chart-load', SERIES.load, histData);
});
