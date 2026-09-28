"""电源计划执行层：powercfg 写入 + 注册表回读校验。

已在本机验证：setacvalueindex / setdcvalueindex / setactive 都不需要管理员；
隐藏的电源属性（散热方式、性能提升模式、EPP）同样可写。
校验一律走注册表回读，不解析 powercfg 的输出文本（语言相关，不可靠）。
"""
import ctypes
import re
import subprocess
import time
import winreg

from app.config import SCHEMES

SUB_PROCESSOR = '54533251-82be-4824-96c1-47b60b740d00'
MIN_STATE = '893dee8e-2bef-41e0-89c6-b55d0929964c'
MAX_STATE = 'bc5038f7-23e0-4960-96da-33abaf5935ec'
COOL_POLICY = '94d3a615-a899-4ac5-ae2b-e4d8f634367f'
BOOST_MODE = 'be337238-0d82-4146-a960-4f3749d470c7'
EPP = '36687f9e-e3a5-4dbf-b1dc-15eb381c6863'

SETTINGS = {'min': MIN_STATE, 'max': MAX_STATE, 'cool': COOL_POLICY,
            'boost': BOOST_MODE, 'epp': EPP}
PS_ROOT = r'SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes'
NO_WINDOW = 0x08000000
GUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)


def _run(args, timeout=15):
    res = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    text = ''
    for stream in (res.stdout, res.stderr):
        if stream:
            text += stream.decode('gbk', 'replace')
    return res.returncode, text


def active_scheme():
    rc, out = _run(['powercfg', '/getactivescheme'])
    m = GUID_RE.search(out)
    return m.group(0).lower() if m else None


def list_schemes():
    rc, out = _run(['powercfg', '/list'])
    found = []
    for guid in GUID_RE.findall(out):
        g = guid.lower()
        if g in SCHEMES.values() and g not in found:
            found.append(g)
    return found


def read_setting(scheme, setting_guid, dc=False):
    """回读当前生效值；找不到返回 None。"""
    sub = r'%s\%s\%s\%s' % (PS_ROOT, scheme, SUB_PROCESSOR, setting_guid)
    value = 'DCSettingIndex' if dc else 'ACSettingIndex'
    for hive_root in (winreg.HKEY_LOCAL_MACHINE,):
        try:
            with winreg.OpenKey(hive_root, sub) as key:
                data, _ = winreg.QueryValueEx(key, value)
                return int(data)
        except OSError:
            pass
    return None


def set_setting(scheme, setting_guid, num, dc=False):
    flag = '/setdcvalueindex' if dc else '/setacvalueindex'
    rc, _ = _run(['powercfg', flag, scheme, SUB_PROCESSOR, setting_guid, str(int(num))])
    return rc == 0


class PowerExecutor:
    """把一个档位（scheme + 属性表）落到系统，并记住改动前的原始值。"""

    def __init__(self, log):
        self.log = log
        self.epp_supported = None
        self.baseline = {}          # {scheme: {key: ac/dc 原值}}
        self.applied = None
        self.last_apply_ts = 0.0
        self.last_result = None

    def probe(self):
        scheme = active_scheme() or SCHEMES['balanced']
        self.epp_supported = read_setting(scheme, EPP) is not None
        return {'active_scheme': scheme, 'epp_supported': self.epp_supported}

    def capture_baseline(self, schemes):
        for name in schemes:
            guid = SCHEMES.get(name)
            if not guid or guid in self.baseline:
                continue
            snap = {}
            for key, sg in SETTINGS.items():
                snap[key] = read_setting(guid, sg)
                snap[key + '_dc'] = read_setting(guid, sg, dc=True)
            self.baseline[guid] = snap
        return self.baseline

    def apply(self, tier_name, profile):
        """profile: {scheme, min_ac, max_ac, min_dc, max_dc, cool, boost, epp}"""
        guid = SCHEMES.get(profile.get('scheme'), SCHEMES['balanced'])
        self.capture_baseline([profile.get('scheme')])
        p = dict(profile)
        # 档位表用的是 min_ac/max_ac（交直流分开写），执行层统一成 min/max。
        # 这里曾经漏了映射，导致交流侧的频率上下限一次都没真正写进系统。
        if p.get('min') is None:
            p['min'] = p.get('min_ac')
        if p.get('max') is None:
            p['max'] = p.get('max_ac')
        plan = []
        for key in ('min', 'max', 'cool', 'boost', 'epp'):
            plan.append((key, p.get(key), False))
            plan.append((key + '_dc', p.get(key + '_dc', p.get(key)), True))
        writes, failed = [], []
        for key, value, is_dc in plan:
            if value is None:
                continue
            sg = SETTINGS['epp' if key.startswith('epp') else key.replace('_dc', '')]
            if sg == EPP:
                if self.epp_supported is None:
                    self.probe()
                if not self.epp_supported:
                    continue
            cur = read_setting(guid, sg, dc=is_dc)
            if cur == int(value):
                continue
            if set_setting(guid, sg, value, dc=is_dc):
                writes.append('%s=%s' % (key, value))
            else:
                failed.append(key)
        changed_scheme = active_scheme() != guid
        if changed_scheme:
            if not _run(['powercfg', '/setactive', guid])[0] == 0:
                failed.append('scheme')
        readback = {k: read_setting(guid, SETTINGS[k]) for k in ('min', 'max', 'cool', 'boost', 'epp')}
        readback['min_dc'] = read_setting(guid, MIN_STATE, dc=True)
        readback['max_dc'] = read_setting(guid, MAX_STATE, dc=True)
        self.applied = {'tier': tier_name, 'scheme': guid, 'writes': writes,
                        'failed': failed, 'readback': readback,
                        'scheme_switched': changed_scheme, 'ts': time.time()}
        self.last_apply_ts = time.time()
        self.last_result = self.applied
        if writes or changed_scheme:
            self.log.info('[执行] %s 方案=%s 切换方案=%s 写入=%s 回读=%s%s' % (
                tier_name, guid[:8], changed_scheme, ','.join(writes) or '-',
                readback, ' 失败:' + ','.join(failed) if failed else ''))
        return self.applied

    def restore(self, tiers):
        """退出时把改过的属性写回原始值，并恢复原方案。"""
        ok = True
        for guid, snap in self.baseline.items():
            for key, sg in SETTINGS.items():
                for suffix, is_dc in (('', False), ('_dc', True)):
                    want = snap.get(key + suffix)
                    if want is None:
                        continue
                    if read_setting(guid, sg, dc=is_dc) != want:
                        ok = set_setting(guid, sg, want, dc=is_dc) and ok
        self.baseline = {}
        self.log.info('[还原] 电源设置已%s' % ('恢复' if ok else '部分失败'))
        return ok

    def external_scheme(self):
        return active_scheme()
