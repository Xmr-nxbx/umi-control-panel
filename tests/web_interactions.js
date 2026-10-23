async function runWebInteractions() {
  const originalApi = api;
  const originalConfirm = window.confirm;
  const originalWaitPresetState = waitPresetState;
  const originalSavePresets = savePresets;
  const originalSetTimeout = window.setTimeout;
  const originalRaf = window.requestAnimationFrame;
  const saved = {
    lastState, META, histData, presets, plDirty, plFilled, plBusy, plSubmitted, plNeedsRefresh, plEpoch, presetBusy,
    fanSpeedDirty, fanSpeedBusy, fanSpeedSubmitted, lightSelectedColor,
    lightEffect, lightEditorInit, lightBusy, lightSubmitted,
    creatorModeSeen, creatorActiveTab, fanSource, oemCurve, oemOrig, oemDraft, oemOrigName,
    oemEpoch, spcEpoch, oemReadAttempted, oemReadError, oemType, oemWriting, oemLoading,
    oemDragIndex, oemFocusIndex, oemChartGeometry, curveData, curveLoading,
    spcDirty: [...spcDirty], spcBusy, spcSubmitted, spcNeedsRefresh,
    pending: structuredClone(PENDING), quickSending: [...quickSending], benchWasRunning,
    stateRequestId: typeof stateRequestId === 'undefined' ? undefined : stateRequestId,
    stateSettledId: typeof stateSettledId === 'undefined' ? undefined : stateSettledId,
    stateSnapshotTs: typeof stateSnapshotTs === 'undefined' ? undefined : stateSnapshotTs,
  };
  const originalView = document.querySelector('.view.active')?.id.replace('view-', '') || 'overview';
  const originalFocus = document.activeElement;
  const originalPresetFormHidden = $('preset-form').hidden;
  const viewInert = [...document.querySelectorAll('.view')].map((el) => [el, el.inert]);
  const seriesCurrent = Object.values(SERIES).flat().map((series) => ({
    series, had: Object.hasOwn(series, '_cur'), value: series._cur,
  }));
  let storedView;
  try { storedView = localStorage.getItem('umi.view'); } catch { /* 隐私模式 */ }
  const controls = [...document.querySelectorAll('input, select')].map((el) => ({
    el, value: el.value, checked: el.checked, disabled: el.disabled, min: el.min, max: el.max,
  }));
  const feedback = ['preset-progress', 'quick-feedback', 'fanspeed-feedback', 'fan-default-feedback'].map((id) => ({
    el: $(id), text: $(id).textContent, hidden: $(id).hidden,
  }));
  const realState = structuredClone(lastState);
  const realCurve = structuredClone(oemCurve);
  const results = [];
  const posts = [];
  const presetSaves = [];
  const unexpected = [];
  const timers = new Set();
  const frames = new Set();
  const deferreds = [];
  const running = new Set();
  const stateReadQueue = [];
  let state;
  let curve;
  let defaults;
  let heldWrite = null;
  let heldRead = null;
  let readError = null;
  let postError = null;
  let skipCurveWrite = false;
  const skipStateWrites = new Set();
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const input = (id, value) => {
    const el = $(id); el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const change = (id, checked) => {
    $(id).checked = checked;
    $(id).dispatchEvent(new Event('change', { bubbles: true }));
  };
  const defer = () => {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    deferreds.push(resolve);
    return { promise, resolve };
  };
  // 入队时冻结快照，GET 按调用顺序取出、按 resolve 顺序返回；失败也不访问真实 API。
  const queueStateRead = (snapshot = state, error = null) => {
    const read = { ...defer(), snapshot: structuredClone(snapshot), error, started: false };
    stateReadQueue.push(read);
    return read;
  };
  const track = (promise) => { running.add(promise); return promise; };
  const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  const check = async (name, action) => {
    await action();
    assert(!unexpected.length, '出现未列入 mock 的 API：' + unexpected.join(', '));
    results.push(name);
  };
  const reset = () => {
    state = structuredClone(realState);
    state.ts = Date.now() / 1000;
    state.meta = { ...state.meta, fan_curve_expected_name: true };
    state.capabilities = { ...state.capabilities };
    for (const cap of ['mode.write', 'power_limit.write', 'cpu.tcc.write', 'fan.curve.write',
      'lighting.rgb.write', 'lighting.effect.write', 'winkey.write', 'fan.switch_speed.write']) {
      state.capabilities[cap] = { state: 'verified', channel: 'mqtt' };
    }
    state.channels = state.channels || [];
    for (const name of ['mqtt', 'ec']) {
      let channel = state.channels.find((c) => c.name === name);
      if (!channel) { channel = { name, detail: {} }; state.channels.push(channel); }
      channel.alive = true;
    }
    const boundsChannel = state.channels.find((c) => c.detail?.pl_bounds)
      || state.channels.find((c) => c.name === 'mqtt');
    boundsChannel.detail = { ...boundsChannel.detail, pl_bounds: {
      pl1_min: 10, pl1_max: 120, pl2_min: 10, pl2_max: 120, pl4_min: 10, pl4_max: 165,
    } };
    state.hardware.oem.profile_name = 'Mode3'; state.hardware.fan_mode_flag = 'Turbo_Mode';
    state.hardware.oem.fan_table = realCurve.name;
    state.hardware.oem.tcc_offset_c = 3; state.hardware.oem.tcc_offset_max = 5;
    state.hardware.oem.tcc_offset_enabled = false;
    state.hardware.oem.kb_power_on = true; state.hardware.oem.win_key_locked = false;
    state.hardware.oem.kb_effect = 1;
    state.hardware.oem.fan_switch_speed_ms = 500;
    state.hardware.oem.fan_switch_speed_enabled = false;
    state.hardware.pl1 = state.hardware.pl1_setting = 45;
    state.hardware.pl2 = state.hardware.pl2_setting = 65;
    state.hardware.pl4 = null; state.hardware.pl4_setting = null;
    state.bench = { running: false };
    curve = structuredClone(realCurve); curve.stale = false; curve.ts = state.ts;
    for (const type of ['CPU', 'GPU']) curve.tables[type].forEach((p) => {
      p.duty_pct = p.sentinel ? 100 : p.id === 0 ? 0 : 30 + p.id * 5;
    });
    defaults = structuredClone(curve);
    defaults.source = 'DefaultFanTable_Turbo.json';
    for (const type of ['CPU', 'GPU']) defaults.tables[type].forEach((p) => {
      p.duty_pct = p.sentinel ? 100 : p.id === 0 ? 0 : Math.min(100, 20 + p.id * 8);
    });
    for (const key of Object.keys(PENDING)) delete PENDING[key];
    lastState = state; creatorModeSeen = 'turbo'; fanSource = 'mqtt';
    // 兼容主程序尚未引入门控变量的版本；每例由首次 mock GET 建立时间戳基线。
    if (typeof stateRequestId !== 'undefined') stateRequestId = 0;
    if (typeof stateSettledId !== 'undefined') stateSettledId = 0;
    if (typeof stateSnapshotTs !== 'undefined') stateSnapshotTs = 0;
    oemEpoch++; spcEpoch++; oemReadAttempted = true; oemReadError = '';
    oemCurve = normalizeOemCurve(curve); oemOrigName = curve.name;
    oemOrig = {}; oemDraft = {};
    for (const type of ['CPU', 'GPU']) {
      oemOrig[type] = oemCurve.tables[type].map((p) => p.duty_pct);
      oemDraft[type] = oemOrig[type].slice();
    }
    oemType = 'CPU'; oemWriting = false; oemLoading = false; curveLoading = false;
    oemDragIndex = null; oemFocusIndex = null; oemChartGeometry = null;
    curveData = null; spcDirty.clear(); spcBusy = false; spcSubmitted = null; spcNeedsRefresh = false;
    quickSending.clear(); presetBusy = false; presets = []; plDirty = false; plFilled = false;
    plBusy = false; plSubmitted = null; plNeedsRefresh = false; plEpoch++;
    fanSpeedDirty = false; fanSpeedBusy = false; fanSpeedSubmitted = null;
    lightBusy = false; lightSubmitted = null; lightSelectedColor = 0; lightEditorInit = false;
    posts.length = 0; presetSaves.length = 0; stateReadQueue.length = 0;
    heldWrite = null; heldRead = null; readError = null; postError = null;
    skipCurveWrite = false; skipStateWrites.clear(); benchWasRunning = false;
    waitPresetState = originalWaitPresetState;
    $('preset-progress').hidden = true; $('preset-progress').textContent = '';
    $('fanspeed-feedback').textContent = ''; $('preset-form').hidden = true;
    renderPresetLock();
    showView('creator'); showCreatorTab('fan'); renderOemCurve(); renderLighting(state);
    renderQuick(state); renderOem(state); renderFanSpeed(state); fillSpc(state, true);
  };
  // 所有读写都在内存中；未知路径也不允许落到 originalApi。
  api = async (path, body) => {
    if (body) {
      posts.push({ path, body: structuredClone(body) });
      if (!['/api/fan-curve', '/api/power-limits', '/api/tcc', '/api/action',
        '/api/mode', '/api/fan-switch-speed', '/api/keyboard-effect'].includes(path)) {
        unexpected.push(path);
        throw new Error('交互测试禁止未声明的 POST：' + path);
      }
      if (heldWrite) await heldWrite;
      if (postError?.path === path) throw new Error(postError.message);
      if (skipStateWrites.has(path)) return { ok: true, detail: '已下发，等待回读' };
      if (path === '/api/fan-curve' && !skipCurveWrite) {
        curve.tables[body.type].forEach((p) => { p.duty_pct = body.duties[p.id]; });
      } else if (path === '/api/power-limits') {
        Object.entries(body).forEach(([key, value]) => { state.hardware[key + '_setting'] = value; });
      } else if (path === '/api/tcc') {
        state.hardware.oem.tcc_offset_c = body.value;
        state.hardware.oem.tcc_offset_enabled = body.enabled;
      } else if (path === '/api/action') {
        const actions = {
          KB_POWER_ON: ['kb_power_on', true], KB_POWER_OFF: ['kb_power_on', false],
          WINKEY_LOCK: ['win_key_locked', true], WINKEY_UNLOCK: ['win_key_locked', false],
        };
        const action = actions[body.action];
        if (!action) {
          unexpected.push(body.action);
          throw new Error('交互测试禁止未声明的 action：' + body.action);
        }
        state.hardware.oem[action[0]] = action[1];
      } else if (path === '/api/mode') {
        assert(['turbo', 'office'].includes(body.mode), 'mock 收到未知模式');
        state.hardware.oem.profile_name = body.mode === 'turbo' ? 'Mode3' : 'Mode2';
        state.hardware.fan_mode_flag = body.mode === 'turbo' ? 'Turbo_Mode' : 'User_Fan_HiMode';
      } else if (path === '/api/fan-switch-speed') {
        state.hardware.oem.fan_switch_speed_enabled = body.enabled;
        if (body.enabled) state.hardware.oem.fan_switch_speed_ms = body.ms;
      }
      // 灯效故意不自动回声，保留旧用例对等待锁的验证。
      return { ok: true, detail: skipCurveWrite ? '已下发，等待回读' : '回读一致' };
    }
    if (path === '/api/state') {
      const read = stateReadQueue.shift();
      if (!read) return structuredClone(state);
      read.started = true;
      await read.promise;
      if (read.error) throw new Error(read.error);
      return structuredClone(read.snapshot);
    }
    if (path.startsWith('/api/fan-curve/defaults?name=')) {
      assert(decodeURIComponent(path.split('name=')[1]) === curve.name, '默认表请求没有绑定当前表名');
      const loaded = structuredClone(defaults);
      if (heldRead) await heldRead;
      if (readError) throw new Error(readError);
      return loaded;
    }
    if (path === '/api/fan-curve?source=mqtt') {
      const loaded = structuredClone(curve);
      if (heldRead) await heldRead;
      if (readError) throw new Error(readError);
      return loaded;
    }
    if (path === '/api/fan-curve') return { ...structuredClone(curve), reads: 96, writes: 0, elapsed_s: 3, note: '' };
    if (path === '/api/history') return histData;
    if (path === '/api/bench') return { tiers: [] };
    if (path.startsWith('/api/logs')) return { lines: [] };
    unexpected.push(path);
    throw new Error('交互测试禁止访问真实 API：' + path);
  };
  window.confirm = () => true;
  // 保存方案同样只落内存，不污染用户的 localStorage。
  savePresets = () => { presetSaves.push(structuredClone(presets)); };
  META = structuredClone(saved.META);
  histData = structuredClone(saved.histData);
  // 撤销本轮操作留下的延迟 poll，避免退出 mock 后再请求真实服务。
  window.setTimeout = (callback, delay, ...args) => {
    const id = originalSetTimeout(() => { timers.delete(id); callback(...args); }, delay);
    timers.add(id);
    return id;
  };
  window.requestAnimationFrame = (callback) => {
    const id = originalRaf((time) => { frames.delete(id); callback(time); });
    frames.add(id);
    return id;
  };
  try {
    await check('并发 poll 新 Office 先到、旧 Turbo 后到不能覆盖状态与界面', async () => {
      reset();
      const oldRead = queueStateRead();
      state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode';
      // 两份快照 ts 相等，必须靠请求顺序门控，不能只比较时间戳。
      const newRead = queueStateRead();
      const oldPoll = track(poll()), newPoll = track(poll());
      assert(oldRead.started && newRead.started && !stateReadQueue.length, '两个 poll 没有并发进入受控 GET');
      newRead.resolve();
      assert(await newPoll === true, '新 Office 回读没有返回成功');
      const accepted = lastState;
      assert(currentOemMode(accepted) === 'office' && creatorModeSeen === 'office', '新 Office 未生效');
      oldRead.resolve();
      assert(await oldPoll === false, '旧 Turbo 回包仍被报告为成功');
      assert(lastState === accepted && lastState.ts === newRead.snapshot.ts
        && creatorModeSeen === 'office' && $('ov-status-card').dataset.mode === 'office', '迟到 Turbo 覆盖了新状态或界面');
      assert(posts.length === 0, '只读竞态测试发送了命令');
    });
    await check('并发 poll 新成功后旧失败不能将新状态置为离线', async () => {
      reset();
      const oldRead = queueStateRead(state, '模拟旧状态 GET 失败');
      state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode';
      const newRead = queueStateRead();
      const oldPoll = track(poll()), newPoll = track(poll());
      assert(oldRead.started && newRead.started, '新旧 GET 没有同时等待');
      newRead.resolve();
      assert(await newPoll === true, '新状态 GET 成功未返回 true');
      const accepted = lastState, status = $('foot-status').textContent;
      oldRead.resolve();
      assert(await oldPoll === false, '失败的旧 poll 没有返回 false');
      assert(lastState === accepted && lastState.ts === newRead.snapshot.ts && deviceOnline(lastState)
        && currentOemMode(lastState) === 'office', '旧失败将新状态 ts 清零或恢复了旧模式');
      assert($('foot-status').textContent === status && !$('quick-kb').disabled, '旧失败污染了连接提示或写入门禁');
      assert(posts.length === 0 && !stateReadQueue.length, '竞态测试遗留请求或发送了命令');
    });
    await check('同一轮旧 GET 失败与新 GET 成功完成，不把新状态置为离线', async () => {
      reset();
      const oldRead = queueStateRead(state, '模拟旧 GET 同时失败');
      state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode';
      const newRead = queueStateRead();
      const oldPoll = track(poll()), newPoll = track(poll());
      oldRead.resolve(); newRead.resolve();
      const results = await Promise.all([oldPoll, newPoll]);
      assert(results[0] === false && results[1] === true && deviceOnline(lastState)
        && currentOemMode(lastState) === 'office', '错误在传递给 poll 期间把新状态置为离线');
      assert(posts.length === 0 && !stateReadQueue.length, '并发读取意外写入或遗留请求');
    });
    await check('串行 poll 拒绝仍新鲜但 ts 倒退的快照', async () => {
      reset();
      state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode';
      assert(await poll() === true, '时间戳基线未成功读取');
      const accepted = lastState;
      const stale = structuredClone(state); stale.ts -= 1;
      stale.hardware.oem.profile_name = 'Mode3'; stale.hardware.fan_mode_flag = 'Turbo_Mode';
      assert(deviceOnline(stale), '倒退快照必须仍新鲜，不能由离线门禁代替时间戳门控');
      const read = queueStateRead(stale); read.resolve();
      assert(await poll() === false && read.started, '串行较旧快照被当作成功');
      assert(lastState === accepted && lastState.ts === accepted.ts && deviceOnline(lastState)
        && creatorModeSeen === 'office', '时间戳倒退覆盖状态或导致离线');
      assert(posts.length === 0, '串行只读测试发送了命令');
    });
    reset();
    await check('三页签与键盘导航', () => {
      $('creator-tab-fan').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      assert(creatorActiveTab === 'performance', '右方向键没有切到性能');
      assert(document.querySelectorAll('.creator-pane:not([hidden])').length === 1, '多个页签同时显示');
    });
    await check('多项滑块和 TCC 开关草稿不被轮询覆盖', async () => {
      input('spc-pl1', 48); input('spc-pl2', 70);
      assert($('spc-tcc').disabled, 'TCC 关闭时滑块仍可操作');
      assert(Number(getComputedStyle($('spc-tcc')).opacity) < 1, 'TCC 禁用态缺少视觉区分');
      $('spc-tcc-enabled').checked = true; $('spc-tcc-enabled').dispatchEvent(new Event('change'));
      assert(!$('spc-tcc').disabled, '开启 TCC 草稿后滑块未解锁');
      assert(getComputedStyle($('spc-tcc')).opacity === '1', 'TCC 开启后仍显示禁用态'); input('spc-tcc', 4);
      await poll();
      assert($('spc-pl1').value === '48' && $('spc-pl2').value === '70', '功耗草稿丢失');
      assert($('spc-tcc').value === '4' && $('spc-tcc-enabled').checked, 'TCC 草稿丢失');
    });
    await check('未回读 PL4 不下发，保留未提交的 TCC 草稿', async () => {
      assert($('spc-pl4').disabled && $('spc-pl4-val').textContent === '未回读', '未知 PL4 可编辑');
      await $('btn-spc-apply').onclick(); await poll();
      assert(!('pl4' in posts[0].body), '未知 PL4 被下发');
      assert(spcDirty.has('tcc') && spcDirty.has('tcc-enabled'), 'PL 提交丢了 TCC 草稿');
      assert(!spcSubmitted, '功耗回读没有销账');
    });
    await check('TCC 提交后确认回读', async () => {
      await $('btn-spc-tcc-apply').onclick(); await poll();
      assert(!spcSubmitted && !spcDirty.has('tcc') && !spcDirty.has('tcc-enabled'), 'TCC 没有确认');
    });
    await check('TCC 改为 4 后取消勾选，旧 false+3 不能确认关闭提交', async () => {
      reset(); showCreatorTab('performance');
      change('spc-tcc-enabled', true); input('spc-tcc', 4); change('spc-tcc-enabled', false);
      skipStateWrites.add('/api/tcc');
      await $('btn-spc-tcc-apply').onclick(); await poll();
      assert(posts.length === 1 && posts[0].path === '/api/tcc'
        && posts[0].body.value === 4 && posts[0].body.enabled === false, '关闭 TCC 没有同时发送草稿数值与 false');
      assert(state.hardware.oem.tcc_offset_c === 3 && state.hardware.oem.tcc_offset_enabled === false,
        '受控 mock 没有保留旧 false+3');
      assert(spcSubmitted && spcDirty.has('tcc') && spcDirty.has('tcc-enabled')
        && $('btn-spc-tcc-apply').disabled && $('spc-tcc').value === '4' && !$('spc-tcc-enabled').checked,
        '只匹配 false 就提前销账或重填了数值草稿');
      await $('btn-spc-tcc-apply').onclick();
      assert(posts.length === 1, '等待完整关闭回读时仍能重复提交');
      state.hardware.oem.tcc_offset_c = 4; state.hardware.oem.tcc_offset_enabled = true; await poll();
      assert(spcSubmitted && $('btn-spc-tcc-apply').disabled, '只有数值 4 匹配就确认了关闭');
      state.hardware.oem.tcc_offset_enabled = false; await poll();
      assert(!spcSubmitted && !spcBusy && !spcDirty.has('tcc') && !spcDirty.has('tcc-enabled')
        && !$('btn-spc-tcc-apply').disabled && $('spc-tcc').value === '4' && !$('spc-tcc-enabled').checked,
        'false+4 完整回读后没有销账、清草稿标记并解锁');
      assert(posts.length === 1, '确认关闭时额外下发了命令');
    });
    await check('CPU/GPU 密集与重复温度节点等距且两端留白', () => {
      reset();
      for (const type of ['CPU', 'GPU']) {
        oemType = type;
        oemCurve.tables[type][2].up_t = oemCurve.tables[type][1].up_t;
        renderOemCurve();
        const { mapped } = oemChartGeometry;
        const gaps = mapped.slice(1).map((p, i) => p.x - mapped[i].x);
        assert(gaps.every((gap) => gap >= 44 && Math.abs(gap - gaps[0]) < 1), '温度点间距不足或重复点重叠');
        assert(mapped[0].x >= 44 && $('fan-curve-chart').clientWidth - mapped.at(-1).x >= 32, '两端节点贴边');
        assert(mapped[1].temp === mapped[2].temp && mapped[1].x !== mapped[2].x, '等距显示改写了真实温度');
      }
    });
    await check('恢复本机默认只填选侧草稿，撤销仍回到当前读数', async () => {
      reset();
      for (const type of ['CPU', 'GPU']) {
        oemType = type; renderOemCurve();
        const other = type === 'CPU' ? 'GPU' : 'CPU';
        const original = structuredClone(oemOrig), otherDraft = oemDraft[other].slice();
        await $('btn-curve-default').onclick();
        assert(oemDraft[type].every((v, i) => v === defaults.tables[type][i].duty_pct), '未填入默认占空');
        assert(JSON.stringify(oemDraft[other]) === JSON.stringify(otherDraft), '默认覆盖了另一侧草稿');
        assert(JSON.stringify(oemOrig) === JSON.stringify(original) && !posts.length, '恢复默认写硬件或覆盖撤销基线');
        restoreOemDraft();
        assert(JSON.stringify(oemDraft[type]) === JSON.stringify(original[type]), '恢复默认后无法撤销');
      }
    });
    await check('默认草稿仍经已有保存和表名校验路径提交', async () => {
      reset(); await $('btn-curve-default').onclick(); await applyOemCurve();
      assert(posts.length === 1 && posts[0].path === '/api/fan-curve'
        && posts[0].body.expected_name === curve.name && posts[0].body.type === 'CPU', '默认绕过既有保存路径');
      assert(posts[0].body.duties.every((v, i) => v === defaults.tables.CPU[i].duty_pct), '默认值保存不完整');
      assert($('fan-default-feedback').hidden, '保存确认后仍显示默认草稿尚未保存');
    });
    await check('重新编辑或成功读取后清除旧默认提示，读取失败保留提示', async () => {
      reset(); await restoreOemDefaults(); setOemDuty(1, 25);
      assert($('fan-default-feedback').hidden, '编辑后仍提示已填入默认值');
      await restoreOemDefaults(); readError = '读取失败'; await loadOemCurve(false);
      assert(!$('fan-default-feedback').hidden && oemDraft.CPU[1] === 28, '失败读取丢失默认草稿或提示');
      readError = null; await loadOemCurve(false);
      assert($('fan-default-feedback').hidden, '读取当前值后仍显示默认提示');
      assert(!posts.length, '提示回归写入设备');
    });
    await check('旧后端默认接口缺失提示重启且保留草稿', async () => {
      for (const error of ['not found', 'HTTP 404']) {
        reset(); setOemDuty(1, 40); const draft = JSON.stringify(oemDraft);
        readError = error; await restoreOemDefaults();
        assert(JSON.stringify(oemDraft) === draft && !oemLoading && !posts.length, '旧后端丢失草稿或未解锁');
        assert($('fan-default-feedback').textContent.includes('正常重启面板'), '旧后端没有明确升级提示');
      }
    });
    await check('触控取消释放节点捕获，后续移动不能继续修改草稿', () => {
      reset(); const cv = $('fan-curve-chart'), rect = cv.getBoundingClientRect();
      const p = oemChartGeometry.mapped[1], before = JSON.stringify(oemDraft);
      const savedCapture = [cv.setPointerCapture, cv.hasPointerCapture, cv.releasePointerCapture];
      let captured = null;
      cv.setPointerCapture = (id) => { captured = id; };
      cv.hasPointerCapture = (id) => captured === id;
      cv.releasePointerCapture = () => { captured = null; };
      try {
        cv.onpointerdown({ pointerId: 42, clientX: rect.left + p.x, clientY: rect.top + p.y });
        assert(oemDragIndex === p.idx && captured === 42, '未捕获拖动节点');
        cv.onpointercancel({ pointerId: 42 });
        cv.onpointermove({ pointerId: 42, clientX: rect.left + p.x, clientY: rect.top + p.y - 20 });
        assert(oemDragIndex === null && captured === null && JSON.stringify(oemDraft) === before && !posts.length,
          '取消后仍保留捕获或继续修改');
      } finally { [cv.setPointerCapture, cv.hasPointerCapture, cv.releasePointerCapture] = savedCapture; }
    });
    await check('默认缓存缺失、温度或表名不匹配时保留草稿', async () => {
      for (const invalid of ['missing', 'temperature', 'down', 'name', 'shape']) {
        reset(); setOemDuty(1, 40); const draft = JSON.stringify(oemDraft);
        if (invalid === 'missing') defaults = { ok: false, reason: '未找到本机默认缓存' };
        if (invalid === 'temperature') defaults.tables.CPU[1].up_t--;
        if (invalid === 'down') defaults.tables.CPU[1].down_t--;
        if (invalid === 'name') defaults.name = 'M2T1';
        if (invalid === 'shape') defaults.tables.CPU.pop();
        await $('btn-curve-default').onclick();
        assert(JSON.stringify(oemDraft) === draft && !posts.length && !oemLoading, '无效默认覆盖草稿或写设备：' + invalid);
        assert(document.querySelector('.toast').classList.contains('bad'), '无效默认没有明确报错：' + invalid);
      }
    });
    await check('默认读取失败与取消覆盖均保留草稿', async () => {
      reset(); setOemDuty(1, 40); const draft = JSON.stringify(oemDraft);
      window.confirm = () => false;
      try { await $('btn-curve-default').onclick(); } finally { window.confirm = () => true; }
      assert(JSON.stringify(oemDraft) === draft && !posts.length, '取消仍覆盖草稿');
      readError = '默认缓存读取失败'; await $('btn-curve-default').onclick();
      assert(JSON.stringify(oemDraft) === draft && !oemLoading && !posts.length, '失败丢草稿或未解锁');
    });
    await check('默认读取期间锁住编辑与切表，断线后不接纳', async () => {
      reset(); const draft = JSON.stringify(oemDraft); const gate = defer(); heldRead = gate.promise;
      const loading = track($('btn-curve-default').onclick());
      assert(oemLoading && $('btn-curve-apply').disabled && $('btn-curve-default').disabled, '读取默认未锁编辑');
      document.querySelector('[data-curve-table="GPU"]').onclick();
      assert(oemType === 'CPU', '读取默认期间切表');
      state.ts = 0; gate.resolve(); heldRead = null; await loading;
      assert(JSON.stringify(oemDraft) === draft && !posts.length, '断线后接纳旧默认');
    });
    await check('默认延迟回包不能污染新的硬件表', async () => {
      reset(); const gate = defer(); heldRead = gate.promise;
      const loading = track($('btn-curve-default').onclick());
      state.hardware.oem.fan_table = 'M3T2'; renderCreator(state);
      gate.resolve(); heldRead = null; await loading;
      assert(!oemDraft && !oemCurve && !posts.length, '旧表默认污染新表');
    });
    reset();
    await check('CPU/GPU 草稿独立与相邻节点约束', () => {
      setOemDuty(1, 999); assert(oemDraft.CPU[1] === 40, '上邻点约束失效');
      document.querySelector('[data-curve-table="GPU"]').click();
      setOemDuty(1, -99); assert(oemDraft.GPU[1] === 0, '下邻点约束失效');
      assert(oemDraft.CPU[1] === 40, 'GPU 编辑覆盖 CPU');
    });
    await check('写入持久锁、目标表名与另一张草稿保留', async () => {
      const gate = defer(); heldWrite = gate.promise;
      const saving = track(applyOemCurve());
      assert(oemWriting && $('btn-curve-apply').disabled && $('btn-curve-undo').disabled, '写入锁没有生效');
      const before = posts.length; await applyOemCurve();
      assert(posts.length === before, '重复提交没有拦截');
      document.querySelector('[data-curve-table="CPU"]').onclick();
      assert(oemType === 'GPU', '写入期间切换了表');
      gate.resolve(); heldWrite = null; await saving;
      assert(posts[0].body.expected_name === curve.name && posts[0].body.type === 'GPU', '目标表绑定丢失');
      assert(oemDraft.CPU[1] === 40 && oemDraft.GPU[1] === 0, '回读丢失另一张草稿');
    });
    await check('撤销不是恢复默认，且不发送命令', () => {
      setOemDuty(1, 35); const before = posts.length; restoreOemDraft();
      assert(posts.length === before && oemDraft.GPU[1] === 0, '撤销发送了命令或基线错误');
    });
    await check('EC 对照不被自动 OEM 读取覆盖', async () => {
      await loadFanCurve(false); await poll();
      assert(fanSource === 'ec' && curveData && oemDraft.CPU[1] === 40, 'EC 对照或草稿丢失');
    });
    await check('EC 切回 OEM 后缩放仍绘制 OEM 编辑目标', () => {
      document.querySelector('[data-curve-table="CPU"]').click();
      const originalEcDraw = drawFanCurveChart, originalOemDraw = drawOemCurveEditor;
      let ecDraws = 0, oemDraws = 0;
      drawFanCurveChart = () => { ecDraws++; };
      drawOemCurveEditor = () => { oemDraws++; };
      try {
        window.dispatchEvent(new Event('resize'));
        assert(fanSource === 'mqtt' && ecDraws === 0 && oemDraws === 1, '画的是 EC，编辑的是 OEM');
      } finally { drawFanCurveChart = originalEcDraw; drawOemCurveEditor = originalOemDraw; }
    });
    reset();
    await check('未确认的曲线提交保留两张草稿且禁用再次下发', async () => {
      setOemDuty(1, 40); oemDraft.GPU[1] = 40; skipCurveWrite = true;
      await applyOemCurve();
      assert(oemDraft.CPU[1] === 40 && oemDraft.GPU[1] === 40, '未确认回读覆盖了草稿');
      assert(oemOrig.CPU[1] === 35 && !oemWritable(), '未确认回读被当成新的基线');
    });
    reset();
    await check('节点乱序归一化，重复或越界编号拒绝', () => {
      const unordered = structuredClone(curve); unordered.tables.CPU.reverse();
      assert(normalizeOemCurve(unordered).tables.CPU[0].id === 0, '节点乱序未整理');
      for (const id of [1, 16, -1]) {
        const invalid = structuredClone(curve); invalid.tables.CPU[0].id = id;
        let rejected = false; try { normalizeOemCurve(invalid); } catch { rejected = true; }
        assert(rejected, '错误节点编号被接纳：' + id);
      }
    });
    await check('读取失败与 stale 表保留草稿但不能下发', async () => {
      setOemDuty(1, 40); oemDraft.GPU[1] = 40; readError = '模拟读取超时';
      await loadOemCurve(false);
      assert(oemDraft.CPU[1] === 40 && oemDraft.GPU[1] === 40 && !oemWritable(), '读取失败丢草稿或开放写入');
      readError = null; curve.stale = true; await loadOemCurve(false);
      assert(oemDraft.CPU[1] === 40 && !oemWritable(), 'stale 表被接纳');
    });
    reset();
    await check('延迟曲线回包不覆盖已切换的档位', async () => {
      const gate = defer(); heldRead = gate.promise;
      const loading = track(loadOemCurve(false));
      state.hardware.oem.profile_name = 'Mode2'; renderCreator(state);
      gate.resolve(); heldRead = null; await loading;
      assert(!oemCurve && !oemDraft && creatorModeSeen === 'office', '旧档位回包被接纳');
    });
    reset();
    await check('档位变化令曲线失效并阻止旧性能草稿提交', () => {
      input('spc-pl1', 48); state.hardware.oem.profile_name = 'Mode2'; renderCreator(state);
      assert(!oemCurve && !oemDraft && spcNeedsRefresh, '旧草稿仍可应用');
      assert($('creator-workspace').inert && $('btn-spc-apply').disabled, '关闭模式仍可编辑');
    });
    reset();
    await check('自定义功耗墙也不发送未回读的 PL4', async () => {
      showView('custom'); renderCustom(state, true);
      assert($('pl4-range').disabled && $('pl4-val').textContent === '未回读', '自定义页未知 PL4 可编辑');
      await $('btn-pl-apply').onclick();
      assert(posts.length === 1 && !('pl4' in posts[0].body), '自定义页发送了未知 PL4');
    });
    reset();
    await check('总览区分硬件模式与软件档，并支持键盘进入配置', () => {
      state.tier = 'eco'; state.ts = Date.now() / 1000; renderOverview(state);
      assert($('ov-status-card').dataset.mode === 'turbo' && $('ov-perf-tier').textContent === TIERS.eco, '硬件档与软件档混写');
      assert($('ov-status-card').dataset.fresh === 'true', '新读数没有标识');
      showView('overview'); $('ov-status-card').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      assert($('view-creator').classList.contains('active'), '状态入口键盘不可用');
      const unknown = { ...state, ts: 0, hardware: {}, intent: null }; renderOverview(unknown);
      assert($('ov-status-card').dataset.mode === 'unknown' && $('ov-intent').textContent === '--', '未知状态被猜成默认值');
      assert($('ov-status-card').dataset.fresh === 'false', '过期读数仍显示实时');
    });
    reset();
    await check('无表名保护协议的旧后端只能看曲线', () => {
      delete state.meta.fan_curve_expected_name; renderCreator(state); renderOemCurve();
      assert(!oemWritable() && $('btn-curve-apply').disabled, '旧后端写入未被禁用');
    });
    reset(); showView('lighting');
    await check('七种灯效控件矩阵', () => {
      for (const effect of KB_EFFECTS) {
        document.querySelector('[data-effect="' + effect.id + '"]').click();
        const opts = KB_EFFECT_OPTS[effect.id];
        assert(!$('light-bright-box').hidden === opts.bright, effect.id + ' 亮度不匹配');
        assert(!$('light-speed-box').hidden === opts.speed, effect.id + ' 速度不匹配');
        assert(!$('light-direction-box').hidden === (opts.dir !== 'none'), effect.id + ' 方向不匹配');
        assert([...$('light-colors').children].filter((el) => !el.hidden).length === opts.colors, effect.id + ' 色块不匹配');
      }
    });
    await check('关闭或未知背光不可编辑，欢迎同步始终可见', () => {
      for (const power of [false, null]) {
        state.hardware.oem.kb_power_on = power; renderLighting(state);
        assert($('light-editor').hidden && $('btn-light-welcome').disabled, '背光未开启仍可编辑');
        assert(!$('btn-light-welcome').closest('.welcome-sync').hidden, '同步区域消失');
      }
    });
    await check('欢迎同步发送 welcome:true，灯效编辑加锁', async () => {
      state.hardware.oem.kb_power_on = true; renderLighting(state); selectLightEffect('Breathing');
      await applyLighting(true);
      assert(posts.at(-1).body.welcome === true && $('light-editor').inert, '欢迎同步参数或锁错误');
    });
    await check('自定义快捷入口与更多设置', () => {
      showView('custom'); document.querySelector('[data-creator-target="performance"]').click();
      assert(creatorActiveTab === 'performance' && $('view-creator').classList.contains('active'), '快捷入口失效');
      showView('settings'); assert($('view-settings').classList.contains('active'), '更多设置不可访问');
    });
    await check('快速开关下发中与等待回读均拒绝重复，确认后才解锁', async () => {
      for (const spec of [
        { id: 'kb', key: 'kbpower', field: 'kb_power_on', want: false, action: 'KB_POWER_OFF' },
        { id: 'win', key: 'winkey', field: 'win_key_locked', want: true, action: 'WINKEY_LOCK' },
      ]) {
        reset(); showView('quick');
        const button = $('quick-' + spec.id);
        assert(!button.disabled && typeof button.onclick === 'function', spec.id + ' 没有可用处理器');
        skipStateWrites.add('/api/action');
        const gate = defer(); heldWrite = gate.promise;
        const sending = track(button.onclick());
        assert(quickSending.has(spec.id) && button.disabled, spec.id + ' 下发期间没有锁');
        await poll(); await button.onclick();
        assert(posts.length === 1, spec.id + ' 轮询或重复点击绕过下发锁');
        gate.resolve(); heldWrite = null; await sending; await poll();
        assert(posts[0].path === '/api/action' && posts[0].body.action === spec.action, '快速开关命令错误');
        assert(PENDING[spec.key]?.want === spec.want && button.disabled, spec.id + ' 未回读就解锁');
        await button.onclick();
        assert(posts.length === 1, spec.id + ' 等待回读时重复下发');
        state.hardware.oem[spec.field] = spec.want; await poll();
        assert(!PENDING[spec.key] && !quickSending.size && !button.disabled, spec.id + ' 回读后仍锁定');
        assert(button.getAttribute('aria-checked') === String(spec.want), spec.id + ' 开关显示没有跟随回读');
        skipStateWrites.delete('/api/action');
        await button.onclick(); await poll();
        assert(posts.length === 2 && state.hardware.oem[spec.field] === !spec.want,
          spec.id + ' 反向开关没有使用 mock action 回声');
      }
    });
    await check('快速开关拒绝未知值、未验证能力、过期或断开连接', async () => {
      for (const [id, field, cap] of [
        ['kb', 'kb_power_on', 'lighting.rgb.write'], ['win', 'win_key_locked', 'winkey.write'],
      ]) {
        for (const reason of ['unknown', 'unverified', 'missing-cap', 'stale', 'offline']) {
          reset();
          if (reason === 'unknown') state.hardware.oem[field] = null;
          if (reason === 'unverified') state.capabilities[cap].state = 'unknown';
          if (reason === 'missing-cap') delete state.capabilities[cap];
          if (reason === 'stale') state.ts = Date.now() / 1000 - 60;
          if (reason === 'offline') state.channels.find((c) => c.name === 'mqtt').alive = false;
          await poll();
          const button = $('quick-' + id);
          assert(button.disabled, id + ' 在 ' + reason + ' 时仍可点');
          await button.onclick();
          assert(posts.length === 0 && !quickSending.size && !PENDING.kbpower && !PENDING.winkey,
            id + ' 在 ' + reason + ' 时仍发送命令');
        }
      }
    });
    reset();
    await check('风扇响应开关与间隔草稿不被非焦点轮询覆盖', async () => {
      showCreatorTab('performance');
      change('fanspeed-enabled', true); input('fanspeed-value', 900); $('fanspeed-value').blur();
      await poll();
      assert(fanSpeedDirty && $('fanspeed-enabled').checked && $('fanspeed-value').value === '900',
        '响应间隔或开关草稿被回读重填');
      assert(posts.length === 0, '编辑风扇响应草稿就下发了命令');
    });
    await check('风扇响应锁持续到 enabled 与 ms 同时匹配', async () => {
      skipStateWrites.add('/api/fan-switch-speed');
      const gate = defer(); heldWrite = gate.promise;
      const saving = track($('btn-fanspeed-apply').onclick());
      assert(fanSpeedBusy && $('btn-fanspeed-apply').disabled && $('btn-fanspeed-off').disabled,
        '风扇响应下发期间没有锁');
      await poll(); await $('btn-fanspeed-apply').onclick(); await $('btn-fanspeed-off').onclick();
      assert(posts.length === 1, '风扇响应重复提交或关闭绕过锁');
      gate.resolve(); heldWrite = null; await saving; await poll();
      assert(posts[0].path === '/api/fan-switch-speed' && posts[0].body.enabled === true
        && posts[0].body.ms === 900, '风扇响应提交没有使用草稿');
      assert(fanSpeedSubmitted && fanSpeedDirty, '未回读就清除了提交或草稿');
      state.hardware.oem.fan_switch_speed_enabled = true; await poll();
      assert(fanSpeedSubmitted && $('btn-fanspeed-apply').disabled && $('fanspeed-value').value === '900',
        '仅 enabled 匹配、ms 不匹配时提前确认或重填了草稿');
      state.hardware.oem.fan_switch_speed_enabled = false;
      state.hardware.oem.fan_switch_speed_ms = 900; await poll();
      assert(fanSpeedSubmitted && $('btn-fanspeed-apply').disabled, '仅 ms 匹配时提前确认');
      await $('btn-fanspeed-apply').onclick();
      assert(posts.length === 1, '等待精确回读时仍能重复下发');
      state.hardware.oem.fan_switch_speed_enabled = true; await poll();
      assert(!fanSpeedSubmitted && !fanSpeedBusy && !fanSpeedDirty && !$('btn-fanspeed-apply').disabled,
        '两项回读匹配后没有解除锁和草稿标记');
    });
    await check('风扇响应保存遵从 checkbox，恢复自动只等 enabled 回读', async () => {
      for (const buttonId of ['btn-fanspeed-apply', 'btn-fanspeed-off']) {
        reset(); state.hardware.oem.fan_switch_speed_enabled = true; await poll();
        change('fanspeed-enabled', buttonId === 'btn-fanspeed-off'); input('fanspeed-value', 1700);
        skipStateWrites.add('/api/fan-switch-speed');
        await $(buttonId).onclick(); await poll();
        assert(posts.length === 1 && posts[0].body.enabled === false, buttonId + ' 没有提交关闭');
        assert(fanSpeedSubmitted && $('btn-fanspeed-apply').disabled, '关闭没有等待 enabled 回读');
        state.hardware.oem.fan_switch_speed_enabled = false;
        state.hardware.oem.fan_switch_speed_ms = 300; await poll();
        assert(!fanSpeedSubmitted && !fanSpeedBusy && !fanSpeedDirty, '关闭仍错误等待 ms 匹配');
      }
    });
    await check('CC 功耗默认值只填三项草稿，保留 TCC 且绝不 POST', async () => {
      reset(); state.hardware.pl4 = state.hardware.pl4_setting = 150; await poll();
      change('spc-tcc-enabled', true); input('spc-tcc', 4);
      assert(!$('btn-spc-default').disabled, '已知读数和合法范围下默认草稿按钮未开放');
      await $('btn-spc-default').onclick(); await poll();
      for (const [key, value] of [['pl1', '75'], ['pl2', '75'], ['pl4', '165']]) {
        assert($('spc-' + key).value === value && spcDirty.has(key), key + ' 默认草稿未填入或被轮询覆盖');
      }
      assert(posts.length === 0 && state.hardware.pl1_setting === 45 && state.hardware.pl4_setting === 150,
        '填默认草稿改变了 mock 硬件状态');
      assert($('spc-tcc-enabled').checked && $('spc-tcc').value === '4'
        && spcDirty.has('tcc') && spcDirty.has('tcc-enabled'), '默认功耗覆盖了 TCC 草稿');
    });
    await check('默认草稿拒绝缺读数、不可写、无范围、越界和非实时状态', async () => {
      for (const reason of ['pl1', 'pl2', 'pl4', 'cap', 'bounds', 'range', 'stale', 'offline', 'mode']) {
        reset(); state.hardware.pl4 = state.hardware.pl4_setting = 150;
        if (['pl1', 'pl2', 'pl4'].includes(reason)) {
          state.hardware[reason] = null; state.hardware[reason + '_setting'] = null;
        }
        if (reason === 'cap') state.capabilities['power_limit.write'].state = 'unknown';
        if (reason === 'bounds') state.channels.forEach((c) => { if (c.detail) delete c.detail.pl_bounds; });
        if (reason === 'range') state.channels.find((c) => c.detail?.pl_bounds).detail.pl_bounds.pl4_max = 160;
        if (reason === 'stale') state.ts = Date.now() / 1000 - 60;
        if (reason === 'offline') state.channels.forEach((c) => { c.alive = false; });
        if (reason === 'mode') state.hardware.oem.profile_name = 'Mode2';
        await poll();
        const before = ['pl1', 'pl2', 'pl4'].map((k) => $('spc-' + k).value).join(',');
        assert($('btn-spc-default').disabled, reason + ' 时默认草稿按钮仍开放');
        await $('btn-spc-default').onclick();
        assert(posts.length === 0 && spcDirty.size === 0
          && ['pl1', 'pl2', 'pl4'].map((k) => $('spc-' + k).value).join(',') === before,
          reason + ' 时强制调用仍填入默认草稿或发送命令');
      }
    });
    await check('功耗提交拒绝 PL1 大于 PL2 或 PL2 大于 PL4', async () => {
      for (const values of [[90, 70, 150], [45, 100, 90]]) {
        reset(); state.hardware.pl4 = state.hardware.pl4_setting = 150; await poll();
        ['pl1', 'pl2', 'pl4'].forEach((k, i) => input('spc-' + k, values[i]));
        assert(!$('btn-spc-apply').disabled, '顺序校验用例没有进入可提交条件');
        await $('btn-spc-apply').onclick(); await poll();
        assert(posts.length === 0 && !spcBusy && !spcSubmitted, '非法功耗顺序仍下发或进入等待态');
        assert(['pl1', 'pl2', 'pl4'].every((k, i) => spcDirty.has(k)
          && Number($('spc-' + k).value) === values[i]), '非法顺序拒绝后丢失草稿');
      }
      reset(); state.hardware.pl4 = state.hardware.pl4_setting = 75; await poll();
      ['pl1', 'pl2', 'pl4'].forEach((k) => input('spc-' + k, 75));
      await $('btn-spc-apply').onclick(); await poll();
      assert(posts.length === 1 && posts[0].body.pl1 === 75 && posts[0].body.pl2 === 75
        && posts[0].body.pl4 === 75 && !spcSubmitted, '相等边界的合法功耗顺序被拒绝');
    });
    await check('常亮和音乐四物理区选色，palette 只改选中色块', async () => {
      reset(); showView('lighting');
      for (const effect of ['Single', 'Music']) {
        selectLightEffect(effect);
        const zones = [...$('keyboard-preview').querySelectorAll('button[data-zone]')];
        assert(zones.length === 4 && new Set(zones.map((b) => b.dataset.zone)).size === 4,
          effect + ' 不是四个独立物理区');
        assert(zones.every((b) => !b.disabled), effect + ' 物理分区不能选');
        for (let i = 0; i < 7; i++) input('light-color-' + i, '#123456');
        zones[2].click();
        assert(lightSelectedColor === Number(zones[2].dataset.zone), effect + ' 点击分区未选中色块');
        const chosen = lightSelectedColor;
        const swatches = [...$('palette-swatches').querySelectorAll('button')];
        assert(!$('palette-swatches').hidden && swatches.length > 1, effect + ' 缺少可用调色板');
        swatches[0].click();
        const first = $('light-color-' + chosen).value;
        swatches[1].click();
        assert($('light-color-' + chosen).value !== first, effect + ' 调色板点击未修改选中色块');
        assert([0, 1, 2, 3].filter((i) => i !== chosen).every((i) => $('light-color-' + i).value === '#123456'),
          effect + ' 调色板污染其他物理区');
        const body = lightingEditorPayload();
        assert(body.colors.slice(4).every((color) => JSON.stringify(color) === JSON.stringify(body.colors[3])),
          effect + ' 后三报文颜色没有跟随第四区');
      }
      await poll();
      assert(posts.length === 0, '预览分区或 palette 点击就发送命令');
    });
    await check('七色序列可选色但不能点物理区，彩虹不显示 palette', () => {
      reset(); showView('lighting');
      for (const effect of ['Breathing', 'Wave', 'Mix', 'Flash']) {
        selectLightEffect(effect);
        const zones = [...$('keyboard-preview').querySelectorAll('button[data-zone]')];
        assert(zones.length === 4 && zones.every((b) => b.disabled), effect + ' 把七色序列冒充物理分区');
        assert([...$('light-colors').children].filter((el) => !el.hidden).length === 7,
          effect + ' 没有保留七个序列颜色 input');
        $('light-color-6').focus();
        input('light-color-6', '#123456');
        assert(lightSelectedColor === 6, effect + ' 第七色 input 没有被选中');
        zones[0].click();
        assert(lightSelectedColor === 6, effect + ' 禁用区按钮仍改变颜色选择');
        const swatches = [...$('palette-swatches').querySelectorAll('button')];
        assert(!$('palette-swatches').hidden && swatches.length > 1, effect + ' 七色序列不能用 palette');
        swatches[0].click(); const first = $('light-color-6').value;
        swatches[1].click();
        assert($('light-color-6').value !== first && lightSelectedColor === 6,
          effect + ' palette 没有修改第七个序列颜色');
      }
      selectLightEffect('Rainbow');
      assert($('palette-swatches').hidden, '彩虹仍开放 palette');
      assert([...$('keyboard-preview').querySelectorAll('button[data-zone]')].every((b) => b.disabled),
        '彩虹仍允许点击物理分区');
      assert(posts.length === 0, '颜色预览发送了命令');
    });
    const makePreset = () => ({
      id: 'interaction-preset', name: '交互测试方案', ts: Date.now(), mode: 'turbo',
      tcc: { value: 4, enabled: true }, fanSpeed: { ms: 900, enabled: true },
    });
    await check('waitPresetState 不能采纳 ts 倒退但目标值匹配的回读', async () => {
      reset(); state.ts -= 2;
      assert(await poll() === true, '方案等待时间戳基线未读取');
      const accepted = lastState;
      const stale = structuredClone(state); stale.ts -= 1;
      stale.hardware.oem.tcc_offset_c = 4;
      assert(deviceOnline(stale), '方案倒退快照必须仍在新鲜窗口');
      const oldRead = queueStateRead(stale); oldRead.resolve();
      const fresh = structuredClone(stale); fresh.ts = accepted.ts + 1;
      const newRead = queueStateRead(fresh);
      const seen = []; let retries = 0;
      const savedTimeout = window.setTimeout;
      window.setTimeout = (callback, delay, ...args) => {
        if (delay !== 800) return savedTimeout(callback, delay, ...args);
        retries++;
        assert(retries === 1 && lastState === accepted && lastState.hardware.oem.tcc_offset_c === 3,
          '方案等待采纳了倒退快照，或没有在新回读后结束');
        newRead.resolve(); queueMicrotask(() => callback(...args)); return 0;
      };
      try {
        const read = await track(originalWaitPresetState((s) => {
          seen.push(s.ts); return s.hardware.oem.tcc_offset_c === 4;
        }, 1000));
        assert(oldRead.started && newRead.started && !stateReadQueue.length && retries === 1,
          '方案等待在倒退的匹配快照上提前完成');
        assert(!seen.includes(stale.ts) && read.ts === fresh.ts && lastState === read
          && read.hardware.oem.tcc_offset_c === 4, '未使用门控后的新快照确认方案');
        assert(posts.length === 0, '方案等待写入了设备');
      } finally { window.setTimeout = savedTimeout; }
    });
    await check('同档模式加 TCC 方案只发送 TCC，不进入模式等待', async () => {
      for (const cachedMode of ['turbo', 'office']) {
        reset();
        if (cachedMode === 'office') {
          state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode';
        }
        await poll(); showView('custom');
        // 即使界面缓存还是 Office，新 GET 已经是目标 Turbo，也不能再发同档命令。
        state.hardware.oem.profile_name = 'Mode3'; state.hardware.fan_mode_flag = 'Turbo_Mode';
        const p = makePreset(); delete p.fanSpeed; presets = [p]; renderPresets();
        const waits = [];
        waitPresetState = async (match, timeout) => {
          waits.push({ path: posts.at(-1)?.path, timeout });
          return originalWaitPresetState(match, timeout);
        };
        await applyPreset(p.id); await flush();
        assert(posts.length === 1 && posts[0].path === '/api/tcc'
          && posts[0].body.value === 4 && posts[0].body.enabled === true, cachedMode + ' 缓存下同档方案仍发模式或遗漏 TCC');
        assert(waits.length === 1 && waits[0].path === '/api/tcc' && waits[0].timeout === 20000,
          '同档方案调用了模式等待或没有确认 TCC');
        assert(!presetBusy && $('preset-progress').textContent.includes('已生效')
          && $('preset-progress').textContent.includes('造物者开关、提前降温'), '同档模式未记为完成或 TCC 未确认');
      }
    });
    await check('方案 TCC 关闭仍核对数值，只能由 false+4 确认', async () => {
      reset(); showView('custom');
      const p = { id: 'interaction-tcc-off', name: 'TCC 关闭确认', ts: Date.now(), tcc: { value: 4, enabled: false } };
      presets = [p]; skipStateWrites.add('/api/tcc');
      let retries = 0;
      const savedTimeout = window.setTimeout;
      window.setTimeout = (callback, delay, ...args) => {
        if (delay !== 800) return savedTimeout(callback, delay, ...args);
        retries++;
        assert(retries <= 2 && presetBusy && !$('preset-progress').textContent.includes('已生效'),
          'TCC 方案未等到两项匹配就完成，或两项匹配后仍等待');
        state.hardware.oem.tcc_offset_c = 4;
        state.hardware.oem.tcc_offset_enabled = retries === 1;
        queueMicrotask(() => callback(...args)); return 0;
      };
      try {
        await applyPreset(p.id); await flush();
        assert(posts.length === 1 && posts[0].path === '/api/tcc'
          && posts[0].body.value === 4 && posts[0].body.enabled === false, '关闭方案没有发出 false+4');
        assert(retries === 2 && !presetBusy && $('preset-progress').textContent.includes('已生效')
          && lastState.hardware.oem.tcc_offset_c === 4 && lastState.hardware.oem.tcc_offset_enabled === false,
          '关闭方案提前接受 false+3 或 true+4，或未接受 false+4');
      } finally { window.setTimeout = savedTimeout; }
    });
    await check('方案回读等待只接受连接新鲜且匹配的状态', async () => {
      for (const reason of ['stale', 'offline']) {
        reset();
        const mqtt = state.channels.find((c) => c.name === 'mqtt');
        if (reason === 'stale') state.ts = 0;
        else mqtt.alive = false;
        let retries = 0;
        const savedTimeout = window.setTimeout;
        // 仅推进等待函数的轮询，不执行真实 800 ms / 45 s 等待。
        window.setTimeout = (callback, delay, ...args) => {
          if (delay !== 800) return savedTimeout(callback, delay, ...args);
          retries++;
          if (retries > 2) throw new Error('等待函数未接受新鲜匹配状态');
          state.ts = Date.now() / 1000; mqtt.alive = true;
          state.hardware.oem.kb_power_on = retries === 2;
          queueMicrotask(() => callback(...args));
          return 0;
        };
        try {
          const read = await originalWaitPresetState((s) => s.hardware.oem.kb_power_on === true, 1000);
          assert(retries === 2 && read.hardware.oem.kb_power_on === true && read.ts > 0,
            reason + ' 或值不匹配的回读被提前接受');
          assert(posts.length === 0, '方案确认轮询发送了写命令');
        } finally { window.setTimeout = savedTimeout; }
      }
    });
    await check('方案等待模式 Promise 确认前不发送下一步，并拒绝重复应用', async () => {
      reset(); state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode'; await poll(); showView('custom');
      const p = makePreset(); presets = [p, { ...p, id: 'interaction-other' }]; renderPresets();
      const button = $('preset-list').querySelector('[data-apply="' + p.id + '"]');
      const gate = defer(); const waits = [];
      waitPresetState = async (match, timeout) => {
        waits.push({ match, timeout, path: posts.at(-1).path });
        if (waits.length === 1) await gate.promise;
        const read = await api('/api/state');
        if (!match(read)) throw new Error('mock 回读不匹配');
        lastState = read;
        return read;
      };
      const applying = track(button.onclick()); await flush();
      assert(posts.length === 1 && posts[0].path === '/api/mode' && waits.length === 1,
        '模式等待 Promise 未完成就发送了下一步');
      assert(waits[0].timeout === 45000 && presetBusy && button.disabled, '模式确认等待或方案锁未生效');
      const wrong = structuredClone(state); wrong.hardware.oem.profile_name = 'Mode2';
      wrong.hardware.fan_mode_flag = 'User_Fan_HiMode';
      assert(!waits[0].match(wrong), '模式回读 predicate 接受错误档位');
      assert(!$('preset-progress').hidden && $('preset-progress').textContent.includes('等待'), '没有呈现模式等待状态');
      await applyPreset(p.id, button);
      await $('preset-list').querySelector('[data-apply="interaction-other"]').onclick();
      assert(posts.length === 1 && waits.length === 1 && presetBusy, '同一或另一方案绕过全局重复锁');
      gate.resolve(); await applying; await flush();
      assert(posts.map((x) => x.path).join(',') === '/api/mode,/api/tcc,/api/fan-switch-speed',
        '确认后的方案执行顺序错误');
      assert(waits.map((x) => x.path).join(',') === posts.map((x) => x.path).join(','), '有步骤未等待回读');
      const wrongTcc = structuredClone(state); wrongTcc.hardware.oem.tcc_offset_c = 3;
      const wrongSpeed = structuredClone(state); wrongSpeed.hardware.oem.fan_switch_speed_ms = 500;
      assert(!waits[1].match(wrongTcc) && !waits[2].match(wrongSpeed), '方案只比使能位、不核对数值');
      assert(!presetBusy && !button.disabled && $('preset-progress').textContent.includes('已生效'),
        '全部确认后方案没有解锁或显示成功');
    });
    await check('方案确认超时立即停止，保留已完成步骤且不自动回退', async () => {
      for (const failAt of [1, 2]) {
        reset(); state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode'; await poll();
        const p = makePreset(); presets = [p]; showView('custom');
        let waits = 0;
        waitPresetState = async (match) => {
          waits++;
          if (waits === failAt) throw new Error('模拟确认超时');
          const read = await api('/api/state');
          if (!match(read)) throw new Error('mock 回读不匹配');
          lastState = read; return read;
        };
        await applyPreset(p.id); await flush();
        assert(posts.map((x) => x.path).join(',') === ['/api/mode', '/api/tcc'].slice(0, failAt).join(','),
          '超时后仍发送后续步骤或回退请求');
        const progressText = $('preset-progress').textContent;
        assert(waits === failAt && !presetBusy && ! $('preset-progress').hidden
          && progressText.includes('模拟确认超时') && progressText.includes('已完成：' + (failAt === 1 ? '无' : '造物者开关')),
          '超时未停止、未解锁或没有说明已完成步骤');
        assert(currentOemMode(state) === 'turbo', '失败后自动回退了已下发档位');
      }
    });
    await check('方案部分写入失败不发送后续灯效，也不回退已确认设置', async () => {
      reset(); state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode'; await poll();
      const p = { ...makePreset(), lighting: lightingEditorPayload() }; presets = [p]; showView('custom');
      postError = { path: '/api/fan-switch-speed', message: '模拟第三步写入失败' };
      let waits = 0;
      waitPresetState = async (match) => {
        waits++;
        const read = await api('/api/state');
        if (!match(read)) throw new Error('mock 回读不匹配');
        lastState = read; return read;
      };
      await applyPreset(p.id); await flush();
      assert(posts.map((x) => x.path).join(',') === '/api/mode,/api/tcc,/api/fan-switch-speed' && waits === 2,
        '部分失败后仍发灯效、回退或对失败写入等待确认');
      assert(currentOemMode(state) === 'turbo' && state.hardware.oem.tcc_offset_enabled
        && state.hardware.oem.tcc_offset_c === 4, '已确认的模式或 TCC 被回退');
      assert(!presetBusy && !$('preset-progress').hidden
        && $('preset-progress').textContent.includes('模拟第三步写入失败')
        && $('preset-progress').textContent.includes('已完成：造物者开关、提前降温'), '部分成功结果没有如实保留');
    });
    await check('高级功耗草稿跨轮询和导航保留，未知 PL4 始终不下发', async () => {
      reset(); showView('custom');
      input('pl1-range', 48); input('pl2-range', 70); $('pl2-range').blur();
      await poll(); showView('settings'); showView('custom');
      assert(plDirty && $('pl1-range').value === '48' && $('pl2-range').value === '70',
        '高级功耗非焦点轮询或重新进入页面丢失草稿');
      assert($('pl4-range').disabled && $('pl4-val').textContent === '未回读', '未知 PL4 被开放');
      await $('btn-pl-apply').onclick(); await poll();
      assert(posts.length === 1 && posts[0].path === '/api/power-limits'
        && posts[0].body.pl1 === 48 && posts[0].body.pl2 === 70 && !('pl4' in posts[0].body),
        '高级功耗没有使用草稿，或擅自补发未知 PL4');
      assert(state.hardware.pl4 === null && state.hardware.pl4_setting === null
        && $('pl4-range').disabled && $('pl4-val').textContent === '未回读', '确认其他功耗后未知 PL4 被猜值');
      assert(!plBusy && !plSubmitted && !plDirty, '已知项目完整回读后未销账');
    });
    await check('高级功耗发送和确认期间拒绝重复，三项完整且新鲜回读才解锁', async () => {
      reset(); state.hardware.pl4 = state.hardware.pl4_setting = 150; showView('custom');
      const values = { pl1: 48, pl2: 70, pl4: 155 };
      Object.entries(values).forEach(([k, v]) => input(k + '-range', v));
      skipStateWrites.add('/api/power-limits');
      const gate = defer(); heldWrite = gate.promise;
      const saving = track($('btn-pl-apply').onclick());
      assert(plBusy && $('btn-pl-apply').disabled && $('btn-pl-refresh').disabled, '高级功耗没有持久发送锁');
      await poll(); showView('settings'); showView('custom');
      await $('btn-pl-apply').onclick(); await $('btn-pl-refresh').onclick();
      assert(posts.length === 1 && plDirty && $('pl1-range').value === '48', '轮询/导航/重填绕过发送锁');
      gate.resolve(); heldWrite = null; await saving; await poll();
      assert(!plBusy && plSubmitted && plDirty && $('btn-pl-apply').disabled, 'POST 成功被误当成已生效');
      for (const missing of Object.keys(values)) {
        Object.entries(values).forEach(([k, v]) => { state.hardware[k + '_setting'] = k === missing ? v - 1 : v; });
        await poll(); await $('btn-pl-apply').onclick(); await $('btn-pl-refresh').onclick();
        assert(plSubmitted && plDirty && posts.length === 1 && $('btn-pl-refresh').disabled,
          missing + ' 不匹配时提前销账，或重填清掉了确认锁');
        assert(Object.entries(values).every(([k, v]) => Number($(k + '-range').value) === v), '不完整回读覆盖了提交草稿');
      }
      Object.entries(values).forEach(([k, v]) => { state.hardware[k + '_setting'] = v; });
      state.ts = Date.now() / 1000 - 60; await poll();
      assert(plSubmitted && $('btn-pl-apply').disabled, '过期的完整功耗读数被接受');
      state.ts = Date.now() / 1000; state.channels.find((c) => c.name === 'mqtt').alive = false; await poll();
      assert(plSubmitted && $('btn-pl-apply').disabled, '离线的完整功耗读数被接受');
      state.channels.find((c) => c.name === 'mqtt').alive = true; await poll();
      assert(!plSubmitted && !plBusy && !plDirty && !$('btn-pl-apply').disabled
        && !$('btn-pl-refresh').disabled && posts.length === 1, '完整实时回读未解除高级功耗锁');
    });
    await check('高级功耗处理器重新核对新鲜度、模式、能力和范围，不信任旧按钮状态', async () => {
      for (const reason of ['stale', 'offline', 'mode', 'unknown-mode', 'pending-mode', 'cap', 'missing-cap', 'bounds', 'no-readings']) {
        reset(); showView('custom'); input('pl1-range', 48);
        assert(!$('btn-pl-apply').disabled, '门禁用例初始状态不可提交');
        if (reason === 'stale') state.ts = Date.now() / 1000 - 60;
        if (reason === 'offline') state.channels.find((c) => c.name === 'mqtt').alive = false;
        if (reason === 'mode') { state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode'; }
        if (reason === 'unknown-mode') { state.hardware.oem.profile_name = null; state.hardware.fan_mode_flag = null; }
        if (reason === 'pending-mode') markPending('mode', 'office', 45, '模拟切档');
        if (reason === 'cap') state.capabilities['power_limit.write'].state = 'unknown';
        if (reason === 'missing-cap') delete state.capabilities['power_limit.write'];
        if (reason === 'bounds') state.channels.forEach((c) => { if (c.detail) delete c.detail.pl_bounds; });
        if (reason === 'no-readings') ['pl1', 'pl2', 'pl4'].forEach((k) => {
          state.hardware[k] = null; state.hardware[k + '_setting'] = null;
        });
        // 故意不先 render/poll：旧 enabled 按钮不能成为写权限。
        await $('btn-pl-apply').onclick();
        renderCustom(lastState);
        assert(posts.length === 0 && !plBusy && !plSubmitted && plDirty, reason + ' 绕过门禁或丢失草稿');
        assert($('btn-pl-apply').disabled, reason + ' 时高级功耗按钮仍开放');
      }
    });
    await check('高级功耗校验 PL1≤PL2≤PL4，拒绝后保留草稿且允许相等边界', async () => {
      for (const values of [[90, 70, 150], [45, 100, 90], [75, 75, 75]]) {
        reset(); state.hardware.pl4 = state.hardware.pl4_setting = 150; showView('custom');
        ['pl1', 'pl2', 'pl4'].forEach((k, i) => input(k + '-range', values[i]));
        await $('btn-pl-apply').onclick(); await poll();
        if (values[0] === 75) {
          assert(posts.length === 1 && ['pl1', 'pl2', 'pl4'].every((k) => posts[0].body[k] === 75)
            && !plSubmitted, '高级功耗拒绝了相等的合法边界');
        } else {
          assert(posts.length === 0 && plDirty && !plBusy && !plSubmitted, '非法功耗顺序进入发送态');
          assert(['pl1', 'pl2', 'pl4'].every((k, i) => Number($(k + '-range').value) === values[i]), '顺序拒绝后丢失草稿');
        }
      }
    });
    await check('高级功耗跨档位保留草稿但必须显式重填才解锁', async () => {
      reset(); showView('custom'); input('pl1-range', 48); input('pl2-range', 70);
      const epoch = plEpoch;
      state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode'; await poll();
      assert(plEpoch > epoch && plNeedsRefresh && plDirty, '切档没有使高级功耗草稿失效');
      state.hardware.oem.profile_name = 'Mode3'; state.hardware.fan_mode_flag = 'Turbo_Mode'; await poll();
      showView('settings'); showView('custom');
      await $('btn-pl-apply').onclick();
      assert(plNeedsRefresh && $('btn-pl-apply').disabled && posts.length === 0
        && $('pl1-range').value === '48' && $('pl2-range').value === '70', '回到原档或导航自动解锁/重填了旧草稿');
      assert(!$('btn-pl-refresh').disabled, '显式重填入口没有开放');
      await $('btn-pl-refresh').onclick(); await poll();
      assert(!plNeedsRefresh && !plDirty && !plSubmitted && !$('btn-pl-apply').disabled
        && $('pl1-range').value === '45' && $('pl2-range').value === '65' && posts.length === 0,
        '显式重填没有读取当前基线，或意外下发');
    });
    await check('高级功耗旧 epoch 的延迟写入回包不恢复提交态', async () => {
      reset(); showView('custom'); input('pl1-range', 48);
      skipStateWrites.add('/api/power-limits');
      const gate = defer(); heldWrite = gate.promise;
      const saving = track($('btn-pl-apply').onclick()); const epoch = plEpoch;
      state.hardware.oem.profile_name = 'Mode2'; state.hardware.fan_mode_flag = 'User_Fan_HiMode'; await poll();
      assert(plEpoch > epoch && plNeedsRefresh, '写入期间外部切档未推进 epoch');
      gate.resolve(); heldWrite = null; await saving; await poll();
      assert(!plBusy && !plSubmitted && plNeedsRefresh && plDirty && $('pl1-range').value === '48'
        && $('btn-pl-apply').disabled && posts.length === 1, '旧档位回包重新建立确认态或清掉了草稿');
    });
    await check('高级功耗确认超时保留草稿，重填前不能再次发送', async () => {
      reset(); showView('custom'); input('pl1-range', 48); skipStateWrites.add('/api/power-limits');
      await $('btn-pl-apply').onclick(); await poll();
      assert(plSubmitted, '超时用例没有进入确认态');
      plSubmitted.until = Date.now() - 1; await poll();
      assert(!plSubmitted && !plBusy && plDirty && plNeedsRefresh && $('pl1-range').value === '48'
        && $('btn-pl-apply').disabled, '超时丢草稿或未经核对重新开放写入');
      await $('btn-pl-apply').onclick();
      assert(posts.length === 1, '确认超时后自动/重复发送');
      await $('btn-pl-refresh').onclick(); await poll();
      assert(!plNeedsRefresh && !plDirty && !$('btn-pl-apply').disabled && posts.length === 1, '超时后显式重填未解锁');
    });
    await check('高级功耗写入失败释放 busy 但保留草稿，不假报等待回读', async () => {
      reset(); showView('custom'); input('pl1-range', 48);
      postError = { path: '/api/power-limits', message: '模拟功耗写入被拒绝' };
      await $('btn-pl-apply').onclick(); await poll();
      assert(posts.length === 1 && !plBusy && !plSubmitted && plDirty && $('pl1-range').value === '48',
        '功耗异常路径死锁、丢草稿或假报已下发');
      assert(state.hardware.pl1_setting === 45, '失败 mock 仍改变了硬件读数');
    });
    await check('高级功耗发送和等待回读均与 SPC、模式和方案互锁', async () => {
      reset(); showView('custom'); input('pl1-range', 48); skipStateWrites.add('/api/power-limits');
      const p = makePreset(); presets = [p]; renderPresets();
      const gate = defer(); heldWrite = gate.promise;
      const saving = track($('btn-pl-apply').onclick());
      for (const phase of ['busy', 'submitted']) {
        if (phase === 'submitted') { gate.resolve(); heldWrite = null; await saving; }
        await poll();
        assert(phase === 'busy' ? plBusy : !!plSubmitted, '互锁测试未进入 ' + phase);
        for (const id of ['btn-spc-apply', 'btn-spc-tcc-apply', 'btn-spc-default', 'btn-creator-mode']) {
          assert($(id).disabled, phase + ' 时 ' + id + ' 未禁用');
          await $(id).onclick();
        }
        await applyPreset(p.id);
        assert(posts.length === 1 && !presetBusy && !spcBusy && !spcSubmitted && !PENDING.mode,
          phase + ' 时其他编辑器绕过高级功耗锁');
      }
      state.hardware.pl1_setting = 48; await poll(); renderCreator(lastState);
      assert(!plSubmitted && !$('btn-spc-apply').disabled && !$('btn-creator-mode').disabled, '功耗确认后其他编辑器未解锁');
    });
    await check('SPC、模式和方案锁反向阻止高级功耗提交与重填', async () => {
      for (const lock of ['spcBusy', 'spcSubmitted', 'mode', 'presetBusy']) {
        reset(); showView('custom'); input('pl1-range', 48);
        if (lock === 'spcBusy') spcBusy = true;
        if (lock === 'spcSubmitted') spcSubmitted = { values: { pl1: 49 }, until: Date.now() + 20000 };
        if (lock === 'mode') markPending('mode', 'office', 45, '模拟切档');
        if (lock === 'presetBusy') presetBusy = true;
        await $('btn-pl-apply').onclick(); renderCustom(lastState);
        assert($('btn-pl-apply').disabled && $('btn-pl-refresh').disabled, lock + ' 未反向锁定高级功耗');
        await $('btn-pl-refresh').onclick();
        assert(posts.length === 0 && plDirty && $('pl1-range').value === '48' && !plBusy && !plSubmitted,
          lock + ' 时提交/重填绕过锁或丢草稿');
      }
    });
    await check('灯效手动刷新借用 kb 发送锁，重复刷新/背光/灯效都不能穿透', async () => {
      const savedTimeout = window.setTimeout;
      window.setTimeout = (callback, delay, ...args) => {
        if (delay !== 800) return savedTimeout(callback, delay, ...args);
        queueMicrotask(() => callback(...args)); return 0;
      };
      try {
        for (const power of [true, false]) {
          reset(); state.hardware.oem.kb_power_on = power; showView('lighting'); renderLighting(state);
          const gate = defer(); heldWrite = gate.promise;
          const refreshing = track($('btn-light-refresh').onclick());
          assert(quickSending.has('kb') && $('btn-light-refresh').disabled, '手动刷新没有持久 kb 发送锁');
          await poll();
          assert($('quick-kb').disabled && $('btn-light-power').disabled && $('btn-light-apply').disabled,
            '刷新期间轮询解锁了背光或灯效');
          await $('btn-light-refresh').onclick(); await $('quick-kb').onclick();
          await $('btn-light-power').onclick(); await applyLighting();
          assert(posts.length === 1 && posts[0].path === '/api/action'
            && posts[0].body.action === (power ? 'KB_POWER_ON' : 'KB_POWER_OFF'),
            '刷新不是幂等背光请求，或重复调用绕过锁');
          gate.resolve(); heldWrite = null; await refreshing; await poll();
          assert(!quickSending.has('kb') && !$('btn-light-refresh').disabled && !$('quick-kb').disabled
            && state.hardware.oem.kb_power_on === power, '刷新完成没有解锁或改变了背光');
        }
      } finally { window.setTimeout = savedTimeout; }
    });
    await check('灯效刷新 POST 成功但状态 GET 失败，保留草稿、释放 kb 锁且不报成功', async () => {
      reset(); showView('lighting'); selectLightEffect('Wave');
      input('light-brightness', 1); input('light-speed', 1);
      for (let i = 0; i < 7; i++) input('light-color-' + i, '#123456');
      const draft = JSON.stringify(lightingEditorPayload());
      const read = queueStateRead(state, '模拟键盘刷新状态 GET 失败');
      const notices = [], savedToast = toast, savedTimeout = window.setTimeout;
      toast = (text, bad) => { notices.push({ text, bad: !!bad }); savedToast(text, bad); };
      window.setTimeout = (callback, delay, ...args) => {
        if (delay !== 800) return savedTimeout(callback, delay, ...args);
        queueMicrotask(() => callback(...args)); return 0;
      };
      try {
        const refreshing = track($('btn-light-refresh').onclick()); await flush();
        assert(posts.length === 1 && posts[0].path === '/api/action' && posts[0].body.action === 'KB_POWER_ON'
          && read.started && quickSending.has('kb'), '刷新没有在 POST 成功后持锁等待状态 GET');
        read.resolve(); await refreshing;
        assert(JSON.stringify(lightingEditorPayload()) === draft && lightEditorInit,
          '状态 GET 失败后仍用缓存重填并丢失灯光草稿');
        assert(!quickSending.has('kb') && !lightBusy && !lightSubmitted && !PENDING.kbpower,
          '状态 GET 失败遗留 kb 发送锁或伪造了提交态');
        assert(notices.some((n) => n.bad && n.text.includes('刷新失败')) && !notices.some((n) => !n.bad),
          '状态 GET 失败没有错误提示，或错误地显示了成功提示');
        assert(!deviceOnline(lastState) && $('btn-light-refresh').disabled, '最新 GET 失败没有保持离线门禁');
        assert(await poll() === true && !$('btn-light-refresh').disabled
          && JSON.stringify(lightingEditorPayload()) === draft && posts.length === 1, '连接恢复后仍死锁、丢草稿或自动重发');
      } finally { toast = savedToast; window.setTimeout = savedTimeout; }
    });
    await check('灯效手动刷新重新核对在线、能力、未知状态和各项操作锁', async () => {
      for (const reason of ['stale', 'offline', 'cap', 'missing-cap', 'unknown', 'preset', 'lightBusy', 'lightSubmitted', 'kbSending', 'kbPending']) {
        reset(); showView('lighting');
        if (reason === 'stale') state.ts = Date.now() / 1000 - 60;
        if (reason === 'offline') state.channels.find((c) => c.name === 'mqtt').alive = false;
        if (reason === 'cap') state.capabilities['lighting.rgb.write'].state = 'unknown';
        if (reason === 'missing-cap') delete state.capabilities['lighting.rgb.write'];
        if (reason === 'unknown') state.hardware.oem.kb_power_on = null;
        if (reason === 'preset') presetBusy = true;
        if (reason === 'lightBusy') lightBusy = true;
        if (reason === 'lightSubmitted') lightSubmitted = {
          body: { ...lightingEditorPayload(), effect: 'Rainbow' }, until: Date.now() + 20000,
        };
        if (reason === 'kbSending') quickSending.add('kb');
        if (reason === 'kbPending') markPending('kbpower', false, 20, '模拟背光等待');
        await $('btn-light-refresh').onclick(); renderLighting(lastState);
        assert(posts.length === 0 && $('btn-light-refresh').disabled, reason + ' 时手动刷新仍发送幂等写命令');
      }
    });
    await check('灯效刷新 finally 按最新门禁渲染，失败释放 kb 锁而非无条件点亮', async () => {
      for (const fail of [false, true]) {
        reset(); showView('lighting');
        const savedTimeout = window.setTimeout;
        window.setTimeout = (callback, delay, ...args) => {
          if (delay !== 800) return savedTimeout(callback, delay, ...args);
          queueMicrotask(() => callback(...args)); return 0;
        };
        try {
          if (fail) postError = { path: '/api/action', message: '模拟键盘刷新失败' };
          const gate = defer(); heldWrite = gate.promise;
          const refreshing = track($('btn-light-refresh').onclick());
          state.capabilities['lighting.rgb.write'].state = 'unknown'; await poll();
          gate.resolve(); heldWrite = null; await refreshing;
          assert(!quickSending.has('kb') && $('btn-light-refresh').disabled && $('quick-kb').disabled
            && $('btn-light-power').disabled && posts.length === 1, '刷新 finally 忽略新能力状态或遗留发送锁');
          state.capabilities['lighting.rgb.write'].state = 'verified'; await poll();
          assert(!$('btn-light-refresh').disabled && !$('quick-kb').disabled, '恢复能力后刷新仍死锁');
        } finally { window.setTimeout = savedTimeout; }
      }
    });
    await check('风扇响应写入错误详情经 finally 和轮询仍保留，草稿可重试', async () => {
      reset(); showCreatorTab('performance'); change('fanspeed-enabled', true); input('fanspeed-value', 900);
      postError = { path: '/api/fan-switch-speed', message: '模拟响应写入被拒绝：设备忙 E17' };
      await $('btn-fanspeed-apply').onclick(); await poll(); renderFanSpeed(lastState);
      assert($('fanspeed-feedback').textContent.includes(postError.message), 'finally/轮询覆盖了风扇响应错误详情');
      assert(!fanSpeedBusy && !fanSpeedSubmitted && fanSpeedDirty && $('fanspeed-enabled').checked
        && $('fanspeed-value').value === '900' && !$('btn-fanspeed-apply').disabled, '失败后丢草稿或留下死锁');
      assert(posts.length === 1 && state.hardware.oem.fan_switch_speed_ms === 500, '失败后自动重发或改变读数');
      postError = null; await $('btn-fanspeed-apply').onclick(); await poll();
      assert(posts.length === 2 && !fanSpeedDirty && !fanSpeedSubmitted
        && !$('fanspeed-feedback').textContent.includes('E17'), '显式重试成功后没有清除旧错误');
    });
    const missingPresetFields = [
      ['mode', () => { state.hardware.oem.profile_name = null; state.hardware.fan_mode_flag = null; }],
      ['tcc', () => { state.hardware.oem.tcc_offset_c = null; }],
      ['tcc-enabled', () => { state.hardware.oem.tcc_offset_enabled = null; }],
      ['fanspeed', () => { state.hardware.oem.fan_switch_speed_ms = null; }],
      ['fanspeed-enabled', () => { state.hardware.oem.fan_switch_speed_enabled = null; }],
      ['light', () => { lightEditorInit = false; }],
      ['light-power', () => { state.hardware.oem.kb_power_on = null; }],
    ];
    await check('capturePreset 精确捕获所选项目，性能保存已确认读数而非未提交草稿', () => {
      reset(); change('spc-tcc-enabled', true); input('spc-tcc', 4);
      change('fanspeed-enabled', true); input('fanspeed-value', 900);
      for (const [key, field] of [['mode', 'mode'], ['tcc', 'tcc'], ['fanspeed', 'fanSpeed'], ['light', 'lighting']]) {
        const p = capturePreset('单项捕获', { [key]: true });
        assert(p && p[field] != null, key + ' 已选且有读数却被遗漏');
        assert(['mode', 'tcc', 'fanSpeed', 'lighting'].filter((other) => other !== field)
          .every((other) => !(other in p)), key + ' 捕获了未选择的项目');
        if (key === 'tcc') assert(p.tcc.value === 3 && p.tcc.enabled === false, 'TCC 把未确认草稿当成设备读数');
        if (key === 'fanspeed') assert(p.fanSpeed.ms === 500 && p.fanSpeed.enabled === false, '响应设置把未确认草稿当成设备读数');
        if (key === 'light') assert(JSON.stringify(p.lighting) === JSON.stringify(lightingEditorPayload()), '灯效捕获未保持编辑器内容');
      }
      assert(posts.length === 0 && presetSaves.length === 0 && presets.length === 0, 'capturePreset 产生了副作用');
    });
    await check('保存含缺读数选项的方案整体失败，已有方案和表单内容保持不变', async () => {
      for (const [field, remove] of missingPresetFields) {
        reset(); showView('custom'); presets = [makePreset()]; renderPresets();
        const before = JSON.stringify(presets);
        $('btn-preset-new').onclick(); $('preset-name').value = '不可部分保存';
        for (const key of ['mode', 'tcc', 'fanspeed', 'light']) $('preset-inc-' + key).checked = true;
        remove(); await $('btn-preset-save').onclick();
        assert(JSON.stringify(presets) === before && presetSaves.length === 0 && posts.length === 0,
          field + ' 缺读数时仍保存部分方案或写入持久存储');
        assert(!$('preset-form').hidden && $('preset-name').value === '不可部分保存'
          && ['mode', 'tcc', 'fanspeed', 'light'].every((key) => $('preset-inc-' + key).checked), '整体拒绝后表单或选项被清空');
      }
    });
    await check('完整方案保存只落 mock，未勾选的缺读数项不影响保存', async () => {
      for (const full of [true, false]) {
        reset(); showView('custom'); $('btn-preset-new').onclick(); $('preset-name').value = '完整捕获测试';
        for (const key of ['mode', 'tcc', 'fanspeed', 'light']) $('preset-inc-' + key).checked = full || key === 'mode';
        if (!full) {
          state.hardware.oem.tcc_offset_c = null; state.hardware.oem.fan_switch_speed_ms = null;
          lightEditorInit = false; state.hardware.oem.kb_power_on = null;
        }
        const lighting = full ? lightingEditorPayload() : null;
        await $('btn-preset-save').onclick();
        assert(presets.length === 1 && presetSaves.length === 1 && posts.length === 0 && $('preset-form').hidden,
          '合法方案未保存或意外发送硬件命令');
        const p = presets[0];
        assert(p.mode === 'turbo' && p.name === '完整捕获测试', '方案遗漏已选择的模式/名称');
        if (full) {
          assert(p.tcc?.value === 3 && p.tcc.enabled === false && p.fanSpeed?.ms === 500 && p.fanSpeed.enabled === false
            && JSON.stringify(p.lighting) === JSON.stringify(lighting), '完整方案静默遗漏选中的项目或误存草稿');
        } else assert(!p.tcc && !p.fanSpeed && !p.lighting, '方案加入了未勾选的项目');
      }
    });
    await check('OEM 曲线接受扩展转换后的 hsla 颜色，渐变使用语义 fill tokens', () => {
      reset(); showView('creator'); showCreatorTab('fan');
      const originalChartColor = chartColor;
      const cv = $('fan-curve-chart'); const g = cv.getContext('2d');
      const originalGradient = g.createLinearGradient;
      const requested = [], stops = [];
      chartColor = (token) => {
        requested.push(token);
        if (token === '--cyan') return 'hsla(180, 100%, 50%, 0.9)';
        if (token === '--color-chart-fill-start') return 'hsla(180, 100%, 50%, 0.16)';
        if (token === '--color-chart-fill-end') return 'hsla(180, 100%, 50%, 0.01)';
        return originalChartColor(token);
      };
      g.createLinearGradient = function (...args) {
        const gradient = originalGradient.apply(this, args);
        const add = gradient.addColorStop;
        gradient.addColorStop = function (offset, color) {
          stops.push([offset, color]); return add.call(this, offset, color);
        };
        return gradient;
      };
      try {
        assert(cv.clientWidth > 0 && cv.clientHeight > 0, '曲线不可见，绘图回归不能空跑');
        drawOemCurveEditor();
        assert(oemChartGeometry?.mapped.length > 0, 'hsla 颜色导致曲线绘制中断');
        assert(requested.includes('--color-chart-fill-start') && requested.includes('--color-chart-fill-end'), '渐变未使用语义颜色令牌');
        assert(stops.length === 2 && stops[0][0] === 0 && stops[1][0] === 1
          && stops[0][1] === 'hsla(180, 100%, 50%, 0.16)' && stops[1][1] === 'hsla(180, 100%, 50%, 0.01)',
          '渐变仍从 cyan 文本拼接 alpha 而不是直接使用 fill token');
        assert(posts.length === 0, '纯绘图触发了硬件命令');
      } finally { chartColor = originalChartColor; g.createLinearGradient = originalGradient; }
    });
    return { passed: results.length, cases: results, hardwareWrites: 0 };
  } finally {
    // 失败路径也先释放受控 Promise；清理完成前绝不恢复真实 API。
    waitPresetState = async () => { throw new Error('交互测试结束'); };
    deferreds.forEach((resolve) => resolve());
    heldWrite = null; heldRead = null; stateReadQueue.length = 0;
    await Promise.allSettled([...running]); await flush();
    const restoreGlobals = () => {
      if (typeof stateRequestId !== 'undefined') stateRequestId = saved.stateRequestId;
      if (typeof stateSettledId !== 'undefined') stateSettledId = saved.stateSettledId;
      if (typeof stateSnapshotTs !== 'undefined') stateSnapshotTs = saved.stateSnapshotTs;
      ({ lastState, META, histData, presets, plDirty, plFilled, plBusy, plSubmitted, plNeedsRefresh, plEpoch, presetBusy,
        fanSpeedDirty, fanSpeedBusy, fanSpeedSubmitted, lightSelectedColor,
        lightEffect, lightEditorInit, lightBusy, lightSubmitted,
        creatorModeSeen, creatorActiveTab, fanSource, oemCurve, oemOrig, oemDraft, oemOrigName,
        oemEpoch, spcEpoch, oemReadAttempted, oemReadError, oemType, oemWriting, oemLoading,
        oemDragIndex, oemFocusIndex, oemChartGeometry, curveData, curveLoading,
        spcBusy, spcSubmitted, spcNeedsRefresh, benchWasRunning } = saved);
      spcDirty.clear(); saved.spcDirty.forEach((key) => spcDirty.add(key));
      quickSending.clear(); saved.quickSending.forEach((key) => quickSending.add(key));
      Object.keys(PENDING).forEach((key) => delete PENDING[key]);
      Object.assign(PENDING, structuredClone(saved.pending));
    };
    try {
      restoreGlobals();
      // 避免恢复视图时触发按需读取；已有草稿按原值保留，不重建成基线。
      oemReadAttempted = true;
      selectLightEffect(saved.lightEffect);
      showView(originalView, { scroll: false }); showCreatorTab(saved.creatorActiveTab);
      renderOverview(lastState); renderCreator(lastState); renderLighting(lastState);
      renderQuick(lastState); renderFanSpeed(lastState); renderCustom(lastState, true);
      renderPresets(); renderOemCurve();
      controls.forEach(({ el, value, checked, disabled, min, max }) => {
        if (!el.isConnected) return;
        if (min !== undefined) el.min = min;
        if (max !== undefined) el.max = max;
        el.value = value; el.checked = checked; el.disabled = disabled;
        if (el.type === 'range') paintRange(el);
      });
      updateLightVals(); syncLightPreview();
      feedback.forEach(({ el, text, hidden }) => { el.textContent = text; el.hidden = hidden; });
      $('preset-form').hidden = originalPresetFormHidden;
      viewInert.forEach(([el, inert]) => { el.inert = inert; });
      if (originalFocus?.isConnected) originalFocus.focus({ preventScroll: true });
      try {
        if (storedView === null) localStorage.removeItem('umi.view');
        else if (storedView !== undefined) localStorage.setItem('umi.view', storedView);
      } catch { /* 隐私模式 */ }
    } finally {
      restoreGlobals();
      seriesCurrent.forEach(({ series, had, value }) => {
        if (had) series._cur = value;
        else delete series._cur;
      });
      timers.forEach((id) => clearTimeout(id));
      frames.forEach((id) => cancelAnimationFrame(id));
      clearTimeout(toastTimer); document.querySelector('.toast')?.classList.remove('show');
      window.setTimeout = originalSetTimeout; window.requestAnimationFrame = originalRaf;
      waitPresetState = originalWaitPresetState; savePresets = originalSavePresets;
      window.confirm = originalConfirm; api = originalApi;
    }
  }
}
