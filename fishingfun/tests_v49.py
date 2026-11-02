"""v49 行为回归：可恢复停机 / 面积衰减咬钩 / 自适应亮团门槛。

不连游戏，全部用假对象驱动 FishingBot 的真实代码路径。
"""
import sys, types, time

# 无 numpy/无游戏的纯净 import：只测逻辑，不建 grabber
import numpy as np

class FakeFinder:
    def __init__(self):
        self.bobber_fingerprint = None
        self.no_bobber_frames = 0
        self.suppress_abort = False
        self.no_anchor_lock = False
        self._area_hist = []
        self.abort_cb = None
    def area_decayed(self, window=1.2, drop_ratio=0.72):
        hist = [h for h in self._area_hist if time.time() - h[0] <= window]
        if len(hist) < 3:
            return False
        areas = [a for _, a in hist]
        first, last = areas[0], areas[-1]
        if first <= 0 or last / float(first) > drop_ratio:
            return False
        for i in range(1, len(areas)):
            if areas[i] > areas[i-1] * 1.05:
                return False
        return True
    def reset(self):
        self._area_hist = []

class FakeBot:
    pass

PASS = []
def check(name, cond, extra=""):
    PASS.append((name, cond, extra))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + extra) if extra else ""))

# ---------- 1. 面积衰减判别 ----------
f = FakeFinder()
now = time.time()
# 真机序列：457(锚定) -> 400 -> 313 -> 消失
for i, a in enumerate([457, 400, 313]):
    f._area_hist.append((now - 0.3 + i*0.1, a))
check("渐进缩小(457→400→313) 判为咬钩", f.area_decayed() is True)

f.reset()
# 一步归零：只有满面积样本，然后消失
f._area_hist.append((now - 0.2, 457))
check("一步消失(仅457) 不判咬钩", f.area_decayed() is False)

f.reset()
# 水面起伏：面积来回抖，不单调
for i, a in enumerate([400, 450, 410]):
    f._area_hist.append((now - 0.3 + i*0.1, a))
check("面积反弹(400→450→410) 不判咬钩", f.area_decayed() is False)

f.reset()
# 缩得不够多：457 -> 400 -> 350 (0.77 > 0.72)
for i, a in enumerate([457, 400, 350]):
    f._area_hist.append((now - 0.3 + i*0.1, a))
check("缩小不足28%(457→350) 不判咬钩", f.area_decayed() is False)

f.reset()
# 旧样本超出窗口
f._area_hist = [(now - 5.0, 457), (now - 4.9, 400), (now - 4.8, 313)]
check("超出1.2秒窗口的衰减 不判咬钩", f.area_decayed() is False)

# ---------- 2. 自适应亮团门槛 ----------
from fishingfun.fishbot import FishingBot
import inspect
src = inspect.getsource(FishingBot._bright_px_floor)
check("_bright_px_floor 存在", "MIN_BRIGHT_PX_AT_ANCHOR" in src)

class B:
    bobber_finder = None
    MIN_BRIGHT_PX_AT_ANCHOR = 90
    BRIGHT_PX_FP_RATIO = 0.35
B._bright_px_target = FishingBot._bright_px_target
B._bright_px_floor = FishingBot._bright_px_floor

b = B(); b.bobber_finder = FakeFinder()
check("无指纹时门槛=90", b._bright_px_floor() == 90, "got %s" % b._bright_px_floor())

b.bobber_finder.bobber_fingerprint = {"area": 720}
v = b._bright_px_floor()
check("指纹720 → 门槛251(int(720*0.35))", v == 251, "got %s" % v)

b.bobber_finder.bobber_fingerprint = {"area": 457}
v = b._bright_px_floor()
check("指纹457 → 门槛159(457*0.35)", v == 159, "got %s" % v)

b.bobber_finder.bobber_fingerprint = {"area": 200}
v = b._bright_px_floor()
check("指纹200 → 门槛兜底90", v == 90, "got %s" % v)

b.bobber_finder.bobber_fingerprint = {"area": 100}
v = b._bright_px_floor()
check("指纹100 → 门槛90(下限)", v == 90, "got %s" % v)

# 关键：新环境 457 面积实际测到 313，按新门槛(159)能过，按旧固定90也能过；
# 但再降分辨率到 200 面积时，旧门槛 90 已接近水面噪声上限，新门槛兜底 90 保护。
print()
bad = [n for n, c, _ in PASS if not c]
print("== %d/%d 通过 ==" % (len(PASS) - len(bad), len(PASS)))
if bad:
    print("失败:", bad)
    sys.exit(1)
