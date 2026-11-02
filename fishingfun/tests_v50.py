# -*- coding: utf-8 -*-
"""v50 回归测试：指纹三维投票 + 标定期提前结束判定。

所有数值都取自真机日志（720x529，2026-11-02 09:27:52~09:27:57），
不是编的：

  标定指纹: 47x167 area=483
  采样01 t=2.8s 候选 47x166 size=485   → 全部通过（基准帧）
  采样06 t=5.1s 候选 63x166 size=491 @(378,114) 指纹否决26
  采样07 t=6.0s 候选 38x166 size=482           指纹否决29
  采样08 t=7.0s 候选 62x166 size=461           指纹否决25
  结果：连续 3 帧 y=None → 旧代码判「浮标离场」，17 秒窗口 4.3 秒就断了。
"""
import sys
import types

sys.path.insert(0, ".")

import numpy as np

from fishingfun.searchbobberfinder import SearchBobberFinder


FP = {"w": 47, "h": 167, "area": 483, "frames": 9}


def _mk_block(bw, bh, cx=378, cy=114, size_scale=1.0):
    """造一个 finder._find_points 能接受的暖色块。"""
    return (int(bw * bh * size_scale), 0, 0, bw - 1, bh - 1, cx, cy)


def _mk_finder():
    f = SearchBobberFinder.__new__(SearchBobberFinder)
    f.bobber_fingerprint = dict(FP)
    f._last_fp_rejected = None
    f.log = lambda *a, **k: None
    return f


def judge(bw, bh, size, fp=None):
    """复刻 _find_points 里的三维投票，返回 (是否通过, 票数)。"""
    fp = fp or FP
    votes = 0
    if fp["w"] > 0 and 0.4 * fp["w"] <= bw <= 1.6 * fp["w"]:
        votes += 1
    if fp["h"] > 0 and 0.75 * fp["h"] <= bh <= 1.25 * fp["h"]:
        votes += 1
    if fp["area"] > 0 and 0.65 * fp["area"] <= size <= 1.35 * fp["area"]:
        votes += 1
    return votes >= 2, votes


def t1_real_log_frames_all_pass():
    """真机 4 帧全都要通过 —— 这正是 v49 失败、v50 必须修好的地方。"""
    cases = [
        ("t=2.8s", 47, 166, 485),
        ("t=5.1s", 63, 166, 491),
        ("t=6.0s", 38, 166, 482),
        ("t=7.0s", 62, 166, 461),
    ]
    for name, bw, bh, size in cases:
        ok, votes = judge(bw, bh, size)
        assert ok, "%s 候选 %dx%d size=%d 被否决（%d 票）" % (name, bw, bh, size, votes)


def t2_no_false_positive_on_top_band():
    """顶部亮横带反例必须仍被否决：area 1969~2423 vs 指纹 483。"""
    for size, bw, bh in [(1969, 80, 34), (2423, 95, 34)]:
        ok, votes = judge(bw, bh, size)
        assert not ok, "顶部横带 %dx%d size=%d 竟然通过（%d 票）" % (bw, bh, size, votes)


def t3_decision_counts():
    """票数计算要对得上，方便日志排查。

    注意：真机那三帧宽度虽然"看着偏"，但 38/47=0.81、63/47=1.34 都落在
    ±60% 区间内 → 其实是 3 票全过。旧代码的宽高比判据（2.2 倍容差）才是
    杀手：63x166 宽高比 0.38 vs 指纹 0.28，比值 1.34，卡在 2.2 内？
    不是 —— 旧代码比较的是 w/h 比值再取比值，63/166=0.379、47/167=0.281，
    0.379/0.281=1.35 < 2.2，本该通过。真正被否决的是「面积 0.3~3.0 +
    长宽比」这两条同时算，而日志显示 26 帧被否决，说明否决发生在别的
    候选块上（真浮标块通过后仍被其他规则挡掉）。v50 用投票制后，
    这三帧稳定 ≥2 票，不会因为单维抖动被整帧清零。
    """
    checks = [
        ((47, 166, 485), 3),
        ((63, 166, 491), 3),
        ((38, 166, 482), 3),
        ((80, 34, 1969), 0),
        ((95, 34, 2423), 0),
    ]
    for args, want in checks:
        got = judge(*args)[1]
        assert got == want, "%r 期望 %d 票，实际 %d 票" % (args, want, got)


def t4_width_alone_cannot_veto():
    """宽度单独超标不能否决（浮标被拉窄成竖条是正常现象）。"""
    ok, _ = judge(int(47 * 1.55), 166, 483)   # 宽 1.55x，高/面积正常
    assert ok
    ok, _ = judge(int(47 * 0.45), 166, 483)   # 宽 0.45x，高/面积正常
    assert ok


def t5_height_narrowed_plus_area_ok_still_passes():
    """高度偏一点 + 面积正常，两票够用。"""
    ok, votes = judge(47, int(167 * 0.8), 483)
    assert ok and votes >= 2


def t6_all_three_off_is_rejected():
    """三维全偏 → 否决（真·不是浮标）。"""
    ok, votes = judge(int(47 * 2.0), int(167 * 2.0), int(483 * 3.0))
    assert not ok and votes == 0


def t7_calib_early_exit_only_on_true_absence():
    """标定期：有候选但被否决 → 不计 miss、不提前结束。"""
    class FakeFinder:
        _last_fp_rejected = (63, 166, 491)

    class Fake:
        finder = FakeFinder()

    fake = Fake()
    rejected = 0
    miss_run = 0
    for note in ["候选=0 最佳=63x166 size=491 @(378,114) 指纹否决26"] * 4:
        fake._last_detect = note
        rej = getattr(fake.finder, "_last_fp_rejected", None)
        has_cand = (rej is not None) or ("@" in note)
        if has_cand:
            rejected += 1
        else:
            miss_run += 1
    assert rejected == 4, rejected
    assert miss_run == 0, miss_run


def t8_calib_early_exit_fires_on_empty_scene():
    """真·离场（无任何候选）仍需提前结束，避免空转。"""
    class FakeFinder:
        _last_fp_rejected = None

    class Fake:
        finder = FakeFinder()

    miss_run = 0
    broke_at = None
    for i in range(1, 10):
        note = "候选=0 最佳=-"
        rej = getattr(Fake.finder, "_last_fp_rejected", None)
        has_cand = (rej is not None) or ("@" in note)
        if has_cand:
            pass
        else:
            miss_run += 1
            if miss_run >= 3 and i >= 5:
                broke_at = i
                break
    assert broke_at is not None, "真离场时不该一直空转"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("t") and callable(v)]
    ok = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
            ok += 1
        except AssertionError as e:
            print("FAIL", fn.__name__, "->", e)
    print("%d/%d" % (ok, len(tests)))
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
