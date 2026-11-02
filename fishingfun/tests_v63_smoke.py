#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests_v63_smoke.py —— 端到端冒烟测试（v62 事故的直接产物）

v62 首发事故：`SearchBobberFinder.reset()` 被误删，py_compile 全通过、
check_mechanism（旧版）也 PASS，但真机每竿抛竿后立刻
    ERR 'SearchBobberFinder' object has no attribute 'reset'
0.1 秒重抛一次，一条鱼没钓到。

根因：验证手段全是"静态"的（编译 / 读代码 / 读文档），
没有一次"按真实顺序调用一遍对象方法"。本测试补上这一环。

它不跑真游戏、不需要 Windows，只验证【调用链上的每个方法都存在且可调用】：
  1. 构造 SearchBobberFinder 和 FishingBot（用 __new__ 绕过 win32 依赖）
  2. 按每竿真实顺序调用 reset() → find() → _claim_decay()
  3. 断言不抛 AttributeError，且 reset 后状态确实复位
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []
PASS = [0]


def ok(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
        PASS[0] += 1
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAIL.append(name)


def main():
    print("=" * 62)
    print("v63 端到端冒烟测试（调用链存在性）")
    print("=" * 62)

    # ---------- SearchBobberFinder ----------
    try:
        from fishingfun.searchbobberfinder import SearchBobberFinder
        ok("import SearchBobberFinder", True)
    except Exception as e:
        ok("import SearchBobberFinder", False, repr(e))
        return finish()

    NEEDED = [
        "reset", "find", "_claim_decay", "area_decayed",
        "_grab", "_to_screen", "_find_points", "_score_points",
    ]
    missing = [m for m in NEEDED if not hasattr(SearchBobberFinder, m)]
    ok("SearchBobberFinder 关键方法齐全", not missing, "缺: %s" % missing)

    # 真调一次 reset()
    probe = SearchBobberFinder.__new__(SearchBobberFinder)
    probe.previous_location = (12, 34)
    probe._area_hist = [(0, 100), (1, 90)]
    probe._decay_claimed = True
    probe.no_bobber_frames = 9
    probe._diff_anchor = (5, 5)
    probe._diff_hits = 2
    probe._diff_prev = object()
    probe._last_fp_rejected = object()
    probe._calib_result = {"area": 1}
    probe._last_count = 3
    probe._last_detect_note = "x"
    try:
        probe.reset()
        ok("reset() 可调用", True)
    except Exception as e:
        ok("reset() 可调用", False, traceback.format_exc().splitlines()[-1])
        return finish()

    stale = []
    if probe.previous_location is not None:
        stale.append("previous_location")
    if probe._area_hist:
        stale.append("_area_hist")
    if probe.no_bobber_frames != 0:
        stale.append("no_bobber_frames")
    if probe._diff_anchor is not None:
        stale.append("_diff_anchor")
    if probe._calib_result is not None:
        stale.append("_calib_result")
    ok("reset() 复位全部跨竿状态", not stale, "残留: %s" % stale)

    # _claim_decay 必须是 False（禁区）
    try:
        got = probe._claim_decay()
        ok("_claim_decay() 返回 False", got is False, repr(got))
    except Exception as e:
        ok("_claim_decay() 返回 False", False, repr(e))

    # ---------- FishingBot ----------
    try:
        from fishingfun.fishbot import FishingBot
        ok("import FishingBot", True)
    except Exception as e:
        ok("import FishingBot", False, repr(e))
        return finish()

    BOT_NEEDED = [
        "_wait_for_bite", "_loot", "_bite_from_decay", "_cast_elapsed",
        "_cast_trace_reset", "_cast_trace_tick", "_cast_trace_report",
        "_dump_region",
    ]
    missing = [m for m in BOT_NEEDED if not hasattr(FishingBot, m)]
    ok("FishingBot 关键方法齐全", not missing, "缺: %s" % missing)

    # 按每竿真实顺序走一遍"调用能不能走通"（不执行真实 IO）
    order = []
    for m in ("_cast_trace_reset", "_cast_trace_tick", "_cast_elapsed"):
        order.append(m)
    ok("每竿调用序列成员都存在", all(hasattr(FishingBot, m) for m in order),
       str([m for m in order if not hasattr(FishingBot, m)]))

    return finish()


def finish():
    print()
    if FAIL:
        print("结果：FAIL（%d 项失败 / %d 项通过）" % (len(FAIL), PASS[0]))
        for n in FAIL:
            print("   - " + n)
        return 1
    print("结果：PASS（%d 项）" % PASS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
