"""自适应调度：把「负载 + 空闲 + 温度趋势」映射成四个执行档。

设计约束（都是本机踩出来的）：
  * 升档要快、降档要慢，且每一级的门槛不同：一次性从省电跳满性能会让风扇骤起；
  * 人工意图（面板/实体键/刚回到电脑前）必须被尊重，不能被瞬时低负载冲掉 —— 驻留期；
  * 亮屏/解锁后的第一拍不能是省电档，否则就是用户说的「回来爬频卡顿」；
  * 温度还在快速上冲时就提前解锁功耗墙：任务快进快出，风扇高转的总时长反而更短。

决策（decide）是纯函数式的：只做判断、不碰系统，用注入的 now 便于单测。
"""
from collections import deque

TIERS = ('eco', 'bal', 'mid', 'perf')
TIER_LABELS = {'eco': '省电', 'bal': '均衡', 'mid': '流畅', 'perf': '性能'}
INTENT_TIER = {'office': 'eco', 'balance': 'bal', 'turbo': 'perf'}
# 这些来源触发的性能档视为「人工意图」，进入驻留期
MANUAL_REASONS = ('手动', '面板', '实体按键', '亮屏', '锁定意图')


def _idx(tier):
    return TIERS.index(tier)


class Scheduler:
    def __init__(self, params, apps=None):
        self.p = dict(params)
        self.apps = apps or {}
        self.tier = 'bal'
        self.reason = '初始'
        self._hold = {}
        self._temp_hist = deque(maxlen=40)
        self._prev_idle = None
        self.throttle = False
        self.dwell_until = 0.0
        self.resume_until = 0.0
        self.last_change_ts = 0.0
        self._back_to = None
        self.pending = None
        self.history = []

    # ---------- 基础工具 ----------
    def _held(self, key, cond, now):
        """返回该条件已连续满足的秒数；一旦不满足立即清零。"""
        if cond:
            start = self._hold.setdefault(key, now)
            return now - start
        self._hold.pop(key, None)
        return 0.0

    def _clear_holds(self, keep=()):
        """换档时清零计时器，但升档计时要保留：
        否则 bal→mid 那一拍会把 perf_enter 抹掉，重负载要再等 2.5 秒才拉得满。"""
        self._hold = {k: v for k, v in self._hold.items() if k in keep}

    def _ok(self, key, cond, now, need_s):
        return self._held(key, cond, now) >= need_s

    def _try(self, tier, reason, now):
        """自动漂移的唯一出口：带防抖。

        本机 2026-09-29 实测：不防抖时性能档和流畅档每 10~30 秒互相切一次，
        用户看到的是「电源方案一直在变」，比永远不给性能档还烦。

        两条规则，都只针对「来回跳」：
          * 降档后隔太久才允许再降（min_down_dwell_s）——降档本身要慢；
          * 刚从哪一档掉下来，短时间内不许原样爬回去（min_up_dwell_s）——
            这一条专门掐死 perf↔mid 的循环；升到**更高**的目标档不拦，
            所以游戏刚开、任务刚点火这些情况仍然是立刻给满。
        """
        target, cur = _idx(tier), _idx(self.tier)
        gap = now - self.last_change_ts
        big = abs(target - cur) >= int(self.p.get('big_jump_levels', 2))
        if target > cur:
            need = float(self.p.get('min_up_dwell_s', 20.0))
            if self._back_to == tier and gap < need:
                self.pending = {'tier': tier, 'reason': reason, 'up': True,
                                'in_s': round(need - gap, 1)}
                return False
        elif not big and gap < float(self.p.get('min_down_dwell_s', 45.0)):
            self.pending = {'tier': tier, 'reason': reason, 'up': False,
                            'in_s': round(float(self.p.get('min_down_dwell_s', 45.0)) - gap, 1)}
            return False
        return self.set_tier(tier, reason, now)

    def set_tier(self, tier, reason, now, forced=False):
        if tier == self.tier and not forced:
            return False
        changed = tier != self.tier
        self.tier = tier
        self.reason = reason
        if changed:
            # 只有「降档」要记住来路：防抖掐的是刚掉下来又原样爬回去，不是升档本身
            self._back_to = self.tier if _idx(tier) < _idx(self.tier) else None
            self.last_change_ts = now
            self._clear_holds(keep=('perf_enter', 'mid_enter'))
            self.history.append((now, tier, reason))
            del self.history[:-30]
        else:
            self._clear_holds()
            del self.history[:-30]
        if tier == 'perf' and any(w in reason for w in MANUAL_REASONS):
            self.dwell_until = now + float(self.p.get('dwell_s', 300.0))
        return changed

    # ---------- 温度趋势 ----------
    def _temp_trend_hot(self, temp, cpu_pct, now):
        if temp is None:
            return False
        window = float(self.p.get('temp_rise_window_s', 5.0))
        self._temp_hist.append((now, temp))
        while self._temp_hist and now - self._temp_hist[0][0] > window * 2:
            self._temp_hist.popleft()
        if len(self._temp_hist) < 2:
            return False
        base_ts, base_t = self._temp_hist[0]
        if now - base_ts < window * 0.6:
            return False
        rise = temp - base_t
        return (rise >= float(self.p.get('temp_rise_deg', 4.0))
                and temp >= float(self.p.get('temp_rise_min_c', 70.0))
                and (cpu_pct or 0) >= float(self.p.get('temp_rise_load_min', 12.0)))

    # ---------- 温度保护 ----------
    def _update_throttle(self, temp, now):
        if temp is None:
            return self.throttle
        if not self.throttle and temp >= float(self.p.get('temp_limit_c', 95.0)):
            self.throttle = True
            self.log_throttle = '温度 %s°C 触发保护' % temp
        elif self.throttle and temp <= float(self.p.get('temp_recover_c', 90.0)):
            self.throttle = False
        return self.throttle

    # ---------- 主判定 ----------
    def decide(self, snap, now, intent='auto'):
        """snap 需要含 cpu_pct / gpu_pct / cpu_temp / idle_s / on_ac / foreground。"""
        cpu = snap.get('cpu_pct') or 0.0
        gpu = snap.get('gpu_pct') or 0.0
        temp = snap.get('cpu_temp')
        idle = snap.get('idle_s') or 0.0
        on_ac = snap.get('on_ac', True)
        fg = (snap.get('foreground') or '').lower()
        self.pending = None

        self._update_throttle(temp, now)
        light = fg in set(a.lower() for a in self.apps.get('light', []))
        boost_app = fg in set(a.lower() for a in self.apps.get('boost', []))

        # 回到电脑前的缓冲：空闲 >=5s 后突然 <0.6s
        if self._prev_idle is not None and self._prev_idle >= 5.0 and idle < 0.6:
            self.resume_until = now + float(self.p.get('resume_buffer_s', 15.0))
        self._prev_idle = idle
        in_resume = now < self.resume_until
        in_dwell = now < self.dwell_until

        if intent != 'auto':
            want = INTENT_TIER[intent]
            if want != self.tier:
                self.set_tier(want, '手动指定（意图=%s）' % intent, now)
                return self._out(now, snap)
            # 锁定档仍受温度保护约束，但不再自动漂移
            return self._out(now, snap)

        hot_rising = self._temp_trend_hot(temp, cpu, now)
        cap_perf_allowed = on_ac or bool(self.p.get('allow_perf_on_battery', True))

        # ---- 升档 ----
        if _idx(self.tier) < _idx('perf') and cap_perf_allowed:
            if boost_app:
                self._try('perf', '性能应用前台（%s）' % fg, now)
                return self._out(now, snap)
            if hot_rising:
                self._try('perf', '温度趋势预判（%s°C 快速上冲）' % temp, now)
                return self._out(now, snap)
            enter_perf = (cpu >= float(self.p['cpu_perf']) or gpu >= float(self.p['gpu_perf']))
            if self._ok('perf_enter', enter_perf, now, float(self.p['perf_hold_s'])):
                self._try('perf', '持续高负载 CPU%s%%/GPU%s%%' % (round(cpu), round(gpu)), now)
                return self._out(now, snap)
        else:
            self._hold.pop('perf_enter', None)

        if _idx(self.tier) < _idx('mid'):
            enter_mid = (cpu >= float(self.p['cpu_mid']) or gpu >= float(self.p['gpu_mid']))
            if self._ok('mid_enter', enter_mid, now, float(self.p['mid_hold_s'])):
                self._try('mid', '中等负载 CPU%s%%/GPU%s%%' % (round(cpu), round(gpu)), now)
                return self._out(now, snap)
        else:
            self._hold.pop('mid_enter', None)

        # ---- 降档 ----
        if self.tier == 'perf':
            low = cpu < float(self.p['cpu_perf_exit']) and gpu < float(self.p['gpu_perf_exit'])
            soft = cpu < float(self.p['cpu_perf_soft_exit'])
            need_hold = float(self.p['perf_exit_hold_s'])
            cond = low
            if in_dwell:
                need_hold = float(self.p.get('dwell_hold_s', 45.0))
                cond = low and idle >= float(self.p.get('dwell_idle_s', 30.0))
            if self._ok('perf_exit', cond, now, need_hold):
                self._try('mid', '性能档退出（%s）' % ('驻留期宽松退出' if in_dwell else '低负载'),
                          now)
                return self._out(now, snap)
            if not in_dwell and self._ok('perf_soft', soft, now, float(self.p['perf_soft_hold_s'])):
                self._try('mid', '性能档软退出（长时间中等负载，不给满）', now)
                return self._out(now, snap)

        elif self.tier == 'mid':
            low = cpu < float(self.p['cpu_bal_exit']) and gpu < float(self.p['gpu_bal_exit'])
            if self._ok('mid_exit', low and not in_resume, now, float(self.p['bal_exit_hold_s'])):
                self._try('bal', '负载回落', now)
                return self._out(now, snap)

        elif self.tier == 'bal':
            factor = float(self.p.get('eco_light_idle_factor', 0.5)) if light else 1.0
            low = cpu < float(self.p['cpu_eco']) and gpu < float(self.p.get('gpu_eco', 5))
            if (self._ok('eco_enter', low, now, float(self.p['eco_hold_s']) * factor)
                    and idle >= float(self.p['eco_idle_s']) * factor):
                self._try('eco', '空闲且低负载（%ss 空闲）' % round(idle), now)
                return self._out(now, snap)

        else:  # eco
            if cpu >= float(self.p['cpu_eco']) or gpu >= float(self.p.get('gpu_eco', 5)):
                if self._ok('eco_exit', True, now, 5.0):
                    self._try('bal', '有负载，离开省电档', now)
                    return self._out(now, snap)
            else:
                self._hold.pop('eco_exit', None)
            if in_resume:
                self._try('mid', '亮屏缓冲：先给流畅档', now)
                return self._out(now, snap)

        return self._out(now, snap)

    def _out(self, now, snap):
        tier = self.tier
        if self.throttle and _idx(tier) > _idx('bal'):
            tier = 'bal'
        return {'tier': self.tier, 'effective': tier, 'reason': self.reason,
                'throttle': self.throttle, 'dwell_left': max(0.0, self.dwell_until - now),
                'resume_left': max(0.0, self.resume_until - now),
                'pending': self.pending, 'intent_lock': False}
