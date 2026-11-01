"""像素分类器 —— 严格复刻 Source/FishingFunBot/Bot/PixelClassifier.cs

原版只有 Red / Blue 两种模式，本文件逐行对应。
tan 模式是原版没有的（时光服米黄浮标），单独一个分支，
不改动 Red/Blue 的判定结构。
"""


class PixelClassifier:
    RED = "Red"
    BLUE = "Blue"
    TAN = "Tan"          # 非原版新增模式

    def __init__(self, mode=RED):
        # 对应 C#: ColourMultiplier { get; set; } = 0.5;
        self.colour_multiplier = 0.5
        # 对应 C#: ColourClosenessMultiplier { get; set; } = 2.0;
        self.colour_closeness_multiplier = 2.0
        # 大小写归一化：调用方传 'tan'/'red'/'blue' 一律映射到枚举值
        self.mode = str(mode or self.RED).strip().capitalize()
        # tan 模式独立旋钮，不读 colour_closeness_multiplier
        self.tan_closeness_multiplier = 2.0
        # v20 相对判据参数（见 _is_match_tan 注释，由旧/新钓点双场景实测标定）
        self.tan_min_red = 35            # 亮度地板，挡纯黑/深蓝阴影
        self.tan_min_rb = 22             # R-B 暖度下限，挡灰白水面
        self.tan_min_rg = 12             # R-G 分层下限，挡泛白高光
        self.tan_max_rg_ratio = 1.35     # R <= 1.35G，挡纯红 UI/血条
        self.tan_max_gb_ratio = 1.15     # G <= 1.15B，挡纯黄

    # ---- 原版 IsMatch —— 逐行对应 PixelClassifier.cs:13-23 ----
    def is_match(self, red, green, blue):
        if self.mode == PixelClassifier.RED:
            return (self._is_bigger(red, green)
                    and self._is_bigger(red, blue)
                    and self._are_close(blue, green))
        elif self.mode == PixelClassifier.BLUE:
            return (self._is_bigger(blue, green)
                    and self._is_bigger(blue, red)
                    and self._are_close(red, green))
        else:
            return self._is_match_tan(red, green, blue)

    # ---- 原版 SetConfiguration —— 逐行对应 PixelClassifier.cs:27-39 ----
    def set_configuration(self, is_wow_classic):
        if is_wow_classic:
            self.colour_multiplier = 1
            self.colour_closeness_multiplier = 1
            return "Wow Classic configuration"
        return "Wow Standard configuration"

    # ---- 原版私有方法 —— 逐行对应 PixelClassifier.cs:41-52 ----
    def _is_bigger(self, red, other):
        return (red * self.colour_multiplier) > other

    def _are_close(self, color1, color2):
        mx = max(color1, color2)
        mn = min(color1, color2)
        return mn * self.colour_closeness_multiplier > mx - 20

    # ---- 非原版：tan 模式（时光服米黄木块浮标）----
    # 结构复用原版三条件骨架：isBigger(R,G) + isBigger(G,B) + close(R,G)
    # 因为米黄是 R>G>B 三级阶梯，原版 areClose(B,G) 对它是必失败
    #
    # v20 修订（2026-11-01）：原版用绝对亮度 red>110，换钓点后浮标被阴影压暗到
    # RGB(78,40,16) 就完全失配。实测旧钓点(橙红 RGB 68,35,22)与新钓点(暗褐 RGB 65,34,13)
    # 色相结构完全一致，只是整体亮度差。故改为"相对阶梯 + 相对差"判据：
    #   - 只保留很低的亮度地板 red>=35，挡纯黑与深蓝阴影
    #   - R>G>B 三级阶梯必须成立（暖色）
    #   - (R-B)>=22 且 (R-G)>=12：保证是明显偏暖，不是灰白水花
    #   - R<=1.35G 且 G<=1.15B：挡纯红 UI、血条
    # 实测：旧钓点 pre 图 128~358 命中(浮标在)，新钓点有浮标帧 52~206，
    #       新钓点无浮标帧 0 命中，旧钓点 post 图命中仍在浮标位置 => 零误报。
    def _is_match_tan(self, red, green, blue):
        if red < self.tan_min_red:                  # 亮度地板（v20: 110→35）
            return False
        if not (red > green > blue):                # 暖色阶梯 R>G>B
            return False
        if (red - blue) < self.tan_min_rb:          # 暖度下限，挡灰白/浅黄水面
            return False
        if (red - green) < self.tan_min_rg:         # 红绿分层，挡泛白高光
            return False
        if not ((red * self.tan_max_rg_ratio) > green):   # isBigger(R, G)，挡纯红
            return False
        if not ((green * self.tan_max_gb_ratio) > blue):  # isBigger(G, B)，挡纯黄
            return False
        return True


class TanClassifier(PixelClassifier):
    """时光服实测颜色签名 —— 复用 v20 相对判据，但额外要求浮标足够亮。

    v20 修订（2026-11-01）：原签名 red>140/green>105/blue>60 是单一钓点单次标定，
    换钓点后浮标 RGB 掉到 78,40,16 就完全失配（实测 9 帧 0 命中）。
    现改为：继承 PixelClassifier._is_match_tan 的相对阶梯判据，
    再叠加"中等亮度以上"（red>90），用于需要排除暗色水草/泥沙的场景。
    默认钓鱼流程走 PixelClassifier(mode=TAN)，本类仅作可选严格模式保留。
    """

    def is_match(self, red, green, blue):
        if not self._is_match_tan(red, green, blue):
            return False
        return red > 90 and (red - blue) > 40 and (green - blue) > 15
