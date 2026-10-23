'use strict';

// 意图 → 档位的对应关系写在这里，面板上照着念，别让用户去猜。
// 全项目只有一套模式词：省电/均衡/流畅/性能。意图就是「自适应 / 锁定某模式」，
// 不再另造「办公/狂暴」——用户反馈过两套词并存根本对不上号。
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
// 风扇 PWM 表、TGP 整组跟着换（notes/hardware-channels.md 6.2）。
// 中文词表不在这里存第二份——统一从后端 meta.fan_mode_words 拿，
// 用户报过「网页一套词、弹窗一套词，对不上号」。
const FAN_KEY_FLAGS = ['Normal_Mode', 'User_Fan_HiMode', 'Turbo_Mode'];

// 键盘灯效：值就是 OEM 的 RGBKB_Effect 名字；显示名跟着 Creator Center 的图标走。
// Keyboard/Status 报的是 ITE 固件码，不是这些名字，所以两边各留一张表。
const KB_EFFECTS = [
  { id: 'Single', name: '常亮' },
  { id: 'Breathing', name: '呼吸' },
  { id: 'Wave', name: '波浪' },
  { id: 'Rainbow', name: '彩虹' },
  { id: 'Mix', name: '混色' },
  { id: 'Flash', name: '闪烁' },
  { id: 'Music', name: '音乐' },
];
const KB_EFFECT_NAME_BY_CODE = {
  1: 'Single', 2: 'Breathing', 3: 'Wave', 5: 'Rainbow', 18: 'Flash', 19: 'Mix', 34: 'Music',
};
// 逆向仓库 HIDKeyboard.Translate_ITE_SpeedValue：界面 0..4 → 固件 10/7/5/3/1
const KB_SPEED_TO_UI = { 10: 0, 7: 1, 5: 2, 3: 3, 1: 4 };
const KB_DIRECTION_BY_CODE = { 0: 'None', 1: 'LeftRight', 2: 'RightLeft' };
const KB_EFFECT_OPTS = {
  Single: { colors: 4, bright: true, speed: false, dir: 'none' },
  Breathing: { colors: 7, bright: true, speed: true, dir: 'none' },
  Wave: { colors: 7, bright: true, speed: true, dir: 'lr' },
  Rainbow: { colors: 0, bright: true, speed: false, dir: 'none' },
  Mix: { colors: 7, bright: true, speed: true, dir: 'none' },
  Flash: { colors: 7, bright: true, speed: true, dir: 'all' },
  Music: { colors: 4, bright: false, speed: false, dir: 'none' },
};
// 灯条无可靠状态回声，写入未经可逆验证，因此仅展示选项。
const LB_EFFECTS = [
  { id: 'Single', name: '单色', colors: 1, bright: true, speed: false, dir: 'none' },
  { id: 'Breathing', name: '呼吸', colors: 7, bright: true, speed: true, dir: 'none' },
  { id: 'Wave', name: '波浪', colors: 7, bright: true, speed: true, dir: 'none' },
  { id: 'Rainbow', name: '彩虹', colors: 0, bright: true, speed: false, dir: 'none' },
  { id: 'Mix', name: '混色', colors: 7, bright: true, speed: true, dir: 'none' },
  { id: 'Impact', name: '碰撞', colors: 7, bright: true, speed: true, dir: 'none' },
  { id: 'Thinking', name: '思维', colors: 7, bright: true, speed: true, dir: 'none' },
  { id: 'Raindrop', name: '雨滴', colors: 7, bright: true, speed: true, dir: 'none' },
  { id: 'Music', name: '音乐', colors: 7, bright: true, speed: true, dir: 'none' },
];
let lightEffect = 'Breathing';
let lightEditorInit = false;
let lightBusy = false;
let lightSubmitted = null;

// 侧边栏分区：标题/副标题只在这里维护一份。
const VIEW_META = {
  overview: ['总览', '你的电脑，现在运行得怎么样'],
  quick: ['快速开关', '常用设置，不必再翻找'],
  creator: ['造物者', '按你的需要，平衡性能与声音'],
  lighting: ['灯光', '选一种效果，配上喜欢的颜色'],
  system: ['诊断', '连接、原始读数与日志，排查问题时再看'],
  monitor: ['监控', '看看温度、负载和功耗如何变化'],
  custom: ['我的方案', '把喜欢的设置留下，下次一键应用'],
  settings: ['更多设置', '扩展能力与接入进展'],
};
const ICONS = {
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M9 18h6M10 21h4"/><path d="M8.5 14a6 6 0 1 1 7 0c-.8.7-1.5 1.6-1.5 2.5h-4c0-.9-.7-1.8-1.5-2.5z"/></svg>',
  fan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="2"/><path d="M12 10c0-4 2-6 4-6s3 2 3 4-2 4-6 4M14 12c4 0 6 2 6 4s-2 3-4 3-4-2-4-6M12 14c0 4-2 6-4 6s-3-2-3-4 2-4 6-4M10 12c-4 0-6-2-6-4s2-3 4-3 4 2 4 6"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/><circle cx="12" cy="12" r="3"/></svg>',
  sliders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/></svg>',
};

const PRESET_KEY = 'umi.presets.v1';
let presets = loadPresets();
let plDirty = false;
let plFilled = false;
let plBusy = false;
let plSubmitted = null;
let plNeedsRefresh = false;
let plEpoch = 0;
let presetBusy = false;
let fanSpeedDirty = false;
let fanSpeedBusy = false;
let fanSpeedSubmitted = null;
let lightSelectedColor = 0;

const rgbToHex = (c) => '#' + (c || []).map((v) => {
  const n = Math.max(0, Math.min(255, Number(v) || 0));
  return n.toString(16).padStart(2, '0');
}).join('');
const hexToRgb = (hex) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return [0, 0, 0];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

let META = { cap_labels: {}, mode_labels: {}, tier_labels: {}, fan_mode_words: {} };
const fanModeWord = (flag) => {
  const w = META.fan_mode_words || {};
  if (!flag) return null;
  if (w[flag]) return w[flag];
  return flag.indexOf('User_Fan') === 0 ? (w.User_Fan || null) : null;
};
const hwModeWord = (m) => (m === 'auto' ? '自适应' : (TIERS[m] || null));
let lastState = null;
let stateRequestId = 0;
let stateSettledId = 0;
let stateSnapshotTs = 0;
let benchWasRunning = false;
let benchTimer = null;   // 跑分进行中改成 1 秒刷一次，进度条才看得出在动
let histData = { samples: [], marks: [] };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) } : { signal: AbortSignal.timeout(15000) };
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || data.reason || ('HTTP ' + res.status));
  return data;
}

async function readDeviceState() {
  const requestId = ++stateRequestId;
  let snapshot;
  try {
    snapshot = await api('/api/state');
  } catch (e) {
    if (requestId < stateSettledId) return null;
    stateSettledId = requestId;
    throw e;
  }
  if (requestId < stateSettledId) return null;
  stateSettledId = requestId;
  if (!Number.isFinite(snapshot.ts) || snapshot.ts < stateSnapshotTs) return null;
  stateSnapshotTs = snapshot.ts;
  lastState = snapshot;
  return snapshot;
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

// ---------- 开关的「正在生效」等待态 ----------
// OEM 的开关不是写完就翻：实测键盘背光约 0.3 秒、Win 键锁定约 6 秒才回读得到新值。
// 这段空窗里按钮和读数还是旧的，用户以为没点上会再点一次（真实反馈过两次）。
// 所以下发成功后本地记一笔「我希望它变成什么」，按钮先改成等待态，
// 直到 /api/state 里的值真的对上才撤；超时没对上就撤并说一声，别让人以为还在等。
const PENDING = {};
const PENDING_WORD = '正在生效…';
// 等待窗口 = 实测生效时间再放宽一档（背光约 0.3 秒、Win 锁约 6 秒，之后还要等 OEM
// 服务答一次 GETSTATUS）。它只决定「正在生效」挂多久，不影响要不要下发。
const WINKEY_WAIT_S = 20;
const KB_WAIT_S = 10;
const TCC_WAIT_S = 20;
const FANSPEED_WAIT_S = 15;

function markPending(key, want, secs, label) {
  PENDING[key] = { want: want, secs: secs, until: Date.now() + secs * 1000, label: label };
}

// cur 是当前读数：对上了就销账（返回 null），没对上返回这条等待记录。
function pendingFor(key, cur) {
  const p = PENDING[key];
  if (!p) return null;
  if (cur === p.want) {
    delete PENDING[key];
    if (key !== 'mode') {
      toast(p.label + '：已生效');
      setText('quick-feedback', p.label + '：已生效，设备读数已确认。');
    }
    return null;
  }
  if (Date.now() > p.until) {
    delete PENDING[key];
    const message = p.label + '：未等到设备确认，请检查当前状态后再试。';
    toast(message, true);
    setText('quick-feedback', message);
    return null;
  }
  return p;
}

// ---------- 自动探测硬件通道 ----------
// 以前要点「重新探测通道」才更新，用户点一次看到「自检通过」就不敢再动了，
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
  const box = $('intents');
  if (!box.children.length) {
    box.innerHTML = INTENTS.map((it) => `<button class="intent" data-id="${it.id}"><span class="name">${it.name}</span><span class="desc">${it.desc}</span></button>`).join('');
    box.querySelectorAll('button').forEach((el) => {
      el.onclick = async () => {
        el.disabled = true;
        try { await api('/api/intent', { intent: el.dataset.id }); toast('调度方式已设为 ' + INTENT_BY_ID[el.dataset.id].name); await poll(); }
        catch (e) { toast('设置失败：' + e.message, true); }
        finally { el.disabled = false; }
      };
    });
  }
  box.querySelectorAll('button').forEach((el) => {
    const selected = !!lastState && lastState.intent === el.dataset.id;
    el.classList.toggle('active', selected);
    el.setAttribute('aria-pressed', String(selected));
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
    // 「防抖」是工程词，用户看不懂。这里说清楚：想降档，但要连续观察一段时间才真降。
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
  const total = chs.length;
  const alive = chs.filter((c) => c.alive).length;
  setText('side-status', `${alive} / ${total} 通道在线`);
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

// OEM 自己报的功耗墙边界（10~120 W 那种）。边界由哪条通道报不写死：找带 detail.pl_bounds
// 的那一条，找不到就整块不显示——不编默认值，也不显示成 0~255 那种假区间。
function wallBoundsRows(s) {
  // 通道列表在 s.channels，不在 s.hardware：hardware 是拍平的 EC 读数，套上去会永远找不到
  const ch = (s.channels || []).find((c) => (c.detail || {}).pl_bounds);
  const bounds = ch ? ch.detail.pl_bounds : null;
  if (!bounds) return [];
  const out = [];
  for (const [name, lo, hi] of [['PL1', 'pl1_min', 'pl1_max'],
                                ['PL2', 'pl2_min', 'pl2_max'],
                                ['PL4', 'pl4_min', 'pl4_max']]) {
    const a = bounds[lo], b = bounds[hi];
    // 缺半边就不显示这一条：只有下限没有上限的区间是假话
    if (a == null || b == null) continue;
    out.push(hwRow(`${name} 允许范围`, `${a} ~ ${b} W`, true));
  }
  return out;
}

function renderHardware(s) {
  const caps = s.capabilities || {};
  const hw = s.hardware || {};
  const verified = (cap) => (caps[cap] || {}).state === 'verified';
  const ecCh = (s.channels || []).find((c) => c.name === 'ec') || {};
  const ecAlive = ecCh.alive === true;
  const keyFlag = FAN_KEY_FLAGS.indexOf(hw.fan_mode_flag) >= 0 ? hw.fan_mode_flag : null;
  // 2026-09-30 12:19 实测纠正：这一行读的是**服务写出来的结果**，不是我们能按下去的原因。
  // 按键 → EC 抬 WMI 事件 → 服务的 SetUserProfile 一次性写 PL、风扇表和这个字节，
  // 所以「全亮 + PL1 75W」是真的同时出现，但**字节不是功耗墙的原因**。
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
    hwRow('功耗墙 PL1/PL2/PL4', hw.pl1_setting != null
          ? ([hw.pl1_setting, hw.pl2_setting, hw.pl4_setting]
               .map((v) => (v == null ? '—' : v + ' W')).join(' / '))
          : '未知', verified('power_limit.read')),
    ...wallBoundsRows(s),
    // 这一行是为了不让人误读上面那三个 TCC 默认值（0x07D8-0x07DA，本机 5/5/5）：
    // 那三个数只有在 0x0786 的使能位（APTN, bit7）置起来时才生效，而参考仓库
    // 实机抓取里这个字节一直是 0x00。所以「降频点 = TjMax−5 = 95 °C」是错的推论。
    // 这里只报使能与偏移两个事实，不算降频点——那还要一个 TjMax，而 TjMax 不是 EC 给的。
    hwRow('CPU TCC 偏移', hw.tcc_offset_enabled == null ? '未知'
          : (hw.tcc_offset_enabled
             ? ('已开启 · 偏移 ' + hw.tcc_offset_c + ' °C')
             : '未开启（出厂那三个默认偏移不生效）'), hw.tcc_offset_enabled != null),
    hwRow('电池（EC）', hw.battery_pct_ec != null
          ? (hw.battery_pct_ec + '% · ' + (hw.battery_temp_c != null ? hw.battery_temp_c + '°C' : '?')
             + (hw.battery_cycles != null ? ' · ' + hw.battery_cycles + ' 次循环' : ''))
          : '未知', hw.battery_pct_ec != null),
    // 「未设限」这个说法是错的：本机根本没有百分比这套机制（封顶走充电电压，
    // 而那个寄存器 host 写不住）。这两个字节恒为 0，照实说清楚，别让人以为
    // 「打开某个开关就能设 80%」。（notes/hardware-channels.md 6.11 第三节）
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

  if (!$('btn-refresh-hw')) {
    $('hw-buttons').innerHTML = '<button class="ghost" id="btn-refresh-hw">重新检查连接</button>';
    $('btn-refresh-hw').onclick = () => { lastProbeAt = 0; autoProbe(true); };
  }
  $('hw-hint').textContent = '这些是设备与服务的原始读数；整机模式请在“造物者”中切换。';
}

// GCUBridge 报上来的开关状态。这一块**只读**：语义还没逐条验证过，
// 先让用户看得见「OEM 自己认为现在是什么状态」，再谈写（notes/hardware-channels.md 6.7）。
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

  // 等待态在这里算一次、两处（读数行 + 按钮）共用：pendingFor 会销账也会超时，
  // 同一轮渲染里算两遍会出现「行上还在等、按钮已经好了」的错位。
  const live = deviceOnline(s);
  const winPend = pendingFor('winkey', live ? oem.win_key_locked : null);
  const kbPend = pendingFor('kbpower', live ? oem.kb_power_on : null);
  const tccPend = pendingFor('tcc', live ? oem.tcc_offset_enabled : null);
  const fanSpeedPend = fanSpeedSubmitted;

  const lim = oem.limits || {};
  const limText = lim.pl1_min != null
    ? `PL1 ${lim.pl1_min}~${lim.pl1_max}W · PL4 ≤${lim.pl4_max}W · TGP ${lim.tgp_min}~${lim.tgp_max} · Boost ${lim.boost_min}~${lim.boost_max} · GPU 目标温度 ${lim.gpu_temp_min}~${lim.gpu_temp_max}°C`
    : '未报';
  // 键盘背光：Keyboard/Status.powerStatus 才是真开关（实测报 Off 的时候灯确实是灭的），
  // Setting/Status.SingleColorKBBL 是另一件事（单色背光这个功能支不支持/开没开），
  // 两个都摆出来，不合并成一个会骗人的值。
  const kbPower = oem.kb_power_on == null ? '未知' : (oem.kb_power_on ? '开' : '关');
  const kbEffectName = KB_EFFECT_NAME_BY_CODE[String(oem.kb_effect)];
  const kbSpeedUi = KB_SPEED_TO_UI[String(oem.kb_speed)];
  const kbText = `${kbPower} · 亮度 ${oem.kb_brightness_ac != null ? oem.kb_brightness_ac : '?'} 档（电池 ${oem.kb_brightness_dc != null ? oem.kb_brightness_dc : '?'} 档）`
    + (kbEffectName ? ` · 灯效 ${kbEffectName}` : '')
    + (kbSpeedUi != null ? ` 速度 ${kbSpeedUi} 档` : '')
    + (oem.kb_controller ? ` · ${oem.kb_controller}` : '');
  const barText = oem.lightbar_on == null ? '未知'
    : ((oem.lightbar_on ? '开' : '关')
       + (oem.lightbar_brightness != null ? ` · 亮度 ${oem.lightbar_brightness}` : ''));
  // 2026-09-30 实测：开关字段说「直连已开」，DGpu 字段却是 NV_CTRL_PANEL_AUTOSELECT。
  // 更可能是两层不同的东西（直连开关 vs NVIDIA 控制面板的输出偏好），没验证前两个都报。
  const tccText = oem.tcc_offset_c == null ? '未报'
    : `${oem.tcc_offset_enabled == null ? '开关未知'
        : (oem.tcc_offset_enabled ? '已开启' : '未开启')} · ${oem.tcc_offset_c} °C`
      + `（上限 ${oem.tcc_offset_max != null ? oem.tcc_offset_max : '?'}）`
      + (tccPend ? ' · ' + PENDING_WORD : '');
  const fanSpeedText = oem.fan_switch_speed_ms == null ? '未报'
    : `${oem.fan_switch_speed_enabled == null ? '开关未知'
        : (oem.fan_switch_speed_enabled ? '已启用' : '未启用')} · ${oem.fan_switch_speed_ms} ms`
      + (fanSpeedPend ? ' · ' + PENDING_WORD : '');
  const muxRaw = oem.dgpu_raw ? ` · NVIDIA 侧 ${oem.dgpu_raw}` : '';
  const muxText = oem.mux_support === false ? '本机不支持（OEM 报 NotSupport）'
    : (onOff(oem.mux_on, '已开（独显直连）', '关（混合输出）') + muxRaw);

  box.innerHTML = [
    hwRow('Win 键锁定', onOff(oem.win_key_locked, '已锁定', '未锁定')
          + '（与 EC 的 STAUTS_BYTE 已对上号）'
          + (winPend ? ' · ' + PENDING_WORD : ''),
          oem.win_key_locked != null),
    hwRow('触摸板', onOff(oem.touchpad_on, '开', '关')
          + '（触摸板上还有个实体拨动开关；OEM 也有 TOUCHPAD_TOGGLE_ON/OFF，'
          + '但没验证过会不会被实体开关盖掉，所以面板不写）', oem.touchpad_on != null),
    hwRow('键盘背光', kbText + (kbPend ? ' · ' + PENDING_WORD : ''), oem.kb_power_on != null),
    hwRow('单色背光功能', onOff(oem.kb_single_color_on, '开', '关'), oem.kb_single_color_on != null),
    // 用户/Creator Center 叫它「海岸灯」，面板跟着叫，别让他对不上号
    hwRow('海岸灯（灯条）', barText, oem.lightbar_on != null),
    hwRow('TCC 偏移（OEM 读数）', tccText, oem.tcc_offset_c != null),
    hwRow('风扇换挡间隔（OEM 读数）', fanSpeedText, oem.fan_switch_speed_ms != null),
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

  $('oem-buttons').replaceChildren();
  hint.textContent = '以下是官方服务上次报告的状态，不保证每项都有硬件反馈。常用操作在“快速开关”和“造物者”，这里仅用于诊断。';
}

// ---------- 侧边栏 / 总览 / 自定义 ----------
function setText(id, text) { const el = $(id); if (el) el.textContent = text; }
function setBar(id, pct) {
  const el = $(id);
  if (el) el.style.width = Math.max(0, Math.min(100, Number(pct) || 0)) + '%';
}

// 滑杆的填充比例靠 CSS 变量 --pct；value 被轮询或代码改掉时也要重画
function paintRange(el) {
  if (!el) return;
  const min = Number(el.min) || 0;
  const max = Number(el.max) || 100;
  const v = Number(el.value);
  const pct = max > min ? ((v - min) / (max - min)) * 100 : 0;
  el.style.setProperty('--pct', Math.max(0, Math.min(100, pct)) + '%');
}

// OEM 当前档位：服务自己报的 ProfileName 最可靠（Mode3=性能、Mode2=省电）；
// 认不出来再看 EC 风扇字节，最后才报 unknown——不拿面板自己的软件档冒充。
function currentOemMode(s) {
  const hw = (s && s.hardware) || {};
  const profile = String((hw.oem || {}).profile_name || '');
  if (profile.indexOf('Mode3') === 0) return 'turbo';
  if (profile.indexOf('Mode2') === 0) return 'office';
  const flag = String(hw.fan_mode_flag || '');
  if (flag === 'Turbo_Mode') return 'turbo';
  if (flag === 'User_Fan_HiMode') return 'office';
  return null;
}

const MODE_NAME = { office: '省电模式', turbo: '性能模式' };
const MODE_DESC = { office: '功耗墙收低，风扇更安静', turbo: '功耗墙和风扇拉满，性能优先' };

function renderOverview(s) {
  const x = (s && s.sensor) || {};
  const g = x.gpu || {};
  const hw = (s && s.hardware) || {};
  const mem = x.mem || {};
  const disk = x.disk || {};
  // 顶栏
  setText('top-cpu', x.cpu_temp != null ? x.cpu_temp + '°C' : '--');
  setText('top-gpu', g.temp_c != null ? g.temp_c + '°C' : '--');
  setText('top-fan', hw.fan_rpm != null ? fmtRpm(hw.fan_rpm) : '--');
  // CPU 卡
  setText('ov-cpu-temp', x.cpu_temp != null ? x.cpu_temp : '--');
  setText('ov-cpu-mhz', x.cpu_mhz != null ? x.cpu_mhz + ' MHz' : '-- MHz');
  setText('ov-cpu-load', x.cpu_pct != null ? '占用 ' + x.cpu_pct + '%' : '占用 --');
  setBar('ov-cpu-bar', x.cpu_pct);
  // GPU 卡
  setText('ov-gpu-temp', g.temp_c != null ? g.temp_c : '--');
  setText('ov-gpu-w', g.power_w != null ? g.power_w + ' W' : '-- W');
  setText('ov-gpu-load', x.gpu_pct != null ? '占用 ' + x.gpu_pct + '%' : '占用 --');
  setBar('ov-gpu-bar', x.gpu_pct);
  // 内存卡
  setText('ov-mem-used', mem.used_gb != null ? mem.used_gb : '--');
  setText('ov-mem-total', mem.total_gb != null ? '共 ' + mem.total_gb + ' GB' : '共 -- GB');
  setText('ov-mem-pct', mem.pct != null ? '占用 ' + mem.pct + '%' : '占用 --');
  setBar('ov-mem-bar', mem.pct);
  // 存储卡（型号/温度还没采，先给容量与占用；拿不到就显示未知）
  setText('ov-disk-drive', disk.drive ? '（' + disk.drive + '）' : '');
  setText('ov-disk-used', disk.used_gb != null ? disk.used_gb : '--');
  setText('ov-disk-total', disk.total_gb != null ? '共 ' + disk.total_gb + ' GB' : '共 -- GB');
  setText('ov-disk-pct', disk.pct != null ? '已用 ' + disk.pct + '%' : '已用 --');
  setBar('ov-disk-bar', disk.pct);
  const mode = currentOemMode(s);
  const status = $('ov-status-card');
  const pending = PENDING.mode;
  const fresh = !!(s && Date.now() / 1000 - s.ts < 8);
  status.dataset.mode = pending ? 'pending' : mode || 'unknown';
  status.dataset.fresh = String(fresh);
  setText('ov-state-freshness', fresh ? '本机实时读数' : '读数待更新');
  setText('ov-creator-mode', pending ? '正在切换' : mode == null ? '等待回读' : mode === 'turbo' ? '已开启' : '已关闭');
  setText('ov-creator-desc', pending ? '等待 OEM 硬件档位生效'
    : mode === 'turbo' ? 'OEM 性能档 · Turbo' : mode === 'office' ? 'OEM 省电档 · Office' : '尚未确认 OEM 硬件档位');
  setText('ov-perf-tier', TIERS[s && s.tier] || (s && s.tier) || '--');
  const it = INTENT_BY_ID[s && s.intent];
  setText('ov-intent', it ? it.name : '--');
  setText('ov-fan-rpm', hw.fan_rpm != null ? fmtRpm(hw.fan_rpm) : '--');
  setText('ov-fan-detail', '副风扇 ' + (hw.fan2_rpm != null ? fmtRpm(hw.fan2_rpm) : '--'));
  const pl1 = hw.pl1 != null ? hw.pl1 : hw.pl1_setting;
  setText('ov-pl1', pl1 != null ? pl1 + ' W' : '--');
  setText('cool-fan-rpm', hw.fan_rpm != null ? Math.round(hw.fan_rpm).toLocaleString() : '--');
  setText('cool-fan-duty', '占空 ' + (hw.fan_duty_l != null ? hw.fan_duty_l + '%' : '--'));
  setText('cool-fan2-rpm', hw.fan2_rpm != null ? Math.round(hw.fan2_rpm).toLocaleString() : '--');
  setText('cool-fan2-duty', '占空 ' + (hw.fan_duty_r != null ? hw.fan_duty_r + '%' : '--'));
  setText('cool-cpu-temp', x.cpu_temp != null ? x.cpu_temp : '--');
  setText('cool-hw-mode', '硬件模式 ' + (hwModeWord(hw.hw_mode) || '未知'));
  renderLightbar(s);
}

const QUICK_SWITCHES = {
  kb: { key: 'kbpower', field: 'kb_power_on', cap: 'lighting.rgb.write', label: '键盘背光', on: 'KB_POWER_ON', off: 'KB_POWER_OFF', wait: KB_WAIT_S },
  win: { key: 'winkey', field: 'win_key_locked', cap: 'winkey.write', label: 'Win 键锁定', on: 'WINKEY_LOCK', off: 'WINKEY_UNLOCK', wait: WINKEY_WAIT_S },
};
const quickSending = new Set();

function renderQuick(s) {
  const oem = ((s || {}).hardware || {}).oem || {};
  const live = deviceOnline(s);
  for (const [id, spec] of Object.entries(QUICK_SWITCHES)) {
    const value = oem[spec.field];
    const waiting = !!PENDING[spec.key] || quickSending.has(id);
    const button = $('quick-' + id);
    button.disabled = !live || typeof value !== 'boolean' || waiting || presetBusy
      || (((s || {}).capabilities || {})[spec.cap] || {}).state !== 'verified'
      || (id === 'kb' && (lightBusy || !!lightSubmitted));
    button.classList.toggle('on', value === true);
    button.classList.toggle('pending', waiting);
    button.setAttribute('aria-checked', String(value === true));
    setText('quick-' + id + '-state', waiting ? '等待确认…' : !live ? '连接待更新'
      : typeof value !== 'boolean' ? '状态未知' : value ? '已开启' : '已关闭');
  }
  const rows = [
    ['电池保养', (META.battery_mode_labels || {})[(((s || {}).hardware || {}).battery_mode)] || null, '充电设置仅显示，不在面板试写'],
    ['独显直连', oem.mux_support === false ? '本机未报告支持' : '状态待核实', '服务标志的直连含义尚未验证，暂由 CC 管理'],
    ['色彩模式', oem.display_mode === 'DISPLAY_STANDARD_MODE' ? '标准' : oem.display_mode ? '未识别' : '未回读', '屏幕颜色配置，暂由 CC 管理'],
    ['功能键屏幕提示', onOff(oem.osd_hidden, '隐藏', '显示'), '暂由 CC 管理'],
    ['关机 USB 供电', onOff(oem.usb_charger_on, '开启', '关闭'), '暂由 CC 管理'],
  ];
  $('quick-device-status').innerHTML = rows.map(([label, value, desc]) => `<div class="readonly-row"><div><b>${esc(label)}</b><small>${esc(desc)}</small></div><span class="tag">${esc(live ? value || '未回读' : '连接待更新')}</span></div>`).join('');
  if (!live) setText('quick-feedback', '等待设备连接；确认当前状态前不会发送开关指令。');
  else if (!PENDING.kbpower && !PENDING.winkey && !quickSending.size && /^(等待设备|正在读取设备)/.test($('quick-feedback').textContent)) {
    setText('quick-feedback', '设备已连接，可以操作。');
  }
}

async function toggleQuick(id) {
  const spec = QUICK_SWITCHES[id];
  renderQuick(lastState);
  if (!spec || $('quick-' + id).disabled) return;
  const value = !lastState.hardware.oem[spec.field];
  quickSending.add(id);
  renderQuick(lastState);
  setText('quick-feedback', '正在下发' + spec.label + '…');
  try {
    await api('/api/action', { action: value ? spec.on : spec.off });
    markPending(spec.key, value, spec.wait, spec.label);
    setText('quick-feedback', spec.label + '：已下发，等待设备确认。');
    toast(spec.label + '：已下发');
  } catch (e) {
    setText('quick-feedback', '未完成：' + e.message);
    toast('下发失败：' + e.message, true);
  } finally {
    quickSending.delete(id);
    renderQuick(lastState);
  }
  poll();
}

function renderLightbar(s) {
  const box = $('lightbar-status');
  if (!box) return;
  const oem = ((s && s.hardware) || {}).oem || {};
  box.innerHTML = hwRow('开关', onOff(oem.lightbar_on, '开', '关'), oem.lightbar_on != null)
    + hwRow('亮度', oem.lightbar_brightness != null ? oem.lightbar_brightness + ' 档' : '未知',
            oem.lightbar_brightness != null)
    + hwRow('控制器', oem.kb_controller || '未知', !!oem.kb_controller);
}

function showView(name, opts) {
  const meta = VIEW_META[name] || VIEW_META.overview;
  const key = VIEW_META[name] ? name : 'overview';
  document.querySelectorAll('.view').forEach((v) => {
    v.classList.toggle('active', v.id === 'view-' + key);
  });
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === key);
  });
  setText('page-title', meta[0]);
  setText('page-sub', meta[1]);
  document.title = `${meta[0]} · Umi Control Panel`;
  try { localStorage.setItem('umi.view', key); } catch (e) { /* 隐私模式不记 */ }
  if (key === 'overview' || key === 'monitor') setTimeout(loadHistory, 40);
  if (key === 'custom') { renderCustom(lastState); renderPresets(); }
  if (presetBusy) renderPresetLock();
  if (key === 'creator') {
    renderCreator(lastState);
    requestAnimationFrame(() => {
      if (fanSource === 'mqtt') drawOemCurveEditor();
      else renderFanCurve();
    });
  }
  if (!(opts && opts.scroll === false)) window.scrollTo(0, 0);
}

function initNav() {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.onclick = () => showView(b.dataset.view);
  });
  document.querySelectorAll('[data-goto]').forEach((b) => {
    b.onclick = () => {
      showView(b.dataset.goto);
      if (b.dataset.creatorTarget) showCreatorTab(b.dataset.creatorTarget);
    };
    if (b.tagName !== 'BUTTON') b.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); b.click(); }
    };
  });
  let saved = 'overview';
  try {
    saved = new URLSearchParams(location.search).get('view')
      || localStorage.getItem('umi.view') || 'overview';
  } catch (e) { /* ignore */ }
  if (!VIEW_META[saved]) saved = 'overview';
  showView(saved, { scroll: false });
}

// ---------- 自定义方案 ----------
function loadPresets() {
  try {
    const raw = localStorage.getItem(PRESET_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}

function savePresets() {
  try { localStorage.setItem(PRESET_KEY, JSON.stringify(presets)); }
  catch (e) { toast('方案保存失败：' + e.message, true); }
}

function effectLabel(id) {
  const e = KB_EFFECTS.find((x) => x.id === id);
  return e ? e.name : (id || '未知');
}

function captureLighting() {
  return lightingEditorPayload();
}

function capturePreset(name, include) {
  const s = lastState || {};
  const hw = s.hardware || {};
  const oem = hw.oem || {};
  const p = { id: 'p' + Date.now().toString(36), name, ts: Date.now() };
  if (include.mode) {
    const mode = currentOemMode(s);
    if (mode) p.mode = mode;
  }
  if (include.tcc && currentOemMode(s) === 'turbo' && oem.tcc_offset_c != null
      && typeof oem.tcc_offset_enabled === 'boolean') {
    p.tcc = { value: oem.tcc_offset_c, enabled: oem.tcc_offset_enabled };
  }
  if (include.fanspeed && currentOemMode(s) === 'turbo' && oem.fan_switch_speed_ms != null
      && typeof oem.fan_switch_speed_enabled === 'boolean') {
    p.fanSpeed = { ms: oem.fan_switch_speed_ms, enabled: oem.fan_switch_speed_enabled };
  }
  if (include.light && lightEditorInit && !lightBusy && !lightSubmitted
      && oem.kb_power_on === true) p.lighting = captureLighting();
  return p;
}

function presetChips(p) {
  const chips = [];
  if (p.mode) chips.push(`<span class="chip cyan">造物者${p.mode === 'turbo' ? '开启' : '关闭'}</span>`);
  if (p.tcc) chips.push(`<span class="chip amber">提前降温 ${esc(p.tcc.value)}°C · ${p.tcc.enabled ? '开' : '关'}</span>`);
  if (p.fanSpeed) chips.push(`<span class="chip">响应 ${esc(p.fanSpeed.ms)} ms · ${p.fanSpeed.enabled ? '自定义' : '自动'}</span>`);
  if (p.lighting) chips.push(`<span class="chip purple">${esc(effectLabel(p.lighting.effect))}</span>`);
  return chips.join('') || '<span class="chip">空方案</span>';
}

function renderPresets() {
  const box = $('preset-list');
  if (!box) return;
  if (!presets.length) {
    box.innerHTML = '<div class="preset-empty">还没有方案。先在「造物者」和「灯光」调好，再回来保存。</div>';
    return;
  }
  box.innerHTML = presets.map((p) => `
    <div class="preset-card" data-id="${esc(p.id)}">
      <div class="preset-name">${esc(p.name)}<small>${new Date(p.ts).toLocaleDateString('zh-CN')}</small></div>
      <div class="preset-chips">${presetChips(p)}</div>
      <div class="btn-row">
        <button class="primary" data-apply="${esc(p.id)}">应用方案</button>
        <button class="ghost" data-del="${esc(p.id)}">删除</button>
      </div>
    </div>`).join('');
  box.querySelectorAll('[data-apply]').forEach((b) => {
    b.onclick = () => applyPreset(b.dataset.apply, b);
  });
  box.querySelectorAll('[data-del]').forEach((b) => {
    b.onclick = () => {
      const p = presets.find((x) => x.id === b.dataset.del);
      if (!p || !confirm(`删除方案「${p.name}」？`)) return;
      presets = presets.filter((x) => x.id !== b.dataset.del);
      savePresets();
      renderPresets();
    };
  });
}

function deviceOnline(s) {
  return !!s && Number.isFinite(s.ts) && Date.now() / 1000 - s.ts < 8
    && !!((s.channels || []).find((c) => c.name === 'mqtt') || {}).alive;
}

function canOemMode(s, mode) {
  const channel = ((s || {}).channels || []).find((c) => c.name === 'mqtt') || {};
  return ((((s || {}).capabilities || {})['mode.write']) || {}).state === 'verified'
    && ((channel.detail || {}).mode_options || []).some((option) => option.id === mode);
}

function lightingMatches(oem, body) {
  const opts = KB_EFFECT_OPTS[body.effect];
  return !!opts && KB_EFFECT_NAME_BY_CODE[String(oem.kb_effect)] === body.effect
    && (!opts.bright || oem.kb_light === body.light)
    && (!opts.speed || KB_SPEED_TO_UI[String(oem.kb_speed)] === body.speed)
    && (opts.dir === 'none' || KB_DIRECTION_BY_CODE[String(oem.kb_direction)] === body.direction)
    && (!opts.colors || (Array.isArray(oem.kb_colors)
      && body.colors.slice(0, opts.colors).every((color, i) =>
        JSON.stringify(color) === JSON.stringify(oem.kb_colors[i]))));
}

function renderPresetLock() {
  document.querySelectorAll('.view:not(#view-custom)').forEach((view) => { view.inert = presetBusy; });
  document.querySelectorAll('#view-custom button, #view-custom input').forEach((el) => {
    if (presetBusy) el.disabled = true;
    else if (!el.id.endsWith('-range')) el.disabled = false;
  });
}

async function waitPresetState(match, timeout = 45000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const s = await readDeviceState();
    if (deviceOnline(s) && match(s)) return s;
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
  throw new Error('未等到设备确认，后续设置未发送');
}

async function applyPreset(id, btn) {
  const p = presets.find((x) => x.id === id);
  if (!p || presetBusy) return;
  if (oemWriting || spcBusy || spcSubmitted || plBusy || plSubmitted || fanSpeedBusy || fanSpeedSubmitted
      || lightBusy || lightSubmitted || quickSending.size || Object.keys(PENDING).length) {
    toast('其他设置正在生效，请确认完成后再应用方案', true); return;
  }
  if (!deviceOnline(lastState)) { toast('请先等待设备连接', true); return; }
  if (p.mode && !canOemMode(lastState, p.mode)) { toast('方案中的档位当前未开放', true); return; }
  if ((p.tcc || p.fanSpeed) && (p.mode || currentOemMode(lastState)) !== 'turbo') {
    toast('含降温或风扇响应设置的方案需要开启造物者模式', true); return;
  }
  const steps = [];
  if (p.mode) steps.push({ label: '造物者开关', cap: 'mode.write', path: '/api/mode', body: { mode: p.mode },
    match: (s) => currentOemMode(s) === p.mode });
  if (p.tcc) steps.push({ label: '提前降温', cap: 'cpu.tcc.write', path: '/api/tcc', body: p.tcc,
    match: (s) => s.hardware.oem.tcc_offset_enabled === p.tcc.enabled
      && s.hardware.oem.tcc_offset_c === p.tcc.value });
  if (p.fanSpeed) steps.push({ label: '风扇响应', cap: 'fan.switch_speed.write', path: '/api/fan-switch-speed', body: p.fanSpeed,
    match: (s) => s.hardware.oem.fan_switch_speed_enabled === p.fanSpeed.enabled
      && (!p.fanSpeed.enabled || s.hardware.oem.fan_switch_speed_ms === p.fanSpeed.ms) });
  if (p.lighting) steps.push({ label: '键盘灯效', cap: 'lighting.effect.write', path: '/api/keyboard-effect', body: p.lighting,
    match: (s) => lightingMatches(s.hardware.oem, p.lighting) });
  if (!steps.length) { toast('这个方案没有可应用的内容', true); return; }
  if (steps.some((step) => ((lastState.capabilities || {})[step.cap] || {}).state !== 'verified')) {
    toast('方案中有当前不可操作的项目，请检查设备连接和功能状态', true); return;
  }
  if (p.lighting && !((lastState.hardware || {}).oem || {}).kb_power_on) {
    toast('请先打开键盘背光再应用含灯效的方案', true); return;
  }
  if (!confirm(`应用方案「${p.name}」？\n\n${steps.map((step) => step.label).join(' → ')}\n逐步确认；中途失败即停止，不自动回退已经生效的步骤。`)) return;
  const progress = $('preset-progress');
  const completed = [];
  let label = '';
  presetBusy = true;
  progress.hidden = false;
  renderPresetLock();
  try {
    for (const [index, step] of steps.entries()) {
      label = step.label;
      progress.textContent = `${index + 1}/${steps.length} · 正在下发${label}…`;
      const fresh = await readDeviceState();
      if (!deviceOnline(fresh)) throw new Error('未取得新的在线设备读数');
      if (((fresh.capabilities || {})[step.cap] || {}).state !== 'verified') throw new Error('功能当前不可写');
      if (step.path === '/api/mode' && !canOemMode(fresh, p.mode)) throw new Error('档位当前未开放');
      if (step.path === '/api/keyboard-effect' && fresh.hardware.oem.kb_power_on !== true) throw new Error('键盘背光已关闭');
      if ((step.path === '/api/tcc' || step.path === '/api/fan-switch-speed')
          && currentOemMode(fresh) !== 'turbo') throw new Error('造物者模式已改变');
      if (step.path === '/api/mode' && step.match(fresh)) {
        completed.push(label);
        progress.textContent = `${index + 1}/${steps.length} · ${label}已是目标档位，无需重复切换。`;
        continue;
      }
      await api(step.path, step.body);
      progress.textContent = `${index + 1}/${steps.length} · ${label}已下发，等待设备确认…`;
      await waitPresetState(step.match, step.path === '/api/mode' ? 45000 : 20000);
      completed.push(label);
    }
    progress.textContent = `方案「${p.name}」已生效：${completed.join('、')}读数已确认。`;
    toast('方案已生效');
  } catch (e) {
    progress.textContent = `${label}未完成：${e.message}。已完成：${completed.join('、') || '无'}；未自动回退。`;
    toast('方案已停止，请查看执行结果', true);
  } finally {
    presetBusy = false;
    renderPresetLock();
    if (btn) btn.disabled = false;
    await poll();
  }
}

function renderFanSpeed(s) {
  const oem = (((s || {}).hardware || {}).oem || {});
  const checkbox = $('fanspeed-enabled');
  const input = $('fanspeed-value');
  const can = deviceOnline(s) && currentOemMode(s) === 'turbo' && !PENDING.mode
    && (((s.capabilities || {})['fan.switch_speed.write'] || {}).state === 'verified')
    && typeof oem.fan_switch_speed_enabled === 'boolean' && oem.fan_switch_speed_ms != null;
  if (fanSpeedSubmitted) {
    const wanted = fanSpeedSubmitted.body;
    if (deviceOnline(s) && oem.fan_switch_speed_enabled === wanted.enabled
        && (!wanted.enabled || oem.fan_switch_speed_ms === wanted.ms)) {
      fanSpeedSubmitted = null;
      fanSpeedDirty = false;
      setText('fanspeed-feedback', '已生效：响应设置读数已确认。');
      toast('风扇响应：已生效');
    } else if (Date.now() > fanSpeedSubmitted.until) {
      fanSpeedSubmitted = null;
      setText('fanspeed-feedback', '未等到一致读数，草稿已保留；请核对设备后再试。');
      toast('风扇响应未确认', true);
    }
  }
  if (!fanSpeedDirty && !fanSpeedBusy && !fanSpeedSubmitted) {
    checkbox.checked = oem.fan_switch_speed_enabled === true;
    input.value = oem.fan_switch_speed_ms == null ? '' : oem.fan_switch_speed_ms;
  }
  const locked = !can || fanSpeedBusy || !!fanSpeedSubmitted || presetBusy;
  checkbox.disabled = locked;
  input.disabled = locked || !checkbox.checked;
  $('btn-fanspeed-apply').disabled = locked;
  $('btn-fanspeed-off').disabled = locked || oem.fan_switch_speed_enabled !== true;
  if (!can) setText('fanspeed-feedback', '开启造物者并读取当前响应设置后，可以编辑。');
  else if (fanSpeedBusy) setText('fanspeed-feedback', '正在下发响应设置…');
  else if (fanSpeedSubmitted) setText('fanspeed-feedback', '已下发，正在确认开关和间隔读数…');
  else if (fanSpeedDirty && !/^未等到|^未完成/.test($('fanspeed-feedback').textContent)) {
    setText('fanspeed-feedback', '有未保存的修改，不会自动应用。');
  } else if (!fanSpeedDirty && !$('fanspeed-feedback').textContent.startsWith('已生效')) {
    setText('fanspeed-feedback', '当前设备读数。间隔越短，转速响应越快；关闭自定义则由设备管理。');
  }
}

async function applyFanSpeed(enabled = $('fanspeed-enabled').checked) {
  renderFanSpeed(lastState);
  if ($('btn-fanspeed-apply').disabled) return;
  const ms = Number($('fanspeed-value').value);
  if (enabled && (!Number.isInteger(ms) || ms < 100 || ms > 2000 || ms % 100)) {
    toast('响应间隔须为 100–2000 ms，且为 100 的整数倍', true); return;
  }
  const body = enabled ? { enabled, ms } : { enabled };
  fanSpeedDirty = true;
  fanSpeedBusy = true;
  renderFanSpeed(lastState);
  try {
    await api('/api/fan-switch-speed', body);
    fanSpeedSubmitted = { body, until: Date.now() + FANSPEED_WAIT_S * 1000 };
    toast('风扇响应：已下发，等待确认');
  } catch (e) {
    setText('fanspeed-feedback', '未完成：' + e.message);
    toast('下发失败：' + e.message, true);
  } finally {
    fanSpeedBusy = false;
    renderFanSpeed(lastState);
  }
  await poll();
}

function plBounds(s) {
  const ch = ((s && s.channels) || []).find((c) => (c.detail || {}).pl_bounds);
  const b = ch ? ch.detail.pl_bounds : null;
  if (!b) return null;
  const out = {};
  for (const k of ['pl1', 'pl2', 'pl4']) {
    if (b[k + '_min'] == null || b[k + '_max'] == null) return null;
    out[k] = [b[k + '_min'], b[k + '_max']];
  }
  return out;
}

function fillPlControls(s, force) {
  if (force) { plDirty = false; plFilled = false; plNeedsRefresh = false; }
  const hint = $('pl-hint');
  const bounds = plBounds(s);
  const hw = (s && s.hardware) || {};
  const values = {
    pl1: hw.pl1_setting != null ? hw.pl1_setting : hw.pl1,
    pl2: hw.pl2_setting != null ? hw.pl2_setting : hw.pl2,
    pl4: hw.pl4_setting != null ? hw.pl4_setting : hw.pl4,
  };
  if (plSubmitted) {
    if (deviceOnline(s) && currentOemMode(s) === 'turbo'
        && Object.entries(plSubmitted.body).every(([key, value]) => values[key] === value)) {
      plSubmitted = null; plDirty = false;
      toast('已生效：临时功耗读数已确认');
    } else if (Date.now() > plSubmitted.until) {
      plSubmitted = null; plNeedsRefresh = true;
      toast('临时功耗未确认，草稿保留，请重读后再操作', true);
    }
  }
  const locked = plBusy || !!plSubmitted || plNeedsRefresh || presetBusy || spcBusy || !!spcSubmitted
    || !deviceOnline(s) || currentOemMode(s) !== 'turbo' || !!PENDING.mode;
  const can = !!bounds && (((s || {}).capabilities || {})['power_limit.write'] || {}).state === 'verified';
  for (const k of ['pl1', 'pl2', 'pl4']) {
    const el = $(k + '-range');
    if (bounds) { el.min = bounds[k][0]; el.max = bounds[k][1]; }
    el.disabled = !can || locked || values[k] == null;
    if (bounds && values[k] != null && !plDirty && !plBusy && !plSubmitted) {
      el.value = Math.max(bounds[k][0], Math.min(bounds[k][1], values[k]));
    }
    $(k + '-val').textContent = values[k] == null ? '未回读' : el.value + ' W';
    paintRange(el);
  }
  $('btn-pl-apply').disabled = ['pl1', 'pl2', 'pl4'].every((k) => $(k + '-range').disabled);
  $('btn-pl-refresh').disabled = plBusy || !!plSubmitted || presetBusy || spcBusy || !!spcSubmitted
    || !!PENDING.mode || !deviceOnline(s);
  hint.textContent = plNeedsRefresh ? '档位变化或回读未确认，草稿已保留。请按当前读数重填后再操作。'
    : plBusy ? '正在下发临时功耗…'
    : plSubmitted ? '已下发，等待所有功耗读数一致后解锁。'
    : !deviceOnline(s) || currentOemMode(s) !== 'turbo' ? '连接设备并开启造物者模式后可以调整。'
    : !can ? '未取得设备允许范围或写入能力，暂时不能调整功耗。'
    : plDirty ? '有未保存的修改。长时功耗 ≤ 短时功耗 ≤ 瞬时功耗。'
    : '当前临时功耗读数；切档、息屏或官方服务重算后可能变化。';
  plFilled = true;
}

function renderCustom(s, force) {
  fillPlControls(s, !!force);
}

// ---------- 造物者模式（OEM 硬件档 + 三页签） ----------
let creatorActiveTab = 'fan';
let creatorModeSeen = null;
let spcDirty = new Set();
let spcSubmitted = null;
let spcBusy = false;
let spcNeedsRefresh = false;
let spcEpoch = 0;

function ensureCreatorCurve() {
  if ($('view-creator').classList.contains('active') && creatorActiveTab === 'fan'
      && fanSource === 'mqtt' && currentOemMode(lastState) === 'turbo'
      && !oemReadAttempted && !oemLoading) loadOemCurve(false);
}

function showCreatorTab(key) {
  creatorActiveTab = ['fan', 'performance', 'benchmark'].includes(key) ? key : 'fan';
  document.querySelectorAll('[data-creator-tab]').forEach((button) => {
    const active = button.dataset.creatorTab === creatorActiveTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll('[data-creator-pane]').forEach((pane) => {
    const active = pane.dataset.creatorPane === creatorActiveTab;
    pane.hidden = !active;
    pane.classList.toggle('active', active);
  });
  ensureCreatorCurve();
  if (creatorActiveTab === 'fan') requestAnimationFrame(() => {
    if (fanSource === 'mqtt') drawOemCurveEditor();
    else renderFanCurve();
  });
  if (creatorActiveTab === 'performance' && lastState) fillSpc(lastState);
}

function renderCreator(s) {
  const mode = currentOemMode(s);
  const tableName = (((s || {}).hardware || {}).oem || {}).fan_table;
  if (mode !== creatorModeSeen || (oemCurve && tableName && tableName !== oemCurve.name)) {
    creatorModeSeen = mode;
    oemEpoch += 1;
    spcEpoch += 1;
    oemCurve = null; oemDraft = null; oemOrig = null; oemOrigName = null;
    $('fan-default-feedback').hidden = true;
    oemReadAttempted = false; oemReadError = ''; oemChartGeometry = null; oemDragIndex = null; oemFocusIndex = null;
    curveData = null;
    $('fan-curve').innerHTML = '';
    $('fan-curve-editor').innerHTML = '';
    $('fan-curve-chart-wrap').hidden = true;
    $('fan-curve-empty').hidden = false;
    $('btn-curve-undo').disabled = true;
    setText('fan-table-meta', '档位或表已变化，请重新读取');
    spcNeedsRefresh = spcDirty.size > 0;
    spcSubmitted = null;
    plEpoch += 1;
    plNeedsRefresh = plDirty;
    plSubmitted = null;
  }
  const on = mode === 'turbo';
  const known = mode != null;
  const pending = PENDING.mode;
  const btn = $('btn-creator-mode');
  if (btn) {
    btn.classList.toggle('on', on);
    btn.classList.toggle('pending', !!pending);
    btn.disabled = !known || !!pending || oemWriting || spcBusy || !!spcSubmitted
      || plBusy || !!plSubmitted || fanSpeedBusy || !!fanSpeedSubmitted || presetBusy || !deviceOnline(s)
      || !canOemMode(s, on ? 'office' : 'turbo');
    btn.setAttribute('aria-checked', String(on));
  }
  setText('creator-mode-state', !known ? '未知档位'
    : (on ? '已开启' : '已关闭'));
  setText('creator-mode-desc', known
    ? (on ? '性能档（Turbo）· 风扇、性能与跑分已开放'
      : '省电档（Office）· 开启后才能编辑，切换约 24 秒')
    : '认不出当前 OEM 档位，先等状态刷新。');
  const pendEl = $('creator-pending');
  if (pendEl) pendEl.hidden = !pending;
  if (pending) {
    setText('creator-pending', `正在切到${pending.want === 'turbo' ? '性能档' : '省电档'}…约 24 秒`);
  }
  const workspace = $('creator-workspace');
  if (workspace) {
    workspace.classList.toggle('locked', !on || !!pending);
    workspace.inert = !on || !!pending;
    workspace.setAttribute('aria-disabled', String(!on || !!pending));
  }
  const lock = $('creator-lock');
  if (lock) lock.hidden = on && !pending;
  document.querySelectorAll('.fan-point-input').forEach((input) => { input.disabled = !oemWritable(); });
  if ($('btn-curve-apply')) $('btn-curve-apply').disabled = !oemWritable();
  $('btn-curve-undo').disabled = !oemWritable() || !oemDraftChanged(oemType);
  $('btn-curve-default').disabled = !oemWritable();
  fillSpc(s);
  ensureCreatorCurve();
}

function fillSpc(s, force = false) {
  if (force) { spcDirty.clear(); spcSubmitted = null; spcNeedsRefresh = false; }
  const hw = (s && s.hardware) || {};
  const oem = hw.oem || {};
  const bounds = plBounds(s);
  const vals = {
    pl1: hw.pl1_setting != null ? hw.pl1_setting : hw.pl1,
    pl2: hw.pl2_setting != null ? hw.pl2_setting : hw.pl2,
    pl4: hw.pl4_setting != null ? hw.pl4_setting : hw.pl4,
  };
  const caps = (s || {}).capabilities || {};
  const plCan = !!bounds && (caps['power_limit.write'] || {}).state === 'verified';
  const tccCan = (caps['cpu.tcc.write'] || {}).state === 'verified'
    && oem.tcc_offset_c != null && oem.tcc_offset_enabled != null;
  if (spcSubmitted) {
    const confirmed = deviceOnline(s) && Object.entries(spcSubmitted.values).every(([key, value]) => {
      const read = key === 'tcc' ? oem.tcc_offset_c
        : key === 'tcc-enabled' ? oem.tcc_offset_enabled : vals[key];
      return read != null && Number(read) === Number(value);
    });
    if (confirmed) {
      Object.keys(spcSubmitted.values).forEach((key) => spcDirty.delete(key));
      spcSubmitted = null;
      toast('已生效：处理器设置回读一致');
    } else if (Date.now() > spcSubmitted.until) {
      spcSubmitted = null;
      toast('处理器设置未等到一致回读，草稿保留，请核对后再操作', true);
    }
  }
  const locked = spcBusy || !!spcSubmitted || plBusy || !!plSubmitted || spcNeedsRefresh || presetBusy
    || currentOemMode(s) !== 'turbo' || !!PENDING.mode || !deviceOnline(s);
  const plReady = Object.keys(vals).some((key) => vals[key] != null);
  $('btn-spc-apply').disabled = !plCan || !plReady || locked;
  $('btn-spc-tcc-apply').disabled = !tccCan || locked || !!PENDING.tcc;
  $('btn-spc-refresh').disabled = spcBusy || !!spcSubmitted || presetBusy;
  $('btn-spc-default').disabled = !plCan || locked || Object.entries({ pl1: 75, pl2: 75, pl4: 165 })
    .some(([key, value]) => vals[key] == null || value < bounds[key][0] || value > bounds[key][1]);
  for (const k of ['pl1', 'pl2', 'pl4']) {
    setText('spc-' + k + '-range', bounds ? `${bounds[k][0]}–${bounds[k][1]} W · 设备允许范围` : '等待允许范围');
    const el = $('spc-' + k);
    if (!el) continue;
    if (bounds) {
      el.min = bounds[k][0];
      el.max = bounds[k][1];
      el.disabled = !plCan || vals[k] == null || locked;
      if (!spcDirty.has(k) && vals[k] != null) {
        el.value = Math.max(bounds[k][0], Math.min(bounds[k][1], Number(vals[k])));
      }
    } else {
      el.disabled = true;
    }
    const lab = $('spc-' + k + '-val');
    if (lab) lab.textContent = vals[k] == null ? '未回读' : el.value + ' W';
    paintRange(el);
  }
  const max = oem.tcc_offset_max != null ? oem.tcc_offset_max : 5;
  setText('spc-tcc-help', `提前降温偏移（TCC）：数值越大，越早降低性能以控制温度。当前设备允许 0–${max} °C。`);
  const chk = $('spc-tcc-enabled');
  if (chk) {
    if (!spcDirty.has('tcc-enabled')) chk.checked = !!oem.tcc_offset_enabled;
    chk.disabled = !tccCan || locked;
  }
  const tcc = $('spc-tcc');
  if (tcc) {
    tcc.max = max;
    tcc.disabled = !tccCan || locked || !chk.checked;
    if (!spcDirty.has('tcc') && oem.tcc_offset_c != null) {
      tcc.value = Math.max(0, Math.min(max, Number(oem.tcc_offset_c)));
    }
    const lab = $('spc-tcc-val');
    if (lab) lab.textContent = oem.tcc_offset_c == null ? '未回读' : tcc.value + ' °C';
    paintRange(tcc);
  }
  const cpu = $('spc-cpu-readonly');
  if (cpu) cpu.innerHTML = hwRow('核心电压偏移 · 只读',
    oem.core_voltage_offset != null ? oem.core_voltage_offset + ' mV' : '未回读',
    oem.core_voltage_offset != null);
  const gpu = $('spc-gpu');
  if (gpu) {
    gpu.innerHTML = hwRow('GPU 核心时钟偏移',
        oem.gpu_core_offset != null ? oem.gpu_core_offset + ' MHz · 未验证' : '未知',
        oem.gpu_core_offset != null)
      + hwRow('GPU 内存时钟偏移',
        oem.gpu_mem_offset != null ? oem.gpu_mem_offset + ' MHz · 未验证' : '未知',
        oem.gpu_mem_offset != null)
      + hwRow('目标温度',
        oem.gpu_target_temp != null ? oem.gpu_target_temp + ' °C · 只读' : '未知',
        oem.gpu_target_temp != null)
      + hwRow('显卡功耗预算（TGP）',
        oem.tgp_target != null ? oem.tgp_target + ' W · ' + (oem.tgp_enabled ? '开' : '关') + ' · 只读' : '未知',
        oem.tgp_target != null)
      + hwRow('动态功耗增益',
        oem.dynamic_boost != null ? oem.dynamic_boost + ' W · ' + (oem.dynamic_boost_on ? '开' : '关') + ' · 只读' : '未知',
        oem.dynamic_boost != null);
  }
  const fanRows = $('spc-fan-rows');
  if (fanRows) {
    fanRows.innerHTML = hwRow('风扇独立输出',
      oemCurve ? triText(oemCurve.fan_control_respective, '开', '关') : '尚未读取 OEM 表',
      !!oemCurve)
      + hwRow('风扇换挡间隔',
        oem.fan_switch_speed_ms != null
          ? `${oem.fan_switch_speed_ms} ms · ${oem.fan_switch_speed_enabled ? '启用' : '关闭'}`
          : '未知',
        oem.fan_switch_speed_ms != null);
  }
  const fstate = $('spc-fan-state');
  if (fstate) {
    fstate.textContent = oemCurve && oemCurve.name ? `当前表 ${oemCurve.name}` : 'OEM 表未读取';
  }
  const hint = $('spc-hint');
  if (hint) {
    hint.textContent = spcNeedsRefresh ? '档位已变化，旧草稿不能保存。请先点击“撤销 / 重读”。'
      : spcBusy ? '正在下发处理器设置…'
      : spcSubmitted ? '已下发，正在等待读数确认；确认前暂时锁定编辑。'
      : spcDirty.size ? '有未保存的修改。确认数值后，点击对应的保存按钮。'
      : bounds ? '显示当前设备读数。设置只在当前档位生效，切档或息屏后可能变化。'
      : '未取得设备允许范围，暂时不能调整功耗。';
  }
}

// ---------- 键盘灯效（OEM SetEffectALL，2026-10-20 已做可逆验证） ----------
function lightOpts() {
  return KB_EFFECT_OPTS[lightEffect] || { colors: 7, dir: 'none' };
}

function lightingEditorPayload() {
  const opts = lightOpts();
  const colors = [];
  for (let i = 0; i < 7; i++) colors.push(hexToRgb(($('light-color-' + i) || {}).value));
  // 常亮/音乐只开放 4 个色块；OEM 报文固定要 7 组，后 3 组跟随第 4 组（和 CC 的显示一致）
  if (opts.colors === 4) {
    for (let i = 4; i < 7; i++) colors[i] = colors[3].slice();
  }
  const dirEl = $('light-direction');
  const current = dirEl ? dirEl.value : 'None';
  const direction = opts.dir === 'none' ? 'None'
    : (opts.dir === 'lr'
      ? (current === 'RightLeft' ? 'RightLeft' : 'LeftRight')
      : (['None', 'LeftRight', 'RightLeft'].includes(current) ? current : 'None'));
  return {
    effect: lightEffect,
    light: Number(($('light-brightness') || {}).value),
    speed: Number(($('light-speed') || {}).value),
    direction,
    colors,
    welcome: false,
  };
}

function selectLightEffect(name) {
  lightEffect = KB_EFFECTS.some((e) => e.id === name) ? name : 'Breathing';
  document.querySelectorAll('#light-effects button').forEach((b) => {
    b.classList.toggle('active', b.dataset.effect === lightEffect);
    b.setAttribute('aria-pressed', String(b.dataset.effect === lightEffect));
  });
  const opts = KB_EFFECT_OPTS[lightEffect] || { colors: 7, bright: true, speed: true, dir: 'none' };
  const cols = $('light-colors');
  const previewWrap = $('light-preview-wrap');
  const preview = $('light-preview');
  if (cols) cols.hidden = opts.colors === 0;
  if (previewWrap) previewWrap.hidden = opts.colors === 0;
  if (preview && opts.colors > 0) preview.style.gridTemplateColumns = `repeat(${opts.colors}, minmax(0, 1fr))`;
  const label = $('light-colors-label');
  if (label) {
    label.textContent = opts.colors === 0 ? '颜色由固件生成'
      : (opts.colors === 4 ? '4 色块' : '7 色块');
  }
  for (let i = 0; i < 7; i++) {
    const inp = $('light-color-' + i);
    if (inp) inp.hidden = i >= opts.colors;
    const cell = preview ? preview.children[i] : null;
    if (cell) cell.hidden = i >= opts.colors;
  }
  // 亮度 / 速度：音乐没有亮度，常亮/彩虹/音乐没有速度——按矩阵整段显隐，不留灰控件骗人
  const brightBox = $('light-bright-box');
  if (brightBox) brightBox.hidden = !opts.bright;
  const speedBox = $('light-speed-box');
  if (speedBox) speedBox.hidden = !opts.speed;
  const box = $('light-direction-box');
  if (box) box.hidden = opts.dir === 'none';
  const sel = $('light-direction');
  if (sel && opts.dir !== 'none') {
    const current = sel.value;
    sel.innerHTML = opts.dir === 'all'
      ? '<option value="None">无方向</option><option value="LeftRight">左 → 右</option><option value="RightLeft">右 → 左</option>'
      : '<option value="LeftRight">左 → 右</option><option value="RightLeft">右 → 左</option>';
    sel.value = opts.dir === 'all' && current === 'None' ? 'None'
      : (current === 'RightLeft' ? 'RightLeft' : 'LeftRight');
  }
  const note = $('light-effect-note');
  if (note) {
    const parts = [];
    parts.push(opts.colors === 0 ? '颜色由固件生成'
      : (opts.colors === 4 ? '4 个键盘分区' : '7 个灯效色位'));
    parts.push(opts.bright ? '亮度' : '无亮度');
    parts.push(opts.speed ? '速度' : '无速度');
    parts.push(opts.dir === 'lr' ? '左→右 / 右→左（必选）'
      : (opts.dir === 'all' ? '无 / 左→右 / 右→左' : '无方向'));
    note.textContent = '这个灯效开放：' + parts.join(' · ') + '。';
    note.hidden = false;
  }
  syncLightPreview();
}

function showLightTab(key) {
  document.querySelectorAll('#light-tabs button').forEach((b) => {
    b.classList.toggle('active', b.dataset.lightTab === key);
  });
  const kb = $('light-pane-kb');
  const lb = $('light-pane-lb');
  if (kb) kb.hidden = key !== 'kb';
  if (lb) lb.hidden = key !== 'lb';
}

function renderLbEffects() {
  const box = $('lb-effects');
  if (!box) return;
  box.innerHTML = LB_EFFECTS.map((e) => {
    const bits = [
      e.colors === 0 ? '固件生成颜色' : e.colors + ' 色块',
      e.bright ? '亮度' : '无亮度',
      e.speed ? '速度' : '无速度',
      '无方向',
    ];
    return `
    <div class="lb-effect${e.colors === 0 ? ' none' : ''}">
      <b>${esc(e.name)}</b>
      <small>${esc(bits.join(' · '))}</small>
    </div>`;
  }).join('');
}

function updateLightVals() {
  const b = $('light-brightness');
  const s = $('light-speed');
  if ($('light-brightness-val')) $('light-brightness-val').textContent = b ? b.value : '--';
  if ($('light-speed-val')) $('light-speed-val').textContent = s ? s.value : '--';
  paintRange(b);
  paintRange(s);
}

function fillLightControls(oem) {
  const name = KB_EFFECT_NAME_BY_CODE[String(oem.kb_effect)] || 'Breathing';
  selectLightEffect(name);
  const light = Number(oem.kb_light);
  const lightEl = $('light-brightness');
  if (lightEl) lightEl.value = Number.isFinite(light) ? Math.max(0, Math.min(4, light)) : 3;
  const speed = KB_SPEED_TO_UI[String(oem.kb_speed)];
  const speedEl = $('light-speed');
  if (speedEl) speedEl.value = speed == null ? 2 : speed;
  const dir = KB_DIRECTION_BY_CODE[String(oem.kb_direction)];
  const dirEl = $('light-direction');
  const opts = KB_EFFECT_OPTS[name] || { dir: 'none' };
  if (dirEl) {
    if (opts.dir === 'lr') {
      dirEl.value = dir === 'RightLeft' ? 'RightLeft' : 'LeftRight';
    } else if (opts.dir === 'all') {
      dirEl.value = dir === 'RightLeft' ? 'RightLeft'
        : (dir === 'LeftRight' ? 'LeftRight' : 'None');
    }
  }
  const colors = Array.isArray(oem.kb_colors) && oem.kb_colors.length === 7 ? oem.kb_colors : null;
  for (let i = 0; i < 7; i++) {
    const inp = $('light-color-' + i);
    if (inp) inp.value = rgbToHex(colors ? colors[i] : [0, 255, 255]);
  }
  updateLightVals();
  syncLightPreview();
  lightEditorInit = true;
}

function syncLightPreview() {
  const box = $('light-preview');
  if (!box) return;
  const opts = lightOpts();
  lightSelectedColor = Math.min(lightSelectedColor, Math.max(0, opts.colors - 1));
  for (let i = 0; i < 7; i++) {
    const input = $('light-color-' + i);
    const color = input ? input.value : '#00ffff';
    if (box.children[i]) box.children[i].style.background = color;
    if (input) input.classList.toggle('active', i === lightSelectedColor);
  }
  document.querySelectorAll('#keyboard-preview [data-zone]').forEach((button, i) => {
    const color = opts.colors === 4 ? $('light-color-' + i).value
      : opts.colors ? $('light-color-' + lightSelectedColor).value
      : ['#ef6b70', '#dfaf36', '#2baa98', '#787de0'][i];
    button.style.setProperty('--zone-color', color);
    button.disabled = opts.colors !== 4;
    button.classList.toggle('active', opts.colors === 4 && i === lightSelectedColor);
    button.setAttribute('aria-pressed', String(opts.colors === 4 && i === lightSelectedColor));
  });
  $('palette-swatches').hidden = !opts.colors;
  document.querySelectorAll('#palette-swatches button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.color === ($('light-color-' + lightSelectedColor) || {}).value));
  });
  setText('keyboard-preview-caption', opts.colors === 4
    ? `正在编辑第 ${lightSelectedColor + 1} 区 · 点击其他分区，再选择颜色。示意图不代表实际键位。`
    : opts.colors ? `正在编辑第 ${lightSelectedColor + 1} 个灯效色位 · 7 色按效果播放，不是 7 个物理分区。`
      : '彩虹颜色由键盘自动生成，无需选择颜色。这里仅作示意。');
}

function buildLightEditor() {
  const fx = $('light-effects');
  if (fx && !fx.children.length) {
    KB_EFFECTS.forEach((e) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.effect = e.id;
      b.textContent = e.name;
      b.onclick = () => selectLightEffect(e.id);
      fx.appendChild(b);
    });
  }
  const prev = $('light-preview');
  if (prev && !prev.children.length) {
    for (let i = 0; i < 7; i++) {
      const s = document.createElement('span');
      s.title = '色块 ' + (i + 1);
      prev.appendChild(s);
    }
  }
  const cols = $('light-colors');
  if (cols && !cols.children.length) {
    for (let i = 0; i < 7; i++) {
      const inp = document.createElement('input');
      inp.type = 'color';
      inp.id = 'light-color-' + i;
      inp.title = '色位 ' + (i + 1);
      inp.setAttribute('aria-label', '色位 ' + (i + 1));
      inp.value = '#00ffff';
      inp.onfocus = () => { lightSelectedColor = i; syncLightPreview(); };
      inp.addEventListener('input', () => { lightSelectedColor = i; syncLightPreview(); });
      cols.appendChild(inp);
    }
  }
  const keyboard = $('keyboard-preview');
  if (!keyboard.children.length) {
    for (let i = 0; i < 4; i++) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'key-zone'; button.dataset.zone = i;
      button.setAttribute('aria-label', '选择键盘第 ' + (i + 1) + ' 区');
      button.innerHTML = '<i aria-hidden="true"></i>'.repeat(12);
      button.onclick = () => { lightSelectedColor = i; syncLightPreview(); };
      keyboard.appendChild(button);
    }
  }
  const palette = $('palette-swatches');
  if (!palette.children.length) {
    for (const [label, color] of [['青色', '#00ffff'], ['蓝色', '#0080ff'], ['紫色', '#a040ff'],
      ['粉色', '#ff40a0'], ['红色', '#ff3030'], ['橙色', '#ff9000'], ['绿色', '#30ff70'], ['白色', '#ffffff']]) {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.color = color;
      button.setAttribute('aria-label', label); button.title = label;
      button.style.setProperty('--swatch', color);
      button.onclick = () => {
        if (!lightOpts().colors) return;
        $('light-color-' + lightSelectedColor).value = color;
        syncLightPreview();
      };
      palette.appendChild(button);
    }
  }
  ['light-brightness', 'light-speed'].forEach((id) => {
    const el = $(id);
    if (el) el.oninput = updateLightVals;
  });
  const apply = $('btn-light-apply');
  if (apply) apply.onclick = () => applyLighting(false);
  $('btn-light-welcome').onclick = () => applyLighting(true);
  const read = $('btn-light-read');
  if (read) {
    read.onclick = () => {
      const oem = ((lastState || {}).hardware || {}).oem || {};
      fillLightControls(oem);
      toast('已按 OEM 上次回读填入（不一定是此刻的灯效）');
    };
  }
  const refresh = $('btn-light-refresh');
  if (refresh) {
    refresh.onclick = async () => {
      renderLighting(lastState);
      if (refresh.disabled) return;
      const oem = ((lastState || {}).hardware || {}).oem || {};
      quickSending.add('kb');
      renderLighting(lastState); renderQuick(lastState);
      try {
        // 服务只在 SetPower 里回读键盘；用当前开关状态发一条幂等命令，不改变背光。
        await api('/api/action', { action: oem.kb_power_on ? 'KB_POWER_ON' : 'KB_POWER_OFF' });
        await new Promise((r) => setTimeout(r, 800));
        if (!await poll() || !deviceOnline(lastState)) throw new Error('未取得新的在线读数，灯光草稿已保留');
        fillLightControls(((lastState || {}).hardware || {}).oem || {});
        toast('已请求键盘回读并重填，请核对当前灯效');
      } catch (e) {
        toast('刷新失败：' + e.message, true);
      } finally {
        quickSending.delete('kb');
        renderLighting(lastState); renderQuick(lastState);
      }
    };
  }
  document.querySelectorAll('#light-tabs button').forEach((b) => {
    b.onclick = () => showLightTab(b.dataset.lightTab);
  });
  showLightTab('kb');
  renderLbEffects();
  selectLightEffect(lightEffect);
  updateLightVals();
  syncLightPreview();
}

async function applyLighting(welcome = false) {
  if (lightBusy || lightSubmitted || $('btn-light-apply').disabled || $('light-editor').hidden) return;
  if (welcome && !confirm('将当前灯效同步到欢迎模式？同步时键盘会播放一次。')) return;
  const body = { ...lightingEditorPayload(), welcome };
  lightBusy = true;
  renderLighting(lastState);
  try {
    const r = await api('/api/keyboard-effect', body);
    toast(welcome ? '已下发当前灯效及欢迎模式同步' : (r.detail || '已下发灯效，等待读数确认'));
    lightSubmitted = { body, until: Date.now() + 15000 };
  } catch (e) {
    toast('下发失败：' + e.message, true);
  } finally {
    lightBusy = false;
    renderLighting(lastState);
  }
  poll();
  setTimeout(poll, 1200);
}

function updateLightPower(oem, caps) {
  const btn = $('btn-light-power');
  setText('light-power-state', oem.kb_power_on == null ? '未知' : oem.kb_power_on ? '已开启' : '已关闭');
  btn.disabled = !deviceOnline(lastState) || (caps['lighting.rgb.write'] || {}).state !== 'verified'
    || oem.kb_power_on == null || lightBusy || !!lightSubmitted || !!PENDING.kbpower
    || quickSending.has('kb') || presetBusy;
  btn.textContent = PENDING.kbpower || quickSending.has('kb') ? '等待确认…'
    : oem.kb_power_on == null ? '等待状态' : oem.kb_power_on ? '关闭背光' : '打开背光';
  btn.onclick = () => toggleQuick('kb');
}

function renderLighting(s) {
  const editor = $('light-editor');
  const hint = $('light-hint');
  if (!editor) return;
  const caps = (s || {}).capabilities || {};
  const oem = ((s || {}).hardware || {}).oem || {};
  const mqCh = ((s || {}).channels || []).find((c) => c.name === 'mqtt') || {};
  const cap = caps['lighting.effect.write'] || {};
  const can = mqCh.alive && cap.state === 'verified';
  if (lightSubmitted) {
    const body = lightSubmitted.body;
    const confirmed = deviceOnline(s) && lightingMatches(oem, body);
    if (confirmed) { lightSubmitted = null; toast('已生效：当前键盘灯效回读一致'); }
    else if (Date.now() > lightSubmitted.until) {
      lightSubmitted = null;
      toast('灯效未等到一致回读，编辑内容保留，请核对键盘状态', true);
    }
  }
  const locked = lightBusy || !!lightSubmitted || !!PENDING.kbpower || quickSending.has('kb') || presetBusy;
  const editable = can && deviceOnline(s) && oem.kb_power_on === true && !locked;
  editor.inert = !editable;
  $('btn-light-apply').disabled = !editable;
  $('btn-light-welcome').disabled = !editable;
  $('btn-light-refresh').disabled = locked || !deviceOnline(s)
    || typeof oem.kb_power_on !== 'boolean' || (caps['lighting.rgb.write'] || {}).state !== 'verified';
  $('btn-light-read').disabled = locked || !deviceOnline(s);
  updateLightPower(oem, caps);
  if (!can) {
    editor.hidden = true;
    hint.classList.add('err');
    hint.textContent = '灯效写入需要 GCUBridge 通道和 lighting.effect.write 能力（当前 '
      + (cap.state || '未上报') + '）。';
    return;
  }
  if (oem.kb_power_on !== true) {
    editor.hidden = true;
    hint.classList.remove('err');
    hint.textContent = oem.kb_power_on == null ? '背光开关状态未知，当前不可编辑或同步。'
      : '打开键盘背光后才能编辑灯效和同步欢迎模式。';
    return;
  }
  editor.hidden = false;
  hint.classList.remove('err');
  if (!lightEditorInit) fillLightControls(oem);
  hint.textContent = locked ? '操作进行中，正在等待读数确认；编辑内容会保留。'
    : '选灯效 → 选色位和颜色 → 应用灯效。预览不会改变键盘，保存时也不会改变背光开关。';
}

// ---------- 风扇曲线（EC 直读，只读） ----------
// 表布局来自同源机型反编译出的 SetEcFanTable/GetEcFanTable（notes/hardware-channels.md 6.11）：
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

function chartColor(token) {
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim();
}

// 风扇曲线图：横轴温度、纵轴占空；信箱/哨兵点不画（它们不是温度/占空）。
function drawFanCurveChart(series, opts) {
  const o = opts || {};
  const cv = $(o.canvasId || 'fan-curve-chart');
  const wrap = o.wrapId === null ? null : $(o.wrapId || 'fan-curve-chart-wrap');
  if (!cv) return;
  const ok = (p) => p && p.duty_pct != null && p.up_t != null && !p.mailbox && !p.sentinel;
  const any = (series || []).some((s) => (s.points || []).some(ok));
  if (!any) {
    if (wrap && o.hideWhenEmpty !== false) wrap.hidden = true;
    return;
  }
  if (wrap) wrap.hidden = false;
  if (cv.id === 'fan-curve-chart') document.querySelector('.fan-axis-note').textContent = 'EC 只读对照 · 横轴温度（线性刻度） · 纵轴占空';
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 900;
  const h = cv.clientHeight || 190;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const padL = 42; const padR = 16; const padT = 16; const padB = 26;
  const iw = w - padL - padR; const ih = h - padT - padB;
  const px = (t) => padL + Math.max(0, Math.min(100, Number(t))) / 100 * iw;
  const py = (d) => padT + ih - Math.max(0, Math.min(100, Number(d))) / 100 * ih;
  const line = chartColor('--line'); const dim = chartColor('--dim');
  g.font = '10px system-ui'; g.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const yy = padT + ih * i / 4;
    g.strokeStyle = line;
    g.beginPath(); g.moveTo(padL, yy); g.lineTo(w - padR, yy); g.stroke();
    g.fillStyle = dim; g.textAlign = 'right';
    g.fillText(String(100 - i * 25) + '%', padL - 6, yy + 3);
  }
  for (let t = 0; t <= 100; t += 20) {
    const xx = px(t);
    g.strokeStyle = line;
    g.beginPath(); g.moveTo(xx, padT); g.lineTo(xx, padT + ih); g.stroke();
    g.fillStyle = dim; g.textAlign = 'center';
    g.fillText(t + '°', xx, padT + ih + 16);
  }
  (series || []).forEach((s, si) => {
    const pts = (s.points || []).filter(ok).slice().sort((a, b) => a.up_t - b.up_t);
    if (!pts.length) return;
    const color = chartColor(s.color);
    g.strokeStyle = color; g.lineWidth = 2;
    g.beginPath();
    pts.forEach((p, i) => {
      if (i === 0) g.moveTo(px(p.up_t), py(p.duty_pct));
      else g.lineTo(px(p.up_t), py(p.duty_pct));
    });
    g.stroke();
    g.fillStyle = color;
    pts.forEach((p) => {
      g.beginPath(); g.arc(px(p.up_t), py(p.duty_pct), 2.6, 0, Math.PI * 2); g.fill();
    });
    g.textAlign = 'right'; g.font = '11px system-ui';
    g.fillText(s.label, w - padR, padT + 12 + si * 15);
  });
}

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
  drawFanCurveChart([
    { label: 'EC CPU', color: '--cyan', points: t.CPU },
    { label: 'EC GPU', color: '--purple', points: t.GPU },
  ]);
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
  if (curveLoading || oemLoading || oemWriting) return;
  fanSource = 'ec';
  const epoch = ++oemEpoch;
  curveLoading = true;
  oemChartGeometry = null;
  const btn = $('btn-fan-curve');
  btn.textContent = '读取中（约 3 秒）…';
  renderOemCurve();
  try {
    const loaded = await api('/api/fan-curve');
    if (epoch !== oemEpoch || fanSource !== 'ec') return;
    curveData = loaded;
    if (say) toast(loaded.ok ? '已读取 EC 曲线，只读对照，不修改 OEM 草稿'
      : '读取不完整：' + (loaded.reason || '未知原因'), !loaded.ok);
    setText('fan-curve-tag', 'EC 对照 · 只读');
    setText('fan-table-meta', 'EC 实际表 · OEM 草稿保留');
    $('fan-curve-empty').hidden = true;
    renderFanCurve();
  } catch (e) {
    if (say) toast('读取失败：' + e.message, true);
  } finally {
    curveLoading = false;
    btn.textContent = 'EC 对照';
    renderOemCurve();
  }
}

// ---------- 风扇曲线（OEM 表，可写占空） ----------
// 写入走 GCUBridge 的 SET_FAN_SPEED_CURVE_SETTING（服务落盘 + 写 EC）。
// 只能改 Duty；Name 必须使用服务报告的当前表名，绝不写死。
let fanSource = 'mqtt';
let oemEpoch = 0;
let oemReadAttempted = false;
let oemReadError = '';
let oemCurve = null;
let oemOrig = null;
let oemOrigName = null;
let oemDraft = null;
let oemType = 'CPU';
let oemLoading = false;
let oemWriting = false;
let oemDragIndex = null;
let oemFocusIndex = null;
let oemChartGeometry = null;

function normalizeOemCurve(loaded) {
  if (!loaded || !loaded.ok || !loaded.name || loaded.stale) {
    throw new Error((loaded || {}).reason || '未取得新鲜 OEM 表，不能编辑缓存曲线');
  }
  const tables = {};
  for (const type of ['CPU', 'GPU']) {
    const points = (loaded.tables || {})[type];
    if (!Array.isArray(points) || points.length !== 16) throw new Error(type + ' 表不是完整的 16 点');
    const ordered = Array(16);
    for (const point of points) {
      if (!point || !Number.isInteger(point.id) || point.id < 0 || point.id > 15 || ordered[point.id]
          || !Number.isInteger(point.duty_pct) || point.duty_pct < 0 || point.duty_pct > 100
          || !Number.isInteger(point.up_t) || (point.up_t !== 255 && (point.up_t < 0 || point.up_t > 100))) {
        throw new Error(type + ' 表节点编号、温度或占空不合法');
      }
      ordered[point.id] = { ...point, sentinel: point.up_t === 255 };
    }
    const active = ordered.filter((p) => !p.sentinel && !p.mailbox);
    if (!active.length || active.some((p, i) => i > 0 && p.up_t < active[i - 1].up_t)) {
      throw new Error(type + ' 表温度节点顺序异常');
    }
    tables[type] = ordered;
  }
  return { ...loaded, tables };
}

function oemWritable() {
  const cap = (((lastState || {}).capabilities) || {})['fan.curve.write'] || {};
  const name = ((((lastState || {}).hardware || {}).oem) || {}).fan_table;
  return fanSource === 'mqtt' && !!oemCurve && !oemCurve.stale && !oemLoading && !oemWriting
    && cap.state === 'verified' && currentOemMode(lastState) === 'turbo' && !PENDING.mode
    && !presetBusy && deviceOnline(lastState)
    && !!((lastState || {}).meta || {}).fan_curve_expected_name
    && (!name || name === oemCurve.name);
}

function oemActivePoints() {
  return ((oemCurve && oemCurve.tables || {})[oemType] || [])
    .filter((p) => !p.sentinel && !p.mailbox);
}

function oemDraftChanged(type) {
  return !!oemDraft && !!oemOrig && oemDraft[type].some((v, i) => v !== oemOrig[type][i]);
}

function setOemDuty(idx, value) {
  if (!oemWritable() || !oemDraft || !Number.isFinite(value)) return;
  const active = oemActivePoints();
  const pos = active.findIndex((p) => p.id === idx);
  if (pos < 0) return;
  const prev = pos > 0 ? oemDraft[oemType][active[pos - 1].id] : 0;
  const next = pos + 1 < active.length ? oemDraft[oemType][active[pos + 1].id] : 100;
  oemDraft[oemType][idx] = Math.max(prev, Math.min(next, Math.round(value)));
  $('fan-default-feedback').hidden = true;
  oemFocusIndex = idx;
  const input = document.querySelector(`.fan-point-input[data-idx="${idx}"]`);
  if (input) input.value = oemDraft[oemType][idx];
  $('btn-curve-undo').disabled = !oemDraftChanged(oemType);
  setText('fan-table-meta', `${active.length} 个温度点 · ${oemDraftChanged(oemType) ? '有未保存的修改' : '当前读数'}`);
  drawOemCurveEditor();
}

function drawOemCurveEditor() {
  const cv = $('fan-curve-chart');
  const wrap = $('fan-curve-chart-wrap');
  if (!cv || fanSource !== 'mqtt' || !oemCurve || !oemDraft) return;
  const points = oemActivePoints();
  if (!points.length) return;
  if (wrap) wrap.hidden = false;
  cv.style.setProperty('--fan-node-count', points.length);
  document.querySelector('.fan-axis-note').textContent = '节点等距排列，标签为真实温度（非线性刻度） · 纵轴占空 · 窄屏可横向滚动';
  const w = cv.clientWidth;
  const h = cv.clientHeight;
  if (!w || !h) { oemChartGeometry = null; return; }
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const pad = { left: 48, right: 24, top: 26, bottom: 38 };
  const iw = w - pad.left - pad.right;
  const ih = h - pad.top - pad.bottom;
  const px = (index) => pad.left + (index + .5) * iw / points.length;
  const py = (d) => pad.top + ih - Math.max(0, Math.min(100, Number(d))) / 100 * ih;

  const line = chartColor('--line'); const dim = chartColor('--dim');
  const cyan = chartColor('--cyan'); const panel = chartColor('--panel');
  g.font = '11px system-ui';
  g.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const value = 100 - i * 25;
    const y = py(value);
    g.strokeStyle = i === 4 ? dim : line;
    g.beginPath(); g.moveTo(pad.left, y); g.lineTo(w - pad.right, y); g.stroke();
    g.fillStyle = dim; g.textAlign = 'right'; g.fillText(value + '%', pad.left - 8, y + 4);
  }
  points.forEach((p, index) => {
    const x = px(index);
    g.strokeStyle = line;
    g.beginPath(); g.moveTo(x, pad.top); g.lineTo(x, pad.top + ih); g.stroke();
    g.fillStyle = dim; g.textAlign = 'center'; g.fillText(p.up_t + '°C', x, h - 10);
  });

  const mapped = points.map((p, index) => ({
    idx: p.id, temp: p.up_t, duty: oemDraft[oemType][p.id],
    x: px(index), y: py(oemDraft[oemType][p.id]),
  }));
  const fill = g.createLinearGradient(0, pad.top, 0, pad.top + ih);
  fill.addColorStop(0, chartColor('--color-chart-fill-start'));
  fill.addColorStop(1, chartColor('--color-chart-fill-end'));
  g.beginPath();
  mapped.forEach((p, i) => { if (i) g.lineTo(p.x, p.y); else g.moveTo(p.x, p.y); });
  g.lineTo(mapped[mapped.length - 1].x, pad.top + ih);
  g.lineTo(mapped[0].x, pad.top + ih);
  g.closePath(); g.fillStyle = fill; g.fill();

  g.beginPath();
  mapped.forEach((p, i) => { if (i) g.lineTo(p.x, p.y); else g.moveTo(p.x, p.y); });
  g.strokeStyle = cyan; g.lineWidth = 3; g.lineJoin = 'round'; g.stroke();
  mapped.forEach((p) => {
    g.beginPath(); g.arc(p.x, p.y, p.idx === oemDragIndex ? 8 : 6, 0, Math.PI * 2);
    g.fillStyle = p.idx === oemDragIndex ? panel : cyan; g.fill();
    g.strokeStyle = p.idx === oemDragIndex ? cyan : panel; g.lineWidth = 3; g.stroke();
  });
  const selected = mapped.find((p) => p.idx === (oemDragIndex == null ? oemFocusIndex : oemDragIndex));
  if (selected) {
    g.fillStyle = chartColor('--txt'); g.font = '600 12px system-ui';
    const label = selected.temp + ' °C · ' + selected.duty + '%';
    const half = g.measureText(label).width / 2;
    const x = Math.max(pad.left + half, Math.min(w - pad.right - half, selected.x));
    const y = selected.y < 45 ? selected.y + 26 : selected.y - 15;
    g.textAlign = 'center'; g.fillText(label, x, y);
  }
  oemChartGeometry = { mapped, pad, ih, h };
}

function bindOemCurveCanvas() {
  const cv = $('fan-curve-chart');
  if (!cv || cv.dataset.dragBound) return;
  cv.dataset.dragBound = '1';
  cv.style.touchAction = 'pan-x';
  cv.onpointerdown = (e) => {
    if (!oemWritable() || !oemChartGeometry) return;
    const rect = cv.getBoundingClientRect();
    const x = e.clientX - rect.left; const y = e.clientY - rect.top;
    const nearest = oemChartGeometry.mapped.reduce((best, p) => {
      const dist = Math.hypot(p.x - x, p.y - y);
      return !best || dist < best.dist ? { idx: p.idx, dist } : best;
    }, null);
    if (!nearest || nearest.dist > 22) return;
    oemDragIndex = nearest.idx;
    cv.setPointerCapture(e.pointerId);
    drawOemCurveEditor();
  };
  cv.onpointermove = (e) => {
    if (!oemWritable() || !oemChartGeometry) return;
    const rect = cv.getBoundingClientRect();
    const y = e.clientY - rect.top;
    if (oemDragIndex == null) {
      const x = e.clientX - rect.left;
      const nearest = oemChartGeometry.mapped.reduce((a, b) =>
        Math.hypot(a.x - x, a.y - y) < Math.hypot(b.x - x, b.y - y) ? a : b);
      oemFocusIndex = Math.hypot(nearest.x - x, nearest.y - y) <= 22 ? nearest.idx : null;
      drawOemCurveEditor();
      return;
    }
    const duty = (oemChartGeometry.pad.top + oemChartGeometry.ih - y) * 100 / oemChartGeometry.ih;
    setOemDuty(oemDragIndex, duty);
  };
  const finish = (e) => {
    if (oemDragIndex == null) return;
    oemDragIndex = null;
    if (cv.hasPointerCapture(e.pointerId)) cv.releasePointerCapture(e.pointerId);
    drawOemCurveEditor();
  };
  cv.onpointerup = finish;
  cv.onpointercancel = finish;
  cv.onpointerleave = () => {
    if (oemDragIndex == null) { oemFocusIndex = null; drawOemCurveEditor(); }
  };
}

function renderOemCurve() {
  const box = $('fan-curve-editor');
  const hint = $('fan-curve-hint');
  if (!box) return;
  $('btn-fan-curve-oem').disabled = oemLoading || oemWriting || curveLoading;
  $('btn-fan-curve').disabled = oemLoading || oemWriting || curveLoading;
  $('btn-curve-undo').disabled = !oemWritable() || !oemDraftChanged(oemType);
  $('btn-curve-default').disabled = !oemWritable();
  $('fan-curve-empty').hidden = !!oemCurve || !!curveData;
  setText('fan-independent-state', '独立输出 · ' + (oemCurve ? triText(oemCurve.fan_control_respective, '开启 · 只读', '关闭 · 只读') : '等待回读'));
  if (fanSource !== 'mqtt' || !oemCurve) { box.innerHTML = ''; return; }
  hint.classList.remove('err');
  const can = oemWritable();
  const points = oemActivePoints();
  const duties = oemDraft[oemType];
  setText('fan-table-meta', `${oemCurve.name} · ${points.length} 个温度点 · ${oemDraftChanged(oemType) ? '草稿未应用' : '当前读数'}`);
  document.querySelectorAll('[data-curve-table]').forEach((button) => {
    button.classList.toggle('active', button.dataset.curveTable === oemType);
    button.disabled = oemLoading || oemWriting || curveLoading;
  });
  box.innerHTML = `<div class="fan-point-editor">${points.map((p) => `
      <label><span>${p.up_t}°</span><input class="fan-point-input" data-idx="${p.id}" type="number"
        min="0" max="100" step="1" value="${duties[p.id]}" ${can ? '' : 'disabled'}><small>%</small></label>`).join('')}</div>
    <div class="fan-editor-footer">
      <span>确认修改后保存；另一张曲线的草稿会保留。温度点和保护设置不改变。</span>
      <button id="btn-curve-apply" class="primary" ${can ? '' : 'disabled'}>保存 ${oemType} 曲线</button>
    </div>`;
  box.querySelectorAll('.fan-point-input').forEach((input) => {
    input.onchange = () => setOemDuty(Number(input.dataset.idx), Number(input.value));
  });
  $('btn-curve-apply').onclick = applyOemCurve;
  bindOemCurveCanvas();
  drawOemCurveEditor();
  hint.textContent = oemReadError ? '读取失败：' + oemReadError + '；草稿保留，重新读取前不可下发。'
    : oemWriting ? '正在下发并等待回读，暂时锁定曲线编辑。'
    : '恢复默认只填入选中曲线的占空草稿，保存后才生效；撤销回到最近读数。'
      + (!((lastState || {}).meta || {}).fan_curve_expected_name
        ? ' 后端安全校验更新需下次重启面板生效，目前曲线保持只读。'
        : (can ? ' 应用前会校验当前表名并检查高温保护。' : ' 当前条件不满足，曲线保持只读。'));
}

async function loadOemCurve(say, updatedType = null, submittedDuties = null) {
  if (oemLoading || curveLoading || (oemWriting && !updatedType)) return;
  if (say && ['CPU', 'GPU'].some(oemDraftChanged)
      && !confirm('重新读取会覆盖 CPU 和 GPU 的未应用草稿，继续？')) return;
  fanSource = 'mqtt';
  oemReadAttempted = true;
  oemReadError = '';
  const epoch = ++oemEpoch;
  const mode = currentOemMode(lastState);
  oemLoading = true;
  const btn = $('btn-fan-curve-oem');
  btn.textContent = '读取中…';
  renderOemCurve();
  try {
    const loaded = normalizeOemCurve(await api('/api/fan-curve?source=mqtt'));
    if (epoch !== oemEpoch || mode !== currentOemMode(lastState) || fanSource !== 'mqtt') return;
    const currentName = ((((lastState || {}).hardware || {}).oem) || {}).fan_table;
    if (currentName && currentName !== loaded.name) throw new Error('OEM 表名已变化，请等状态刷新后重新读取');
    if (updatedType && submittedDuties && !loaded.tables[updatedType].every((p) => p.duty_pct === submittedDuties[p.id])) {
      throw new Error('提交尚未确认，回读与下发值不一致，请核对后重新读取');
    }
    const preserveOther = updatedType && oemDraft && oemOrigName === loaded.name;
    oemCurve = loaded;
    oemOrigName = loaded.name;
    oemOrig = oemOrig || {};
    oemDraft = oemDraft || {};
    for (const type of ['CPU', 'GPU']) {
      const values = loaded.tables[type].map((p) => p.duty_pct);
      if (!preserveOther || type === updatedType) {
        oemOrig[type] = values.slice();
        oemDraft[type] = values.slice();
      }
    }
    oemChartGeometry = null;
    curveData = null;
    $('fan-default-feedback').hidden = true;
    $('fan-curve').innerHTML = '';
    setText('fan-curve-tag', '设备曲线 · 可编辑');
    if (say) toast(`已读取 ${loaded.name} 的当前曲线`);
    return true;
  } catch (e) {
    if (epoch === oemEpoch) {
      oemReadError = e.message;
      if (oemCurve) oemCurve = { ...oemCurve, stale: true };
      else $('fan-curve-chart-wrap').hidden = true;
      setText('fan-table-meta', '读取失败');
      setText('fan-curve-hint', '读取失败：' + e.message);
      if (say) toast('读取 OEM 表失败：' + e.message, true);
    }
  } finally {
    oemLoading = false;
    btn.textContent = '读取当前曲线';
    renderOemCurve();
    if (lastState) fillSpc(lastState);
    ensureCreatorCurve();
  }
}

function restoreOemDraft() {
  if (!oemWritable() || !oemOrig) return;
  oemDraft[oemType] = oemOrig[oemType].slice();
  $('fan-default-feedback').hidden = true;
  renderOemCurve();
  toast(`已撤销 ${oemType} 草稿改动，没有下发硬件命令`);
}

async function restoreOemDefaults() {
  if (!oemWritable()) return;
  const type = oemType, name = oemCurve.name, epoch = oemEpoch;
  if (oemDraftChanged(type) && !confirm(`用本机默认占空替换 ${type} 的未保存草稿？另一张曲线不变。`)) return;
  const feedback = $('fan-default-feedback');
  feedback.hidden = false;
  feedback.textContent = '正在读取本机默认缓存，不发送硬件命令…';
  oemLoading = true;
  renderOemCurve();
  try {
    const loaded = normalizeOemCurve(await api('/api/fan-curve/defaults?name=' + encodeURIComponent(name)));
    oemLoading = false;
    if (epoch !== oemEpoch || !oemCurve || name !== oemCurve.name || type !== oemType) return;
    if (!oemWritable()) throw new Error('设备状态已变化，请重新读取当前曲线');
    if (loaded.name !== name || !loaded.tables[type].every((p, i) =>
      p.up_t === oemCurve.tables[type][i].up_t && p.down_t === oemCurve.tables[type][i].down_t)) {
      throw new Error('默认表与当前表的温度阈值不一致，不能仅恢复占空');
    }
    oemDraft[type] = loaded.tables[type].map((p, i) =>
      oemCurve.tables[type][i].sentinel || oemCurve.tables[type][i].mailbox ? oemDraft[type][i] : p.duty_pct);
    feedback.textContent = `${type} 默认占空已填入草稿（来源：${loaded.source}），尚未保存；另一张曲线、温度阈值与独立输出不变。`;
    toast(`${type} 默认占空已填入，请核对后保存`);
  } catch (e) {
    if (epoch === oemEpoch) {
      const reason = e.message === 'not found' || e.message === 'HTTP 404'
        ? '运行中的后端尚不支持默认表读取，请正常重启面板后重试' : e.message;
      feedback.textContent = '未恢复默认，草稿保留：' + reason;
      toast(feedback.textContent, true);
    }
  } finally {
    oemLoading = false;
    if (epoch !== oemEpoch) feedback.hidden = true;
    renderOemCurve();
  }
}

async function applyOemCurve() {
  if (!oemWritable() || !oemDraft) return;
  const type = oemType;
  const name = oemCurve.name;
  const epoch = oemEpoch;
  const duties = oemDraft[type].slice();
  const active = oemActivePoints();
  if (duties.length !== 16 || duties.some((v) => !Number.isInteger(v) || v < 0 || v > 100)) {
    toast('曲线必须包含 16 个 0~100 的整数占空', true); return;
  }
  if (active.some((p, i) => i > 0 && duties[p.id] < duties[active[i - 1].id])) {
    toast('曲线不能倒退：后一个温度点必须大于等于前一个', true); return;
  }
  if (!confirm(`应用 ${name} ${type} 曲线？\n\n有效温度点：${active.length} 个\n占空范围：${Math.min(...duties)}% ~ ${Math.max(...duties)}%\n\n`
      + '仅修改占空，温度点不变；表名变化或高温保护开启时拒绝下发。')) return;
  oemWriting = true;
  oemDragIndex = null;
  renderOemCurve();
  toast('正在下发，等待服务回读…');
  try {
    const r = await api('/api/fan-curve', { duties, type, expected_name: name });
    toast('已下发：' + (r.detail || '等待曲线回读'));
    if (epoch === oemEpoch) {
      const confirmed = await loadOemCurve(false, type, duties);
      if (confirmed) toast(`已生效：${name} ${type} 曲线回读一致`);
      else if (epoch === oemEpoch) toast('曲线尚未确认，草稿保留，请核对后重新读取', true);
    }
  } catch (e) {
    toast('下发失败：' + e.message, true);
  } finally {
    oemWriting = false;
    renderOemCurve();
  }
}

function renderCaps(s) {
  const caps = s.capabilities || {};
  const labels = META.cap_labels || {};
  const shown = ['mode.read', 'mode.write', 'power_limit.read', 'power_limit.write',
                 'fan.rpm', 'fan.mode', 'fan.curve', 'fan.curve.write', 'ec.temp',
                 'battery.limit',
                 'battery.mode.write', 'winkey.write', 'gpu.mux', 'lighting.rgb',
                 'lighting.rgb.write'];
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
  // 之前只显示档位，用户选了自适应却看到标题一会儿流畅一会儿性能，以为按键坏了。
  $('tier-intent').textContent = s
    ? `控制意图：${it.name}${locked ? '' : '（不锁模式，按负载自动换）'}`
    : '控制意图：--';
  $('tier-now').textContent = locked ? `锁定 · ${tierName}` : tierName;
  $('tier-now').className = 'tier-now' + (locked ? ' locked' : '');
  const x = (s && s.sensor) || {};
  $('tier-clock').textContent = x.cpu_mhz ? (x.cpu_mhz + ' MHz') : '-- MHz';
  $('tier-reason').textContent = s
    ? (s.reason ? '最近换挡：' + s.reason : '')
    : '等待数据…';
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
  if (document.hidden) return false;
  const requestId = stateRequestId + 1;
  try {
    const s = await readDeviceState();
    if (!s || requestId < stateSettledId) return false;
    META = Object.assign(META, s.meta || {});
    const ver = (s.meta || {}).version;
    if (ver) $('foot-version').textContent = 'Umi Control Panel v' + ver + ' · 本地运行';
    // 硬件档要 24 秒左右才回读得到；等到值对上时补一句「已生效」
    const wantMode = PENDING.mode ? PENDING.mode.want : null;
    const modePend = pendingFor('mode', deviceOnline(s) ? currentOemMode(s) : null);
    if (wantMode && !modePend && currentOemMode(s) === wantMode) toast('OEM 硬件档：已生效');
    renderTier(s); renderPills(s); renderIntents(); renderProfiles(s); renderMeters(s);
    renderHardware(s); renderOem(s); renderLighting(s); renderCaps(s);
    renderOverview(s); renderCreator(s); renderCustom(s); renderFanSpeed(s); renderQuick(s);
    if (presetBusy) renderPresetLock();
    if ((s.bench || {}).running || benchWasRunning) loadBench();
    benchWasRunning = !!(s.bench || {}).running;
    return true;
  } catch (e) {
    if (requestId < stateSettledId) return false;
    $('foot-status').textContent = '取数失败：' + e.message;
    if (lastState) {
      lastState = { ...lastState, ts: 0 };
      renderQuick(lastState); renderCreator(lastState); renderCustom(lastState); renderLighting(lastState); renderFanSpeed(lastState);
    }
    return false;
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
const TIER_COLOR = { perf: '--red', mid: '--amber', bal: '--cyan', eco: '--green' };
const SERIES = {
  temp: [
    { key: 'cpu_temp', label: 'CPU 温度', axis: 'L', unit: '°C', color: '--cyan' },
    { key: 'cpu_mhz', label: '实际频率', axis: 'R', unit: 'MHz', color: '--purple' },
  ],
  load: [
    { key: 'cpu_pct', label: 'CPU 占用', axis: 'L', unit: '%', color: '--cyan', max: 100 },
    { key: 'gpu_pct', label: 'GPU 占用', axis: 'L', unit: '%', color: '--purple', max: 100 },
    { key: 'fan_rpm', label: '风扇转速', axis: 'R', unit: ' RPM', color: '--amber' },
  ],
  // 墙单独一轴：60 W 和 1500 MHz 差 25 倍，和工作温度/占用塞同轴会把墙压成一条直线
  wall: [
    { key: 'pl1_setting', label: 'PL1 功耗墙', axis: 'L', unit: ' W', color: '--cyan' },
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
  const dim = chartColor('--dim');
  const box = document.getElementById(canvasId.replace('chart-', 'legend-'));
  if (rows.length < 3) {
    g.fillStyle = dim; g.font = '12px system-ui';
    g.fillText(`正在积累数据（每 5 秒一个点，已采到 ${rows.length} 个）…`, 12, h / 2);
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
  g.strokeStyle = chartColor('--line'); g.fillStyle = dim; g.font = '10px system-ui'; g.lineWidth = 1;
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
    g.strokeStyle = chartColor(TIER_COLOR[m.tier] || '--dim');
    g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x, padT); g.lineTo(x, padT + ih); g.stroke();
    g.setLineDash([]);
  });
  // 曲线
  series.forEach((s) => {
    g.strokeStyle = chartColor(s.color); g.lineWidth = 1.6; g.beginPath();
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
  g.fillStyle = dim; g.textAlign = 'left';
  g.fillText(new Date(t0 * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), padL, h - 4);
  g.textAlign = 'right';
  g.fillText(new Date(t1 * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), w - padR, h - 4);
  if (box) {
    box.innerHTML = series.map((s) => {
      const vals = rows.map((r) => r[s.key]).filter((v) => v != null);
      const lo = vals.length ? Math.min(...vals) : null;
      const hi = vals.length ? Math.max(...vals) : null;
      return `<span><i style="background:${chartColor(s.color)}"></i>${esc(s.label)} `
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
    drawChart('chart-wall', SERIES.wall, d);
    drawChart('chart-ov-temp', SERIES.temp, d);
    drawChart('chart-ov-load', SERIES.load, d);
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
  // 用户不用截图、也不用描述「我点了什么」：体检结果 + 最近日志一次拷走
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
// 风扇曲线不自动读：一轮近百次读约 3 秒，只有用户点了才打 EC
$('btn-fan-curve').onclick = () => loadFanCurve(true);
// OEM 表走 GCUBridge，读得快；编辑器里只有做过可逆验证的「写占空」是亮的
$('btn-fan-curve-oem').onclick = () => loadOemCurve(true);
$('quick-kb').onclick = () => toggleQuick('kb');
$('quick-win').onclick = () => toggleQuick('win');
$('fanspeed-enabled').onchange = () => { fanSpeedDirty = true; renderFanSpeed(lastState); };
$('fanspeed-value').oninput = () => { fanSpeedDirty = true; renderFanSpeed(lastState); };
$('btn-fanspeed-apply').onclick = () => applyFanSpeed();
$('btn-fanspeed-off').onclick = () => applyFanSpeed(false);
$('btn-stop').onclick = async () => {
  if (!confirm('确定停止面板服务？停止后自适应调度与掉档守护都会失效。')) return;
  try { await api('/api/shutdown', {}); toast('服务已停止'); }
  catch (e) { toast('停止失败：' + e.message, true); }
};

// ---------- 自定义方案 ----------
function showPresetForm(on) {
  const form = $('preset-form');
  if (!form) return;
  form.hidden = !on;
  if (on) {
    if ($('preset-name')) { $('preset-name').value = ''; $('preset-name').focus(); }
  }
}
$('btn-preset-new').onclick = () => showPresetForm(true);
$('btn-preset-cancel').onclick = () => showPresetForm(false);
$('btn-preset-save').onclick = () => {
  const name = (($('preset-name') || {}).value || '').trim();
  if (!name) { toast('先给方案起个名字', true); return; }
  const include = {
    mode: !!($('preset-inc-mode') || {}).checked,
    tcc: !!($('preset-inc-tcc') || {}).checked,
    fanspeed: !!($('preset-inc-fanspeed') || {}).checked,
    light: !!($('preset-inc-light') || {}).checked,
  };
  if (!deviceOnline(lastState) || presetBusy || plBusy || plSubmitted || spcBusy || spcSubmitted
      || fanSpeedBusy || fanSpeedSubmitted || lightBusy || lightSubmitted || quickSending.size || Object.keys(PENDING).length) {
    toast('请等待设备连接和正在生效的设置完成后再保存方案', true); return;
  }
  const p = capturePreset(name, include);
  const missing = [[include.mode, p.mode, '造物者模式'], [include.tcc, p.tcc, '提前降温'],
    [include.fanspeed, p.fanSpeed, '风扇响应'], [include.light, p.lighting, '键盘灯效']]
    .filter(([selected, value]) => selected && !value).map(([, , label]) => label);
  if (missing.length) {
    toast('未保存：' + missing.join('、') + '没有可用读数，请先读取或取消勾选', true); return;
  }
  if (!p.mode && !p.tcc && !p.fanSpeed && !p.lighting) {
    toast('当前读数里没有可保存的已验证设置，等面板转两秒再试', true);
    return;
  }
  presets.unshift(p);
  savePresets();
  showPresetForm(false);
  renderPresets();
  toast(`方案「${name}」已保存在本机浏览器`);
};
$('btn-preset-refresh').onclick = () => { poll(); toast('正在刷新读数…'); };

// ---------- 高级：临时功耗墙 ----------
['pl1', 'pl2', 'pl4'].forEach((k) => {
  const el = $(k + '-range');
  if (!el) return;
  el.addEventListener('input', () => {
    plDirty = true;
    const lab = $(k + '-val');
    if (lab) lab.textContent = el.value + ' W';
    paintRange(el);
  });
});
$('btn-pl-apply').onclick = async () => {
  fillPlControls(lastState);
  if ($('btn-pl-apply').disabled) return;
  const body = {};
  const bounds = plBounds(lastState);
  if (!bounds) return;
  for (const k of ['pl1', 'pl2', 'pl4']) {
    const el = $(k + '-range');
    if (el.disabled) continue;
    const v = Number(el.value);
    if (!Number.isInteger(v) || v < bounds[k][0] || v > bounds[k][1]) {
      toast('功耗墙超出 OEM 范围，请先重填读数', true); return;
    }
    body[k] = v;
  }
  if (!Object.keys(body).length) return;
  if ((body.pl1 != null && body.pl2 != null && body.pl1 > body.pl2)
      || (body.pl2 != null && body.pl4 != null && body.pl2 > body.pl4)) {
    toast('请保持长时功耗 ≤ 短时功耗 ≤ 瞬时功耗上限', true); return;
  }
  if (!confirm('下发临时功耗墙？\n\n'
    + Object.entries(body).map(([key, value]) => key.toUpperCase() + ' ' + value + ' W').join('\n')
    + '\n\n未回读的项目不下发；切档、息屏或 OEM 服务重算都可能改回去。')) return;
  const epoch = plEpoch;
  plBusy = true; plDirty = true;
  fillPlControls(lastState); renderCreator(lastState);
  try {
    await api('/api/power-limits', body);
    if (epoch !== plEpoch) { plNeedsRefresh = true; return; }
    plSubmitted = { body, until: Date.now() + 20000 };
    toast('临时功耗已下发，等待设备确认');
  } catch (e) {
    toast('下发失败：' + e.message, true);
  } finally {
    plBusy = false;
    fillPlControls(lastState); renderCreator(lastState);
  }
  await poll();
};
$('btn-pl-refresh').onclick = async () => {
  fillPlControls(lastState);
  if ($('btn-pl-refresh').disabled) return;
  await poll();
  if (deviceOnline(lastState)) fillPlControls(lastState, true);
};

// ---------- 造物者模式 ----------
$('btn-creator-mode').onclick = async () => {
  if ($('btn-creator-mode').disabled || PENDING.mode || oemWriting || spcBusy || spcSubmitted) return;
  const mode = currentOemMode(lastState);
  if (mode == null) { toast('认不出当前 OEM 档位，先等状态刷新', true); return; }
  const want = mode === 'turbo' ? 'office' : 'turbo';
  const btn = $('btn-creator-mode');
  btn.disabled = true;
  markPending('mode', want, 45, '造物者开关');
  renderCreator(lastState);
  try {
    const r = await api('/api/mode', { mode: want });
    toast((r.detail || '已下发')
      + ` · 约 24 秒后切到${want === 'turbo' ? '性能档' : '省电档'}`);
  } catch (e) {
    delete PENDING.mode;
    toast('切换失败：' + e.message, true);
    renderCreator(lastState);
  }
  poll();
};
document.querySelectorAll('[data-creator-tab]').forEach((button, index, buttons) => {
  button.onclick = () => showCreatorTab(button.dataset.creatorTab);
  button.onkeydown = (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1
      : (index + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    showCreatorTab(buttons[next].dataset.creatorTab);
    buttons[next].focus();
  };
});
document.querySelectorAll('[data-curve-table]').forEach((button) => {
  button.onclick = () => {
    if (oemLoading || oemWriting || curveLoading) return;
    oemType = button.dataset.curveTable;
    oemDragIndex = null; oemFocusIndex = null;
    if (fanSource === 'ec' && oemCurve) {
      fanSource = 'mqtt';
      $('fan-curve').innerHTML = '';
      setText('fan-curve-tag', '设备曲线 · 可编辑');
    }
    renderOemCurve();
  };
});
$('btn-curve-undo').onclick = restoreOemDraft;
$('btn-curve-default').onclick = restoreOemDefaults;
$('btn-spc-default').onclick = () => {
  fillSpc(lastState);
  if ($('btn-spc-default').disabled) return;
  for (const [key, value] of Object.entries({ pl1: 75, pl2: 75, pl4: 165 })) {
    $('spc-' + key).value = value;
    spcDirty.add(key);
  }
  fillSpc(lastState);
  toast('已填入 CC 默认功耗草稿，尚未发送给设备');
};
$('btn-spc-refresh').onclick = () => {
  if (spcBusy || spcSubmitted) return;
  fillSpc(lastState, true);
  poll();
};
['pl1', 'pl2', 'pl4'].forEach((k) => {
  const el = $('spc-' + k);
  if (!el) return;
  el.addEventListener('input', () => {
    spcDirty.add(k);
    const lab = $('spc-' + k + '-val');
    if (lab) lab.textContent = el.value + ' W';
    paintRange(el);
  });
});
$('spc-tcc').addEventListener('input', () => {
  spcDirty.add('tcc');
  const el = $('spc-tcc');
  const lab = $('spc-tcc-val');
  if (lab) lab.textContent = el.value + ' °C';
  paintRange(el);
});
$('spc-tcc-enabled').onchange = () => { spcDirty.add('tcc-enabled'); fillSpc(lastState); };
$('btn-spc-apply').onclick = async () => {
  if (spcBusy || spcSubmitted || spcNeedsRefresh || currentOemMode(lastState) !== 'turbo' || PENDING.mode) return;
  const body = {};
  const bounds = plBounds(lastState);
  if (!bounds) return;
  for (const k of ['pl1', 'pl2', 'pl4']) {
    const el = $('spc-' + k);
    if (el.disabled) continue;
    const v = Number(el.value);
    if (!Number.isInteger(v) || v < bounds[k][0] || v > bounds[k][1]) {
      toast('功耗墙超出 OEM 范围，请重填读数', true); return;
    }
    body[k] = v;
  }
  if (!Object.keys(body).length) return;
  if ((body.pl1 != null && body.pl2 != null && body.pl1 > body.pl2)
      || (body.pl2 != null && body.pl4 != null && body.pl2 > body.pl4)) {
    toast('请保持长时功耗 ≤ 短时功耗 ≤ 瞬时功耗上限', true); return;
  }
  const values = Object.entries(body).map(([key, value]) => key.toUpperCase() + ' ' + value + ' W');
  if (!confirm('下发临时功耗墙？\n\n' + values.join('\n')
      + '\n\n未回读的项目不下发；切档、息屏或 OEM 重算都可能改回去。')) return;
  const epoch = spcEpoch;
  spcBusy = true;
  Object.keys(body).forEach((key) => spcDirty.add(key));
  renderCreator(lastState);
  try {
    const r = await api('/api/power-limits', body);
    toast(r.detail || '已下发临时功耗墙，等待读数确认');
    if (epoch === spcEpoch) spcSubmitted = { values: body, until: Date.now() + 20000 };
  } catch (e) {
    toast('下发失败：' + e.message, true);
  } finally {
    spcBusy = false;
    renderCreator(lastState);
  }
  setTimeout(poll, 1200);
};
$('btn-spc-tcc-apply').onclick = async () => {
  if (spcBusy || spcSubmitted || spcNeedsRefresh || $('btn-spc-tcc-apply').disabled
      || currentOemMode(lastState) !== 'turbo' || PENDING.mode) return;
  const enabled = $('spc-tcc-enabled').checked;
  const value = Number($('spc-tcc').value);
  if (!Number.isInteger(value) || value < 0 || value > Number($('spc-tcc').max)) {
    toast('TCC 值超出范围', true); return;
  }
  if (!confirm(`应用 TCC 偏移？\n\n值 ${value} °C，${enabled ? '开启' : '关闭'}\n\n`
    + '这是当前档位的临时设置，切档会被服务重算。')) return;
  const epoch = spcEpoch;
  spcBusy = true;
  spcDirty.add('tcc'); spcDirty.add('tcc-enabled');
  renderCreator(lastState);
  try {
    const r = await api('/api/tcc', { value, enabled });
    toast(r.detail || '已下发 TCC，等待读数确认');
    if (epoch === spcEpoch) {
      spcSubmitted = { values: { tcc: value, 'tcc-enabled': enabled },
        until: Date.now() + TCC_WAIT_S * 1000 };
    }
  } catch (e) {
    toast('下发失败：' + e.message, true);
  } finally {
    spcBusy = false;
    renderCreator(lastState);
  }
  poll();
};

initNav();
renderIntents();
buildLightEditor();
document.querySelectorAll('input[type=range]').forEach((el) => {
  paintRange(el);
  el.addEventListener('input', () => paintRange(el));
});
poll();
loadLogs();
loadBench();
loadHistory();
setInterval(poll, 2000);
setInterval(loadLogs, 10000);
setInterval(loadHistory, 5000);
setInterval(scheduleProbe, PROBE_EVERY_MS);
// 切回这个页面时：立刻补一次数据，再顺带探一次通道（都带防抖）。
// 页面看不见时什么都不发——面板是常驻的，用户可能几天不看一眼，
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
  drawChart('chart-wall', SERIES.wall, histData);
  drawChart('chart-ov-temp', SERIES.temp, histData);
  drawChart('chart-ov-load', SERIES.load, histData);
  if (fanSource === 'ec' && curveData && curveData.tables) {
    drawFanCurveChart([
      { label: 'EC CPU', color: '--cyan', points: curveData.tables.CPU },
      { label: 'EC GPU', color: '--purple', points: curveData.tables.GPU },
    ]);
  } else if (fanSource === 'mqtt' && oemCurve && oemCurve.tables) {
    drawOemCurveEditor();
  }
});
