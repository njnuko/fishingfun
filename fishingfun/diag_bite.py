"""全自动咬钩诊断 —— 回车里运行，全程无人值守

用法：
    python diag_bite.py

流程：
    回车确认 → 等 1 秒 → 自动切到魔兽世界窗口并最大化 → 按 4 抛竿
    → 连续采集 17 秒（这段时间内必定咬钩）→ 打印诊断结论

日志：
    终端同步显示，同时写入 logs/diag_YYYYmmdd_HHMMSS.log
    每帧一行（帧号/位置/水面基准/下沉/连续/耗时），
    逐帧结构化数据另存 diag_bite_track.json
"""
import json
import logging
import os
import sys
import time
from datetime import datetime

import numpy as np

from fishingfun import (PixelClassifier, SearchBobberFinder, PositionBiteWatcher,
                        WowScreen, WowProcess)
from fishingfun.vkeys import key_to_vk, vk_name

try:
    import ctypes
    _user32 = ctypes.windll.user32
except Exception:                                  # 非 Windows
    _user32 = None


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint),
                ("flags", ctypes.c_uint),
                ("showCmd", ctypes.c_uint),
                ("ptMinPosition", _POINT),
                ("ptMaxPosition", _POINT),
                ("rcNormalPosition", _RECT)]


# ---------------- 可调参数 ----------------
CAST_KEY = "4"          # 抛竿按键
STRIKE = 5              # 判定阈值（相对水面基准下沉多少像素）
CONFIRM = 1             # 连续帧数（实测咬钩只持续 1 帧，必须为 1）
VANISH = 4              # 连续多少帧检测不到浮标算咬钩（鱼拖入水下）
DURATION = 17.0         # 采集秒数（17 秒内必定咬钩）
MAX_DURATION = 20.0     # 日志最长采集（17 秒没咬钩则补采到 20 秒）
PRECAST_WAIT = 1.0      # 切窗口后等多久再按键
LOG_DIR = "logs"        # 日志目录
# -----------------------------------------

_log_path = None        # 全局，结束时打印


def setup_logging():
    """终端 + 文件双输出。文件用 UTF-8，Windows 记事本可读。"""
    global _log_path
    os.makedirs(LOG_DIR, exist_ok=True)
    _log_path = os.path.join(
        LOG_DIR, "diag_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")

    lg = logging.getLogger("diag")
    lg.setLevel(logging.DEBUG)
    lg.handlers.clear()

    fh = logging.FileHandler(_log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"))
    lg.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)          # 终端只显示关键行，逐帧细节全在文件里
    ch.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(ch)
    return lg


log = logging.getLogger("diag")


def activate(hwnd):
    """切到游戏窗口并最大化（SetForegroundWindow 对前台进程外的窗口常被拒，
    用 AttachThreadInput 绕过前台锁）。"""
    if not hwnd or _user32 is None:
        log.warning("⚠️ 未找到游戏窗口或非 Windows，跳过激活")
        return False

    fg_thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    tgt_thread = _user32.GetWindowThreadProcessId(hwnd, None)
    log.debug("线程附着: 前台=0x%X 目标=0x%X 需附着=%s",
              fg_thread, tgt_thread, fg_thread != tgt_thread)
    if fg_thread != tgt_thread:
        _user32.AttachThreadInput(fg_thread, tgt_thread, True)
    try:
        _user32.ShowWindow(hwnd, 9)                 # SW_RESTORE 先取消最小化
        _user32.ShowWindow(hwnd, 3)                 # SW_MAXIMIZE 最大化
        ok = _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
    finally:
        if fg_thread != tgt_thread:
            _user32.AttachThreadInput(fg_thread, tgt_thread, False)

    time.sleep(0.8)                                 # 等最大化动画结束
    fg = _user32.GetForegroundWindow()
    good = (fg == hwnd)
    log.info("前台窗口: 0x%X %s", fg, "✅游戏" if good else "❌ 不是游戏（按键可能无效）")

    if ok:
        wp = _WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
        if _user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            show = wp.showCmd
            if show == 3:
                log.info("窗口状态: ✅ 已最大化")
            else:
                log.warning("窗口状态: ⚠️ showCmd=%d（未最大化，可能被游戏全屏模式限制）",
                            show)
        r = _RECT()
        if _user32.GetClientRect(hwnd, ctypes.byref(r)):
            log.debug("客户区矩形: %dx%d", r.right - r.left, r.bottom - r.top)
    return good


def main():
    global log
    log = setup_logging()

    log.info("=" * 66)
    log.info("咬钩诊断 diag_bite.py 启动")
    log.info("Python %s / 平台 %s", sys.version.split()[0], sys.platform)
    log.info("日志文件: %s", os.path.abspath(_log_path))
    log.info("=" * 66)

    screen = WowScreen()
    desc = screen.describe()
    log.info(desc)

    hwnd = getattr(screen, "_hwnd", None)
    if hwnd:
        log.info("游戏窗口句柄: 0x%X", hwnd)
    else:
        log.error("⚠️ 没找到魔兽世界窗口！")
        log.error("   → 请确认游戏已启动，且窗口标题含「魔兽世界」")
        log.error("   → 将退化为全屏截图 + 不自动切窗口（按键仍会发出）")

    cast_vk = key_to_vk(CAST_KEY)
    log.info("抛竿键: '%s' → VK=0x%02X (%s)", CAST_KEY, cast_vk, vk_name(cast_vk))
    log.info("判定参数: strike=%dpx  连续确认=%d帧  消失判定=%d帧  采集=%.0fs",
             STRIKE, CONFIRM, VANISH, DURATION)
    log.info("参数明细: CAST_KEY=%s STRIKE=%d CONFIRM=%d VANISH=%d "
             "DURATION=%.1f PRECAST_WAIT=%.1f",
             CAST_KEY, STRIKE, CONFIRM, VANISH, DURATION, PRECAST_WAIT)

    log.info("-" * 66)
    input("👉 请先回到船舱、关闭所有弹窗，然后按【回车】开始"
          "（之后会自动切窗口并抛竿，请不要动鼠标键盘）...")
    log.info("1 秒后切换到魔兽世界窗口并自动抛竿...")
    time.sleep(PRECAST_WAIT)

    t_act = time.time()
    act_ok = activate(hwnd)
    log.info("激活结果: %s  耗时 %.0fms", "成功" if act_ok else "失败",
             (time.time() - t_act) * 1000)

    log.info("重新检测截图区域...")
    desc2 = screen.describe()
    log.info(desc2)
    l, t, cw, ch = screen.search_area()
    log.info("截图区: 左=%d 上=%d 宽=%d 高=%d", l, t, cw, ch)
    if cw < 900:
        log.warning("⚠️ 截图区只有 %dx%d，窗口可能没真正最大化，浮标会很小", cw, ch)

    wow = WowProcess(hwnd=hwnd, log=lambda *a: log.debug("  [按键] " + (a[0] % a[1:])),
                     key_mode="scancode")
    t_cast = time.time()
    log.info("抛竿: 按下 %s (VK=0x%02X)  t=0ms", CAST_KEY, cast_vk)
    wow.press_key(cast_vk)
    log.info("抛竿完成 耗时 %.0fms", (time.time() - t_cast) * 1000)

    finder = SearchBobberFinder(PixelClassifier("tan"))

    watcher = None
    track = []
    t0 = time.time()
    nframe = hit = miss = 0
    max_delta = 0
    max_delta_frame = None
    min_delta = 0
    cur_vanish = max_vanish = 0
    bite_at = None
    bite_reason = None
    bite_detail = None
    first_pos = None
    rec = []
    fps_samples = []

    log.info("-" * 66)
    log.info("%5s %6s %18s %8s %6s %6s %8s",
             "帧", "时刻ms", "位置", "水面基准", "下沉", "连续", "本帧ms")
    log.info("-" * 66)

    while time.time() - t0 < MAX_DURATION:
        tf = time.time()
        bmp = screen.grab()
        nframe += 1
        pos = finder.find(bmp)
        elapsed = (time.time() - t0) * 1000
        t_frame = (time.time() - tf) * 1000
        fps_samples.append(t_frame)

        if pos is None:
            miss += 1
            cur_vanish += 1
            max_vanish = max(max_vanish, cur_vanish)
            if watcher is None:
                log.debug("%5d %6.0f %18s %8s %6s %6s %8.1f   ← 还没锁定浮标（第%d次未命中）",
                          nframe, elapsed, "-", "-", "-", "-", t_frame, cur_vanish)
                continue
            fired = watcher.is_bite(None)
            log.debug("%5d %6.0f %18s %8s %6s %6d %8.1f   ← 浮标消失",
                      nframe, elapsed, "❌未检测到", "-", "-", cur_vanish, t_frame)
            rec.append([nframe, round(elapsed), None, None, None, cur_vanish,
                        round(t_frame, 1), "miss"])
            if fired:
                bite_at, bite_reason = nframe, "浮标连续消失%d帧" % cur_vanish
                log.info("")
                log.info("🎣 判定咬钩！（浮标消失 %d 帧，第 %d 帧，t=%.0fms）",
                         cur_vanish, nframe, elapsed)
                break
            continue

        cur_vanish = 0
        hit += 1
        track.append([nframe, pos[0], pos[1]])

        if watcher is None:
            first_pos = pos
            watcher = PositionBiteWatcher(STRIKE, mode="robust",
                                          confirm_frames=CONFIRM,
                                          vanish_frames=VANISH)
            watcher.reset(pos)
            log.info("%5d %6.0f %18s %8d %6s %6s %8.1f   ★ 锁定浮标，建立水面基准",
                      nframe, elapsed, str(pos), pos[1], "-", "-", t_frame)
            rec.append([nframe, round(elapsed), pos[0], pos[1], pos[1], 0,
                        round(t_frame, 1), "lock"])
            continue

        fired = watcher.is_bite(pos)
        surface_y = min(watcher._recent) if watcher._recent else pos[1]
        delta = pos[1] - surface_y
        rec.append([nframe, round(elapsed), pos[0], pos[1], surface_y, delta,
                    round(t_frame, 1), "bite" if fired else "frame"])

        if delta > max_delta:
            max_delta, max_delta_frame = delta, nframe
        if delta < min_delta:
            min_delta = delta

        # 每帧都写文件（DEBUG），终端只显示异常帧
        over17 = elapsed > DURATION * 1000
        log.debug("%5d %6.0f %18s %8d %+6d %6d %8.1f%s",
                  nframe, elapsed, str(pos), surface_y, delta,
                  watcher._streak, t_frame, "  [补采]" if over17 else "")
        if delta >= STRIKE or watcher._streak > 0:
            log.warning("%5d %6.0f %18s %8d %+6d %6d %8.1f   ← 超阈值/计数中",
                        nframe, elapsed, str(pos), surface_y, delta,
                        watcher._streak, t_frame)

        if fired:
            bite_at, bite_reason = nframe, "下沉%dpx" % delta
            bite_detail = (nframe, pos[1], surface_y, delta)
            log.info("")
            log.info("🎣 判定咬钩！（下沉 %dpx，第 %d 帧，t=%.0fms）",
                     delta, nframe, elapsed)
            break

        time.sleep(0.02)

    dt = time.time() - t0
    fps = nframe / max(dt, 1e-9)
    avg_frame = sum(fps_samples) / max(len(fps_samples), 1)

    log.info("")
    log.info("=" * 66)
    log.info("采集 %.1fs  总帧 %d  命中 %d  未命中 %d  命中率 %.0f%%  %.1f fps",
             dt, nframe, hit, miss, hit * 100.0 / max(nframe, 1), fps)
    log.info("单帧耗时: 平均 %.1fms  最大 %.1fms", avg_frame, max(fps_samples or [0]))
    if first_pos:
        log.info("首次锁定浮标: %s", first_pos)
    log.info("最大单帧下沉: %dpx (第 %s 帧)   strike=%d",
             max_delta, max_delta_frame, STRIKE)
    log.info("最深上浮: %dpx   最长连续消失: %d 帧   vanish_frames=%d",
             min_delta, max_vanish, VANISH)
    log.info("截图区最终: %dx%d@%d,%d", cw, ch, l, t)

    log.info("")
    log.info("=" * 66)
    if bite_at:
        log.info("✅ 咬钩成功判定 —— 第 %d 帧，%s", bite_at, bite_reason)
        if bite_detail:
            log.info("   位置 y=%d  水面基准=%d  下沉=%dpx",
                     bite_detail[1], bite_detail[2], bite_detail[3])
        if max_delta > STRIKE:
            log.info("   建议：--strike %d（当前 %d，实测最深 %dpx，可略降误报）",
                     min(max_delta, STRIKE + 4), STRIKE, max_delta)
    else:
        log.error("❌ %.0f 秒内未判定咬钩（前 %.0f 秒必咬钩窗口已过）", dt, DURATION)
        if max_delta >= 3:
            log.error("   实测最大下沉 %dpx → 建议 --strike %d", max_delta, max_delta)
        elif max_vanish >= 2:
            log.error("   实测最长消失 %d 帧 → 建议 --vanish-frames %d",
                      max_vanish, max(2, max_vanish))
        else:
            log.error("   浮标几乎没动（最大下沉 %dpx），可能：", max_delta)
            log.error("     · 抛竿没成功（检查是否在钓鱼状态/技能冷却）")
            log.error("     · 浮标检测到的不是真浮标")
        if track:
            ys = [p[2] for p in track]
            log.error("   检测点 y 范围 %d~%d (跨度 %dpx)",
                      min(ys), max(ys), max(ys) - min(ys))

    if track:
        out_json = "diag_bite_track.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump({"config": {"strike": STRIKE, "confirm": CONFIRM,
                                  "vanish": VANISH, "duration": DURATION,
                                  "cast_key": CAST_KEY, "log": _log_path},
                       "screen": {"client_w": cw, "client_h": ch,
                                  "left": l, "top": t},
                       "track": track, "detail": rec}, f,
                      ensure_ascii=False, indent=1)
        log.info("逐帧明细: %s (%d 帧)", os.path.abspath(out_json), len(rec))
    log.info("日志文件: %s", os.path.abspath(_log_path))
    log.info("=" * 66)


if __name__ == "__main__":
    main()
