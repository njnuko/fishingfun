"""v48 闸门回归验证：用真机截帧（用户上传的 1280x801 区域帧）离线跑
_anchor_has_bobber 的新判据，确认「浮标必过、空白水面必拒」。

真机事实（2026-11-01 用户上传帧 img_2d27b1fa00a9，1280x801）：
  浮标本体 bbox x488..519 y321..344，质心 (504,331)
  锚点日志值局部 (501,332)，与质心差 3px（正常抖动）
"""
import struct
import sys

import numpy as np

sys.path.insert(0, "/tmp/up/x/fishingfun")


def load_rgb(path):
    d = open(path, "rb").read()
    off = struct.unpack_from("<I", d, 10)[0]
    w, h = struct.unpack_from("<ii", d, 18)
    bottom_up = h > 0
    h = abs(h)
    row = ((w * 3) + 3) // 4 * 4
    a = np.frombuffer(d, dtype=np.uint8, count=row * h, offset=off)
    a = a.reshape(h, row)[:, : w * 3].reshape(h, w, 3)
    if bottom_up:
        a = a[::-1]
    return np.ascontiguousarray(a[:, :, ::-1])


class FakeBot:
    """只带 _anchor_has_bobber 需要的属性，避免拉起整个 FishingBot。"""

    ANCHOR_WIN_R = 30
    MIN_BRIGHT_PX_AT_ANCHOR = 90
    MIN_BRIGHT_PEAK_AT_ANCHOR = 70
    MIN_BRIGHT_AREA_RATIO = 0.03
    MIN_WARM_PX_AT_ANCHOR = 20

    def __init__(self):
        self.msgs = []

    def log(self, fmt, *a):
        self.msgs.append(fmt % a if a else fmt)


from fishingfun.fishbot import FishingBot  # noqa: E402

FakeBot._anchor_has_bobber = FishingBot._anchor_has_bobber

img = load_rgb("/opt/data/cache/images/img_2d27b1fa00a9.jpg")
print("帧尺寸", img.shape)

bot = FakeBot()

CASES = [
    # (描述, 局部坐标, 期望通过)
    ("浮标-日志锚点", (501, 332), True),
    ("浮标-质心", (504, 331), True),
    ("浮标-偏左上3px", (498, 329), True),
    ("空白水面A", (450, 300), False),
    ("空白水面B", (300, 350), False),
    ("空白水面C", (700, 500), False),
    ("空白水面D", (900, 600), False),
    ("空白天面E", (600, 150), False),
    ("水面反光最强处G", (1000, 400), False),
    ("远右空白", (1100, 700), False),
]

fails = 0
print("-" * 78)
for name, xy, want in CASES:
    bot.msgs.clear()
    got = bot._anchor_has_bobber(xy, bmp=img, log=True)
    ok = (got == want)
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name, xy, "期望", want, "实际", got)
    for m in bot.msgs:
        print("      ", m)
print("-" * 78)
print("结果:", "全部通过" if fails == 0 else "%d 条失败" % fails)
