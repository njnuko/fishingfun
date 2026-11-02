"""模拟 v49 主循环：验证「锁定过浮标→丢标」不再退出。"""
import sys, time, types

# 抽取真实 run.py 的 _on_no_bobber 逻辑做等价复刻校验
class Bot:
    def __init__(self):
        self.is_enabled = False
        self._anchor_locked_this_cast = False
        self._calibrating = False
        self._soft_stop = False
        self.soft_stop_pause = 0.35

def make_cb(bot, logs):
    def _on_no_bobber(n):
        if bot._calibrating:
            logs.append("标定期抑制")
            return
        if bot._anchor_locked_this_cast:
            bot._soft_stop = True
            bot.is_enabled = False
            logs.append("软停")
            return
        bot._soft_stop = False
        bot.is_enabled = False
        logs.append("硬停")
    return _on_no_bobber

def run_loop(bot, finder_cb, casts=5, locked_per_cast=(True, True, False, True, True)):
    """复刻 run() 的 while 结构。"""
    bot.is_enabled = True
    done = 0
    while bot.is_enabled:
        if done >= casts:
            break
        bot._anchor_locked_this_cast = locked_per_cast[done]
        bot._soft_stop = False
        # 模拟本竿失败触发 abort
        finder_cb(3)
        # 循环末尾 v49 的软停处理
        if bot._soft_stop:
            bot._soft_stop = False
            bot.is_enabled = True
            time.sleep(0)
        done += 1
    return done

logs = []
bot = Bot()
cb = make_cb(bot, logs)
n = run_loop(bot, cb)
print("跑完竿数:", n, "| 日志:", logs)
ok1 = (n == 3 and logs == ["软停", "软停", "硬停"])
print(("PASS" if ok1 else "FAIL") + " 软停继续、遇硬停退出（第3竿退出）")

# 标定期抑制
logs2 = []
bot2 = Bot(); bot2._calibrating = True
cb2 = make_cb(bot2, logs2)
run_loop(bot2, cb2, casts=2, locked_per_cast=(True, True))
ok2 = logs2.count("标定期抑制") == 2
print(("PASS" if ok2 else "FAIL") + " 标定期不判停机 | " + str(logs2))

sys.exit(0 if (ok1 and ok2) else 1)
