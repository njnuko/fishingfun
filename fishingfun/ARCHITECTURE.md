# FishingFun Python 版 — 架构与模块说明

> 对应版本：**v48**（2026-11-01）
> 配套文档：`ISSUES.md`（问题清单与状态）、`V48_CHANGELOG.md`（v48 判据去色相化的证据与实现）

> 对应版本：v42（2026-11-01）
> 原型：julianperrott/FishingFun（C# / .NET）
> 目标平台：Windows 10/11 + 魔兽世界时光服，2560×1600 窗口化

---

## 一、总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                          run.py  （入口/装配）                     │
│  argparse 解析 → 构造各模块 → 注入依赖 → FishingBot.start()        │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FishingBot  （主状态机）                        │
│                                                                  │
│  start() 主循环：                                                 │
│   ① park_cursor  → ② press cast_key → ③ arm_screen_area          │
│   ④ _watch(2000ms)  ── 飞行期跟踪 + 抓 post 帧                   │
│   ⑤ _maybe_calibrate_sink() ── 17s 采样反推 strike_value          │
│   ⑥ _wait_for_bite()  ── 找锚点（复用 or 重找）                   │
│   ⑦ _verify_and_watch() ── 稳定期 → 咬钩监控循环                  │
│   ⑧ _loot() ── 收竿 → 双击拾取 → 读「没有鱼上钩」/ 战利品         │
└───┬────────────┬────────────┬────────────┬────────────┬──────────┘
    │            │            │            │            │
    ▼            ▼            ▼            ▼            ▼
┌────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐
│WowScreen│ │SearchBob-│ │Position- │ │SinkCal-│ │ NoBite-  │
│屏幕抓取 │ │berFinder │ │BiteWat-  │ │ibrator│ │ Detector │
│边界检测 │ │浮标定位  │ │咬钩判定  │ │阈值标定│ │黄字检测  │
└────┬───┘ └────┬─────┘ └────┬─────┘ └───┬────┘ └──────────┘
     │          │            │           │
     │          ▼            │           ▼
     │   ┌────────────┐      │    ┌────────────┐
     │   │bobbercalib │      │    │PixelClassi-│
     │   │图像算法库  │      │    │fier 颜色判据│
     │   │(差分/连通域)│      │    └────────────┘
     │   └────────────┘      │
     │                       │
     ▼                       ▼
┌────────────┐        ┌────────────┐
│ WowProcess │        │ LootReader │
│按键/鼠标   │        │战利品识别  │
└────────────┘        └────────────┘
```

### 数据流（一竿的完整生命周期）

```
按下 4 键
   │
   ├─ _grab_calib_pre()         抓「抛竿前」帧（水面上没有浮标）
   │
   ├─ _watch(2000ms)            每帧 find()，记录命中位置序列
   │     ├─ 飞行期 (0~1.8s)：no_anchor_lock=True，只记录不写锚点
   │     └─ 落水后：色差检 3 帧确认 → 报位置
   │     产出：positions[] / last / post_bmp / _last_track_stable
   │
   ├─ _maybe_calibrate_sink()    17s 内每 1s 采一帧 y 值
   │     产出：suggested strike_value（下限 6，上限 10）
   │
   ├─ _wait_for_bite()           拿锚点
   │     ├─ 优先：_tracked_anchor（_watch 稳定位）→ 现场验一帧
   │     └─ 回退：差分标定 _calibrate() → 逐帧颜色搜索（anchor_wait）
   │     产出：bobber_position（屏幕坐标）
   │
   ├─ _verify_and_watch()        可信度检查 → 稳定期 2s → 监控循环
   │     ├─ current is None 连续 8 帧 → 判咬钩（vanish）
   │     └─ y 下沉 ≥ strike 且连续 2 帧递增 → 判咬钩
   │
   └─ _loot()                    收竿 → 双击 → 读屏确认结果
```

---

## 二、模块逐个说明

### 1. `run.py` — 入口与装配

| 项 | 内容 |
|---|---|
| 职责 | 解析命令行、构造所有对象、注入依赖、启动主循环 |
| 关键点 | `sys.path.insert` 保证 `fishingfun` 包可导入；`enable_dpi_awareness()` 必须在取屏尺寸**之前**调用 |
| 输出 | 所有日志 + 启动横幅 `=== FishingFun vNN ===` |

主要参数（默认值）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--cast-key` | `4` | 抛竿键 |
| `--ten-min-key` | `F` | 防挂机宏键（空=不用） |
| `--mode` | `tan` | 浮标色系：tan / red / blue |
| `--strike` | `4` | 咬钩阈值**下限**（标定后会被抬高到 6~10） |
| `--confirm-frames` | `2` | 连续几帧下沉确认 |
| `--vanish-frames` | `8` | 连续几帧消失算咬钩 |
| `--settle-time` | `2.0` | 抛竿后稳定期，期内不判咬钩 |
| `--cast-flight` | `1.8` | 飞行/水花期 |
| `--anchor-wait` | `3.3` | 找锚点超时（会自动抬到 flight+1.5） |
| `--anchor-ratio-min` | `0.35` | 锚点可信度门槛 |

---

### 2. `fishingfun/wowscreen.py` — 屏幕抓取与截屏区

| 项 | 内容 |
|---|---|
| 职责 | 找 WoW 窗口、算截屏区、抓帧、坐标换算 |
| 关键类 | `WowScreen` / `FakeGrabber`（离线测试） |

**截屏区算法（`search_area()`）**

- 上边界：`_detect_horizon()` 扫居中竖带逐行亮度，找「上亮下暗」最大梯度行，减 10px 余量
- 下边界：`_detect_bottom()` 找绿色钓鱼进度条上沿；失败退回 `_detect_actionbar()` 动作条顶边
- 左右：客户区宽度 1/4 起，取 1/2 宽
- **锁定机制**：`_area_armed` 为 False 时用兜底值（`h/4` ~ `h/2+100`）；第一次抛竿后 `arm_area()` 才真正测量并**永久锁定**（`_area_cache`）

> ⚠️ 这是设计上最关键的一环：每帧重测会让画面尺寸抖动，差分校准和序列定位全部失效。

**坐标系（两个！）**

| 坐标系 | 范围 | 使用者 |
|---|---|---|
| **局部坐标** | 截屏区内，左上角 (0,0) | `SearchBobberFinder` 内部一切计算（`previous_location`、`_diff_anchor`、`_find_points`） |
| **屏幕坐标** | 整个游戏窗口 | `find()` 的**返回值**、`WowProcess.click()`、`bite_watcher`、`_loot()` |

转换函数：`to_screen(x, y) → (x + left, y + top)`，其中 `(left, top)` 是截屏区左上角的屏幕坐标。

---

### 3. `fishingfun/searchbobberfinder.py` — 浮标定位（核心）

这是全项目最复杂的模块，`find()` 是一条**多级降级流水线**：

```
find(bmp)
  │
  ├─ ① _find_bobber_by_diff(bmp)          【直检，主力路径】
  │     mask = 暖色掩膜(R-B>20 且 R>80)
  │            ∪ 亮团掩膜(dev=亮度-局部背景 > 30)   ← v48 起默认开启
  │     ├─ _drop_uniform_rows()  剔除窗口边框纯色行（y=0,1 蓝 / y=9,10 黄）
  │     ├─ label_blocks()        连通域
  │     ├─ 过滤：面积 / 左右 1/7 / 下 1/4 / 天空线以上 / UI 遮罩 /
  │     │        尺寸 > 35%宽 / 长宽比 > 6 / 任一边 < 4px
  │     └─ 多帧确认：同位置连续 diff_confirm_frames(3) 帧
  │     命中且 previous_location is None → 直接返回（高置信）
  │
  ├─ ② _calibrate(bmp)                    【差分标定，首次锚定】
  │     pre 帧（抛竿前）vs post 帧（浮标已出现）→ 像素差 → 挑浮标
  │
  ├─ ③ _find_bobber_by_diff(single_frame=True)  【已有锚点时的跟踪】
  │     每帧全图扫，绕开 TAN ±40px 窗口
  │
  ├─ ④ _find_points() + _drop_excluded() + _score_points()   【原版 TAN 路径】
  │     逐行跳变找候选点 → 排除区过滤 → 打分（唯一保留）
  │     受 diff_anchor_sticky 约束：离色差锚点 > 40px 则作废
  │
  └─ 返回 _to_screen(pos)  ※ 屏幕坐标
```

**关键属性**

| 属性 | 含义 |
|---|---|
| `previous_location` | 当前锁定位置，**局部坐标**；None = 无锚点 |
| `_diff_anchor` | 色差检锁定的锚点，局部坐标，用于粘性约束 |
| `no_anchor_lock` | True = 飞行期，只记录不写锚点 |
| `suppress_abort` | True = 飞行期，找不到不计数不停机 |
| `exclude_side_ratio` | 左右排除比例（默认 1/7） |
| `exclude_bottom_ratio` | 下方排除比例（默认 0.25） |
| `sky_limit_y` | 标定测出的天空分界线，**局部坐标 y** |
| `ui_masks` | UI 遮罩矩形，**整屏比例坐标** |
| `_last_detect_note` | 本帧候选统计，供日志诊断 |
| `bobber_bright_enabled` | **v48**：亮团判据开关，默认 True |
| `bright_dev_threshold` | **v48**：亮团阈值，默认 30.0（亮度 − 大尺度局部背景） |

**★ v48 判据：为什么是「亮团」而不是「暖色」**

浮标在 WoW 里颜色随环境光变化（清晨偏冷、正午偏白、黄昏偏橙）。「R 比 B 高」不是浮标的固有属性。
真机帧实测：暖色判据在浮标处 149 px、在空白水面反光处 129 px —— 分不开；
做一次「冷光全蓝化」变换，浮标的暖色像素直接归 0，闸门误拒真浮标。

浮标唯一稳定的固有属性是：**比周围水面亮、边缘清晰成团**。
实测同一帧 `dev>30` 的像素数：浮标 720 px/峰值 126，水面最强反光 56 px/峰值 50，分离 13 倍，
且冷/暖/白/灰浮标通用。所以 v48 用亮团做主判据，暖色仅作日志参考项。
详见 `V48_CHANGELOG.md`。

**坐标坑（重点）**

- `_screen_search_area()` 返回 `(left, top, full_w, full_h)`，用来把 `ui_masks` 的比例坐标换算成局部坐标
- `_drop_excluded()` 也用 `self._bmp.shape` 推尺寸，**默认 bmp 就是截屏区**
- 唯一一次"逃出"局部坐标系的地方是 `find()` 的 `return self._to_screen(...)`

---

### 4. `fishingfun/positionbitewatcher.py` — 咬钩判定

两条独立判据，任一触发即咬钩：

| 判据 | 触发条件 | 抗噪设计 |
|---|---|---|
| **消失** | `current is None` 连续 `vanish_frames`(8) 帧 | 短丢只重置计数，不判咬钩 |
| **下沉** | `delta = y - surface_y >= effective_strike` 且连续 `confirm_frames`(2) 帧递增 | 必须"越来越深"才算；来回抖动会清零 streak |

**水面基线 `surface_y`**：取最近窗口 y 序列的 **P25**（不是中位数）。
理由：浮标静止姿态对应 y 的较小值；中位数会被 ±5px 波纹带着跑，delta 瞬间拉到 10px。

**自适应阈值 `_update_strike()`**：

```
噪声样本 ← 每帧 delta（无条件收集，>0 才收）
样本 < 8  → 用配置值
否则      → eff = clamp(max(配置值, P90 + 2), 配置值, 12)
```

关键：**只会抬高，不会降低**，封顶 12 防止漏掉小幅真咬钩。

---

### 5. `fishingfun/sinkcalib.py` — 下沉幅度标定

第一竿抛竿后自动跑一次 17 秒采样（每 1s 一帧），反推 `strike_value`。

**算法**

1. 剔除飞行期 `t < cast_flight` 的帧
2. 剔除"面积骤变"的帧对（`max/min > 2.5`，说明目标丢失/被水花打散）
3. 算相邻帧 y 位移：`med`（中位）、`p90`、`max`
4. `ripple = max(med × 1.5, p90 × 0.5)`
5. `suggested = ripple + 2`，然后夹到 **[6, 10]**
6. 若 `max_jump ≥ 10`，再限制 `suggested ≤ max_jump × 0.6`（防止阈值越过真咬钩）

**诊断输出**（都进日志）

- 静水抖动 P90 / 最大净下沉 → 判断"疑似真咬钩"还是"仅波纹"
- 最长连续丢失帧数 → 建议 `vanish_frames`

---

### 6. `fishingfun/nobitedetector.py` — 「没有鱼上钩」检测

| 项 | 内容 |
|---|---|
| 判据 | 纯黄像素 `(r>200, g>180, b<90)` |
| 命中条件 | 单行黄像素 ≥ `MIN_PX_PER_ROW`，且命中行数 ≥ `MIN_ROWS`，且横向跨度 ≥ 全屏宽 × `MIN_SPAN_RATIO` |
| 抓取区域 | 屏幕中央偏上，按 `_X_RATIO` 比例 |
| 接口 | `check()` 单次判 / `confirm_no_bite(timeout=5)` 轮询确认 / `wait_clear(timeout=4.5)` 等它消失 |

三重判据（像素数 + 行数 + 横向跨度）是为了区分"真的黄字提示"和"游戏里其他黄色 UI 元素"。

---

### 7. `fishingfun/lootreader.py` — 战利品识别

抓左下角聊天/拾取提示区（`CHAT_BOX`），逐行分析：

```
行级命中条件：黄块 ≥1 且 绿汉字块 ≥5 且 亮块 ≥9
  （"你获得了物品：[xxx]" —— "你"是黄色，中间6个汉字是绿色）
```

物品名提取（实测结构）：

```
13-34 黄图标 │ 61-195 六个绿汉字 │ 207-210 绿冒号
222-226 灰 '[' │ 228-272 灰物品名 │ 274-277 灰 ']' │ 282-287 绿句号
```

- 取冒号之后的**灰色**块
- 排除宽度 ≤ 7 的窄块（就是方括号）
- 剩下的即物品名
- 可选 `read_item_name()` 用 tesseract OCR（仅在装了 pytesseract 时可用）

---

### 8. `fishingfun/wowprocess.py` — 按键与鼠标

| 功能 | 实现 |
|---|---|
| 按键 | 两种模式：`scancode`（SendInput，默认）/ `post`（PostMessage） |
| 鼠标 | `_send_mouse` 用绝对坐标归一化 `65535` |
| `right_click_mouse(pos)` | 移动 → 右键 → 双击拾取 → 回原位 |
| `park_cursor()` | 把光标挪到屏幕右下角，避免悬停遮挡浮标 |
| `is_wow_classic()` | 按进程名判断客户端类型 |

---

### 9. `fishingfun/bobbercalib.py` — 图像算法库

纯函数集合，无状态。

| 函数 | 用途 |
|---|---|
| `sky_mask` / `sky_zone_mask` | 天空区域掩膜 |
| `yellow_mask` | 纯黄像素掩膜 |
| `estimate_shift` | 块匹配估计 pre/post 帧间位移（防镜头晃动） |
| `diff_mask(pre, post, align)` | 对齐后逐像素差 |
| `label_blocks(mask)` | **连通域标记 → `(label_image, n_labels)`，返回元组不是列表** |
| `morph_close` / `_dilate1` / `_erode1` | 形态学（实测闭运算有害，慎用） |
| `warm_mask` / `is_warm` | 暖色判据 `R-B > 20` |
| `pick_bobber(pre, post)` | 差分图里挑浮标块 |
| `signature_from(rgb)` | 生成颜色指纹，用于跨帧匹配 |

---

### 10. `fishingfun/pixelclassifier.py` — 原版颜色判据

保留 C# 原版逻辑，`mode` 可选 `tan` / `red` / `blue`。有 `TanClassifier` 子类专门处理时光服米黄浮标。

> 实际运行中这条路径基本用不上——色差检和差分标定都优先。只在 `--mode` 显式指定且其他路径全失效时兜底。

---

## 三、日志标签速查

| 日志前缀 | 来源 | 含义 |
|---|---|---|
| `色差浮标命中:` | finder | 色差检命中，坐标是**局部坐标** |
| `色差候选 ... 等确认` | finder | 多帧确认中，还没到 3 帧 |
| `色差检未命中 (n/3)` | finder | 找不到，累计计数 |
| `抛竿后跟踪 2.0s: N 帧, 命中 M 帧, 末位置 (...)` | `_watch` | 跟踪期统计，**屏幕坐标** |
| `追踪稳定位:` | `_watch` | 众数聚类结果（仅在 `stable != last` 时才打） |
| `跟踪期锚点保留:` | `start()` | 采纳 `_last_track_stable` 作为本竿锚点 |
| `标定采样 NN t=X.Xs y=YYY` | sinkcalib | 17 秒采样过程 |
| `标定建议: strike_value = N` | sinkcalib | 反推的阈值 |
| `浮标位置: (...)` | `_wait_for_bite` | 最终锚点 |
| `锚点可信度: N/M 帧 = XX%` | `_verify_and_watch` | 命中率检查 |
| `监控中: 浮标=... yDiff=...` | 监控循环 | 3 秒心跳 |
| `咬钩判定: 下沉 yDiff=...` | 监控循环 | 判到咬钩 |
| `DIAG找锚点 退出耗时=...` | `_wait_for_bite` | 找锚点失败诊断 |

---

## 四、关键设计约束（踩过的坑）

1. **截屏区必须锁定**：每帧重测边界 → 尺寸抖动 → 差分/序列定位全废。
2. **飞行期禁止定锚**：抛竿后 0~1.8s 画面是上一竿残影，抓到的是错的。
3. **色差判据优先于 TAN**：TAN 在冰冻之海钓点会命中水面，偏 670px。
4. **不做形态学闭运算**：会把浮标和 30px 外的水面反光连成一块，中心偏 45px。
5. **frame 尺寸必须一致**：所有差分/序列算法都依赖同一尺寸。
6. **窗口边框纯色行必须剔除**：y=9,10 那条 1280px 的黄条会被当成最大暖色块。
7. **两个坐标系不能混**：`previous_location` 是局部，`find()` 返回是屏幕。详见 ISSUES.md #1。
8. **判据不能依赖色相**（v48）：浮标颜色随魔兽环境光变化，暖色只是巧合。用「局部亮度高于周围水面 + 边缘清晰成团」，暖色降级为参考项。详见 V48_CHANGELOG.md。
9. **锚点绝不跨竿**（v46）：每竿落点随机，锚点必须现场重新判定，第一竿只标定特征和咬钩阈值。
10. **单帧命中不定锚**（v47）：候选必须过三帧确认（连续 3 帧检出、跨度 ≤28px、每帧过闸门）。
11. **任何锚点都要过闸门**（v46/v48）：差分标定 / 跟踪期复用 / 实时检测三条来源一视同仁，闸门拒了就按「本帧未命中」继续扫，不锁定错目标。
