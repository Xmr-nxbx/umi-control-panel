"""Shared desktop tokens and dependency-free console artwork."""
import ctypes
import math
import struct
import sys
from functools import lru_cache

PALETTE = {
    'bg': '#0B1220',
    'surface': '#121E30',
    'border': '#2B3C54',
    'text': '#EEF4FF',
    'muted': '#9CAEC6',
    'accent': '#64C9F3',
}
TIER_COLORS = {
    'eco': (105, 214, 177),
    'bal': (100, 201, 243),
    'mid': (230, 186, 114),
    'perf': (244, 126, 141),
    'unknown': (149, 164, 187),
}
TIER_BARS = {'eco': 1, 'bal': 2, 'mid': 3, 'perf': 4, 'unknown': 0}


def normalize_tier(tier):
    key = tier.strip().lower() if isinstance(tier, str) else 'unknown'
    return key if key in TIER_COLORS else 'unknown'


def _rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _shapes(tier):
    accent = TIER_COLORS[normalize_tier(tier)]
    shapes = [
        ('rect', (1, 2, 30, 28, 6), accent),
        ('rect', (2.5, 3.5, 27, 25, 4.5), _rgb(PALETTE['surface'])),
        ('line', (6.5, 8, 10, 11, 1.8), _rgb(PALETTE['text'])),
        ('line', (10, 11, 6.5, 14, 1.8), _rgb(PALETTE['text'])),
        ('line', (13, 14, 16.5, 14, 1.6), _rgb(PALETTE['text'])),
    ]
    bars = TIER_BARS[normalize_tier(tier)]
    if bars:
        for i in range(bars):
            height = 4 + i * 3
            shapes.append(('rect', (6 + i * 5.5, 26 - height, 3.5, height, .8), accent))
    else:
        # A broken baseline means unknown, never a guessed performance level.
        for x in (6, 14, 22):
            shapes.append(('rect', (x, 23, 4, 2, 1), accent))
    return shapes


def _inside(kind, geometry, x, y):
    if kind == 'rect':
        left, top, width, height, radius = geometry
        dx = max(left + radius - x, 0, x - (left + width - radius))
        dy = max(top + radius - y, 0, y - (top + height - radius))
        return dx * dx + dy * dy <= radius * radius
    x1, y1, x2, y2, width = geometry
    vx, vy = x2 - x1, y2 - y1
    t = max(0, min(1, ((x - x1) * vx + (y - y1) * vy) / (vx * vx + vy * vy)))
    return (x - x1 - t * vx) ** 2 + (y - y1 - t * vy) ** 2 <= (width / 2) ** 2


def _bounds(kind, geometry):
    if kind == 'rect':
        x, y, width, height, _ = geometry
        return x, y, x + width, y + height
    x1, y1, x2, y2, width = geometry
    r = width / 2
    return min(x1, x2) - r, min(y1, y2) - r, max(x1, x2) + r, max(y1, y2) + r


def _validate_size(size):
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 256:
        raise ValueError('Icon size must be an integer between 1 and 256')


def _raster(size, tier):
    samples = 4
    edge = size * samples
    scale = edge / 32
    pixels = bytearray(edge * edge * 4)
    for kind, geometry, color in _shapes(tier):
        left, top, right, bottom = _bounds(kind, geometry)
        rgba = bytes((*color, 255))
        for y in range(max(0, math.floor(top * scale)), min(edge, math.ceil(bottom * scale))):
            for x in range(max(0, math.floor(left * scale)), min(edge, math.ceil(right * scale))):
                if _inside(kind, geometry, (x + .5) / scale, (y + .5) / scale):
                    offset = (y * edge + x) * 4
                    pixels[offset:offset + 4] = rgba
    result = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            red = green = blue = covered = 0
            for sy in range(samples):
                offset = ((y * samples + sy) * edge + x * samples) * 4
                for sx in range(samples):
                    i = offset + sx * 4
                    if pixels[i + 3]:
                        red += pixels[i]
                        green += pixels[i + 1]
                        blue += pixels[i + 2]
                        covered += 1
            if covered:
                i = (y * size + x) * 4
                result[i:i + 4] = bytes((red // covered, green // covered, blue // covered,
                                       (covered * 255 + 8) // 16))
    return result


@lru_cache(maxsize=32)
def _icon_bytes(size, tier):
    rgba = _raster(size, tier)
    stride = ((size + 31) // 32) * 4
    mask = bytearray(stride * size)
    bitmap = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            src = (y * size + x) * 4
            dest = ((size - 1 - y) * size + x) * 4
            red, green, blue, alpha = rgba[src:src + 4]
            bitmap[dest:dest + 4] = bytes((blue, green, red, alpha))
            if not alpha:
                mask[(size - 1 - y) * stride + x // 8] |= 0x80 >> (x % 8)
    dib = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0,
                      len(bitmap) + len(mask), 0, 0, 0, 0) + bitmap + mask
    entry = struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(dib), 22)
    return struct.pack('<HHH', 0, 1, 1) + entry + dib


def make_ico_bytes(size=32, tier='bal'):
    """Return a deterministic, antialiased 32-bit ICO; no filesystem access."""
    _validate_size(size)
    return _icon_bytes(size, normalize_tier(tier))


def make_svg(tier='bal'):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="Umi Control Panel">',
             '  <title>Umi Control Panel</title>']
    for kind, geometry, color in _shapes(tier):
        fill = '#%02X%02X%02X' % color
        if kind == 'rect':
            x, y, w, h, r = geometry
            parts.append(f'  <rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{r:g}" fill="{fill}"/>')
        else:
            x1, y1, x2, y2, width = geometry
            parts.append(f'  <path d="M{x1:g} {y1:g}L{x2:g} {y2:g}" fill="none" stroke="{fill}" stroke-width="{width:g}" stroke-linecap="round"/>')
    return '\n'.join(parts + ['</svg>', ''])


def load_icon(tier='bal', size=32):
    """Return an owned HICON. The caller must call user32.DestroyIcon."""
    data = make_ico_bytes(size, tier)[22:]
    if sys.platform != 'win32':
        raise OSError('HICON loading requires Windows')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    create = user32.CreateIconFromResourceEx
    create.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                       ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    create.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    handle = create(buffer, len(data), True, 0x00030000, size, size, 0)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle
