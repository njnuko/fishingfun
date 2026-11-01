"""收竿结果判定 —— 读屏幕中上方「没有鱼上钩」游戏系统提示

用户实测（2026-11-01）：
    钓鱼时只要在鱼漂跳动的正确时机点，每次都能钓到；
    时机不对（收竿过早）游戏会在**固定位置**弹出黄字「没有鱼上钩」。

这给了机器人一个真实的地面判据，比靠锚点 yDiff 猜测可靠得多：
    - 右键收竿后 1.0 ~ 1.6 秒内，屏幕中上方出现「没有鱼上钩」→ 本竿误判，实际没鱼
    - 不出现 → 咬钩时机正确，真的钓到了

为什么不用 OCR：
    用户机器只有 python 标准库 + numpy + mss。这行字颜色是纯 (255,255,0)，
    判定「有多少个纯黄像素 + 横向跨度」就足够，不需要认字。

本机实测坐标（游戏客户区，分辨率无关的判定方式见 _locate_band）：
    提示行位于屏幕中上方、水平中线偏右，
    x ≈ 1214..1345（宽约 131px），y ≈ 256..280（高约 24px），全屏 2560x1599。
    文字左边缘 x/总宽 ≈ 0.474，右边缘 ≈ 0.525，垂直 y/总高 ≈ 0.160~0.175。

判定口径（不依赖绝对分辨率）：
    1. 取屏幕上半部分中间 30% 宽度的条带
    2. 统计纯黄像素 (r>230, g>210, b<90)
    3. 若某一行黄像素数量 ≥ MIN_PX_PER_ROW，累计该行
    4. 命中行数 ≥ MIN_ROWS 且整体横向跨度 ≥ MIN_SPAN → 判定为「没有鱼上钩」
"""

import time

try:
    import numpy as np
except ImportError:          # pragma: no cover
    np = None


# 纯黄阈值：(255,255,0) 是游戏系统提示的标准色
_PURE_YELLOW_R = 230
_PURE_YELLOW_G = 210
_PURE_YELLOW_B = 90

# 判定参数（按 2560 宽的屏幕标定，内部按实际宽度等比缩放）
MIN_SPAN_RATIO = 0.030      # 横向跨度 ≥ 屏宽 3.0%（2560 → 77px；实测 131px）
MIN_ROWS = 8                # v19: 12→8，抗锯齿/半透明背景下行数会少
MIN_PX_PER_ROW = 6          # v19: 12→6，理由同上

# v19 关键修复：黄字在全屏的固定位置（2560x1599 实测）
#   x ≈ 1214..1345（宽 131px），y ≈ 256..280（高 24px）
# 用「全屏比例」描述，实际截屏由 mss 直接按全屏坐标抓，不再依赖 search_area。
_Y_RATIO = (0.10, 0.30)     # 覆盖 0.160~0.175，上下留足余量
_X_RATIO = (0.36, 0.62)     # 覆盖 0.474~0.525，留足余量


class NoBiteDetector:
    """检测屏幕中上方的「没有鱼上钩」系统提示。"""

    def __init__(self, screen, log=None):
        """screen: 需要提供 grab() -> numpy 数组 (H,W,3) 的对象。"""
        self.screen = screen
        self.log = log or (lambda *a: None)
        self.last_seen_at = 0.0

    # ------------------------------------------------------------------
    def _grab_region(self):
        """按**全屏坐标**截取「中上部条带」，返回 (sub, ox, oy)。

        v19 修复：旧版直接对 screen.grab() 拿到的小图按比例切，但小图其实是
        浮标搜索区（约 1280x638 @ 640,409），切出来的 y 落在全屏 473~600，
        而黄字在 y 216~240 → 永远扫不到（v18 实测 46 次 px 全 0）。

        现在直接问 screen 要全屏尺寸，再用 mss 抓绝对区域，彻底解耦。
        找不到 mss/screen 时返回 (None, 0, 0)。
        """
        if np is None:
            return None, 0, 0

        sw, sh = self._full_screen_size()
        if not sw or not sh:
            return None, 0, 0

        x0 = int(sw * _X_RATIO[0])
        x1 = int(sw * _X_RATIO[1])
        y0 = int(sh * _Y_RATIO[0])
        y1 = int(sh * _Y_RATIO[1])
        # 客户端有顶栏偏移时，纵向整体下移（黄字位置按客户区量得）
        off = getattr(self, "client_offset", 0) or 0
        y0 += off
        y1 += off
        w = max(x1 - x0, 10)
        h = max(y1 - y0, 10)
        if y1 > sh:
            h = max(sh - y0, 10)
        self._last_region = (x0, y0, w, h)

        # 优先用 screen 自己的全屏截图（便于离线测试注入假画面）
        full = None
        for name in ("grab_full", "grab"):
            fn = getattr(self.screen, name, None)
            if fn is None:
                continue
            try:
                img = fn()
            except Exception:
                continue
            if img is None or not hasattr(img, "shape"):
                continue
            # 只接受接近全屏尺寸的图，避免拿到 search_area 小图
            if img.shape[1] >= sw * 0.9 and img.shape[0] >= sh * 0.5:
                full = img
                break

        if full is not None:
            sub = full[y0:y0 + h, x0:x0 + w]
            if sub.size:
                return np.ascontiguousarray(sub), x0, y0

        try:
            import mss
            with mss.mss() as sct:
                raw = sct.grab({"left": x0, "top": y0, "width": w, "height": h})
            arr = np.frombuffer(bytes(raw.raw), np.uint8)
            sub = arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1]   # BGRA → RGB
            return np.ascontiguousarray(sub), x0, y0
        except Exception as e:
            self.log("黄字截屏失败: %s", e)
            return None, 0, 0

    def _full_screen_size(self):
        """取全屏逻辑尺寸（优先 screen.screen_size + 客户端偏移）。"""
        w = h = 0
        try:
            sz = self.screen.screen_size()
            if sz and len(sz) == 2:
                w, h = int(sz[0]), int(sz[1])
        except Exception:
            pass
        if w and h:
            return w, h
        try:
            import mss
            with mss.mss() as sct:
                m = sct.monitors[1]
                return int(m["width"]), int(m["height"])
        except Exception:
            return 0, 0

    # ------------------------------------------------------------------
    def check(self):
        """检查当前屏幕是否有「没有鱼上钩」。

        返回 dict:
            hit        : bool 是否命中
            rows       : int  命中行数
            span       : int  横向跨度(px)
            px         : int  黄色像素总数
            box        : (x0,y0,x1,y1) 命中的全屏范围，未命中为 None
        """
        res = {"hit": False, "rows": 0, "span": 0, "px": 0, "box": None}
        sub, ox, oy = self._grab_region()
        if sub is None or sub.size == 0:
            return res

        r = sub[:, :, 0].astype(np.int16)
        g = sub[:, :, 1].astype(np.int16)
        b = sub[:, :, 2].astype(np.int16)
        mask = (r > _PURE_YELLOW_R) & (g > _PURE_YELLOW_G) & (b < _PURE_YELLOW_B)

        total = int(mask.sum())
        res["px"] = total
        if total < MIN_PX_PER_ROW:
            return res

        row_counts = mask.sum(axis=1)
        hit_rows = np.where(row_counts >= MIN_PX_PER_ROW)[0]
        res["rows"] = int(len(hit_rows))
        if len(hit_rows) < MIN_ROWS:
            return res

        # 横向跨度：只看命中的行
        cols = np.where(mask[hit_rows, :].any(axis=0))[0]
        if len(cols) == 0:
            return res
        span = int(cols[-1] - cols[0] + 1)
        res["span"] = span

        # v19: 子图宽 = 全屏宽 × (_X_RATIO[1]-_X_RATIO[0])，按比例反推全屏宽
        span_ratio = _X_RATIO[1] - _X_RATIO[0]
        full_w = sub.shape[1] / span_ratio if span_ratio > 0 else sub.shape[1]
        if span < int(full_w * MIN_SPAN_RATIO):
            return res

        res["hit"] = True
        res["box"] = (ox + int(cols[0]), oy + int(hit_rows[0]),
                      ox + int(cols[-1]), oy + int(hit_rows[-1]))
        self.last_seen_at = time.monotonic()
        return res

    # ------------------------------------------------------------------
    def confirm_no_bite(self, timeout=5.0, poll=0.10):
        """收竿后等待并确认是否出现「没有鱼上钩」。

        v19：窗口 1.5s → 5.0s。
        原因：用户实测黄字约 1 秒才弹出、持续约 4 秒消失。旧 1.5s 窗口从右键
        起算太短，且右键后还要等拾取动作，实际开扫往往已在 2~3 秒后，
        正好错过。5.0s 覆盖「弹出 + 持续」全过程。
        """
        deadline = time.monotonic() + timeout
        best = None
        while time.monotonic() < deadline:
            res = self.check()
            if res["hit"]:
                return True, res
            if best is None or res["px"] > best["px"]:
                best = res
            time.sleep(poll)
        return False, best or {"hit": False, "rows": 0, "span": 0, "px": 0, "box": None}

    # ------------------------------------------------------------------
    def wait_clear(self, timeout=4.5, poll=0.2):
        """等待屏幕上的「没有鱼上钩」消失（约 4 秒后自动消失）。

        用于误判后重新抛竿前，避免下一竿的判定读到上一竿的残留提示。
        返回 True = 已清空，False = 超时仍在。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.check()["hit"]:
                return True
            time.sleep(poll)
        return False
