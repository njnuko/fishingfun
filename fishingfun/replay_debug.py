"""FishingFun 离线时序重放测试 —— 把 debug 包里的区域帧按时间轴喂给主程序。

目的
----
真机跑一竿要 20 秒以上，还要等鱼咬钩，验证一个 bug 修复成本极高。
debug.zip 里已经落了「锚定 / 跟踪逐秒 / 咬钩 / 重抛」四类区域帧，
文件名里带截屏区尺寸、原点、相对抛竿时刻、当帧检测结果。

本脚本做三件事：
  1. 解析 debug/ 里所有 `720x436@360,210` 的区域帧，按文件名里的时间
     和竿号排成时间轴；
  2. 用 TimeLineGrabber 冒充 WowScreen.grab()，按「虚拟时钟」返回该时刻
     应显示的那一帧；时钟只在抓帧时前进，因此不需要真的 sleep，
     20 秒的竿几毫秒就能跑完；
  3. 真正调用 FishingBot.start() 主流程（SearchBobberFinder / PositionBiteWatcher
     / _watch / _wait_for_bite / 咬钩判定 / 拾取），只把
       · 屏幕抓取  -> TimeLineGrabber
       · 鼠标键盘  -> FakeWowProcess（记录所有按键/点击/鼠标移动）
     换掉，并把日志逐行打出来。

用法
----
    python replay_debug.py <debug目录> [--min-bite 2.0]
    python replay_debug.py debug --verbose

判定
----
    结束时打印：
      · 每竿是否有锚点、是否判咬钩、是否拾取
      · 鼠标是否每竿抛竿前都移到边缘
      · 从未锁定浮标的竿数（这是 v54 要消灭的）
    退出码 0 = 完整跑完；1 = 有竿「从未锁定浮标」或抛异常。
"""

import argparse
import os
import re
import sys
import time
import traceback
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


# ---------------------------------------------------------------- BMP 读取
def load_bmp(path):
    """读 24/32 位 BMP 为 HxWx3 RGB uint8。debug 帧都是 BMP。"""
    import struct
    with open(path, "rb") as f:
        d = f.read()
    off = struct.unpack_from("<I", d, 10)[0]
    w = struct.unpack_from("<i", d, 18)[0]
    h = struct.unpack_from("<i", d, 22)[0]
    flip = h > 0
    h = abs(h)
    bpp = struct.unpack_from("<H", d, 28)[0]
    nbytes = bpp // 8
    row = ((w * bpp // 8) + 3) // 4 * 4
    buf = np.frombuffer(d, np.uint8, count=row * h, offset=off).reshape(h, row)
    img = buf[:, : w * nbytes].reshape(h, w, nbytes)[:, :, :3]
    if flip:
        img = img[::-1]
    return np.ascontiguousarray(img[:, :, ::-1])


# ---------------------------------------------------------------- 帧索引
# 区域帧文件名形如：
#   c001_锚定_720x436@360,210_t17.8s_锚点713_406_104841.bmp
#   c002_跟踪_720x436@360,210_t1.1s_检测-1_-1_104856.bmp
#   c002_重抛_720x436@360,210_t5.5s_找锚点失败_104901.bmp
#   c001_咬钩_720x436@360,210_t20.2s_锚点713_406_当前消失_104844.bmp
REGION_RE = re.compile(
    r"^c(\d+)_(?P<kind>[^_]+)_"
    r"(?P<w>\d+)x(?P<h>\d+)@(?P<ox>\d+),(?P<oy>\d+)_"
    r"t(?P<t>[\d.]+)s_(?P<rest>.*)_(?P<wall>\d{6})\.bmp$"
)
# 整屏帧：帧01_全屏_1440x900_104826.bmp
FULL_RE = re.compile(r"^帧(\d+)_全屏_(\d+)x(\d+)_(\d{6})\.bmp$")


class Cast:
    """一竿的帧序列。"""

    def __init__(self, no):
        self.no = no
        self.region = None          # (w, h, ox, oy)
        self.frames = []            # [(t, kind, rest, path)]
        self.finish_t = None        # 下竿开始时刻（= 本竿时长上界）

    def __repr__(self):
        return "<Cast #%d %d帧 %s>" % (self.no, len(self.frames), self.region)


def scan_debug_dir(root):
    """扫描 debug 目录，返回 (sorted casts, full_frames, origin)。"""
    cands = [root]
    # 兼容用户直接解压 zip 到子目录的情况
    for name in os.listdir(root) if os.path.isdir(root) else []:
        p = os.path.join(root, name, "debug")
        if os.path.isdir(p):
            cands.append(p)
    files = []
    for d in cands:
        if os.path.isdir(d):
            files = [os.path.join(d, f) for f in sorted(os.listdir(d))]
            if any(f.lower().endswith(".bmp") for f in files):
                break

    casts = {}
    fulls = []
    origin = None
    skipped = 0
    for path in files:
        base = os.path.basename(path)
        if not base.lower().endswith(".bmp"):
            continue
        m = REGION_RE.match(base)
        if m:
            no = int(m.group(1))
            c = casts.setdefault(no, Cast(no))
            region = (int(m.group("w")), int(m.group("h")),
                      int(m.group("ox")), int(m.group("oy")))
            if c.region is None:
                c.region = region
            origin = origin or region[2:]
            c.frames.append((float(m.group("t")), m.group("kind"),
                             m.group("rest"), path))
            continue
        mf = FULL_RE.match(base)
        if mf:
            fulls.append((int(mf.group(1)), path))
            continue
        skipped += 1

    out = [casts[k] for k in sorted(casts)]
    for i, c in enumerate(out):
        c.frames.sort(key=lambda z: z[0])
        if i + 1 < len(out):
            c.finish_t = out[i + 1].frames[0][0] if out[i + 1].frames else 22.0
        else:
            c.finish_t = (c.frames[-1][0] + 3.0) if c.frames else 22.0
    return out, fulls, origin, skipped


# ---------------------------------------------------------------- 假硬件
class TimeLineGrabber:
    """冒充 WowScreen：按虚拟时钟返回该时刻应显示的区域帧。

    关键点：不发 sleep。时钟只在 grab() 被调用时推进 fixed_step，
    这样 20 秒的竿几毫秒就跑完，且行为与真机「每帧间隔约 step」一致。
    """

    def __init__(self, cast, step=0.05, log=None, idle_frame_idx=None):
        self.cast = cast
        self.step = step
        self.log = log or (lambda *a, **k: None)
        self.clock = 0.0
        self.cursor_park = (0, 0)     # 由 FakeWowProcess 同步
        self._cache = {}
        self.grab_count = 0
        # idle 帧：用于「还没到第一帧时间点」时的兜底画面。
        # 取该竿最早的一帧（通常就是 t=0.1s 的跟踪帧，浮标还在空中/刚落水）。
        self.idle_path = cast.frames[0][3] if cast.frames else None

    def _frame_at(self, t):
        """返回 t 时刻应显示的那一帧路径（最近的前一帧）。"""
        chosen = None
        for ft, kind, rest, path in self.cast.frames:
            if ft <= t + 1e-6:
                chosen = path
            else:
                break
        return chosen or self.idle_path

    def grab(self):
        self.grab_count += 1
        t = self.clock
        self.clock += self.step
        path = self._frame_at(t)
        if path is None:
            # 没有可用帧：返回纯黑，模拟「画面全暗」
            w, h = (self.cast.region[0], self.cast.region[1]) \
                if self.cast.region else (720, 436)
            return np.zeros((h, w, 3), np.uint8)
        img = self._cache.get(path)
        if img is None:
            img = load_bmp(path)
            self._cache[path] = img
        return img

    # WowScreen 接口
    def to_screen(self, x, y):
        ox, oy = (self.cast.region[2], self.cast.region[3]) \
            if self.cast.region else (0, 0)
        return (int(x) + ox, int(y) + oy)

    def screen_size(self):
        return (1440, 900)

    def arm_area(self):
        w, h = (self.cast.region[0], self.cast.region[1]) \
            if self.cast.region else (720, 436)
        return (self.cast.region[2], self.cast.region[3], w, h) \
            if self.cast.region else (360, 210, w, h)

    def search_area(self):
        return self.arm_area()

    def describe(self):
        return "TimeLineGrabber 竿#%d %d帧" % (self.cast.no, len(self.cast.frames))


class FakeWowProcess:
    """冒充 WowProcess：不碰真实键鼠，只记录动作。

    同时维护一个「光标位置」，供 park_cursor_verified() 回读校验
    逻辑照常走（这正是 v54 要测的东西）。
    """

    def __init__(self, log=None):
        self.log = log or (lambda *a, **k: None)
        self.actions = []
        self.cursor = (720, 450)          # 初始在画面中间（真机就是这样）
        self.park_calls = 0
        self.park_ok_calls = 0
        self.casts = 0                    # press_key(cast_key) 次数
        self.clicks = []
        self._cast_vk = 0x34
        self._park_before_cast = []
        # 模拟真机 SendInput 被吞：前 N 竿的第一次移动不生效，靠重试兜底
        self.park_drop_first = False
        self._park_results = []

    # --- 键鼠 ---
    def press_key(self, vk, *a, **k):
        self.actions.append(("press", vk))
        if vk == self._cast_vk:
            self.casts += 1
            self._park_before_cast.append(
                self.cursor in ((1439, 899), (1439, 1), (1, 899), (1, 1)))
        return True

    def key_up(self, vk, *a, **k):
        self.actions.append(("up", vk))
        return True

    def _key_down(self, vk, *a, **k):
        return True

    def click(self, x, y, *a, **k):
        self.clicks.append((x, y))
        self.actions.append(("click", x, y))
        return True

    def right_click(self, x, y, *a, **k):
        self.clicks.append((x, y))
        self.actions.append(("rclick", x, y))
        return True

    def right_click_mouse(self, position, *a, **k):
        """拾取时调用的接口：position 是屏幕坐标 (x, y)。"""
        self.clicks.append(tuple(position))
        self.actions.append(("rclick", tuple(position)))
        return True

    def move_cursor(self, x, y):
        self.cursor = (int(x), int(y))

    def get_cursor_pos(self):
        return self.cursor

    # --- 停靠 ---
    def park_cursor(self):
        self.park_calls += 1
        self.cursor = (1439, 899)

    def cursor_is_parked(self, tol=4):
        return (abs(self.cursor[0] - 1439) <= tol
                and abs(self.cursor[1] - 899) <= tol)

    def park_cursor_verified(self, tries=3, tol=4):
        self.park_calls += 1
        # 模拟真机：SendInput 偶尔被吞。用 park_drop_first 开关控制：
        #   True  -> 本次调用第一次移动不生效，必须靠重试兜底（验证 v54 重试）
        #   False -> 一次到位
        ok = True
        for _ in range(max(1, int(tries))):
            if self.park_drop_first:
                self.park_drop_first = False
                self.cursor = (720, 450)      # 被吞，停在画面中间
                continue
            self.cursor = (1439, 899)
            ok = self.cursor_is_parked(tol)
            break
        if ok:
            self.park_ok_calls += 1
        self._park_results.append(ok)
        return (ok, self.cursor)

    # --- 其它 ---
    def _refocus(self, old=None):
        self.park_cursor()

    def is_wow_classic(self, *a, **k):
        return True


class VirtualClock:
    """把 time.monotonic / time.sleep 换成虚拟时钟，避免真 sleep。"""

    def __init__(self, step=0.05):
        self.t = 0.0
        self.step = step
        self._m = time.monotonic
        self._s = time.sleep

    def monotonic(self):
        self.t += self.step
        return self.t

    def sleep(self, sec):
        self.t += max(0.0, float(sec))

    def install(self):
        time.monotonic = self.monotonic
        time.sleep = self.sleep

    def restore(self):
        time.monotonic = self._m
        time.sleep = self._s


# ---------------------------------------------------------------- 主流程
def run_one_cast(bot, cast, args, log):
    """跑一竿，返回结果字典。"""
    grabber = TimeLineGrabber(cast, step=args.step, log=log)
    bot.bobber_finder.grabber = grabber
    wow = bot.wow_process
    wow._cast_vk = bot.cast_key
    before_casts = wow.casts
    before_clicks = len(wow.clicks)

    res = {"cast": cast.no, "anchor": False, "bite": False,
           "clicks": 0, "err": None, "frames_read": 0}

    bot.is_enabled = True
    # ★ 咬钩的唯一可靠观测量：主程序判定真咬钩后必然调 _loot()。
    #   （代码里没有 _bit_this_cast 之类的标志位，判定分支直接 _loot 返回）
    _loot_cnt = {"n": 0}
    _orig_loot = bot._loot

    def _loot_probe(pos, *a, **k):
        _loot_cnt["n"] += 1
        log("★ 判为咬钩 → 拾取 @ %s", pos)
        return _orig_loot(pos, *a, **k)

    bot._loot = _loot_probe

    try:
        # 直接跑一竿的核心：复制 start() 循环体（不含 while）
        # ★ 鼠标停靠这一段必须照抄 —— v54 要验证的就是「每竿抛竿前都在边缘」
        try:
            if hasattr(bot.wow_process, "park_cursor_verified"):
                ok, pos = bot.wow_process.park_cursor_verified()
                log("鼠标已移到屏幕边缘: (%d,%d) %s", pos[0], pos[1],
                    "" if ok else "⚠️ 校验未通过")
            else:
                bot.wow_process.park_cursor()
        except Exception as _pe:
            log("⚠️ 鼠标停靠失败: %s", _pe)
        bot._cast_no += 1
        bot.fishing_event_handler({"Action": "Cast"})
        bot._grab_calib_pre()
        bot._last_cast_t = time.monotonic()
        bot._anchor_locked_this_cast = False
        bot._anchor_color_stable = False
        bot._calib_anchor_local = None
        bot.wow_process.press_key(bot.cast_key)
        bot._arm_screen_area()
        bot._watch(2000)
        bot._tracked_anchor = None
        bot._set_calibrating(True)
        try:
            bot._maybe_calibrate_sink()
        finally:
            bot._set_calibrating(False)
        bot._wait_for_bite()
        res["anchor"] = bool(getattr(bot, "_anchor_locked_this_cast", False))
        res["bite"] = _loot_cnt["n"] > 0
    except Exception as e:
        res["err"] = "%s: %s" % (type(e).__name__, e)
        log("竿#%d 异常: %s", cast.no, res["err"])
        log(traceback.format_exc())
    finally:
        bot._loot = _orig_loot
    bot.is_enabled = False

    res["clicks"] = len(wow.clicks) - before_clicks
    res["frames_read"] = grabber.grab_count
    res["park_ok"] = (
        bool(wow._park_before_cast)
        and all(wow._park_before_cast[-max(1, wow.casts - before_casts):]))
    return res


def main():
    ap = argparse.ArgumentParser(
        description="FishingFun 离线时序重放（用 debug 包的帧喂主程序）")
    ap.add_argument("debug_dir", help="debug 目录（含 cNNN_*.bmp / 帧NN_*.bmp）")
    ap.add_argument("--step", type=float, default=0.05,
                    help="每次抓帧推进的虚拟时长(秒)，默认 0.05 ≈ 20fps")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印全部日志")
    ap.add_argument("--max-casts", type=int, default=0, help="最多跑几竿(0=全部)")
    ap.add_argument("--no-clock", action="store_true",
                    help="不用虚拟时钟（真 sleep，很慢，仅供对照）")
    ap.add_argument("--drop-first-park", type=int, default=0, metavar="N",
                    help="前 N 竿模拟 SendInput 被吞一次，验证重试兜底")
    args = ap.parse_args()

    lines = []

    def log(msg, *a):
        s = (msg % a) if a else str(msg)
        lines.append(s)
        if args.verbose or not s.startswith(("候选", "  ")):
            print(s)

    if not os.path.isdir(args.debug_dir):
        print("目录不存在: %s" % args.debug_dir)
        return 2

    casts, fulls, origin, skipped = scan_debug_dir(args.debug_dir)
    if not casts:
        print("没找到区域帧（cNNN_xxx_WxH@ox,oy_tN.Ns_*.bmp）")
        return 2

    print("=" * 78)
    print("FishingFun 离线时序重放")
    print("  区域帧竿数: %d    整屏帧: %d    跳过: %d" % (len(casts), len(fulls), skipped))
    print("  截屏区原点: %s" % (origin,))
    for c in casts:
        kinds = {}
        for t, k, r, p in c.frames:
            kinds[k] = kinds.get(k, 0) + 1
        print("  #%d  %d帧  %s  %.1fs~%.1fs" % (
            c.no, len(c.frames), kinds,
            c.frames[0][0] if c.frames else 0,
            c.frames[-1][0] if c.frames else 0))
    print("=" * 78)

    # ---- 构建与 run.py 等价的对象图 ----
    from fishingfun import (FishingBot, PixelClassifier, PositionBiteWatcher,
                            SearchBobberFinder)
    from fishingfun.wowscreen import WowScreen

    classifier = PixelClassifier("yellow")
    finder = SearchBobberFinder(
        classifier, logger=log,
        ui_masks=[(0.88, 0.00, 1.00, 1.00),
                  (0.00, 0.00, 0.22, 0.16),
                  (0.00, 0.80, 1.00, 1.00)])
    finder.exclude_side_ratio = 0.0
    finder.exclude_bottom_ratio = 0.0
    finder.bobber_diff_enabled = True
    finder.diff_confirm_frames = 3
    finder.no_bobber_limit = 85
    finder.strict_yellow = True

    watcher = PositionBiteWatcher(0.0, mode="yellow", confirm_frames=2,
                                  vanish_frames=3)
    watcher.auto_strike = True
    watcher.per_cast_anchor_mode = True
    watcher.log = log

    wow = FakeWowProcess(log=log)
    bot = FishingBot(finder, watcher, cast_key=0x34, wow_process=wow,
                     log=log, grabber=None)
    bot.calib_sink = False          # 重放不做 17 秒标定（帧不够）
    wow.park_drop_first = args.drop_first_park > 0
    bot.save_regions = False
    bot.frame_log = False
    bot.trace_seconds = 0.0
    bot.anchor_wait = 4.0
    bot.cast_flight = 1.8
    # abort 回调：重放里不让它停机，只记日志
    finder.abort_cb = lambda n: log("  [abort_cb] 连续 %d 帧找不到浮标" % n)

    clock = None
    if not args.no_clock:
        clock = VirtualClock(step=args.step)
        clock.install()

    results = []
    try:
        todo = casts[:args.max_casts] if args.max_casts else casts
        for c in todo:
            log("\n" + "-" * 70)
            log("▶ 第 %d 竿（%d 帧，%.1fs~%.1fs）",
                c.no, len(c.frames),
                c.frames[0][0] if c.frames else 0,
                c.frames[-1][0] if c.frames else 0)
            results.append(run_one_cast(bot, c, args, log))
    finally:
        if clock:
            clock.restore()

    # ---- 汇总 ----
    print("\n" + "=" * 78)
    print("重放结果")
    print("=" * 78)
    print("%-6s %-8s %-8s %-8s %-8s %s" %
          ("竿", "读到帧", "锚点", "咬钩", "点击", "备注"))
    bad = 0
    for r in results:
        note = r["err"] or ""
        if not r["anchor"]:
            note = (note + " 从未锁定浮标").strip()
            bad += 1
        print("%-6d %-8d %-8s %-8s %-8d %s" % (
            r["cast"], r["frames_read"],
            "✅" if r["anchor"] else "❌",
            "✅" if r["bite"] else "—",
            r["clicks"], note))

    print("\n鼠标停靠: 每次抛竿前都在边缘 = %s | park 调用 %d 次，最终校验通过 %d 次" % (
        "✅" if all(r.get("park_ok") for r in results) else "❌",
        wow.park_calls, wow.park_ok_calls))
    print("总按键 %d 次 / 右键拾取 %d 次" % (len(wow.actions), len(wow.clicks)))
    print("虚拟总时长 %.1fs" % (clock.t if clock else 0.0))
    if bad:
        print("\n❌ %d/%d 竿从未锁定浮标" % (bad, len(results)))
    else:
        print("\n✅ 全部 %d 竿都锁定了浮标" % len(results))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
