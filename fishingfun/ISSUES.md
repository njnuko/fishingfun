# FishingFun 问题清单与修复方案

> 最初分析：2026-11-01（针对 v42） · 最后更新：2026-11-01（v48）

---

## 零、版本演进与问题状态总表

| 编号 | 问题 | 首次提出 | 状态 | 落版本 |
|---|---|---|---|---|
| #1 | `_tracked_anchor` 屏幕坐标塞进 `previous_location`（局部坐标） | v42 | ⚠️ 确认是坐标系混用，但**不是停机主因**（`single_frame=True` 走全图扫描，绕开 previous_location） | v43 修 |
| #2 | `_watch` 尾部「等黄浮标」循环不写 `positions` | v42 | ✅ 已修 | v43 |
| #3 | `stable == last` 时不打日志 | v42 | ✅ 已修 | v43 |
| #4 | 众数判定 `cnt >= max(5, len*0.45)` 静默返回 None | v42 | ✅ 阈值收紧为 `frac>=0.45 且 span<=60` | v43 |
| **#5** | **锚点闸门用「暖色像素数」，浮标颜色随魔兽光线变化 → 真浮标被误拒** | v46 引入 / v48 暴露 | ✅ **已修** | **v48** |
| #6 | 锚点跨竿复用（每竿落点随机，必须现场重判） | v44 | ✅ 已修 | v46 |
| #7 | 单帧命中就直接定锚，无一致性校验 | v46 | ✅ 已修（三帧确认，跨度 ≤28px） | v47 |
| #8 | 亮团判据默认关闭，只当暖色全灭后的备选 | v47 | ✅ 已修（默认开 + 取并集） | v48 |
| #9 | 咬钩判定只看 y 位移阈值，大鱼拉扯力度变化可能偏 | 待观察 | ⬜ 未改动 | — |
| #10 | 强反光钓点，白色浪花/飞鸟倒影可能落入 59~90px 灰区 | v48 观察项 | ⬜ 尚无真机样本 | — |

**v48 视角的根因链条**：浮标随光照变色 → 暖色判据在冷色/去饱和光线下等于 0 px → 闸门把真浮标判成「该处没有浮标」→ 锚点锁定失败 → 重抛或停机。v48 换掉这条判据，链条断开。

### #5 详解：为什么「暖色」根本不能当判据

实测同一张真机区域帧（1280×801）：

| 度量 | 真浮标邻域 | 空白水面（有波纹反光） | 水面最强反光 |
|---|---|---|---|
| 暖色像素（R−B>20 且 R>80） | 149 | **129** | 0 |
| `dev>30` 亮团像素 | 720 | 22 | 56 |

暖色判据在「有浮标」和「水面有反光」之间只差 20 px，而判据门槛是 20 —— 等于没有区分能力。
再做光照变换，冷光全蓝化 / 去饱和成白灰时，真浮标的暖色像素直接变成 **0**，闸门 100% 拒绝真浮标。
这正是用户说的「魔兽里面会随着光线的不同，浮标的色调也会看上去不一样」。

亮团判据（`dev = 亮度 − 大尺度局部背景`）在同一张图上给出 720 vs 56，13 倍分离，且冷/暖/白/灰浮标全部适用。
完整数据与 7 种光照变换矩阵见 **V48_CHANGELOG.md**。

---

## 一、结论先行

**只有一个致命 bug，但它是系统性的坐标系混用，导致整条「稳定锚点复用」链路全废。**

| 严重度 | 编号 | 问题 | 影响 |
|---|---|---|---|
| 🔴 P0 | #1 | `_tracked_anchor` 存屏幕坐标，却被塞进 `previous_location`（局部坐标） | 每竿必失败 → 停机 |
| 🟡 P1 | #2 | `_watch` 尾部「等黄浮标」循环不写 `positions` | 众数结果不可靠（本次无影响，但埋雷） |
| 🟡 P1 | #3 | `stable == last` 时不打日志 | 无法从日志判断众数是否生效 |
| 🟢 P2 | #4 | 众数判定条件 `cnt >= max(5, len*0.45)` 在命中率低时会静默返回 None | 诊断困难 |
| 🟢 P2 | #5 | `_last_detect_note` / `_last_track_stable` 等状态散落在实例上 | 可维护性差 |
| 🟢 P3 | #6 | 诊断图目录无限增长 | 磁盘占用 |

---

## 二、P0 详解：#1 坐标系混用

### 现象（v42 日志原文）

```
21:04:21,111 抛竿后跟踪 2.0s: 41 帧, 命中 33 帧, 末位置 (1258, 767)
21:04:21,111 跟踪期锚点保留: (1258, 767)（命中 33/41 帧，标定后直接复用）
21:04:37,009 跟踪期锚点 (1258, 767) 现场验证失败（标定期间浮标已移动），退回重新找锚点
21:04:37,010 差分标定未命中，退回逐帧颜色搜索
21:04:37,048 色差检未命中（1/3），不回落到原版搜索 -> None
21:04:37,089 色差检未命中（2/3），不回落到原版搜索 -> None
21:04:37,132 色差检未命中（3/3），不回落到原版搜索 -> None
21:04:37,133 ⚠️ 连续 3 帧找不到浮标 —— 本竿从未锁定浮标
21:04:37,243 Bot has Stopped.
```

### 关键发现：`(1258, 767)` **本身是正确的**

```
截屏区: 640,354  尺寸 1280x801
色差命中局部坐标: (618, 413)
屏幕坐标 = (618+640, 413+354) = (1258, 767)   ✅ 完全吻合
```

日志前 53 行全是 `色差浮标命中: (618,413)`，连续 35 帧，**说明真浮标一直在那里，`_watch` 拿到的位置也是对的**。

### 真正的 bug 在 `_wait_for_bite()` 第 680 行

```python
tracked = getattr(self, "_tracked_anchor", None)   # ← 屏幕坐标 (1258,767)
if tracked is not None:
    self.bobber_finder.previous_location = tracked  # ❌ 塞进了局部坐标字段
    _probe = self.bobber_finder.find()              # 必然失败
```

`previous_location` 在整个 `SearchBobberFinder` 内部**全程是局部坐标**：

- `_find_bobber_by_diff` 赋值：`self.previous_location = yb`（yb 是局部坐标）
- `_calibrate` 赋值：`self.previous_location = pos`（局部）
- `_find_points` 使用：`min_x = self.previous_location[0] - r`，`r = max(40, ...)`，直接当像素窗口用
- `find()` 返回时才 `_to_screen()` 转成屏幕坐标

把 `(1258,767)` 当局部坐标用，而截屏区只有 **1280×801**：

- `_find_points` 的窗口变成 `x∈[1218, 1280]`、`y∈[727, 801]` —— 右下角一小块
- 真浮标在局部 `(618,413)`，**根本不在窗口内**
- 色差检那条路径 `single_frame=True` 也不看 `previous_location`，但 `_diff_anchor` 还是旧的，且 `_diff_hits` 被重置
- 结果：`find()` 连续返回 None

### 失败的连锁反应

```
previous_location = None（清空，正确）
  ↓
退回「逐帧颜色搜索」找锚点
  ↓
但锚点搜索的 anchor_wait 只有 3.3s，且此时 _diff_hits 已被重置
  ↓
色差检需要「同位置连续 3 帧」确认
  ↓
3 帧 miss → no_bobber_frames 达到上限 3
  ↓
误判「本竿从未锁定浮标」→ 停机
```

**注意最后那句文案是错的**：日志明确显示之前锁定了 33/41 帧。`_anchor_locked_this_cast` 因为走的是 `tracked` 分支失败路径，从未被置 True。

### 修复方案

**方案 A（推荐）：在 `_wait_for_bite` 里转换坐标**

```python
tracked = getattr(self, "_tracked_anchor", None)
if tracked is not None:
    # _tracked_anchor 是 find() 的返回值 = 屏幕坐标；
    # previous_location 必须是局部坐标，转回来再塞
    try:
        l, t, _cw, _ch = self.bobber_finder.grabber.search_area()
        prev_local = (int(tracked[0]) - l, int(tracked[1]) - t)
    except Exception:
        prev_local = None
    if prev_local is not None:
        w = h = None
        try:
            _b = self.bobber_finder.grabber.grab()
            h, w = _b.shape[0], _b.shape[1]
        except Exception:
            pass
        if w and (prev_local[0] < 0 or prev_local[0] >= w
                  or prev_local[1] < 0 or prev_local[1] >= h):
            self.log("跟踪期锚点 %s 换算局部坐标后越界，丢弃", tracked)
            prev_local = None
    if prev_local is not None:
        self.bobber_finder.previous_location = prev_local
        _probe = self.bobber_finder.find()
        ...
```

**方案 B（更稳，改动更小）：让 `_tracked_anchor` 从一开始就存局部坐标**

`_watch` 里不要存 `find()` 的返回值，改存 `bobber_finder.previous_location`：

```python
# _watch 里，替换 self._last_track_stable
stable_local = self.bobber_finder.previous_location   # 局部坐标
self._last_track_stable = stable_local
```

但这有个前提：`_watch` 结束时 `previous_location` 必须还是有效值。由于 `_watch` 里 `no_anchor_lock=True` 会把它设成 None，需要额外记录。

**我推荐方案 A**，改动局部、语义清晰，且能加越界检查兜底。

### 附加修复

`_anchor_locked_this_cast` 应该在 `_watch` 里就置 True（只要有 >10 帧命中），而不是等到 `_verify_and_watch`：

```python
# _watch 末尾
if hits >= 10:
    self._anchor_locked_this_cast = True
```

这样即使后续失败，停机文案也会准确提示「锁定后丢失」而不是「从未锁定」。

---

## 三、P1 详解

### #2 `_watch` 尾部循环不写 `positions`

```python
while post_bmp is None and time.monotonic() < post_deadline:
    if last is not None:          # ← 一旦 last 非空就直接 break
        break
    pos = self.bobber_finder.find()
    if pos is not None:
        last = pos                # ❌ 没写进 positions
```

**实际影响**：因为 `if last is not None` 会立即 break，这个循环在正常情况下一帧都不会执行。**本次日志未触发**。

**风险**：如果 `_watch` 主循环一次都没命中（`last is None`），进入这个循环后拿到的位置不会进 `positions`，但 `last` 有值了 → 众数仍返回 None → `_tracked_anchor` 为 None → 走原来的找锚点流程。**后果是退化成 v41 之前的行为，不会更糟，但也不会有改善**。

**修复**：把 `positions.append(pos)` 补上，保持一致。

### #3 `stable == last` 时不打日志

```python
if stable != last:
    self.log("追踪稳定位: %s（最大簇 %d/%d 帧，跨度 %dpx）→ 覆盖末位置 %s", ...)
```

本次日志里 `stable` 就是 `(1258,767)`，和 `last` 相同，所以这行没打。**逻辑上没错，但诊断时看不出来众数到底跑没跑**。

**修复**：改成无条件打日志，只把「是否覆盖」作为后缀：

```python
if stable is not None:
    self.log("追踪稳定位: %s（最大簇 %d/%d 帧，跨度 %dpx）%s",
             stable, cnt, len(positions), span,
             "" if stable == last else "→ 覆盖末位置 %s" % (last,))
else:
    self.log("追踪位不稳（最大簇 %d/%d=%.0f%%，跨度 %dpx），不提供跟踪期锚点", ...)
```

### #4 众数判定条件过于严格

```python
if cnt >= max(5, int(len(positions) * 0.45)) and span <= 60:
```

- `len(positions)` 只统计**命中帧**，不统计总帧数
- 33 命中 / 41 总帧 → `positions=33`，需要 `cnt >= 14`
- 实际最大簇 33，条件满足
- 但如果命中分散（比如两簇各 16、17），`cnt=17 < int(33*0.45)=14` → **刚好通过**，但跨度可能 > 60 → 返回 None

**问题**：条件满足/不满足的边界很模糊，日志不够明确。

**修复**：把判定拆成两行日志，明确说明哪条没过。

---

## 四、P2/P3 说明

### #5 状态散落

`_last_track` / `_last_track_stable` / `_tracked_anchor` / `_anchor_locked_this_cast` / `_sink_calibrated` / `_last_cast_t` / `_last_beat` 全部挂在 `self` 上，无类型标注、无初始化声明。

**不影响功能**，但每次加新状态都要用 `getattr(self, "xxx", default)` 兜底，说明初始化不全。

**建议**：在 `__init__` 里统一声明，用 `dataclass` 或至少全部显式初始化。

### #6 诊断图目录无限增长

日志开头：`诊断截图目录: C:\fishingfun\diag`

每次失败/标定都会落盘 BMP（1280×801 24bpp ≈ 3MB/张），多跑几小时就是几个 GB。

**建议**：加轮转（保留最近 N 个）或 `--no-diag` 开关。

---

## 五、v42 时期的遗留疑点

日志第 97-98 行：

```
21:04:35,614 ⚠️ 地平线候选 y=9 过于贴近截图区顶边（< 20），判定为窗口 UI 硬边，本次不排除天空侧
21:04:35,615 排除区: 地平线未测出/不可信，上方不排除（左右 14%、下方 25% 仍生效）
```

**这条是正确行为**（v41 加的边框剔除起了作用），但它意味着**本次运行 `sky_limit_y = None`**，即**天空侧没有排除**。

结合 `(1258,767)` 在局部坐标 `y=413`（截屏区高 801，下边界 601），`413 < 601` 所以仍然通过下方排除。**本次没有造成误判**，但如果浮标出现在天空区域，就会误抓。

**这不是 bug，是设计退化**：地平线检测失败时静默降级，用户无法从日志判断"这次排除区是不是完整的"。

---

## 六、修复优先级与工作量

| 优先级 | 问题 | 改动量 | 风险 |
|---|---|---|---|
| 1 | #1 坐标转换 | ~20 行，`_wait_for_bite` | 低（有越界兜底） |
| 2 | #1b `_anchor_locked_this_cast` 提前置位 | 3 行，`_watch` | 极低 |
| 3 | #3 众数日志无条件输出 | 5 行，`_watch` | 极低 |
| 4 | #2 `positions.append` 补齐 | 1 行 | 极低 |
| 5 | #4 判定日志拆分 | 8 行，`_watch` | 极低 |
| 6 | #5 状态初始化统一 | ~15 行，`__init__` | 中（要核对所有 getattr 默认值） |
| 7 | #6 诊断目录轮转 | ~10 行，`run.py` | 低 |

**建议 1-5 一起出 v43，6-7 留到 v44。**

---

## 七、v43 修改清单（历史记录，已落地）

> 下面是 v43 当时的落地清单，保留作追溯用。v44~v48 的改动分别见各自 BUILD 说明与 V48_CHANGELOG.md。

### 改动 1：`fishbot.py` `_watch()` 末尾

```python
# 原
self._last_track = (frames, hits)
self._last_track_stable = stable

# 改
self._last_track = (frames, hits)
self._last_track_stable = stable
if hits >= 10:
    self._anchor_locked_this_cast = True
```

### 改动 2：`_watch()` 尾部等黄浮标循环

```python
# 原
if pos is not None:
    last = pos

# 改
if pos is not None:
    last = pos
    positions.append(pos)
```

### 改动 3：`_watch()` 众数日志（替换整个 if/else 块）

```python
stable = None
if positions:
    # ... 聚类不变 ...
    clusters.sort(key=len, reverse=True)
    best = clusters[0]
    cnt = len(best)
    xs = [p[0] for p in best]
    ys = [p[1] for p in best]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    need = max(5, int(len(positions) * 0.45))
    if cnt >= need and span <= 60:
        stable = (int(sum(xs) / float(cnt)), int(sum(ys) / float(cnt)))
        self.log("追踪稳定位: %s（最大簇 %d/%d 帧，需≥%d，跨度 %dpx）%s",
                 stable, cnt, len(positions), need, span,
                 "" if stable == last else " → 覆盖末位置 %s" % (last,))
    else:
        self.log("追踪位不稳: 最大簇 %d/%d 帧（需≥%d），跨度 %dpx（需≤60）"
                 " → 不提供跟踪期锚点",
                 cnt, len(positions), need, span)
```

### 改动 4：`_wait_for_bite()` 复用锚点前转换坐标

```python
tracked = getattr(self, "_tracked_anchor", None)
if tracked is not None:
    prev_local = None
    try:
        # _tracked_anchor 是 find() 返回值 = 屏幕坐标
        # previous_location 必须是截屏区局部坐标
        g = getattr(self.bobber_finder, "grabber", None)
        _l, _t, _cw, _ch = g.search_area()
        _lx, _ly = int(tracked[0]) - _l, int(tracked[1]) - _t
        _b = g.grab()
        _h, _w = _b.shape[0], _b.shape[1]
        if 0 <= _lx < _w and 0 <= _ly < _h:
            prev_local = (_lx, _ly)
        else:
            self.log("跟踪期锚点 %s → 局部 %s 越界（截屏区 %dx%d），丢弃",
                     tracked, (_lx, _ly), _w, _h)
    except Exception as e:
        self.log("跟踪期锚点坐标换算失败 %s，丢弃", e)

    if prev_local is not None:
        try:
            self.bobber_finder.previous_location = prev_local
            _probe = self.bobber_finder.find()
            if _probe is None:
                self.log("跟踪期锚点 %s（局部 %s）现场验证失败，退回重新找锚点",
                         tracked, prev_local)
                self.bobber_finder.previous_location = None
            else:
                self.bite_watcher.reset(_probe)
                self.log("浮标位置: %s（复用跟踪期锚点 %s，跳过重新找）",
                         _probe, tracked)
                self._verify_and_watch(_probe)
                return
        except Exception as e:
            self.log("复用跟踪期锚点失败 %s，退回重新找锚点", e)
            self.bobber_finder.previous_location = None
    else:
        self.bobber_finder.previous_location = None
```

---

## 八、验证方法

改完后**不要只看"能不能跑"**。用这份 checklist 判断：

### 成功标志（日志必须出现）

1. `追踪稳定位: (x, y)（最大簇 N/M 帧，需≥K，跨度 Spx）` ← 无条件出现
2. `浮标位置: (x,y)（复用跟踪期锚点 (x,y)，跳过重新找）` ← 走复用路径
3. 之后进入 `稳定期 2.0s 内不判咬钩` → `监控中: 浮标=...`
4. **不再出现** `跟踪期锚点 (...) 现场验证失败`

### 失败时的诊断线索

| 日志 | 说明 |
|---|---|
| `→ 局部 (a,b) 越界` | 坐标换算仍有问题，检查 `search_area()` 偏移 |
| `现场验证失败` | 坐标换算对了但浮标真的动了（标定 17 秒太长？） |
| `追踪位不稳` | 命中位置太分散，需要看 `末位置` 和 `追踪稳定位` 差多少 |
| 又出现 `本竿从未锁定浮标` | `_anchor_locked_this_cast` 改动没生效 |
