"""计时动作 —— 严格复刻 Source/FishingFunBot/Platform/TimedAction.cs（47 行）"""

import time


class TimedAction:
    def __init__(self, action, action_timeout_ms, max_time_secs):
        self.action = action                    # Action<TimedAction>
        self.action_timeout_ms = action_timeout_ms
        self.max_time_secs = max_time_secs
        self._sw = time.monotonic()
        self._max = time.monotonic()

    @property
    def elapsed_secs(self):
        # C#: (int)maxTime.Elapsed.TotalSeconds
        return int(time.monotonic() - self._max)

    def execute_now(self):
        self.action(self)

    # 逐行对应 TimedAction.cs:36-46
    def execute_if_due(self):
        if (time.monotonic() - self._sw) * 1000.0 > self.action_timeout_ms:
            self.action(self)
            self._sw = time.monotonic()
        # 返回「还没到 maxTime」，调用方 if (!...) return; 即为超时退出
        return (time.monotonic() - self._max) < self.max_time_secs
