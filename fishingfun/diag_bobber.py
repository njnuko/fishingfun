"""诊断咬钩等待期为什么丢浮标

用法（管理员 CMD）：
    python diag_bobber.py

它不进钓鱼循环，只做：
    1. 持续截屏 5 秒，每帧跑检测，打印命中点、面积、bbox、长宽比
    2. 把检测到的位置画框存图 diag_N.png，让你肉眼确认框住的是不是鱼漂
    3. 汇总：命中率、位置漂移范围、面积分布

不按任何键，不会干扰你游戏。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PIL import Image

from fishingfun import PixelClassifier, SearchBobberFinder, WowScreen, enable_dpi_awareness
from fishingfun.wowscreen import find_wow_window


def tan_mask_info(img):
    r = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    b = img[:, :, 2].astype(np.int16)
    m = ((r > 140) & (g > 105) & (b > 60) & (b < 175)
         & ((r - b) > 45) & ((g - b) > 15) & (np.abs(r - g) < 80))
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return 0, None, None, None
    return (len(xs),
            (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
            (int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)),
            (int(r[m].mean()), int(g[m].mean()), int(b[m].mean())))


def main():
    enable_dpi_awareness()
    found = find_wow_window()
    if not found:
        print("❌ 没找到游戏窗口")
        return
    hwnd = found[0]

    scr = WowScreen()
    scr._hwnd = hwnd
    print(scr.describe())
    print()

    finder = SearchBobberFinder(PixelClassifier("tan"), logger=lambda *a: None)
    finder.grabber = scr
    finder.tan_min_area = 40          # 诊断期放宽，先看真实分布

    print("持续 5 秒采样（不按键）。请让游戏停在「已抛竿、浮标在水面」的状态。")
    print(f"{'帧':>3} {'检测点':>14} {'像素':>6} {'bbox w×h':>10} {'ratio':>6}  平均RGB")
    print("-" * 74)

    t0 = time.time()
    n = 0
    hits = 0
    pts = []
    frames = []
    while time.time() - t0 < 5.0:
        n += 1
        img = scr.grab()
        cnt, bbox, wh, rgb = tan_mask_info(img)
        pos = finder.find(img)

        if pos is not None:
            hits += 1
            pts.append(pos)
            l, t, _, _ = scr.search_area()
            bx = pos[0] - l
            by = pos[1] - t
            ratio = (wh[0] / wh[1]) if wh and wh[1] else 0
            print(f"{n:>3} ({pos[0]:>4},{pos[1]:>4}) {cnt:>6} "
                  f"{wh[0]:>4}x{wh[1]:<4} {ratio:>6.2f}  {rgb}")
            if len(frames) < 3:
                frames.append((img.copy(), bx, by))
        else:
            ratio = (wh[0] / wh[1]) if wh and wh[1] else 0
            tag = "  ← 被校验过滤" if cnt >= 40 else "  ← 无 tan 像素"
            print(f"{n:>3} {'None':>14} {cnt:>6} "
                  f"{(f'{wh[0]}x{wh[1]}' if wh else '-'):>10} {ratio:>6.2f}{tag}")
        time.sleep(0.06)

    print()
    print("=" * 74)
    print(f"总帧数 {n}，命中 {hits}，命中率 {hits/n*100:.0f}%")
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        print(f"位置范围 x {min(xs)}..{max(xs)}  y {min(ys)}..{max(ys)}")
        print(f"抖动幅度 Δx={max(xs)-min(xs)}  Δy={max(ys)-min(ys)}")
        if max(ys) - min(ys) > 30:
            print("⚠️ 位置抖动超过 30px —— 说明检测到的可能不是同一个物体")

    for i, (img, bx, by) in enumerate(frames, 1):
        out = img.copy()
        y0, y1 = max(by - 18, 0), min(by + 18, out.shape[0])
        x0, x1 = max(bx - 18, 0), min(bx + 18, out.shape[1])
        out[y0:y0+2, x0:x1] = [255, 0, 0]
        out[y1-2:y1, x0:x1] = [255, 0, 0]
        out[y0:y1, x0:x0+2] = [255, 0, 0]
        out[y0:y1, x1-2:x1] = [255, 0, 0]
        fn = f"diag_{i}.png"
        Image.fromarray(out).save(fn)
        cy0, cy1 = max(by-60, 0), min(by+60, img.shape[0])
        cx0, cx1 = max(bx-60, 0), min(bx+60, img.shape[1])
        if cy1 > cy0 and cx1 > cx0:
            Image.fromarray(img[cy0:cy1, cx0:cx1]).resize(
                ((cx1-cx0)*3, (cy1-cy0)*3), Image.LANCZOS).save(f"diag_zoom_{i}.png")
    print(f"已存 {len(frames)} 张 diag_N.png（红框标注）和 diag_zoom_N.png（3倍放大）")


if __name__ == "__main__":
    main()
