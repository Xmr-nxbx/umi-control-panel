"""Real Config in temporary files, mocked OSD/HTTP; no service, local config or hardware."""
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config, DEFAULTS
from app.daemon import Daemon
from app.server.httpd import STATIC, make_handler


def saved_config(daemon):
    with open(daemon.cfg.path, encoding='utf-8') as stream:
        return json.load(stream)


class DesktopTestCase(unittest.TestCase):
    def setUp(self):
        # Fail closed if a regression tries to use machine paths or Config.load().
        for target in ('app.config.data_path', 'app.config.Config.load'):
            guard = patch(target, side_effect=AssertionError('real config access forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_count = 0

    def fake_daemon(self, enabled=True, available=True):
        self.config_count += 1
        cfg = Config({'tray': {'osd': enabled}},
                     path=os.path.join(self.tmp.name, '%d.json' % self.config_count))
        cfg.save()
        cfg.save = Mock(wraps=cfg.save)
        return fake_daemon(cfg, available)


class FakeOsd:
    def __init__(self):
        self.calls = []
        self.accept = True

    def show(self, title, sub='', tier=None):
        self.calls.append(('show', title, sub) if tier is None else ('show', title, sub, tier))
        return self.accept

    def hide(self):
        self.calls.append(('hide',))


def fake_daemon(cfg, available=True):
    # Deliberately bypass __init__: it probes sensors and power capabilities.
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = cfg
    daemon.log = Mock()
    daemon.osd = FakeOsd() if available else None
    daemon._osd_lock = threading.RLock()
    # Any accidental call into a hardware/scheduler method fails the test.
    daemon.hw = SimpleNamespace()
    daemon.power = SimpleNamespace()
    daemon.sched = SimpleNamespace()
    return daemon


def request(daemon, path, body=None, method='POST', headers=None, raw=None):
    raw = raw if raw is not None else json.dumps(body).encode('utf-8')
    handler_type = make_handler(daemon, daemon.cfg, Mock(side_effect=AssertionError('shutdown')))
    handler = handler_type.__new__(handler_type)
    handler.path = path
    handler.headers = {'Host': '127.0.0.1:8747', 'Origin': 'http://127.0.0.1:8747',
                       'Content-Length': str(len(raw)), **(headers or {})}
    handler.rfile = io.BytesIO(raw)
    responses = []
    handler._send = lambda code, payload, *extra: responses.append((code, payload, extra))
    getattr(handler, 'do_' + method)()
    assert len(responses) == 1
    assert handler.rfile.tell() == (len(raw) if method == 'POST' else 0)
    return responses[0]


class DesktopPreferencesTests(DesktopTestCase):
    def test_preferences_are_boolean_copies_even_when_unavailable(self):
        for enabled in (False, True):
            for available in (False, True):
                d = self.fake_daemon(enabled, available)
                self.assertEqual(d.desktop_preferences(), {'osd': enabled, 'available': available})
                view = d.desktop_preferences()
                view['osd'] = not enabled
                self.assertIs(d.desktop_preferences()['osd'], enabled)

    def test_setter_strict_boolean_and_no_side_effect_on_invalid_input(self):
        d = self.fake_daemon()
        for value in (None, 0, 1, 'true', 'false', [], {}, 1.0):
            with self.subTest(value=value):
                self.assertFalse(d.set_osd_enabled(value)[0])
                self.assertEqual(d.cfg.data, saved_config(d))
        self.assertEqual(d.cfg.save.call_count, 0)
        self.assertEqual(d.osd.calls, [])

    def test_disable_reenable_preserves_intent_and_hardware_gates(self):
        d = self.fake_daemon()
        before = copy.deepcopy(d.cfg.data)
        d._osd('first')
        self.assertTrue(d.set_osd_enabled(False)[0])
        self.assertFalse(saved_config(d)['tray']['osd'])
        d._osd('suppressed')
        self.assertEqual(d.osd.calls, [('show', 'first', ''), ('hide',)])
        self.assertTrue(d.set_osd_enabled(True)[0])
        d._osd('again')
        self.assertEqual(d.osd.calls[-1], ('show', 'again', ''))
        self.assertEqual(d.cfg.data, before)
        self.assertEqual(saved_config(d), before)

    def test_failed_save_restores_both_directions_without_hide_or_show(self):
        for enabled in (False, True):
            for operation in ('tempfile.mkstemp', 'json.dump', 'os.replace'):
                with self.subTest(enabled=enabled, operation=operation):
                    d = self.fake_daemon(enabled)
                    with patch('app.config.' + operation, side_effect=OSError('disk write failed')):
                        self.assertFalse(d.set_osd_enabled(not enabled)[0])
                    self.assertEqual(d.cfg.data, saved_config(d))
                    self.assertIs(d.desktop_preferences()['osd'], enabled)
                    self.assertEqual(d.osd.calls, [])
                    self.assertFalse(any(name.endswith('.tmp') for name in os.listdir(self.tmp.name)))
                    d._osd('after failure')
                    self.assertEqual(len(d.osd.calls), int(enabled))
                    self.assertTrue(d.set_osd_enabled(not enabled)[0], 'failure must release locks')

    def test_disabled_setting_can_be_saved_without_osd(self):
        d = self.fake_daemon(available=False)
        self.assertTrue(d.set_osd_enabled(False)[0])
        self.assertTrue(d.set_osd_enabled(True)[0])
        self.assertEqual(d.desktop_preferences(), {'osd': True, 'available': False})
        ok, detail = d.preview_osd()
        self.assertFalse(ok)
        self.assertIn('初始化失败或桌面环境不可用', detail)

    def test_osd_errors_do_not_break_saved_preference_or_regular_action(self):
        d = self.fake_daemon()
        d.osd.hide = Mock(side_effect=RuntimeError('unavailable desktop'))
        ok, detail = d.set_osd_enabled(False)
        self.assertTrue(ok)
        self.assertIn('未能立即隐藏', detail)
        self.assertFalse(saved_config(d)['tray']['osd'])
        self.assertTrue(d.set_osd_enabled(True)[0])
        d.osd.show = Mock(side_effect=RuntimeError('unavailable desktop'))
        d._osd('ignored')
        self.assertFalse(d.preview_osd()[0])

    def test_preview_requires_enabled_available_and_honors_show_false(self):
        for enabled, available in ((False, True), (True, False), (False, False)):
            d = self.fake_daemon(enabled, available)
            self.assertFalse(d.preview_osd()[0])
            if d.osd:
                self.assertEqual(d.osd.calls, [])
        d = self.fake_daemon()
        before = copy.deepcopy(d.cfg.data)
        self.assertTrue(d.preview_osd()[0])
        _, title, sub, tier = d.osd.calls[-1]
        self.assertEqual(tier, 'perf')
        self.assertIn('预览', title)
        self.assertIn('不会切换性能或写入硬件', sub)
        d.osd.accept = False
        self.assertFalse(d.preview_osd()[0])
        self.assertEqual(d.cfg.data, before)
        self.assertEqual(d.cfg.save.call_count, 0)

    def test_regular_osd_rechecks_config_every_time(self):
        d = self.fake_daemon()
        d.cfg.set('tray', 'osd', False)
        d._osd('muted')
        self.assertEqual(d.osd.calls, [])
        d.cfg.set('tray', 'osd', True)
        d._osd('enabled')
        self.assertEqual(d.osd.calls, [('show', 'enabled', '')])

    def test_physical_key_still_changes_intent_when_osd_is_disabled(self):
        d = self.fake_daemon(False)
        d.hw.ec = SimpleNamespace(fan_flag_name=lambda value: value)
        d.set_intent = Mock(return_value=(True, 'ok'))
        for flag, intent in (('Turbo_Mode', 'turbo'), ('Normal_Mode', 'auto'),
                             ('User_Fan_HiMode', 'office')):
            d.on_fan_key(None, flag)
            d.set_intent.assert_called_with(intent, announce=False)
        self.assertEqual(d.osd.calls, [])

    def test_state_merges_current_preferences_without_mutating_previous_state(self):
        d = self.fake_daemon()
        d._state = {'ts': 123, 'meta': {'version': 'test'}, 'desktop': {'osd': True}}
        stored = copy.deepcopy(d._state)
        first = d.state()
        self.assertIs(first['meta']['desktop_preferences'], True)
        d.set_osd_enabled(False)
        second = d.state()
        self.assertIs(second['desktop']['osd'], False)
        self.assertIs(first['desktop']['osd'], True)
        self.assertEqual(d._state, stored)
        second['meta']['version'] = 'changed'
        self.assertEqual(d._state['meta']['version'], 'test')
        del d._state
        self.assertIs(d.state()['meta']['desktop_preferences'], True)

    def test_show_and_disable_are_serialized(self):
        d = self.fake_daemon()
        entered, release, disabling, disabled = (threading.Event() for _ in range(4))
        original_show = d.osd.show
        errors = []

        def show(*args):
            entered.set()
            if not release.wait(2):
                errors.append('show was not released')
            return original_show(*args)

        def disable():
            disabling.set()
            d.set_osd_enabled(False)
            disabled.set()

        d.osd.show = show
        showing = threading.Thread(target=d._osd, args=('in flight',), daemon=True)
        stopping = threading.Thread(target=disable, daemon=True)
        showing.start()
        try:
            self.assertTrue(entered.wait(2))
            acquired = d._osd_lock.acquire(blocking=False)
            if acquired:
                d._osd_lock.release()
            self.assertFalse(acquired, 'show must hold the same RLock as the setter')
            stopping.start()
            self.assertTrue(disabling.wait(2))
            self.assertFalse(disabled.is_set())
        finally:
            release.set()
            showing.join(2)
            if stopping.ident:
                stopping.join(2)
        self.assertFalse(errors)
        self.assertTrue(disabled.is_set())
        self.assertEqual(d.osd.calls, [('show', 'in flight', ''), ('hide',)])
        d._osd('after disable')
        self.assertEqual(len(d.osd.calls), 2)


class ObservedRLock:
    """Real reentrant lock; report a contender's failed nonblocking acquisition."""
    def __init__(self):
        self.lock = threading.RLock()
        self.attempted = threading.Event()
        self.blocked = False

    def __enter__(self):
        if threading.current_thread().name == 'config-contender':
            acquired = self.lock.acquire(blocking=False)
            self.blocked = not acquired
            self.attempted.set()
            if acquired:
                return self
        self.lock.acquire()
        return self

    def __exit__(self, *exc):
        self.lock.release()


class ConfigConcurrencyTests(DesktopTestCase):
    def run_contended(self, cfg, first, second, entered, release):
        errors, results = [], []
        cfg.lock = ObservedRLock()

        def run(action, contender=False):
            try:
                results.append(action())
            except BaseException as exc:
                errors.append(exc)
            finally:
                if contender:
                    cfg.lock.attempted.set()  # Also wakes the test on unlocked implementations.

        writer = threading.Thread(target=run, args=(first,), daemon=True)
        contender = threading.Thread(target=run, args=(second, True),
                                     name='config-contender', daemon=True)
        writer.start()
        try:
            self.assertTrue(entered.wait(3), 'first writer did not reach the controlled boundary')
            contender.start()
            self.assertTrue(cfg.lock.attempted.wait(3), 'contender did not attempt the config lock')
            blocked = cfg.lock.blocked
        finally:
            release.set()
            writer.join(3)
            if contender.ident:
                contender.join(3)
        self.assertFalse(writer.is_alive() or contender.is_alive(), 'config/OSD lock order deadlocked')
        self.assertFalse(errors, errors)
        self.assertTrue(blocked, 'competing mutation/save bypassed the shared config lock')
        return results

    def test_save_serializes_snapshot_through_replace_against_all_mutators(self):
        for stage in ('snapshot', 'replace'):
            for operation in ('set', 'set_profile', 'save'):
                with self.subTest(stage=stage, operation=operation):
                    d = self.fake_daemon()
                    cfg = d.cfg
                    entered, release = threading.Event(), threading.Event()
                    original = cfg._for_disk if stage == 'snapshot' else os.replace

                    def boundary(*args):
                        # snapshot is already detached here: locking only replace is too late.
                        snapshot = original(*args) if stage == 'snapshot' else None
                        if threading.current_thread().name != 'config-contender':
                            entered.set()
                            if not release.wait(3):
                                raise AssertionError('writer was not released')
                        return snapshot if stage == 'snapshot' else original(*args)

                    def competing():
                        if operation == 'set':
                            cfg.set('intent', 'office')
                        elif operation == 'set_profile':
                            cfg.set_profile('quiet')
                        cfg.save()

                    target = patch.object(cfg, '_for_disk', side_effect=boundary) if stage == 'snapshot' else patch(
                        'app.config.os.replace', side_effect=boundary)
                    with target:
                        self.run_contended(cfg, cfg.save, competing, entered, release)
                    disk = saved_config(d)
                    self.assertEqual(disk, cfg._for_disk())
                    if operation == 'set':
                        self.assertEqual(disk['intent'], 'office')
                    elif operation == 'set_profile':
                        self.assertEqual(disk['scheduler']['profile'], 'quiet')
                        self.assertEqual(disk['scheduler']['cpu_perf'], DEFAULTS['scheduler']['cpu_perf'])
                        self.assertEqual(cfg.get('scheduler', 'cpu_perf'), 70)

    def test_old_save_cannot_overwrite_successful_osd_transaction(self):
        d = self.fake_daemon()
        entered, release = threading.Event(), threading.Event()
        original = d.cfg._for_disk

        def snapshot():
            data = original()
            if threading.current_thread().name != 'config-contender':
                entered.set()
                if not release.wait(3):
                    raise AssertionError('snapshot was not released')
            return data

        with patch.object(d.cfg, '_for_disk', side_effect=snapshot):
            results = self.run_contended(d.cfg, d.cfg.save, lambda: d.set_osd_enabled(False), entered, release)
        self.assertIn((True, '已保存：Umi 屏幕提示关闭'), results)
        self.assertFalse(saved_config(d)['tray']['osd'])
        self.assertEqual(saved_config(d), d.cfg._for_disk())

    def test_osd_transaction_holds_lock_outside_save_including_rollback(self):
        for stage in ('old-value', 'mutated', 'rollback'):
            for enabled in (False, True):
                with self.subTest(stage=stage, enabled=enabled):
                    d = self.fake_daemon(enabled)
                    entered, release = threading.Event(), threading.Event()
                    original_get, original_set, original_replace = d.cfg.get, d.cfg.set, os.replace

                    def pause():
                        entered.set()
                        if not release.wait(3):
                            raise AssertionError('transaction was not released')

                    def get(*keys, **kwargs):
                        value = original_get(*keys, **kwargs)
                        if stage == 'old-value' and keys == ('tray', 'osd'):
                            pause()  # Old value captured, set() has not run yet.
                        return value

                    def set_value(*args):
                        if stage == 'rollback' and args == ('tray', 'osd', enabled):
                            pause()  # save() has already failed and released its own lock.
                        original_set(*args)
                        if stage == 'mutated' and args == ('tray', 'osd', not enabled):
                            pause()  # set() returned, save() has not acquired its lock yet.

                    def replace(*args):
                        if threading.current_thread().name != 'config-contender':
                            raise OSError('write failed')
                        return original_replace(*args)

                    def competing():
                        d.cfg.set('intent', 'office')
                        d.cfg.save()

                    with patch.object(d.cfg, 'get', side_effect=get), patch.object(
                            d.cfg, 'set', side_effect=set_value), patch('app.config.os.replace', side_effect=replace):
                        self.run_contended(d.cfg, lambda: d.set_osd_enabled(not enabled),
                                           competing, entered, release)
                    disk = saved_config(d)
                    self.assertIs(disk['tray']['osd'], enabled)
                    self.assertEqual(disk['intent'], 'office')
                    self.assertEqual(disk, d.cfg._for_disk())
                    self.assertEqual(d.osd.calls, [])

    def test_failed_osd_transaction_cannot_be_committed_by_other_save_paths(self):
        for enabled in (False, True):
            for operation in ('intent', 'profile', 'save'):
                with self.subTest(enabled=enabled, operation=operation):
                    d = self.fake_daemon(enabled)
                    d.sched = SimpleNamespace(p=None)
                    entered, release = threading.Event(), threading.Event()
                    original = os.replace

                    def replace(source, destination):
                        if threading.current_thread().name != 'config-contender':
                            entered.set()
                            if not release.wait(3):
                                raise AssertionError('failed writer was not released')
                            raise OSError('simulated atomic replace failure')
                        return original(source, destination)

                    def competing():
                        if operation == 'intent':
                            return d.set_intent('auto')  # No _apply/power path; real OSD lock ordering.
                        if operation == 'profile':
                            return d.set_sched_profile('quiet')
                        return d.cfg.save()

                    with patch('app.config.os.replace', side_effect=replace):
                        results = self.run_contended(d.cfg, lambda: d.set_osd_enabled(not enabled),
                                                     competing, entered, release)
                    self.assertTrue(any(result and result[0] is False for result in results))
                    self.assertIs(saved_config(d)['tray']['osd'], enabled)
                    self.assertEqual(saved_config(d), d.cfg._for_disk())
                    self.assertNotIn(('hide',), d.osd.calls)
                    self.assertFalse(any(name.endswith('.tmp') for name in os.listdir(self.tmp.name)))
                    if operation == 'profile':
                        self.assertEqual(saved_config(d)['scheduler']['profile'], 'quiet')
                        self.assertEqual(d.sched.p['cpu_perf'], 70)


class DesktopHttpTests(DesktopTestCase):
    def test_get_and_post_preferences(self):
        d = self.fake_daemon()
        code, prefs, _ = request(d, '/api/desktop', method='GET')
        self.assertEqual((code, prefs), (200, {'osd': True, 'available': True}))
        code, result, _ = request(d, '/api/desktop', {'osd': False})
        self.assertEqual(code, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(result['desktop'], {'osd': False, 'available': True})
        self.assertEqual(d.osd.calls, [('hide',)])
        code, state, _ = request(d, '/api/state', method='GET')
        self.assertEqual(code, 200)
        self.assertIs(state['meta']['desktop_preferences'], True)
        self.assertFalse(state['desktop']['osd'])

    def test_rejects_missing_extra_nonboolean_and_malformed_bodies(self):
        d = self.fake_daemon()
        bodies = [{}, {'osd': 1}, {'osd': 0}, {'osd': 'false'}, {'osd': None},
                  {'osd': []}, {'osd': True, 'intent': 'turbo'}, [], None, True]
        for body in bodies:
            self.assertEqual(request(d, '/api/desktop', body)[0], 400, body)
        for raw in (b'', b'{invalid', b'\xff', b' ' * 65537):
            self.assertEqual(request(d, '/api/desktop', raw=raw)[0], 400)
        self.assertEqual(d.cfg.save.call_count, 0)
        self.assertEqual(d.osd.calls, [])

    def test_persistence_and_preview_failures_return_conflict(self):
        d = self.fake_daemon()
        with patch('app.config.os.replace', side_effect=OSError('replace failed')):
            code, result, _ = request(d, '/api/desktop', {'osd': False})
        self.assertEqual(code, 409)
        self.assertFalse(result['ok'])
        self.assertTrue(result['desktop']['osd'])
        d.osd.accept = False
        self.assertEqual(request(d, '/api/desktop/preview', {})[0], 409)
        d.osd.accept = True
        self.assertEqual(request(d, '/api/desktop/preview', {})[0], 200)
        d.cfg.set('tray', 'osd', False)
        self.assertEqual(request(d, '/api/desktop/preview', {})[0], 409)
        self.assertEqual(d.cfg.save.call_count, 1)

    def test_origin_and_host_gates_cover_both_posts(self):
        d = self.fake_daemon()
        for path in ('/api/desktop', '/api/desktop/preview'):
            for headers in ({'Origin': 'https://example.test'},
                            {'Origin': '', 'Referer': 'https://example.test/'},
                            {'Host': 'evil.test:8747', 'Origin': 'http://evil.test:8747'}):
                self.assertEqual(request(d, path, {'osd': False}, headers=headers)[0], 403)
        self.assertEqual(d.cfg.save.call_count, 0)
        self.assertEqual(d.osd.calls, [])

    def test_icon_static_whitelist_and_mime_types(self):
        d = self.fake_daemon()
        for path, expected in (('/umi.svg', ('umi.svg', 'image/svg+xml')),
                               ('/favicon.ico', ('umi.ico', 'image/x-icon')),
                               ('/umi.ico', ('umi.ico', 'image/x-icon'))):
            self.assertEqual(STATIC[path], expected)
            with patch('builtins.open', mock_open(read_data=b'asset')) as opened:
                code, payload, extra = request(d, path, method='GET')
            self.assertEqual((code, payload, extra), (200, b'asset', (expected[1],)))
            self.assertTrue(opened.call_args.args[0].endswith('/' + expected[0]))
        self.assertEqual(request(d, '/../../data/config.json', method='GET')[0], 404)


if __name__ == '__main__':
    unittest.main()
