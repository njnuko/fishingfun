"""差分标定：抛竿前后两张截图做差，浮标就是新出现的那个团块。

为什么不用颜色阈值：换钓点后浮标会被光照压暗（实测从 r≈230 掉到 r≈78），
任何绝对亮度门槛都会失效。差分不需要预设颜色，只依赖「浮标是按下 4 键后
才出现在水面的」这个物理事实。

流程：
    pre  按键前抓一帧（画面里只有水面，没有浮标）
    post 按键后 2 秒抓一帧（浮标已落水）
    diff = |post - pre| > 阈值 → 二值化 → 连通域 → 按几何筛 → 取色值

几何筛选（用实测的真浮标分布标定，1280 宽截图）：
    面积 30~2500 px
    长宽比 1.0~4.0
    紧凑度 (面积/bbox面积) > 0.35   ← 排除水波纹那种细长/镂空的团
    不贴画面四边 8px 以内          ← 排除镜头位移造成的整片边缘假差异

镜头位移补偿：如果画面整体平移（走路/镜头微转），差分会在所有物体边缘留
下一圈轮廓。这里用「块匹配估全局位移」再对齐 post，避免整片误检。
"""
import numpy as np

# ---- 几何筛选参数（1280 宽为基准，按实际宽度等比缩放）----
MIN_AREA = 30
MAX_AREA = 2500
MIN_RATIO = 1.0
MAX_RATIO = 4.0
MIN_FILL = 0.35          # 面积 / bbox 面积
EDGE_MARGIN = 8          # 贴边多少像素内的块直接丢弃
DIFF_TH = 28             # 单通道差值阈值，> 才算变了的像素
DIFF_MIN_CH = 2          # 至少 2 个通道同时超过阈值，滤掉亮度噪声

# ---- 黄浮标预设（用户机器实测，1280x638 截图区）----
# 实测背景（水面/天空/倒影，整屏恒定）：
#     逐行均值 rgb=(29~33, 72~121, 93~139)，r 最大只到 138
# 实测真浮标：rgb=(255,216,7) / (255,212,3)，r=255、b<=7
# 实测误锁目标（必须排除）：
#     红色 UI 元素 (204,55,55) —— b 低但 g 也低
#     暗棕杂物   (39,27,19) / (72,55,67) —— 整体暗
# 判据 r>=150 && b<=100 能把三者干净分开：
#     背景 r<=138 出局；UI 红 g=55 出局；暗杂物 r<150 出局。
YELLOW_R_MIN = 150       # 红通道下限（背景最高 138）
# ★ v38：蓝通道上限从 100 放到 175。
#   实测两个钓点的浮标色值差异极大：
#     A 钓点 rgb(255, 213, 7)   → b=7    （阈值 100 足够）
#     B 钓点 rgb(227, 190, 142) → b=142  （阈值 100 直接判死！）
#   日志证据：v38 第 83 行「post 帧: 等黄色浮标 6s 未出现」，浮标明明
#   命中 (773,280)，却被 yellow_mask 判为「没有黄浮标」，于是 _watch
#   每竿都硬等满 6 秒，连带把标定窗口从 17 秒压到 10 秒。
#   放宽到 175 仍然远低于水面/天空的蓝通道（实测 b>=180 起才大量出现），
#   r-b>=80 的强约束继续负责排除白色和灰色。
YELLOW_B_MAX = 175       # 蓝通道上限（浮标 b<=142，背景 b>=180）
YELLOW_G_MIN = 140       # 绿通道下限（浮标 g>=190，UI 红 g=55）
YELLOW_RB_MIN = 80       # r-b 下限（浮标 r-b>=85，保证是黄而非白）

# ---- 天空区域预设（用户指定：每次钓鱼前窗口上方 1/4 是天空）----
# 用途：初始化判定浮标时排除天空区，避免把天空里的云/远景/UI 当成浮标。
# 实测本机截图区（1280x638）顶部：
#     y=0   rgb=(29,72,93)   y=40  rgb=(31,89,109)
#     y=80  rgb=(32,103,122) y=120 rgb=(32,114,132)
# 都是 b 最大、g 次之、r 最小，且 r<=40、b-r>=60 —— 与黄浮标(r=255)完全反向。
SKY_MAX_R = 60           # 天空红通道上限
SKY_MIN_BR = 40          # b-r 下限（天空偏蓝，浮标 r 远大于 b）
SKY_BAND_RATIO = 0.25    # 顶部 1/4 视为天空带


def sky_mask(bmp):
    """天空掩膜：偏蓝冷色（b>g>r 且 b-r 足够大）。

    注意这只能标出「像天空的像素」，真正决定要不要排除的是
    sky_zone_mask —— 用户说的「上 1/4 是天空」是位置先验，
    比逐像素判色更可靠（阴天/黄昏时天空色会变）。
    """
    a = bmp.astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return ((b > g) & (g > r) & (r <= SKY_MAX_R) & ((b - r) >= SKY_MIN_BR))


def sky_zone_mask(shape, ratio=None):
    """位置先验：顶部 ratio 比例是天空带，返回 bool 掩膜。"""
    h, w = shape[:2]
    r = SKY_BAND_RATIO if ratio is None else ratio
    m = np.zeros((h, w), bool)
    m[:max(int(h * r), 1), :] = True
    return m


def yellow_mask(bmp):
    """黄浮标掩膜：绝对亮度 + 通道方向双约束。

    为什么不用「暖色差 r-b>=22」：暗处噪声 r=39 b=19 也满足 r-b=20 出头，
    实测会把 (39,27,19) 这种近黑块当成浮标。加绝对下限后干净得多。

    为什么不用「r>g>b 方向」单独判断：红色 UI (204,55,55) 方向也对，
    但 g=55 << 140 被排除。
    """
    a = bmp.astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return ((r >= YELLOW_R_MIN) & (g >= YELLOW_G_MIN) & (b <= YELLOW_B_MAX)
            & ((r - b) >= YELLOW_RB_MIN))


def _to_gray(bmp):
    return (bmp[:, :, 0].astype(np.int16) * 77
            + bmp[:, :, 1].astype(np.int16) * 150
            + bmp[:, :, 2].astype(np.int16) * 29) >> 8


def estimate_shift(pre, post, max_shift=24, block=64, step=16):
    """块匹配估全局位移，返回 (dx, dy)：post 相对 pre 平移量。

    只用于「有没有明显位移」的粗估，找不到平滑区域时返回 (0,0)。
    """
    h, w = pre.shape[:2]
    pg = _to_gray(pre)
    qg = _to_gray(post)
    if h < block * 2 or w < block * 2:
        return 0, 0
    best = (1e18, 0, 0)
    # 只在中部区域取块，避开 UI 边缘
    for ys in range(h // 4, h - block, block + step):
        for xs in range(w // 4, w - block, block + step):
            blk = pg[ys:ys + block, xs:xs + block]
            if blk.std() < 6:            # 纯水平面，块匹配不可靠，跳过
                continue
            for dy in range(-max_shift, max_shift + 1, 2):
                for dx in range(-max_shift, max_shift + 1, 2):
                    y0, x0 = ys + dy, xs + dx
                    if y0 < 0 or x0 < 0 or y0 + block > h or x0 + block > w:
                        continue
                    d = float(np.abs(blk - qg[y0:y0 + block, x0:x0 + block]).mean())
                    if d < best[0]:
                        best = (d, dx, dy)
    if best[0] > 20.0:                   # 匹配残差太大 = 没有可信位移
        return 0, 0
    return best[1], best[2]


def _shift_img(bmp, dx, dy):
    """把 bmp 反向平移 (dx,dy)，用来和参考帧对齐。"""
    if dx == 0 and dy == 0:
        return bmp
    h, w = bmp.shape[:2]
    out = np.zeros_like(bmp)
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    yd0, yd1 = max(0, -dy), min(h, h - dy)
    xd0, xd1 = max(0, -dx), min(w, w - dx)
    out[yd0:yd1, xd0:xd1] = bmp[ys0:ys1, xs0:xs1]
    return out


def diff_mask(pre, post, align=True):
    """返回布尔掩膜：post 里新出现的东西。"""
    if align:
        dx, dy = estimate_shift(pre, post)
        if dx or dy:
            post = _shift_img(post, dx, dy)
    a = pre.astype(np.int16)
    b = post.astype(np.int16)
    d = np.abs(b - a)
    over = (d > DIFF_TH).sum(axis=2)
    return over >= DIFF_MIN_CH


def label_blocks(mask, max_blocks=200000):
    """连通域标记（8 邻接），返回 [(size, x0, y0, x1, y1, cx, cy), ...] 按面积降序。

    max_blocks 只防内存爆掉，默认给得很大：No.1 不能让「一堆 1~4px 噪声块」
    把 200 的上限塞满，害得真浮标挤不进列表（这个坑实测踩过）。
    """
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return []
    h, w = mask.shape[:2]
    seen = np.zeros((h, w), dtype=bool)
    # 用点集加速邻居查询
    pts = set(zip(xs.tolist(), ys.tolist()))
    out = []
    for sx, sy in zip(xs.tolist(), ys.tolist()):
        if seen[sy, sx]:
            continue
        stack = [(sx, sy)]
        seen[sy, sx] = True
        n = 0
        minx = maxx = sx
        miny = maxy = sy
        while stack:
            px, py = stack.pop()
            n += 1
            if px < minx: minx = px
            if px > maxx: maxx = px
            if py < miny: miny = py
            if py > maxy: maxy = py
            for ddy in (-1, 0, 1):
                for ddx in (-1, 0, 1):
                    if ddx == 0 and ddy == 0:
                        continue
                    nx, ny = px + ddx, py + ddy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny, nx] \
                            and (nx, ny) in pts:
                        seen[ny, nx] = True
                        stack.append((nx, ny))
        out.append((n, minx, miny, maxx, maxy,
                    (minx + maxx) // 2, (miny + maxy) // 2))
        if len(out) >= max_blocks:
            break
    out.sort(key=lambda t: -t[0])
    return out


def morph_close(mask, r=2):
    """二值闭运算（先膨胀再腐蚀），把被噪声打散的浮标连回一块。

    纯 numpy 用最大值/最小值滤波实现；r=2 对 30px 的浮标够用，
    又不会把相邻的独立小块粘成一个假目标。
    """
    m = mask
    for _ in range(r):
        m = _dilate1(m)
    for _ in range(r):
        m = _erode1(m)
    return m


def _dilate1(m):
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _erode1(m):
    out = m.copy()
    out[1:, :] &= m[:-1, :]
    out[:-1, :] &= m[1:, :]
    out[:, 1:] &= m[:, :-1]
    out[:, :-1] &= m[:, 1:]
    out[0, :] = False
    out[-1, :] = False
    out[:, 0] = False
    out[:, -1] = False
    return out


def median_rgb_in(bmp, x0, y0, x1, y1):
    """取框内中位色。框越界自动裁剪，返回 (r,g,b) int 元组。"""
    h, w = bmp.shape[:2]
    x0 = max(int(x0), 0); y0 = max(int(y0), 0)
    x1 = min(int(x1), w); y1 = min(int(y1), h)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0)
    sub = bmp[y0:y1, x0:x1].reshape(-1, 3)
    med = np.median(sub, axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def warm_mask(bmp):
    """逐像素暖色掩膜：浮标 r>g>b 且 r-b 明显；水面波纹 b>g>r。

    只判颜色方向，不设绝对亮度 —— 换钓点被压暗的浮标实测 r≈66 能过，
    青蓝水面 r≈35 一律不过。
    """
    px = bmp.astype(np.int16)
    r, g, b = px[:, :, 0], px[:, :, 1], px[:, :, 2]
    return (r > g) & (r > b) & ((r - b) >= 12)


def is_warm(bmp, x0, y0, x1, y1):
    """块内是否有足量暖色像素（供单块复核）。"""
    patch = bmp[max(y0, 0):y1 + 1, max(x0, 0):x1 + 1]
    if patch.size == 0:
        return False
    px = patch.reshape(-1, 3).astype(np.int16)
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    warm_n = int(np.count_nonzero((r > g) & (r > b) & ((r - b) >= 12)))
    return warm_n >= max(6, int(0.25 * len(px)))


def pick_bobber(pre, post, log=None, align=True):
    """从差分里挑浮标。成功返回 dict，失败返回 None。

    返回 dict:
        x, y       — 截图内坐标（中心）
        w, h       — bbox 尺寸
        area       — 差分像素数
        median_rgb — 该块在 post 里的中位色（用于日志/交叉验证）
    """
    mask = diff_mask(pre, post, align=align)
    if log:
        log("差分: 变化像素 %d (%.2f%%)"
            % (int(mask.sum()), 100.0 * mask.sum() / mask.size))

    h, w = mask.shape[:2]
    scale = w / 1280.0
    min_area = max(int(MIN_AREA * scale * scale), 12)
    max_area = max(int(MAX_AREA * scale * scale), 200)
    m = max(int(EDGE_MARGIN * scale), 4)

    # 关键一步：先按「暖色」过滤，再做连通域。
    # 水面波动造成的变化像素是青蓝色（b 最大），浮标是暖色（r 最大）。
    # 如果直接差分就做连通域，大波动会把半个画面连成一整块、浮标被并进去；
    # 先剔掉冷色像素，浮标自然就独立成块。
    # ⚠️ v23 纠正：这里不做任何颜色过滤。
    #
    # 用户明确指出：浮标颜色随周围环境变化，不能固定色值。差分的价值就是
    # 不依赖颜色 —— 浮标是「抛竿前没有、抛竿后出现」的那团东西，颜色和
    # 水面再像也无所谓，因为水面在 pre 帧里就在、差分后归零。
    #
    # v21 曾在这里加 yellow_mask 强约束，实测 post 帧黄色像素恒为 0
    # （浮标不是亮黄），导致每次都退回、还累得候选被筛空。已删除。
    # 只做闭运算把被噪声打散的团块连回去。
    mask = morph_close(mask, r=2)
    if log:
        log("差分: 闭运算后 %d px（不按颜色过滤）" % int(mask.sum()))

    blocks = label_blocks(mask)
    if not blocks:
        if log:
            log("差分标定失败: 无暖色变化块（浮标未出现或不在画面）")
        return None

    # 天空区预判（用户指定：钓鱼窗口上方 1/4 是天空）。
    # ⚠️ 不能「在天空带里就一律排除」—— 实测浮标落点 y=57~71，
    # 就在 638 高的顶部 12%，正好落在 1/4 天空带内。
    # 所以只排除「整块都是天空色」的候选（那才是云/远景），
    # 黄浮标 r=255 天然通不过 sky_mask，不受影响。
    zone = sky_zone_mask(post.shape)
    sky = sky_mask(post)
    sky_in_zone = sky & zone

    ok = []
    for size, x0, y0, x1, y1, cx, cy in blocks:
        bw = x1 - x0 + 1
        bh = y1 - y0 + 1
        if size < min_area:
            continue
        # 候选框内天空像素占比 > 80% → 判为天空/远景，丢弃
        box_zone = zone[y0:y1 + 1, x0:x1 + 1]
        box_sky = sky[y0:y1 + 1, x0:x1 + 1]
        if box_zone.size and box_zone.mean() > 0.5:
            frac = float(box_sky.sum()) / max(int(box_sky.size), 1)
            if frac > 0.80:
                if log:
                    log("差分丢弃: (%d,%d) %dx%d 位于天空带且 %.0f%% 是天空色"
                        % (cx, cy, bw, bh, frac * 100))
                continue
        if size > max_area:
            if log:
                log("差分丢弃: 面积 %d > %d（整片变化/镜头移动）" % (size, max_area))
            continue
        if x0 <= m or y0 <= m or x1 >= w - 1 - m or y1 >= h - 1 - m:
            continue
        bw2, bh2 = max(bw, 1), max(bh, 1)
        ratio = max(bw2, bh2) / float(min(bw2, bh2))
        if not (MIN_RATIO <= ratio <= MAX_RATIO):
            if log:
                log("差分丢弃: %dx%d 长宽比 %.2f 超范围" % (bw, bh, ratio))
            continue
        fill = size / float(bw2 * bh2)
        if fill < MIN_FILL:
            if log:
                log("差分丢弃: %dx%d 紧凑度 %.2f < %.2f（水波纹/镂空）"
                    % (bw, bh, fill, MIN_FILL))
            continue
        # 暖色判据：浮标是暖色（r>g>≈b），水面波纹是青蓝（b>g>r）。
        # 差分出来的大片变化块九成是水面波动，光靠几何筛不掉，必须看颜色方向。
        # v23：不按颜色筛候选（浮标颜色随环境变）。is_warm 保留但不用。
        if False and not is_warm(post, x0, y0, x1, y1):
            continue
        ok.append((size, cx, cy, bw, bh, fill))

    if not ok:
        if log:
            log("差分标定失败: %d 个变化块全被筛掉" % len(blocks))
        return None

    # ★ v43 核心修复：v23 用 `if False and ...` 把暖色过滤整个关掉后，
    #   pick_bobber 退化成「取变化区里面积最大的块」。真机实测（21:17:25）：
    #   水面波动合成的大块 61x59 area=1685 rgb=(41,59,75)（R-B=-34，深蓝）
    #   压过真浮标 26x16 area≈360 rgb≈(214,180,134)。结果整竿拿一个水面块
    #   当锚点，而色差搜索明明在同一帧稳定命中 (548,235)。
    #
    #   不恢复固定色阈值（那会被"换钓点浮标变暗"打脸），改为「色彩方向」筛选：
    #   浮标在游戏里恒为暖色（R>B），水面波动/天空恒为冷色（B>R）。这是相对
    #   判据，与绝对亮度无关，既抗光照变化又能剔除水面块。
    def _warm_dir(block):
        _s, _cx, _cy, _bw, _bh, _f = block
        _p = post[max(_cy - _bh // 2, 0):_cy + _bh // 2 + 1,
                  max(_cx - _bw // 2, 0):_cx + _bw // 2 + 1]
        if not _p.size:
            return 0.0
        _m = np.median(_p.reshape(-1, 3), axis=0)
        return float(_m[0]) - float(_m[2])          # R - B，正=暖色

    warm_ok = [b for b in ok if _warm_dir(b) > 5]
    if warm_ok:
        if log and len(warm_ok) < len(ok):
            log("差分: %d 个候选中 %d 个为暖色方向（R>B），优先在暖色候选里挑",
                len(ok), len(warm_ok))
        ok = warm_ok
    else:
        # 一个暖色候选都没有 → 这一帧里没有可信浮标。宁可失败退回颜色搜索，
        # 也不要把水面块封成「本竿固定锚点」。
        if log:
            log("差分标定失败: %d 个候选全是冷色（R-B<=5），判为水面波动，"
                "不采信（退回颜色搜索）", len(ok))
        return None

    # 按面积降序，第一个优先（暖色候选里浮标通常是面积最大的那块）
    ok.sort(key=lambda t: -t[0])
    size, cx, cy, bw, bh, fill = ok[0]
    patch = post[max(cy - bh // 2, 0):cy + bh // 2 + 1,
                 max(cx - bw // 2, 0):cx + bw // 2 + 1]
    med = np.median(patch.reshape(-1, 3), axis=0) if patch.size else np.zeros(3)
    if log:
        log("差分标定: 候选 %d 个，取最大 %dx%d @(%d,%d) area=%d fill=%.2f rgb=(%d,%d,%d)"
            % (len(ok), bw, bh, cx, cy, size, fill,
               int(med[0]), int(med[1]), int(med[2])))
    return dict(x=int(cx), y=int(cy), w=int(bw), h=int(bh), area=int(size),
                median_rgb=(int(med[0]), int(med[1]), int(med[2])),
                n_cand=len(ok))


def signature_from(rgb, ratio=0.72):
    """把标定到的中位色转成 tan 判据，供 find() 直接用。

    换钓点后浮标被压暗时，绝对亮度门槛（r>140）会全失配。这里按实测色值
    生成相对门槛：r 的下限取实测 r 的 ratio 倍，色差门槛也按实测色差缩放，
    保证同一个浮标在任意光照下都能过，而纯水面/暖色木桩过不了。
    """
    r, g, b = [int(v) for v in rgb]
    d_rb = max(r - b, 8)
    d_rg = max(r - g, 0)
    return dict(
        tan_min_red=max(int(r * ratio), 24),
        tan_min_rb=max(int(d_rb * 0.6), 10),
        tan_min_rg=int(d_rg * 0.5),
    )
