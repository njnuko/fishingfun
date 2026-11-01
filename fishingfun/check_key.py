"""按键自检 —— 判断「按 4 到底有没有抛竿」

用法（管理员 CMD，在 C:\\fishingfun 下）：
    python check_key.py 4              # 默认：先激活游戏窗口，再按 4
    python check_key.py 4 --no-focus   # 不激活窗口，仅 PostMessage（对比用）

会依次尝试三种按键方式，每种后面停 3 秒让你观察：
    1. PostMessage(WM_KEYDOWN/UP)   —— 原版 C# 用的方式
    2. SendInput 扫描码（游戏最认这个）
    3. SendInput 虚拟键码

每种方式之后会截一张图存成 key_test_N.png，
并统计画面里 tan 像素（鱼漂）命中的连通块，直接告诉你有没有抛出竿。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from fishingfun import WowScreen, enable_dpi_awareness
from fishingfun.wowscreen import find_wow_window
from fishingfun.vkeys import key_to_vk

u32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
MAPVK_VK_TO_VSC = 0


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _UNION)]


def _send(vk, up=False, scancode=False):
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    ki = _KEYBDINPUT()
    ki.wVk = 0 if scancode else vk
    ki.wScan = u32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) if scancode else 0
    ki.dwFlags = (KEYEVENTF_SCANCODE if scancode else 0) | (KEYEVENTF_KEYUP if up else 0)
    inp.ki = ki
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def press_postmessage(hwnd, vk):
    u32.PostMessageW(hwnd, 0x0100, vk, 0)
    time.sleep(0.06)
    u32.PostMessageW(hwnd, 0x0101, vk, 0)


def press_sendinput_scancode(vk):
    _send(vk, up=False, scancode=True)
    time.sleep(0.06)
    _send(vk, up=True, scancode=True)


def press_sendinput_vk(vk):
    _send(vk, up=False, scancode=False)
    time.sleep(0.06)
    _send(vk, up=True, scancode=False)


def tan_blobs(img):
    """返回 (命中连通块数, 每块描述) —— 用与 bot 完全相同的规则"""
    r = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    b = img[:, :, 2].astype(np.int16)
    m = ((r > 140) & (g > 105) & (b > 60) & (b < 175)
         & ((r - b) > 45) & ((g - b) > 15) & (np.abs(r - g) < 80))
    ys, xs = np.nonzero(m)
    return len(xs), (xs, ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", default="4")
    ap.add_argument("--no-focus", action="store_true")
    ap.add_argument("--region", default=None, help="x0,y0,x1,y1 比例，可选")
    args = ap.parse_args()

    vk = key_to_vk(args.key)
    print(f"测试按键: '{args.key}'  VK=0x{vk:02X}  (数字 4 必须是 0x34)")

    enable_dpi_awareness()
    found = find_wow_window()
    if not found:
        print("❌ 没找到游戏窗口！标题需含 World of Warcraft / 魔兽世界")
        print("   当前所有可见窗口标题：")
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, _):
            if u32.IsWindowVisible(hwnd):
                n = u32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.strip():
                        print("    -", buf.value)
            return True
        u32.EnumWindows(EnumProc(cb), 0)
        return

    hwnd, left, top, cw, ch = found
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, buf, 512)
    print(f"游戏窗口: 「{buf.value}」 {cw}x{ch} @ {left},{top}  hwnd=0x{hwnd:X}")

    fg = u32.GetForegroundWindow()
    print(f"当前前台窗口: 0x{fg:X}  {'✅是游戏' if fg == hwnd else '❌不是游戏'}")

    if not args.no_focus:
        print("→ 激活游戏窗口…")
        u32.ShowWindow(hwnd, 9)          # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
        time.sleep(0.8)
        fg = u32.GetForegroundWindow()
        print(f"   激活后前台: 0x{fg:X}  {'✅是游戏' if fg == hwnd else '❌仍不是游戏'}")

    region = None
    if args.region:
        region = tuple(float(x) for x in args.region.split(","))
    scr = WowScreen(region=region)
    scr._hwnd = hwnd
    print(scr.describe())
    print()

    tests = [
        ("PostMessage(WM_KEYDOWN)", lambda: press_postmessage(hwnd, vk)),
        ("SendInput 扫描码", lambda: press_sendinput_scancode(vk)),
        ("SendInput 虚拟键码", lambda: press_sendinput_vk(vk)),
    ]

    for i, (name, fn) in enumerate(tests, 1):
        print(f"=== [{i}/3] {name} ===")
        print("  3 秒后发送按键，请盯着游戏画面看有没有抛竿…")
        time.sleep(3)
        fn()
        print("  已发送。观察 3 秒…")
        time.sleep(3)
        img = scr.grab()
        cnt, _ = tan_blobs(img)
        fn_out = f"key_test_{i}.png"
        from PIL import Image
        Image.fromarray(img).save(fn_out)
        print(f"  tan 像素={cnt}  截图已存 {fn_out}")
        print()

    print("三种方式都试完了。")
    print("· 哪一种抛竿了 → 告诉我序号，我把它改成默认按键方式")
    print("· 全都没抛竿 → 要么 4 号键没绑钓鱼，要么游戏不认模拟按键")
    print("   请打开游戏技能栏截图发我，确认钓鱼绑在哪个键")


if __name__ == "__main__":
    main()
