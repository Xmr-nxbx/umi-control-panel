'use strict';

const INTENTS = [
  { id: 'auto', name: '自适应', desc: '按负载/空闲/温度趋势自动换挡' },
  { id: 'office', name: '办公', desc: '锁定省电档，最低能耗' },
  { id: 'balance', name: '均衡', desc: '锁定均衡档，日常够用' },
  { id: 'turbo', name: '狂暴', desc: '锁定性能档，功耗拉满' },
];
const TIERS = { perf: '性能', mid: '流畅', bal: '均衡', eco: '省电' };

let META = { cap_labels: {}, mode_labels: {} };
let lastState = null;

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
  const cells = [
    meter('CPU 占用', x.cpu_pct, '%', x.cpu_pct),
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

function hwRow(k, v, ok) {
  return `<div class="hw-row"><span class="k">${esc(k)}</span>
    <span class="v${ok ? '' : ' na'}">${ok ? esc(v) : '不可控'}</span></div>`;
}

function renderHardware(s) {
  const caps = s.capabilities || {};
  const hw = s.hardware || {};
  const modeOk = (caps['mode.read'] || {}).state === 'verified';
  const rows = [
    hwRow('当前硬件档位', (META.mode_labels || {})[hw.mode] || hw.mode || '未知', modeOk),
    hwRow('PL1 / PL2', hw.pl1 != null ? (hw.pl1 + 'W / ' + (hw.pl2 || '?') + 'W') : '未知',
          (caps['power_limit.read'] || {}).state === 'verified'),
    hwRow('风扇转速', hw.fan_rpm ? hw.fan_rpm : '未知', (caps['fan.rpm'] || {}).state === 'verified'),
    hwRow('风扇强开', hw.fan_boost == null ? '未知' : (hw.fan_boost ? '开' : '关'),
          hw.fan_boost != null),
    hwRow('数据来源', hw.source || '无', !!hw.source),
  ];
  $('hw').innerHTML = rows.join('');

  const canWrite = (caps['mode.write'] || {}).state === 'verified';
  const modes = ['office', 'balance', 'turbo'];
  $('hw-buttons').innerHTML = modes.map((m) => `
    <button data-mode="${m}" ${canWrite ? '' : 'disabled'}>${esc((META.mode_labels || {})[m] || m)}</button>`).join('')
    + `<button class="ghost" id="btn-refresh-hw">重新探测通道</button>`;
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
  const ecCh = (s.channels || []).find((c) => c.name === 'ec') || {};
  const mqCh = (s.channels || []).find((c) => c.name === 'mqtt') || {};
  const why = (c) => (c.detail || {}).reason || '未探测';
  $('hw-hint').textContent = canWrite ? ''
    : ('硬件档位暂不可写。EC 直连：' + why(ecCh) + '；GCUBridge：' + why(mqCh));
  $('hw-hint').classList.toggle('err', !canWrite);
}

function renderCaps(s) {
  const caps = s.capabilities || {};
  const labels = META.cap_labels || {};
  const shown = ['mode.read', 'mode.write', 'power_limit.read', 'power_limit.write',
                 'fan.rpm', 'fan.curve', 'battery.limit', 'gpu.mux', 'lighting.rgb'];
  const stateText = { verified: '可用', unknown: '待验证', blocked: '需管理员', unsupported: '不支持' };
  $('caps').innerHTML = shown.map((cap) => {
    const c = caps[cap] || { state: 'unsupported', channel: '-' };
    return `<div class="cap"><span>${esc(labels[cap] || cap)}</span>
      <span class="st ${c.state}">${stateText[c.state] || c.state} · ${esc(c.channel || '—')}</span></div>`;
  }).join('');
}

function renderTier(s) {
  $('tier-now').textContent = s ? (TIERS[s.tier] || s.tier) : '--';
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
  } catch (e) {
    $('foot-status').textContent = '取数失败：' + e.message;
  }
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
$('btn-stop').onclick = async () => {
  if (!confirm('确定停止面板服务？停止后自适应调度与掉档守护都会失效。')) return;
  try { await api('/api/shutdown', {}); toast('服务已停止'); }
  catch (e) { toast('停止失败：' + e.message, true); }
};

renderIntents();
poll();
loadLogs();
setInterval(poll, 2000);
setInterval(loadLogs, 10000);
