"""v64 守卫测试：第 1 竿 = 纯标定竿。

用户定死的规则（第三次强调后固化，任何版本都不得违反）：
  ① 第 1 竿整个 17 秒只做标定，量出「浮标特征」和「咬钩特征」两个值；
  ② 这 17 秒里即便真咬钩也不右键 —— 右键会让浮标消失、17 秒窗口作废；
  ③ 17 秒走完直接收竿，从第 2 竿开始正常钓鱼；
  ④ 第 1 竿的锚点绝不搬给后面的竿（每竿落点不同，锚点必须现场重定）。

本测试用假对象直接驱动真实代码，验证的是「行为」，不是「注释」。
运行：python3 tests_v64_calib_cast.py
"""
import sys
import types

sys.path.insert(0, ".")

from fishingfun.fishbot import FishingBot  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        FAILS.append(name)


class FakeFinder:
    def __init__(self):
        self.previous_location = None
        self.no_bobber_frames = 0
        self.suppress_abort = False
        self.abort_cb = None
        self.decay_claim_cb = None
        self.grabber = None
        self.bobber_fingerprint = {"w": 26, "h": 18, "area": 377, "frames": 9}
        self.reset_called = 0
        self.find_called = 0

    def reset(self):
        self.reset_called += 1

    def find(self, bitmap=None):
        self.find_called += 1
        return None

    def clear_fingerprint(self):
        old = self.bobber_fingerprint
        self.bobber_fingerprint = None
        return old


class FakeWatcher:
    def __init__(self):
        self.strike_value = 4
        self.mode = "robust"
        self.y_diff = 0

    def reset(self, p=None):
        pass

    def is_bite(self, cur):
        return False


class FakeProc:
    def __init__(self):
        self.clicks = []
        self.keys = []

    def press_key(self, k):
        self.keys.append(k)

    def click(self, x, y):
        self.clicks.append((x, y))

    def park_cursor_verified(self):
        return True, (1, 1)


def make_bot():
    logs = []
    bot = FishingBot(
        FakeFinder(), FakeWatcher(),
        cast_key=4, wow_process=FakeProc(),
        log=lambda fmt, *a: logs.append(fmt % a if a else fmt),
    )
    bot.calib_sink = True
    bot._cast_no = 1
    return bot, logs


print("=" * 62)
print("FishingFun v64 守卫测试：首竿 = 纯标定竿")
print("=" * 62)

# ---- T1：标定竿不找锚点、不判咬钩、不右键 ----
bot, logs = make_bot()
bot.bobber_finder.bobber_fingerprint = {"w": 26, "h": 18, "area": 377, "frames": 9}
bot._calib_cast_done = True          # 模拟 _maybe_calibrate_sink 刚跑完
bot._wait_for_bite()                  # 真实方法
check("T1 标定竿不调用 finder.reset()", bot.bobber_finder.reset_called == 0,
      "reset=%d" % bot.bobber_finder.reset_called)
check("T1 标定竿不扫锚点（find 0 次）", bot.bobber_finder.find_called == 0,
      "find=%d" % bot.bobber_finder.find_called)
check("T1 标定竿不右键", bot.wow_process.clicks == [],
      "clicks=%s" % (bot.wow_process.clicks,))
check("T1 标定竿结束有报告日志", any("标定竿结束" in s for s in logs), str(logs[-3:]))
check("T1 previous_location 被清空", bot.bobber_finder.previous_location is None)

# ---- T2：第 2 竿不受标定竿标记影响 ----
bot, logs = make_bot()
bot._calib_cast_done = False
bot.bobber_finder.previous_location = (100, 100)
bot.anchor_wait = 0.01
bot.cast_flight = 0.0
bot.calib_enabled = False
bot._wait_for_bite()
check("T2 普通竿会调用 finder.reset()", bot.bobber_finder.reset_called == 1,
      "reset=%d" % bot.bobber_finder.reset_called)

# ---- T3：标定时清掉旧指纹 + 置标定竿标记 ----
bot, logs = make_bot()
bot._sink_calibrated = False
bot.bobber_finder.bobber_fingerprint = {"w": 47, "h": 167, "area": 483, "frames": 12}
# 用假的 sinkcalib 模块替换，避免真的跑 17 秒截图
fake = types.ModuleType("fishingfun.sinkcalib")

class FakeCalib:
    def __init__(self, *a, **k):
        self.points = []
        self.anchor = (618, 413)
        self.p90 = 1.0

    def run(self, t_key=None):
        return None

    def detect_horizon(self):
        return None, 0.0

    def locate_in_sequence(self):
        return None

fake.SinkCalibrator = FakeCalib
sys.modules["fishingfun.sinkcalib"] = fake
bot.dump_frame_pair = lambda *a, **k: None
bot._dump_confirm = lambda *a, **k: None
bot._boxes_for_zones = lambda b, x=None: []
try:
    bot._maybe_calibrate_sink()
except Exception as e:
    check("T3 _maybe_calibrate_sink 不抛异常", False, repr(e))
check("T3 旧浮标指纹被清空", bot.bobber_finder.bobber_fingerprint is None,
      str(bot.bobber_finder.bobber_fingerprint))
check("T3 置了标定竿标记 _calib_cast_done", bot._calib_cast_done is True)
check("T3 标定竿日志出现", any("纯标定竿" in s for s in logs), str(logs[:3]))
check("T3 标定期锚点只做取证（记录但不复用）",
      bot._calib_anchor_local == (618, 413), str(bot._calib_anchor_local))

# ---- T4：确保标定锚点不会流到 _wait_for_bite ----
bot._cast_no = 1
bot._wait_for_bite()
check("T4 标定锚点没有变成下竿的 previous_location",
      bot.bobber_finder.previous_location is None,
      str(bot.bobber_finder.previous_location))

# ---- T5：静态检查 —— 主循环必须在按 4 键之前定标定竿标记 ----
src = open("fishingfun/fishbot.py", encoding="utf-8").read()
i_arm = src.find("self._calib_cast_armed = bool(")
i_key = src.find("self.wow_process.press_key(self.cast_key)")
check("T5 标定竿标记在 press_key 之前赋值",
      i_arm != -1 and i_key != -1 and i_arm < i_key,
      "arm@%d key@%d" % (i_arm, i_key))

print()
if FAILS:
    print("结果：FAIL —— %d 项不通过: %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("结果：PASS（%d 项）" % 12)
