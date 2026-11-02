# -*- coding: utf-8 -*-
"""用真机日志逐帧重放 sinkcalib.run() 的判定逻辑，对比 v49 / v50。

v49 的规则：y is None → miss_run+=1；miss_run>=3 且 i>=5 → break
v50 的规则：y is None 时看有没有候选；有候选 → 不计 miss；无候选 → 计 miss

真机序列来自 2026-11-02 09:27:52~09:27:57（720x529）。
"""
# (i, t, y, area, note)
SEQ = [
    (1, 2.8, 114, 485, "候选=1 最佳=47x166 size=485"),
    (6, 5.1, None, 0, "候选=0 最佳=63x166 size=491 @(378,114) 指纹否决26"),
    (7, 6.0, None, 0, "候选=0 最佳=38x166 size=482 @(378,114) 指纹否决29"),
    (8, 7.0, None, 0, "候选=0 最佳=62x166 size=461 @(378,114) 指纹否决25"),
]

def run_v49(seq, total_frames=17):
    miss_run = 0
    break_at = None
    for (i, t, y, area, note) in seq:
        if y is None:
            miss_run += 1
            if miss_run >= 3 and i >= 5:
                break_at = (i, t)
                break
        else:
            miss_run = 0
    return break_at

def run_v50(seq, total_frames=17):
    miss_run = 0
    rejected = 0
    break_at = None
    for (i, t, y, area, note) in seq:
        if y is None:
            has_cand = ("@(" in note) or ("size=" in note and "最佳=-" not in note)
            if has_cand:
                rejected += 1
            else:
                miss_run += 1
                if miss_run >= 3 and i >= 5:
                    break_at = (i, t)
                    break
        else:
            miss_run = 0
    return break_at, rejected

v49 = run_v49(SEQ)
v50, rej = run_v50(SEQ)
print("v49 提前结束于:", v49, "→ 窗口时长 %.1fs（应为 17s）" % (v49[1]-SEQ[0][1]+0 if v49 else 17))
print("v50 提前结束于:", v50, "| 被否决帧:", rej)
assert v49 == (8, 7.0), v49
assert v50 is None, "v50 不该提前结束，实际 %r" % (v50,)
assert rej == 3, rej
print("PASS 真机日志重放：v49 在 t=7.0s 断（4.3s），v50 跑满 17s")
