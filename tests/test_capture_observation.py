# -*- coding: utf-8 -*-
"""录制只隔离 ingest 的自动键盘刷新；fake 时钟/通道，不连接 broker 或硬件。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.act.channels import mqtt_gcu
from app.act.channels.base import CAP_FAN_CURVE, CAP_RGB_WRITE


class FakeCfg:
    def get(self, *keys, default=None):
        if keys == ('hardware', 'mqtt'):
            return {'enabled': False}
        if keys == ('hardware', 'ec', 'allow_write'):
            return True
        return default


class CaptureObservationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='umi-observation-test-')
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.object(mqtt_gcu, 'CAPTURE_DIR', tmp.name))
        self.enterContext(patch.object(mqtt_gcu, 'MqttClient',
                                       side_effect=AssertionError('no broker in tests')))
        self.enterContext(patch.object(mqtt_gcu.socket, 'create_connection',
                                       side_effect=AssertionError('no network in tests')))
        self.clock = self.enterContext(patch.object(mqtt_gcu.time, 'time', return_value=10000.0))
        self.enterContext(patch.object(mqtt_gcu.time, 'strftime', return_value='20261021-120000'))
        actions = {'topics': {'keyboard_ctrl': 'Keyboard/Ctrl'}, 'actions': {
            'KB_POWER_' + state: {'topic': 'keyboard_ctrl', 'field': 'function',
                                 'value': 'SetPower', 'args': {'powerstatus': value},
                                 'cap': CAP_RGB_WRITE}
            for state, value in (('ON', 1), ('OFF', 0))}}
        with patch.object(mqtt_gcu.MqttChannel, '_load_actions', return_value=actions):
            self.ch = mqtt_gcu.MqttChannel(FakeCfg(), Mock())
        self.addCleanup(self.ch.stop_capture)
        self.ch.alive = True  # Only the in-memory queue; no session is started.
        self.ch.caps[CAP_RGB_WRITE] = 'verified'
        self.ch.send_action = Mock(wraps=self.ch.send_action)
        self.ingest('Keyboard/Status', {'powerStatus': 'On'})

    def ingest(self, topic='Keyboard/Ctrl', payload=None):
        if payload is None:
            payload = {'function': 'SetEffectALL', 'effect': 'Wave'}
        self.ch._ingest(topic, json.dumps(payload).encode('utf-8'))

    def records(self, path):
        with open(path, encoding='utf-8') as stream:
            return [json.loads(line) for line in stream]

    def assert_status(self, status, active):
        self.assertTrue({'active', 'path', 'count', 'left_s'} <= status.keys())
        self.assertIs(status['active'], active)
        self.assertIs(status['auto_keyboard_refresh_suppressed'], active)
        self.assertEqual(status['suppression_scope'], 'ingest_external_set_effect_all')
        self.assertIs(status['normal_service_actions_continue'], True)

    def assert_refresh(self, action='KB_POWER_ON'):
        self.ch.send_action.assert_called_once_with(action, note='刷新键盘状态（灯效回读）')
        messages = [(topic, json.loads(raw)) for topic, raw in self.ch._outq.queue]
        self.assertIn(('Keyboard/Ctrl', {'function': 'SetPower',
                                       'powerstatus': int(action == 'KB_POWER_ON')}), messages)

    def test_inactive_refreshes_on_and_off(self):
        for power in ('On', 'Off'):
            with self.subTest(power=power):
                self.ch.send_action.reset_mock()
                self.ingest('Keyboard/Status', {'powerStatus': power})
                self.ingest()
                self.assert_refresh('KB_POWER_' + power.upper())
        self.assert_status(self.ch.capture_status(), False)

    def test_default_capture_suppresses_send_and_keeps_payload(self):
        status = self.ch.start_capture()
        for power in ('On', 'Off'):
            self.ingest('Keyboard/Status', {'powerStatus': power})
            self.ingest()
        self.ch.send_action.assert_not_called()
        self.assertTrue(self.ch._outq.empty())
        self.assertEqual(self.ch.payloads()['Keyboard/Ctrl']['data']['effect'], 'Wave')
        self.assertEqual(self.ch.payloads()['Keyboard/Status']['data']['powerStatus'], 'Off')
        self.assert_status(status, True)
        self.assert_status(self.ch.capture_status(), True)
        self.assertEqual(self.ch.capture_status()['count'], 4)

    def test_stop_releases_guard_without_replaying_observation(self):
        self.ch.start_capture()
        self.ingest()
        status = self.ch.stop_capture()
        self.ch.send_action.assert_not_called()
        self.assert_status(status, False)
        self.assertEqual(status['count'], 1)
        self.assertEqual(len(self.records(status['path'])), 1)
        self.ingest()
        self.assert_refresh()
        self.assert_status(self.ch.stop_capture(), False)

    def test_timeout_boundary_is_recorded_and_does_not_send(self):
        for delay in (600.0, 601.0):
            with self.subTest(delay=delay):
                self.clock.return_value = 10000.0
                self.ch.send_action.reset_mock()
                status = self.ch.start_capture()
                self.clock.return_value += delay
                self.ingest()
                self.ch.send_action.assert_not_called()
                self.assert_status(self.ch.capture_status(), False)
                self.assertEqual(self.ch.capture_status()['count'], 1)
                self.assertEqual(self.records(status['path'])[0]['payload']['effect'], 'Wave')
                self.ingest()
                self.assert_refresh()

    def test_expiry_during_write_preserves_message_guard(self):
        status = self.ch.start_capture()
        stream = self.ch._cap_fh
        wrapped = Mock(wraps=stream)

        def write_then_expire(text):
            result = stream.write(text)
            self.clock.return_value = 10600.0
            return result

        wrapped.write.side_effect = write_then_expire
        self.ch._cap_fh = wrapped
        self.ingest()
        self.ch.send_action.assert_not_called()
        self.assertIsNone(self.ch._cap_fh)
        self.assertEqual(len(self.records(status['path'])), 1)
        self.ingest()
        self.assert_refresh()

    def test_status_poll_expires_without_any_incoming_message(self):
        self.ch.start_capture()
        self.clock.return_value = 10600.0
        self.assert_status(self.ch.capture_status(), False)
        self.assertIsNone(self.ch._cap_fh)
        self.assert_status(self.ch.detail['capture'], False)
        self.ingest()
        self.assert_refresh()

    def test_write_failure_releases_guard_but_does_not_send_for_failed_row(self):
        self.ch.start_capture()
        stream = self.ch._cap_fh
        wrapped = Mock(wraps=stream)
        wrapped.write.side_effect = OSError('fake disk full')
        self.ch._cap_fh = wrapped
        self.ingest()
        self.ch.send_action.assert_not_called()
        self.assertTrue(self.ch._outq.empty())
        self.assertTrue(stream.closed)
        self.assert_status(self.ch.capture_status(), False)
        self.assertIn('fake disk full', self.ch.capture_status()['error'])
        self.assertEqual(self.ch.capture_status()['count'], 0)
        self.ingest()
        self.assert_refresh()
        self.assert_status(self.ch.start_capture(), True)
        self.assertNotIn('error', self.ch.capture_status())

    def test_open_failure_after_restart_releases_guard(self):
        self.ch.start_capture()
        stream = self.ch._cap_fh
        with patch.object(mqtt_gcu, 'open', side_effect=OSError('fake open failure'), create=True):
            status = self.ch.start_capture()
        self.assertTrue(stream.closed)
        self.assert_status(status, False)
        self.assertIn('error', status)
        self.assert_status(self.ch.detail['capture'], False)
        self.ingest()
        self.assert_refresh()

    def test_close_failure_still_releases_guard(self):
        self.ch.start_capture()
        stream = self.ch._cap_fh
        wrapped = Mock(wraps=stream)

        def close_then_fail():
            stream.close()
            raise OSError('fake close failure')

        wrapped.close.side_effect = close_then_fail
        self.ch._cap_fh = wrapped
        self.assert_status(self.ch.stop_capture(), False)
        self.assertIsNone(self.ch._cap_fh)
        self.ingest()
        self.assert_refresh()

    def test_same_second_starts_are_unique_and_do_not_truncate(self):
        first = self.ch.start_capture()
        self.ingest('Fan/Control', {'Action': 'FIRST'})
        with open(first['path'], 'rb') as stream:
            before = stream.read()
        second = self.ch.start_capture()
        self.assertNotEqual(first['path'], second['path'])
        self.ingest('Fan/Control', {'Action': 'SECOND'})
        self.ch.stop_capture()
        with open(first['path'], 'rb') as stream:
            self.assertEqual(stream.read(), before)
        self.assertEqual(self.records(second['path'])[0]['payload']['Action'], 'SECOND')

    def test_forced_name_collision_never_overwrites_existing_file(self):
        with patch.object(mqtt_gcu.uuid, 'uuid4', return_value=Mock(hex='fixed')):
            first = self.ch.start_capture()
            self.ingest('Fan/Control', {'Action': 'KEEP'})
            self.ch.stop_capture()
            with open(first['path'], 'rb') as stream:
                before = stream.read()
            failed = self.ch.start_capture()
        self.assert_status(failed, False)
        self.assertIn('error', failed)
        with open(first['path'], 'rb') as stream:
            self.assertEqual(stream.read(), before)
        self.ingest()
        self.assert_refresh()

    def test_capture_uses_exclusive_create(self):
        modes = []
        real_open = open

        def record_open(path, mode, **kwargs):
            modes.append(mode)
            return real_open(path, mode, **kwargs)

        with patch.object(mqtt_gcu, 'open', side_effect=record_open, create=True):
            self.assertTrue(self.ch.start_capture()['active'])
        self.assertEqual(modes, ['x'])

    def test_records_are_observed_with_unknown_publisher(self):
        status = self.ch.start_capture()
        for topic in mqtt_gcu.SUBSCRIBE_TOPICS:
            self.ingest(topic, {'MqttID': 'CC', 'Action': 'UNATTRIBUTED'})
        self.ch.stop_capture()
        rows = self.records(status['path'])
        self.assertEqual(len(rows), len(mqtt_gcu.SUBSCRIBE_TOPICS))
        for row in rows:
            self.assertTrue({'ts', 'topic', 'payload'} <= row.keys())
            self.assertEqual(row['source'], 'observed')
            self.assertEqual(row['publisher'], 'unknown')
            self.assertEqual(row['payload']['MqttID'], 'CC')

    def test_normal_status_capabilities_callbacks_and_actions_continue(self):
        self.ch.start_capture()
        self.ingest('Fan/Table', {'Name': 'FAKE', 'CPU': [{'Duty': 30}] * 16})
        self.assertEqual(self.ch.caps[CAP_FAN_CURVE], 'verified')
        self.ingest('Fan/Status', {'CPU_PL1Minimum': '10', 'CPU_PL1Maximum': '120'})
        self.assertEqual(self.ch.detail['pl_bounds']['pl1_max'], 120)
        callback = Mock()
        self.ch.set_mode_callback(callback)
        self.ingest('Tray/Status', {'OperatingMode': '0'})
        self.ingest('Tray/Status', {'OperatingMode': '2'})
        callback.assert_called_once_with('turbo', {'OperatingMode': '2'})
        self.ch.tick()
        asks = [(t, json.loads(p)) for t, p in self.ch._outq.queue]
        self.assertEqual(asks, [(t, {'Action': 'GETSTATUS'}) for t in self.ch.ASK_TOPICS])
        self.assertTrue(self.ch.send_action('KB_POWER_OFF')[0])
        self.assertTrue(self.ch.request_fan_curve()[0])
        self.assert_status(self.ch.capture_status(), True)

    def test_invalid_start_does_not_release_existing_guard(self):
        self.ch.start_capture()
        for value in ('bad', 0, -1, 61, float('nan'), float('inf')):
            with self.subTest(value=value):
                status = self.ch.start_capture(value)
                self.assertIn('error', status)
                self.assert_status(status, True)
        self.ingest()
        self.ch.send_action.assert_not_called()

    def test_self_echo_and_non_effect_messages_still_skip_refresh(self):
        self.ch._last_effect_ts = self.clock.return_value
        self.ingest()
        self.clock.return_value += 3
        self.ingest(payload={'function': 'SetPower'})
        self.ch.send_action.assert_not_called()

    def test_start_cannot_interleave_between_capture_check_and_auto_send(self):
        checked, release, attempting_start = (threading.Event() for _ in range(3))
        original = self.ch._capture_write
        errors, started = [], []

        def pause_after_check(topic, data):
            result = original(topic, data)
            checked.set()
            if not release.wait(5):
                raise AssertionError('test did not release ingest')
            return result

        def run_ingest():
            try:
                self.ingest()
            except BaseException as exc:
                errors.append(exc)

        def run_start():
            attempting_start.set()
            try:
                started.append(self.ch.start_capture())
            except BaseException as exc:
                errors.append(exc)

        with patch.object(self.ch, '_capture_write', side_effect=pause_after_check):
            ingest_thread = threading.Thread(target=run_ingest, daemon=True)
            start_thread = threading.Thread(target=run_start, daemon=True)
            ingest_thread.start()
            try:
                self.assertTrue(checked.wait(5))
                # A nonblocking acquire tests the lock invariant without timing sleeps.
                acquired = self.ch._cap_lock.acquire(blocking=False)
                if acquired:
                    self.ch._cap_lock.release()
                self.assertFalse(acquired, 'start could slip between decision and send')
                start_thread.start()
                self.assertTrue(attempting_start.wait(5))
                self.assertEqual(started, [])
            finally:
                release.set()
                ingest_thread.join(5)
                if start_thread.ident is not None:
                    start_thread.join(5)
        self.assertFalse(ingest_thread.is_alive())
        self.assertFalse(start_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(started), 1)
        self.assert_refresh()  # Linearized before start, not during observation.
        self.ch.send_action.reset_mock()
        self.ingest()
        self.ch.send_action.assert_not_called()
        self.assert_status(self.ch.capture_status(), True)

    def test_concurrent_stop_timeout_and_failure_keep_per_message_guard(self):
        for ending in ('stop', 'timeout', 'io_failure'):
            with self.subTest(ending=ending):
                self.clock.return_value = 10000.0
                self.ch.send_action.reset_mock()
                self.ch.start_capture()
                if ending == 'timeout':
                    self.clock.return_value = 10600.0
                elif ending == 'io_failure':
                    wrapped = Mock(wraps=self.ch._cap_fh)
                    wrapped.write.side_effect = OSError('fake concurrent failure')
                    self.ch._cap_fh = wrapped
                checked, release, attempted = (threading.Event() for _ in range(3))
                original = self.ch._capture_write
                errors, results = [], []
                operation = {'stop': self.ch.stop_capture,
                             'timeout': self.ch.capture_status,
                             'io_failure': self.ch.start_capture}[ending]

                def pause_after_write(topic, data):
                    observed = original(topic, data)
                    checked.set()
                    if not release.wait(5):
                        raise AssertionError('test did not release observation')
                    return observed

                def observe():
                    try:
                        self.ingest()
                    except BaseException as exc:
                        errors.append(exc)

                def change_capture():
                    attempted.set()
                    try:
                        results.append(operation())
                    except BaseException as exc:
                        errors.append(exc)

                with patch.object(self.ch, '_capture_write', side_effect=pause_after_write):
                    observer = threading.Thread(target=observe, daemon=True)
                    changer = threading.Thread(target=change_capture, daemon=True)
                    observer.start()
                    try:
                        self.assertTrue(checked.wait(5))
                        acquired = self.ch._cap_lock.acquire(blocking=False)
                        if acquired:
                            self.ch._cap_lock.release()
                        self.assertFalse(acquired)
                        changer.start()
                        self.assertTrue(attempted.wait(5))
                        self.assertEqual(results, [])
                    finally:
                        release.set()
                        observer.join(5)
                        if changer.ident is not None:
                            changer.join(5)
                self.assertFalse(observer.is_alive())
                self.assertFalse(changer.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 1)
                self.ch.send_action.assert_not_called()
                self.assert_status(results[0], ending == 'io_failure')
                if ending == 'io_failure':
                    self.ingest()
                    self.ch.send_action.assert_not_called()
                self.assert_status(self.ch.stop_capture(), False)
                self.ingest()
                self.assert_refresh()


if __name__ == '__main__':
    unittest.main(verbosity=2)
