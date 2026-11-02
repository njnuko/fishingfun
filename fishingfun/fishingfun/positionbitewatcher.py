"""咬钩判定 —— 严格复刻 Source/FishingFunBot/Bot/PositionBiteWatcher.cs（71 行）

⚠️ 原版逻辑（legacy 模式，默认）没有缺陷，不要改：
   yPositions 只增不删、只去重、每次排序；
   基准下标 = int((Count + 0.5) / 2)；
   yDiff = 基准 - 当前Y；判定 yDiff <= -strikeValue。

非原版增强（robust 模式，需显式开启）：
   原版按 1366 宽画面标定，鱼漂高 30+px，下沉 14px 才触发。
   高分辨率窗口缩小后鱼漂只剩 8~10px，中位数机制会把真实信号淹没、
   且单帧检测噪声（水面反光/杂物）会制造伪峰值。故 robust 模式改为：
     · 滑动窗口取最近 N 帧的最小 y 作为「水面基准」（浮漂最高点）
     · 连续 M 帧满足 (y - 水面基准) >= strike 才判定咬钩
     · 连续 D 帧检测不到浮标也判定咬钩（鱼把漂拖进水下）
   三个参数都可调，默认 N=30 / M=3 / D=4。
"""

from collections import deque

from .timedaction import TimedAction


def _median(seq):
    """纯 python 中位数（不引 numpy，保持零依赖）。"""
    s = sorted(seq)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


class PositionBiteWatcher:
    def __init__(self, strike_value=7, mode="legacy",
                 window=30, confirm_frames=3, vanish_frames=4):
        self.strike_value = strike_value
        self.mode = mode                    # "legacy"（原版） | "robust"
        self.window = window
        self.confirm_frames = confirm_frames
        self.vanish_frames = vanish_frames

        self.y_positions = []               # legacy 用
        self.y_diff = 0

        # robust 用
        self._recent = deque(maxlen=window)
        self._streak = 0                    # 连续超过阈值的帧数
        self._vanish = 0                    # 连续未检测到浮标的帧数

        # ★ v44：每竿重新锚定模式。见 _is_bite_robust 里的说明。
        #   per_cast_anchor_mode=False 时行为与 v43 完全一致（可回退）。
        self.per_cast_anchor_mode = False
        self.per_cast_anchor_y = None          # 本竿第一帧的 y（每竿重置）
        self.per_cast_anchor_xy = None         # ★ v45：本竿锚点 (x,y)，便于日志对号入座
        self.log = lambda *a, **k: None     # 由 FishBot 注入

        # ★ v45：逐帧明细日志（调试用，输出量很大，默认关闭）。
        #   开启后每帧一行：t / 检测坐标 / 当前 y / 相对锚点下沉值 / 阈值。
        #   用户要求「每一秒的标定值都进日志」，靠这一项复盘浮标怎么动的。
        self.frame_log = False

        # ★ v37：抖动自适应阈值
        #   实测（2560x1600 真图 16 帧）：平静期 y 抖动只有 1px 下沉，
        #   但换钓点/换分辨率后会变。原固定 strike=4 一旦低于真实抖动就
        #   满屏误判（历史上 43/46 次误触发就是这么来的）。
        #   策略：记录「判定为平静」时的下沉幅度，取 P90 作为噪声线，
        #   实际阈值 = max(配置 strike, 噪声线 + 2)。只会抬高，不会降低。
        self._noise_samples = deque(maxlen=120)
        self.effective_strike = int(strike_value)
        self.auto_strike = True             # 可用 --no-auto-strike 关闭

        self.timer = None
        self.fishing_event_handler = lambda e: None

    def raise_event(self, ev):
        try:
            self.fishing_event_handler(ev)
        except Exception:
            pass

    def reset(self, initial_bobber_position):
        self.raise_event({"Action": "Reset"})
        self.y_positions = []
        self.y_positions.append(int(initial_bobber_position[1]))

        self._recent.clear()
        self._recent.append(int(initial_bobber_position[1]))
        self._streak = 0
        self._vanish = 0
        self._noise_samples.clear()      # v37：每竿重新学抖动
        self.effective_strike = int(self.strike_value)
        self._last_delta = None          # v37：连续下沉判据的上帧值
        # ★ v44：每竿清空锚点 —— 锚点必须是「本竿第一帧」的 y，跨竿复用必错
        self.per_cast_anchor_y = None
        self.per_cast_anchor_xy = None

        self.timer = TimedAction(
            lambda a: self.raise_event({"Amplitude": self.y_diff, "Action": "BobberMove"}),
            500, 25)

    def _fire(self):
        self.raise_event({"Action": "Loot"})
        if self.timer is not None:
            self.timer.execute_now()
        return True

    # ---- 原版逐行对应 PositionBiteWatcher.cs:42-70 ----
    def is_bite(self, current_bobber_position):
        if self.mode == "robust":
            return self._is_bite_robust(current_bobber_position)

        y = int(current_bobber_position[1])

        if y not in self.y_positions:
            self.y_positions.append(y)
            self.y_positions.sort()

        idx = int((len(self.y_positions) + 0.5) / 2)
        if idx >= len(self.y_positions):
            idx = len(self.y_positions) - 1
        self.y_diff = self.y_positions[idx] - y

        threshold_reached = self.y_diff <= -self.strike_value

        if self.timer is not None:
            self.timer.execute_if_due()

        if threshold_reached:
            return self._fire()
        return False

    # ---- 非原版：稳定期只喂基准，不做任何判定 ----
    def observe(self, y):
        self._recent.append(int(y))
        self.y_diff = 0
        self._streak = 0
        self._vanish = 0

    def _update_strike(self, delta):
        """★ v37：根据实测抖动自适应抬高咬钩阈值（只会抬高，不会降低）。

        为什么要它：原 `strike=4` 是按某一次实测定的固定值。换钓点、换
        分辨率、换主机后真实抖动一变，固定值要么太低（满屏误判）、要么
        太高（漏掉真咬钩）。历史日志里 43/46 次误触发全是「yDiff=-4 一分
        不多一分不少」，正是固定阈值卡在真实抖动线上的典型症状。

        做法：把每一帧的下沉量记入噪声样本，取 P90 当噪声线，
        有效阈值 = max(配置值, 噪声线 + 2)。真咬钩的大下沉（≥ 当前阈值）
        不计入噪声样本，避免自己把门槛抬到天上去。
        """
        if not getattr(self, "auto_strike", True):
            self.effective_strike = int(self.strike_value)
            return self.effective_strike

        base = int(self.strike_value)
        # v37-b：噪声样本必须**无条件**收集当前帧下沉量。
        # 早期版本用 `if delta < base` 过滤，结果 ±5px 抖动环境下
        # delta=5 的帧全被排除，P90 永远算不到 5，阈值死守在 4，
        # 实测 60 帧里误触发 19 次。现在改为全收，靠 P90 自己抗离群。
        if delta > 0:
            self._noise_samples.append(int(delta))

        n = len(self._noise_samples)
        if n < 8:                       # 样本不足时先用配置值（8 帧即可起步）
            self.effective_strike = base
            return base

        s = sorted(self._noise_samples)
        p90 = s[min(n - 1, int(n * 0.9))]
        # 噪声线 +2 作为安全余量；绝不低于配置值，封顶 12 防止漏真咬钩
        eff = max(base, p90 + 2)
        eff = min(eff, 12)
        self.effective_strike = int(eff)
        return self.effective_strike

    # ---- 非原版增强：抗噪声 / 抗漏检 ----
    def _is_bite_robust(self, current_bobber_position):
        # 浮标消失：连续多帧检测不到 → 被鱼拖入水下。
        # ⚠️ 这条路径曾是最大误判源：目标稍一晃就丢一帧，单帧丢失
        # 被当成咬钩（43/46 次触发都是「浮标消失」且计数只有 0~1）。
        # 现在默认要求连续 8 帧（约 2 秒）都消失才算，短暂丢失只重置强度。
        if current_bobber_position is None:
            self._vanish += 1
            self._streak = 0
            if self.vanish_frames and self._vanish >= self.vanish_frames:
                self.y_diff = 0
                return self._fire()
            return False

        self._vanish = 0
        y = int(current_bobber_position[1])

        # ★ v44：每竿重新锚定（用户指定的判定逻辑）。
        #   「每一竿，第一帧，根据浮标特征确定锚点；后面每一帧，判断浮标
        #    和锚点的上下浮动差值，≥ 第一竿标定的咬钩特征值就判咬钩。」
        #
        #   ⚠️ 锚点是「本竿」的浮标位置，不是跨竿固定坐标：
        #      每抛一竿浮标落点都不同（实测 21:24 那竿落在局部 (608,236)），
        #      所以 reset() 每次都清空 anchor_y，用本竿第一帧的 y 重新定。
        #      跨竿复用上一竿的坐标必然全错。
        #
        #   与下面的 v37 自适应基线互斥：那种基线取「近 N 帧中位数」，
        #   会随水面/浮标漂移不断移动，慢慢漂远时基准跟着跑，真咬钩的
        #   下沉量被抵消掉。这里整竿只有一个参照点。
        #   y_diff 语义与原版一致：负数 = 下沉，正数 = 上浮。
        if getattr(self, "per_cast_anchor_mode", False):
            if self.per_cast_anchor_y is None:
                self.per_cast_anchor_y = y
                self.per_cast_anchor_xy = (int(current_bobber_position[0]), y)
                self.log("本竿锚点: (x=%d, y=%d)（本竿第一帧，后续整竿以此为参照，"
                         "阈值=%dpx）",
                         int(current_bobber_position[0]), y,
                         int(self.strike_value))
            self.y_diff = self.per_cast_anchor_y - y
            # ★ v45：逐帧明细。用户要「每帧/每秒的浮动值」都能在日志里查到，
            #   否则出现误判时无从复现。只在 frame_log 开启时输出。
            _eff = max(1, int(self.strike_value))
            if getattr(self, "frame_log", False):
                self.log("浮动值: t帧 y=%d 锚点y=%d yDiff=%d 下沉=%dpx 阈值=%dpx 命中=%s",
                         y, self.per_cast_anchor_y, self.y_diff, -self.y_diff,
                         _eff,
                         "是" if -self.y_diff >= _eff else "否")
            # ★ v55：阈值兜底。strike_value=0 时 `-y_diff >= 0` 恒成立，
            #   第一帧就会「判咬钩」（重放脚本误传 0 时实测如此）。
            #   真机标定返回值有下限（p90+2 且 max 6），但配置错 / 标定失败
            #   兜底 / 外部脚本构造都可能给 0，这里统一按 1px 起算，
            #   宁可钝一点，也绝不空钩乱抬。
            _eff = max(1, int(self.strike_value))
            if -self.y_diff >= _eff:
                self._streak += 1
                self.log("锚点判定: 锚点 y=%d 当前 y=%d 下沉 %dpx ≥ 阈值 %d（连续 %d）",
                         self.per_cast_anchor_y, y, -self.y_diff,
                         _eff, self._streak)
                if self._streak >= max(1, int(getattr(self, "confirm_frames", 1))):
                    return self._fire()
            else:
                self._streak = 0
            return False

        # ★ v37：水位基线用「稳健上沿」而不是中位数。
        #   中位数会被水面的上下波动带跑：±5px 抖动时中位数也在 ±5px 里晃，
        #   delta 瞬间拉到 10px，把阈值抬到 12 也压不住（实测 60 帧误触发
        #   10 次）。真正稳定的参照是「水面静止时浮标顶点的 y」——浮标浮在
        #   水上，静止姿态对应 y 序列的**较小值**（越小越靠上）。
        #   取 P25 作为静止水位线，抖动被压到上四分位以外，不会被带动。
        if len(self._recent) >= 8:
            s = sorted(self._recent)
            self.surface_y = float(s[int(len(s) * 0.25)])
        elif self._recent:
            self.surface_y = float(_median(self._recent))
        else:
            self.surface_y = float(y)
        delta = y - self.surface_y             # 正数 = 下沉
        # 对外报告用负值表示下沉，保持与原版 yDiff 语义一致
        self.y_diff = -int(round(delta)) if delta > 0 else 0

        # ★ v37：自适应阈值。先把本帧下沉量记入噪声样本，再算有效阈值。
        thr = self._update_strike(delta)

        # ★ v37：真咬钩是「持续下沉」，水面抖动是「来回晃」。
        #   只有 delta 连续增长（后一帧比前一帧更深）才累积 streak，
        #   否则清零。这样 ±5px 的来回抖动即使单帧 delta 够大也不会累计。
        if delta >= thr:
            prev = getattr(self, "_last_delta", None)
            if prev is not None and delta > prev:
                self._streak += 1
            elif self._streak == 0:
                self._streak = 1          # 第一帧允许起头
            else:
                self._streak = 0          # 没继续下沉 → 是抖动
        else:
            self._streak = 0
        self._last_delta = delta

        # 基线慢速跟随（只在水位没在明显下沉时才更新），
        # 避免把真咬钩的那一下下沉吸收进基线里。
        if delta < thr * 0.5:
            self._recent.append(y)   # deque(maxlen=window) 自动丢最旧的

        if self.timer is not None:
            self.timer.execute_if_due()

        if self._streak >= self.confirm_frames:
            return self._fire()
        return False
