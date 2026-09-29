"""配置读写：默认值 + 深合并 + 原子落盘。

配置损坏时不致命：任何解析错误都回退到默认值并保留坏文件，
避免重蹈 open-revo「一次异常后永久起不来」的覆辙。
"""
import json
import os
import tempfile

from app.paths import data_path

CONFIG_NAME = 'config.json'

DEFAULTS = {
    # 档位调校版本：改 tiers 默认值时把它 +1，老配置会自动换用新档位（见 _migrate）
    'tier_tuning': 4,
    'server': {
        'bind': '127.0.0.1',
        'port': 8747,
        'open_browser_on_start': True,
    },
    # intent: auto | office | balance | turbo —— 用户在面板上选择的意图
    'intent': 'auto',
    'tray': {
        'enabled': True,
    },
    'scheduler': {
        'enabled': True,
        'cpu_mid': 35, 'gpu_mid': 20, 'mid_hold_s': 2.0,
        'cpu_perf': 60, 'gpu_perf': 40, 'perf_hold_s': 2.5,
        'cpu_perf_exit': 35, 'gpu_perf_exit': 15, 'perf_exit_hold_s': 8.0,
        'cpu_perf_soft_exit': 55, 'perf_soft_hold_s': 40.0,
        'cpu_bal_exit': 20, 'gpu_bal_exit': 8, 'bal_exit_hold_s': 15.0,
        'cpu_eco': 15, 'gpu_eco': 5, 'eco_hold_s': 20.0, 'eco_idle_s': 90.0,
        'eco_light_idle_factor': 0.5,
        'temp_rise_deg': 4.0, 'temp_rise_window_s': 5.0,
        'temp_rise_min_c': 70.0, 'temp_rise_load_min': 12.0,
        'temp_limit_c': 95.0, 'temp_recover_c': 90.0,
        'dwell_s': 300.0,
        'dwell_idle_s': 30.0, 'dwell_hold_s': 45.0,
        'resume_buffer_s': 15.0,
        # 换挡防抖（秒）：本机实测过不防抖时性能/流畅互相切了 14 分钟，比不切还烦。
        #   min_down_dwell_s：降档之间至少隔这么久（降档要慢，避免刚闲下来就掉）；
        #   min_up_dwell_s ：刚从哪一档掉下来，这么久内不许原样爬回去。
        # 注意升档只对「爬回来路」设限，升到更高档永远放行 —— 游戏刚开、
        # 任务刚点火这些还是立刻给满，掐死的只有来回跳。
        'min_up_dwell_s': 20.0, 'min_down_dwell_s': 45.0, 'big_jump_levels': 2,
        'allow_perf_on_battery': True,
        'sleep_guard_idle_s': 60.0,
    },
    # 四档必须在「实际频率」上拉开差距，否则用户体感不到。本机 2026-09-29 实测结论：
    #   * 睿频开关（PERFBOOSTMODE）在这台机器上不管用：省电档写了 boost=0，
    #     满载仍跑到 4577 MHz，四档跑分成绩几乎一样（99～101 分）；
    #   * 最低处理器状态只影响空闲频率，满载时看不出区别；
    #   * 唯一真正能压住频率的是「最大处理器状态」：99% 就禁用睿频，
    #     85% ≈ 锁 1.96GHz（标称 2304 MHz）。
    # 所以省电档用 max 上限做主闸，其余档位靠最低频率/EPP 改善响应速度。
    # boost 取值：0=禁用 1=启用 2=激进 3=高效启用 4=高效激进 5=保证频率激进
    #
    # 2026-09-29 第二次调校（tier_tuning 4）：四档统一写在「平衡」这一个方案里。
    # 之前性能档单独用「高性能」方案，实测四档吞吐差异仍在噪声范围内，
    # 但方案 GUID 每切一次就顺带改掉硬盘睡眠/PCIe 省电这些无关设置，
    # 而且 Windows 会弹方案变更提示——只留下噪声，没有收益。
    'tiers': {
        'perf': {'scheme': 'balanced', 'min_ac': 100, 'max_ac': 100,
                 'min_dc': 60, 'max_dc': 100, 'cool': 0, 'boost': 2, 'epp': 0},
        'mid':  {'scheme': 'balanced', 'min_ac': 50, 'max_ac': 100,
                 'min_dc': 30, 'max_dc': 100, 'cool': 0, 'boost': 2, 'epp': 25},
        'bal':  {'scheme': 'balanced', 'min_ac': 20, 'max_ac': 100,
                 'min_dc': 15, 'max_dc': 100, 'cool': 0, 'boost': 1, 'epp': 50},
        'eco':  {'scheme': 'balanced', 'min_ac': 5, 'max_ac': 85,
                 'min_dc': 5, 'max_dc': 70, 'cool': 1, 'boost': 0, 'epp': 85},
    },
    'apps': {
        'performance': ['code.exe', 'chrome.exe', 'edge.exe', 'taskmgr.exe'],
        'light': ['wechat.exe', 'qq.exe', 'dingtalk.exe', 'cloudmusic.exe',
                  'spotify.exe', 'potplayer.exe'],
        'boost': ['cs2.exe', 'csgo.exe', 'valorant.exe', 'gta5.exe', 'eldenring.exe',
                  'wegame.exe', 'leagueoflegends.exe', 'overwatch.exe', 'apex.exe'],
    },
    'hardware': {
        # EC 直连（主通道）：只读默认开，写操作需要显式开启
        # allow_write 只放开「语义已确认」的写入（目前是风扇模式字节）；
        # 档位/PL 这类语义未确认的寄存器在代码层面硬拦，配置开了也不写。
        'ec': {'enabled': True, 'allow_write': False,
               # 风扇模式跟随调度档位：空闲安静、性能档加强散热。
               # 取值必须来自 OEM 枚举 MyFanCTLByteFlag，别的名字一律不下发。
               'fan_follow_tier': True, 'fan_cooldown_s': 10.0,
               'fan_map': {'eco': 'Normal_Mode', 'bal': 'Normal_Mode',
                           'mid': 'Normal_Mode', 'perf': 'Turbo_Mode'},
               # 实体「造物者模式」按键优先：观察到按键改了风扇字节后，
               # 这段时间内面板一律不自动跟随，免得把用户的自定义曲线覆盖掉。
               # 上一版没有这条，实测到按键改完 0 秒就被面板写回去。
               'respect_external_s': 900.0},
        # OEM GCUBridge MQTT（兜底通道）：服务活着才启用
        # 身份（clientId/username/password）不入库，放 data/mqtt_identity.json（已 gitignore）
        'mqtt': {'enabled': True, 'host': '127.0.0.1', 'port': 13688,
                 'client_id': '', 'username': '', 'password': ''},
        # 自适应时是否联动硬件档（perf/eco 边界）
        'sync_ec_mode': False,
        'ec_mode_map': {'perf': 'turbo', 'eco': 'office'},
    },
    'power': {
        'restore_on_exit': True,
        'watchdog_external_change': True,
    },
}

SCHEMES = {
    'balanced': '381b4222-f694-41f0-9685-ff5bb260df2e',
    'high_perf': '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c',
    'power_saver': 'a1841308-3541-4fab-bc81-f71556f20b4a',
}

SCHEME_LABELS = {'balanced': '平衡', 'high_perf': '高性能', 'power_saver': '节能'}


def _deep_merge(dst, src):
    """把已保存的配置盖到默认值上。

    标量必须覆盖，否则用户在面板上选的意图重启后就丢了（这个 bug 真实存在过）。
    """
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = json.loads(json.dumps(v))
    return dst


def _migrate(cfg):
    """档位参数是「调出来的」，老配置里存的旧值会把新调校整个盖掉。

    用 tier_tuning 版本号判定：版本落后（含从没存过这个键）就整块换成当前默认档位，
    用户自己改过的其它键（意图、阈值、白名单）一律保留。
    """
    if cfg.raw_tier_tuning != DEFAULTS['tier_tuning']:
        cfg.data['tiers'] = json.loads(json.dumps(DEFAULTS['tiers']))
        cfg.data['tier_tuning'] = DEFAULTS['tier_tuning']
    return cfg


def _strip_unknown(cfg):
    """丢掉默认值里没有的键，防止旧键堆积。"""
    def walk(keep, node):
        if not isinstance(keep, dict) or not isinstance(node, dict):
            return
        for k in list(node.keys()):
            if k not in keep:
                node.pop(k)
            else:
                walk(keep[k], node[k])
    walk(DEFAULTS, cfg)
    return cfg


def _apply_local_identity(cfg):
    """把本机私有的 broker 身份合并进来（只存在于 data/，不进仓库、不进 config.json）。"""
    path = data_path('mqtt_identity.json')
    if not os.path.exists(path):
        return cfg
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(raw, dict):
        node = cfg.data.setdefault('hardware', {}).setdefault('mqtt', {})
        for key in ('client_id', 'username', 'password'):
            if raw.get(key):
                node[key] = raw[key]
    return cfg


class Config:
    def __init__(self, data=None, path=None):
        self.data = _deep_merge(json.loads(json.dumps(DEFAULTS)), data or {})
        self.raw_tier_tuning = (data or {}).get('tier_tuning') if isinstance(data, dict) else None
        self.path = path
        self.bad_file = None

    # --- 访问 ---
    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, *path_and_value):
        *keys, value = path_and_value
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    def __getitem__(self, k):
        return self.data[k]

    # --- 持久化 ---
    def save(self):
        path = self.path or data_path(CONFIG_NAME)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.path = path

    @classmethod
    def load(cls):
        path = data_path(CONFIG_NAME)
        raw = None
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    raw = json.load(f)
            except Exception as exc:
                broken = path + '.bad'
                try:
                    os.replace(path, broken)
                except OSError:
                    broken = None
                return cls(path=path), str(exc), broken
        cfg = cls(raw if isinstance(raw, dict) else None, path=path)
        _strip_unknown(cfg.data)
        _migrate(cfg)
        return _apply_local_identity(cfg), None, None
