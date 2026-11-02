"""键鼠输入 —— 对应 Source/FishingFunBot/Platform/WowProcess.cs（215 行）

原版右击拾取走的是 `RightClickMouse_LiamCooper`（WowProcess.cs:156）：
    RightUp(position) → sleep 200 → Cursor.Position = position
    → sleep LootDelay(2000) → RightDown(position) → sleep 30~77
    → RightUp(position) → Refocus → sleep LootDelay/2

本机实测（2560x1600 @175% DPI）：
    SetCursorPos 0/3 成功（返回 True 但落点按 1.75 等比偏）
    mouse_event(MOVE|ABSOLUTE) 3/3 成功
    SendInput(MOVE|ABSOLUTE)   3/3 成功
→ 时序逐条照抄，只把「移动光标」的实现换成 SendInput 绝对坐标。
"""

import ctypes
import ctypes.wintypes as wt
import random
import time

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

INPUT_MOUSE = 0
PUL = ctypes.POINTER(ctypes.c_ulong)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _user32():
    return ctypes.windll.user32


def _screen_size():
    u = _user32()
    return (u.GetSystemMetrics(0) or 1, u.GetSystemMetrics(1) or 1)


def _send_mouse(flags, nx=0, ny=0):
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(nx, ny, 0, flags, 0, None)
    _user32().SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _normalize(x, y):
    sw, sh = _screen_size()
    nx = int(round(x * 65535.0 / max(sw - 1, 1)))
    ny = int(round(y * 65535.0 / max(sh - 1, 1)))
    return nx, ny


def move_cursor(x, y):
    nx, ny = _normalize(x, y)
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny)


def get_cursor_pos():
    pt = wt.POINT(0, 0)
    _user32().GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def _button(flag):
    _send_mouse(flag)


class WowProcess:
    """对应 C# 的 static class WowProcess。LootDelay 单位毫秒，与原版一致。"""

    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    LootDelay = 2000

    def __init__(self, hwnd=None, log=None, key_mode="scancode"):
        self.hwnd = hwnd
        self.log = log or (lambda *a, **k: None)
        self._last_key = None
        self._random = random.Random()
        # 按键方式：postmessage(原版) / scancode(SendInput扫描码) / vk(SendInput虚拟键)
        self.key_mode = key_mode

    # ---- 逐行对应 WowProcess.cs:81-86 ----
    def press_key(self, vk):
        self._key_down(vk)
        time.sleep((50 + self._random.randrange(0, 75)) / 1000.0)
        self.key_up(vk)

    def _key_down(self, vk):
        self._last_key = vk
        if self.key_mode == "postmessage":
            self._post(self.WM_KEYDOWN, int(vk))
        else:
            self._sendinput_key(int(vk), up=False)

    def key_up(self, vk):
        if self.key_mode == "postmessage":
            self._post(self.WM_KEYUP, int(vk))
        else:
            self._sendinput_key(int(vk), up=True)

    def _post(self, msg, wparam):
        if self.hwnd:
            _user32().PostMessageW(self.hwnd, msg, wparam, 0)

    # ---- 非原版：SendInput 键击（时光服/Wine 不认 PostMessage 时用）----
    def _sendinput_key(self, vk, up):
        u = _user32()
        scancode = (self.key_mode == "scancode")
        inp = _INPUT()
        inp.type = 1                                   # INPUT_KEYBOARD
        ki = _KEYBDINPUT()
        ki.wVk = 0 if scancode else vk
        ki.wScan = u.MapVirtualKeyW(vk, 0) if scancode else 0
        ki.dwFlags = (0x0008 if scancode else 0) | (0x0002 if up else 0)
        inp.ki = ki
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    # ---- 逐行对应 WowProcess.cs:156-174 ----
    def right_click_mouse(self, position):
        x, y = int(position[0]), int(position[1])

        _button(MOUSEEVENTF_RIGHTUP)              # ① 先补一个右 UP（原版第 1 行）
        old_position = get_cursor_pos()           #    记原位，供 Refocus 恢复

        time.sleep(0.200)
        move_cursor(x, y)                         # ② Cursor.Position = position
        time.sleep(0.050)                         # ★ 给游戏一帧时间处理光标
        time.sleep(self.LootDelay / 1000.0)       # ③ LootDelay = 2000ms，不能跳

        got = get_cursor_pos()
        self.log("拾取: 目标=(%d,%d) 光标实际=(%d,%d) 差=(%+d,%+d)",
                 x, y, got[0], got[1], got[0] - x, got[1] - y)

        _button(MOUSEEVENTF_RIGHTDOWN)            # ④
        time.sleep((30 + self._random.randrange(0, 47)) / 1000.0)
        _button(MOUSEEVENTF_RIGHTUP)              # ⑤

        self._refocus(old_position)
        time.sleep(self.LootDelay / 2000.0)       # ⑥ LootDelay / 2

    @staticmethod
    def park_cursor():
        """把鼠标停到屏幕右下角（避开游戏画面，防止悬停提示干扰判断）。

        ★ v52：原来写成实例方法 def park_cursor():（漏了 self），
        调用 self.wow_process.park_cursor() 必然 TypeError，又被上层
        except 吞掉 —— 等于从来没停过鼠标。改成 staticmethod，
        并且不再静默：失败要把异常抛给调用方记日志。
        """
        sw, sh = _screen_size()
        move_cursor(sw - 1, sh - 1)

    @staticmethod
    def park_cursor_verified(tries=3, tol=4):
        """把鼠标移到屏幕边缘并【回读校验】，不在边缘就重试。

        ★ v54：用户明确要求「每次抛竿前把鼠标移到屏幕边缘」。
        原来 start() 里只调一次 park_cursor() + 回读，回读失败只打警告、
        不做任何补救。真机若恰好在那一刻被游戏抢焦点/DPI 换算偏移，
        光标就留在画面中间，悬停提示会盖住浮标。

        本函数按右下角 → 右上角 → 左下角 依次尝试，每次移动后回读确认；
        全部失败返回 False，由调用方记日志。

        返回 (ok, (x, y)) —— 实际落点，便于日志审计。
        """
        try:
            sw, sh = _screen_size()
        except Exception:
            return (False, (0, 0))
        # 依次尝试的边角。右下角优先（离浮标最远）。
        targets = [(sw - 1, sh - 1), (sw - 1, 1), (1, sh - 1), (1, 1)]
        last = (0, 0)
        for tx, ty in targets:
            for _ in range(max(1, int(tries))):
                try:
                    move_cursor(tx, ty)
                except Exception:
                    continue
                try:
                    x, y = get_cursor_pos()
                except Exception:
                    return (False, (0, 0))
                last = (x, y)
                if abs(x - tx) <= tol and abs(y - ty) <= tol:
                    return (True, last)
        return (False, last)

    @staticmethod
    def cursor_is_parked(tol=4):
        """当前光标是否已经停靠在角落（park 生效校验用）。"""
        try:
            sw, sh = _screen_size()
            x, y = get_cursor_pos()
            return abs(x - (sw - 1)) <= tol and abs(y - (sh - 1)) <= tol
        except Exception:
            return False

    # ---- 逐行对应 WowProcess.cs:176-198 ----
    def _refocus(self, old_position):
        try:
            fg = _user32().GetForegroundWindow()
            if self.hwnd and fg and fg != self.hwnd:
                self._post(self.WM_RBUTTONDOWN, 0x02)
                time.sleep(0.030)
                self._post(self.WM_RBUTTONUP, 0x02)
                self._key_down(0x1B)              # VK_ESCAPE
                time.sleep(0.030)
                self.key_up(0x1B)
            # 非原版：不再移回原位，改为停靠屏幕右下角（避开悬停提示）
            self.park_cursor()
        except Exception as e:
            self.log("Refocus 失败: %s", e)

    @staticmethod
    def is_wow_classic(process_names=()):
        return any("classic" in str(n).lower() for n in process_names)
