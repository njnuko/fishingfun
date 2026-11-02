"""钓鱼主循环 —— 严格复刻 Source/FishingFunBot/Bot/FishBot.cs（210 行）

════════════════════════════════════════════════════════════════════
★★ 游戏机制（用户 2026-11-02 明确说明，所有判定设计的前提）★★
  抛竿后浮标在水面停留 **17 秒**。这 17 秒内：
    · 浮标必定会出现「咬钩」—— 表现为浮标晃动（下沉/抖动）;
    · 17 秒一到，游戏 **自动收杆**，浮标消失，水面不再有浮标;
    · 也就是说：「浮标消失」有两种完全不同的含义 ——
        (a) 17 秒到期自动收杆 → 正常，重抛即可，**不是咬钩**;
        (b) 咬钩后浮标沉入水下   → **是咬钩**，必须立刻右键拾取。
      两者的区别靠「距抛竿过了多久」+「消失前有没有晃动」来区分。
  ⇒ 所以任何「找不到浮标就判咬钩」的写法都是错的，必须结合时间与
    消失前的形变证据。参见 _wait_for_bite 顶部说明与 area_decayed()。
════════════════════════════════════════════════════════════════════

关键点（一个都不能省，省了就「只抛竿不拾取」）：
  · PressKey(castKey) 之后必须 Watch(2000) 收敛基准
  · WaitForBite() 用 FindBobber() 找到的「抛竿位置」做 biteWatcher.Reset
    这个位置整竿固定，Loot 就传它（不是咬钩瞬间坐标）
  · Watch() 里只做 bobberFinder.Find()，不做别的（不要加命中率统计）
  · 判咬钩窗口 = 抛竿后 17 秒（游戏浮标存活期），不是 25 秒
"""

import time

from .timedaction import TimedAction
from . import __version__ as _PKG_VERSION

# 原版 ConsoleKey 枚举里 0x34='4'、0x35='5'、0x20=Space
VK_SPACE = 0x20
VK_4 = 0x34
VK_5 = 0x35


class FishingBot:
    def __init__(self, bobber_finder, bite_watcher, cast_key=VK_4,
                 ten_min_key=None, wow_process=None, log=None, grabber=None):
        self.bobber_finder = bobber_finder
        self.bite_watcher = bite_watcher
        self.cast_key = cast_key
        self.ten_min_key = list(ten_min_key or [])
        self.wow_process = wow_process
        self.log = log or (lambda *a, **k: None)
        self.bobber_finder.grabber = grabber
        self.is_enabled = False
        self.start_time = time.monotonic()
        self._rng = __import__("random").Random()
        self.anchor_wait = 1.5      # 非原版：找锚点最多等这么久，超时立刻重抛
        self.fishing_event_handler = lambda e: None
        self.loot_reader = None     # 非原版：拾取后读「你获得了物品：[xxx]」
        self._loot_fp = ""          # 上一竿结束时的物品提示指纹
        self.loot_stats = {}        # 物品名 → 数量
        # 非原版：收竿后检测屏幕上方的「没有鱼上钩」系统提示。
        # 出现 = 收竿过早/误判咬钩（用户实测：时机对了每次都能钓到）。
        self.no_bite_detector = None
        self.miss_count = 0         # 误判（没有鱼上钩）竿数
        # v17 诊断：收集本轮真实分布，用于确定门槛，不改变判定行为
        self._diag = {"conf": [], "noanchor": [], "nobite": [], "own": {}, "prehit": 0}
        self.hit_count = 0          # 真咬钩竿数
        # 非原版调试（--debug=1）：每竿存 咬钩前/拾取后 两帧到 debug/，用于事后对比
        self.debug_frames = False
        self._cast_no = 0           # 本竿序号
        self._debug_dir = "debug"
        # v21：咬钩幅度自动标定（只做一次，需显式开启）
        self.calib_sink = False
        self._sink_calibrated = False
        self.sink_calib_seconds = 17
        # v38：按键时刻（monotonic）。标定采样以此为零点 —— 游戏 17 秒
        # 窗口从按下抛竿键算起（含飞行时间），晚算一秒就少一秒有效样本。
        self._last_cast_t = None
        # ★ v44：标定期记录的浮标锚点（截屏区局部坐标），每竿开头清空
        self._calib_anchor_local = None
        self._area_armed_done = False   # 首竿标定截屏边界只做一次
        # ★ v45：日志/截图增强（用户要求每竿可复盘）
        self.save_regions = False   # True = 每竿锚定/咬钩/重抛都存区域图
        # ★ v55：关键节点取证（区域图+红框 / 全屏图+红框 / 特征值txt）
        #   用户要求：首竿浮标特征+咬钩特征、每竿浮标截图+咬钩图+咬钩特征值、
        #   每竿完整全屏截图（供离线调试）。
        self.forensic = False
        self._is_first_cast = False
        self.frame_log = False      # True = 判咬钩循环每帧输出浮动值一行
        self.trace_seconds = 1.0    # 每竿前 N 秒逐秒存区域图（0=关）
        # ★ v55：debug 图按秒节流（用户要求「每秒存一次就行」）。
        #   判咬钩循环约 14fps（检测 21ms + sleep 50ms），每帧都存要多花
        #   40ms/帧 落盘，帧率腰斩 → 咬钩那 3~7 帧可能被跳过。
        self.save_interval_sec = 1.0     # 普通帧：1 秒 1 张
        self.critical_tags = ("咬钩", "锚定", "首竿标定", "重抛", "咬钩衰减")
        self._last_dump_t = 0.0
        self._last_dump_cast = None
        self._dump_skipped = 0
        self._dense_dump = False         # True = 咬钩前后密集段（200ms 1 张）
        self.keep_debug_casts = 20       # 只保留最近 N 竿的帧图（0=不限）
        # ★ v49：可恢复停机（只结束本轮，不退出主循环）+ 第 N 竿标定保护
        self._soft_stop = False
        self.soft_stop_pause = 0.35
        # ★ v49：首竿标定期间（17 秒）浮标本来就可能被咬走/离场，
        #   这段时间的「找不到浮标」一律不判停机，只记日志。
        self._calibrating = False

    # ---- 非原版：首竿标定截屏边界 ----
    def _set_calibrating(self, on):
        """★ v49：标定期间关掉「连续 N 帧找不到浮标 → 停机」这条链。

        标定要连续采样 17 秒，期间鱼咬钩、浮标沉底、玩家转身都会让浮标
        短时消失 —— 这些都不是故障，是正常钓鱼过程。原来这段时间里
        finder 的 no_bobber_frames 照样累加，凑满 3 帧就停整个程序。

        做法：进标定先把 suppress_abort + no_anchor_lock 打开（飞行期用的
        同一套抑制开关），出来再恢复成原来的值，不影响其它阶段。
        """
        self._calibrating = bool(on)
        f = self.bobber_finder
        if f is None:
            return
        if on:
            self._calib_saved_flags = (
                getattr(f, "suppress_abort", False),
                getattr(f, "no_anchor_lock", False),
            )
            f.suppress_abort = True
            f.no_anchor_lock = True
            try:
                if hasattr(f, "no_bobber_frames"):
                    f.no_bobber_frames = 0
            except Exception:
                pass
        else:
            sa, nal = getattr(self, "_calib_saved_flags", (False, False))
            f.suppress_abort = sa
            f.no_anchor_lock = nal
            try:
                if hasattr(f, "no_bobber_frames"):
                    f.no_bobber_frames = 0
            except Exception:
                pass


    def _arm_screen_area(self):
        """第一次抛竿后量一次截屏边界，之后永久沿用（用户设计）。

        抛竿瞬间绿条要过几帧才画出来，所以这里稍等再量；量到后锁定，
        后续所有帧用同一个区域 —— 帧尺寸一致，差分标定/序列定位才不会失效。
        """
        if self._area_armed_done:
            return
        screen = getattr(self.bobber_finder, "screen", None)
        if screen is None or not hasattr(screen, "arm_area"):
            return
        time.sleep(0.6)                       # 等绿条画出来
        area = screen.arm_area()              # 测一次 → 锁定
        self._area_armed_done = True
        if area:
            self.log("锁定截屏区（首竿标定）: %dx%d@%d,%d", area[2], area[3], area[0], area[1])

    # ---- 逐行对应 FishBot.cs:38-69 ----
    def start(self):
        self.is_enabled = True
        self._do_ten_minute_key()

        while self.is_enabled:
            try:
                self._cast_no += 1
                # ★ v55：首竿标记 —— 取证文件名带「首竿_」，一眼找到首竿特征
                self._is_first_cast = (self._cast_no == 1)
                # ★ v55：按秒存图后一竿约 20~40 张，跑一天几百竿会堆到几十 GB。
                #   这里只保留最近 keep_debug_casts 竿（默认 20 竿，约 600MB）
                #   的帧图，更早的连同 timeline 记录一起删；日志文件不动。
                try:
                    self._prune_debug_frames()
                except Exception as _pe:
                    self.log("debug 帧清理异常 %s", _pe)
                self.log("Pressing key %s to Cast.", self.cast_key)
                # 非原版：抛竿前先把光标移到屏幕边缘，避免鼠标悬停在抛竿点附近
                # 挡住浮标 / 弹出悬停提示干扰判定
                # ★ v52：park_cursor 以前是坏方法（漏 self）又被 except 吞掉，
                #   等于没停。
                # ★ v54：用户明确要求「每次抛竿前把鼠标移到屏幕边缘」。
                #   改为 park_cursor_verified()：移到边缘后回读校验，不在边缘
                #   就换边角重试（右下→右上→左下→左上），并把实际落点写进日志，
                #   便于事后核对「鼠标到底有没有移开」。
                try:
                    if hasattr(self.wow_process, "park_cursor_verified"):
                        ok, pos = self.wow_process.park_cursor_verified()
                        if ok:
                            self.log("鼠标已移到屏幕边缘: (%d,%d)", pos[0], pos[1])
                        else:
                            self.log("⚠️ 鼠标未能移到屏幕边缘（回读落点 %d,%d），"
                                     "本竿检测可能受光标干扰", pos[0], pos[1])
                    else:
                        self.wow_process.park_cursor()
                except Exception as _pe:
                    self.log("⚠️ 鼠标停靠失败: %s", _pe)
                self._press_ten_min_key_if_due()
                self.fishing_event_handler({"Action": "Cast"})
                # v20：抛竿前抓 pre（此刻画面里还没有浮标），抛竿后 2 秒
                # 由 _watch 抓 post，两帧差分就能拿到浮标位置和真实色值。
                self._grab_calib_pre()
                # v38：记录按键时刻。游戏的 17 秒浮标窗口从按下 4 键算起
                # （含飞行时间），标定采样必须用这个时刻当零点。
                self._last_cast_t = time.monotonic()
                # ★ v41：每竿开始清零「本竿锁定过浮标」标志，
                #   供 run.py 的 _on_no_bobber 区分「从没锁定」和「锁定后又丢」。
                self._anchor_locked_this_cast = False
                # ★ v59：每竿清掉衰减裁决回调与「本竿已按咬钩收尾」标记。
                #   回调在 _verify_and_watch 里按本竿锚点重新注册；不清会让
                #   上一竿的锚点坐标残留到下一竿的拾取点击上。
                self._bite_handled_this_cast = False
                try:
                    self.bobber_finder.decay_claim_cb = None
                except Exception:
                    pass
                # ★ v60：注入「距本竿抛竿已过多少秒」。
                #   17 秒浮标存活期的判定（到期收杆 vs 鱼咬钩）靠它。
                try:
                    self.bobber_finder.seconds_since_cast = (
                        lambda: (time.monotonic() - self._last_cast_t)
                        if getattr(self, "_last_cast_t", None) else 0.0)
                except Exception:
                    pass
                # ★ v43：每竿都清零，避免上一竿的稳定锁定标记污染本竿的
                #   差分交叉校验（原来 _anchor_color_stable 只在 _watch 里赋值，
                #   异常路径下会残留上一竿的 True）。
                self._anchor_color_stable = False
                # ★ v44：每竿清空标定期锚点。锚点只能用「本竿第一帧」
                #   找到的浮标位置；沿用上一竿的坐标必然对不上（落点不同）。
                self._calib_anchor_local = None
                self.wow_process.press_key(self.cast_key)

                # 第一次抛竿后：绿条出现，此刻才量边界，量一次就永久锁定。
                # 抛竿前画面里没有绿条/浮标，那时量出来的边界是错的
                # （实测会量成 1280x172，把水面砍掉一大半，然后锁死）。
                self._arm_screen_area()

                self._watch(2000)          # ← 原版 FishBot.cs:57，不能省

                # ★ v41：把 _watch(2000) 跟踪期间已经锁定成功的浮标位置
                #   留下来。实测日志 20:42:03.607~20:42:09.025 连续 64 帧
                #   稳定命中 (773,280)，但 _watch 结束时 bf.reset() 会把
                #   previous_location 清空，紧接着：
                #     - _maybe_calibrate_sink() 跑 10.2 秒（17 秒窗口采样）
                #     - 然后 _wait_for_bite() 重新用 anchor_wait 找锚点
                #   结果 anchor_wait 那 1.5 秒早在标定期间就跑完了，
                #   _wait_for_bite 只剩 0.15 秒 3 帧，全丢 → 报「钓点太暗」
                #   → 重抛 → 停机。用户看到的就是「钓一竿就停」。
                #   现在把 _watch 的末位置带过去，标定结束后直接用它当锚点，
                #   根本不用再找一次。
                # ★ v51：把「跟踪期锚点保留」整段删掉。
                #   真机日志 2026-11-02 09:52:16~09:52:17 的现场证据：
                #     09:52:16,127 跟踪期锚点保留: (752, 283)   ← _watch 打的稳定位
                #     09:52:16,453 三帧确认通过 → 本竿锚点 屏幕 (763, 285)
                #     09:52:17,129 复用跟踪期锚点失败 '<' not supported
                #                  between instances of 'tuple' and 'int'
                #   两个问题叠在一起：
                #     ① 「追踪稳定位」日志打的位置和真正写进 _tracked_anchor 的
                #        变量不是同一个数（752 vs 763，差 11px），日志与实际不符；
                #     ② 复用路径做坐标换算后再比较，类型一不对就抛异常，整条
                #        捷径白跑，每竿白花 0.7~1.5 秒，最后还是退回现场找锚点。
                #   更根本的是用户反复强调的铁律：每竿锚点必须现场重新判定，
                #   绝不搬运上一阶段/上一竿的坐标。所以这里彻底不再保留。
                self._tracked_anchor = None

                # v21：第一竿抛竿后采 17 秒，实测本钓点浮标下沉幅度，
                # 反推 strike_value。只做一次，之后所有竿沿用。
                # ★ v49：标定期间禁用 abort —— 这 17 秒里浮标被咬走/离场是
                #   常态（真机日志 08:56:33 就是标定刚结束 3 帧内丢标），
                #   之前会直接判「目标消失」并停机，等于把一次正常咬钩当成故障。
                self._set_calibrating(True)
                try:
                    self._maybe_calibrate_sink()
                finally:
                    self._set_calibrating(False)

                self._wait_for_bite()
            except Exception as e:
                self.log("ERR %s", e)
                time.sleep(2.0)

            # ★ v49：可恢复停机检查。
            #   run.py 的 abort_cb 在「本竿已锁定过浮标、之后找不到」时
            #   判定为咬钩/目标消失，要求「重抛继续」。
            #   v48 及以前它直接 is_enabled=False，而主循环 while 只看
            #   is_enabled，于是打完「重抛继续」下一行就是 Bot has Stopped ——
            #   真机日志 08:56:33 就是这么停的（浮标 area 458→313 渐进缩小，
            #   明显是鱼把浮标拉进水了，本该立刻重抛）。
            #   现在：可恢复停机只清 _soft_stop，主循环继续；
            #   永久停机（用户要求退出 / 本竿从未锁定浮标）才真正结束。
            if getattr(self, "_soft_stop", False):
                self._soft_stop = False
                self.is_enabled = True
                pause = getattr(self, "soft_stop_pause", 0.35)
                self.log("→ 可恢复停机：本轮结束，%.2f 秒后重抛（不退出）", pause)
                time.sleep(pause)

        self.log("Bot has Stopped.")

    def stop(self):
        self.is_enabled = False
        total = self.hit_count + self.miss_count
        if total > 0:
            rate = 100.0 * self.hit_count / total
            self.log("收竿统计: 总计 %d 竿 | 真咬钩 %d | 没有鱼上钩(误判) %d | 准确率 %.1f%%",
                     total, self.hit_count, self.miss_count, rate)
        # 诊断(v17)：汇总本轮所有 DIAG 数据，用于定门槛
        try:
            self._diag_summary()
        except Exception as e:
            self.log("DIAG汇总失败: %s", e)
        self.log("Bot is Stopping...")

    def _diag_summary(self):
        """v17 诊断汇总：把本轮收集的可信度/找锚点/黄字分布打出来。"""
        d = getattr(self, "_diag", None) or {}
        conf = d.get("conf", [])
        if conf:
            s = sorted(conf)
            n = len(s)
            self.log("DIAG汇总 可信度 n=%d min=%.2f p25=%.2f 中位=%.2f p75=%.2f max=%.2f",
                     n, s[0], s[int(n * 0.25)], s[n // 2], s[int(n * 0.75)], s[-1])
            for th in (0.30, 0.35, 0.40, 0.45):
                ok = sum(1 for x in s if x >= th)
                self.log("DIAG汇总 门槛%.2f 通过 %d/%d = %.0f%%", th, ok, n, 100.0 * ok / n)
            self.log("DIAG汇总 v18 生效门槛=%.2f（低于此值放弃重抛）",
                     getattr(self, "anchor_ratio_min", 0.35))
        na = d.get("noanchor", [])
        if na:
            self.log("DIAG汇总 找锚点失败 %d 次，退出原因: %s", len(na), na)
        nb = d.get("nobite", [])
        if nb:
            self.log("DIAG汇总 黄字检测 %d 次，命中 %d 次；峰值 px=%s rows=%s span=%s",
                     len(nb), sum(1 for x in nb if x[0]),
                     [x[1] for x in nb], [x[2] for x in nb], [x[3] for x in nb])
        own = d.get("own", {})
        if own:
            self.log("DIAG汇总 归属: 真咬钩=%d 误判=%d 被基准吞=%d",
                     own.get("hit", 0), own.get("miss", 0), own.get("eaten", 0))
        pre = d.get("prehit", 0)
        self.log("DIAG汇总 收竿前已有黄字的竿数=%d（这些竿的误判会被吞）", pre)

    # ---- v20 新增：差分标定的取帧 ----
    def _grab_calib_pre(self):
        """抛竿前抓 pre 帧。抓不到就置 None，退化为原版纯色阈值逻辑。"""
        bf = self.bobber_finder
        if not hasattr(bf, "calib_pre"):
            return
        if not getattr(bf, "calib_enabled", True):
            return
        try:
            # v20.1 修正：原来错调 self.wow_process.grab()，但 wow_process 是
            # 窗口/键盘对象，根本没有 grab —— 每竿都报"pre 帧抓取失败"，
            # 差分标定等于从未启动。真正的截图入口是注入给 finder 的
            # grabber（WowScreen），_watch 里也是走这条路。
            grabber = getattr(bf, "grabber", None)
            pre = grabber.grab() if grabber is not None else None
            bf.calib_pre = pre
            bf._calib_bmp = None
            if pre is None:
                self.log("差分标定: pre 帧抓取失败，本竿退回色阈逻辑")
            elif getattr(self, "debug_frames", False):
                try:
                    import os
                    self._dump_bmp(pre, "calib_pre")
                except Exception as e:
                    self.log("差分标定: pre 落盘失败 %s", e)
        except Exception as e:
            bf.calib_pre = None
            self.log("差分标定: pre 帧异常 %s，本竿退回色阈逻辑", e)

    def _dump_bmp(self, bmp, tag):
        """把一帧存成 bmp，用于事后验证差分标定是否真的对得上。"""
        import os
        from PIL import Image
        os.makedirs("debug", exist_ok=True)
        fn = os.path.join("debug", "%s_%s.bmp"
                          % (tag, time.strftime("%H%M%S")))
        Image.fromarray(bmp).save(fn)
        self.log("差分标定: 已存 %s", fn)

    # ★ v46：锚点合法性校验 —— 锚点落地处必须真的有暖色浮标。
    #   真机实测（2026-11-01 21:24，17 张区域帧逐像素核对）：
    #     浮标真实位置 局部 (606,236)，26x19，约 360 个暖色像素；
    #     而整竿被写进 previous_location 的是 局部 (429,350)，
    #     它 40x40 邻域内暖色像素 = 0（整张图 x<450 的暖色像素也是 0）。
    #   也就是说锚点定在了一片空白水面上，之后 ±40px 窗口 + 粘滞校验
    #   把真浮标全程挡在外面 → 每帧「未命中 3/3」→ 误判目标消失 → 停机。
    #   这条判据能 100% 拦住那个错值，且不依赖任何具体坐标。
    # ★ v48：锚点闸门 —— 从「暖色方向」改成「局部高对比度」
    #
    #   为什么必须换（2026-11-01 真机图逐像素实测，1280x801 区域帧）：
    #     · 浮标本体所在 40x40 邻域，暖色判据（R-B>20 且 R>80）只有 149 px；
    #     · 而一片空白水面的波纹反光，同样判据有 129 px —— 几乎分不开；
    #     · 换一帧光照，浮标直接掉到 0 px，闸门把真浮标判成「该处没有浮标」。
    #   WoW 里浮标颜色随环境光变化（清晨偏冷、正午偏白、黄昏偏暖），
    #   「R 比 B 高」根本不是浮标的固有属性，只是某几种光线下碰巧成立。
    #
    #   浮标真正的固有属性只有两条，且与色相无关：
    #     ① 局部亮度显著高于周围水面（凸出水面、受光照）；
    #     ② 边缘清晰成团（不是弥散的水波反光）。
    #   实测同一张图，用「亮度 − 大尺度局部背景」的偏离量 dev：
    #       浮标本体   dev>30 = 720 px，峰值 126
    #       水面最亮的反光块 dev>30 =  56 px，峰值  50
    #       其余 6 处水面 dev>30 = 0~10 px，峰值 4~40
    #   13 倍以上差距，且冷色/暖色/白色浮标全部适用。
    #
    #   暖色在这里保留为「加分项」而不是「必要条件」：命中暖色只加日志标记，
    #   不作为拒绝理由 —— 绝不会再因为光照把真浮标挡在外面。
    MIN_WARM_PX_AT_ANCHOR = 20        # 兼容旧调用，仅用于「暖色加分」日志
    ANCHOR_WIN_R = 30                 # ★ 邻域半径 20→30（浮标本体 31x23，40x40 太小）
    MIN_BRIGHT_PX_AT_ANCHOR = 90      # dev>30 的像素数下限（浮标 720，水面最多 56）
    # ★ v49：亮团像素门槛改为「随浮标指纹面积自适应」。
    #   固定 90 px 是在 1280x801 环境定的（浮标亮团 720 px，水面最多 56），
    #   换到 720x529 后浮标只有 457 px —— 固定值仍能过，但一旦用户再降分辨率
    #   或浮标更远（实测新环境已出现 313 px 的帧），90 就会随机误拒，
    #   表现为「同样的水面，有时认浮标有时不认」。
    #   新口径：门槛 = clamp(指纹亮团面积 × 0.35, MIN_BRIGHT_PX_AT_ANCHOR, 指纹面积)
    #   · 有指纹时按比例缩放，分辨率变了自动跟随；
    #   · 无指纹（首竿之前）退回固定 90，行为与 v48 一致；
    #   · 下限仍为 90，防止指纹本身偏小时门槛低到把水面当浮标。
    BRIGHT_PX_FP_RATIO = 0.35
    MIN_BRIGHT_PEAK_AT_ANCHOR = 70    # dev 峰值下限（浮标 126，水面最高 50）
    MIN_BRIGHT_AREA_RATIO = 0.03      # dev>30 像素占邻域面积比例下限

    def _confirm_anchor_3frames(self, cand_screen, max_frames=12):
        """★ v46：三帧一致性确认（模拟首竿标定的三帧确认口径）。

        用户要求：第一竿是怎么确认锚点的，后面每一竿也照同样标准来。
        首竿标定靠「多帧采样 + 序列定位 + 面积/连续性筛选」确定锚点，
        单帧命中从不直接采信。所以后面每竿也必须满足：
          · 连续 3 帧都检测到浮标；
          · 且三帧位置互相靠近（容差 TOL）；
          · 且每帧都过暖色闸门。
        只有全部满足才把锚点交给 bite_watcher，否则继续扫（最多 max_frames 帧）。

        返回 (anchor_screen, info) 或 (None, 原因)
        """
        TOL = 28            # 浮标静置时帧间抖动远小于此；水波纹/假目标会飘
        hits = []
        tried = 0
        while tried < max_frames and self.is_enabled:
            tried += 1
            p = None
            try:
                p = self.bobber_finder.find()
            except Exception as e:
                self.log("三帧确认: find() 异常 %s", e)
            if p is None:
                if hits:
                    self.log("三帧确认: 中途丢失（已确认 %d 帧），重新开始", len(hits))
                hits = []
                continue
            loc = self._to_local(p)
            if loc is None or not self._anchor_has_bobber(loc, log=False):
                self.log("三帧确认: 候选 %s 暖色闸门不过，丢弃", p)
                hits = []
                try:
                    self.bobber_finder.previous_location = None
                except Exception:
                    pass
                continue
            if hits:
                lx, ly = loc
                px, py = hits[-1][1]
                d = max(abs(lx - px), abs(ly - py))
                if d > TOL:
                    self.log("三帧确认: 候选 %s 与上一帧 %s 相距 %dpx（>%d），疑似假目标，重新开始",
                             loc, (px, py), d, TOL)
                    hits = []
                    continue
            hits.append((p, loc))
            if len(hits) >= 3:
                xs = [h[1][0] for h in hits]
                ys = [h[1][1] for h in hits]
                sx = int(sum(xs) / 3.0)
                sy = int(sum(ys) / 3.0)
                span = max(max(xs) - min(xs), max(ys) - min(ys))
                sa = (0, 0)
                try:
                    sa = self.bobber_finder.grabber.search_area()[:2]
                except Exception:
                    pass
                anchor = (sx + int(sa[0]), sy + int(sa[1]))
                # 再用真实一点的位置校验一次（平均点可能落到浮标边缘外）
                if not self._anchor_has_bobber((sx, sy)):
                    self.log("三帧确认: 均值点局部 (%d,%d) 暖色闸门不过，重新开始", sx, sy)
                    hits = []
                    continue
                self.log("三帧确认通过: 局部 3 帧 %s 跨度 %dpx → 本竿锚点 屏幕 %s | 局部 (%d,%d)",
                         [h[1] for h in hits], span, anchor, sx, sy)
                return anchor, {"frames": tried, "span": span}
        return None, "未能在 %d 帧内取得三帧一致锚点" % max_frames

    def _to_local(self, screen_xy):
        """屏幕坐标 → 截屏区局部坐标（v46 日志用）。"""
        try:
            g = getattr(self.bobber_finder, "grabber", None)
            sa = g.search_area() if g is not None else (0, 0, 0, 0)
            return (int(screen_xy[0]) - int(sa[0]), int(screen_xy[1]) - int(sa[1]))
        except Exception:
            return None

    def _anchor_has_bobber(self, local_xy, bmp=None, log=True):
        """★ v48：校验局部坐标邻域内是否是一块「高对比度亮团」（=浮标）。

        判据（与色相完全无关，冷/暖/白浮标通用）：
          ① dev = 亮度 − 大尺度局部背景，dev > 30 的像素数 ≥ MIN_BRIGHT_PX_AT_ANCHOR；
          ② dev 峰值 ≥ MIN_BRIGHT_PEAK_AT_ANCHOR（排除弥散水波反光）；
          ③ 亮团质心离锚点 ≤ 0.8×邻域半径（锚点必须落在亮团本身上，
             而不是「邻域里恰好有个亮点」）。
        暖色像素只写进日志当参考，不参与通过/拒绝的判定。
        """
        try:
            lx, ly = int(local_xy[0]), int(local_xy[1])
        except Exception:
            return False
        if bmp is None:
            g = getattr(self.bobber_finder, "grabber", None)
            try:
                bmp = g.grab() if g is not None else None
            except Exception:
                bmp = None
        if bmp is None:
            return False
        import numpy as _np
        h, w = bmp.shape[0], bmp.shape[1]
        r = int(getattr(self, "ANCHOR_WIN_R", 30))
        x0, x1 = max(0, lx - r), min(w, lx + r + 1)
        y0, y1 = max(0, ly - r), min(h, ly + r + 1)
        if x1 <= x0 or y1 <= y0:
            if log:
                self.log("锚点校验: 局部 (%d,%d) 越界（区域 %dx%d），拒绝", lx, ly, w, h)
            return False

        f = bmp[:, :, :3].astype(_np.float32)
        # 与 _bright_blob_mask 同口径：BT.601 亮度，权重对齐 BGR/RGB 都无碍（对称近似）
        lum = 0.114 * f[:, :, 2] + 0.587 * f[:, :, 1] + 0.299 * f[:, :, 0]
        # 大尺度局部背景：以 4x 降采样 + 半径 8 的盒式均值（≈68px 视野）
        s = 4
        hs, ws2 = max(1, h // s), max(1, w // s)
        small = lum[:hs * s, :ws2 * s].reshape(hs, s, ws2, s).mean(axis=(1, 3))
        rad = 8
        pad = _np.pad(small, rad, mode="edge")
        c = _np.cumsum(_np.cumsum(pad.astype(_np.float64), 0), 1)
        c = _np.pad(c, ((1, 0), (1, 0)))
        k = 2 * rad + 1
        box = (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / float(k * k)
        bg = _np.repeat(_np.repeat(box.astype(_np.float32), s, axis=0), s, axis=1)
        bh, bw = bg.shape
        dev = _np.zeros((h, w), _np.float32)
        dev[:bh, :bw] = lum[:bh, :bw] - bg

        sub = dev[y0:y1, x0:x1]
        bright = sub > 30.0
        n_bright = int(bright.sum())
        peak = float(sub.max()) if sub.size else 0.0
        area = sub.shape[0] * sub.shape[1]
        ratio = n_bright / float(area) if area else 0.0
        # 亮团质心（相对锚点的偏移）
        off_cx = off_cy = 0.0
        if n_bright > 0:
            ys_, xs_ = _np.nonzero(bright)
            off_cx = float(xs_.mean()) - (lx - x0)
            off_cy = float(ys_.mean()) - (ly - y0)
        off = max(abs(off_cx), abs(off_cy))

        # 暖色：仅作参考信息，不参与判定
        rr = bmp[y0:y1, x0:x1, 0].astype(_np.int16)
        bb = bmp[y0:y1, x0:x1, 2].astype(_np.int16)
        warm = int((((rr - bb) > 20) & (rr > 80)).sum())

        ok = (n_bright >= self._bright_px_floor()
              and peak >= self.MIN_BRIGHT_PEAK_AT_ANCHOR
              and ratio >= self.MIN_BRIGHT_AREA_RATIO
              and off <= 0.8 * r)
        if log:
            self.log("锚点校验: 局部 (%d,%d) 高对比亮团 %d px(占比%.0f%%) 峰值%.0f "
                     "质心偏移%.0fpx（暖色%d px 仅参考）→ %s（门槛 %d px%s）",
                     lx, ly, n_bright, ratio * 100.0, peak, off, warm,
                     "通过" if ok else "拒绝（该处不是浮标亮团）",
                     self._bright_px_floor(),
                     "" if self._bright_px_target() is None
                     else "，指纹面积 %d" % self._bright_px_target())
        return ok

    def _bright_px_target(self):
        """★ v49：从浮标指纹里取「亮团面积」基准，没有指纹返回 None。

        指纹在 finder 上（learn_fingerprint 写入），字段 area 是浮标候选
        的整体像素数。它和 _anchor_has_bobber 里 dev>30 的亮团数不完全同量纲
        （后者是「显著高于局部背景」的部分），实测两者同环境比值约 0.6~0.7，
        所以用 0.35 的系数留足余量，只做「随分辨率缩放」的用途，不做精判。
        """
        f = self.bobber_finder
        if f is None:
            return None
        fp = getattr(f, "bobber_fingerprint", None)
        if not fp:
            return None
        try:
            a = int(fp.get("area") or 0)
        except Exception:
            return None
        return a if a > 0 else None

    def _bright_px_floor(self):
        """★ v49：本竿亮团像素门槛（见 BRIGHT_PX_FP_RATIO 说明）。"""
        target = self._bright_px_target()
        if target is None:
            return self.MIN_BRIGHT_PX_AT_ANCHOR
        need = int(target * self.BRIGHT_PX_FP_RATIO)
        need = max(need, self.MIN_BRIGHT_PX_AT_ANCHOR)
        # 不超过指纹面积本身 —— 否则一旦系数定错就永远过不了
        return min(need, target)

    # ================= ★ v55 关键节点取证 =================
    # 用户要求：把「关键节点数据」落到日志里，不只写一行文字，要能事后离线复盘：
    #   1. 首竿拿到的浮标特征 + 咬钩特征（区域图 + 浮标处红框）
    #   2. 之后每竿的浮标截图（区域图 + 红框）+ 咬钩图 + 咬钩特征值
    #   3. 每竿一张完整全屏截图（含 UI），用于离线调试比对
    #
    # 命名统一为（一竿一组，按竿号排序即可按时序读）：
    #   c001_首竿_锚定_区域_720x436@360,210_t1.2s_104841.bmp   区域图+红框
    #   c001_首竿_锚定_全屏_1440x900_104841.bmp               全屏图+红框
    #   c001_首竿_锚定_特征_104841.txt                        特征值纯文本
    #   c001_咬钩_区域_... / c001_咬钩_全屏_... / c001_咬钩_特征_...
    # 红框画在「浮标外接矩形外扩 4px」处；全屏图上的框用屏幕坐标，区域图用局部坐标。

    FORENSIC_BOX_PAD = 4          # 红框相对浮标外接矩形外扩的像素

    def _forensic_dir(self):
        import os
        d = os.path.join(os.getcwd(), self._debug_dir or "debug")
        os.makedirs(d, exist_ok=True)
        return d

    def _grab_fullscreen(self):
        """抓一张完整全屏（含 UI）。返回 HxWx3 RGB；失败返回 None。"""
        try:
            import mss
            import numpy as np
            with mss.mss() as sct:
                mon = sct.monitors[1]
                raw = sct.grab(mon)
            arr = np.frombuffer(bytes(raw.raw), np.uint8)
            arr = arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1]
            return np.ascontiguousarray(arr), (int(mon["left"]), int(mon["top"]))
        except Exception as e:
            self.log("全屏截图失败 %s", e)
            return None, (0, 0)

    @staticmethod
    def _draw_box(img, box, color=(255, 40, 40), pad=4, width=2, label=None):
        """在 PIL Image 上画红框。box=(x0,y0,x1,y1) 会自动外扩 pad。"""
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        if box is None:
            return img
        x0, y0, x1, y1 = [int(v) for v in box]
        x0, y0 = x0 - pad, y0 - pad
        x1, y1 = x1 + pad, y1 + pad
        W, H = img.size
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W - 1, x1), min(H - 1, y1)
        draw.rectangle([x0, y0, x1, y1], outline=color, width=width)
        if label:
            # 标签画在框上方，越界则画在框内左上
            ty = y0 - 12 if y0 >= 14 else y0 + 2
            draw.text((x0 + 1, ty), label, fill=color)
        return img

    def _save_forensic(self, tag, cast_no, bmp_region=None, box_local=None,
                       box_screen=None, features=None, also_fullscreen=True,
                       stamp=None):
        """★ v55：统一的「关键节点取证」入口。

        一次性落三样东西（同一竿同一时刻共用同一个 stamp，文件名天然对齐）：
          · <区域图>   bmp_region 或现场 grab()，浮标处画红框（局部坐标）
          · <全屏图>   mss 整屏，浮标处画红框（屏幕坐标）
          · <特征txt>  features 字典逐行写出（人类可读，含坐标换算关系）

        box_local / box_screen 可只给一个；只给 box_screen 时按截屏区原点反算局部框。
        返回 (区域文件名, 全屏文件名, 特征文件名)。
        """
        import os
        from PIL import Image

        if not (getattr(self, "forensic", False)
                or getattr(self, "save_regions", False)
                or getattr(self, "debug_frames", False)):
            return (None, None, None)

        d = self._forensic_dir()
        stamp = stamp or time.strftime("%H%M%S")
        cast_no = cast_no if cast_no is not None else self._cast_no
        # 首竿额外打标记，方便一眼找到「首竿特征」
        pre = "首竿_" if getattr(self, "_is_first_cast", False) else ""

        g = getattr(self.bobber_finder, "grabber", None)
        l = t = 0
        try:
            if g is not None:
                sa = g.search_area()
                l, t = int(sa[0]), int(sa[1])
        except Exception:
            pass

        # 只有屏幕框时反算局部框
        if box_local is None and box_screen is not None:
            box_local = (box_screen[0] - l, box_screen[1] - t,
                         box_screen[2] - l, box_screen[3] - t)
        if box_screen is None and box_local is not None:
            box_screen = (box_local[0] + l, box_local[1] + t,
                          box_local[2] + l, box_local[3] + t)

        region_name = full_name = feat_name = None
        elapsed = ""
        if getattr(self, "_last_cast_t", None):
            elapsed = "_t%.1fs" % (time.monotonic() - self._last_cast_t)

        # 1) 区域图 + 红框
        try:
            if bmp_region is None and g is not None:
                bmp_region = g.grab()
            if bmp_region is not None:
                h, w = bmp_region.shape[0], bmp_region.shape[1]
                img = Image.fromarray(bmp_region).convert("RGB")
                cx = None
                if box_local is not None:
                    cx = ((box_local[0] + box_local[2]) // 2,
                          (box_local[1] + box_local[3]) // 2)
                label = "BOBBER %d,%d" % cx if cx else None
                self._draw_box(img, box_local, pad=self.FORENSIC_BOX_PAD,
                               label=label)
                region_name = "c%03d_%s%s_区域_%dx%d@%d,%d%s_%s.bmp" % (
                    cast_no, pre, tag, w, h, l, t, elapsed, stamp)
                img.save(os.path.join(d, region_name))
                self.log("取证[%s] 区域图 debug/%s  红框=局部%s 屏幕%s",
                         tag, region_name, box_local, box_screen)
        except Exception as e:
            self.log("取证[%s] 区域图失败 %s", tag, e)

        # 2) 全屏图 + 红框（屏幕坐标）
        if also_fullscreen:
            try:
                arr, (mx, my) = self._grab_fullscreen()
                if arr is not None:
                    img = Image.fromarray(arr).convert("RGB")
                    bx = box_screen
                    if bx is not None and (mx or my):
                        bx = (bx[0] - mx, bx[1] - my, bx[2] - mx, bx[3] - my)
                    self._draw_box(img, bx, pad=self.FORENSIC_BOX_PAD,
                                   label="BOBBER(screen)")
                    full_name = "c%03d_%s%s_全屏_%dx%d%s_%s.bmp" % (
                        cast_no, pre, tag, arr.shape[1], arr.shape[0],
                        elapsed, stamp)
                    img.save(os.path.join(d, full_name))
                    self.log("取证[%s] 全屏图 debug/%s", tag, full_name)
            except Exception as e:
                self.log("取证[%s] 全屏图失败 %s", tag, e)

        # 3) 特征值 txt
        if features:
            try:
                feat_name = "c%03d_%s%s_特征_%s.txt" % (cast_no, pre, tag, stamp)
                lines = ["# FishingFun %s 关键节点特征" % _PKG_VERSION,
                         "时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                         "竿号: %d  %s" % (cast_no, "（首竿）" if pre else ""),
                         "节点: %s" % tag,
                         "区域图: %s" % region_name,
                         "全屏图: %s" % full_name,
                         "截屏区: %dx%d @ (%d,%d)" % (
                             (bmp_region.shape[1], bmp_region.shape[0], l, t)
                             if bmp_region is not None else (0, 0, l, t)),
                         "浮标框(局部): %s" % (box_local,),
                         "浮标框(屏幕): %s" % (box_screen,),
                         "坐标换算: 屏幕 = 局部 + (%d,%d)" % (l, t),
                         "-" * 60]
                for k, v in features.items():
                    if isinstance(v, (list, tuple)):
                        v = ", ".join(str(x) for x in v)
                    lines.append("%-14s = %s" % (k, v))
                with open(os.path.join(d, feat_name), "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                self.log("取证[%s] 特征 debug/%s", tag, feat_name)
                # 同时把关键值直接打进日志，不看文件也能核对
                self.log("取证[%s] 特征值: %s", tag,
                         " | ".join("%s=%s" % (k, v) for k, v in features.items()))
            except Exception as e:
                self.log("取证[%s] 特征写入失败 %s", tag, e)

        return (region_name, full_name, feat_name)

    def _anchor_features(self, cand_local, cand_screen):
        """★ v55：采集一根竿此刻的「浮标特征」字典（用于 txt + 日志）。

        字段全部来自 finder 真实存在的属性（2026-11-02 核对过）：
          · _last_block = (面积, 宽, 高)   —— 最后一次命中候选的尺寸
          · _last_detect_note = "候选=N 最佳=WxH size=A @(x,y)"
          · bobber_fingerprint = {area,w,h,rgb,frames} —— 首竿学到的指纹
        """
        bf = self.bobber_finder
        h = w = 0
        try:
            b = getattr(bf, "_calib_bmp", None) or getattr(bf, "last_bmp", None)
            if b is not None:
                h, w = b.shape[0], b.shape[1]
        except Exception:
            pass
        feats = {
            "浮标局部坐标": cand_local,
            "浮标屏幕坐标": cand_screen,
        }
        blk = getattr(bf, "_last_block", None)
        if blk:
            try:
                feats["浮标面积"], feats["浮标宽"], feats["浮标高"] = (
                    int(blk[0]), int(blk[1]), int(blk[2]))
                feats["填充率"] = round(float(blk[0]) / max(1, blk[1] * blk[2]), 3)
            except Exception:
                feats["浮标尺寸"] = blk
        note = getattr(bf, "_last_detect_note", "")
        if note:
            feats["本帧检测"] = note
        fp = getattr(bf, "bobber_fingerprint", None)
        if fp:
            feats["指纹"] = "%dx%d area=%d rgb=%s (%d帧)" % (
                fp.get("w", 0), fp.get("h", 0), fp.get("area", 0),
                fp.get("rgb"), fp.get("frames", 0))
        else:
            feats["指纹"] = "未建立（本竿即首竿）"
        if w and h:
            feats["截屏区"] = "%dx%d" % (w, h)
        return feats

    def _bite_features(self, current, anchor_screen, reason=None):
        """★ v55：采集咬钩瞬间的「咬钩特征值」。"""
        bw = self.bite_watcher
        feats = {
            "咬钩判据": reason or "?",
            "本竿锚点(屏幕)": anchor_screen,
            "当前浮标": current if current is not None else "已消失",
            "strike_value": getattr(bw, "strike_value", "?"),
            "effective_strike": getattr(bw, "effective_strike", "?"),
            "y_diff": getattr(bw, "y_diff", "?"),
        }
        ay = getattr(bw, "per_cast_anchor_y", None)
        if ay is not None:
            feats["本竿锚点y"] = ay
            if current is not None:
                feats["实际下沉px"] = int(ay - current[1])
        try:
            feats["面积衰减"] = self.bobber_finder.area_decayed()
        except Exception:
            pass
        yp = getattr(bw, "y_positions", None)
        if yp:
            feats["最近y序列"] = [int(v) for v in list(yp)[-12:]]
        return feats

    def _forensic_bite(self, current, anchor_screen, reason):
        """★ v55：咬钩节点取证（区域图+红框 / 全屏+红框 / 咬钩特征值）。

        current=None 表示浮标已消失（面积衰减式咬钩），此时红框画在
        本竿锚点位置 —— 那里正是刚刚消失的浮标，框住它才有意义。
        """
        try:
            blk = getattr(self.bobber_finder, "_last_block", None)
            # 注意：current 已是屏幕坐标（finder 返回屏幕坐标），
            # 所以这里只给 box_screen，由 _save_forensic 反算局部框，避免再次坐标错配。
            if current is not None and blk and len(blk) >= 3:
                bw, bh = int(blk[1]), int(blk[2])
                box_screen = (current[0] - bw // 2, current[1] - bh // 2,
                              current[0] + bw // 2, current[1] + bh // 2)
            else:
                box_screen = (anchor_screen[0] - 14, anchor_screen[1] - 14,
                              anchor_screen[0] + 14, anchor_screen[1] + 14)
            self._save_forensic(
                "咬钩", self._cast_no,
                box_screen=box_screen,
                features=self._bite_features(current, anchor_screen, reason))
        except Exception as e:
            self.log("咬钩取证失败 %s", e)

    def _prune_debug_frames(self):
        """★ v55：只保留最近 keep_debug_casts 竿的 debug 帧图，防止无限膨胀。

        按秒存图后实测一竿约 20~40 张，720x436 BMP ≈ 940KB/张
        → 约 20~40MB/竿。一天 200 竿就是 4~8GB，必须自动回收。

        规则：
          · 文件名 cNNN_... 里的竿号 < (当前竿号 - keep) 的一律删；
          · timeline.jsonl 同步过滤（保留未过期竿号的行）；
          · 取证图（forensic/，首竿特征+锚点红框）不删 —— 那是唯一一次
            标定的证据，删了以后无从追溯基准值从哪来；
          · 日志文件不动，永远全留。
        """
        keep = int(getattr(self, "keep_debug_casts", 20) or 0)
        if keep <= 0 or not (getattr(self, "debug_frames", False)
                             or getattr(self, "save_regions", False)):
            return
        import os
        import re
        d = os.path.join(os.getcwd(), self._debug_dir)
        if not os.path.isdir(d):
            return
        cutoff = self._cast_no - keep
        if cutoff < 1:
            return
        removed = 0
        freed = 0
        for fn in os.listdir(d):
            m = re.match(r"^c(\d+)_", fn)
            if not m or not fn.lower().endswith(".bmp"):
                continue
            if int(m.group(1)) < cutoff:
                try:
                    p = os.path.join(d, fn)
                    freed += os.path.getsize(p)
                    os.remove(p)
                    removed += 1
                except Exception:
                    pass
        # timeline.jsonl 同步裁剪
        tl = os.path.join(d, "timeline.jsonl")
        if removed and os.path.isfile(tl):
            try:
                import json
                rows = []
                with open(tl, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if int(r.get("cast", 0)) >= cutoff:
                            rows.append(line)
                with open(tl, "w", encoding="utf-8") as f:
                    f.write("\n".join(rows) + ("\n" if rows else ""))
            except Exception as e:
                self.log("timeline 裁剪失败 %s", e)
        if removed:
            self.log("debug 帧清理: 删除 %d 张 / %.1fMB（保留最近 %d 竿，"
                     "即 c%03d 起）", removed, freed / 1048576.0, keep, cutoff)

    def _dump_region(self, tag, bmp=None, extra="", force=False):
        """★ v45：存一帧「截屏区」原图到 debug/，文件名自带竿号+时间+区域信息。

        为什么要单独一个入口：
          · 用户要求「每杆都存区域截图」，一张图对应一帧日志；
          · 但每帧都存会拖慢检测 —— 实测 grab() + PIL 落盘 ~40ms，
            判咬钩循环本来一帧 ~42ms（真机实测中位 42ms，23.8fps），
            存图直接让帧率腰斩，真咬钩可能只出现 3~7 帧，慢一半就是漏判。
        命名：c003_锚定_1280x801@640,354_t12.3s_局部607x236.bmp

        ★ v55 按秒节流（用户要求「debug 图每秒存一次就行」）：
          普通帧受 save_interval_sec 控制（默认 1.0 秒 1 张）；
          以下情况无论如何都存，否则离线复盘会缺最关键的证据：
            · force=True 的调用（锚点锁定、咬钩判定、重抛失败等事件帧）
            · _dense_dump=True 的密集段（咬钩趋势出现后切 200ms 一张）
          注意：标签本身不做强制（"咬钩"阶段每帧都调本函数，
          若按标签强制就等于逐帧存图，与「每秒一张」冲突）。
        """
        if not (force or getattr(self, "debug_frames", False)
                or getattr(self, "save_regions", False)):
            return None

        # ---- v55 节流：决定这一帧到底存不存 ----
        now = time.monotonic()
        interval = float(getattr(self, "save_interval_sec", 1.0) or 0.0)
        dense = bool(getattr(self, "_dense_dump", False))
        crit = bool(force)
        last_t = getattr(self, "_last_dump_t", 0.0)
        last_cast = getattr(self, "_last_dump_cast", None)
        gap = (now - last_t) if last_cast == self._cast_no else 1e9
        # 密集段 200ms 一张；普通段 save_interval_sec（默认 1 秒）一张
        need = 0.2 if dense else interval
        if not crit and gap < need:
            self._dump_skipped = getattr(self, "_dump_skipped", 0) + 1
            return None
        if not crit and interval <= 0:
            # 间隔设 0 表示「只存事件帧」，普通帧一律不落盘
            self._dump_skipped = getattr(self, "_dump_skipped", 0) + 1
            return None

        import os
        from PIL import Image
        g = getattr(self.bobber_finder, "grabber", None)
        if g is None:
            return None
        try:
            if bmp is None:
                bmp = g.grab()
            if bmp is None:
                return None
            h, w = bmp.shape[0], bmp.shape[1]
            l = t = 0
            try:
                sa = g.search_area()
                l, t = int(sa[0]), int(sa[1])
            except Exception:
                pass
            d = os.path.join(os.getcwd(), self._debug_dir)
            os.makedirs(d, exist_ok=True)
            ts = time.strftime("%H%M%S")
            el = ""
            if getattr(self, "_last_cast_t", None):
                el = "_t%.1fs" % (time.monotonic() - self._last_cast_t)
            name = "c%03d_%s_%dx%d@%d,%d%s%s_%s.bmp" % (
                self._cast_no, tag, w, h, l, t, el,
                ("_" + extra) if extra else "", ts)
            Image.fromarray(bmp).save(os.path.join(d, name))
            # 记录节流状态
            self._last_dump_t = now
            self._last_dump_cast = self._cast_no
            # ★ v55：结构化清单。图片文件名里塞不下 yDiff / 检测坐标 / 阈值，
            #   而离线复盘要的就是「这一帧程序到底算出了什么」。每存一张图
            #   就追加一行到 debug/timeline.jsonl，字段与图一一对应，
            #   重放脚本按它精确对齐，不再靠解析文件名猜。
            try:
                import json
                rec = {
                    "cast": int(self._cast_no),
                    "tag": tag,
                    "file": name,
                    "t": round(float(now - (self._last_cast_t or now)), 3),
                    "wall": ts,
                    "elapsed": el.lstrip("_") or None,
                    "w": int(w), "h": int(h), "offset": [int(l), int(t)],
                    "detect": getattr(self, "_last_detect_xy", None),
                    "anchor": getattr(self, "_calib_anchor_local", None),
                    "anchor_y": getattr(self.bite_watcher, "per_cast_anchor_y", None),
                    "y": getattr(self.bite_watcher, "_last_y", None),
                    "y_diff": getattr(self.bite_watcher, "y_diff", None),
                    "strike": getattr(self.bite_watcher, "strike_value", None),
                    "dense": bool(getattr(self, "_dense_dump", False)),
                    "extra": extra or None,
                }
                with open(os.path.join(d, "timeline.jsonl"), "a",
                          encoding="utf-8") as _tf:
                    _tf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as _je:
                self.log("timeline 记录失败 %s", _je)
            self.log("区域帧[%s] debug/%s", tag, name)
            return name
        except Exception as e:
            self.log("区域帧[%s] 落盘失败 %s", tag, e)
            return None

    def dump_frame_pair(self, tag, index=0):
        """v36：同时存「全屏」+「截屏区」两张图，用于离线复盘。

        为什么要全屏：只存截屏区时，无法判断检测算出的坐标到底对应屏幕上
        哪个东西 —— 之前十几个版本全在这个盲区里猜。

        文件名形如 帧01_全屏_t1.0s.bmp / 帧01_区域_1280x911@640,244_t1.0s.bmp，
        坐标与区域尺寸直接写进文件名，拿到图就能对号入座。
        """
        import os
        from PIL import Image
        outdir = "debug"
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception:
            return
        ts = time.strftime("%H%M%S")
        # 1) 截屏区
        l = t = 0
        cw = ch = 0
        try:
            g = getattr(self.bobber_finder, "grabber", None)
            if g is not None:
                l, t, cw, ch = g.search_area()
        except Exception:
            pass
        try:
            bmp = None
            g = getattr(self.bobber_finder, "grabber", None)
            if g is not None:
                bmp = g.grab()
            if bmp is not None:
                fn = os.path.join(
                    outdir, "帧%02d_区域_%dx%d@%d,%d_%s.bmp"
                    % (index, bmp.shape[1], bmp.shape[0], l, t, ts))
                Image.fromarray(bmp).save(fn)
        except Exception as e:
            self.log("存区域帧失败 %s", e)
        # 2) 全屏（含 UI，用于人工核对浮标在屏幕上的真实位置）
        try:
            import mss
            import numpy as np
            with mss.mss() as sct:
                mon = sct.monitors[1]
                raw = sct.grab(mon)
            arr = np.frombuffer(bytes(raw.raw), np.uint8)
            arr = arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1]
            arr = np.ascontiguousarray(arr)
            fn = os.path.join(
                outdir, "帧%02d_全屏_%dx%d_%s.bmp"
                % (index, arr.shape[1], arr.shape[0], ts))
            Image.fromarray(arr).save(fn)
        except Exception as e:
            self.log("存全屏帧失败 %s", e)

    def _dump_confirm(self, bmp, tag, note, boxes=None):
        """存一张「需要人工确认」的截图，文件名自带说明。

        用户部署后跑一竿，就能凭 debug/ 里的图核对：
          - 排除区（左右 1/7、下方 1/4、天空分界线）画得对不对
          - 浮标真实位置在不在保留区里
          - 序列定位框住的是浮标还是整屏

        boxes: [(x0,y0,x1,y1,color_name), ...] 会在图上描边，
               颜色 红=找到的浮标 蓝=排除参考线 黄=候选。
        """
        import os
        from PIL import Image, ImageDraw
        d = os.path.join(os.getcwd(), "debug")
        os.makedirs(d, exist_ok=True)
        img = Image.fromarray(bmp).convert("RGB")
        draw = ImageDraw.Draw(img)
        palette = {"red": (255, 40, 40), "blue": (40, 140, 255),
                   "yellow": (255, 220, 0), "green": (40, 240, 120)}
        order = {"blue": 0, "green": 1, "yellow": 2, "red": 3}
        for bx in sorted((boxes or []), key=lambda b: order.get(b[4], 0)):
            x0, y0, x1, y1, cname = bx[0], bx[1], bx[2], bx[3], bx[4]
            col = palette.get(cname, (255, 255, 255))
            if y0 == y1:                       # 纯水平线（分界线），rectangle 画不出来
                draw.line([(x0, y0), (x1, y1)], fill=col, width=2)
            elif x0 == x1:                     # 纯垂直线
                draw.line([(x0, y0), (x1, y1)], fill=col, width=2)
            else:
                draw.rectangle([x0, y0, x1, y1], outline=col, width=2)
        safe = "".join(ch for ch in note if ch not in '\\/:*?"<>|')[:28]
        fn = os.path.join(d, "%s_%s_%s.bmp"
                          % (time.strftime("%H%M%S"), tag, safe))
        try:
            img.save(fn)
            self.log("需确认截图: %s", fn)
        except Exception as e:
            self.log("需确认截图写失败 %s", e)
            return None
        return fn

    def _boxes_for_zones(self, bmp, extra=None):
        """按当前 finder 的排除参数生成描边框（蓝色参考线）。"""
        h, w = bmp.shape[0], bmp.shape[1]
        bf = self.bobber_finder
        sx = int(w * getattr(bf, "exclude_side_ratio", 1.0 / 7.0))
        by = int(h * (1.0 - getattr(bf, "exclude_bottom_ratio", 0.25)))
        lim = getattr(bf, "sky_limit_y", None)
        top = int(lim) if lim is not None else 0
        boxes = [(0, 0, max(sx - 1, 0), h - 1, "blue"),
                 (w - sx, 0, w - 1, h - 1, "blue"),
                 (0, by + 1, w - 1, h - 1, "blue"),
                 (0, top, w - 1, top, "yellow")]
        if lim is not None:
            boxes.append((0, 0, w - 1, top, "blue"))
        return boxes + list(extra or [])

    def _maybe_calibrate_sink(self):
        """v21：第一竿抛竿后跑一次 17 秒采样，标定咬钩下沉幅度。

        为什么要标定：strike_value 是绝对像素数。写死 4 的时候，水面
        波纹 ±2~4px 的常规浮动就足以触发判定 —— 日志实测每次「咬钩」
        都是 yDiff=-4，且都在开始监控后 2 秒内，那是波纹不是鱼。
        不同钓点/分辨率下真实下沉幅度不同，只能实测。

        采样期间浮标还没进入咬钩监控，所以这 17 秒的鱼咬钩不会被
        误判；采完把 strike_value 改成实测值，本竿照常继续。
        """
        if not self.calib_sink or self._sink_calibrated:
            return
        self._sink_calibrated = True      # 无论成败都只做一次
        # 标定期间浮标本来就会漂/沉，找不到不算「这个钓点太暗」
        self.bobber_finder.suppress_abort = True
        # ★ v38：标定期连 no_bobber_frames 也要冻结。
        #   suppress_abort 只挡了 abort_cb，计数器仍每帧 +1，实测第一竿
        #   抛完后计数已经爬到 68（日志「连续 68 帧」），标定结束一解除
        #   抑制就立刻越线停机 —— 用户看到的就是「刚钓一竿就停」。
        self.bobber_finder.no_bobber_frames = 0
        try:
            from .sinkcalib import SinkCalibrator
            dump = self._dump_bmp if self.debug_frames else None
            calib = SinkCalibrator(
                self.bobber_finder,
                getattr(self.bobber_finder, "grabber", None),
                log=self.log,
                seconds=self.sink_calib_seconds,
                dump=dump)
            # v36：无论 --debug 是否开启，标定期每帧都存「全屏 + 区域」两张图。
            # 坐标/尺寸写在文件名里，方便直接对号入座复盘。
            try:
                calib._frame_dump = self.dump_frame_pair
            except Exception:
                pass
            # v38：把按键时刻传进去 —— 17 秒从按下 4 键算起（含飞行时间），
            # 采样窗口必须和游戏计时对齐，否则前几秒（含落水瞬间）全丢。
            got = calib.run(t_key=getattr(self, "_last_cast_t", None))
            # ★ v44：把标定期记录的「浮标本竿锚点」交给 _wait_for_bite。
            #   用户规定的流程：第 1 竿只标定（浮标特征 + 咬钩浮动值），
            #   标定完直接用这个锚点，不重新找、不做现场验证。
            try:
                self._calib_anchor_local = getattr(calib, "anchor", None)
                if self._calib_anchor_local is not None:
                    self.log("标定期记录锚点: 局部 %s", self._calib_anchor_local)
            except Exception:
                self._calib_anchor_local = None
            # v38：飞行时间告诉分析器，让它剔除空中的帧
            try:
                calib.cast_flight = float(getattr(self, "cast_flight", 0.0) or 0.0)
            except Exception:
                pass
            ref = None
            try:
                for (_t, _y, _a, b) in getattr(calib, "points", []):
                    if b is not None:
                        ref = b        # 取最后一帧作为确认图的底图
            except Exception:
                ref = None

            # v25：先把天空/水面分界线测出来并交给 finder。
            # 用户明确要求「严格按第一次抛竿后的画面确定排除区」，
            # 所以地平线不依赖序列定位成败，独立生效。
            try:
                hz, hconf = calib.detect_horizon()
            except Exception as e:
                hz, hconf = None, 0.0
                self.log("地平线检测异常 %s", e)
            bf0 = self.bobber_finder
            # 置信度门槛：低于 min_horizon_conf 视为「没测出」。
            # 实测：原版坐标 (640,400) 下海平面贴在第 1 行，检出
            # y=1 但置信只有 30x；而 370 坐标下置信 73x。贴边那种 30x
            # 虽然位置对，但上均亮 61 / 下均亮 49 差得很少，说明
            # 区里几乎没有天空 —— 这时按它排除会误伤第 2 行起的浮标。
            min_conf = getattr(self, "min_horizon_conf", 15.0)
            if hz is not None and hconf < min_conf:
                self.log("地平线 y=%d 置信度仅 %.1fx（< %.0fx），"
                         "判定为不可信，上方不做排除", int(hz), hconf, min_conf)
                hz = None
            if hz is not None:
                bf0.sky_limit_y = int(hz)
                self.log("排除区已定: 天空分界线 y=%d（置信度 %.1fx），"
                         "上方全部排除；左右各 %.0f%%、下方 %.0f%%",
                         int(hz), hconf,
                         100.0 * bf0.exclude_side_ratio,
                         100.0 * bf0.exclude_bottom_ratio)
            else:
                bf0.sky_limit_y = None
                self.log("排除区: 地平线未测出/不可信，上方不排除"
                         "（左右 %.0f%%、下方 %.0f%% 仍生效）",
                         100.0 * bf0.exclude_side_ratio,
                         100.0 * bf0.exclude_bottom_ratio)

            # 存一张排除区确认图（无论 --debug 是否开，这条信息必须人工核对）
            try:
                if ref is not None:
                    self._dump_confirm(
                        ref, "confirm_zone",
                        "分界线y%d" % (int(hz) if hz is not None else -1),
                        boxes=self._boxes_for_zones(ref))
            except Exception as e:
                self.log("排除区确认图异常 %s", e)

            # v23：用这 17 帧顺便定位浮标（不看颜色，只认「抛竿后持续
            # 存在且不动」的团块）。成功就用它固定锚点，比抛竿后 2 秒
            # 的单帧差分可靠得多 —— 后者实测 post 帧里根本没有浮标。
            try:
                loc = calib.locate_in_sequence()
            except Exception as e:
                loc = None
                self.log("序列定位异常 %s", e)
            if loc:
                bf = self.bobber_finder
                # 整屏被判成浮标 = 明显误判（实测 1276x634 全屏面积）。
                # 这种情况不采信锚点，也不放宽 tan —— 否则门槛塌掉。
                lw = loc.get("w", 0)
                lh = loc.get("h", 0)
                la = loc.get("area", 0)
                suspicious = False
                if ref is not None:
                    rh, rw = ref.shape[0], ref.shape[1]
                    if la > 0.20 * rw * rh:
                        suspicious = True
                if suspicious:
                    self.log("序列定位可疑: 面积 %d 占画面 %.0f%%，判为误判，不采信锚点"
                             "（保留原版颜色搜索）", la,
                             100.0 * la / float(rw * rh) if ref is not None else 0.0)
                    loc = None

            if loc:
                bf = self.bobber_finder
                if hasattr(bf, "previous_location"):
                    bf.previous_location = (loc["x"], loc["y"])
                bf._calib_result = loc
                try:
                    sig = __import__("fishingfun.bobbercalib", fromlist=["x"]).signature_from(loc["median_rgb"])
                    c = bf.pixel_classifier
                    c.tan_min_red = min(c.tan_min_red, sig["tan_min_red"])
                    c.tan_min_rb = min(c.tan_min_rb, sig["tan_min_rb"])
                    c.tan_min_rg = min(c.tan_min_rg, sig["tan_min_rg"])
                    self.log("锚点已按序列定位固定: (%d,%d) → tan 放宽为 r>=%d rb>=%d rg>=%d",
                             loc["x"], loc["y"],
                             c.tan_min_red, c.tan_min_rb, c.tan_min_rg)
                except Exception as e:
                    self.log("锚点固定但 tan 放宽失败 %s", e)
                # 确认图：红框=序列定位认定的浮标
                try:
                    if ref is not None:
                        bx = [(int(loc["x"] - lw / 2), int(loc["y"] - lh / 2),
                               int(loc["x"] + lw / 2), int(loc["y"] + lh / 2), "red")]
                        self._dump_confirm(ref, "confirm_anchor",
                                           "锚点%d_%d" % (int(loc["x"]), int(loc["y"])),
                                           boxes=self._boxes_for_zones(ref, bx))
                except Exception as e:
                    self.log("锚点确认图异常 %s", e)
            else:
                self.log("序列定位未命中，锚点仍由原版颜色搜索决定")
                try:
                    if ref is not None:
                        self._dump_confirm(ref, "confirm_anchor_none", "序列定位未命中",
                                           boxes=self._boxes_for_zones(ref))
                except Exception as e:
                    self.log("锚点确认图异常 %s", e)


            if got:
                old = getattr(self.bite_watcher, "strike_value", None)
                self.bite_watcher.strike_value = int(got)
                self.log("咬钩阈值已按实测标定: strike_value %s → %d", old, int(got))
                # ★ v55 用户要求第①条：首竿「咬钩特征值」必须落盘取证。
                #   这是整场唯一一次标定，一旦没留证据，之后所有咬钩判定的
                #   基准值从哪来就无从追溯。区域图用标定参考帧（ref），
                #   红框打在本竿锚点上，txt 记 strike 前后值和采样统计。
                if getattr(self, "forensic", False):
                    try:
                        feat = {
                            "标定类型": "首竿下沉幅度标定（静水采样）",
                            "采样窗口": "%.0fs（含飞行时间）" % float(self.sink_calib_seconds),
                            "采样帧数": len(getattr(calib, "points", [])),
                            "标定前_strike": old if old is not None else "?",
                            "标定后_strike": int(got),
                            "静水抖动P90": round(float(getattr(calib, "p90", 0.0) or 0.0), 2),
                            "最大净下沉": getattr(calib, "max_sink", "?"),
                            "浮动估计": round(float(getattr(calib, "float_est", 0.0) or 0.0), 2),
                            "说明": "此后每竿判咬钩都用这一个阈值，锚点按竿重定、阈值不重标",
                        }
                        anchor = getattr(self, "_calib_anchor_local", None)
                        # _save_forensic(tag, cast_no, bmp_region, box_local, ...)
                        # 标定锚点是「局部坐标」，只给 box_local，
                        # 屏幕框由函数按截屏区原点反算，不猜。
                        self._save_forensic("首竿标定", self._cast_no,
                                            bmp_region=ref, box_local=anchor,
                                            features=feat)
                    except Exception as _fe:
                        self.log("取证[首竿标定] 失败 %s", _fe)
            else:
                self.log("咬钩标定没拿到有效数据，沿用原阈值 %s",
                         getattr(self.bite_watcher, "strike_value", "?"))
        except Exception as e:
            self.log("咬钩标定异常 %s，沿用原阈值", e)
        finally:
            # 标定结束，恢复正常判定
            self.bobber_finder.suppress_abort = False
            self.bobber_finder.no_bobber_frames = 0

    def _has_yellow_post(self, bmp):
        """这一帧里有没有黄浮标（够多才算落水）。

        阈值 60px：实测真实浮标 652~657px，水面噪声 0px，
        取 60 留足余量又不至于被零星高光骗过。
        """
        try:
            from .bobbercalib import yellow_mask
            return int(yellow_mask(bmp).sum()) >= 60
        except Exception:
            return False

    # ---- 逐行对应 FishBot.cs:76-86 ----
    def _watch(self, milliseconds):
        self.bobber_finder.reset()
        # 飞行期：浮标还在天上/刚落水，这一段的「找不到」没有任何诊断价值，
        # 不能让它凑满 no_bobber_limit 直接停机（实测第一竿 0.7s 就停了）。
        self.bobber_finder.suppress_abort = True
        # ★ v37：飞行期禁止定锚。
        #   实测（帧数据验证）：抛竿后 53ms，浮标还在空中，画面是上一竿的
        #   残留影像。此时全图搜索会抓到一个「连续 3 帧位置都稳定」的暖色块
        #   （实测 (701,24) 26x27 area=445，真浮标却在 (820,342)，差 123/318 px），
        #   一旦写进 previous_location 就锁死在 ±40px 窗口里追错目标到收竿。
        #   飞行期只记录位置、不回写锚点；落水后再由稳定性判定定锚。
        self.bobber_finder.no_anchor_lock = True
        if hasattr(self.bobber_finder, "loose_aspect"):
            self.bobber_finder.loose_aspect = True     # 抛竿飞行期长宽比失真
        start = time.monotonic()
        self._trace_next = 0.0      # ★ v45：逐秒采样下一次触发时刻（相对 start）
        frames = hits = 0
        last = None
        # ★ v52：可信度分母只统计「浮标确实该在画面里」的帧。
        #   真机日志（09:56 第 2 竿）：锚点可信度 2/40 帧 = 5% → 放弃重抛。
        #   那 40 帧里有 1.8 秒飞行期（浮标还在空中）+ 前面等待黄色浮标的时段
        #   （浮标刚落水还没稳定），这些帧「找不到浮标」是物理必然，
        #   拿去当分母等于自罚。这里从「第一次命中」开始计帧。
        frames_inview = 0
        positions = []          # ★ v42：记录每次命中位置，用于取众数
        _blocks = []            # ★ v43：(pos, (area,w,h)) 用于首竿建浮标指纹
        last_bmp = None
        # v22：post 帧不再取「2 秒那一刻」，而是取「黄色浮标真正出现」那一帧。
        # 实测 c006_post（2 秒帧）整帧 r max 只有 122，黄浮标(r=255)一个都没有
        # —— 浮标还在空中。用 0 个黄像素的帧去差分，必然挑到水面杂块。
        post_bmp = None
        post_at = None
        post_deadline = start + 6.0      # 最多等 6 秒，等不到就用最后一帧兜底
        try:
            while (time.monotonic() - start) * 1000.0 < milliseconds:
                pos = self.bobber_finder.find()
                frames += 1
                # ★ v52：浮标第一次出现之后，每一帧都算「应该在画面里」
                if positions:
                    frames_inview += 1
                # v20：留存本次抓到的原始帧，循环结束时它就是 post
                last_bmp = getattr(self.bobber_finder, "_bmp", None)
                # v22：这一帧里已经能看见黄浮标 → 它就是合格的 post
                if post_bmp is None and last_bmp is not None:
                    if self._has_yellow_post(last_bmp):
                        post_bmp = last_bmp
                        post_at = time.monotonic() - start
                if pos is not None:
                    hits += 1
                    last = pos
                    # ★ v53 核心修复：find() 返回的是【屏幕坐标】，而下面
                    #   聚类/稳定位/previous_location 全都要【局部坐标】。
                    #   真机日志（2026-11-02 10:37:43 第 1 竿）铁证：
                    #     抛竿后跟踪 2.0s: 37 帧, 命中 7 帧, 末位置 (917, 506)
                    #     追踪稳定位(局部): (910, 511)      ← 屏幕坐标冒充局部
                    #     找锚点: 用本竿跟踪稳定位 局部 (910, 511) 作为搜索窗口中心
                    #   而截屏区是 720x436@360,210，真浮标局部 (557,296)。
                    #   910-360=550 / 511-210=301 → 就是浮标；不改的话
                    #   previous_location 被写成屏幕值，搜索窗口直接跑到图外
                    #   353px，后面每帧 find() 都在错误窗口里找，
                    #   日志表现为「三帧确认: 候选 (917,506) 暖色闸门不过」反复刷。
                    _pl = self._to_local(pos) if pos is not None else None
                    positions.append(_pl if _pl is not None else pos)   # ★ v53
                    _lb = getattr(self.bobber_finder, "_last_block", None)
                    if _lb is not None:
                        # ★ v53：_blocks 与 positions 同坐标系（局部），
                        #   否则下面按 stable(局部) 筛指纹样本会 100% 不匹配。
                        _blocks.append((_pl if _pl is not None else pos, _lb))
                # ★ v45：跟踪期逐秒留痕。用户要「后面每一杆的锚点 + 每秒值」
                #   都能查，而 _watch 这 2 秒正是浮标落水后第一步定锚的窗口。
                _tr = getattr(self, "trace_seconds", 0) or 0
                if ((getattr(self, "debug_frames", False)
                     or getattr(self, "save_regions", False))
                        and _tr > 0):
                    _el = time.monotonic() - start
                    if _el >= self._trace_next:
                        self._trace_next = _el + _tr
                        self.log("跟踪采样 t=%.1fs 检测=%s 帧=%d 命中=%d",
                                 _el, pos, frames, hits)
                        try:
                            lb = getattr(self.bobber_finder, "_bmp", None)
                            self._dump_region(
                                "跟踪", bmp=lb, extra="检测%d_%d" % (
                                    (pos[0], pos[1]) if pos else (-1, -1)))
                        except Exception as e:
                            self.log("跟踪采样存帧异常 %s", e)
            # _watch 结束后继续等黄色浮标出现，最多到 6 秒。
            # ★ v38：加一条「已经靠色差锁定浮标」的提前退出。
            #   实测日志：20:42:03.607 色差浮标已命中 (775,280)，
            #   但这里仍然硬等满 6 秒（20:42:09.025 才退出），
            #   把后续标定的 17 秒窗口吃掉 4 秒 —— 标定 t0 从 6.8s 起算，
            #   采样只覆盖到 16s 就被 17s 上限截断。
            #   既然色差已经稳定命中（连续 3 帧确认过），就没有必要再等
            #   「黄色浮标」这个更弱的判据；两帧差分标定用哪一帧都能做。
            while post_bmp is None and time.monotonic() < post_deadline:
                # 色差已锁定 → 当前帧就是合格的 post，立即收工
                if last is not None:
                    lb = getattr(self.bobber_finder, "_bmp", None)
                    if lb is not None:
                        post_bmp = lb
                        post_at = time.monotonic() - start
                        self.log("post 帧: 色差已锁定浮标 (%.1fs)，不再等黄色判据",
                                 post_at)
                        break
                pos = self.bobber_finder.find()
                last_bmp = getattr(self.bobber_finder, "_bmp", None)
                if pos is not None:
                    last = pos
                    # ★ v53：与 _watch 主循环同口径 —— 一律存局部坐标。
                    #   v43 这里漏改，导致同一竿里 positions 混进屏幕坐标，
                    #   聚类把 (557,296)屏幕→(917,506) 和真局部点分别成簇，
                    #   最大簇可能是错的那一簇。
                    _pl2 = self._to_local(pos)
                    positions.append(_pl2 if _pl2 is not None else pos)
                if last_bmp is not None and self._has_yellow_post(last_bmp):
                    post_bmp = last_bmp
                    post_at = time.monotonic() - start
                    break
                time.sleep(0.05)
        finally:
            if hasattr(self.bobber_finder, "loose_aspect"):
                self.bobber_finder.loose_aspect = False
            # v37：飞行期结束，放行定锚
            self.bobber_finder.no_anchor_lock = False
        # v22：post 帧优先用「浮标已出现」那一帧；没等到才用最后一帧兜底
        used = post_bmp if post_bmp is not None else last_bmp
        if used is not None and hasattr(self.bobber_finder, "calib_pre"):
            self.bobber_finder._calib_bmp = used
            if post_bmp is not None:
                self.log("post 帧: 浮标已出现（抛竿后 %.1fs）", post_at or 0.0)
            else:
                self.log("post 帧: 等黄色浮标 6s 未出现，改用最后一帧兜底")
            if (getattr(self, "debug_frames", False)
                    and getattr(self.bobber_finder, "calib_pre", None) is not None):
                try:
                    self._dump_bmp(used, "calib_post")
                except Exception as e:
                    self.log("差分标定: post 落盘失败 %s", e)
        self.log("抛竿后跟踪 %.1fs: %d 帧, 命中 %d 帧, 末位置 %s",
                 milliseconds / 1000.0, frames, hits, last)
        # ★ v42：last 是「最后一次命中」，但追踪尾声可能抓到水面高光的假目标。
        #   实测日志（v41 真机）：浮标 31/39 帧稳定在 (544,269)，末位置却是
        #   (1184,622) —— 差了 640px。这个错值被当成跟踪期锚点带进标定之后
        #   的找锚点阶段，搜索窗口锁死在右下角，连续 3 帧全丢 → 误报停机。
        #   改为：对全部命中位置做距离聚类，取最大簇的质心。
        #   （不能按固定网格分箱：真浮标会分散在相邻两格各自计票，
        #     反而输给假目标。）
        stable = None
        if positions:
            clusters = []
            for _p in positions:
                _home = None
                for _c in clusters:
                    for _q in _c:
                        if max(abs(_p[0] - _q[0]), abs(_p[1] - _q[1])) <= 12:
                            _home = _c
                            break
                    if _home is not None:
                        break
                if _home is None:
                    clusters.append([_p])
                else:
                    _home.append(_p)
            clusters.sort(key=len, reverse=True)
            best = clusters[0]
            cnt = len(best)
            xs = [p[0] for p in best]
            ys = [p[1] for p in best]
            span = max(max(xs) - min(xs), max(ys) - min(ys))
            frac = cnt / float(len(positions))
            need = max(5, int(len(positions) * 0.45))
            if cnt >= need and span <= 60:
                stable = (int(sum(xs) / float(cnt)), int(sum(ys) / float(cnt)))
                self.log("追踪稳定位(局部): %s（最大簇 %d/%d 帧，需≥%d，跨度 %dpx）",
                         (int(stable[0]), int(stable[1])),
                         cnt, len(positions), need, span)
                # ★ v53 自检：稳定位必须落在截屏区图像范围内。
                #   v52 之前这里打印过 (910,511)，而截屏区只有 720x436
                #   —— 屏幕坐标冒充局部坐标，肉眼一看就是错的。
                #   现在再加一条越界告警，防止以后再混进去。
                try:
                    _g = getattr(self.bobber_finder, "grabber", None)
                    _sa = _g.search_area() if _g is not None else None
                    _bmp_chk = getattr(self.bobber_finder, "_bmp", None)
                    if _sa and _bmp_chk is not None:
                        _iw = int(_bmp_chk.shape[1])
                        _ih = int(_bmp_chk.shape[0])
                        if not (0 <= stable[0] < _iw and 0 <= stable[1] < _ih):
                            self.log("⚠️ 追踪稳定位越界 局部(%d,%d) 不在截图区 %dx%d 内，"
                                     "疑似坐标系混用，本竿不提供跟踪期锚点",
                                     stable[0], stable[1], _iw, _ih)
                            stable = None
                except Exception as _e:
                    self.log("追踪稳定位越界自检异常 %s", _e)
            else:
                self.log("追踪位不稳: 最大簇 %d/%d 帧（需≥%d），跨度 %dpx（需≤60）"
                         " → 不提供跟踪期锚点",
                         cnt, len(positions), need, span)
        self._last_track = (max(frames_inview, 10), hits)   # ★ v52：分母已去掉飞行期/等待期
        #   分母再设 10 帧下限：浮标只露 1 帧就丢（1/1=100%）不能算高可信。
        # ★ v52：坐标系铁律 —— positions 里的值就是【局部坐标】（截屏区图像内），
        #   因为 find() 的每条返回路径都是 _to_screen(局部)，即 局部 + sa[0..1]，
        #   而 sa 在 arm_area() 之后整竿不变、每帧用的又是同一张截屏图像。
        #   所以这里直接存 stable，不做任何二次换算，日志也直接打局部值。
        #   真机日志（2026-11-02 09:56，100% 缩放）证明 v51 减错了：
        #     追踪稳定位: (646, 284) 最大簇 18/19 帧 跨度 0px
        #     锚点校验: 局部 (286,123) 高对比亮团 4px → 拒绝
        #     （截屏区 720x382@360,161 → 646-360=286，284-161=123）
        #   646/284 减完落到左上角一片死水，校验当然只有 4px 亮团。
        self._last_track_stable = stable
        # ★ v43：首竿建「浮标指纹」。只取落在稳定簇内的块 —— 那些才是
        #   通过聚类校验的真浮标帧，边缘散点（水面反光）不参与。
        if stable is not None and _blocks and \
                getattr(self.bobber_finder, "bobber_fingerprint", None) is None:
            _samples = [(b[0], b[1], b[2], None) for p, b in _blocks
                        if abs(p[0] - stable[0]) <= 20 and abs(p[1] - stable[1]) <= 20]
            self.bobber_finder.learn_fingerprint(_samples, log=self.log)
        # ★ v43：标记「颜色搜索是否已稳定锁定」，供差分标定交叉校验用。
        #   仅当最大簇覆盖 ≥45% 帧且跨度 ≤60px 时才算稳定（与上面同一判据）。
        self._anchor_color_stable = (stable is not None)
        # ★ v43：只要跟踪期命中够多，就记下「本竿锁定过浮标」。
        #   否则后续复用失败时，停机文案会误报「本竿从未锁定浮标」。
        if hits >= 10:
            self._anchor_locked_this_cast = True
        # 飞行期结束：下面开始正式找锚点，这时候找不到才是真问题
        self.bobber_finder.suppress_abort = False
        self.bobber_finder.no_bobber_frames = 0

        # ★ v51：把「跟踪期稳定位」作为【本竿候选】交给找锚点阶段。
        #   真机日志（2026-11-02 09:52:08~09:52:13 第 2 竿）暴露的问题：
        #     抛竿后跟踪 2.0s: 40 帧, 命中 8 帧 → 跟丢了；
        #     anchor_wait 3.3s 里连一帧都没定上锚点 → 重抛。
        #   原因是 find() 每帧只有一个 previous_location（±40px 窗口），
        #   命中率只有 20% 时，下一帧窗口就漂走了，越扫越远。
        #   而 8 帧命中里本来就有 7 帧稳定聚在同一个点（日志：最大簇 7/8 帧）。
        #   所以：把那个稳定簇作为「本竿候选窗口中心」写进 previous_location，
        #   让补扫的每一帧都围绕它找 —— 命中率会从 20% 拉回高位。
        #   注意这绝不是跨竿/跨阶段搬运：它来自「本竿抛竿后的画面」，
        #   而且下面还要过亮团闸门 + 三帧一致性确认，验不过照样重抛。
        _st_l = getattr(self, "_last_track_stable", None)   # v51：局部坐标
        if _st_l is not None:
            try:
                self.bobber_finder.previous_location = (int(_st_l[0]), int(_st_l[1]))
                self.log("找锚点: 用本竿跟踪稳定位 局部 %s 作为搜索窗口中心", tuple(_st_l))
            except Exception as _e:
                self.log("找锚点: 稳定位写入失败 %s", _e)
                self.bobber_finder.previous_location = None
        else:
            try:
                self.bobber_finder.previous_location = None
            except Exception:
                pass

        # 注意：抛竿后立刻抓一帧 BMP 会占满 IO，且此刻光标可能还在画面里，
        # 只在「找不到浮标」时才落盘（见 _wait_for_bite），平时不抓。

    # ---- 逐行对应 FishBot.cs:94-125 ----
    def _wait_for_bite(self):
        self.bobber_finder.reset()

        # ★ v41：如果 _watch(2000) 跟踪期已经稳定锁定过浮标，标定结束后
        #   直接复用那个位置 —— 不要再用 anchor_wait 重新找一遍。
        #   原因（实测日志 20:42:21.497）：
        #     DIAG找锚点 退出耗时=0.15s 帧=3 miss(落水后)=0 anchor_wait=1.5
        #   标定 _maybe_calibrate_sink() 跑了 10.2 秒（17 秒窗口采样），
        #   这段期间浮标可能已经沉下去/视图变了，标定结束后再花 1.5 秒
        #   找锚点必然失败，然后误报「这个钓点太暗了」并停机。
        #   复用跟踪期锚点，等于把「找锚点」这一步彻底省掉。
        self._calib_anchor_local = None
        # ★ v51：复用跟踪期锚点的整条捷径已删除（证据见 _tracked_anchor 处注释）。
        #   以前这段会：坐标换算 → 亮团闸门校验 → find() 探针 → 再三帧确认，
        #   实测只有一个结局 —— 抛异常或校验不过，然后照样退回下面的现场找锚点，
        #   白花 0.7~1.5 秒（真机 09:52:16.127 → 09:52:17.129 正好 1.0 秒）。
        #   现在直接把 previous_location 清空，让 find() 从全画面开始扫。
        self._tracked_anchor = None
        try:
            self.bobber_finder.previous_location = None
        except Exception:
            pass

        # 非原版改进：找锚点阶段。抛竿后浮标要飞 0.5~1 秒才落水，落水后
        # 还有水花遮挡，这一段单帧扫不到浮标是正常的。所以不能「一帧没扫
        # 到就重抛」（v14 的 anchor_wait=1.5s 单次判定就是被用户看到的
        # 「1 秒重复抛竿」）。改为：连续 miss_frames 帧且总时长超过
        # anchor_wait 才算真的没有浮标。
        anchor_wait = getattr(self, "anchor_wait", 4.0)
        miss_need = getattr(self, "anchor_miss_frames", 12)   # v18 起不再用于提前退出
        # v18：抛竿后浮标要飞 0.5~1.8 秒才落水，加水面荡开的水花，这段时间
        # 全部 miss 是必然而非异常。旧代码 `miss >= 12 and elapsed >= 1.2`
        # 会在 1.2 秒就退出（v17 实测 20/20 次全是 miss@1.2s），把
        # --anchor-wait 3.0 完全架空，这就是"抛竿1秒多又重抛"的根因。
        # 现在改为：飞行期内 miss 一律不计数，之后全程等满 anchor_wait。
        flight = getattr(self, "cast_flight", 1.8)
        # ★ v38：anchor_wait 必须大于飞行期，否则找锚点循环在浮标还没落水
        #   时就超时退出了。实测日志（v38 第一次真机跑）：
        #       找锚点超时: 1.5s | 飞行期 1.8s
        #       DIAG找锚点 退出耗时=0.15s ... anchor_wait=1.5 飞行期=1.8s
        #   1.5 < 1.8，浮标还在空中就已经「找不到」，于是每竿都判重抛，
        #   用户看到的是「抛一竿就停」。这里强制抬到 flight + 1.5s 的落水
        #   观察余量，保住原本语义又不与飞行期矛盾。
        min_wait = flight + 1.5
        if anchor_wait < min_wait:
            self.log("找锚点超时 %.1fs < 飞行期 %.1fs + 余量，自动抬到 %.1fs"
                     "（否则浮标还在空中就判重抛）",
                     anchor_wait, flight, min_wait)
            anchor_wait = min_wait
        t0 = time.monotonic()
        bobber_position = None
        miss = 0
        frames = 0

        # v20：差分标定。_watch(2000) 已经把 post 帧存好了，先试着用
        # 「抛竿前 vs 抛竿后2秒」两张图差出浮标。成功就直接当锚点，
        # 完全不用碰颜色阈值 —— 换钓点浮标被压暗也不会失效。
        bf = self.bobber_finder
        if (getattr(bf, "calib_enabled", False)
                and getattr(bf, "calib_pre", None) is not None
                and getattr(bf, "_calib_bmp", None) is not None):
            # ★ v43：差分结果必须和「颜色搜索已经稳定锁定的位置」交叉校验。
            #   真机实测 21:17:25：_watch 跟踪期色差连中 19 帧锁定 (548,235)，
            #   差分却返回 (388,641)（深蓝水面块），两条路差 400+ px。
            #   原来直接用差分覆盖，整竿锚点就错了。规则：
            #     · 色差已稳定锁定 → 差分只有在「同一位置」（±60px）时才采纳
            #     · 色差没锁定   → 差分可用，但 _calibrate 内部已做暖色自检
            # ★ v51：交叉校验改成用「本竿 _watch 期稳定簇」和「差分结果」比，
            #   不再读 _tracked_anchor（v51 已彻底停用那条捷径）。
            #   v43 的问题依然成立：真机 21:17:25 色差连中 19 帧锁定 (548,235)，
            #   差分却返回 (388,641)（深蓝水面块），两条路差 400+ px。
            #   所以差分也要和颜色搜索的稳定结果对齐：
            #     · 颜色搜索本竿已稳定 → 差分只有落在同一位置（±60px）才采纳
            #     · 颜色搜索没稳定   → 差分可用，但下面仍要过亮团闸门+三帧确认
            _color_lock = None
            if getattr(self, "_anchor_color_stable", False):
                _st = getattr(self, "_last_track_stable", None)   # v51：已是局部坐标
                if _st is not None:
                    _color_lock = (int(_st[0]), int(_st[1]))
            try:
                bobber_position = bf._calibrate(bf._calib_bmp)
            except Exception as e:
                self.log("差分标定异常: %s", e)
                bobber_position = None
            if bobber_position is not None and _color_lock is not None:
                # 两边统一换算成局部坐标再比（差分本来就是局部坐标）
                _d = max(abs(bobber_position[0] - _color_lock[0]),
                         abs(bobber_position[1] - _color_lock[1]))
                if _d > 60:
                    self.log("差分标定否决: 差分 (%d,%d) 与颜色搜索稳定位置 局部 %s 相距 %dpx"
                             "（>60），不采信，改用颜色搜索",
                             bobber_position[0], bobber_position[1], _color_lock, _d)
                    bf.previous_location = None
                    bobber_position = None
            if bobber_position is not None:
                # ★ v46：差分结果同样要过暖色校验。差分偶尔会在水面挑出
                #   「稳定但冷色」的块（真机实测整片水面 242 个候选全冷色），
                #   这类块一旦当锚点，后续 ±40px 粘滞会把真浮标挡住。
                if not self._anchor_has_bobber(bobber_position,
                                               bmp=bf._calib_bmp):
                    self.log("差分标定否决: (%d,%d) 暖色校验不过，不采信，改用颜色搜索",
                             bobber_position[0], bobber_position[1])
                    bf.previous_location = None
                    bobber_position = None
            if bobber_position is not None:
                # ★ v46：差分只是「候选」，同样要过三帧一致性确认才定锚点。
                bf.previous_location = bobber_position
                _anchor, _info = self._confirm_anchor_3frames(bobber_position,
                                                             max_frames=12)
                if _anchor is None:
                    self.log("差分候选 (%d,%d) 三帧确认失败（%s），退回颜色搜索",
                             bobber_position[0], bobber_position[1], _info)
                    bf.previous_location = None
                    bobber_position = None
                else:
                    bobber_position = _anchor
                    bf.previous_location = self._to_local(_anchor)
            if bobber_position is not None:
                # 锁定到 previous_location，后续帧走原版 ±40px 窗口跟踪
                self.log("浮标位置(本竿锚点): 屏幕 %s | 局部 %s（差分标定+三帧确认）",
                         bobber_position, self._to_local(bobber_position))
            else:
                self.log("差分标定未命中，退回逐帧颜色搜索")
        # 差分没成功才走原版找锚点循环
        if bobber_position is None:
            # ★ v43：找锚点期间禁用 find() 内部的 abort，由本循环统一决定放弃时机。
            #   原来 find() 的 abort_cb 在 no_bobber_frames 到 3 时直接把 Bot 打停，
            #   抢在本循环的 anchor_wait / miss 重试逻辑之前，导致真机日志里
            #   「miss(落水后)=0 却 abort」、「本竿从未锁定浮标」自相矛盾。
            _saved_abort = getattr(bf, "abort_cb", None)
            if _saved_abort is not None:
                bf.abort_cb = None
            try:
                while self.is_enabled:
                    elapsed = time.monotonic() - t0
                    if elapsed >= anchor_wait:
                        break
                    # ★ v37：飞行/水花期内只检测、不锁定锚点。
                    #   实测日志 20:06:22.445（抛竿后 53ms）锁到的 (701,24)
                    #   是上一竿残留影像：那块 rgb=(29,48,65) R-B=-31，
                    #   按色差判据根本不可能选中，是 _find_points 的 TAN 路径
                    #   在空画面里凑出来的。落水前锁定它 = 整竿追错目标。
                    bf = self.bobber_finder
                    if hasattr(bf, "no_anchor_lock"):
                        bf.no_anchor_lock = elapsed < flight
                    try:
                        bobber_position = self.bobber_finder.find()
                    finally:
                        if hasattr(bf, "no_anchor_lock"):
                            bf.no_anchor_lock = False
                    frames += 1
                    if bobber_position is not None:
                        # ★ v46：找到候选后不直接定锚点，先做「三帧一致性确认」。
                        #   用户要求：后面每一竿都按第一竿标定的标准确认锚点。
                        #   首竿标定是多帧采样+序列定位，但标定一结束浮标就沉了，
                        #   所以在标定期之外补上这套三帧确认：连续 3 帧同一位置
                        #   才算锁定，单帧命中一律存疑。
                        _anchor, _info = self._confirm_anchor_3frames(
                            bobber_position, max_frames=12)
                        if _anchor is not None:
                            bobber_position = _anchor
                            bf.previous_location = self._to_local(_anchor)
                            self.log("三帧确认: 耗时 %d 帧，跨度 %dpx",
                                     _info.get("frames", 0), _info.get("span", 0))
                            break
                        self.log("三帧确认失败（%s），继续扫锚点", _info)
                        try:
                            bf.previous_location = None
                        except Exception:
                            pass
                        bobber_position = None
                        if elapsed >= flight:
                            miss += 1
                        if time.monotonic() - t0 >= anchor_wait + 2.0:
                            self.log("找锚点: 三帧确认迟迟不成立，延长等待到 %.1fs",
                                     anchor_wait + 2.0)
                            anchor_wait += 2.0
                        continue
                    # v18：飞行/水花期内不计 miss，只统计"落水后仍找不到"的次数
                    if elapsed >= flight:
                        miss += 1
            finally:
                if _saved_abort is not None:
                    bf.abort_cb = _saved_abort
                    # 找锚点失败时把计数清零，避免残留计数让下一竿一上来就 abort
                    if bobber_position is None:
                        bf.no_bobber_frames = 0

        if bobber_position is None:            # Point.Empty
            # v18：已删除 miss 提前退出，现在只可能是 anchor_wait 超时
            self.log("DIAG找锚点 退出耗时=%.2fs 帧=%d miss(落水后)=%d anchor_wait=%.1f 飞行期=%.1fs 原因=anchor_wait超时",
                     time.monotonic() - t0, frames, miss, anchor_wait, flight)
            try:
                self._diag["noanchor"].append(f"超时@{time.monotonic()-t0:.1f}s")
            except Exception:
                pass
            self.log("未找到浮标（%.1fs / %d 帧全丢），重抛",
                     time.monotonic() - t0, frames)
            try:
                self._dump_frame("lost", None)   # 只在失败时存一张，事后排查用
            except Exception as e:
                self.log("诊断: 失败帧落盘失败 %s", e)
            try:
                self._dump_region("重抛", extra="找锚点失败", force=True)
            except Exception as e:
                self.log("重抛帧落盘失败 %s", e)
            # 确认图：这一帧里浮标到底在不在、在不在排除区外
            try:
                g = getattr(self.bobber_finder, "grabber", None)
                if g is not None:
                    fb = g.grab()
                    self._dump_confirm(fb, "confirm_lost", "找锚点失败", 
                                       boxes=self._boxes_for_zones(fb))
            except Exception as e:
                self.log("确认图异常 %s", e)
            return

        self.bite_watcher.reset(bobber_position)
        # ★ v46：日志坐标标注统一为「屏幕 x,y | 局部 x,y」，避免把局部坐标
        #   误读成屏幕坐标（v45 真机日志把两者混在一行，排查时严重误导）。
        self.log("浮标位置(本竿锚点): 屏幕 %s | 局部 %s（现场检测锁定）",
                 bobber_position, self._to_local(bobber_position))
        # ★ v45：锚点定下来立刻存一张区域图 —— 之后浮标一沉，画面就变了，
        #   这张是唯一能事后核对「锚点打在哪」的证据。
        self._dump_region("锚定", extra="锚点%d_%d" % (bobber_position[0],
                                                      bobber_position[1]), force=True)
        # ★ v55：关键节点取证 —— 浮标区域图(红框) + 全屏图(红框) + 特征值
        #   首竿的文件名带「首竿_」，一眼能找到「首竿拿到的浮标特征」。
        try:
            _loc = self._to_local(bobber_position)
            _blk = getattr(self.bobber_finder, "_last_block", None)
            _box_l = None
            if _blk and len(_blk) >= 3:
                _bw, _bh = int(_blk[1]), int(_blk[2])
                _box_l = (_loc[0] - _bw // 2, _loc[1] - _bh // 2,
                          _loc[0] + _bw // 2, _loc[1] + _bh // 2)
            else:
                _box_l = (_loc[0] - 12, _loc[1] - 12, _loc[0] + 12, _loc[1] + 12)
            self._save_forensic("锚定", self._cast_no, box_local=_box_l,
                                features=self._anchor_features(_loc, bobber_position))
        except Exception as e:
            self.log("锚定取证失败 %s", e)
        self._verify_and_watch(bobber_position)

    def _verify_and_watch(self, bobber_position):
        """锚点已定 → 可信度检查 → 稳定期 → 咬钩监控循环。

        v41：从 _wait_for_bite 尾部抽出来，让「复用跟踪期锚点」和
        「重新找锚点」两条路径共用同一段逻辑，避免维护两份。
        """
        # 非原版：锚点可信度检查。若 2 秒跟踪期命中帧占比过低（<45%），
        # ★ v41：走到这里说明本竿已经拿到锚点位置，标记下来供停机文案判断。
        self._anchor_locked_this_cast = True
        # ★ v59：注册「abort 前衰减裁决」回调。
        #   浮标消失到撞上 no_bobber_limit 只有 3 帧（≈150ms），
        #   _wait_for_bite 主循环里的 area_decayed() 分支永远等不到那一帧；
        #   必须由 find() 在 abort 之前回调进来，这里就地完成拾取。
        #   只会被调一次（finder 侧 _decay_claimed 每竿清零）。
        def _on_decay_bite(_pos=(bobber_position[0], bobber_position[1])):
            self._bite_from_decay(_pos)
        self.bobber_finder.decay_claim_cb = _on_decay_bite
        # 说明真鱼鳔大部分帧检测不到，锚点很可能是旁边别的米黄物体，
        # 整个 25 秒都会拿它当基准 → 满屏误判。直接放弃本竿重抛。
        track = getattr(self, "_last_track", None)
        if track:
            frames, hits = track
            ratio = hits / float(frames or 1)
            thresh = getattr(self, "anchor_ratio_min", 0.35)
            self.log("锚点可信度: %d/%d 帧 = %.0f%%（分母=浮标出现后的帧数）",
                     hits, frames, ratio * 100)
            # 诊断(v17)：输出命中率分布，用于定门槛。不改判定行为。
            self.log("DIAG可信度 n=%d hits=%d ratio=%.3f 门槛=%.2f", frames, hits, ratio, thresh)
            try:
                self._diag["conf"].append(ratio)
            except Exception:
                pass
            # v18：门槛 0.45 → 0.35。v17 实测 76 个样本呈双峰分布：
            #   0.36~0.45 有 34 次(45%)、0.70~1.00 有 42 次(55%)、中间无样本。
            # 双峰说明"要么浮标清楚、要么基本看不见"，不存在勉强能看的过渡带；
            # 0.45 正好切在低峰中间，把 45% 的竿全拒掉造成无效重抛。
            # 降到 0.35（实测最低 0.359）等于不再误拒，同时保留极端保护。
            # ★ v52：不再「放弃重抛」。走到这里锚点已经过三帧一致性确认
            #   + 高对比亮团校验，是现场验过的真浮标；可信度只是个统计指标，
            #   拿来否定已确认的锚点是本末倒置。真机日志 09:56 第 2 竿就是
            #   2/40=5% 直接重抛，白扔一竿（而且那 40 帧分母本身是错的）。
            #   现在只警告，继续走稳定期 + 咬钩判定。
            if ratio < thresh:
                self.log("⚠️ 锚点可信度偏低（%.0f%% < %.0f%%），但锚点已三帧确认，继续使用",
                         ratio * 100, thresh * 100)

        robust = getattr(self.bite_watcher, "mode", "legacy") == "robust"

        # 非原版：抛竿后稳定期。落水/水花/UI 弹字会让浮标一出现就带 y 抖动，
        # 这段时间内的 yDiff 全部是噪声（实测多次 0.3~1.7 秒就误报咬钩），
        # 稳定期内只更新水面基准、不判定咬钩。
        settle = getattr(self, "settle_time", 3.0)
        t_anchor = time.monotonic()
        settled = False
        if robust and settle > 0:
            self.log("稳定期 %.1fs 内不判咬钩（只更新水面基准）", settle)

        timed_task = TimedAction(
            lambda a: self.log("Fishing timed out!"), 25 * 1000, 25)

        while self.is_enabled:
            # 非原版改进：_find_bobber 内部最多重试 5 次×1s，浮标消失那一帧
            # 它能卡 2~5 秒，vanish 计数被吃掉 → 只能等 25s 超时。这里单帧取图。
            _t0 = time.monotonic()
            current = self.bobber_finder.find()
            _dt = time.monotonic() - _t0
            # ★ v55：把本帧检测结果记到实例上，供 _dump_region 写进 timeline.jsonl。
            #   之前没有任何地方保存这两个值，timeline 里 detect/y 会全是 null，
            #   等于白记 —— 而「这一帧检测到哪儿、y 是多少」正是复盘的核心。
            self._last_detect_xy = ([int(current[0]), int(current[1])]
                                    if current is not None else None)
            if current is None and _dt > 0.4:
                self.log("单帧扫描耗时 %.2fs（浮标可能已消失）", _dt)

            # ★ v57 核心修复（B）：跳变作废门限（横向 + 纵向）。
            #   真机证据（debug.zip c002__咬钩前_t12.1s）：
            #     本竿锚点 (655,334)，检测点突然变成 (977,259) —— Δx=+322, Δy=-75，
            #     于是 yDiff=-76 被当成「浮标下沉」直接判咬钩。
            #     但那一帧的 (977,259) 是**船体/水面亮线**，不是浮标：
            #     真浮标 20 帧里 17 帧稳定在 (655,334)±1px，从未移动过。
            #   判定条件（任一成立即本帧作废）：
            #     ① 横向跳 > max(3×浮标宽, 40px)  —— 浮标拴在鱼线上，横向几乎不动
            #     ② 纵向跳 > max(6×strike_value, 40px) —— 真实下沉是逐帧 2~8px，
            #        一帧跳 70px 只可能是换了检测目标（实测 strike_value 标定 4~6，
            #        上界 6×6=36 取 40 兜底，仍远小于船体造成的 72px 误差）
            #   作废帧不喂 watcher：既不污染 y 基准，也不参与咬钩判定。
            if current is not None and bobber_position is not None:
                _adx = abs(int(current[0]) - int(bobber_position[0]))
                _ady = abs(int(current[1]) - int(bobber_position[1]))
                _bw = getattr(self.bobber_finder, "_last_block", None)
                _bwid = (_bw[1] if _bw else 20) or 20
                _xgate = max(3.0 * _bwid, 40.0)
                _strike = getattr(self.bite_watcher, "strike_value", 6) or 6
                _ygate = max(6.0 * float(_strike), 40.0)
                _jx = _adx > _xgate and _adx > 3.0 * _ady
                _jy = _ady > _ygate
                if _jx or _jy:
                    self.log("检测跳变: (%d,%d) → (%d,%d) Δx=%d(限%d) Δy=%d(限%d)，"
                             "判为换目标/检测丢失，本帧不参与咬钩判定",
                             bobber_position[0], bobber_position[1],
                             current[0], current[1],
                             _adx, int(_xgate), _ady, int(_ygate))
                    current = None

            # ★ v57：y 基准只吃「通过门限」的检测点。
            #   原来这行在门限之前，船开过时假点 (332,72) 会把 y 基准拽走
            #   70+ px，之后真浮标回来 yDiff 立刻变成 +70 → 反向误判。
            if current is not None:
                self.bite_watcher._last_y = int(current[1])

            # ★ v55：判咬钩循环按秒存图（用户要求「debug 图每秒存一次就行」）。
            #   之前这一段一帧图都不存，离线复盘时只能看到「yDiff=6 命中」这种
            #   数字，没法核对那一帧浮标到底沉了多少像素 —— 而这正是最该验的部分。
            #   节流逻辑在 _dump_region 里：普通帧 1 秒 1 张；一旦检测到下沉趋势
            #   （下沉 ≥ 3px，距阈值 6px 只有一半）就切密集模式 200ms 1 张，
            #   咬钩判定那帧由 critical_tags 强制存。
            try:
                if getattr(self, "debug_frames", False) or getattr(
                        self, "save_regions", False):
                    _yd = getattr(self.bite_watcher, "y_diff", 0) or 0
                    _sink = -_yd
                    self._dense_dump = _sink >= 3
                    _lb = getattr(self.bobber_finder, "_bmp", None)
                    self._dump_region(
                        "咬钩前" if not self._dense_dump else "咬钩前密集",
                        bmp=_lb,
                        extra="y%d_沉%d_检%s" % (
                            current[1] if current else -1, _sink,
                            "%d_%d" % current if current else "无"))
            except Exception as _de:
                self.log("咬钩期存帧异常 %s", _de)

            # robust 模式：浮标消失本身是咬钩信号，必须传给 watcher
            if robust:
                elapsed = time.monotonic() - t_anchor
                if not settled:
                    if elapsed < settle:
                        # 稳定期：只喂 y 更新水面基准，吞掉一切判定
                        if current is not None:
                            self.bite_watcher.observe(current[1])
                        if not timed_task.execute_if_due():
                            return
                        continue
                    settled = True
                    self.log("稳定期结束（%.1fs），开始判咬钩", elapsed)

                if self.bite_watcher.is_bite(current):
                    self.log("咬钩判定: 下沉 yDiff=%s（%s）",
                             self.bite_watcher.y_diff,
                             "浮标消失" if current is None else "位置下沉")
                    # 非原版调试：判定的同时存「咬钩前」帧（浮标此刻原样）
                    self._dump_dbg("pre", bobber_position, current)
                    self._dump_region("咬钩", extra="锚点%d_%d_当前%s" % (
                        bobber_position[0], bobber_position[1],
                        "%d_%d" % (current[0], current[1])
                        if current is not None else "消失"), force=True)
                    # ★ v55：咬钩节点取证 —— 咬钩区域图(红框) + 全屏 + 咬钩特征值
                    self._forensic_bite(current, bobber_position, "y位移下沉")
                    self._loot(bobber_position)     # ← 传抛竿位置，不是 current
                    self._press_ten_min_key_if_due()
                    return
                # ★ v49：浮标丢了，但本次是「面积渐进缩小」丢的 → 这就是咬钩。
                #   v48 以前只看 y 位移，浮标面积从 457 缩到 313 再消失这种
                #   曲线被当成「目标消失 → 重抛」，白白放跑一条鱼。
                #   注意只在本竿锚点已锁定、且当前帧确实没找到时判，
                #   避免和上面 y 位移判定重复。
                if current is None and getattr(self, "_anchor_locked_this_cast", False):
                    try:
                        decayed = self.bobber_finder.area_decayed()
                    except Exception as e:
                        self.log("面积衰减判定异常 %s", e)
                        decayed = False
                    if decayed:
                        self._bite_from_decay(bobber_position)
                        return
                if current is not None and current[0] == 0:
                    return
            else:
                # 原版逻辑：找不到浮标直接放弃本竿
                if current is None or current[0] == 0:
                    return

                if self.bite_watcher.is_bite(current):
                    # ★ v55：legacy 分支也要取证（否则切 --bite-mode legacy 时
                    #   关键节点数据全丢，离线无从复盘）
                    self.log("咬钩判定(legacy): 下沉 yDiff=%s（%s）",
                             getattr(self.bite_watcher, "y_diff", None),
                             "浮标消失" if current is None else "位置下沉")
                    self._dump_region("咬钩", extra="锚点%d_%d_当前%s" % (
                        bobber_position[0], bobber_position[1],
                        "%d_%d" % (current[0], current[1])
                        if current is not None else "消失"), force=True)
                    self._forensic_bite(current, bobber_position, "y位移下沉(legacy)")
                    self._loot(bobber_position)     # ← 传抛竿位置，不是 current
                    self._press_ten_min_key_if_due()
                    return

            if not timed_task.execute_if_due():
                return

            # 心跳：每 ~3 秒报一次当前状态（解决"静默 16 秒看不出在干什么"）
            # ★ v45：frame_log 开启时，逐帧明细已经在 bite_watcher 里输出，
            #   心跳降级为「每 5 秒一行汇总」，避免日志被刷成两倍。
            now = time.monotonic()
            _beat_gap = 5.0 if getattr(self, "frame_log", False) else 3.0
            if now - getattr(self, "_last_beat", 0) >= _beat_gap:
                self._last_beat = now
                self.log("监控中: 浮标=%s yDiff=%s 连续=%d 消失计数=%d 锚点y=%s 阈值=%d",
                         current, getattr(self.bite_watcher, "y_diff", "?"),
                         getattr(self.bite_watcher, "_streak", 0),
                         getattr(self.bite_watcher, "_vanish", 0),
                         getattr(self.bite_watcher, "per_cast_anchor_y", None),
                         int(getattr(self.bite_watcher, "strike_value", 0)))

    # ---- 逐行对应 FishBot.cs:164-168 ----
    def _bite_from_decay(self, bobber_position):
        """★ v59：面积渐进衰减式咬钩的统一处理（主循环分支 + abort 前回调共用）。

        真机依据见 searchbobberfinder.area_decayed() 与 _claim_decay()。
        两条触发路径：
          ① _wait_for_bite 主循环 current is None 时（旧路径，很少能轮到）
          ② find() 即将 abort 前的 decay_claim_cb（新路径，实测走这条）

        ★ v60：17 秒门控在这里统一实施，两条路径都绕不过去。
          否则主循环那条路径会在 17 秒到期收杆时照样右键空气。
        """
        # ── 17 秒存活期门控（见 FISHING_MECHANISMS.md 第 1 节）──
        _el = None
        try:
            _f = getattr(self.bobber_finder, "seconds_since_cast", None)
            if callable(_f):
                _el = float(_f())
        except Exception:
            _el = None
        _dl = float(getattr(self.bobber_finder, "bite_deadline_s", 16.5))
        if _el is not None and _el >= _dl:
            self.log("面积衰减发生在抛竿后 %.1fs（≥%.1fs 浮标存活期）→ "
                     "判为 17 秒到期自动收杆，不拾取", _el, _dl)
            return
        if getattr(self, "_bite_handled_this_cast", False):
            return
        self.log("咬钩判定: 浮标面积渐进缩小至消失（抛竿后 %s）→ 判为咬钩",
                 "%.1fs" % _el if _el is not None else "未知")
        try:
            self._dump_region("咬钩衰减", extra="锚点%d_%d" % (
                bobber_position[0], bobber_position[1]))
        except Exception as e:
            self.log("衰减咬钩存图失败: %s", e)
        try:
            self._forensic_bite(None, bobber_position, "面积渐进衰减")
        except Exception as e:
            self.log("衰减咬钩取证失败: %s", e)
        self._loot(bobber_position)
        self._press_ten_min_key_if_due()
        # 本竿到此结束：finder 侧已判过衰减，abort_cb 不能再把整轮打成
        # 「目标消失 → 重抛」；这里只标记本竿收尾，主循环正常进下一竿。
        self._bite_handled_this_cast = True

    def _loot(self, bobber_position):
        # 收竿前先拍一张「基准」：如果这行字本来就还挂在屏幕上（上一竿残留），
        # 之后就不能把它当成这一竿的结果。
        pre_hit = False
        nd = self.no_bite_detector
        if nd is not None:
            try:
                _pr = nd.check()
                pre_hit = _pr.get("hit", False)
                # v19：收竿前若已有黄字（上一竿残留），先等它消失再继续，
                # 旧版直接按「真咬钩」放行 → 误判被静默吞掉。
                if pre_hit:
                    self.log("DIAG基准 收竿前已有黄字 px=%d rows=%d span=%d，等残留消失",
                             _pr.get("px", 0), _pr.get("rows", 0), _pr.get("span", 0))
                    try:
                        self._diag["prehit"] = self._diag.get("prehit", 0) + 1
                    except Exception:
                        pass
                    cleared = nd.wait_clear(timeout=6.0, poll=0.15)
                    if cleared:
                        self.log("DIAG基准 残留已消失，本竿重新建立基准")
                        pre_hit = False
                    else:
                        self.log("DIAG基准 ⚠️残留 6.0s 未消失，本竿判定不可靠")
            except Exception:
                pre_hit = False

        self.log("Right clicking mouse to Loot @ %s", bobber_position)
        self.wow_process.right_click_mouse(bobber_position)
        # 非原版调试：拾取动作做完立刻存「拾取后」帧（含聊天框物品提示）
        self._dump_dbg("post", bobber_position, None)
        # 非原版：判收竿结果 —— 屏幕上方弹出「没有鱼上钩」= 时机不对，其实没鱼
        nb_hit, nb_res = self._check_no_bite()
        # 诊断(v17)：记录本竿最终归属，重点看 pre_hit=True 的竿（基准污染）
        _own = "误判" if (nb_hit and not pre_hit) else ("被基准吞" if (nb_hit and pre_hit) else "真咬钩")
        self.log("DIAG归属 pre_hit=%s nb_hit=%s -> %s", pre_hit, nb_hit, _own)
        try:
            _k = {"误判": "miss", "被基准吞": "eaten", "真咬钩": "hit"}[_own]
            self._diag["own"][_k] = self._diag["own"].get(_k, 0) + 1
        except Exception:
            pass
        if nb_hit and not pre_hit:
            self.miss_count += 1
            self.log("收竿结果: 没有鱼上钩（误判，实际没咬钩） px=%d rows=%d",
                     nb_res.get("px", 0), nb_res.get("rows", 0))
            # 这行字约 4 秒后消失；等它清掉再重抛，避免下一竿读到残留
            if nd is not None:
                try:
                    cleared = nd.wait_clear(timeout=4.5, poll=0.2)
                    if not cleared:
                        self.log("⚠️「没有鱼上钩」4.5 秒未消失，下一竿判定可能不准")
                except Exception:
                    pass
            return
        if pre_hit:
            self.log("收竿结果: 屏幕已有「没有鱼上钩」残留，本轮无法判定，按真咬钩处理")
        else:
            self.log("收竿结果: 未出现「没有鱼上钩」→ 咬钩时机正确")
        self.hit_count += 1
        self._read_loot()

    # ---- 非原版增强：确认是否「没有鱼上钩」 ----
    def _check_no_bite(self):
        """右键收竿后 ~1.8 秒内，看屏幕上方是否弹出黄字「没有鱼上钩」。

        用户实测（2026-11-01）：钓鱼时机正确时每次都能钓到；
        时机不对（收竿过早）游戏会在固定位置弹「没有鱼上钩」。
        所以这行字是判断「咬钩判定对不对」最硬的依据。
        """
        nd = self.no_bite_detector
        if nd is None:
            return False, {}
        t0 = time.monotonic()
        try:
            hit, res = nd.confirm_no_bite(timeout=5.0, poll=0.10)
        except Exception as e:
            self.log("检测「没有鱼上钩」异常: %s", e)
            return False, {}
        # 诊断(v17)：无论命中与否都记录峰值，用于判断是「没扫到」还是「扫到不够阈值」。
        # pre=收竿前基准命中（若为 True 说明本竿判定被前置基准污染）。
        try:
            self.log("DIAG黄字 hit=%s px=%d rows=%d span=%d 耗时=%.2fs",
                     hit, res.get("px", 0), res.get("rows", 0),
                     res.get("span", 0), time.monotonic() - t0)
            self._diag["nobite"].append((hit, res.get("px", 0), res.get("rows", 0),
                                         res.get("span", 0)))
        except Exception:
            pass
        return hit, res

    # ---- 非原版增强：拾取后读聊天框物品提示 ----
    def _read_loot(self):
        """拾取动作做完后读一行「你获得了物品：[xxx]」写进日志。

        判定口径：拾取前的指纹 → 拾取后 3 秒内出现不同的物品提示行，
        才算这一竿真的捡到东西。读不到就记「(未见物品提示)」，
        这样误判咬钩（其实没鱼）会立刻在日志里暴露。
        """
        lr = self.loot_reader
        if lr is None:
            return
        try:
            hit, name, fp = lr.wait_new_loot(self._loot_fp, timeout=3.0)
            self._loot_fp = fp or self._loot_fp
        except Exception as e:
            self.log("读物品提示异常: %s", e)
            return
        if hit:
            label = name or "(未识别)"
            self.loot_stats[label] = self.loot_stats.get(label, 0) + 1
            self.log("拾取成功: 你获得了物品 [%s]  累计=%d  %s",
                     label, self.loot_stats[label],
                     " ".join("%s×%d" % (k, v) for k, v in
                              sorted(self.loot_stats.items(),
                                     key=lambda kv: -kv[1])[:6]))
        else:
            self.log("拾取结果: (未见物品提示) —— 疑似误判/空竿")

    # ---- 诊断增强（非原版）：把关键帧原图 + tan 候选全落盘 ----
    def _write_bmp(self, bmp, path):
        h, w = bmp.shape[:2]
        row = w * 3
        pad = (4 - row % 4) % 4
        with open(path, "wb") as f:
            f.write(b"BM")
            size = 54 + (row + pad) * h
            f.write(size.to_bytes(4, "little") + (0).to_bytes(4, "little")
                    + (54).to_bytes(4, "little"))
            f.write((40).to_bytes(4, "little") + w.to_bytes(4, "little")
                    + h.to_bytes(4, "little") + (1).to_bytes(2, "little")
                    + (24).to_bytes(2, "little"))
            f.write((0).to_bytes(4, "little") + (row + pad).to_bytes(4, "little")
                    + (2835).to_bytes(4, "little") + (2835).to_bytes(4, "little")
                    + (0).to_bytes(4, "little") + (0).to_bytes(4, "little"))
            for y in range(h - 1, -1, -1):
                f.write(bmp[y, :, ::-1].tobytes())
                if pad:
                    f.write(b"\x00" * pad)

    # ---- 非原版调试：每竿 pre/post 两帧落盘 ----
    def _dump_dbg(self, tag, anchor=None, current=None):
        """把当前截屏原样存 debug/，文件名带竿号和关键坐标。

        pre  = 咬钩判定那一刻（浮标还在，用于判断位置对不对）
        post = 拾取动作结束后（含聊天框，用于判断有没有捡到东西）
        """
        if not getattr(self, "debug_frames", False):
            return
        grabber = getattr(self.bobber_finder, "grabber", None)
        if grabber is None:
            return
        import os
        try:
            bmp = grabber.grab()
            h, w = bmp.shape[:2]
            left = top = 0
            sa = getattr(grabber, "search_area", None)
            if callable(sa):
                sa_ = sa()
                left, top = sa_[0], sa_[1]
            d = os.path.join(os.getcwd(), self._debug_dir)
            os.makedirs(d, exist_ok=True)
            ay = ax = cy = cx = -1
            if anchor is not None:
                ax, ay = anchor[0], anchor[1]
            if current is not None:
                cx, cy = current[0], current[1]
            name = "c%03d_%s_%dx%d_%d_%d_a%d_%d_c%d_%d.bmp" % (
                self._cast_no, tag, w, h, left, top, ax, ay, cx, cy)
            self._write_bmp(bmp, os.path.join(d, name))
            self.log("调试帧[%s] debug/%s 锚点=(%d,%d) 当前=(%d,%d) yDiff=%s",
                     tag, name, ax, ay, cx, cy,
                     getattr(self.bite_watcher, "y_diff", "?"))
        except Exception as e:
            self.log("调试帧[%s] 落盘失败 %s", tag, e)

    def _dump_frame(self, tag, chosen):
        grabber = getattr(self.bobber_finder, "grabber", None)
        if grabber is None:
            return
        import os
        import numpy as np
        d = os.path.join(os.getcwd(), "diag")
        os.makedirs(d, exist_ok=True)

        bmp = grabber.grab()
        h, w = bmp.shape[:2]
        left, top = 0, 0
        sa = getattr(grabber, "search_area", None)
        if callable(sa):
            sa_ = sa()
            left, top = sa_[0], sa_[1]
        stamp = time.strftime("%H%M%S")
        name = "%s_%s_%dx%d_%d_%d.bmp" % (stamp, tag, w, h, left, top)
        path = os.path.join(d, name)
        try:
            self._write_bmp(bmp, path)
        except Exception as e:
            self.log("诊断: 写 BMP 失败 %s", e)
            name = "(写失败)"

        cand = []
        try:
            clf = getattr(self.bobber_finder, "pixel_classifier", None)
            mode = getattr(clf, "mode", None)
            if str(mode).strip().capitalize() == "Tan":
                rgb = bmp.astype(np.int16)
                r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
                m = ((r > 140) & (g > 105) & (b > 60) & (b < 175)
                     & ((r - b) > 45) & ((g - b) > 15) & (np.abs(r - g) < 80))
                ys, xs = np.nonzero(m)
                pts = np.stack([xs, ys], 1) if len(xs) else np.zeros((0, 2), np.int64)
                seen = np.zeros(len(pts), bool)
                for i in range(len(pts)):
                    if seen[i]:
                        continue
                    sel = (np.abs(pts[:, 0] - pts[i, 0]) <= 40) & \
                          (np.abs(pts[:, 1] - pts[i, 1]) <= 40)
                    seen |= sel
                    c = pts[sel]
                    bw = int(c[:, 0].max() - c[:, 0].min() + 1)
                    bh = int(c[:, 1].max() - c[:, 1].min() + 1)
                    cx = int(c[:, 0].mean()) + left
                    cy = int(c[:, 1].mean()) + top
                    cand.append((len(c), bw, bh, cx, cy))
                cand.sort(reverse=True)
        except Exception as e:
            self.log("诊断: 候选统计失败 %s", e)

        self.log("诊断帧[%s] 文件=diag/%s 选择=%s 候选数=%d", tag, name, chosen, len(cand))
        for ci, (n, bw, bh, cx, cy) in enumerate(cand[:12]):
            ratio = (bw / float(bh)) if bh else 0.0
            self.log("  诊断候选#%d 像素=%d 尺寸=%dx%d 比例=%.2f 中心=(%d,%d)",
                     ci, n, bw, bh, ratio, cx, cy)

    # ---- 逐行对应 FishBot.cs:129-162 ----
    def _press_ten_min_key_if_due(self):
        if (time.monotonic() - self.start_time) > 600 and self.ten_min_key:
            self._do_ten_minute_key()

    def _do_ten_minute_key(self):
        self.start_time = time.monotonic()
        if not self.ten_min_key:
            self.log("Ten Minute Key: No keys defined in tenMinKey")
        for key in self.ten_min_key:
            self.log("Ten Minute Key: Pressing key %s", key)
            self.wow_process.press_key(key)

    # ---- 逐行对应 FishBot.cs:200-209 ----
    def _find_bobber(self):
        timer = TimedAction(
            lambda a: self.log("Waited seconds for target: %d", a.elapsed_secs),
            1000, 5)
        while True:
            target = self.bobber_finder.find()
            if target is not None or not timer.execute_if_due():
                return target
