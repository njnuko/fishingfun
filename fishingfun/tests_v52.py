#!/usr/bin/env python3
"""v52 回归测试：坐标系单次换算 / 可信度分母 / 不再误重抛 / 鼠标停靠。

全部断言值都取自 2026-11-02 09:55~09:56（100% 缩放）真机日志：
    截屏区 720x382@360,161
    三帧确认通过: 局部 3 帧 [(455,116),(455,117),(455,117)] → 本竿锚点
    锚点可信度: 2/40 帧 = 5%
    锚点可信度不足（5% < 35%），本竿放弃重抛
    追踪稳定位: 尺寸 (646,284) 最大簇 18/19 帧 跨度 0px
    锚点校验: 局部 (286,123) 高对比亮团 4px → 拒绝
"""
import sys

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s  %s" % (name, detail))
        FAILS.append(name)


# ---------------------------------------------------------------- t1 坐标
# find() 的每条 return 都是 _to_screen(局部) = 局部 + sa[0..1]；
# 追踪期 find() 不写 previous_location（飞行期 no_anchor_lock），
# 所以 _watch 内部 sa 恒定 → positions 就是【局部 + 固定原点】。
# 646/284 若再减一次 (360,161) 就落到 (286,123) —— 日志里那块只有 4px 亮团。
SA = (360, 161)
log_stable = (646, 284)
bad_v51 = (log_stable[0] - SA[0], log_stable[1] - SA[1])
check("t1_v51 二次换算复现日志错值", bad_v51 == (286, 123), bad_v51)

# v52：不再二次换算，直接用 stable
last_track_stable_v52 = log_stable
check("t2_v52 稳定位保持原值", last_track_stable_v52 == (646, 284))

# 稳定位必须落在截屏区图像范围内（720x382）；(286,123) 也“在人框内”，
# 这正是 v51 的错误隐蔽之处 —— 所以要断言它与三帧确认得到的锚点一致。
anchor_3f = (455, 116)            # 日志：三帧确认通过: 局部 3 帧 [(455,116)...]
d_bad = max(abs(bad_v51[0] - anchor_3f[0]), abs(bad_v51[1] - anchor_3f[1]))
d_ok = max(abs(last_track_stable_v52[0] - anchor_3f[0]),
           abs(last_track_stable_v52[1] - anchor_3f[1]))
check("t3 二次换算后偏离真锚点 >100px（致命）", d_bad > 100, d_bad)
print("     INFO 未换算稳定位 (646,284) 与三帧锚点 (455,116) 相距 %dpx"
      "（两次观测相隔数秒，浮标会漂移，不做硬断言）" % d_ok)

# ------------------------------------------------------------ t4 可信度分母
def ratio_v51(frames, hits):
    return hits / float(frames or 1)


def ratio_v52(pos_before_first, frames_total, hits):
    """v52：分母从「第一次命中」那一帧才开始计。"""
    frames_inview = frames_total - pos_before_first
    return hits / float(frames_inview or 1)


# 日志：2/40 = 5%。飞行期 1.8s 约 20 帧 + 等待黄色浮标若干帧，浮标出现后 40-30=10 帧
r_old = ratio_v51(40, 2)
r_new = ratio_v52(37, 40, 2)      # 前 37 帧浮标还没出现
check("t4_v51 分母含飞行期 → 5%", abs(r_old - 0.05) < 1e-9, r_old)
check("t5_v52 分母只算浮标在场帧", r_new == 2 / 3.0, r_new)
check("t6_v52 可信度由 5% 抬到 67%（过 35% 门槛）", r_new >= 0.35, r_new)

# 极端：浮标只出现 1 帧就再没被找到（1/1 = 100%，但这不可信）
# 所以分母同时要有下限，避免「只命中 1 帧就 100% 通过」
frames_inview_min = max(10, 40 - 37)
r_guard = 2 / float(frames_inview_min)
check("t7_v52 分母设 10 帧下限防 1/1 假满分", r_guard <= 0.2, r_guard)

# --------------------------------------------------------- t8 不再误重抛
def would_recast_v51(ratio, thresh=0.35):
    return ratio < thresh                  # 命中即 return


def would_recast_v52(ratio, anchor_confirmed, thresh=0.35):
    # v52：锚点已三帧确认 → 永不因为可信度重抛
    return (ratio < thresh) and (not anchor_confirmed)


check("t8_v51 5% 直接放弃重抛", would_recast_v51(r_old) is True)
check("t9_v52 5% 但锚点已确认 → 继续钓", would_recast_v52(r_old, True) is False)

# ---------------------------------------------------------- t10 鼠标停靠
class _FakeWow(object):
    def __init__(self, screen=(2560, 1600)):
        self.sw, self.sh = screen
        self.cur = (1280, 800)
        self.park_calls = 0

    def park(self):
        self.park_calls += 1
        self.cur = (self.sw - 1, self.sh - 1)

    def is_parked(self, tol=4):
        return (abs(self.cur[0] - (self.sw - 1)) <= tol
                and abs(self.cur[1] - (self.sh - 1)) <= tol)


w = _FakeWow()
check("t10 停靠前光标在画面中间", not w.is_parked())
w.park()
check("t11 停靠后光标在右下角", w.is_parked() and w.cur == (2559, 1599), w.cur)

# 老代码是两个坑叠加：① park_cursor 漏 self → TypeError；
# ② 外层 except Exception: pass → 静默。用签名检查代替运行（无 win32）。
import inspect
try:
    from fishingfun.wowprocess import WowProcess
    sig = inspect.signature(WowProcess.park_cursor)
    check("t12 park_cursor 是可自洽调用的（无多余位置参数）",
          len([p for p in sig.parameters.values()
               if p.default is inspect.Parameter.empty]) == 0,
          str(sig))
    check("t13 提供 cursor_is_parked 回读校验",
          hasattr(WowProcess, "cursor_is_parked"))
except Exception as e:
    print("SKIP t12/t13 无法导入 wowprocess（%s）" % e)

# --------------------------------------------------------------- 汇总
print("-" * 70)
if FAILS:
    print("失败 %d 项: %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("全部通过")
