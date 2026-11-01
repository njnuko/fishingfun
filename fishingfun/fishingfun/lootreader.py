"""拾取后读聊天框的「你获得了物品：[xxx]」 —— 非原版增强（诊断/统计用）

原版 FishingFun 完全没有读取物品信息的逻辑，这里只是把游戏自己打出来的
系统提示读进日志，方便核对「这一竿到底有没有真拾取到东西」。

为什么不用 pytesseract / easyocr：
    用户机器上只有 python 标准库 + numpy + mss，装 OCR 依赖太重。
    这里直接把裁剪区二值化后当「点阵」做模板比对 —— 只认固定格式的一行字，
    不需要通用 OCR。

区域（本机 2560x1600 @175%，游戏客户区 2560x1476@0,40）：
    系统消息行 y≈1201..1236，文字从 x≈20 开始
    为了容错，抓 y=1195..1245, x=0..900 整条

判定方式（不依赖 OCR 字库）：
    1. 二值化取文字像素（亮度 > 90）
    2. 统计「行内有哪些列有文字」→ 得到一串列段（字块）的个数与总宽度
    3. 「你获得了物品」这行固定 6 个汉字 + 冒号 + 方括号 + 物品名
       → 字块数 ≥ 8 且首块宽约 20~26px（等于「你」的宽度）就算命中
    4. 命中后仍尝试 tesseract（若机器有装）把物品名读出来，读不到就记 (未识别)
"""

import ctypes
import os
import shutil
import subprocess
import tempfile

try:
    import numpy as np
except ImportError:          # pragma: no cover
    np = None


# 相对「游戏客户区」的固定像素区域（不随分辨率缩放，本机实测坐标）
# 2026-11-01 实测重定位：客户区 2560x1476，聊天框系统提示行共 3 行可见：
#   y 1065-1085 / 1165-1185 / 1215-1235，每行结构 = 黄图标 x13-34 + 绿字 x61-287
# 取 y 1050-1250 覆盖 3 行历史（留上下余量），x 0-450 覆盖图标+文字。
# ⚠️ 旧值 (0,1205,900,1245) 是按裁切图算的，在窗口模式下会超出截图高度。
CHAT_BOX = (0, 1050, 450, 1250)

_LUM_THRESHOLD = 85

# 「你获得了物品：[xxx]」这行的配色模板（本机 2560x1476 客户区实测）：
#   x13-34   黄色图标（RGB≈156,132,15）
#   x61-195  6 个绿色汉字「你获得了物品」（RGB≈0,166,1），每个宽 20~21
#   x207-210 绿色冒号
#   x222-278 灰色方括号 + 物品名（RGB≈143,145,145）
# 判定靠这个「黄图标 + 连续 6 个绿字块」结构，比字形模板稳。
_YELLOW = dict(min_r=110, min_g=90, max_b=80, delta=70)
_GREEN = dict(min_g=140, max_r=70, max_b=70)
_GRAY = dict(min_v=110, max_delta=25)


def _is_yellow(px):
    r, g, b = px
    return (r > _YELLOW["min_r"] and g > _YELLOW["min_g"]
            and b < _YELLOW["max_b"] and (r - b) > _YELLOW["delta"])


def _is_green(px):
    r, g, b = px
    return g > _GREEN["min_g"] and r < _GREEN["min_r"] and b < _GREEN["max_b"]


def _is_gray(px):
    r, g, b = px
    return (min(r, g, b) > _GRAY["min_v"]
            and max(r, g, b) - min(r, g, b) < _GRAY["max_delta"])


def _user32():
    return ctypes.windll.user32


class LootReader:
    """拾取后读一行系统消息。read() 返回 (是否命中物品行, 物品名或None)"""

    def __init__(self, box=CHAT_BOX, log=None):
        self.box = box                    # (x0, y0, x1, y1) 客户区坐标
        self.log = log or (lambda *a, **k: None)
        self._tess = shutil.which("tesseract")
        self._hwnd = None

    # ---------- 抓图 ----------
    def _client_origin(self):
        from .wowscreen import find_wow_window
        found = find_wow_window()
        if found:
            self._hwnd = found[0]
            return found[1], found[2]
        u = _user32()
        return 0, 0

    def grab(self):
        """返回 (H, W, 3) RGB uint8，区域已含窗口偏移"""
        import mss
        oleft, otop = self._client_origin()
        x0, y0, x1, y1 = self.box
        l, t = oleft + x0, otop + y0
        w, h = max(x1 - x0, 10), max(y1 - y0, 10)
        with mss.mss() as sct:
            raw = sct.grab({"left": l, "top": t, "width": w, "height": h})
        arr = np.frombuffer(bytes(raw.raw), np.uint8)
        return np.ascontiguousarray(arr.reshape(raw.height, raw.width, 4)[:, :, 2::-1])

    # ---------- 特征分析 ----------
    @staticmethod
    def _masks(rgb):
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)
        yellow = (r > 110) & (g > 90) & (b < 80) & ((r - b) > 70)
        green = (g > 140) & (r < 70) & (b < 70)
        lum = r * 0.299 + g * 0.587 + b * 0.114
        light = lum > _LUM_THRESHOLD
        return yellow, green, light

    @staticmethod
    def _blocks(mask, min_w=3, gap=1):
        """按列切字块，返回 [(x_start, x_end), ...]"""
        cols = mask.any(axis=0)
        idx = np.nonzero(cols)[0]
        if len(idx) == 0:
            return []
        segs = []
        start = prev = int(idx[0])
        for x in idx[1:]:
            x = int(x)
            if x - prev > gap:
                if prev - start + 1 >= min_w:
                    segs.append((start, prev))
                start = x
            prev = x
        if prev - start + 1 >= min_w:
            segs.append((start, prev))
        return segs

    @staticmethod
    def _row_bands(light, min_px=8, gap=2, line_h=32):
        """按行切出文字带，返回 [(y0, y1), ...]（相对区域顶部）

        ⚠️ 实测行距：一行汉字高约 21px，行间空 4 行（但汉字下伸笔画会占几行）。
        做法：先用小 gap 分粗段，再把高度明显超过 line_h 的粗段按固定步长切开，
        避免 gap 太大把整个区域粘成一段、gap 太小把两行粘成一段。
        """
        rows = np.nonzero(light.sum(axis=1) > min_px)[0]
        if len(rows) == 0:
            return []
        segs = []
        start = prev = int(rows[0])
        for y in rows[1:]:
            y = int(y)
            if y - prev > gap:
                segs.append((start, prev))
                start = y
            prev = y
        segs.append((start, prev))

        bands = []
        for (a, b) in segs:
            h = b - a + 1
            if h <= line_h:
                if h >= 6:
                    bands.append((a, b))
                continue
            # 粗段过高 → 按 line_h 步长切，只在有明显空行处断开
            ys = np.nonzero(light[a:b + 1].sum(axis=1) > min_px)[0] + a
            sub_start = sub_prev = int(ys[0])
            for y in ys[1:]:
                y = int(y)
                if (y - sub_prev) >= 4 or (y - sub_start) >= line_h:
                    if sub_prev - sub_start + 1 >= 6:
                        bands.append((sub_start, sub_prev))
                    sub_start = y
                sub_prev = y
            if sub_prev - sub_start + 1 >= 6:
                bands.append((sub_start, sub_prev))
        return bands

    def analyze(self, rgb=None):
        """返回 dict(hit, ...)。hit=True 表示区域内存在「你获得了物品：[xxx]」"""
        if rgb is None:
            rgb = self.grab()
        yellow, green, light = self._masks(rgb)

        # 区域内可能有 2~3 行历史提示，逐行判断，任意一行命中即算命中
        best = None
        for (y0, y1) in self._row_bands(light):
            sub_y = slice(y0, y1 + 1)
            sub_rgb = rgb[sub_y]
            sub_l = light[sub_y]
            sub_g = green[sub_y]
            sub_yel = yellow[sub_y]

            y_blk = self._blocks(sub_yel, min_w=3)
            g_blk = self._blocks(sub_g, min_w=3)
            l_blk = self._blocks(sub_l, min_w=3)
            # 6 个「获得」汉字：宽 15~30 的连续绿块（『你』是黄色，不在此列）
            g_han = [b_ - a_ + 1 for a_, b_ in g_blk if 15 <= (b_ - a_ + 1) <= 30]

            hit = (len(y_blk) >= 1 and len(g_han) >= 5 and len(l_blk) >= 9)
            if not hit:
                continue

            # 物品名：方括号内的灰字。实测结构（行内 x 坐标）：
            #   13-34 黄图标 | 61-195 六个绿汉字 | 207-210 绿冒号
            #   222-226 灰 '[' | 228-272 灰物品名 | 274-277 灰 ']' | 282-287 绿句号
            # ⚠️ 不能以「最后一个绿块」为界（末尾句号也是绿的），
            # 必须取冒号之后的灰块，并排除最外侧两个方括号。
            colon_end = 0
            for a_, b_ in g_blk:
                if (b_ - a_ + 1) >= 15:       # 汉字块（6 个，宽 19~21）
                    colon_end = max(colon_end, b_)
            for a_, b_ in g_blk:              # 再吸收紧随其后的绿冒号（宽 4）
                if a_ > colon_end and (b_ - a_ + 1) <= 8:
                    colon_end = max(colon_end, b_)
                    break
            gray_blk = []
            for a_, b_ in l_blk:
                if b_ <= colon_end:
                    continue
                sub = sub_rgb[:, a_:b_ + 1]
                px = sub[sub_l[:, a_:b_ + 1]]
                if len(px) == 0:
                    continue
                mr, mg, mb = px[:, 0].mean(), px[:, 1].mean(), px[:, 2].mean()
                if _is_gray((mr, mg, mb)):
                    gray_blk.append((a_, b_))
            # 去掉首尾方括号（宽度 ≤ 7 的窄块），剩下的是物品名
            brackets = [blk for blk in gray_blk if (blk[1] - blk[0] + 1) <= 7]
            names = [blk for blk in gray_blk if (blk[1] - blk[0] + 1) > 7]
            if not names:
                names = gray_blk
            item_blocks = names

            cand = {"hit": True, "row": (int(y0), int(y1)),
                    "y_blk": len(y_blk), "g_han": len(g_han),
                    "l_blk": len(l_blk), "item_blocks": item_blocks,
                    "item_x": (item_blocks[0][0], item_blocks[-1][1]) if item_blocks else None,
                    "green_end": (g_blk[-1][1] if g_blk else 0),
                    "name_row": (int(y0), int(y1))}
            # 取最靠下（最新）的那一行
            if best is None or cand["row"][0] > best["row"][0]:
                best = cand

        if best is None:
            return {"hit": False, "y_blk": 0, "g_han": 0, "l_blk": 0,
                    "item_blocks": [], "item_x": None, "green_end": 0,
                    "row": None, "name_row": None}
        return best

    # ---------- 读物品名（可选，靠 tesseract） ----------
    def read_item_name(self, rgb=None):
        """只把方括号内那一段切出来 OCR，避免整行噪声"""
        if not self._tess:
            return None
        if rgb is None:
            rgb = self.grab()
        from PIL import Image
        info = self.analyze(rgb)
        if not info.get("hit"):
            return None
        # 只保留命中的那一行，避免相邻行的字混进 OCR
        row = info.get("name_row")
        if row:
            y0, y1 = row
            rgb = rgb[max(0, y0 - 1):y1 + 2]
            info = self.analyze(rgb)
            if not info.get("hit"):
                return None
        x0, x1 = info.get("item_x") or (info.get("green_end", 0), rgb.shape[1] - 1)
        x0 = max(0, x0 - 8)                    # 带上方括号
        x1 = min(rgb.shape[1] - 1, x1 + 8)
        mask = self._masks(rgb)[2][:, x0:x1 + 1]
        out = np.where(mask, 255, 0).astype(np.uint8)
        img = Image.fromarray(out)
        img = img.resize((img.width * 8, img.height * 8), Image.NEAREST)
        tmp = os.path.join(tempfile.gettempdir(), "_loot_ocr.png")
        img.save(tmp)
        try:
            res = subprocess.run(
                [self._tess, tmp, "stdout", "-l", "chi_sim", "--psm", "7"],
                capture_output=True, timeout=8)
            text = res.stdout.decode("utf-8", "ignore")
        except Exception as e:
            self.log("OCR 失败: %s", e)
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        text = "".join(text.split())
        # 只保留方括号里的内容
        for lb, rb in (("[", "]"), ("[", "|"), ("【", "】")):
            if lb in text and rb in text:
                text = text.split(lb, 1)[1].split(rb, 1)[0]
                break
        text = text.strip("[]|【】:：")
        return text or None

    # ---------- 对外 ----------
    def snapshot(self, rgb=None):
        """返回这一行的指纹（用来判断有没有新提示出现）"""
        if rgb is None:
            rgb = self.grab()
        _y, _g, light = self._masks(rgb)
        return light

    @staticmethod
    def fingerprint(light):
        """把二值图压成短字符串，用于「这一帧和上一帧是否同一行」比对"""
        if light is None or light.size == 0:
            return ""
        h = light.shape[0]
        # 取中间 60% 的竖直带，压成 60 列 × 21 行签名
        step_y = max(h // 21, 1)
        w = light.shape[1]
        step_x = max(w // 60, 1)
        out = []
        for y in range(0, min(h, 21 * step_y), step_y):
            for x in range(0, min(w, 60 * step_x), step_x):
                blk = light[y:y + step_y, x:x + step_x]
                out.append("1" if blk.mean() > 0.25 else "0")
        return "".join(out)

    def read(self, want_name=False):
        """返回 dict(hit=bool, name=str|None, fp=str)"""
        try:
            rgb = self.grab()
        except Exception as e:
            self.log("读取聊天框失败: %s", e)
            return {"hit": False, "name": None, "fp": ""}
        info = self.analyze(rgb)
        fp = self.fingerprint(self._masks(rgb)[2])
        if not info["hit"]:
            return {"hit": False, "name": None, "fp": fp}
        name = self.read_item_name(rgb) if want_name else None
        return {"hit": True, "name": name, "fp": fp}

    def wait_new_loot(self, prev_fp="", timeout=3.0, interval=0.25):
        """轮询等待「出现一条新的物品提示」（指纹与本竿抛竿前不同）。

        返回 (hit, name, fp)。超时返回 (False, None, 最后一次指纹)。
        """
        import time as _t
        t0 = _t.monotonic()
        last = prev_fp
        while _t.monotonic() - t0 < timeout:
            r = self.read(want_name=False)
            last = r["fp"] or last
            if r["hit"] and r["fp"] != prev_fp:
                name = self.read_item_name()
                return (True, name, r["fp"])
            _t.sleep(interval)
        return (False, None, last)
