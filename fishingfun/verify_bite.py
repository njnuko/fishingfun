"""用三次实测采样回放验证咬钩判定参数。

不需要游戏，纯离线。用法：
    python verify_bite.py

三次采样来自真实游戏画面（同一浮标，y 坐标逐帧记录）。
已知：第1次第17点(+5px)、第2次第15点(+8px)、第3次第17点(+6px) 为真咬钩；
      第2次第5点(+5px)、第16点(+5px) 为水面波动误报。
"""
import sys

from fishingfun import PositionBiteWatcher

# 实测序列（y 坐标）
SEQ1 = [627,629,628,627,625,627,628,627,628,629,628,627,625,626,627,628,627,
        630,626,629,628,627,628,629,628,627,626,625,626,627,628,627,628,629,
        628,627,626,627,628,627]
SEQ2 = [683,686,683,685,682,687,685,683,686,684,683,683,682,685,683,690,687,
        683,686,684,683]
SEQ3 = [776,778,777,776,777,776,777,778,777,776,777,776,777,778,777,776,777,
        782,778,777,776,777,778,777,776]

# (序列, 真咬钩下标集合)
DATA = [
    ("第1次", SEQ1, {17}),
    ("第2次", SEQ2, {15}),
    ("第3次", SEQ3, {17}),
]


def run(seq, strike, confirm, vanish=99):
    w = PositionBiteWatcher(strike, mode="robust",
                            confirm_frames=confirm, vanish_frames=vanish)
    w.reset((100, seq[0]))
    fires = []
    for i in range(1, len(seq)):
        if w.is_bite((100, seq[i])):
            fires.append(i)
    return fires


def main():
    print("=" * 78)
    print(f"{'参数':<22}{'第1次(真@17)':<20}{'第2次(真@15)':<20}{'第3次(真@17)':<20}")
    print("=" * 78)

    best = []
    for strike in (4, 5, 6, 7):
        for confirm in (1, 2, 3):
            row = []
            tp = fp = fn = 0
            for name, seq, truth in DATA:
                fires = run(seq, strike, confirm)
                hit = set(fires) & truth
                bad = set(fires) - truth
                tp += len(hit)
                fp += len(bad)
                fn += len(truth) - len(hit)
                mark = "".join([
                    "✅" if hit else "❌",
                    f"+{len(bad)}误" if bad else "",
                ])
                row.append(mark)
            score = f"TP={tp} FP={fp} FN={fn}"
            print(f"strike={strike} 连续={confirm:<14}"
                  f"{row[0]:<20}{row[1]:<20}{row[2]:<20} {score}")
            best.append((tp - fp * 2 - fn, strike, confirm, tp, fp, fn))

    best.sort(reverse=True)
    b = best[0]
    print("\n" + "=" * 78)
    print(f"推荐: --strike {b[1]} --confirm-frames {b[2]}")
    print(f"      命中 {b[3]}/3  误报 {b[4]}  漏检 {b[5]}")
    print("=" * 78)

    zero_fp = [x for x in best if x[4] == 0]
    if zero_fp:
        z = zero_fp[0]
        print(f"零误报最优: --strike {z[1]} --confirm-frames {z[2]}  "
              f"命中 {z[3]}/3  漏检 {z[5]}")
    print("\n说明：第1次咬钩只有 +5px，第2次误报也正好 +5px，")
    print("      信号与噪声在 719 宽画面下重叠。窗口最大化可拉开这个差距。")


if __name__ == "__main__":
    main()
