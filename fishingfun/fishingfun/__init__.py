"""FishingFun Python 移植（严格 1:1 对应 julianperrott/FishingFun 的 8 个 C# 文件）"""

# 版本号：写进日志开头，一眼就能判定跑的是哪一版（避免"以为更新了其实没更新"）
__version__ = "v37"
BUILD = "2026-11-01 定锚+跟踪+抖动三修:①飞行期不锁锚点(修(701,24)残影误锁) ②色差锚点粘住不被TAN顶掉(修偏670px追水面) ③咬钩阈值按实测抖动自适应+P25水位基线+要求持续下沉(修±5px波纹误判)"

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
