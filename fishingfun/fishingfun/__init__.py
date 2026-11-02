"""FishingFun Python 移植（严格 1:1 对应 julianperrott/FishingFun 的 8 个 C# 文件）"""

# 版本号：写进日志开头，一眼就能判定跑的是哪一版（避免"以为更新了其实没更新"）
__version__ = "v49"
BUILD = (
    "2026-11-02 v49 修「重抛继续却停机」+ 咬钩识别补一条路 + 门槛自适应:"
    "①【停机根因】run.py 的 _on_no_bobber 在「本竿锁定过浮标」分支里打印"
    "「重抛继续」后，下一行照样执行 is_enabled=False，主循环 while 立刻退出，"
    "必打 Bot has Stopped（真机 08:56:33 即此）。现区分「软停/硬停」："
    "软停只置 _soft_stop，由 start() 循环末尾清零并继续下一竿；"
    "硬停（用户退出 / 本竿从未锁定浮标）才真正结束；"
    "②【reset 漏洞】SearchBobberFinder.reset() 原来不清 no_bobber_frames，"
    "上一竿凑满 3 帧停机后计数器仍是 3，新竿第一帧再 miss 就立刻又到 3 —— "
    "即使不退出也会重抛后秒停。现在 reset() 一并清零；"
    "③【标定期免判】首竿 17 秒标定期间通过 _set_calibrating() 屏蔽 abort，"
    "这期间浮标被咬走/离场属正常，不再被当成故障停机；"
    "④【咬钩新判据】新增 area_decayed()：浮标丢失前若面积在渐进缩小"
    "（最近 1.2s 内 >=3 个样本、末/首 <=0.72、整体不回升），判为鱼把浮标"
    "拉入水面 → 立刻提竿，不再当「目标消失」重抛。真机序列 457→400→313→消失 "
    "正好命中（313/457=0.69）；一步消失/面积反弹/缩小不足 28% 均不误判；"
    "⑤【门槛自适应】锚点亮团像素门槛从固定 90 改为 "
    "clamp(浮标指纹面积x0.35, 90, 指纹面积)：指纹 720→251、457→159、"
    "无指纹或指纹过小时退回 90，换分辨率不再随机误拒；"
    "⑥回归：衰减判别 5/5、自适应门槛 6/6、主循环软硬停 2/2、"
    "v48 亮团闸门 10/10 全部通过。"
    "———— v48 ————:"
    "2026-11-01 v48 判据去色相化（浮标颜色随魔兽光线变化）:"
    "①锚点闸门 _anchor_has_bobber() 从「邻域暖色 >=20px」改为「局部高对比亮团」："
    "dev=亮度-大尺度局部背景>30 的像素数>=90、峰值>=70、占比>=3%、"
    "亮团质心离锚点<=0.8x半径。暖色像素降级为日志参考项，不再参与通过/拒绝；"
    "②邻域半径 20->30（浮标本体实测 31x23，40x40 窗口装不下浮标+抖动）；"
    "③检测主路径 _find_bobber_by_diff 判据改为「暖色 ∪ 亮团」并集，"
    "bobber_bright_enabled 默认开启（此前为关，等于只有暖色一条路）；"
    "④真机帧离线回归 10/10：真浮标 772~800px 必过、"
    "最亮水面反光仅 59px 必拒，分离度 13倍；"
    "⑤光照鲁棒性验证 7 种变换（全蓝化/去饱和/压暗50%/提亮30%/R-B互换/黄变青）全部通过，"
    "同条件下旧暖色判据在「全蓝化」「去饱和」时浮标暖色像素=0 → 直接误拒真浮标。"
    "———— v47 ————:"
    "三帧确认锚点（每竿都按首竿标定标准来）:"
    "①新增 _confirm_anchor_3frames()：任何候选锚点必须连续 3 帧都检测到、"
    "三帧位置跨度 <=28px、且每帧都过闸门，才允许定为本竿锚点；"
    "②差分标定 / 跟踪期复用 / 现场检测 三条路径全部接入该确认，"
    "单帧命中一律只当候选，不再直接定锚；"
    "③确认迟迟不成立时自动延长等待（每次 +2s），不轻易判重抛；"
    "④离线回归 5 场景全通过：干净三帧✔ 幽灵+真浮标✔ 单帧命中后全丢✘(正确拒绝) "
    "抖动 3 帧✔ 漂移假目标✔。"
    "———— v46 ————:"
    "锚点绝不跨竿 + 锚点必须真有浮标:"
    "①删除 v44「标定期锚点直接复用」捷径——第一竿只标定浮标特征/咬钩特征，"
    "从第二竿起浮标落点每竿随机，锚点一律现场重新判定，绝不跨竿搬运；"
    "②新增锚点闸门 _anchor_has_bobber()：任何来源（差分标定/跟踪期复用/"
    "实时检测）的锚点，落地处邻域必须真有浮标特征，否则一律拒绝。"
    "（判据在 v48 由「暖色 >=20px」改为「局部高对比亮团」，见 v48 条目）"
    "真机回溯验证 17/17 帧：真浮标 (606,236) 全放行、幽灵锚点 (429,350) 全拒绝；"
    "③日志坐标标注统一为「屏幕 x,y | 局部 x,y」，杜绝把局部坐标误读成屏幕坐标；"
    "④实时检测结果过不了闸门时按「本帧未命中」处理并继续扫，不再锁定错目标。"
)

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
