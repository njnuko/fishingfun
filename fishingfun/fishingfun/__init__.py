"""FishingFun Python 移植（严格 1:1 对应 julianperrott/FishingFun 的 8 个 C# 文件）"""

# 版本号：写进日志开头，一眼就能判定跑的是哪一版（避免"以为更新了其实没更新"）
__version__ = "v40"
BUILD = ("2026-11-01 v40 真机回归验证:①run.py --anchor-wait 默认 1.5→3.3"
         "(v39 只加了自动抬高逻辑，但 CLI 传入的 1.5 仍压住它，日志实测 anchor_wait=1.5 未生效) "
         "②地平线 <2% 行高判 UI 硬边(y=0~10 实测为纯色蓝(40,140,255)与黄(255,220,0)边框) "
         "③三批次 50 帧离线回归: 20:06 命中11/16(y 334~343 跨9px) "
         "20:22 命中11/17(y 307~310 跨3px) 20:42 命中17/17(y 280~285 跨5px)"
         "④后段未命中帧实测暖色像素=0(浮标确已消失)，判定正确非漏检")

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
