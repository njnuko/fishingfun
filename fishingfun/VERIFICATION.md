# FishingFun v42 真机验证报告（图片 + 日志交叉取证）

> 数据来源：`fishingfun.log`（21:04:15~21:04:37）+ `debug.zip`（17 对全屏/区域 BMP）
> 验证时间：2026-11-01

---

## 一、重要更正

我在第一版分析里断定「`_tracked_anchor` 存屏幕坐标导致搜索窗错位」是**唯一真根因**。
**用 debug.zip 实际图片复现后，这个结论被推翻了。**

实测：把 `previous_location` 设成屏幕坐标 `(1258,767)`，`find()` **照样命中**。
原因：`previous_location` 非空时走的是

```python
if self.previous_location is not None and getattr(self, "diff_track_enabled", True):
    yb = self._find_bobber_by_diff(bmp, single_frame=True)   # ← 全图扫描，不看 previous_location
```

`single_frame=True` 绕开了 ±40px 窗口和 3 帧确认，**直接全图找**。
所以 `previous_location` 填错坐标**不影响跟踪**。

坐标系确实是混用的（这点没错），但它**不是**这次停机的原因。

---

## 二、图片实测结果（关键证据）

### 2.1 坐标系确认

| 项目 | 数值 |
|---|---|
| 截屏区（锁定后） | `1280x801 @ 640,354` |
| 区域图里浮标局部质心 | `(618,413)`（17 帧中 16 帧稳定在此，±2px） |
| 浮标 RGB | `[214,180,134]` 米黄 |
| 全屏图 `(1258,767)` 附近暖色 | 1100 px，质心 `(1259,768)` |
| `(1259-640, 768-354)` | **`(619,414)`** ✅ 与区域图完全吻合 |

**结论**：`(1258,767)` 是正确的屏幕坐标，`(618,413)` 是对应的局部坐标。
日志里「末位置 (1258,767)」本身没算错。

### 2.2 v42 错误窗口其实是空的

```
窗口 x[1218,1280] y[727,801]（把屏幕坐标当局部坐标时）
尺寸 62x74，暖色像素 = 0
真浮标局部 (618,413) 在窗口内？ False
```

这个窗口确实抓不到浮标——**但因为 `single_frame=True` 根本不看这个窗口，所以它不影响结果**。

### 2.3 帧 07（t=6.0s）浮标消失了

| 帧 | 浮标区暖色 px | 该区域 mean RGB | 判定 |
|---|---|---|---|
| 01 | 1147 | `[60.9, 64.2, 67.2]` | 正常 |
| 06 | 1124 | — | 正常 |
| **07** | **0** | **`[33.9, 47.5, 62.0]`** | **浮标不见了，水面变暗** |
| 08 | 1088 | `[64.2, 67.5, 71.2]` | 恢复 |
| 17 | 1139 | `[58.2, 61.7, 64.6]` | 正常 |

帧 07 整幅只有 44 px 暖色，集中在 `(549,401)` 附近，RGB `[83,35,15]` **深红褐色**
——那是水花或鱼线，**不是浮标**（浮标是 `[214,180,134]` 米黄）。

---

## 三、真机失败的准确机制

### 3.1 用真实帧复现（场景 B）

连续喂：帧15 → 帧16 → **帧07（消失）** → 帧17

```
帧1  find()=None        miss=1   色差候选 (617,415) 连续 1/3，等确认
帧2  find()=None        miss=2   色差候选 (618,412) 连续 2/3，等确认
帧3  find()=None        miss=3   色差检未命中（3/3）      ← 浮标消失
     >>> ABORT(3)
```

**与真机日志 L107-109 完全一致。**

### 3.2 完整因果链

```
① 第一竿 _watch 跟踪 2 秒，稳定命中 (618,413)/屏幕(1258,767)，33/41 帧
   ↓
② _maybe_calibrate_sink() 跑 17 秒标定
   期间 t=6.0s 浮标被咬走（消失约 1 秒）
   ↓  标定模块丢掉这段，建议 strike=6
③ 标定结束，_wait_for_bite() 用 _tracked_anchor=(1258,767) 验一帧
   ↓  previous_location 非空 → _find_bobber_by_diff(single_frame=True) 全图扫描
   ↓  这一帧浮标还没回来（或刚好又消失）→ None
   ↓  日志："跟踪期锚点 (1258,767) 现场验证失败"
   ↓  previous_location = None
④ 退回逐帧颜色搜索，anchor_wait=3.3s
   但每帧 find() 要 ~40ms，3.3s 只够跑 3 帧（日志 L115: 帧=3）
   ↓
⑤ 这 3 帧里第 3 帧又丢目标 → _diff_hits 归零 → miss 累加到 3
   ↓
⑥ abort_cb 触发 → "连续 3 帧找不到浮标 —— 本竿从未锁定浮标" → 停机
```

### 3.3 真正的设计缺陷（两个）

#### 缺陷 A：`diff_confirm_frames=3` 与 `no_bobber_limit=3` 数值相等

```python
# searchbobberfinder.py L137
self.no_bobber_frames += 1        # 第 1、2 帧（等确认）都计 miss
if self._diff_hits < cf:          # cf=3
    return None
```

- 正常情况：帧1 miss=1 → 帧2 miss=2 → 帧3 命中，清零 ✅
- **任意一帧丢目标**：确认链打断，`_diff_hits` 归零重来，但 `no_bobber_frames` **只增不减**
- 结果：3 帧内只要有一次丢目标，必然 abort

日志 L56 显示 `_watch` 命中 33/41 帧（80%），
**意味着每 5 帧就有 1 帧丢目标** —— 3 帧窗口内撞上的概率约 50%。

#### 缺陷 B：`anchor_wait=3.3s` 只够 3 帧

```
L115: DIAG找锚点 退出耗时=0.12s 帧=3 miss(落水后)=0 anchor_wait=3.3 飞行期=1.8s
```

`退出耗时=0.12s` 但 `miss=0`、`帧=3` —— 说明这 3 帧是**在飞行期 1.8s 内**跑的，
飞行期不计 miss，但 `_diff_hits` 照样在累 / 被重置。

日志说 `原因=anchor_wait超时`，但实际只用了 0.12s。
**`miss(落水后)=0` 与 abort 自相矛盾** —— abort 是 `find()` 内部触发的（`no_bobber_frames=3`），
而 `_wait_for_bite` 的 `miss` 变量是另一套计数，两者不通信。

这是**两套独立计数 + abort 优先级错误**：`find()` 内部的 abort 抢在 `_wait_for_bite` 的重试逻辑之前把 Bot 打停了。

#### 缺陷 C（标定）：把「消失」误判成「下沉 12px」

```
L94: y 范围 413~425（跨度 12）| 相邻位移 中位 1.0 p90 1.7 最大 2@t=9.0s
L96: 最大净下沉 = 12px（本期 疑似真咬钩）
```

y=425 那个点来自帧 07（面积 176、`30x9`、位置 `(612,425)`），是**目标丢失后的噪点**，不是浮标下沉。

标定逻辑已跳过 t=6.0/t=7.0 的"跳变对"（L92-93），但 **y 序列里保留了 425 这个假值**，
导致 `最大净下沉` 和 `y 范围` 都被污染。

---

## 四、修复方案（已实现为 v43，但需按新结论调整）

### 4.1 必改（真正解决停机）

#### 修复 1：`no_bobber_limit` 必须大于 `diff_confirm_frames`，且「等确认」帧不计 miss

```python
# searchbobberfinder.py L137 附近
if getattr(self, "strict_yellow", False):
    # ★ 等确认的帧不算「找不到浮标」：
    #   它已经看见了候选，只是还没攒够 3 帧连续性。
    #   原逻辑把它计 miss，导致「确认链中断 → 立刻烧完 3 条命 → abort」。
    if self._diff_hits > 0 and self._diff_hits < getattr(self, "diff_confirm_frames", 1):
        self.log("色差候选确认中（%d/%d），不计 miss",
                 self._diff_hits, getattr(self, "diff_confirm_frames", 1))
        self.previous_location = None
        return None
    self.no_bobber_frames += 1
    ...
```

并且在 `run.py` 加校验：

```python
if args.no_bobber_frames <= finder.diff_confirm_frames:
    print("⚠️  --no-bobber-frames(%d) 必须大于 diff_confirm_frames(%d)，自动抬到 %d"
          % (args.no_bobber_frames, finder.diff_confirm_frames, finder.diff_confirm_frames + 3))
    finder.no_bobber_limit = finder.diff_confirm_frames + 3
```

#### 修复 2：`find()` 内部的 abort 不应抢在 `_wait_for_bite` 的重试之前

`_wait_for_bite` 已经有完整的重试逻辑（`anchor_wait` + `miss` 计数 + 重抛），
`find()` 内部的 `abort_cb` 是**第二套机制**，两者冲突。

**推荐**：在 `_wait_for_bite` 的找锚点循环期间，临时把 `abort_cb` 置 None：

```python
# _wait_for_bite 进入找锚点循环前
old_abort = self.bobber_finder.abort_cb
self.bobber_finder.abort_cb = None
try:
    while self.is_enabled:
        ...
finally:
    self.bobber_finder.abort_cb = old_abort
```

由 `_wait_for_bite` 自己决定何时放弃（它已经有 `anchor_wait`）。

#### 修复 3：`anchor_wait` 太短

3.3s 只跑 3 帧（每帧 ~40ms），而确认需要 3 帧连续命中。
**建议 `anchor_wait` 至少 `diff_confirm_frames × 0.1s + 1.0s` = 1.3s**，
但考虑到浮标可能正在被咬/刚恢复，实际应给 **2.5s 以上**，并在期间**不因单帧丢失而 abort**。

#### 修复 4（标定）：面积骤降的帧不计入 y 序列

在 `sinkcalib.py` 的 y 值收集处：

```python
# 面积相对前一帧掉到 <30% = 目标丢失，该帧 y 值作废
if prev_area > 0 and area < prev_area * 0.3:
    self.log("标定: t=%.1fs 面积骤降 %d→%d，判为目标丢失，y 值不计入统计", t, prev_area, area)
    continue
```

---

### 4.2 可选优化（不影响停机）

#### 修复 5：坐标系统一（预防性）

虽然 `single_frame=True` 全图扫描让 `previous_location` 填错不影响结果，
但**一旦关掉 `diff_track_enabled` 就会走 ±40px 窗口，立刻暴露**。

建议仍在 `_wait_for_bite` 里做坐标换算（v43 已加），但**不要把它当成这次停机的根因**。
它是**隐患**，不是**病因**。

#### 修复 6：日志改进

- `abort` 触发时打印 `_diff_hits`、`_diff_prev`、上一帧的 `_last_detect_note`
- `_wait_for_bite` 的 `miss` 和 `find()` 的 `no_bobber_frames` 统一为一个概念
- 标定的「疑似真咬钩」判定要区分「下沉」和「消失」

---

## 五、结论

| 问题 | 严重度 | 是否是本次停机原因 |
|---|---|---|
| 「等确认」帧计入 `no_bobber_frames` | 🔴 致命 | **是** |
| `find()` 内部 abort 抢占重试 | 🔴 致命 | **是** |
| `anchor_wait` 只够 3 帧 | 🟡 重要 | 是（放大问题） |
| 标定把消失当下沉 | 🟡 重要 | 否（但污染了 strike 值） |
| `_tracked_anchor` 坐标系混用 | 🟢 隐患 | **否**（实测不影响） |

**一句话**：浮标在 t=6.0s 被咬走消失约 1 秒 —— 这是**大鱼咬钩的正常现象**。
程序本该因此判定咬钩、收竿、重抛，却因为「确认帧计入 miss」+「abort 抢占」+「anchor_wait 太短」
三条叠加，把自己打停了，还错误地报告「本竿从未锁定浮标」。
