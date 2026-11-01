# FishingFun (Python 移植版)

魔兽世界**时光服**自动钓鱼机器人。逐行对应 [julianperrott/FishingFun](https://github.com/julianperrott/FishingFun) 的 8 个 C# 文件移植到 Python。

当前版本：**v37**（2026-11-01）

## 快速开始

```bat
cd C:\fishingfun
python run.py
```

首次运行前确认：
- 游戏窗口化（2560×1600 实测）
- 快捷键 `4` 绑定了钓鱼技能
- 浮标颜色为时光服米黄色

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--strike` | 4 | 咬钩阈值**下限**（相对水位基线下沉多少像素） |
| `--no-auto-strike` | 关 | 关闭阈值自适应（默认按实测抖动自动抬高，只抬不降） |
| `--bite-mode` | robust | 咬钩判定：robust=抗噪声 / legacy=原版中位数 |
| `--confirm-frames` | 2 | 连续多少帧下沉才确认 |
| `--vanish-frames` | 8 | 连续多少帧检测不到浮标才算咬钩（≈2秒） |
| `--settle-time` | 2.0 | 抛竿后稳定期秒数，期内只更新基准不判咬钩 |
| `--cast-flight` | 1.8 | 抛竿飞行/水花期，期内扫不到浮标不计数、不锁定 |
| `--diff-threshold` | — | 色差浮标检测阈值（R-B 色差） |

## v37 修复的三个核心问题

### ① 抛竿后误锁残影

日志里出现过抛竿后 **53 毫秒** 就锁定 `(701,24)` 的情况。那块 rgb=(29,48,65)、`R-B = -31`，按色差判据根本不可能被选中 —— 是 TAN 路径在「浮标还在空中」的空画面里凑出来的候选。

**修复**：`elapsed < cast_flight` 期间只检测、不写锚点。

### ② 已锁定的正确锚点被 TAN 顶掉

`searchbobberfinder.py` 原本无条件执行 `self.previous_location = None`，然后重新用 `_find_points` 的 TAN 色匹配赋值。

实测后果：锚点正确锁在浮标 `(820,343)`，下一帧被顶成 `(149,78)` —— **偏了 670 像素，追的是水面**。之后全程追水面，收竿时 `y=None`。

**修复**：色差锚点粘住不被顶掉；TAN 结果偏离 >40px 直接丢弃；后续帧优先继续用色差跟踪。

### ③ 水面抖动误判咬钩

实测平静期 y 抖动只有 **1px**（16 帧样本：341/342/343，中位 342）。但历史日志里 43/46 次误触发都是 `yDiff=-4 一分不多一分不少`，是固定阈值卡在真实抖动线上的典型症状。

**修复**：
- **P25 水位基线**（中位数会被波动带跑 —— ±5px 抖动时阈值抬到 12 仍误触发 10 次）
- **阈值自适应**：取实测抖动 P90+2，只抬不降，封顶 12
- **要求连续下沉**：后一帧必须比前一帧更深，来回晃不算

压力测试（±5px 高抖动 60 帧）：误触发 **19 → 1**（剩 1 次落在开机学习期，实际有 2 秒 settle 期挡着）。

## 验证结果

用 16 帧 2560×1600 真实游戏截图回放：

```
飞行帧1  pos=None  anchor=None     ← 不再瞎锁
飞行帧2  pos=None  anchor=None
帧03    (820,343)  ← 定锚准（与真浮标吻合）
帧04~11  (820,341)~(822,342)  ← 抖动 ±2px
帧12~16  None       ← 收竿消失，正确
```

| 测试项 | 结果 |
|---|---|
| 平静期误触发（真实帧回放） | 0 次 |
| 真咬钩必触发（模拟下沉 8px） | 第 2 帧触发 |
| ±5px 高抖动 | 误触发 19 → 1 |

## 目录结构

```
run.py                      入口，参数解析
fishingfun/
  __init__.py               版本号
  fishbot.py                主流程（抛竿→找锚点→等咬钩→拾取）
  searchbobberfinder.py     浮标定位（色差检测 + TAN 匹配）
  positionbitewatcher.py    咬钩判定（自适应阈值）
  pixelclassifier.py        像素颜色分类
  sinkcalib.py              下沉标定
  lootreader.py             拾取物读取
  nobitedetector.py         「没有鱼上钩」黄字检测
  wowscreen.py              屏幕捕获
  wowprocess.py             进程/窗口操作
  bobbercalib.py            浮标标定
  timedaction.py            定时动作
  vkeys.py                  虚拟键码
```

## 与原版的差异

原版 C# 逻辑默认保留（`--bite-mode legacy` 可切回）。所有增强都是**可选开关**，不改原版行为：

- 色差浮标检测（`--no-diff-bobber` 关闭）
- 咬钩阈值自适应（`--no-auto-strike` 关闭）
- 飞行期不锁锚点

## 已知限制

- 分辨率强相关：本版参数按 2560×1600 实测标定
- 换钓点后浮标变暗时，TAN 判据可能失效（色差判据更稳）
- `vision_analyze` 类工具对游戏画面不可靠，所有诊断均基于程序化像素分析

## License

原版 FishingFun 为 MIT License，本移植版遵循相同协议。
