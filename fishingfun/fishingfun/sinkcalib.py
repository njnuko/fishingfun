"""咬钩幅度自动标定（v21）。

为什么需要它：
    strike_value 是个绝对像素值。写死 4 的时候，水面波纹浮动 ±2~4px
    就会立刻满足判定 —— 日志实测每次「咬钩」都是 yDiff=-4，一分不多
    一分不少，且都在开始监控后 2 秒左右，这就是波纹而不是鱼。

    换个钓点、换个分辨率、换个水面，真实下沉幅度都会变，绝对阈值
    永远调不准。所以改成：抛竿后连续采样 17 秒，秒级截一帧，看浮标
    y 的真实波动范围；把「正常浮动上限」和「真咬钩跳变」分开，
    用实测数据反推阈值。

用法（只在第一竿做一次）：
    calib = SinkCalibrator(finder, screen, log=log)
    thr = calib.run()          # 阻塞 17 秒，返回实测阈值
    watcher.strike_value = thr
"""

import time

import numpy as np


def _argmax_darkening(G, row, lo, hi, start):
    """在 [start, hi) 里找梯度最大、且方向为「上亮下暗」的分界行。

    真地平线 = 天空(亮) → 水面(暗)，行亮度递减。UI 条结束、黑边结束都是
    递增方向，必须排除，否则会把天空边界当成地平线。
    """
    a = max(int(lo), int(start))
    b = min(int(hi), len(G))
    if b <= a:
        return None
    best_i, best_g = None, -1.0
    for i in range(a, b):
        before = float(row[max(0, i - 3):i].mean())
        after = float(row[i:min(len(row), i + 4)].mean())
        if after >= before:          # 方向不符（暗→亮），跳过
            continue
        g = float(G[i])
        if g > best_g:
            best_g, best_i = g, i
    return best_i


class SinkCalibrator:
    """抛竿后 17 秒采样，实测浮标 y 的波动幅度，反推咬钩阈值。

    采样策略（按用户指定）：
        · 抛竿后等 17 秒，每一秒截一帧
        · 记录每帧浮标中心 y 和像素面积
        · 求相邻帧 |Δy|，取最大值 = 该点位的真实跳变幅度

    排除干扰：
        · 浮标消失/面积骤减的帧 → 不算位移（那是目标丢失，不是下沉）
        · 面积突变超过 2.5 倍的帧 → 同上
    """

    # 正常水面浮动上限的经验倍数：实测跳变幅度 × 这个系数，
    # 保证真咬钩能过、波纹抖动过不了。
    RIPPLE_SLACK = 0.35

    # 与时间中位数背景的差异容差（RGB 三通道差之和）。
    # 水面噪声实测 ±3/通道 → 和约 9，取 24 留足余量。
    DIFF_TOL = 24

    def __init__(self, finder, screen, log=None, seconds=17, samples=None,
                 dump=None):
        self.finder = finder
        self.screen = screen
        self.log = log or (lambda *a, **k: None)
        self.seconds = seconds
        # ⚠️ 必须取整：seconds 是 float(17.0)，直接给 range() 会抛
        # 'float' object cannot be interpreted as an integer
        self.samples = int(samples) if samples else max(1, int(round(float(seconds))))
        self.dump = dump                    # 可选：把每帧 bmp 存盘
        self.points = []                    # [(t, y, area, bmp), ...]

    # ---- 单帧采样 ----
    def _grab_and_locate(self):
        """抓一帧并定位浮标，返回 (y, area, bmp) 或 (None, 0, bmp)。"""
        bmp = None
        try:
            grab = getattr(self.screen, "grab", None)
            if grab is not None:
                bmp = grab()
        except Exception as e:
            self.log("标定: 截图异常 %s", e)
        y = area = None
        self._last_detect = "-"
        try:
            pos = self.finder.find(bmp) if bmp is not None else self.finder.find()
            if pos is not None:
                y = int(pos[1])
                res = getattr(self.finder, "_calib_result", None)
                if res:
                    area = int(res.get("area", 0))
                if area is None:
                    cnt = getattr(self.finder, "_last_count", None)
                    area = int(cnt) if cnt else 0
            # v36：记录这一帧的检测明细（尺寸 + 候选数），用于离线核对
            self._last_detect = getattr(self.finder, "_last_detect_note", "-")
        except Exception as e:
            self.log("标定: 定位异常 %s", e)
        return y, (area or 0), bmp

    def detect_horizon(self):
        """自动测出「天空/水面分界线」的行号（不硬编码位置）。

        依据用户给出的关键事实：**分界线在整个钓鱼过程中不变**。
        所以做法是：取采样帧的每一行，算它在时间上的稳定性 ——
        天空行和水面行各自内部都稳定，只有分界线那一行附近
        「上下颜色过渡」会让行均值在两个平台的交界处跳变。

        更稳的算法：对每帧求「行均值在垂直方向的梯度」，天空→水面
        的交界处梯度最大；再对所有帧取中位数，得到不受水花干扰的行号。

        返回 (y_horizon, confidence) —— y_horizon 以上的行视为天空。
        """
        bs = [b for (_t, _y, _a, b) in self.points if b is not None]
        if len(bs) < 3:
            return None, 0.0
        shapes = set(b.shape for b in bs)
        if len(shapes) != 1:
            return None, 0.0

        grads = []
        for b in bs:
            # ⚠️ 原写法 b.reshape(h,-1,3).mean(axis=1) 是「每行拉平成一个数，
            # 但 mean(axis=1) 求的是通道均值，不是整行均值」——它丢掉了
            # 横向结构，实测真分界线跳变 64.2 被稀释成 6.3，检测反而落到
            # 平坦水面的噪声行上（y=625）。正确做法：先求每行三通道均值，
            # 再对行序列求一阶差分。
            row = b[:, :, :3].mean(axis=2).mean(axis=1)        # (h,) 每行亮度
            g = np.abs(np.diff(row))                           # (h-1,)
            grads.append(g)
        G = np.median(np.stack(grads, axis=0), axis=0)        # 取中位数抗水花

        # 分界线搜索区间：从画面顶部（0）到 55%。
        # ⚠️ 实测本机：真海平面在截屏区上边界附近甚至其上方。
        #   截屏区 1280x638 @ (640,409) 时，海平面 396~402 完全在区外，
        #   区内全是水面 —— 任何区间内搜索都只能找到水面噪声（实测 y=625）。
        #   所以这里不设下限（lo 允许为 1），只要顶部有真过渡就能测到。
        #   run.py 的截屏区上移到 370 后，海平面落在局部 y≈25~32，能被测到。
        h = bs[0].shape[0]
        lo = 1
        hi = min(int(h * 0.55), len(G) - 1)
        if hi <= lo:
            return None, 0.0
        seg = G[lo:hi]
        idx = int(np.argmax(seg)) + lo
        peak = float(seg.max())
        # 置信度：峰值要显著高于整段的中位数，否则说明没有清晰分界
        med = float(np.median(seg)) or 1e-6
        conf = peak / med
        # ★ v32 判据修正：不能用「离顶部多少行」来判假地平线 —— 实测真地平线
        #   完全可能落在截屏区局部 y=9（截屏区上边界本就贴着天空分界线切）。
        #   真正能区分的是**梯度方向**：
        #     真地平线（天空→水面）= 上亮下暗，方向为负
        #     UI 条/黑边结束（暗→亮）  = 上暗下亮，方向为正
        #   实测病态帧 local y=9 处 上均亮 127 → 下均亮 49 是负方向（真分界），
        #   而黑边结束处是 12 → 205 的正方向，用方向判据即可准确区分。
        last = bs[-1][:, :, :3].mean(axis=2).mean(axis=1)
        prev_lum = float(last[max(0, idx - 3):idx].mean())
        next_lum = float(last[idx:idx + 4].mean())
        if next_lum > prev_lum:
            self.log("⚠️  地平线梯度峰值 y=%d 方向为上暗下亮（%.0f→%.0f），"
                     "判为 UI/黑边交界而非天空→水面分界，改在下方重找",
                     idx, prev_lum, next_lum)
            idx2 = _argmax_darkening(G, last, lo, hi, idx + 1)
            if idx2 is None:
                self.log("⚠️  未找到上亮下暗的分界，本次不排除天空侧")
                self._horizon_conf = 0.0
                return None, 0.0
            self.log("地平线改用 y=%d", idx2)
            idx = idx2
            peak = float(G[idx])
            conf = peak / med
            prev_lum = float(last[max(0, idx - 3):idx].mean())
            next_lum = float(last[idx:idx + 4].mean())
        # 亮度差：只作参考，不再作为否决条件。
        # 实测本机截屏区几乎全是水面，上下亮度差只有 1.2，
        # 但分界线位置依然是稳定的 —— 用户要求「严格按分界线排除」，
        # 所以哪怕过渡很淡也要给出行号，只是标记为低置信。
        top = float(bs[-1][:idx + 1].mean())
        bot = float(bs[-1][idx + 1:].mean())
        self._horizon_conf = conf
        if abs(top - bot) < 6.0:
            self.log("地平线检测: y=%d 置信度=%.1fx（上下亮度差仅 %.1f，过渡很淡，仍按此线排除）",
                     idx, conf, abs(top - bot))
        else:
            self.log("地平线检测: y=%d 置信度=%.1fx 上均亮 %.0f 下均亮 %.0f",
                     idx, conf, top, bot)
        return idx, conf

    def locate_in_sequence(self):
        """用采到的多帧序列找浮标，**完全不依赖颜色**。

        原理：浮标是抛竿后出现在水里、并在接下来 17 秒里「一直在那儿、
        位置几乎不动」的那个团块。而水花、涟漪会迅速扩散消失，飞鸟/云影
        会移动，UI 元素则从头到尾都在（因此也不是「新出现」）。

        做法：
          1. 以第 1 帧为参考，对每一帧做差分，得到「相对首帧变了的地方」
          2. 求所有帧的「稳定性」：某像素/团块在多少帧里持续表现为变化
          3. 取持续最久、且位置方差最小的那个团块 —— 就是浮标

        返回 dict（x,y,w,h,area）或 None。
        """
        from . import bobbercalib as bc
        bmps = [(t, y, a, b) for (t, y, a, b) in self.points if b is not None]
        if len(bmps) < 4:
            self.log("序列定位: 有效帧只有 %d，放弃", len(bmps))
            return None
        shapes = set()
        for (_t, _y, _a, b) in bmps:
            shapes.add(b.shape)
        if len(shapes) != 1:
            self.log("序列定位: 帧尺寸不一致 %s，放弃", shapes)
            return None

        h, w = bmps[0][3].shape[:2]

        # ⚠️ 关键：不能拿「首帧」当参考。首帧往往还没有浮标，而浮标颜色
        # 又和周围接近时，逐帧 vs 首帧的差分几乎是 0（实测只剩 2 像素）。
        # 正确做法是「时间中位数背景」：同一像素在所有帧里取中位数，
        # 浮标只在部分帧存在 → 会被中位数剔除，得到的干净背景里没有浮标；
        # 再让每一帧和这个背景比，浮标就非常突出。
        cnt = np.zeros((h, w), np.int16)
        used = 0
        prev = None
        # 逐帧相邻差分：天空、水面这些「不动的东西」差分恒为 0；
        # 浮标只要和周围有一点点颜色差，随水面上下浮动就会持续产生
        # 差分区。水花/涟漪虽然也产生差分区，但位置每帧乱跳 →
        # 累加计数后不成「块」，后面按连通块的持续性再筛掉。
        for idx, (_t, _y, _a, b) in enumerate(bmps):
            cur = b.astype(np.int16)
            if prev is None:
                prev = cur
                continue
            d = np.abs(cur - prev).sum(axis=2) >= self.DIFF_TOL
            cnt += d.astype(np.int16)
            used += 1
            prev = cur
        if used < 3:
            self.log("序列定位: 可用帧 %d 太少，放弃", used)
            return None
        # 再补一层「相对整体平均帧」的差异，抓取长期稳定存在的浮标本体
        mean_f = np.stack([b.astype(np.float32) for (_t, _y, _a, b) in bmps],
                          axis=0).mean(axis=0)
        body = np.zeros((h, w), np.int16)
        for (_t, _y, _a, b) in bmps:
            body |= (np.abs(b.astype(np.float32) - mean_f).sum(axis=2)
                     >= self.DIFF_TOL).astype(np.int16)
        inter = None
        if used < 3:
            self.log("序列定位: 可用帧 %d 太少，放弃", used)
            return None

        # 判据：相对「整段时间的平均画面」始终不同的地方 = 一直待在那里的东西。
        # 天空、水面这些占大部分时间的区域，在均值里就是它自己 → 差分为 0。
        # 浮标只占一小块、但 17 帧里都在 → 均值里被稀释 → 差分持续命中。
        # 水花只在个别帧出现 → 也命中，但位置乱跳、不连通成块，后面几何过滤掉。
        stable = body > 0
        n_stable = int(stable.sum())
        ratio_px = n_stable / float(h * w)
        self.log("序列定位: 用 %d 帧，本体差分区 %d px / %d px = %.0f%%（动差 %d px 作参考）",
                 used, n_stable, h * w, 100.0 * ratio_px,
                 int((cnt >= max(int(used*0.35), 2)).sum()))
        # ★ v34：差分区过密说明「整幅画面都在动」——典型是水面波纹很强、或
        #   浮标与水面颜色几乎重合（实测浮标 rgb(27,45,57) vs 水面 rgb(31,48,61)，
        #   三通道只差 4/3/4）。此时浮标在差分掩膜里根本不突出，剩下能被
        #   分出来的只有波纹噪点，实测会挑中 (405,70) 这种天空/远山噪点当锚点，
        #   再用它的颜色放宽 tan，直接毁掉后续搜索。
        #   宁可不给锚点（退回已被确认过的色差命中点），也不能给错锚点。
        if ratio_px > 0.50:
            self.log("序列定位: 差分区占画面 %.0f%% > 50%%，画面整体在动，"
                     "浮标与水面色差过小，本算法失效 —— 放弃锚点（不采信）",
                     100.0 * ratio_px)
            return None
        # 补回浮标本体：上面只拿到「和背景不同」的像素，浮标内部同色区域
        # 可能漏掉，用闭运算连通即可
        if not stable.any():
            return None

        # 闭运算连回被打散的浮标
        stable = bc.morph_close(stable, r=2)
        blocks = bc.label_blocks(stable)
        if not blocks:
            self.log("序列定位: 没有稳定团块")
            return None

        # ---- 天空排除：先用 17 帧测出地平线，分界线以上的团块一律不要 ----
        horizon, hconf = self.detect_horizon()
        self._horizon = horizon
        self._horizon_conf = hconf

        scale = w / 1280.0
        # ⚠️ 序列掩膜 = 「与时间均值不同」，浮标内部颜色均匀只剩边缘环，
        # 比原版单帧差分的块要小得多。这里不能用原版 MIN_AREA=30 卡，
        # 否则 9px 半径的浮标只有 29px，差 1 像素就被扔掉。
        min_area = max(int(10 * scale * scale), 8)
        # 排除区（与 searchbobberfinder 保持一致）：左右各 1/7、下 1/4、天空分界线以上
        bf = self.finder
        xs0 = int(w * getattr(bf, "exclude_side_ratio", 1.0 / 7.0))
        xs1 = int(w * (1.0 - getattr(bf, "exclude_side_ratio", 1.0 / 7.0)))
        yb1 = int(h * (1.0 - getattr(bf, "exclude_bottom_ratio", 0.25)))
        cands = []
        skipped_sky = 0
        skipped_zone = 0
        for size, x0, y0, x1, y1, cx, cy in blocks:
            if size < min_area:
                self.log("  候选 (%d,%d) area=%d < min_area=%d 丢弃",
                         cx, cy, size, min_area)
                continue
            # 浮标不可能在天空里。用「块中心 + 80% 块体」双重判断，
            # 避免浮标刚好压线时被误杀。
            if horizon is not None:
                body_top = y0
                body_bot = y1
                in_sky_center = cy <= horizon
                mostly_sky = body_bot <= horizon
                if in_sky_center and mostly_sky:
                    skipped_sky += 1
                    continue
            # 排除区：中心落在左/右 1/7 或下方 1/4 一律丢
            if cx < xs0 or cx > xs1 or cy > yb1:
                skipped_zone += 1
                continue
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            # ★ v32：不能只看中心点是否在排除区内 —— 实测整片水面会连成一个
            #   1276x907 的超大团块，中心恰好在画面正中（cx=639），于是「中心
            #   合格」被放行，占画面 74%，最后在 fishbot 里才被判可疑丢弃。
            #   浮标的包围盒必然远小于画面：宽高任一超过画面的 25% 直接否决。
            if bw > w * 0.25 or bh > h * 0.25:
                self.log("  候选 (%d,%d) %dx%d 尺寸超画面 25%%（%dx%d），"
                         "判为大面积水面团块丢弃", cx, cy, bw, bh, w, h)
                skipped_zone += 1
                continue
            ratio = max(bw, bh) / float(max(min(bw, bh), 1))
            if not (bc.MIN_RATIO <= ratio <= bc.MAX_RATIO):
                self.log("  候选 (%d,%d) %dx%d 长宽比 %.1f 超限丢弃",
                         cx, cy, bw, bh, ratio)
                continue
            fill = size / float(bw * bh)
            # 序列定位的掩膜来自「与时间均值不同」，浮标内部颜色均匀会
            # 只剩边缘一圈 → fill 天然低于原版阈值，这里放宽到 0.12。
            if fill < 0.12:
                self.log("  候选 (%d,%d) %dx%d fill=%.2f 太稀疏丢弃",
                         cx, cy, bw, bh, fill)
                continue
            # 连续性检查（软性）：只用于「同样大小时谁更该被选」，不作硬门槛。
            # 浮标静止时相邻帧差恒为 0，硬卡 0.90 会把真浮标全筛掉。
            sub_cnt = cnt[max(y0, 0):y1 + 1, max(x0, 0):x1 + 1]
            cont = float((sub_cnt > 0).mean()) if sub_cnt.size else 0.0
            cands.append((size, fill, cx, cy, bw, bh, cont))
        if skipped_sky or skipped_zone:
            self.log("序列定位: 地平线 y=%s 已排除 %d 个天空侧团块；"
                     "左右 %.0f%%/下 %.0f%% 排除 %d 个（保留 x%d~%d y<%d）",
                     horizon, skipped_sky,
                     100.0 * getattr(bf, "exclude_side_ratio", 1.0 / 7.0),
                     100.0 * getattr(bf, "exclude_bottom_ratio", 0.25),
                     skipped_zone, xs0, xs1, yb1)
        if not cands:
            self.log("序列定位: 稳定团块都被几何条件筛掉")
            return None

        # 取面积最大（浮标是画面里最显眼的稳定新物体）
        # 先按面积排序（浮标是最显眼的稳定物体），面积相当再比连续性
        cands.sort(key=lambda c: (c[0], c[6]), reverse=True)
        size, fill, cx, cy, bw, bh, cont = cands[0]
        rgb = bc.median_rgb_in(bmps[-1][3], cx - bw // 2, cy - bh // 2,
                               cx + bw // 2, cy + bh // 2)
        self.log("序列定位成功: (%d,%d) %dx%d area=%d fill=%.2f rgb=%s",
                 cx, cy, bw, bh, size, fill, tuple(int(v) for v in rgb))
        return {"x": int(cx), "y": int(cy), "w": int(bw), "h": int(bh),
                "area": int(size), "median_rgb": rgb}

    def run(self):
        """阻塞采满 seconds 秒，返回建议的 strike_value。失败返回 None。"""
        self.points = []
        t0 = time.monotonic()
        interval = self.seconds / float(self.samples)
        self.log("标定开始: 抛竿后 %d 秒内每秒采样一次浮标位置（本次标定仅做一次）",
                 self.seconds)
        for i in range(self.samples):
            target = t0 + (i + 1) * interval
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)
            y, area, bmp = self._grab_and_locate()
            t = time.monotonic() - t0
            self.points.append((t, y, area, bmp))
            # v36：把每帧的检测明细也打出来 —— 只报 y 无法判断检测器到底
            # 看中了画面哪一块，之前只能靠猜。
            self.log("标定采样 %02d/%02d t=%.1fs y=%s area=%s 检测=%s",
                     i + 1, self.samples, t, y, area,
                     getattr(self, "_last_detect", "-"))
            # v36：无条件存「全屏 + 区域」两图（不再依赖 --debug）
            hook = getattr(self, "_frame_dump", None)
            if hook is not None:
                try:
                    hook("calib", i + 1)
                except Exception as e:
                    self.log("存帧异常 %s", e)
            elif self.dump and bmp is not None:
                try:
                    self.dump(bmp, "calib_s%02d" % (i + 1))
                except Exception:
                    pass
        return self.analyze()

    # ---- 分析采样结果 ----
    def analyze(self):
        """从采样点里算出建议阈值。数据不足返回 None。"""
        seq = [(t, y, a) for (t, y, a, _b) in self.points if y is not None]
        if len(seq) < 5:
            self.log("标定失败: 有效采样只有 %d 个（至少 5 个），沿用默认阈值", len(seq))
            return None

        ys = [y for (_t, y, _a) in seq]
        areas = [a for (_t, _y, a) in seq]

        # 逐步位移，剔除「面积骤变」的帧对（目标丢失/被水花打散，不是下沉）
        jumps = []
        for i in range(1, len(seq)):
            a0, a1 = seq[i - 1][2] or 0, seq[i][2] or 0
            if a0 and a1:
                ratio = max(a0, a1) / float(min(a0, a1))
                if ratio > 2.5:
                    self.log("标定: 跳过 t=%.1fs 的跳变（面积 %.0f→%.0f，疑似目标丢失）",
                             seq[i][0], a0, a1)
                    continue
            d = abs(seq[i][1] - seq[i - 1][1])
            jumps.append((d, seq[i][0]))

        if not jumps:
            self.log("标定失败: 没有可用的位移样本，沿用默认阈值")
            return None

        jumps.sort(reverse=True)
        max_jump, max_t = jumps[0]
        med = float(np.median([d for d, _t in jumps]))
        p90 = float(np.percentile([d for d, _t in jumps], 90))

        # 建议阈值。标定逻辑（实测数据支撑）：
        #   · 波纹浮动幅 3~4px 时，阈值 6 起误判为 0；阈值 4~5 会误判
        #   · 阈值 12 会漏掉 10px 的小幅真咬钩
        #   → 安全区在 6~10。取「浮动上限 + 余量」和这个区间求交。
        #
        # 浮动上限用中位数和 p90 估计：常规波纹是大多数帧的位移，
        # 真咬钩是极少数突变（会被 p90 部分包含，所以 p90 要打折扣）。
        ripple = max(med * 1.5, p90 * 0.5)
        suggested = int(round(max(ripple + 2, 6)))     # 至少 6
        if max_jump and max_jump >= 10:
            # 有明确的突变样本，阈值不越过它的一半，否则真咬钩会漏
            suggested = min(suggested, max(int(round(max_jump * 0.6)), 6))
        suggested = min(suggested, 10)                 # 上限 10，保小幅度咬钩
        suggested = max(suggested, 6)                  # 下限 6，保不误判

        self.log("标定结果: 采样 %d 点 | y 范围 %d~%d（跨度 %d）| 相邻位移 中位 %.1f p90 %.1f 最大 %d@t=%.1fs",
                 len(seq), min(ys), max(ys), max(ys) - min(ys),
                 med, p90, max_jump, max_t)
        self.log("标定建议: strike_value = %d（浮动估计 %.1f，安全区 6~10）",
                 suggested, ripple)
        return suggested
