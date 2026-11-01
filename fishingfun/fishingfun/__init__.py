"""FishingFun Python 移植（严格 1:1 对应 julianperrott/FishingFun 的 8 个 C# 文件）"""

# 版本号：写进日志开头，一眼就能判定跑的是哪一版（避免"以为更新了其实没更新"）
__version__ = "v39"
BUILD = "2026-11-01 v39 真机日志四修:①黄色判据蓝通道上限100→175(修B钓点浮标rgb(227,190,142)被判无黄标,固等6秒) ②_watch见到色差已锁定就立即结束不再等黄标(修标定窗口被吃掉4.8秒) ③anchor_wait自动抬到飞行期+1.5s(修1.5<1.8浮标在空中就判重抛) ④地平线候选过于贴顶(<2%行高)判UI硬边不排除天空侧(修y=9)"

from .pixelclassifier import PixelClassifier, TanClassifier
from .searchbobberfinder import SearchBobberFinder
from .positionbitewatcher import PositionBiteWatcher
from .timedaction import TimedAction
from .fishbot import FishingBot, VK_SPACE, VK_4, VK_5
from .wowscreen import WowScreen, FakeGrabber, enable_dpi_awareness
from .wowprocess import WowProcess

__all__ = [
    "PixelClassifier", "TanClassifier", "SearchBobberFinder",
    "PositionBiteWatcher", "TimedAction", "FishingBot",
    "WowScreen", "FakeGrabber", "WowProcess",
    "enable_dpi_awareness", "VK_SPACE", "VK_4", "VK_5",
]
