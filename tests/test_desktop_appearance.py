"""Offline artwork checks and mocked OSD lifecycle tests; never opens a window."""
import ctypes
import importlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.desktop import appearance
from tools.make_icons import SIZES, make_multi_ico


def decode_ico(data):
    reserved, kind, count = struct.unpack_from('<HHH', data)
    assert (reserved, kind) == (0, 1)
    images = []
    last_end = 6 + 16 * count
    for i in range(count):
        width, height, colors, reserved, planes, bits, length, offset = struct.unpack_from(
            '<BBBBHHII', data, 6 + 16 * i)
        width, height = width or 256, height or 256
        assert (colors, reserved, planes, bits) == (0, 0, 1, 32)
        assert offset == last_end
        assert offset + length <= len(data)
        header = struct.unpack_from('<IiiHHIIiiII', data, offset)
        assert header[:6] == (40, width, height * 2, 1, 32, 0)
        stride = ((width + 31) // 32) * 4
        assert length == 40 + width * height * 4 + stride * height
        assert header[6] == length - 40
        pixels = []
        for y in range(height):
            row = []
            for x in range(width):
                base = offset + 40 + ((height - 1 - y) * width + x) * 4
                b, g, r, a = data[base:base + 4]
                row.append((r, g, b, a))
                mask = data[offset + 40 + width * height * 4 + (height - 1 - y) * stride + x // 8]
                assert bool(mask & (0x80 >> (x % 8))) == (a == 0)
            pixels.append(row)
        images.append((width, height, pixels))
        last_end = offset + length
    assert last_end == len(data)
    return images


class AppearanceTests(unittest.TestCase):
    def test_tokens_and_text_contrast(self):
        self.assertEqual(set(appearance.PALETTE), {'bg', 'surface', 'border', 'text', 'muted', 'accent'})
        for value in appearance.PALETTE.values():
            self.assertRegex(value, r'^#[0-9A-Fa-f]{6}$')
        self.assertEqual(set(appearance.TIER_COLORS), {'eco', 'bal', 'mid', 'perf', 'unknown'})
        self.assertEqual(len(set(appearance.TIER_COLORS.values())), 5)
        for value in appearance.TIER_COLORS.values():
            self.assertEqual(len(value), 3)
            self.assertTrue(all(isinstance(c, int) and 0 <= c <= 255 for c in value))

        def luminance(value):
            rgb = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * k for v, k in zip(linear, (.2126, .7152, .0722)))

        for text in ('text', 'muted'):
            for background in ('bg', 'surface'):
                ratio = ((luminance(appearance.PALETTE[text]) + .05) /
                         (luminance(appearance.PALETTE[background]) + .05))
                self.assertGreaterEqual(ratio, 4.5)

    def test_all_sizes_have_alpha_and_valid_masks(self):
        for size in SIZES:
            with self.subTest(size=size):
                data = appearance.make_ico_bytes(size)
                self.assertIsInstance(data, bytes)
                [(width, height, pixels)] = decode_ico(data)
                self.assertEqual((width, height), (size, size))
                alphas = {pixel[3] for row in pixels for pixel in row}
                self.assertIn(0, alphas)
                self.assertIn(255, alphas)
                self.assertTrue(any(0 < a < 255 for a in alphas))
                self.assertEqual(pixels[0][0], (0, 0, 0, 0))

    def test_tiers_are_distinct_even_at_small_tray_sizes(self):
        for size in (16, 20, 24, 32):
            images = {tier: appearance.make_ico_bytes(size, tier) for tier in appearance.TIER_COLORS}
            self.assertEqual(len(set(images.values())), 5)
            silhouettes = []
            for tier, data in images.items():
                pixels = decode_ico(data)[0][2]
                color = appearance.TIER_COLORS[tier]
                silhouettes.append(tuple(pixel[:3] == color for row in pixels for pixel in row))
            self.assertEqual(len(set(silhouettes)), 5)

    def test_one_two_three_four_bars(self):
        samples = ((7, 24), (13, 22), (18, 19), (24, 16))
        for count, tier in enumerate(('eco', 'bal', 'mid', 'perf'), 1):
            pixels = decode_ico(appearance.make_ico_bytes(32, tier))[0][2]
            for i, (x, y) in enumerate(samples):
                self.assertEqual(pixels[y][x][:3] == appearance.TIER_COLORS[tier], i < count)
        self.assertEqual(appearance.TIER_BARS['unknown'], 0)

    def test_unknown_is_not_guessed(self):
        unknown = appearance.make_ico_bytes(tier='unknown')
        for tier in (None, '', 'auto', 'future-mode', 7):
            self.assertEqual(appearance.make_ico_bytes(tier=tier), unknown)
        self.assertNotEqual(unknown, appearance.make_ico_bytes(tier='bal'))
        self.assertNotEqual(unknown, appearance.make_ico_bytes(tier='perf'))
        self.assertEqual(appearance.make_ico_bytes(tier=' PERF '), appearance.make_ico_bytes(tier='perf'))

    def test_invalid_size_rejected(self):
        for size in (0, -1, 257, 32.0, '32', None, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                appearance.make_ico_bytes(size)

    def test_deterministic_regeneration(self):
        first = appearance.make_ico_bytes(20, 'mid')
        appearance._icon_bytes.cache_clear()
        self.assertEqual(first, appearance.make_ico_bytes(20, 'mid'))
        self.assertEqual(appearance.make_svg(), appearance.make_svg())

    def test_multi_resolution_and_matching_vector(self):
        data = make_multi_ico()
        images = decode_ico(data)
        self.assertEqual(tuple(w for w, _, _ in images), SIZES)
        svg = ET.fromstring(appearance.make_svg())
        self.assertEqual(svg.attrib['viewBox'], '0 0 32 32')
        self.assertEqual(svg.find('{http://www.w3.org/2000/svg}title').text, 'Umi Control Panel')
        self.assertEqual(len(svg) - 1, len(appearance._shapes('bal')))
        self.assertFalse(any(term in appearance.make_svg().lower() for term in ('oem', '<image', 'href=')))
        self.assertEqual((ROOT / 'app/web/umi.ico').read_bytes(), data)
        self.assertEqual((ROOT / 'app/web/umi.svg').read_text(encoding='utf-8'), appearance.make_svg())

    def test_load_icon_uses_memory_and_returns_owned_full_width_handle(self):
        create = MagicMock(return_value=0x123456789AB)
        user32 = MagicMock(CreateIconFromResourceEx=create)
        with patch.object(appearance.sys, 'platform', 'win32'), \
                patch.object(appearance.ctypes, 'WinDLL', return_value=user32, create=True), \
                patch('builtins.open', side_effect=AssertionError('No icon temp files')):
            handle = appearance.load_icon('eco', 24)
        self.assertEqual(handle, 0x123456789AB)
        args = create.call_args.args
        self.assertEqual(args[2:], (True, 0x00030000, 24, 24, 0))
        self.assertEqual(args[0].raw[:args[1]], appearance.make_ico_bytes(24, 'eco')[22:])
        self.assertIs(create.restype, ctypes.c_void_p)


@unittest.skipUnless(sys.platform == 'win32', 'Win32 dispatch tests require Windows ctypes')
class OsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module('app.tray.osd')

    def setUp(self):
        self.u32 = MagicMock()
        self.gdi = MagicMock()
        self.u32.SetTimer.return_value = 1
        self.u32.PostMessageW.return_value = 1
        self.patches = [patch.object(self.module, 'user32', self.u32),
                        patch.object(self.module, 'gdi32', self.gdi)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.osd = self.module.Osd()
        self.osd._hwnd, self.osd._ok = 101, True
        self.osd.start = MagicMock(return_value=True)
        self.osd._position = MagicMock()

    def deliver_latest(self):
        args = self.u32.PostMessageW.call_args.args
        self.osd._wnd_proc(*args)

    def test_exact_title_inference_and_unknown(self):
        infer = self.module._infer_tier
        self.assertEqual(infer('性能模式', None), 'perf')
        for title in ('自适应模式', '风扇加速', '未确认性能模式', 'Unknown'):
            self.assertEqual(infer(title, None), 'unknown')
        self.assertEqual(infer('性能模式', 'unknown'), 'unknown')
        self.assertEqual(infer('任意标题', 'mid'), 'mid')

    def test_requested_duration_and_repeat_reset(self):
        with patch.object(self.module.time, 'monotonic', return_value=10):
            self.assertTrue(self.osd.show('性能模式', '稍后生效', 4800))
            self.deliver_latest()
        self.assertAlmostEqual(self.osd._deadline, 14.8)
        self.assertEqual(self.osd._payload[3], 'perf')
        self.u32.ShowWindow.assert_called_with(101, self.module.SW_SHOWNOACTIVATE)
        with patch.object(self.module.time, 'monotonic', return_value=11):
            self.osd.show('均衡模式', ms=750)
            self.deliver_latest()
        self.assertAlmostEqual(self.osd._deadline, 11.75)
        with patch.object(self.module.time, 'monotonic', return_value=11.74):
            self.osd._wnd_proc(101, self.module.WM_TIMER, self.module.TIMER_ID, 0)
        self.assertTrue(self.osd._visible)
        with patch.object(self.module.time, 'monotonic', return_value=11.751):
            self.osd._wnd_proc(101, self.module.WM_TIMER, self.module.TIMER_ID, 0)
        self.assertFalse(self.osd._visible)
        self.u32.ShowWindow.assert_called_with(101, self.module.SW_HIDE)

    def test_short_duration_not_clamped_to_600ms(self):
        with patch.object(self.module.time, 'monotonic', return_value=1):
            self.osd.show('短提示', ms=80)
            self.deliver_latest()
        self.assertAlmostEqual(self.osd._deadline, 1.08)

    def test_stale_shows_and_hide_are_coalesced(self):
        self.osd.show('性能模式', ms=4000)
        old = self.u32.PostMessageW.call_args.args
        self.osd.show('省电模式', ms=1200)
        latest = self.u32.PostMessageW.call_args.args
        self.osd._wnd_proc(*old)
        self.osd._position.assert_not_called()
        self.osd._wnd_proc(*latest)
        self.assertEqual(self.osd._payload[0], '省电模式')
        self.osd.hide()
        hide = self.u32.PostMessageW.call_args.args
        self.osd._wnd_proc(*hide)
        self.assertFalse(self.osd._visible)
        self.osd._wnd_proc(*latest)
        self.assertFalse(self.osd._visible)

    def test_post_failures_return_false(self):
        self.u32.PostMessageW.return_value = 0
        self.assertFalse(self.osd.show('title'))
        self.assertFalse(self.osd.hide())
        self.assertFalse(self.osd.show('title', ms='invalid'))

    def test_close_during_layout_never_reveals_window(self):
        self.osd._position.side_effect = lambda hwnd: self.osd.close()
        self.osd.show('title')
        self.deliver_latest()
        self.assertFalse(self.osd._visible)
        self.u32.SetTimer.assert_not_called()
        self.assertTrue(all(call.args[1] == self.module.SW_HIDE
                            for call in self.u32.ShowWindow.call_args_list))

    def test_failed_thread_start_is_unavailable(self):
        osd = self.module.Osd()
        thread = MagicMock()
        thread.start.side_effect = RuntimeError('thread unavailable')
        with patch.object(self.module.threading, 'Thread', return_value=thread):
            self.assertFalse(osd.start())
        self.assertTrue(osd._closed)
        self.assertTrue(osd._stopped.is_set())

    def test_hide_and_close_post_without_cross_thread_window_calls(self):
        self.assertTrue(self.osd.hide())
        self.assertEqual(self.u32.PostMessageW.call_args.args[1], self.module.WM_APP_HIDE)
        self.assertTrue(self.osd.close())
        self.assertEqual(self.u32.PostMessageW.call_args.args[1], self.module.WM_APP_CLOSE)
        self.u32.ShowWindow.assert_not_called()
        self.u32.DestroyWindow.assert_not_called()
        self.assertFalse(self.osd.available)
        self.assertFalse(self.osd.show('after close'))

    def test_close_falls_back_to_owner_thread_quit(self):
        self.u32.PostMessageW.return_value = 0
        self.osd._thread_id = 55
        self.assertTrue(self.osd.close())
        self.u32.PostThreadMessageW.assert_called_once_with(55, self.module.WM_QUIT, 0, 0)

    def test_no_activation_or_hit_testing(self):
        self.assertEqual(self.osd._wnd_proc(101, self.module.WM_MOUSEACTIVATE, 0, 0), 3)
        self.assertEqual(self.osd._wnd_proc(101, self.module.WM_NCHITTEST, 0, 0), -1)
        self.u32.DefWindowProcW.assert_not_called()

    def test_creation_is_hidden_and_clickthrough(self):
        self.u32.CreateWindowExW.return_value = 101
        with patch.object(self.module, 'kernel32', MagicMock(GetModuleHandleW=MagicMock(return_value=42))):
            self.osd._create()
        args = self.u32.CreateWindowExW.call_args.args
        for flag in (self.module.WS_EX_NOACTIVATE, self.module.WS_EX_TRANSPARENT,
                     self.module.WS_EX_LAYERED, self.module.WS_EX_TOOLWINDOW):
            self.assertTrue(args[0] & flag)
        self.assertEqual(args[3], self.module.WS_POPUP)
        self.u32.ShowWindow.assert_not_called()

    def test_failed_startup_cleans_partial_resources(self):
        self.osd._registered, self.osd._hinst = True, 42
        self.osd._icon, self.osd._fonts = 999, {(13, 400): 888}
        with patch.object(self.osd, '_create', side_effect=OSError('startup failure')), \
                patch.object(self.module, '_set_thread_dpi', None), \
                patch.object(self.module, 'kernel32', MagicMock()):
            self.osd._run()
        self.u32.DestroyWindow.assert_called_once_with(101)
        self.u32.DestroyIcon.assert_called_once_with(999)
        self.gdi.DeleteObject.assert_called_once_with(888)
        self.u32.UnregisterClassW.assert_called_once_with(self.osd._class_name, 42)
        self.assertTrue(self.osd._ready.is_set())
        self.assertTrue(self.osd._stopped.is_set())
        self.assertFalse(self.osd.available)

    def test_callback_failure_does_not_escape(self):
        self.osd._position.side_effect = OSError('layout failure')
        self.osd.show('title')
        self.deliver_latest()
        self.assertFalse(self.osd.available)
        self.u32.PostQuitMessage.assert_called_with(1)
        self.u32.ShowWindow.assert_called_with(101, self.module.SW_HIDE)

    def test_paint_failure_restores_and_releases_gdi_objects(self):
        self.u32.BeginPaint.return_value = 111
        self.gdi.CreateCompatibleDC.return_value = 222
        self.gdi.CreateCompatibleBitmap.return_value = 333
        self.gdi.SelectObject.return_value = 444
        self.osd._draw = MagicMock(side_effect=OSError('paint failed'))
        with self.assertRaises(OSError):
            self.osd._paint(101)
        self.gdi.SelectObject.assert_called_with(222, 444)
        self.gdi.DeleteObject.assert_called_once_with(333)
        self.gdi.DeleteDC.assert_called_once_with(222)
        self.u32.EndPaint.assert_called_once()

    def test_progress_tracks_elapsed_time(self):
        self.osd._round, self.osd._text = MagicMock(), MagicMock()
        self.osd._duration_ms, self.osd._deadline = 4000, 14
        lengths = []
        for now in (10, 12):
            with patch.object(self.module.time, 'monotonic', return_value=now):
                self.osd._draw(111)
            box = self.osd._round.call_args.args[1]
            lengths.append(box[2] - box[0])
        self.assertEqual(lengths, [400, 200])
        self.osd._round.reset_mock()
        with patch.object(self.module.time, 'monotonic', return_value=14):
            self.osd._draw(111)
        self.assertEqual(self.osd._round.call_count, 3)

    def test_work_area_prefers_active_foreground_monitor(self):
        self.u32.GetForegroundWindow.return_value = 777
        self.u32.MonitorFromWindow.return_value = 888

        def monitor_info(handle, ptr):
            ptr._obj.rcWork = self.module.wintypes.RECT(-1920, 0, 0, 1040)
            return 1

        self.u32.GetMonitorInfoW.side_effect = monitor_info
        rect = self.osd._work_area()
        self.assertEqual((rect.left, rect.right, rect.bottom), (-1920, 0, 1040))
        self.u32.MonitorFromWindow.assert_called_once_with(777, 2)

    def test_dpi_layout_wraps_long_subtitle_inside_work_area(self):
        osd = self.module.Osd()
        osd._hwnd = 101
        osd._payload = ('性能模式', '已下发，约 24 秒后生效（功耗墙 75W、风扇强冷），期间别重复点', 4200, 'perf')
        osd._work_area = MagicMock(return_value=self.module.wintypes.RECT(-1920, 0, 0, 1040))
        self.u32.GetDC.return_value = 123

        def measure(hdc, text, rect, size, color, flags):
            self.assertTrue(flags & self.module.DT_WORDBREAK)
            self.assertTrue(flags & self.module.DT_CALCRECT)
            rect.bottom = 72

        osd._text = measure
        with patch.object(self.module, '_get_dpi', return_value=144), \
                patch.object(self.module, 'load_icon', return_value=456):
            osd._position(101)
        args = self.u32.SetWindowPos.call_args.args
        _, _, x, y, width, height, flags = args
        self.assertEqual(width, 660)
        self.assertEqual(height, 198)
        self.assertGreaterEqual(x, -1920)
        self.assertLessEqual(x + width, 0)
        self.assertLessEqual(y + height, 1040)
        self.assertTrue(flags & self.module.SWP_NOACTIVATE)
        self.u32.ReleaseDC.assert_called_once_with(101, 123)


if __name__ == '__main__':
    unittest.main(verbosity=2)
