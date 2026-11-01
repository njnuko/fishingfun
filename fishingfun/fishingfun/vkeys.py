"""按键名 → Windows VK 码转换（公共工具）

⚠️ 这是 v1 移植里最致命的 bug 来源：
    原版 C# 用 ConsoleKey.D4，值是 0x34。
    第一版移植写成 cast_key=int("4") = 4 = 0x04，
    0x04 不是任何按键（VK 里 0x30-0x39 才是数字键），
    所以 PostMessage/SendInput 全部打空 —— 「抛竿动作都没有」。

所有把「命令行按键字符串」转 VK 的地方都必须走这个函数。
"""

import ctypes

_VK_NAMED = {
    "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
    "ESC": 0x1B, "ESCAPE": 0x1B, "BACKSPACE": 0x08,
    "HOME": 0x24, "END": 0x23, "INSERT": 0x2D, "DELETE": 0x2E,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "SHIFT": 0x10, "CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12,
    "--": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE,
    "/": 0xBF, "\\": 0xDC, "`": 0xC0,
}


def key_to_vk(s):
    """'4' → 0x34, 'a' → 0x41, 'F5' → 0x74, 'space' → 0x20"""
    t = str(s).strip().upper()
    if not t:
        raise SystemExit("按键不能为空")

    if len(t) == 1:
        ch = t[0]
        if "0" <= ch <= "9":
            return 0x30 + (ord(ch) - ord("0"))
        if "A" <= ch <= "Z":
            return 0x41 + (ord(ch) - ord("A"))
        if ch == "-":
            return 0xBD
        try:
            vk = ctypes.windll.user32.VkKeyScanW(ord(t))
            if vk != -1:
                return vk & 0xFF
        except Exception:
            pass
        raise SystemExit(f"无法识别的按键: {s}")

    if t.startswith("F") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)

    if t in _VK_NAMED:
        return _VK_NAMED[t]

    raise SystemExit(f"无法识别的按键: {s}")


def key_to_vk_safe(s, default=0x34):
    try:
        return key_to_vk(s)
    except SystemExit:
        return default


def vk_name(vk):
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x6F}"
    return f"VK_0x{vk:02X}"
