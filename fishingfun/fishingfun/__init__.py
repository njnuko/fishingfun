"""FishingFun Python 移植（严格 1:1 对应 julianperrott/FishingFun 的 8 个 C# 文件）"""

# 版本号：写进日志开头，一眼就能判定跑的是哪一版（避免"以为更新了其实没更新"）
__version__ = "v38"
BUILD = "2026-11-01 v38 时间轴对齐:①标定采样零点改为按下抛竿键时刻(17秒含飞行时间，修采样窗口晚7秒导致前段全丢) ②飞行期未检出帧不计入丢失统计(避免落水前误判咬钩) ③连续3帧丢失即提前结束采样(不再空转) ④标定采样y坐标改回截屏区局部坐标+area从检测明细解析(修y偏244/area恒0) ⑤静水抖动P90与最大净下沉分开报告+给出vanish_frames建议值"

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
