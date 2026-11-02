"""对应 Source/FishingFunConsole/Program.cs（38 行）

用法（管理员权限 CMD/PowerShell，游戏窗口化或窗口化全屏）：
    python run.py                 # 默认 4 号键抛竿、5 号键十分钟宏
    python run.py --cast-key 1 --mode tan --region 0.25,0.25,0.75,0.75
"""

import argparse
import ctypes
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fishingfun import (FishingBot, PixelClassifier, PositionBiteWatcher,
                        SearchBobberFinder, WowProcess, WowScreen,
                        enable_dpi_awareness)
from fishingfun.vkeys import key_to_vk, vk_name


def parse_region(s):
    if not s:
        return None
    try:
        v = [float(x) for x in s.replace("，", ",").split(",")]
    except ValueError:
        raise SystemExit("--region 格式: x0,y0,x1,y1（四个 0-1 比例）")
    if len(v) != 4 or not all(0.0 <= x <= 1.0 for x in v) or v[2] <= v[0] or v[3] <= v[1]:
        raise SystemExit("--region 非法: 需要 x1>x0, y1>y0, 且都在 0-1 之间")
    return tuple(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cast-key", default="4", help="抛竿按键（默认 4）")
    # v22：默认从 "5" 改为 "F"。5 是游戏里绑了技能的键，启动时按一下会释放技能；
    # F 用于规避 10 分钟防挂机，不触发任何技能。
    ap.add_argument("--ten-min-key", default="F", help="十分钟宏按键（逗号分隔，空=不用）")
    ap.add_argument("--mode", default="tan", choices=["tan", "red", "blue"],
                    help="浮标颜色模式（默认 tan = 时光服米黄浮标）")
    ap.add_argument("--strike", type=int, default=4,
                    help="咬钩阈值下限：浮标相对水位基准下沉多少像素触发（默认 4）")
    ap.add_argument("--no-auto-strike", dest="auto_strike", action="store_false",
                    help="关闭咬钩阈值自适应（默认开启：按实测抖动自动抬高，只会抬不会降）")
    ap.add_argument("--bite-mode", default="robust", choices=["robust", "legacy"],
                    help="咬钩判定：robust=抗噪声(默认) / legacy=原版中位数")
    ap.add_argument("--confirm-frames", type=int, default=2,
                    help="robust 模式：连续多少帧下沉才确认（默认 2；1 帧会把水面噪点当真咬钩）")
    ap.add_argument("--vanish-frames", type=int, default=8,
                    help="robust 模式：连续多少帧检测不到浮标才算咬钩（默认 8≈2秒；0=禁用。设 4 会误判）")
    ap.add_argument("--settle-time", type=float, default=2.0,
                    help="抛竿后稳定期秒数，期内只更新水面基准不判咬钩（默认 2.0）")
    ap.add_argument("--region", default=None, help="截取范围 x0,y0,x1,y1（比例）")
    ap.add_argument("--cast-flight", type=float, default=1.8,
                    help="抛竿飞行/水花期(秒)，期内扫不到浮标不计数（默认 1.8）")
    ap.add_argument("--anchor-ratio-min", type=float, default=0.35,
                    help="锚点可信度门槛(0~1)，低于此值本竿放弃重抛（默认 0.35）")
    ap.add_argument("--anchor-wait", type=float, default=3.3,
                    help="找浮标锚点最多等几秒，超时立刻重抛"
                         "（默认 3.3 = 飞行期1.8 + 落水观察1.5；"
                         "小于飞行期会导致浮标还在空中就判重抛）")
    ap.add_argument("--read-loot", default="1", choices=["1", "0"],
                    help="拾取后读聊天框「你获得了物品：[xxx]」写进日志（默认 1 开）")
    ap.add_argument("--calib", default="1", choices=["1", "0"],
                    help="抛竿前后差分标定浮标（默认 1 开；0=只用颜色阈值，v19 行为）")
    ap.add_argument("--calib-sink", default="1", choices=["1", "0"],
                    help="第一竿抛竿后采 17 秒，实测咬钩下沉幅度并自动定阈值（默认 1 开）")
    ap.add_argument("--calib-sink-sec", type=float, default=17.0,
                    help="咬钩幅度采样时长秒数（默认 17）")
    ap.add_argument("--exclude-side", type=float, default=1.0 / 7.0,
                    help="左右各排除多少（比例，默认 0.142857 = 1/7，即小地图/头像/聊天框区）")
    ap.add_argument("--exclude-bottom", type=float, default=0.25,
                    help="下方排除多少（比例，默认 0.25 = 1/4，即技能条区）")
    ap.add_argument("--no-bite", default="1", choices=["1", "0"],
                    help="收竿后检测屏幕上方「没有鱼上钩」黄字，判断咬钩是否误判（默认 1 开）")
    ap.add_argument("--debug", default="0", choices=["1", "0"],
                    help="每竿存 咬钩前/拾取后 两帧到 debug/，用于事后对比（默认 0 关）")
    # ★ v45：复盘日志（用户要求每竿锚点 / 每秒浮动值 / 区域截图都进日志）
    ap.add_argument("--save-regions", default="1", choices=["1", "0"],
                    help="每竿的「锚定 / 咬钩 / 重抛」区域截图存 debug/（默认 1 开）")
    ap.add_argument("--forensic", default="1", choices=["1", "0"],
                    help="★ 关键节点取证（默认 1 开）：每竿存 "
                         "锚定/咬钩 的「区域图+红框」「全屏图+红框」「特征值.txt」。"
                         "首竿文件名带 首竿_ 前缀")
    ap.add_argument("--frame-log", default="1", choices=["1", "0"],
                    help="判咬钩期间逐帧输出「当前y / 锚点y / 下沉px / 阈值」（默认 1 开，"
                         "日志会变长）")
    ap.add_argument("--trace-sec", type=float, default=1.0,
                    help="每竿跟踪期每 N 秒存一张区域图并记一行采样（默认 1.0 秒，0=关）")
    ap.add_argument("--save-interval", type=float, default=1.0,
                    help="判咬钩期 debug 图存档间隔秒数（默认 1.0 = 每秒一张）。"
                         "咬钩趋势出现时自动切到 0.2 秒一张，事件帧始终强制存")
    ap.add_argument("--keep-casts", type=int, default=20,
                    help="debug 帧图只保留最近 N 竿（默认 20 竿约 600MB），0=不限。日志不受影响")
    ap.add_argument("--diff-bobber", default="1", choices=["1", "0"],
                    help="用 R-B 色差找浮标（默认 1 开）。不写死颜色，亮黄/暗橙都能找")
    ap.add_argument("--diff-threshold", type=int, default=20,
                    help="色差阈值：R-B 超过多少算浮标暖色（默认 20；实测水面≈-23，浮标 90~255）")
    ap.add_argument("--diff-confirm", type=int, default=3,
                    help="同位置连续多少帧色差命中才确认是浮标（默认 3，用于滤掉水面反光）")
    ap.add_argument("--no-bobber-frames", type=int, default=3,
                    help="连续多少帧找不到浮标就停止并提示换钓点（默认 3）")
    ap.add_argument("--log", default="INFO", help="日志级别")
    ap.add_argument("--key-mode", default="scancode",
                    choices=["scancode", "vk", "postmessage"],
                    help="按键方式（默认 scancode = SendInput 扫描码；postmessage = 原版 C# 方式）")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler("fishingfun.log", mode="w", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("Fishbot").info

    # ★ 必须在取任何屏幕尺寸之前
    enable_dpi_awareness()

    screen = WowScreen(region=parse_region(args.region), log=log)
    from fishingfun import __version__
    log("=== FishingFun %s ===", __version__)
    log("启动参数: %s", " ".join(sys.argv[1:]))
    log(screen.describe())

    # 逐行对应 Program.cs:18-31
    classifier = PixelClassifier(args.mode)
    if args.mode == "red" or args.mode == "blue":
        classifier.set_configuration(True)      # 时光服 = classic 配置

    finder = SearchBobberFinder(classifier, logger=log,
                                ui_masks=[(0.88, 0.00, 1.00, 1.00),
                                          (0.00, 0.00, 0.22, 0.16),
                                          (0.00, 0.80, 1.00, 1.00)])
    finder.screen = screen          # 首竿后由 bot 调 screen.arm_area() 标定边界
    finder.exclude_side_ratio = args.exclude_side
    finder.exclude_bottom_ratio = args.exclude_bottom
    log("排除区: 左右各 %.1f%%（--exclude-side）| 下方 %.1f%%（--exclude-bottom）| 上边界=首竿标定测出的天空分界线",
        100.0 * finder.exclude_side_ratio, 100.0 * finder.exclude_bottom_ratio)

    # 非原版增强：色差找浮标。不写死颜色，只判「R 明显高于 B」。
    # 找不到 = 这个钓点太暗（浮标颜色沉进水面），必须停下来让用户换地方，
    # 不能硬着头皮瞎抛 —— 实测瞎抛会连续空竿 + 误判咬钩。
    finder.bobber_diff_enabled = args.diff_bobber == "1"
    finder.diff_threshold = args.diff_threshold
    finder.diff_confirm_frames = args.diff_confirm
    finder.no_bobber_limit = args.no_bobber_frames
    finder.strict_yellow = True
    log("色差找浮标: %s | R-B>%d | 同位置连续 %d 帧才确认 | 连续 %d 帧找不到就停止提示换钓点",
        "开" if args.diff_bobber == "1" else "关", args.diff_threshold,
        args.diff_confirm, args.no_bobber_frames)
    watcher = PositionBiteWatcher(args.strike, mode=args.bite_mode,
                                  confirm_frames=args.confirm_frames,
                                  vanish_frames=args.vanish_frames)
    watcher.auto_strike = args.auto_strike          # v37：抖动自适应开关
    # ★ v44：每竿重新锚定判定（用户指定逻辑）——
    #   每竿第一帧用浮标特征定锚点（每竿落点不同，逐竿重定）；之后每帧算 |本竿锚点y - 当前y|，
    #   下沉量 ≥ 第一竿标定的 strike_value 就判咬钩 → 移鼠标 → 右键拾取。
    watcher.per_cast_anchor_mode = True
    watcher.log = log
    log("咬钩判定: mode=%s strike=%s(标定值) 每竿重新锚定=开 连续确认=%d帧 消失判定=%d帧",
        args.bite_mode, args.strike, args.confirm_frames, args.vanish_frames)
    # 顺手把「去哪拿诊断截图」打出来，免得跑完找不到 diag 目录
    log("诊断截图目录: %s", os.path.join(os.getcwd(), "diag"))

    wow = WowProcess(hwnd=screen._hwnd, log=log, key_mode=args.key_mode)
    log("按键方式: %s", args.key_mode)

    ten = [key_to_vk(k) for k in args.ten_min_key.split(",") if k.strip()]
    cast_vk = key_to_vk(args.cast_key)
    log("抛竿键: '%s' → VK=0x%02X (%s)", args.cast_key, cast_vk, vk_name(cast_vk))
    log("十分钟宏: %s", [f"0x{v:02X}" for v in ten] or "无")
    bot = FishingBot(finder, watcher, cast_key=cast_vk,
                     ten_min_key=ten, wow_process=wow, log=log, grabber=screen)
    bot.anchor_wait = args.anchor_wait
    bot.cast_flight = args.cast_flight
    bot.anchor_ratio_min = args.anchor_ratio_min
    bot.settle_time = args.settle_time
    # v20：差分标定开关。关掉就是 v19 的纯颜色阈值行为。
    if hasattr(finder, "calib_enabled"):
        finder.calib_enabled = args.calib == "1"
    # v21：咬钩幅度自动标定
    bot.calib_sink = args.calib_sink == "1"
    bot.sink_calib_seconds = args.calib_sink_sec
    log("找锚点超时: %.1fs（超时立刻重抛）| 飞行期 %.1fs | 可信度门槛 %.0f%% | 稳定期: %.1fs | 差分标定: %s",
        bot.anchor_wait, bot.cast_flight, bot.anchor_ratio_min * 100, bot.settle_time,
        "开" if args.calib == "1" else "关")
    bot.fishing_event_handler = lambda e: log("event: %s", e)

    # 非原版增强：找不到浮标 → 停止程序并提示用户。
    # 触发条件由 finder.no_bobber_limit 控制（默认连续 3 帧）。
    # ★ v41：必须区分两种「连续 N 帧找不到」——
    #   (a) 本竿从未锁定过浮标 → 可能是真的看不清，提示检查；
    #   (b) 本竿锁定过、后来又丢了 → 是咬钩（浮标沉下去）或目标离开视野，
    #       绝不能打印「钓点太暗」误导用户（实测 20:42 日志：前面 64 帧
    #       全部稳定命中 (773,280)，标定结束后 3 帧丢失就报「钓点太暗」）。
    def _on_no_bobber(n):
        if getattr(bot, "_calibrating", False):
            log("标定期间连续 %d 帧找不到浮标（浮标被咬走/离场属正常），不判停机", n)
            return
        if getattr(bot, "_anchor_locked_this_cast", False):
            log("=" * 60)
            log("⚠️  连续 %d 帧找不到浮标 —— 本竿已锁定过浮标，判为目标消失/咬钩", n)
            log("⚠️  不提示换钓点，重抛继续。")
            log("=" * 60)
            # ★ v49：这里原来直接 is_enabled=False，于是「重抛继续」之后
            #   下一行必定是 Bot has Stopped —— 真机日志 08:56:33 的停机
            #   就是这么来的。现在只标记「本轮结束，可恢复」，
            #   由 FishingBot.run() 的循环末尾清标志并继续下一竿。
            bot._soft_stop = True
            bot.is_enabled = False
            return
        log("=" * 60)
        log("⚠️  连续 %d 帧找不到浮标 —— 本竿从未锁定浮标", n)
        log("⚠️  浮标不在视野内或与水面颜色重合，继续抛只会瞎钓。")
        log("⚠️  请确认浮标在截屏区内、能看清，再重新启动。")
        log("=" * 60)
        # 永久停机：从没锁定过，说明根本没看到浮标，重抛也没用。
        bot._soft_stop = False
        bot.is_enabled = False

    finder.abort_cb = _on_no_bobber

    # 非原版增强：收竿后检测屏幕上方「没有鱼上钩」
    # 用户实测：钓鱼时机对了每次都能钓到，时机不对才弹这行黄字。
    # → 这行字是判断「咬钩判定准不准」最硬的依据。
    if args.no_bite == "1":
        from fishingfun.nobitedetector import NoBiteDetector
        try:
            bot.no_bite_detector = NoBiteDetector(screen, log=log)
            probe = bot.no_bite_detector.check()
            log("没有鱼上钩检测: 开 | 当前屏幕黄字 px=%d rows=%d %s",
                probe.get("px", 0), probe.get("rows", 0),
                "（⚠️提示已在屏上，首竿判定可能不准）" if probe.get("hit") else "（当前无提示）")
        except Exception as e:
            bot.no_bite_detector = None
            log("没有鱼上钩检测: 初始化失败（%s），本局关闭", e)
    else:
        log("没有鱼上钩检测: 关")

    # 非原版增强：拾取后读聊天框「你获得了物品：[xxx]」
    if args.read_loot == "1":
        from fishingfun.lootreader import LootReader, CHAT_BOX
        try:
            lr = LootReader(log=log)
            pre = lr.read(want_name=False)          # 记录当前这条提示的指纹
            bot._loot_fp = pre.get("fp", "")
            bot.loot_reader = lr
            log("物品提示读取: 开 | 区域 x%d-%d y%d-%d | 初始指纹=%s | OCR=%s",
                CHAT_BOX[0], CHAT_BOX[2], CHAT_BOX[1], CHAT_BOX[3],
                bot._loot_fp[:12] or "(空)",
                "有" if lr._tess else "无(只判有无不读名字)")
        except Exception as e:
            bot.loot_reader = None
            log("物品提示读取: 初始化失败（%s），本局关闭", e)
    else:
        log("物品提示读取: 关")

    # 非原版调试：每竿 pre/post 两帧落盘 debug/
    bot.debug_frames = (args.debug == "1")
    if bot.debug_frames:
        log("调试截帧: 开 | 每竿存 debug/cNNN_pre_*.bmp（咬钩前）"
            " + debug/cNNN_post_*.bmp（拾取后）")

    # ★ v45：每竿可复盘日志（用户要求「每一竿的锚点、每秒标定值、区域截图」都留）
    #   --debug 1 视为「全开」：它本来就是拿来复盘的，不该只存 pre/post 两张。
    bot.save_regions = (args.save_regions == "1") or bot.debug_frames
    bot.frame_log = (args.frame_log == "1") or bot.debug_frames
    bot.trace_seconds = float(args.trace_sec)
    bot.save_interval_sec = float(args.save_interval)
    bot.keep_debug_casts = int(args.keep_casts)
    # ★ v55：关键节点取证默认开（--debug 1 也强制开）
    bot.forensic = (args.forensic == "1") or bot.debug_frames
    if bot.forensic:
        log("关键节点取证: 开 → 每竿 debug/ 下三件套")
        log("  · cNNN_[首竿_]锚定_区域_WxH@原点_t秒_时间.bmp  （浮标处红框）")
        log("  · cNNN_[首竿_]锚定_全屏_WxH_t秒_时间.bmp        （整屏+红框，离线调试用）")
        log("  · cNNN_[首竿_]锚定_特征_时间.txt                （浮标尺寸/面积/填充率/指纹）")
        log("  · cNNN_咬钩_区域/全屏/特征                   （咬钩图 + strike_value/yDiff）")
    watcher.frame_log = bot.frame_log
    if bot.save_regions or bot.frame_log:
        log("复盘日志: 区域截图=%s 逐帧浮动值=%s 跟踪逐秒采样=%.1fs",
            "开" if bot.save_regions else "关",
            "开" if bot.frame_log else "关", bot.trace_seconds)
        log("  · 每竿锚点会存 debug/cNNN_锚定_区域WxH@原点_t秒_锚点x_y_*.bmp")
        log("  · 只有「锚定 / 咬钩 / 重抛 / 跟踪逐秒」四类帧会落盘"
            "（每帧都存会让帧率减半，反而漏判）")

    # 激活游戏窗口（SendInput 需要目标窗口在前台，PostMessage 不需要）
    if args.key_mode != "postmessage" and screen._hwnd:
        u = ctypes.windll.user32
        u.ShowWindow(screen._hwnd, 6)          # SW_MINIMIZE：先最小化
        time.sleep(0.15)
        u.ShowWindow(screen._hwnd, 3)          # SW_MAXIMIZE：强制最大化
        time.sleep(0.25)
        u.SetForegroundWindow(screen._hwnd)
        time.sleep(0.6)
        fg = u.GetForegroundWindow()

        # 校验是不是真的最大化了（WS_MAXIMIZE = 0x01000000）
        GWL_STYLE = -16
        style = u.GetWindowLongW(screen._hwnd, GWL_STYLE)
        is_max = bool(style & 0x01000000)
        log("前台窗口: 0x%X %s | 最大化=%s (style=0x%08X)",
            fg, "✅游戏" if fg == screen._hwnd else "❌不是游戏，按键可能无效",
            "✅" if is_max else "❌", style & 0xFFFFFFFF)
        if not is_max:
            u.ShowWindow(screen._hwnd, 3)      # 再试一次
            time.sleep(0.4)
            style = u.GetWindowLongW(screen._hwnd, GWL_STYLE)
            log("最大化重试结果: %s", "✅" if (style & 0x01000000) else "❌ 仍未最大化")

    wow.press_key(0x20)          # Program.cs:33 先按空格（开局/停马）
    time.sleep(1.5)
    # ★ v52：启动后、第一竿之前就把光标挪到屏幕边缘。
    #   用户实测：第一竿抛出去之前光标还停在画面中间，会挡住浮标/出悬停提示，
    #   污染首竿标定（首竿标定决定指纹和 strike_value，最贵）。
    # ★ v54：改用 park_cursor_verified()，回读校验 + 换边角重试。
    try:
        if hasattr(wow, "park_cursor_verified"):
            ok, pos = wow.park_cursor_verified()
            log("鼠标移到屏幕边缘: %s (实际落点 %d,%d)",
                "✅" if ok else "⚠️ 失败", pos[0], pos[1])
        else:
            wow.park_cursor()
    except Exception as e:
        log("鼠标停靠失败: %s", e)

    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()


if __name__ == "__main__":
    main()
