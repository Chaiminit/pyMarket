import math
import numpy as np
from typing import Union


def sigmoid(x):
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
    筹码分布 - 用于计算挂单价格/利率偏离度和交易量权重

    分布特性：
    - 峰值在 position 处（默认5%）
    - 峰值左侧快速上升（幂函数）
    - 峰值右侧指数衰减（长尾）
    - 所有采样值限制在 [0, max_value] 范围内

    PDF (归一化后):
        f(x) = C * x^alpha              , 0 ≤ x ≤ a   (上升段)
               C * m * exp[-λ(x-a)]     , a < x ≤ max  (长尾段)

    参数
    ----
    peak : float
        峰值位置（相对当前价的距离比例），默认 0.05 (5%)
    max_value : float
        最大允许偏离度，默认 0.50 (50%)
    alpha : float, optional
        左侧幂指数，控制峰值陡峭程度，越大越陡
    decay : float, optional
        右侧衰减速率，越小尾巴越长
    seed : int, optional
        随机种子
    """

    __slots__ = (
        "peak",
        "max_value",
        "alpha",
        "decay",
        "_lam",
        "_rng",
        "_left_area",
        "_total_area",
        "_peak_pdf",
    )

    def __init__(
        self,
        peak: float = 0.05,
        max_value: float = 0.50,
        *,
        alpha: float = 2.0,
        decay: float = 15.0,
        seed: int = None,
    ):
        if peak <= 0.0 or peak >= max_value:
            raise ValueError(f"peak 必须在 (0, {max_value}) 区间内")
        if max_value <= peak:
            raise ValueError("max_value 必须 > peak")

        self.peak = float(peak)
        self.max_value = float(max_value)
        self.alpha = float(alpha)
        self.decay = float(decay)

        self._rng = np.random.default_rng(seed)

        # 计算峰值处的 PDF 值
        self._peak_pdf = self.alpha * (self.peak ** (self.alpha - 1))

        # 计算右侧衰减参数 λ
        self._lam = decay / self.max_value

        # 计算左侧面积（积分 0 到 peak: x^alpha dx）
        self._left_area = (self.peak ** (self.alpha + 1)) / (self.alpha + 1)

        # 计算右侧面积（积分 peak 到 max: exp[-λ(x-peak)] dx）
        right_area = (1 - math.exp(-self._lam * (self.max_value - self.peak))) / self._lam

        # 总面积用于归一化
        self._total_area = self._left_area + right_area

    def pdf(self, x: Number) -> np.ndarray:
        """
        计算概率密度 f(x)（未归一化）

        Args:
            x: 输入值或数组

        Returns:
            概率密度值（标量或数组，与输入类型一致）
        """
        is_scalar = np.isscalar(x)
        x_arr = np.asarray(x, dtype=np.float64)
        result = np.zeros_like(x_arr)

        # 左侧区域: 0 <= x <= peak
        left_mask = (x_arr >= 0) & (x_arr <= self.peak)
        if np.any(left_mask):
            result[left_mask] = np.power(x_arr[left_mask], self.alpha)

        # 右侧区域: peak < x <= max_value
        right_mask = (x_arr > self.peak) & (x_arr <= self.max_value)
        if np.any(right_mask):
            result[right_mask] = np.exp(-self._lam * (x_arr[right_mask] - self.peak))

        if is_scalar:
            return float(result)
        return result

    def sample(self) -> float:
        """
        从分布中抽取一个随机样本

        Returns:
            在 [0, max_value] 范围内的随机偏离度
        """
        u = self._rng.random()

        left_prob = self._left_area / self._total_area

        if u < left_prob:
            # 从左侧幂律分布采样
            ratio = u / left_prob
            ratio = min(max(ratio, 0.0), 1.0)
            x = self.peak * (ratio ** (1.0 / (self.alpha + 1)))
            return x
        else:
            # 从右侧指数分布采样（逆变换）
            u_right = (u - left_prob) / (1 - left_prob)
            u_right = min(max(u_right, 0.0), 1.0)

            # 指数分布逆变换，截断到 max_value
            exp_max = math.exp(-self._lam * (self.max_value - self.peak))
            x = self.peak - (1.0 / self._lam) * math.log(1 - u_right * (1 - exp_max))

            return min(x, self.max_value)


chip_distribution = ChipDistribution(peak=0.001, max_value=0.30, alpha=2.0, decay=25.0)
