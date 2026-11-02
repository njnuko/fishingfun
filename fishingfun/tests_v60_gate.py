"""v60 补充测试：FishingBot._bite_from_decay 的 17 秒门控。

目的：证明"面积衰减 → 咬钩"这条路径**两条入口都受 16.5s 门控约束**。
      searchbobberfinder._claim_decay() 的门控由 test_v60_decay.py 覆盖；
      本文件用桩对象直接测 fishbot._bite_from_decay()，不需要 win32。
"""
import sys
import types

# ---- 在导入 fishingfun 前塞入 win32 桩，避免真机依赖 ----
for name in ("win32gui", "win32ui", "win32con", "win32api", "win32process",
             "ctypes.wintypes"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["win32gui"].GetWindowRect = lambda h: (0, 0, 1440, 900)
sys.modules["win32gui"].EnumWindows = lambda *a, **k: None
sys.modules["win32gui"].IsWindowVisible = lambda h: True
sys.modules["win32gui"].GetWindowText = lambda h: ""
sys.modules["win32gui"].GetClassName = lambda h: ""
sys.modules["win32gui"].GetWindowThreadProcessId = lambda h: (0, 0)
sys.modules["win32gui"].SetForegroundWindow = lambda h: None

sys.path.insert(0, "/tmp/up/x/fishingfun")

from fishingfun.fishbot import FishingBot  # noqa: E402


class FakeFinder:
    def __init__(self, elapsed, deadline=16.5, decayed=True):
        self.bite_deadline_s = deadline
        self._elapsed = elapsed
        self.seconds_since_cast = lambda: self._elapsed
        self._decayed = decayed

    def area_decayed(self):
        return self._decayed


class Harness:
    """只装配 _bite_from_decay 需要的属性，绕过 __init__。"""

    def __init__(self, elapsed, deadline=16.5):
        self.logs = []
        self.looted = []
        self.bobber_finder = FakeFinder(elapsed, deadline)
        self._bite_handled_this_cast = False

    def log(self, fmt, *a):
        try:
            self.logs.append(fmt % a if a else fmt)
        except Exception:
            self.logs.append(fmt)

    def _dump_region(self, *a, **k):
        pass

    def _forensic_bite(self, *a, **k):
        pass

    def _loot(self, pos):
        self.looted.append(pos)

    def _press_ten_min_key_if_due(self):
        pass

    # 绑定真实方法
    _bite_from_decay = FishingBot._bite_from_decay


CASES = [
    # (名称, 距抛竿秒数, deadline, 期望是否右键拾取)
    ("未到期 5.0s 面积衰减", 5.0, 16.5, True),
    ("未到期 16.4s（边界内）", 16.4, 16.5, True),
    ("恰好 16.5s（边界外）", 16.5, 16.5, False),
    ("到期 18.4s（第2竿真实值）", 18.4, 16.5, False),
    ("到期 18.66s（第1竿真实值）", 18.66, 16.5, False),
]

fails = 0
for name, el, dl, expect in CASES:
    h = Harness(el, dl)
    h._bite_from_decay((723, 401))
    got = len(h.looted) > 0
    ok = (got == expect)
    if not ok:
        fails += 1
    print("%-28s elapsed=%5.1fs -> 拾取=%-5s 期望=%-5s %s"
          % (name, el, got, expect, "OK" if ok else "FAIL"))
    if not got:
        assert any("到期自动收杆" in s for s in h.logs), h.logs

# 幂等：同一竿重复调用只能拾取一次
h = Harness(5.0)
h._bite_from_decay((723, 401))
h._bite_from_decay((723, 401))
ok = len(h.looted) == 1
if not ok:
    fails += 1
print("%-28s 第1次+第2次 -> 拾取次数=%d 期望 1 %s"
      % ("重复调用防重", len(h.looted), "OK" if ok else "FAIL"))

print()
print("ALL PASS" if fails == 0 else "%d 个用例失败" % fails)
sys.exit(1 if fails else 0)
