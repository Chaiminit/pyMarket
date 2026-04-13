import math
import numpy as np
from typing import Union


def sigmoid(x):
    """Sigmoid函数，支持float和Decimal类型"""
    if hasattr(x, "__float__"):
        x = float(x)

    if x > 10:
        return 1.0
    elif x < -10:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


Number = Union[float, int, np.ndarray]


class ChipDistribution:
    """
    完整的概率密度 + 抽样实现。

    PDF:
        f(x) = m * (x/a)**α                , 0 ≤ x ≤ a
               m * exp[-λ (x-a)]          , x > a

    参数
    ----
    a : float
        峰值位置（相对当前价的距离，%），必须 >0
    m : float
        峰值高度，必须 >0
    alpha : float, optional
        左侧幂指数 α（α>0）。如果不提供，默认取
        α = max(1.0, m*a) + 1，保证左侧面积小于 1 且比线性更陡。
    seed : int|None, optional
        随机数生成器的种子。None 表示使用 NumPy 默认 RNG。
    """

    __slots__ = (
        "a",
        "m",
        "alpha",
        "lam",
        "_inv_a",
        "_left_cdf",
        "_rng",
        "_log_m",
    )  # 为了轻量化只保留必要的属性

    def __init__(self, a: float, m: float, *, alpha: float = None, seed: int = None):
        if a <= 0.0:
            raise ValueError("a 必须为正数")
        if m <= 0.0:
            raise ValueError("m 必须为正数")

        self.a = float(a)
        self.m = float(m)

        # ------------------- 1) 选取 α -------------------
        if alpha is None:
            # 默认让左侧稍微“陡”一点
            self.alpha = max(1.0, self.m * self.a) + 1.0
        else:
            if alpha <= 0.0:
                raise ValueError("alpha 必须为正数")
            self.alpha = float(alpha)

        # ------------------- 2) 计算左侧面积 L 和 λ -------------------
        self._left_cdf = self.m * self.a / (self.alpha + 1.0)  # = L
        if not (0.0 < self._left_cdf < 1.0):
            # 左侧面积太大或太小都会导致 λ 非正或数值不稳
            raise ValueError(
                f"参数导致左侧累计概率 L={self._left_cdf:.6f} 不在 (0,1) "
                "区间。请增大 alpha 或调小 (a,m)。"
            )
        # 根据归一化公式 λ = m / (1 - L)
        self.lam = self.m / (1.0 - self._left_cdf)

        # ------------------- 3) 预计算常量 -------------------
        self._inv_a = 1.0 / self.a
        self._log_m = math.log(self.m)  # 如需要对数输出，可直接使用

        # ------------------- 4) 随机数生成器 -------------------
        self._rng = np.random.default_rng(seed)

    # -----------------------------------------------------------------
    # 1) PDF（向量化） -------------------------------------------------
    def pdf(self, x: Number) -> np.ndarray:
        """
        计算概率密度 f(x)。

        参数
        ----
        x : float, np.ndarray, list
            距离当前价的相对百分比（%），要求 ≥0。

        返回
        ----
        np.ndarray
            与 x 对应的概率密度（已归一化）。
        """
        x_arr = np.asarray(x, dtype=np.float64)

        # 安全：负数视为 0（在金融语境中不存在）
        np.maximum(x_arr, 0.0, out=x_arr)

        t = x_arr * self._inv_a  # t = x / a

        left = np.where(x_arr <= self.a, self.m * np.power(t, self.alpha), 0.0)

        right = np.where(x_arr > self.a, self.m * np.exp(-self.lam * (x_arr - self.a)), 0.0)

        return left + right

    # -----------------------------------------------------------------
    # 2) 逆变换抽样 ---------------------------------------------------
    def sample(self) -> float:
        """
        抽取单个随机变量 X ~ f(x)。

        返回
        ----
        float
            满足该分布的随机样本。
        """
        u = self._rng.random()  # 0 ≤ u < 1

        if u < self._left_cdf:  # 落在左侧区间
            # X = a * (U / L)^{1/(α+1)}
            ratio = u / self._left_cdf
            # 防止由于机器误差出现 ratio>1 的极端情况
            ratio = min(ratio, 1.0)
            x = self.a * ratio ** (1.0 / (self.alpha + 1.0))
            return x
        else:  # 落在右侧区间
            # X = a - (1/λ) * ln( (1-U)/(1-L) )
            numerator = 1.0 - u
            denominator = 1.0 - self._left_cdf
            # 为防止 log(0)（理论上不可能，因为 u<1），加极小值
            eps = np.finfo(float).tiny
            frac = max(numerator / denominator, eps)
            x = self.a - (1.0 / self.lam) * math.log(frac)
            return x


chip_distribution = ChipDistribution(0.05, 1, alpha=0.5)
print(chip_distribution.sample())
