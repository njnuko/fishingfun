"""v60 验证：17 秒浮标存活期门控 + 面积衰减裁决。

真机事实（2026-11-02 13:45-13:46 v59 日志）：
  第1竿 抛竿 13:45:30.542 → 消失 13:45:49.200（+18.66s） 面积序列与第2竿逐帧相同
  第2竿 抛竿 13:45:49.660 → 消失 13:46:08.102（+18.44s） …195,112,158,97,80,70
  第3竿 抛竿 13:46:08.557 → 咬钩 13:46:27.294（+18.74s） y 401→420→425 位移24px
→ 前两竿是 17 秒到期自动收杆（不是咬钩），第三竿才是真咬钩。
"""
import sys, time
sys.path.insert(0, "/tmp/up/x/fishingfun")
from fishingfun.searchbobberfinder import SearchBobberFinder

LOGS = []
def mk(elapsed_s, area_seq):
    f = SearchBobberFinder.__new__(SearchBobberFinder)
    f._area_hist = []
    f.no_bobber_frames = 3
    f.no_bobber_limit = 3
    f.abort_cb = lambda n: LOGS.append(("ABORT", n))
    f.decay_claim_cb = lambda: LOGS.append(("BITE", 1))
    f._decay_claimed = False
    f.bite_deadline_s = 16.5
    f.seconds_since_cast = lambda: elapsed_s
    f.log = lambda *a, **k: None
    now = time.time()
    for i, a in enumerate(area_seq):
        f._area_hist.append((now + i * 0.03, a))
    return f

# 第1/2竿真实序列
EXPIRY = [233, 379, 381, 386, 392, 395, 397, 395, 397, 392, 372, 211, 274,
          186, 162, 153, 108, 97, 80, 70]

def case(name, elapsed, seq, want_claim, want_log):
    LOGS.clear()
    f = mk(elapsed, seq)
    got = f._claim_decay()
    print("%-46s elapsed=%5.1fs -> claim=%-5s 事件=%s  期望 claim=%s" %
          (name, elapsed, got, LOGS, want_claim))
    assert got is want_claim, "%s: claim 期望 %s 实际 %s" % (name, want_claim, got)
    assert (("BITE", 1) in LOGS) is want_log, "%s: 咬钩回调期望 %s" % (name, want_log)
    if want_claim:
        assert ("ABORT", 3) not in LOGS

# 1) 17 秒到期收杆（第2竿真实数据 18.44s）→ 必须拒绝
case("到期收杆 18.44s（第2竿真实序列）", 18.44, EXPIRY, False, False)
# 2) 第1竿 18.66s 同样拒绝
case("到期收杆 18.66s（第1竿）", 18.66, EXPIRY, False, False)
# 3) 未到期 + 干净渐进衰减 → 认咬钩
CLEAN = [400, 360, 330, 300, 270, 240, 200, 170, 140, 120]
case("未到期 10.0s 干净渐进衰减", 10.0, CLEAN, True, True)
# 3b) 未到期 + 第2竿真实收缩序列 → 认咬钩（该序列本身满足衰减判据）
PULL = [271, 269, 225, 220, 219, 216, 217, 213, 211, 205, 201, 195, 112]
case("未到期 10.0s 被拉收缩序列", 10.0, PULL, True, True)
# 3c) 同一 PULL 序列但已到期 → 拒绝（证明门控在起作用）
case("已到期 18.0s 被拉收缩序列", 18.0, PULL, False, False)
# 4) 边界：16.4s（刚过线内侧）→ 认
case("边界 16.4s（<16.5）", 16.4, CLEAN, True, True)
# 5) 边界：16.5s（刚好等于阈值）→ 拒
case("边界 16.5s（>=16.5）", 16.5, CLEAN, False, False)
# 6) 未到期但面积平稳 → 不认
case("未到期 8s 但面积平稳", 8.0, [220, 221, 219, 220, 222, 220], False, False)
# 7) 未到期但断崖 → 不认（v57 保护仍生效）
case("未到期 8s 但单帧断崖 200→71", 8.0, [237, 230, 225, 220, 215, 200, 71], False, False)
# 8) 无回调注册
f = mk(10.0, CLEAN); f.decay_claim_cb = None
r = f._claim_decay()
print("%-46s -> claim=%s 期望 False" % ("无回调注册", r))
assert r is False
# 9) 重复调用只拾取一次
LOGS.clear()
f = mk(10.0, CLEAN)
a = f._claim_decay(); b = f._claim_decay()
print("%-46s -> 第1次=%s 第2次=%s 咬钩次数=%d 期望 1" %
      ("重复调用防重", a, b, LOGS.count(("BITE", 1))))
assert a is True and b is False and LOGS.count(("BITE", 1)) == 1

print("\nALL PASS")
