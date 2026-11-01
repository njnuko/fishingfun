"""截屏 —— 对应 Source/FishingFunBot/Platform/WowScreen.cs（26 行）

原版：
    bmp = new Bitmap(W/2, H/2 - 100)
    CopyFromScreen(W/4, H/4, 0, 0, bmp.Size)
    GetScreenPositionFromBitmapPostion: pos += (W/4, H/4)

适配点（不改比例，只换基准坐标系）：
  - 原版基准是 Screen.PrimaryScreen（前提：WoW 独占全屏）
  - 这里基准换成「游戏窗口客户区」，独占全屏时两者完全等价
  - 必须先声明 DPI 感知，否则 Windows 175% 缩放下拿到的是逻辑像素（1463x914）
"""

import ctypes

try:
    import numpy as np
except ImportError:
    np = None


def enable_dpi_awareness():
    """必须在取任何屏幕尺寸之前调用。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor v2
        return True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False


def _user32():
    return ctypes.windll.user32


WOW_TITLES = ("World of Warcraft", "魔兽世界", "Wow")


def find_wow_window():
    """返回 (hwnd, left, top, w, h) 客户区；找不到返回 None。"""
    u = _user32()
    results = []

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if not any(t.lower() in title.lower() for t in WOW_TITLES):
            return True
        rect = (ctypes.c_long * 4)()
        u.GetClientRect(hwnd, rect)
        cw, ch = rect[2] - rect[0], rect[3] - rect[1]
        if cw < 200 or ch < 200:
            return True
        pt = (ctypes.c_long * 2)()
        u.ClientToScreen(hwnd, pt)
        results.append((hwnd, pt[0], pt[1], cw, ch))
        return True

    cb = EnumProc(_cb)
    u.EnumWindows(cb, 0)
    if not results:
        return None
    return max(results, key=lambda r: r[3] * r[4])


class WowScreen:
    """对应 C# 的 static class WowScreen"""

    def __init__(self, region=None, log=None):
        self.region = region        # (x0, y0, x1, y1) 归一化比例，可选
        self.log = log or (lambda *a, **k: None)
        self._hwnd = None
        self._area_cache = None     # 锁定的截屏区，测一次就不再变
        self._area_armed = False    # 只有抛过竿（绿条出现）才允许测边界并锁死

    def _client_rect(self):
        found = find_wow_window()
        if found:
            self._hwnd = found[0]
            return found[1:]
        u = _user32()
        w = u.GetSystemMetrics(0)
        h = u.GetSystemMetrics(1)
        return (0, 0, w, h)

    def describe(self):
        left, top, w, h = self._client_rect()
        src = "游戏窗口" if self._hwnd else "全屏"
        l, t, cw, ch = self.search_area()
        return (f"截取区域: 来源={src} 客户区={w}x{h}@{left},{top} "
                f"截图={cw}x{ch}@{l},{t}")

    # 逐行对应 WowScreen.cs:13-20
    def search_area(self):
        """截屏区：上边 = 实时检测的水面/天空分界线，左右下边按比例。

        原版写死 (w//4, h//4, w//2, h//2-100)，前提是「地平线恰好在 h//4」。
        实测（2560x1600 冰冻之海）地平线在 y≈356，而 h//4=400 —— 地平线
        永久落在截屏区外，检测器只能在水面里找跳变，报出 y=226 的噪声。
        所以上边必须由画面本身决定，不能写死。

        上边：扫第 0 行到 h/2 的逐行平均亮度，取「上亮下暗」的最大跳变行；
              置信度不足（水面反光、镜头贴水）时退回 h//4 原版值。
        下边：h//2+100。动作条实测在 y≈1150，h//2+100=900 安全在上方。
        左右：w//4 起，宽 w//2（原版）。
        """
        left, top, w, h = self._client_rect()
        if self.region is not None:
            x0, y0, x1, y1 = self.region
            l = left + int(w * x0)
            t = top + int(h * y0)
            cw = max(int(w * (x1 - x0)), 50)
            ch = max(int(h * (y1 - y0)), 50)
            return (l, t, cw, ch)

        # 一旦测好就锁死。按用户设计：边界在**第一次抛竿后**测一次就定下来，
        # 之后跟浮标位置一样不再变。每帧重测会让截屏高度随画面内容跳动
        # （绿条出现/消失、水面波动），差分标定和序列定位都要求帧尺寸一致，
        # 尺寸一变全部失效 —— 这就是「抓不到浮标」的根因。
        cached = self._area_cache
        if cached is not None and cached[0] == (left, top, w, h):
            return cached[1]

        if not self._area_armed:
            # 还没抛过竿：绿条/浮标都不在画面里，此刻测边界必错（实测会测到
            # 1280x172 这种把水面砍掉一大半的值，然后锁死）。用兜底值先跑。
            t0 = top + h // 4
            b0 = top + h // 2 + 100
            return (left + w // 4, t0, w // 2, max(b0 - t0, 100))

        ny = self._detect_horizon(top, h)          # 地平线在客户区里的 y
        by = self._detect_bottom(top, h)           # 钓鱼进度条上沿
        if by is None:
            by = self._detect_actionbar(top, h)     # 退：动作条顶边
        if ny is None:
            ny = top + h // 4                       # 测不出 → 回原版
            self.log("⚠️  地平线未测出，上边界回退到 h/4（水面占屏太少时正常）")
        if by is None:
            by = top + h // 2 + 100
            self.log("⚠️  下边界未测出（绿条/动作条都没有），回退到 h/2+100")
        # 上边留 10px 余量，免得地平线正好压在边界上被切掉。
        # ⚠️ 这里不设「离顶至少 N 行」的硬下限：真地平线判出来是多少就按多少，
        #   多一重门槛反而会误杀（假地平线已在 _detect_horizon 内用梯度方向拦掉）。
        ny = max(top, ny - 10)
        # 下边界必须在地平线之下，否则说明测错了（把天空边界当成绿条），
        # 这时退回动作条，再不行用兜底值 —— 绝不让截屏区塌成 100 高。
        if by is None or by <= ny + 200:
            ab = self._detect_actionbar(top, h)
            if ab is not None and ab > ny + 200:
                by = ab
                self.log("下边界绿条结果不可用，改用动作条 y=%d", int(by))
            else:
                by = top + h // 2 + 100
                self.log("下边界测不出，回退到 h/2+100")
        ch = max(by - ny, 100)
        area = (left + w // 4, ny, w // 2, ch)
        self._area_cache = ((left, top, w, h), area)
        return area

    def invalidate_area(self):
        """画面大改（换钓点/镜头切换）后需要重测边界时调用。"""
        self._area_cache = None
        self._area_armed = False

    def arm_area(self):
        """首竿之后调用：放开边界检测，量一次并永久锁定。返回锁定后的区域。"""
        self._area_cache = None
        self._area_armed = True
        return self.search_area()

    def _row_lum(self, top, y_from, y_to, band_w_ratio=0.25):
        """抓一条水平居中的竖带，返回逐行平均亮度（numpy 一维）。失败 None。"""
        import mss
        left, _t, w, _hh = self._client_rect()
        band_w = max(int(w * band_w_ratio), 40)
        band_x = left + (w - band_w) // 2
        hgt = max(int(y_to - y_from), 10)
        try:
            with mss.mss() as sct:
                raw = sct.grab({"left": band_x, "top": int(y_from),
                                "width": band_w, "height": hgt})
            arr = np.frombuffer(bytes(raw.raw), np.uint8)
            arr = arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1]
        except Exception:
            return None
        return arr[:, :, :3].astype(np.float32).mean(axis=2).mean(axis=1)

    def _detect_bottom(self, top, h):
        """找钓鱼进度条（绿色条纹）上沿，返回绝对 y。找不到返回 None。

        用户指定：下边界 = 第一次抛竿时那条绿色进度条的上方。

        判据（进度条特征很明显，不易误认）：
          1. 绿色像素：G>60 且 G-R>15 且 G-B>15
          2. 连成一段高度 15~60 行的横条
          3. 横条宽度 >= 画面 1/3，且整条填充率 >= 0.10（实心，不是零散绿点）
          4. 位于画面下半屏（y > 55%）

        实测（2560x1599 冰冻之海）：进度条在 y=1155~1190，高 36、宽 1412、
        填充率 0.22。干扰项（钓鱼技能条 121~134 只有 300 宽；水反射的绿
        548~649 填充率仅 0.04）都被规则 3 排除。

        找不到时退回动作条顶边检测。
        """
        band_top = top + int(h * 0.55)
        row_img = self._grab_band(top, band_top, top + h)
        if row_img is None:
            return None
        G = row_img[:, :, 1].astype(np.int16)
        R = row_img[:, :, 0].astype(np.int16)
        B = row_img[:, :, 2].astype(np.int16)
        green = (G > 60) & (G - R > 15) & (G - B > 15)
        rows = green.sum(axis=1)
        w_img = green.shape[1]
        # 钓鱼进度条紧贴动作条上方，位置固定在 60%~80% 屏高之间
        y_lo = int(h * 0.60) - int(h * 0.55)      # 相对 band_top 的偏移
        y_hi = int(h * 0.80) - int(h * 0.55)
        y = max(0, y_lo)
        n = min(len(rows), y_hi)
        # 找连续绿色行段，取最上面合格的那个（越靠上越可能是钓鱼条）
        while y < n:
            if rows[y] < w_img * 0.05:
                y += 1
                continue
            y2 = y
            while y2 < n and rows[y2] >= w_img * 0.05:
                y2 += 1
            seg_h = y2 - y
            if 15 <= seg_h <= 60:
                band = green[y:y2]
                cols = band.sum(axis=0)
                xs = np.where(cols > 0)[0]
                if len(xs) > 0:
                    width = xs.max() - xs.min() + 1
                    fill = band.sum() / float(seg_h * width)
                    if width >= w_img / 3.0 and fill >= 0.10:
                        return band_top + y          # 绿条上沿
            y = y2
        return None

    def _grab_band(self, top, y_from, y_to):
        """抓全宽、指定纵向范围的图（RGB uint8）。失败 None。"""
        import mss
        left, _t, w, _hh = self._client_rect()
        hgt = max(int(y_to - y_from), 10)
        try:
            with mss.mss() as sct:
                raw = sct.grab({"left": left, "top": int(y_from),
                                "width": w, "height": hgt})
            arr = np.frombuffer(bytes(raw.raw), np.uint8)
            arr = arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1]
        except Exception:
            return None
        return np.ascontiguousarray(arr)

    def _detect_actionbar(self, top, h):
        row = self._row_lum(top, top + h // 2, top + h)
        if row is None or len(row) < 200:
            return None
        base = top + h // 2
        lum_thr = 60.0
        run_need = 60          # 水面至少要持续 60 行
        # 从上半段开始找第一段「连续低亮度」的结束位置
        run = 0
        start = -1
        for y in range(len(row)):
            if row[y] < lum_thr:
                if run == 0:
                    start = y
                run += 1
            else:
                if run >= run_need:
                    # 低位段结束 → 判断这里是不是动作条顶边
                    if base + y >= top + h * 0.60:
                        return base + y
                run = 0
        return None

    def _detect_horizon(self, top, h):
        """在客户区上半屏找水面/天空分界线。返回绝对 y，失败返回 None。

        判据（三项同时成立才算地平线）：
          1. 取画面水平中间 1/4 宽的一条竖带（避开左右两侧 UI/图标）
          2. 该行是「上亮下暗」的下降沿，落差 >= 12
          3. 落差之后 60 行内，上方均值比下方均值亮至少 15（真·明暗分界）

        只按「最大落差」是不行的 —— 实测截图顶部 UI 条结束处落差 90，
        比真地平线（落差 20）大得多，会被误判成地平线。加第 3 条就能
        滤掉 UI 条（UI 条下方紧接着可能还是亮的游戏画面）。
        """
        left = self._client_rect()[0]
        y_from = top
        y_to = top + max(h // 2, 40)
        row = self._row_lum(top, y_from, y_to, band_w_ratio=0.25)
        if row is None:
            return None
        n = len(row)
        if n < 60:
            return None
        best_d, best_y = 0.0, -1
        span = 60
        for y in range(20, n - span):
            d = row[y - 1] - row[y]
            if d < 12:
                continue
            up = row[max(0, y - span):y].mean()
            dn = row[y:y + span].mean()
            # 3. 上方必须明显比下方亮
            if up - dn < 15:
                continue
            score = d * (up - dn)
            if score > best_d:
                best_d, best_y = score, y
        if best_y < 0:
            return None
        # ★ 不要用「离顶部多少行」判假地平线：实测真地平线常常就落在截屏区
        #   局部 y=9（截屏区上边界本就贴着天空分界线切），按行号门槛会误杀真值
        #   （实测把 y=111 置信度 74.7x 的正确结果改成 y=145 置信度 6.3x）。
        #   要区分的是梯度方向：天空→水面 = 上亮下暗。若检测到的位置是
        #   上暗下亮（UI 条结束/黑边结束），才判为假象并丢弃。
        prev_lum = float(row[max(0, best_y - 3):best_y].mean())
        next_lum = float(row[best_y:best_y + 4].mean())
        if next_lum > prev_lum:
            self.log("⚠️  地平线候选 y=%d 方向为上暗下亮（%.0f→%.0f），"
                     "判为 UI/黑边交界而非天空→水面分界，丢弃",
                     y_from + best_y, prev_lum, next_lum)
            return None
        return y_from + best_y

    def grab(self):
        """返回 HxWx3 RGB uint8。对应 C# GetBitmap()"""
        import mss
        l, t, cw, ch = self.search_area()
        with mss.mss() as sct:
            raw = sct.grab({"left": l, "top": t, "width": cw, "height": ch})
        arr = np.frombuffer(bytes(raw.raw), np.uint8)
        arr = arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1]   # BGRA → RGB
        return np.ascontiguousarray(arr)

    # 逐行对应 WowScreen.cs:22-25
    def to_screen(self, x, y):
        l, t, _cw, _ch = self.search_area()
        return (int(x) + l, int(y) + t)

    def screen_size(self):
        left, top, w, h = self._client_rect()
        return (w, h)


class FakeGrabber:
    """离线测试用：注入固定画面，to_screen 只加偏移。"""

    def __init__(self, frames, origin=(0, 0)):
        self.frames = list(frames)
        self.origin = origin
        self.clicks = []

    def grab(self):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]

    def to_screen(self, x, y):
        return (int(x) + self.origin[0], int(y) + self.origin[1])

    def screen_size(self):
        f = self.frames[0]
        return (f.shape[1], f.shape[0])

    def describe(self):
        f = self.frames[0]
        return f"截取区域: Fake {f.shape[1]}x{f.shape[0]}"
