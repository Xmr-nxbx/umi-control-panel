"""Generate the shared console favicon assets when explicitly invoked."""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.desktop.appearance import make_ico_bytes, make_svg

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def make_multi_ico(tier='bal'):
    offset = 6 + len(SIZES) * 16
    entries, images = [], []
    for size in SIZES:
        image = make_ico_bytes(size, tier)[22:]
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0,
                                   1, 32, len(image), offset))
        images.append(image)
        offset += len(image)
    return struct.pack('<HHH', 0, 1, len(SIZES)) + b''.join(entries + images)


def main():
    target = ROOT / 'app' / 'web'
    (target / 'umi.ico').write_bytes(make_multi_ico())
    (target / 'umi.svg').write_text(make_svg(), encoding='utf-8', newline='\n')
    print('Generated umi.ico (%s) and umi.svg in %s' %
          (', '.join(map(str, SIZES)), target))


if __name__ == '__main__':
    main()
