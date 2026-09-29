'use strict';

const INTENTS = [
  { id: 'auto', name: '自适应', desc: '按负载/空闲/温度趋势自动换挡' },
  { id: 'office', name: '办公', desc: '锁定省电档，最低能耗' },
  { id: 'balance', name: '均衡', desc: '锁定均衡档，日常够用' },
  { id: 'turbo', name: '狂暴', desc: '锁定性能档，功耗拉满' },
];
const TIERS = { perf: '性能', mid: '流畅', bal: '均衡', eco: '省电' };
const BOOST_TEXT = { 0: '禁用', 1: '启用', 2: '激进', 3: '高效', 4: '高效激进', 5: '保证频率' };
// 风扇模式字节取值来自 OEM 自己的枚举（MyFanCTLByteFlag），这里只做中文注解
// 实体「造物者模式」按键循环的三态（实测见 README 第 6.2 节）。LED 与哪一态对应
// 还没确认，所以这里只写 OEM 枚举自己的语义：自动调速 / 用自定义曲线 / 强冷。
const FAN_KEY_FLAGS = ['Normal_Mode', 'User_Fan_Mode', 'Turbo_Mode'];
const FAN_FLAG_TEXT = { Normal_Mode: '自动', Turbo_Mode: '强冷', FanBoost_Mode: '风扇加速',
  User_Fan_Mode: '自定义曲线', User_Fan_HiMode: '自定义·高',
  User_Fan_Level1: '自定义 1 档', User_Fan_Level2: '自定义 2 档', User_Fan_Level3: '自定义 3 档',
  User_Fan_Level4: '自定义 4 档', User_Fan_Level5: '自定义 5 档' };

let META = { cap_labels: {}, mode_labels: {} };
let lastState = null;
let benchWasRunning = false;

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
  if (s.throttle) pills.push('<span class="pill bad">温度保护中</span>');
  if (s.dwell_left > 0) pills.push(`<span class="pill warn">驻留 ${Math.round(s.dwell_left)}s</span>`);
  if (s.pending) pills.push(`<span class="pill warn">换挡防抖：${Math.round(s.pending.in_s)}s 后 → `
    + `${esc((META.tier_labels || {})[s.pending.tier] || s.pending.tier)}</span>`);
  if (s.resume_left > 0) pills.push(`<span class="pill warn">亮屏缓冲 ${Math.round(s.resume_left)}s</span>`);
  if (s.guard_hits) pills.push(`<span class="pill ok">掉档拦截 ${s.guard_hits} 次</span>`);
  if (s.external_hits) pills.push(`<span class="pill warn">外部改动 ${s.external_hits} 次</span>`);
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

function hwRow(k, v, ok) {  // 文案一律由调用方给：ok 只决定灰不灰。以前这里硬写「不可控」，
  // 结果 EC 明明在线、只是档位语义没确认，也被显示成「不可控」。
  return `<div class="hw-row"><span class="k">${esc(k)}</span>
    <span class="v${ok ? '' : ' na'}">${esc(v)}</span></div>`;
}

function renderHardware(s) {
  const caps = s.capabilities || {};
  const hw = s.hardware || {};
  const verified = (cap) => (caps[cap] || {}).state === 'verified';
  const ecCh = (s.channels || []).find((c) => c.name === 'ec') || {};
  const ecAlive = ecCh.alive === true;
  const keyFlag = FAN_KEY_FLAGS.indexOf(hw.fan_mode_flag) >= 0 ? hw.fan_mode_flag : null;
  // 实测（2026-09-29，tools/ec_watch.py）：实体「造物者模式」按键只改风扇模式字节，
  // PL1/PL2/PL4 与 MyFanCCI_Mode_Index 一动不动 —— 所以这里按「按键三态」显示，
  // 功耗墙那一行仍然如实写「未确认」，不把风扇档说成性能档。
  const keyText = keyFlag ? (FAN_FLAG_TEXT[keyFlag] || keyFlag)
    : (ecAlive ? (FAN_FLAG_TEXT[hw.fan_mode_flag] || hw.fan_mode_flag || '未知') : '不可读');
  const plNote = hw.pl1_setting ? '' : '（出厂默认）';
  const rows = [
    hwRow('造物者模式按键', keyText + (keyFlag ? '' : '（非三态取值）'), ecAlive),
    hwRow('功耗墙档位', hw.mode ? ((META.mode_labels || {})[hw.mode] || hw.mode)
          : (ecAlive ? '未确认（实测按键不动功耗墙）' : '不可控'), !!hw.mode),
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
    hwRow('充电阈值', hw.charge_limit_up != null
          ? (hw.charge_limit_up + '% / 回落 ' + hw.charge_limit_down + '%') : '未知',
          verified('battery.limit')),
    hwRow('机型标识', hw.project_id != null ? ('ProjectID ' + hw.project_id
          + (hw.module_id != null ? ' · Module ' + hw.module_id : '')) : '未知',
          hw.project_id != null),
    hwRow('数据来源', hw.source || '无', !!hw.source),
  ];
  $('hw').innerHTML = rows.join('');

  const canFan = (caps['fan.mode'] || {}).state === 'verified';
  const available = Object.keys(s.fan_modes || {});
  // 只放实体按键真正会循环的那三态；User_Fan_Level1~5 是自定义曲线的子档，
  // 放上来只会让面板看起来比实际能控的东西多。
  const flags = FAN_KEY_FLAGS.filter((f) => available.indexOf(f) >= 0);
  const lockLeft = (ecCh.detail || {}).fan_lock_left || 0;
  const lockBy = (ecCh.detail || {}).fan_lock_by;
  const owned = !!(ecCh.detail || {}).fan_user_owned;
  const fanButtons = canFan ? flags.map((f) => `
    <button data-fan="${f}" class="${hw.fan_mode_flag === f ? 'primary' : ''}">${esc(FAN_FLAG_TEXT[f] || f)}</button>`).join('') : '';
  const canWrite = (caps['mode.write'] || {}).state === 'verified';
  const modes = ['office', 'balance', 'turbo'];
  $('hw-buttons').innerHTML = fanButtons
    + (canWrite ? modes.map((m) => `
    <button data-mode="${m}">${esc((META.mode_labels || {})[m] || m)}</button>`).join('') : '')
    + `<button class="ghost" id="btn-refresh-hw">重新探测通道</button>`;
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
  $('btn-refresh-hw').onclick = async () => {
    try { const r = await api('/api/ec/probe'); toast('EC 探测：' + ((r.detail || {}).reason || '完成')); }
    catch (e) { toast('EC 探测失败：' + e.message, true); }
    poll();
  };
  const mqCh = (s.channels || []).find((c) => c.name === 'mqtt') || {};
  const why = (c) => (c.detail || {}).reason || '未探测';
  const hints = [];
  if (lockLeft) hints.push(`${lockBy || '人工'}优先，${Math.round(lockLeft)} 秒内面板不自动改风扇`);
  else if (owned) hints.push('当前是自定义曲线，面板不会自动改风扇（点上面的按钮可接管）');
  if (!canWrite) hints.push('功耗墙档位不可写：' + ((ecCh.detail || {}).write_reason || why(ecCh)));
  if (!canFan) hints.push('风扇模式不可写：' + ((ecCh.detail || {}).fan_mode_reason || why(ecCh)));
  $('hw-hint').textContent = hints.join('；');
  $('hw-hint').classList.toggle('err', !canWrite && !canFan);
}

function renderCaps(s) {
  const caps = s.capabilities || {};
  const labels = META.cap_labels || {};
  const shown = ['mode.read', 'mode.write', 'power_limit.read', 'power_limit.write',
                 'fan.rpm', 'fan.mode', 'fan.curve', 'ec.temp', 'battery.limit',
                 'gpu.mux', 'lighting.rgb'];
  const stateText = { verified: '可用', unknown: '待验证', blocked: '受限',
                      missing: '缺本机配置', unsupported: '不支持' };
  $('caps').innerHTML = shown.map((cap) => {
    const c = caps[cap] || { state: 'unsupported', channel: '-' };
    return `<div class="cap"><span>${esc(labels[cap] || cap)}</span>
      <span class="st ${c.state}">${stateText[c.state] || c.state} · ${esc(c.channel || '—')}</span></div>`;
  }).join('');
}

function renderTier(s) {
  $('tier-now').textContent = s ? (TIERS[s.tier] || s.tier) : '--';
  const x = (s && s.sensor) || {};
  $('tier-clock').textContent = x.cpu_mhz ? (x.cpu_mhz + ' MHz') : '-- MHz';
  $('tier-reason').textContent = s ? (s.reason || '') : '等待数据…';
  $('live-dot').className = 'dot' + (s && Date.now() / 1000 - s.ts < 8 ? ' on' : '');
  $('foot-status').textContent = s
    ? `采样 ${new Date(s.ts * 1000).toLocaleTimeString()} · 已运行 ${Math.floor((s.uptime_s || 0) / 60)} 分钟 · 活动方案 ${(s.applied || {}).scheme ? (s.applied.scheme.slice(0, 8)) : '--'}`
    : '未取到数据';
  $('intent-hint').textContent = s && s.intent === 'auto'
    ? '自适应：升档快、降档慢；进性能档后 300 秒驻留期内不会因为一时低负载掉档。'
    : '锁定档位：调度器不再自动漂移，温度保护仍然生效。';
}

async function poll() {
  try {
    const s = await api('/api/state');
    lastState = s;
    META = Object.assign(META, s.meta || {});
    renderTier(s); renderPills(s); renderIntents(); renderMeters(s);
    renderHardware(s); renderCaps(s);
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
  $('bench-run').hidden = !(running || done);
  $('btn-bench-current').disabled = running;
  $('btn-bench-compare').disabled = running;
  if (running || done) {
    $('bench-fill').style.width = (done ? 100 : (job.pct || 0)) + '%';
    $('bench-step').textContent = done ? '完成，成绩见下表' : `${job.label || ''} ${job.step || ''}`;
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
  const cell = (v, unit) => (v == null ? '—' : esc(v) + (unit || ''));
  $('bench-table').innerHTML = `<table class="bench"><thead><tr>
      <th>档位</th><th>总分</th><th>单线程<br><small>Mops/s</small></th>
      <th>多线程<br><small>Mops/s</small></th><th>短任务<br><small>ms</small></th>
      <th>内存<br><small>MB/s</small></th>
      <th>实测频率</th><th>最高温</th><th>睿频</th><th>电源方案</th></tr></thead><tbody>
    ${rows.map((r) => {
      const rec = r.record || {}; const sc = r.score || {}; const pf = rec.profile || {};
      return `<tr class="${rec.tier === now ? 'now' : ''}">
        <td>${esc(r.label || r.tier)}${rec.tier === now ? ' <em>当前</em>' : ''}</td>
        <td class="score">${cell(sc.overall)}</td>
        <td>${cell(rec.single_mops)}</td><td>${cell(rec.multi_mops)}</td>
        <td>${cell(rec.burst_ms)}</td>
        <td>${cell(rec.mem_mb_s)}</td><td>${cell(rec.clock_mhz, ' MHz')}</td>
        <td>${cell(rec.temp_after_c, '°C')}</td>
        <td>${esc(BOOST_TEXT[pf.boost] || '—')}</td><td>${esc(pf.scheme || '—')}</td></tr>`;
    }).join('')}</tbody></table>`;
  $('bench-hint').textContent = `基准（=100 分）：单线程 ${base.single_mops} Mops/s、多线程 ${base.multi_mops} Mops/s、短任务 ${base.burst_ms} ms。`
    + '总分越高越快；「短任务」是降频后来一下活的耗时，直接对应亮屏回来点东西卡不卡。';
}

async function loadBench() {
  try { renderBench(await api('/api/bench')); } catch (e) { /* 服务未就绪时静默 */ }
}

async function startBench(mode) {
  const tip = mode === 'compare'
    ? '对比跑分会依次锁到省电/均衡/流畅/性能档，每档满载几秒，全程约 1.5 分钟，风扇会明显转起来；结束后自动回到当前档位。现在开始？'
    : '会在当前档位上满载约 6 秒。现在开始？';
  if (!confirm(tip)) return;
  try {
    await api('/api/bench', { mode });
    toast(mode === 'compare' ? '开始四档对比跑分' : '开始跑分');
  } catch (e) { toast('跑分启动失败：' + e.message, true); }
  loadBench();
}

async function loadLogs() {
  try {
    const d = await api('/api/logs?n=120');
    $('logs').textContent = (d.lines || []).join('\n') || '（暂无日志）';
    $('logs').scrollTop = $('logs').scrollHeight;
  } catch (e) {
    $('logs').textContent = '日志读取失败：' + e.message;
  }
}

$('btn-refresh-logs').onclick = loadLogs;
$('btn-bench-current').onclick = () => startBench('current');
$('btn-bench-compare').onclick = () => startBench('compare');
$('btn-stop').onclick = async () => {
  if (!confirm('确定停止面板服务？停止后自适应调度与掉档守护都会失效。')) return;
  try { await api('/api/shutdown', {}); toast('服务已停止'); }
  catch (e) { toast('停止失败：' + e.message, true); }
};

renderIntents();
poll();
loadLogs();
loadBench();
setInterval(poll, 2000);
setInterval(loadLogs, 10000);
