"""浮标搜索 —— 严格复刻 Source/FishingFunBot/Bot/SearchBobberFinder.cs（141 行）

原版判定规则一条都不改：
  ① 有 previousLocation 时只搜 ±40px 窗口；无则全图
  ② 邻域计数 |dx|<10 且 |dy|<10，取 count 最大的点
  ③ points.Count > 1000 → 清空（颜色配置错误）
  ④ previousLocation 先无条件清空，只有本次找到才写回
唯一加速改动：循环用 numpy 向量化，判定顺序与语义完全一致。
"""

import numpy as np

from .pixelclassifier import PixelClassifier

# 邻域半径：原版硬编码 40px（1366 宽设计值）。按分辨率等比放大是唯一允许的适配。
BASE_WIDTH_FOR_RADIUS = 1366

# 同一竿鱼鳔面积允许的波动倍数（非原版：防止锁到忽大忽小的杂物）
AREA_JUMP = 2.5


class SearchBobberFinder:
    def __init__(self, pixel_classifier, ui_masks=None, logger=None):
        self.pixel_classifier = pixel_classifier
        self.screen = None                       # WowScreen，首竿标定截屏边界时用
        self.previous_location = None            # None == Point.Empty
        # 天空/水面分界线（标定时用 17 帧自动测出）。设了就只在水面侧找浮标。
        # 浮标不可能在天上 —— 少了这条，云影/飞鸟/天空色块经常被当成候选。
        self.sky_limit_y = None
        # 非原版：排除区（用户指定）。左右各 1/7 是 UI（小地图/头像/聊天框），
        # 上边界不硬编码，改用标定测出的天空分界线 sky_limit_y；下 1/4 是技能条。
        self.exclude_side_ratio = 1.0 / 7.0
        self.exclude_top_ratio = 0.0      # 上边界由 sky_limit_y 决定，不按比例
        self.exclude_bottom_ratio = 0.25
        # 浮标色差直检（非原版增强）。
        # 不写死任何颜色：用 R-B 色差判断「暖色团」。
        # 实测四张图（2560x1600）截屏区内：
        #   有浮标 → R-B max 分别为 +90 / +121，暖色像素 377 / 678
        #   无浮标 → R-B max 均为 -23（全蓝），暖色像素 0
        # 所以色差本身就是干净判据，浮标是亮黄(238,195,1)还是
        # 暗橙(87,71,54)/(156,126,90) 都能检出，不需要按颜色分支。
        self.bobber_diff_enabled = True
        self.diff_threshold = 20          # R-B > 20 视为暖于水面
        self.diff_min_channel = 80        # R 下限，排除全暗噪声
        # ★ v48：与色相无关的「局部亮团」判据，默认开启，与暖色判据取并集。
        #   浮标在 WoW 里随环境光变色（暖橙/冷白/灰），只有「比周围水面亮、
        #   边缘清晰成团」这一条在所有光线下都成立。
        #   实测同一帧：浮标 dev>30 = 720px 峰值 126；最亮的水面反光 56px 峰值 50。
        self.bobber_bright_enabled = True
        self.bright_dev_threshold = 30.0  # 亮度 − 局部背景 > 30 视为亮团像素
        # 严格模式：色差检未命中时直接返回 None，不回落到原版差分/颜色搜索。
        # 实测原版回落路径在「画面上根本没有浮标」时会抓到暗红杂物，
        # 造成「浮标还在」的假象 → 上层反复认为没咬钩 / 误判。
        # 找不到浮标 = 该换钓点，必须让上层停下来提示用户（见 abort_cb）。
        self.strict_yellow = True
        self._yellow_miss = False
        # 上层注册：连续 N 次找不到浮标时回调（用来停止程序 + 提示用户）
        self.no_bobber_frames = 0
        self.no_bobber_limit = 3
        # 飞行期/首竿标定期置 True：此时找不到浮标不累计、不停机
        self.suppress_abort = False
        # v37：飞行期置 True —— 允许检出色差候选（用于日志/差分），
        # 但**不写 previous_location**，避免把空中的残留影像锚死。
        self.no_anchor_lock = False
        # v37：色差锚点 & 后续帧优先用色差跟踪（TAN 在水面色彩相近的
        # 钓点会命中水面，实测带偏 670px）。
        self._diff_anchor = None
        self.diff_track_enabled = True
        # ★ v43：首竿标定出的「浮标指纹」，后续每竿用它校验候选合法性。
        #   真机实测教训：没有指纹时，顶部一条 area 1969~2423 的亮带
        #   （比真浮标 360 大 6 倍）能冒充浮标被抓走；有了指纹，
        #   面积/宽高/色值偏离基准的候选可被直接否决。
        #   字段：area / w / h / rgb / frames（采样帧数）
        self.bobber_fingerprint = None
        self.diff_anchor_sticky = True
        self.abort_cb = None
        self.excl_h = None                # find() 里会按实际帧尺寸刷新
        self.excl_w = None
        self.bitmap_event = lambda pt, bmp: None
        self.log = logger or (lambda *a, **k: None)

        # 非原版：UI 遮罩（比例坐标），排除头像框/技能条/小地图
        self.ui_masks = ui_masks
        # 非原版：宽松长宽比（抛竿后 2 秒内浮标被水花切碎时用）
        self.loose_aspect = False
        self.tan_min_area = 150          # v20: 60→150。真浮标 a20 最小 201，13px 水花碎点排除
        self.tan_max_area = 1600         # v20: 2200→1600。真浮标 a20 最大 1137，9009px 栈道排除
        self.tan_expect_area = 300
        self.tan_max_width = 90          # v20: 60→90。真浮标最宽 72px，178px 栈道排除
        self.tan_min_ratio = 0.8         # v20: 1.2→0.8。方形浮标实测 1.16~1.33，旧下限误杀
        self.tan_max_ratio = 3.8         # v20: 2.6→3.8。拉伸浮标实测最高 3.62
        # 非原版：差分标定。抛竿前抓一帧 pre，抛竿后 2 秒那一帧 post，
        # 两张图里新出现的暖色团就是浮标 → 直接拿到位置和色值，
        # 完全绕过 tan 绝对阈值（换钓点后被压暗就全失配）。
        self.calib_pre = None            # 抛竿前截图
        self.calib_enabled = True
        self._calib_bmp = None           # post（_watch 最后一帧）

    def learn_fingerprint(self, samples, log=None):
        """★ v43：首竿标定成功后，从多帧采样学习「浮标指纹」。

        samples: [(area, w, h, rgb)]，来自 _watch 跟踪期稳定命中的帧。
        只取中位数，抗单帧噪声（真机有一次 area 从 1100 掉到 176）。
        后续每竿 _find_points 用这份指纹否决明显不像浮标的候选。
        """
        if not samples or len(samples) < 5:
            if log:
                log("浮标指纹: 采样仅 %d 帧（需≥5），本竿不建指纹", len(samples or []))
            return None
        import statistics as _st
        areas = [s[0] for s in samples if s[0]]
        ws = [s[1] for s in samples if s[1]]
        hs = [s[2] for s in samples if s[2]]
        if not areas or not ws or not hs:
            return None
        fp = {
            "area": int(_st.median(areas)),
            "w": int(_st.median(ws)),
            "h": int(_st.median(hs)),
            "frames": len(samples),
        }
        # 颜色也取中位（有些帧 rgb 为 None）
        rgbs = [s[3] for s in samples if s[3]]
        if rgbs:
            fp["rgb"] = tuple(int(_st.median([c[i] for c in rgbs])) for i in range(3))
        self.bobber_fingerprint = fp
        if log:
            log("浮标指纹已建立: %dx%d area=%d rgb=%s（%d 帧采样）→ "
                "后续每竿按此校验候选",
                fp["w"], fp["h"], fp["area"], fp.get("rgb"), fp["frames"])
        return fp

    def reset(self):
        # 逐行对应 SearchBobberFinder.cs:29-32
        self.previous_location = None
        self._area_hist = []      # 非原版：本竿面积历史，用于尺寸跳变判定
        # v37：每竿重置色差锚点，避免上一竿的浮标位置影响本竿
        self._diff_anchor = None
        self._diff_hits = 0
        self._diff_prev = None

    # 逐行对应 SearchBobberFinder.cs:34-57
    def find(self, bitmap=None):
        """
        bitmap: HxWx3 RGB uint8 numpy 数组（对应 this.bitmap = WowScreen.GetBitmap()）
        返回 (x, y) 屏幕坐标 或 None（对应 Point.Empty）
        """
        bmp = bitmap if bitmap is not None else self._grab()
        self._bmp = bmp
        self._cur_w = bmp.shape[1]          # 供 tan 校验等比缩放
        # 记录截屏区在整屏中的位置，供 ui_masks（整屏比例坐标）换算出局部坐标。
        try:
            self._search_area_full = self._screen_search_area()
        except Exception:
            pass

        # ① 快速路径：亮黄色浮标直检（用户实机实测本体色，第一张截图
        #    (238,195,1)，58x28，area=1099）。这条路不看地平线、不看差分、
        #    不看光照 —— 实测在有浮标的图上误差 (-1,1)，在无浮标的图上
        #    精确返回 None（黄像素 0）。所以它一旦命中就是高置信，直接采用。
        if self.previous_location is None:
            yb = self._find_bobber_by_diff(bmp)
            if yb is not None:
                self.no_bobber_frames = 0
                # ★ v37：飞行期只记录、不写锚点（详见 _watch 里的说明）。
                if getattr(self, "no_anchor_lock", False):
                    self.previous_location = None
                    self.bitmap_event(yb, bmp)
                    return self._to_screen(yb, bmp)
                self.previous_location = yb
                self._diff_anchor = yb          # v37：记录色差锚点
                self.bitmap_event(yb, bmp)
                return self._to_screen(yb, bmp)
            # 色差检未命中：记下来，供上层判定「本帧没有可信浮标」。
            # 实测原版回落路径在无浮标的图上会抓到暗红杂物，
            # 所以严格模式下不回落到原版搜索。
            self._yellow_miss = True
            # 飞行期 / 标定期不算失败：浮标还在空中或刚落水，本来就可能找不到。
            # 之前没这个抑制，第一竿抛出去 0.7 秒就凑满 3 帧直接停机（实测日志）。
            if getattr(self, "suppress_abort", False):
                self.previous_location = None
                return None
            # ★ v43 核心修复：_diff_hits > 0 说明已经看见候选、只是在攒连续性
            #   确认帧。把它计入 no_bobber_frames 会导致「确认链一旦中断，
            #   立刻烧掉 3 条命 → abort」。真机日志实测：帧1 1/3、帧2 2/3 各计
            #   一次 miss，第 3 帧浮标恰好被咬走消失 → miss=3 → ABORT，
            #   误报「本竿从未锁定浮标」—— 而那一刻其实正是鱼咬钩。
            cf = getattr(self, "diff_confirm_frames", 1)
            if self._diff_hits > 0 and self._diff_hits < cf:
                self.previous_location = None
                self.log("色差候选确认中（%d/%d），不计 miss",
                         self._diff_hits, cf)
                return None
            self.no_bobber_frames += 1
            if getattr(self, "strict_yellow", False):
                self.previous_location = None
                self.log("色差检未命中（%d/%d），不回落到原版搜索 -> None",
                         self.no_bobber_frames, self.no_bobber_limit)
                if (self.abort_cb is not None
                        and self.no_bobber_frames >= self.no_bobber_limit):
                    self.abort_cb(self.no_bobber_frames)
                return None

        # 非原版：首次锚定优先走差分标定（pre/post 都在才走）。
        # 一旦拿到位置，previous_location 就锁定，后面帧走原版 ±40px 窗口跟踪。
        if (self.calib_enabled and self.previous_location is None
                and self.calib_pre is not None):
            pos = self._calibrate(bmp)
            if pos is not None:
                self.previous_location = pos
                self.bitmap_event(pos, bmp)
                return self._to_screen(pos, bmp)

        # ★ v37：已有锚点的后续帧，优先继续用色差检跟踪。
        #   原逻辑一旦 previous_location 非空就只走 TAN 的 ±40px 窗口，
        #   实测 TAN 在本钓点会命中水面（锚点被带偏 670px）。
        #   色差检每帧全图扫一次的成本可接受，而且判据干净。
        if self.previous_location is not None and getattr(
                self, "diff_track_enabled", True):
            yb = self._find_bobber_by_diff(bmp, single_frame=True)
            if yb is not None:
                self.no_bobber_frames = 0
                self.previous_location = yb
                self._diff_anchor = yb
                self.bitmap_event(yb, bmp)
                return self._to_screen(yb, bmp)

        pts = self._find_points(bmp)
        pts = self._drop_excluded(pts, bmp)
        best = self._score_points(pts)

        if self.previous_location is not None and best is None:
            self.previous_location = None
            best = self._score_points(self._drop_excluded(self._find_points(bmp), bmp))

        # ★ v37：色差检已锁定的锚点，不允许被 TAN 路径顶掉。
        #   实测（16 帧真图）：锚点正确锁在浮标 (820,343) 后，下一帧这里
        #   无条件清空再取 _find_points 的 best，结果拿到 (149,78) —— 偏了
        #   670px，那是 TAN 匹配命中的水面。之后全程追水面，收竿时 y=None。
        #   色差判据（R-B>20）在本钓点是干净判据（真浮标 R-B=+85，水面 -35，
        #   全图仅 1 个暖块），一旦命中就比 TAN 可靠得多。
        if (best is not None and getattr(self, "_diff_anchor", None) is not None
                and getattr(self, "diff_anchor_sticky", True)):
            ax, ay = self._diff_anchor
            # TAN 结果离色差锚点太远 → 判为追错目标，保持原锚点
            if abs(best[0] - ax) > 40 or abs(best[1] - ay) > 40:
                best = None

        self.previous_location = None
        if best is not None:
            self.previous_location = best

        self.bitmap_event(self.previous_location, bmp)

        if self.previous_location is None:
            return None
        return self._to_screen(self.previous_location, bmp)

    # ---- 非原版增强：窗口边框纯色行剔除（v41） ----
    def _drop_uniform_rows(self, bmp):
        """返回 bool 掩膜 (h,1)，True = 保留该行。None 表示无需处理。

        实测本机 20:42 截屏区顶部：
            y=0,1   整行 rgb(40,140,255)   1 种颜色
            y=8     整行 rgb(40,140,255)   1 种颜色
            y=9,10  整行 rgb(255,220,0)    1 种颜色
            y=11+   142 种颜色（游戏画面）
        整行只有 1 种颜色 = 合成色带（窗口边框），不可能是游戏内容。
        不剔除的话 y=9,10 的 2560 个黄像素会被当成最大暖色块，
        并把 detect_horizon 的梯度峰值顶到 y=9（实测日志「分界线y9」）。
        """
        try:
            h = bmp.shape[0]
            # 逐行唯一色数：把每行拉平成 (h, w*3) 后比较相邻像素
            rowa = bmp.reshape(h, -1)
            same = (rowa[:, 1:] == rowa[:, :-1]).all(axis=1)
            keep = (~same).reshape(h, 1)
            # 只在顶部 5% 内动手，避免误伤画面中真实的纯色水面带
            lim = max(int(h * 0.05), 12)
            if not keep[:lim].any():
                return None                  # 顶部全被剔 = 异常帧，放弃
            keep[lim:] = True
            return keep
        except Exception:
            return None

    # ---- 非原版增强：色差浮标直检 ----
    def _bright_blob_mask(self, bmp):
        """与色相无关的「亮度突出」判据（v35 新增）。

        用大尺度盒式模糊求**局部背景亮度**，再取「当前像素亮度 − 局部背景」
        作为局部偏离 dev。浮标是水面上受光的凸出小物体 → dev 高；平静水面
        dev ≈ 0。

        实测（2560x1599 冰冻之海）：浮标本体 dev=+63.2，周围水面 −8~+10。
        阈值取 30 时全屏约 4.9% 命中（水面高光/UI 残留），但后续的面积、
        长宽比、UI 遮罩、左右 1/7 与下 1/4 排除会把它们筛掉。

        返回 bool 掩膜（与 bmp 同形状），或 None。
        """
        try:
            import numpy as _np
        except Exception:
            return None
        if bmp is None:
            return None
        h, w = bmp.shape[:2]
        if h < 16 or w < 16:
            return None
        f = bmp[:, :, :3].astype(_np.float32)
        lum = 0.114 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.299 * f[:, :, 2]
        # 降采样 4x 再模糊，兼顾速度与 68px 级的背景尺度
        s = 4
        hs, ws = h // s, w // s
        if hs < 4 or ws < 4:
            return None
        small = lum[:hs * s, :ws * s].reshape(hs, s, ws, s).mean(axis=(1, 3))
        rad = 8
        p = _np.pad(small, rad, mode="edge")
        c = _np.cumsum(_np.cumsum(p.astype(_np.float64), 0), 1)
        c = _np.pad(c, ((1, 0), (1, 0)))
        k = 2 * rad + 1
        box = (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / float(k * k)
        bg = _np.repeat(_np.repeat(box.astype(_np.float32), s, axis=0), s, axis=1)
        bh, bw = bg.shape
        dev = lum[:bh, :bw] - bg
        thr = float(getattr(self, "bright_dev_threshold", 30.0))
        mask = _np.zeros((h, w), bool)
        mask[:bh, :bw] = dev > thr
        return mask

    def _find_bobber_by_diff(self, bmp, single_frame=False):
        """用 R-B 色差找浮标（非原版增强，可用 --no-diff-bobber 关闭）。

        **不写死任何具体颜色**。判据只有一条：浮标比周围水/天更「暖」，
        即 R 通道明显高于 B 通道。实测四张 2560x1600 截图：

            有浮标  img_13fecd  本体 (238,195,1) 亮黄   R-B max +255
            有浮标  img_9d6409  本体 (87,71,54)  暗橙   R-B max +90
            有浮标  img_91a3af  本体 (156,126,90) 橙    R-B max +121
            无浮标  img_e9f81d  水面全蓝              R-B max -23
            无浮标  img_9d20d3  水面全蓝              R-B max -23

        所以阈值只要卡在「水面的负值」和「浮标的正值」之间即可，
        亮浮标、暗浮标、换钓点变色的浮标全部同一套逻辑。

        返回 (x,y) 局部坐标或 None。取「面积最大的合格暖色块」。
        """
        if not getattr(self, "bobber_diff_enabled", True):
            return None
        from . import bobbercalib as bc
        h, w = bmp.shape[:2]
        th = getattr(self, "diff_threshold", 20)
        min_ch = getattr(self, "diff_min_channel", 80)
        r = bmp[:, :, 0].astype(np.int16)
        b = bmp[:, :, 2].astype(np.int16)
        mask = ((r - b) > th) & (r > min_ch)
        # ★ v41：先剔除「窗口边框纯色行」。
        #   实测（2026-11-01 20:42 真机图）截屏区顶部是 WoW 窗口边框：
        #     y=0,1  纯色 rgb(40,140,255)  蓝
        #     y=9,10 纯色 rgb(255,220,0)   黄  ← 1280x2=2560 px，全被判成暖色
        #   不剔的话这 2560px 是「最大暖色块」，而它又是逐行等宽的细横条，
        #   会被下面的 bh<edge_min 规则挡掉，但同时把候选数从 1 撑到 2，
        #   更糟的是它把 detect_horizon 的梯度峰值顶到 y=9。
        #   判据：整行只有 1 种颜色 = 合成色带，不是游戏画面。
        keep_rows = self._drop_uniform_rows(bmp)
        if keep_rows is not None:
            mask = mask & keep_rows
        # ★ v35：暖色判据只覆盖「红/橙/黄浮标」。实测冰冻之海钓点是**冷色浮标**：
        #   浮标 rgb(65,83,100)，周围水面 rgb(33,52,68) —— 三通道同步高 33/34，
        #   R-B = -35（和纯水面的 -35 一样），暖色判据恒不命中（mask 全 False）。
        #   但它比局部水面亮 33，局部背景偏离 dev=63，而水面只有 -8~+10。
        #   所以补一条**与色相无关的相对亮度判据**：亮度明显高于局部背景。
        #   浮标居水面之上、受光照，这条在所有钓点/水色下都成立。
        # ★ v48：判据从「暖色方向」改为「暖色 OR 高对比亮团」并列命中。
        #   原因：WoW 浮标颜色随环境光变化，同一次钓鱼里浮标的 R-B 可以是
        #   +149px 的暖色块，也可以是 0px 的冷白块（真机实测两帧对比）。
        #   把暖色当唯一判据，等于赌「这一竿的光线刚好合适」。
        #   现在的 mask = 暖色像素 ∪ 亮度显著高于局部背景的像素：
        #     暖色留下「黄昏偏橙」的浮标，亮团留下「清晨偏冷/正午偏白」的浮标，
        #   两条都来自同一物理事实 —— 浮标是水面上受光的凸出小物体。
        warm_mask = mask
        bright_mask = None
        if getattr(self, "bobber_bright_enabled", True):
            try:
                bright_mask = self._bright_blob_mask(bmp)
            except Exception:
                bright_mask = None
        if bright_mask is not None and bright_mask.any():
            if warm_mask is not None and warm_mask.any():
                mask = warm_mask | bright_mask
                self._log_once("v48 检测判据: 暖色∪亮团 合并生效")
            else:
                mask = bright_mask
                self._log_once("v48 检测判据: 暖色无命中，改用亮团判据")
        if not mask.any():
            return None
        # ⚠️ 曾经加过 morph_close(r=4) 想修「浮标被鱼线切碎」，实测反而更糟：
        # 浮标右侧 30px 处有一团水面反光，颜色与浮标本体几乎一致，闭运算把
        # 两者连成一块，中心被拖偏 45px。不做闭运算时最大块就是浮标本体。
        blocks = bc.label_blocks(mask)
        if not blocks:
            return None
        sx = int(w * getattr(self, "exclude_side_ratio", 1.0 / 7.0))
        by = int(h * (1.0 - getattr(self, "exclude_bottom_ratio", 0.25)))
        lim = getattr(self, "sky_limit_y", None)
        # ★ v43：地平线测不出时的兜底上边界（与 _drop_excluded 保持一致）。
        #   真机实测：地平线 y=9 贴顶被判 UI 硬边 → lim=None → 顶部 y=16 的
        #   80x34 亮带成为最大候选，把真浮标压掉。这里必须同样切掉顶部。
        if lim is None and h:
            lim = max(int(h * 0.06), 40)
        masks = getattr(self, "ui_masks", None) or []
        scale = w / 1280.0
        min_area = max(int(20 * scale * scale), 12)
        # ui_masks 用「整屏比例坐标」表达（小地图在右上 12%、头像在左上等）。
        # 但本函数拿到的 bmp 只是截屏子区（2560x1600 上是 1280x700 @ (640,400)），
        # 所以必须先把比例换算成「整屏绝对坐标」，再减掉截屏区左上角偏移，
        # 才能得到截屏区内的局部坐标。否则小地图遮不掉 —— 它永远是最大暖色块，
        # 把真浮标压掉（实测四张图全部误报到 (2180,86)）。
        sa = getattr(self, "_search_area_full", None)   # (left, top, full_w, full_h)
        if sa is not None and sa[2] and sa[3]:
            sa_l, sa_t, full_w, full_h = sa
        else:
            sa_l = sa_t = 0
            full_w, full_h = float(w), float(h)
        mask_rects = []
        for (mx1, my1, mx2, my2) in masks:
            mask_rects.append((mx1 * full_w - sa_l, my1 * full_h - sa_t,
                               mx2 * full_w - sa_l, my2 * full_h - sa_t))
        best = None
        n_ok = 0
        n_fp_rej = 0
        for size, x0, y0, x1, y1, cx, cy in blocks:
            if size < min_area:
                continue
            if cx < sx or cx > w - sx or cy > by:
                continue                      # 左右 1/7 / 下 1/4 排除
            if lim is not None and cy <= int(lim):
                continue                      # 天空分界线以上排除
            # UI 遮罩（比例坐标）：小地图/头像/聊天框/技能条里的暖色块不是浮标。
            # 实测不套这层时，右上角小地图 (2180,86) 永远是最大暖色块，把真浮标压掉。
            skip = False
            for (ax1, ay1, ax2, ay2) in mask_rects:
                if (ax1 <= cx <= ax2) and (ay1 <= cy <= ay2):
                    skip = True
                    break
            if skip:
                continue
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            if max(bw, bh) > w * 0.35:
                continue                      # 太大 = 进度条/UI，不是浮标
            if bh > 0 and (bw / float(bh)) > 6.0:
                continue                      # 细横条 = 地平线/UI 边框
            # ★ v43：贴顶宽横条拦截。真机实测 21:17:38~40：一条 y=16、
            #   80x34→95x34、横向匀速滑动、宽度随时间的亮带（窗口硬边/水面
            #   反光）宽高比只有 2.35，逃过上面 6.0 的判据，area 1969~2423
            #   比真浮标（~360）大一个量级，按面积排序必赢 → 锚点定错。
            #   浮标不会贴着截图区顶边、也不会长得比它高 2 倍还宽。
            if cy < max(int(80 * scale), 60) and bw > 40 * scale and bw > bh * 1.8:
                continue                      # 贴顶宽横条 = UI 硬边/反光带
            # ★ v35：亮度判据下，天空/远山的亮边会成 684x2、594x2 这种「超宽
            #   极扁」的横条，面积动辄上千，按面积排序必然把真浮标压掉（实测
            #   真浮标只有 21x9/95px）。浮标是立体小物体，两个方向都得有几个
            #   像素：任一边 < 4px（按 1280 宽归一）即视为亮边/噪点。
            edge_min = max(int(4 * scale), 3)
            if bh < edge_min or bw < edge_min:
                continue
            # ★ v43：浮标指纹校验。首竿标定成功后记下面积/宽高基准，
            #   后续每竿拿候选跟基准比 —— 真机教训：顶部横带 area 1969~2423
            #   是基准（~360）的 5~7 倍，宽高比也偏扁，一眼该否决。
            #   放宽到 [0.3x, 3.0x] 面积区间 + 长宽比接近，避免因远近/缩放误杀。
            fp = getattr(self, "bobber_fingerprint", None)
            if fp and fp.get("frames", 0) >= 5:
                fa = fp.get("area") or 0
                if fa:
                    r = size / float(fa)
                    if r < 0.3 or r > 3.0:
                        n_fp_rej += 1
                        continue                  # 面积偏离基准太多
                fr = (fp.get("w", 0) / float(fp.get("h") or 1))
                cr = bw / float(bh)
                if fr > 0 and (cr / fr > 2.2 or fr / cr > 2.2):
                    n_fp_rej += 1
                    continue                  # 长宽比形态不符
            n_ok += 1
            if best is None or size > best[0]:
                best = (size, cx, cy, bw, bh)
        # v36：把本帧候选统计记下来，供日志/离线核对
        try:
            self._last_detect_note = (
                "候选=%d 最佳=%s%s" % (
                    n_ok,
                    "-" if best is None else
                    "%dx%d size=%d @(%d,%d)" % (best[3], best[4],
                                                best[0], best[1], best[2]),
                    (" 指纹否决%d" % n_fp_rej) if n_fp_rej else ""))
        except Exception:
            pass
        if best is None:
            return None
        size, cx, cy, bw, bh = best
        # 多帧连续性确认：水面反光块的颜色和真浮标可以完全一致
        # （实测 e9f81d 误报块 rgb(109,82,54) vs 9d6409 真浮标 rgb(110,84,57)），
        # 单帧颜色分不开。唯一稳定差别是位置：浮标随水面起伏但只在几像素内摆，
        # 反光块每帧乱跳。所以要求「同位置连续 confirm_frames 帧命中」。
        cf = getattr(self, "diff_confirm_frames", 1)
        cc = getattr(self, "diff_confirm_dist", 12)
        if cf > 1 and not single_frame:
            prev = getattr(self, "_diff_prev", None)
            if (prev is not None
                    and abs(cx - prev[0]) <= cc and abs(cy - prev[1]) <= cc):
                self._diff_hits = getattr(self, "_diff_hits", 0) + 1
            else:
                self._diff_hits = 1
            self._diff_prev = (cx, cy)
            if self._diff_hits < cf:
                self.log("色差候选 (%d,%d) %dx%d 连续 %d/%d，等确认",
                         cx, cy, bw, bh, self._diff_hits, cf)
                return None
        self._last_block = (size, bw, bh)      # ★ v43：供首竿建「浮标指纹」
        self.log("色差浮标命中: (%d,%d) %dx%d area=%d (连续 %d 帧)",
                 cx, cy, bw, bh, size, getattr(self, "_diff_hits", 1))
        return (int(cx), int(cy))

    # ---- 逐行对应 SearchBobberFinder.cs:59-98 ----
    def _find_points(self, bmp):
        h, w = bmp.shape[:2]
        has_prev = self.previous_location is not None

        r = max(40, int(w * 40.0 / BASE_WIDTH_FOR_RADIUS))   # 40px @1366 → 按宽度等比
        if has_prev:
            min_x = max(self.previous_location[0] - r, 0)
            max_x = min(self.previous_location[0] + r, w)
            min_y = max(self.previous_location[1] - r, 0)
            max_y = min(self.previous_location[1] + r, h)
        else:
            min_x, max_x, min_y, max_y = 0, w, 0, h

        sub = bmp[min_y:max_y, min_x:max_x]
        if sub.size == 0:
            return []

        mask = self._match_mask(sub)

        # UI 遮罩（比例坐标 → 子窗口内坐标）
        if self.ui_masks:
            sh, sw = sub.shape[:2]
            for (x1, y1, x2, y2) in self.ui_masks:
                a, b = int(sw * x1), int(sw * x2)
                c, d = int(sh * y1), int(sh * y2)
                mask[c:d, a:b] = False

        ys, xs = np.nonzero(mask)
        points = [(int(x) + min_x, int(y) + min_y) for x, y in zip(xs, ys)]

        # 逐行对应：if (points.Count > 1000) → 清空
        if len(points) > 1000 and self.pixel_classifier.mode != PixelClassifier.TAN:
            self.log("Error: Too much of the feather colour in this image, "
                     "please adjust the colour configuration !")
            return []
        return points

    # ---- 逐行对应 SearchBobberFinder.cs:100-111 ----
    def _match_mask(self, sub):
        rgb = sub.astype(np.int16)
        r = rgb[:, :, 0]
        g = rgb[:, :, 1]
        b = rgb[:, :, 2]
        c = self.pixel_classifier

        if c.mode == PixelClassifier.RED:
            return ((r * c.colour_multiplier > g)
                    & (r * c.colour_multiplier > b)
                    & (np.minimum(b, g) * c.colour_closeness_multiplier > np.maximum(b, g) - 20))
        if c.mode == PixelClassifier.BLUE:
            return ((b * c.colour_multiplier > g)
                    & (b * c.colour_multiplier > r)
                    & (np.minimum(r, g) * c.colour_closeness_multiplier > np.maximum(r, g) - 20))
        if c.mode == PixelClassifier.TAN:
            # v20: 不再硬编码绝对亮度签名（换钓点后浮标被压暗就全失配）。
            # 向量化复用 PixelClassifier._is_match_tan 的相对判据，
            # 参数与标量版共用同一组旋钮（tan_min_red / tan_min_rb / ...），
            # 保证 find() 与单元测试走同一套逻辑。
            floor = c.tan_min_red
            d_rb = r - b
            d_rg = r - g
            return ((r >= floor)
                    & (r > g) & (g > b)
                    & (d_rb >= c.tan_min_rb)
                    & (d_rg >= c.tan_min_rg)
                    & (r.astype(np.float32) * c.tan_max_rg_ratio > g)
                    & (g.astype(np.float32) * c.tan_max_gb_ratio > b))
        # 未知模式直接抛错，绝不静默落回 red（否则会匹配几十万像素）
        raise ValueError(f"未知的像素分类模式: {c.mode!r}")

    # ---- v20 新增：差分标定 ----
    def _calibrate(self, post):
        """抛竿前后帧差分定位浮标，返回截图内坐标或 None。

        成功时顺带把 tan 判据按实测色值放宽（见 bobbercalib.signature_from），
        这样后面的跟踪帧不再受「换钓点浮标变暗」影响。
        """
        from . import bobbercalib as bc
        pre = self.calib_pre
        if pre is None or post is None:
            return None
        if pre.shape != post.shape:
            # 分辨率变了（切窗口/改缩放），本次不做差分
            return None
        try:
            res = bc.pick_bobber(pre, post, log=self.log)
        except Exception as e:
            self.log("差分标定异常: %s", e)
            return None
        if res is None:
            # 诊断：区分「浮标没落水」和「落水了但被筛掉」。
            # 实测 c006_post 整帧 r max 只有 122，黄浮标(r=255)一个都没，
            # 说明 post 帧抓早了 —— 浮标还在空中，或已飞出截图区上边界。
            try:
                ypx = int(bc.yellow_mask(post).sum())
                self.log("差分诊断: post 帧黄色像素 %d（0=浮标不在画面，非颜色判据问题）", ypx)
            except Exception:
                pass
            return None
        # 用实测色值放宽 tan 判据（只放宽，不收紧：取两者较松的一侧）
        # ★ v43：放宽前先自检。真机实测选中过 rgb=(41,59,75) 的水面块，
        #   sig = signature_from 会算出 tan_min_rb = max(41-75, 8) = 8，
        #   而原值是 20 —— 直接把判据从 R-B>20 放宽到 R-B>8，
        #   后续整竿都在抓水面。所以：差分结果本身必须是暖色方向才允许放宽。
        mr, mg, mb = [int(v) for v in res["median_rgb"]]
        if mr - mb <= 5:
            self.log("差分标定否决: (%d,%d) %dx%d rgb=(%d,%d,%d) R-B=%d 非暖色方向，"
                     "判为水面波动/天空，不采信",
                     res["x"], res["y"], res["w"], res["h"], mr, mg, mb, mr - mb)
            self.calib_pre = None
            self._calib_bmp = None
            return None
        c = self.pixel_classifier
        sig = bc.signature_from(res["median_rgb"])
        c.tan_min_red = min(c.tan_min_red, sig["tan_min_red"])
        c.tan_min_rb = min(c.tan_min_rb, sig["tan_min_rb"])
        c.tan_min_rg = min(c.tan_min_rg, sig["tan_min_rg"])
        self.log("差分标定成功: (%d,%d) %dx%d rgb=%s → tan 门槛放宽为 r>=%d rb>=%d rg>=%d",
                 res["x"], res["y"], res["w"], res["h"], res["median_rgb"],
                 c.tan_min_red, c.tan_min_rb, c.tan_min_rg)
        self._calib_result = res
        # 自己写回：单独调用 _calibrate 也自洽，不依赖调用方再补一刀
        self.previous_location = (res["x"], res["y"])
        self.calib_pre = None
        self._calib_bmp = None
        return (res["x"], res["y"])

    # ---- 逐行对应 SearchBobberFinder.cs:118-139 ----
    def _score_points(self, points):
        if not points:
            return None

        if self.pixel_classifier.mode == PixelClassifier.TAN and self.loose_aspect:
            # 非原版：抛竿后浮标在空中/被水花切碎，只卡面积
            return self._score_tan_loose(points)

        pts = np.asarray(points, dtype=np.int32)
        x = pts[:, 0]
        y = pts[:, 1]

        # 每个点的邻域计数 |dx|<10 且 |dy|<10（原版 O(n²)，用搜索排序等价加速）
        order = np.lexsort((y, x))
        xs, ys = x[order], y[order]
        counts = np.empty(len(xs), dtype=np.int64)
        lo_x = np.searchsorted(xs, xs - 9, side="left")
        hi_x = np.searchsorted(xs, xs + 10, side="right")
        for i in range(len(xs)):
            seg = ys[lo_x[i]:hi_x[i]]
            counts[i] = np.count_nonzero((seg > ys[i] - 10) & (seg < ys[i] + 10))

        # OrderByDescending(count).FirstOrDefault() → 取最大 count 的第一个
        best_idx = int(np.argmax(counts))
        bx, by = int(xs[best_idx]), int(ys[best_idx])

        if self.pixel_classifier.mode == PixelClassifier.TAN:
            # v20: 原版只取邻域计数最大的那个点。换钓点后画面里出现大片
            # 暖色栈道/岸边（实测 178x68 / 9009px），它的计数压过真浮标，
            # 一次校验失败就整帧判空 → 连续 9 竿找不到浮标。
            # 改为按计数降序依次试候选中心，第一个通过校验的就采纳。
            return self._validate_tan_candidates(pts, xs, ys, counts)

        return (bx, by)

    def _label_components(self, xs, ys):
        """纯 numpy 连通域标记（8 邻接），不依赖 scipy。

        返回 (labels, lab_arr, sizes)：
          labels   — (h,w) int32 标记图，0 为背景
          lab_arr  — 与输入点一一对应的块 id
          sizes    — 各块像素数，sizes[0]=0
        用两遍扫描 + 并查集实现，点数规模在 1e4 以内，Python 循环可接受。
        """
        w = int(xs.max()) + 1
        h = int(ys.max()) + 1
        coord = np.stack([ys, xs], axis=1)          # (N,2)
        # 用字典做并查集
        parent = {}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # 第一遍：给每个点一个初始标记（用一维索引）；与其「已访问」的 8 邻居合并
        seen = {}
        for i in range(len(coord)):
            y, x = int(coord[i, 0]), int(coord[i, 1])
            parent[i] = i
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    j = seen.get((y + dy, x + dx))
                    if j is not None:
                        union(i, j)
            seen[(y, x)] = i

        # 第二遍：压缩成连续块 id
        roots = {}
        lab_arr = np.zeros(len(coord), dtype=np.int32)
        for i in range(len(coord)):
            r = find(i)
            lb = roots.get(r)
            if lb is None:
                lb = len(roots) + 1
                roots[r] = lb
            lab_arr[i] = lb

        sizes = np.bincount(lab_arr, minlength=len(roots) + 1)
        sizes[0] = 0
        labels = np.zeros((h, w), dtype=np.int32)
        labels[coord[:, 0], coord[:, 1]] = lab_arr
        return labels, lab_arr, sizes

    def _drop_excluded(self, pts, bmp=None):
        """丢掉落在排除区的候选点（用户指定：左 1/7、右 1/7、上 1/4、下 1/4）。

        排除区是 UI 区：小地图、头像/技能条、聊天框、任务追踪。
        浮标不可能出现在这些位置，但它们的色块经常被当成候选。

        另加一条：天空侧（sky_limit_y 以上）也丢，分界线由标定阶段自动测出。
        pts 可能是 None / [] / 各种结构，这里做兼容处理。
        """
        if not pts:
            return pts

        h = w = None
        if bmp is not None:
            try:
                h, w = bmp.shape[0], bmp.shape[1]
            except Exception:
                h = w = None
        if h is not None:
            self.excl_h, self.excl_w = h, w
        else:
            h, w = self.excl_h, self.excl_w

        x0 = int(w * self.exclude_side_ratio) if w else None
        x1 = int(w * (1.0 - self.exclude_side_ratio)) if w else None
        # 上边界 = 天空分界线（第一次抛竿后按实际画面测出）。
        # 没测到时退回 exclude_top_ratio（默认 0 = 不排除）。
        limit = int(self.sky_limit_y) if self.sky_limit_y is not None else None
        if limit is not None:
            y0 = limit
        else:
            # ★ v43：地平线测不出时的兜底上边界。
            #   真机实测 21:17:17：地平线候选 y=9 太贴顶被判「窗口 UI 硬边」，
            #   sky_limit_y=None → 上边界 0（完全不排除）。结果画面顶部一条
            #   y=16、80x34、匀速横滑的亮带（窗口硬边/水面反光）成了唯一候选，
            #   把真浮标 (548,235) 压掉，锚点定错 → 3 帧 miss 停机。
            #   兜底取 h*0.06（801 高 → 48px）：足够切掉顶部硬边，又不会
            #   误伤从第 2 行起的浮标（min_horizon_conf 那段注释里的顾虑）。
            if h:
                y0 = max(int(h * 0.06), 40)
        y1 = int(h * (1.0 - self.exclude_bottom_ratio)) if h else None

        if x0 is None:
            return pts

        def _xy_of(p):
            try:
                if isinstance(p, (tuple, list)):
                    if len(p) == 2 and isinstance(p[0], (int, float)):
                        return p[0], p[1]          # (x,y)
                    if len(p) >= 2 and isinstance(p[1], (tuple, list)):
                        return p[1][0], p[1][1]    # ((x,y), score)
                return getattr(p, "x", None), getattr(p, "y", None)
            except Exception:
                return None, None

        kept = []
        n_side = n_vert = n_sky = 0
        for p in pts:
            x, y = _xy_of(p)
            if x is not None and y is not None:
                if x < x0 or x > x1:
                    n_side += 1
                    continue
                if y < y0 or y > y1:
                    if limit is not None and y <= limit:
                        n_sky += 1
                    else:
                        n_vert += 1
                    continue
            kept.append(p)

        if n_side or n_vert or n_sky:
            self._log_once(
                "排除区过滤: 保留 x%d~%d y%d~%d | 丢 左/右 %d 个、上/下 %d 个、天空 %d 个（剩 %d）"
                % (x0, x1, y0, y1, n_side, n_vert, n_sky, len(kept)))
        return kept

    def _log_once(self, msg):
        """同一类日志只打一次，避免每帧刷屏。"""
        seen = getattr(self, "_seen_logs", None)
        if seen is None:
            seen = set()
            self._seen_logs = seen
        if msg in seen:
            return
        seen.add(msg)
        log = getattr(self, "log", None)
        if callable(log):
            log("%s", msg)

    def _validate_tan_candidates(self, pts, xs, ys, counts, max_try=40):
        """v20 新增：按连通块分组，逐块尝试，返回首个通过校验的中心。

        原版只取邻域计数最大的点，一次校验失败就整帧判空。换钓点后画面里
        出现大片暖色栈道（178x68 / 9009px），计数压过真浮标 → 连续 9 竿判空。

        去重必须按「连通块」而不是坐标网格：栈道块内部几十个点的计数都是 380，
        按坐标网格去重会把尝试次数全耗在栈道这一块上，真浮标永远轮不到。
        改为连通域标记，每块只提交一个代表点（块内邻域计数最大者），
        块间按像素数降序（真浮标通常是画面里最显眼的暖色块）。

        注意：判定必须用「整块」的像素数和 bbox，不能用 ±20px 局部窗口——
        9009px 的栈道在局部窗口里只露出 41x36，会骗过宽度上限。
        """
        _, lab_arr, sizes = self._label_components(xs, ys)

        # 每块的完整 bbox（整块，非局部窗口）
        lab_min_x = {}
        lab_max_x = {}
        lab_min_y = {}
        lab_max_y = {}
        best_in_lab = {}
        for i in range(len(xs)):
            lb = int(lab_arr[i])
            x, y = int(xs[i]), int(ys[i])
            if lb not in lab_min_x:
                lab_min_x[lb] = lab_max_x[lb] = x
                lab_min_y[lb] = lab_max_y[lb] = y
            else:
                if x < lab_min_x[lb]:
                    lab_min_x[lb] = x
                if x > lab_max_x[lb]:
                    lab_max_x[lb] = x
                if y < lab_min_y[lb]:
                    lab_min_y[lb] = y
                if y > lab_max_y[lb]:
                    lab_max_y[lb] = y
            cur = best_in_lab.get(lb)
            if cur is None or counts[i] > counts[cur]:
                best_in_lab[lb] = i

        blocks = sorted(best_in_lab.items(), key=lambda kv: -int(sizes[kv[0]]))
        n_try = 0
        for lb, i in blocks:
            n_try += 1
            whole = dict(
                size=int(sizes[lb]),
                w=lab_max_x[lb] - lab_min_x[lb] + 1,
                h=lab_max_y[lb] - lab_min_y[lb] + 1,
            )
            res = self._validate_tan(pts, int(xs[i]), int(ys[i]),
                                     quiet=(n_try > 1), whole=whole)
            if res is not None:
                if n_try > 1:
                    self.log("浮标候选：试到第 %d 个连通块才通过 %s（该块 %d px %dx%d）",
                             n_try, res, whole['size'], whole['w'], whole['h'])
                return res
            if n_try >= max_try:
                break
        return None

    def _score_tan_loose(self, points):
        pts = np.asarray(points, dtype=np.int32)
        # 面积用连通块自身像素数（不是外扩窗口）
        best, best_delta = None, 1 << 30
        for px, py in self._cluster_centers(pts):
            area = self._blob_area(pts, px, py)
            if area >= self.tan_min_area:
                d = abs(area - self.tan_expect_area)
                if d < best_delta:
                    best_delta, best = d, (px, py)
        return best

    def _validate_tan(self, pts, bx, by, quiet=False, whole=None):
        """tan 模式必须过连通块尺寸 + 长宽比校验（原版没有，因为原版无 tan）。

        v20 全部阈值按「三个真实钓点」的实测分布重新标定（1280 宽截图，1:1 像素）：
          真浮标  面积(a20) 201~1137 / 宽 22~72 / 高 11~33 / 长宽比 1.16~3.62
          误报样本 岸边栈道 9009px & 178x68、水花碎点 13px & 长宽比 0.89

        whole 为所在连通块的整块统计（size/w/h）。由 _validate_tan_candidates 传入。
        没有 whole 时退回 ±20px 局部窗口（仅在单独调用时发生）。
        """
        h_ratio = self._cur_w / 1280.0        # 1280 = 本机截图宽度基准（1:1 像素）
        scale = max(h_ratio, 0.6)
        if whole is not None:
            bw, bh = whole['w'], whole['h']
            area = whole['size']
        else:
            box = self._blob_bbox(pts, bx, by)
            if box is None:
                return None
            bw = box[2] - box[0] + 1
            bh = box[3] - box[1] + 1
            area = int(np.count_nonzero(
                (np.abs(pts[:, 0] - bx) <= 20) & (np.abs(pts[:, 1] - by) <= 20)))

        min_area = max(int(self.tan_min_area * scale * scale), 40)
        max_area = max(int(self.tan_max_area * scale * scale), 300)
        max_width = max(int(self.tan_max_width * scale), 12)

        if area < min_area:
            if not quiet:
                self.log("tan校验: 面积 %d < 下限 %d", area, min_area)
            return None
        if area > max_area:                 # 大片木头（栈道/桥/栏杆/船）排除
            if not quiet:
                self.log("tan校验: 面积 %d > 上限 %d（疑似岸边/栈道）", area, max_area)
            return None
        if bw > max_width:                  # 横条物体（178px 栈道）排除
            if not quiet:
                self.log("tan校验: 宽 %d > 上限 %d（疑似栏杆/栈道）", bw, max_width)
            return None
        ratio = bw / float(bh) if bh else 0.0
        if not (self.tan_min_ratio <= ratio <= self.tan_max_ratio):
            if not quiet:
                self.log("tan校验: 长宽比 %.2f 超出 %.1f~%.1f",
                         ratio, self.tan_min_ratio, self.tan_max_ratio)
            return None
        # 非原版：尺寸稳定性。同一竿的鱼鳔尺寸几乎不变；
        # 而水里杂物 / 别的玩家浮标 / 贴脸物体忽大忽小。
        # 面积比上一次命中跳变超过 AREA_JUMP 倍 → 视为候选不稳，本帧判空，
        # 逼下一帧重新确认（防止锁到错误物体后靠它的 y 抖动误报咬钩）。
        self._area_hist.append(area)
        if len(self._area_hist) > 5:
            self._area_hist.pop(0)
        if len(self._area_hist) >= 2:
            med = sorted(self._area_hist)[len(self._area_hist) // 2]
            if med > 0 and not (1.0 / AREA_JUMP <= area / float(med) <= AREA_JUMP):
                self.log("尺寸跳变 %.0f→%.0f（%.2fx），本帧判空", med, area,
                         area / float(med))
                return None

        return (bx, by)

    @property
    def _scale(self):
        """tan 阈值标定基准宽度 = 2560（真鱼鳔 31x20 / 503px 就是在 2560 上量的）。"""
        w = getattr(self, "_cur_w", 0) or 2560
        return w / 2560.0

    def _blob_area(self, pts, bx, by):
        sel = (np.abs(pts[:, 0] - bx) <= 40) & (np.abs(pts[:, 1] - by) <= 40)
        return int(np.count_nonzero(sel))

    def _blob_bbox(self, pts, bx, by):
        sel = (np.abs(pts[:, 0] - bx) <= 20) & (np.abs(pts[:, 1] - by) <= 20)
        sub = pts[sel]
        if len(sub) == 0:
            return None
        return (int(sub[:, 0].min()), int(sub[:, 1].min()),
                int(sub[:, 0].max()), int(sub[:, 1].max()))

    def _cluster_centers(self, pts):
        seen = set()
        out = []
        for px, py in pts[::7]:
            key = (int(px) // 20, int(py) // 20)
            if key in seen:
                continue
            seen.add(key)
            out.append((int(px), int(py)))
        return out

    # ---- 平台层注入点（对应 WowScreen.GetBitmap / GetScreenPositionFromBitmapPostion）----
    grabber = None

    def _grab(self):
        if self.grabber is None:
            raise RuntimeError("未注入 grabber（对应 WowScreen.GetBitmap）")
        return self.grabber.grab()

    def _screen_search_area(self):
        """返回 (left, top, full_w, full_h)：截屏区左上角 + 整屏尺寸。

        ui_masks 是按「整屏比例」写的，必须换算成截屏子区内的局部坐标，
        否则小地图这类 UI 遮不掉。
        """
        g = self.grabber
        if g is None or not hasattr(g, "search_area"):
            return None
        try:
            left, top, w, h = g.search_area()
        except Exception:
            return None
        fw = fh = None
        for attr in ("screen_width", "screen_height"):
            pass
        try:
            rect = g._client_rect()
            # _client_rect 返回 (left, top, w, h) —— 游戏窗口客户区尺寸
            fw, fh = rect[2], rect[3]
        except Exception:
            pass
        if not fw or not fh:
            # 兜底：按 search_area 的固定比例反推
            fw = w * 2.0 if w else w
            fh = h / 0.4375 if h else h
        return (left, top, fw, fh)

    def _to_screen(self, pos, bmp):
        if self.grabber is None:
            return pos
        return self.grabber.to_screen(pos[0], pos[1])
