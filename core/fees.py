"""
Fees 模块 - 手续费系统

提供交易手续费的计算、收取和分配功能：
- 支持 Maker/Taker 不同费率
- 支持按交易金额或固定金额收取
- 支持多种收取方向（买入方、卖出方、双方）
- 支持将手续费支付给指定的 Trader
- 支持通过匿名函数针对不同交易者设置特殊手续费

手续费收取方向：
- "buyer": 仅向买方收取
- "seller": 仅向卖方收取
- "both": 向买卖双方收取
"""

from typing import Dict, Optional, TYPE_CHECKING, Callable, Union
from dataclasses import dataclass
from enum import Enum
from decimal import Decimal

from .utils import to_decimal, D0

if TYPE_CHECKING:
    from .trader import Trader


class FeeDirection(Enum):
    """手续费收取方向"""
    BUYER = "buyer"      # 仅买方支付
    SELLER = "seller"    # 仅卖方支付
    BOTH = "both"        # 双方支付


class FeeType(Enum):
    """手续费类型"""
    PERCENTAGE = "percentage"  # 按百分比收取
    FIXED = "fixed"            # 固定金额收取


@dataclass
class FeeConfig:
    """
    手续费配置类

    支持为 Maker（挂单）和 Taker（吃单）设置不同费率，
    支持按百分比或固定金额收取，也支持通过匿名函数针对不同交易者设置特殊手续费。

    Attributes:
        maker_rate: Maker 费率（如 0.001 表示 0.1%）
        taker_rate: Taker 费率
        fee_type: 手续费类型（百分比或固定金额）
        direction: 收取方向（买方、卖方或双方）
        min_fee: 最小手续费金额（防止小额交易手续费为0）
        max_fee: 最大手续费金额（可选，用于限制高额手续费）
        fee_recipient: 手续费接收者（Trader），None 表示不收取手续费
        fee_modifier: 费率修饰器（匿名函数），接收 (trader, is_buyer, is_taker) 返回修改后的费率

    Examples:
        >>> # 标准交易所费率：Maker 0.1%, Taker 0.2%
        >>> config = FeeConfig(maker_rate=0.001, taker_rate=0.002)
        >>>
        >>> # 仅向卖方收取固定手续费，支付给平台
        >>> config = FeeConfig(
        ...     maker_rate=1.0,
        ...     taker_rate=2.0,
        ...     fee_type=FeeType.FIXED,
        ...     direction=FeeDirection.SELLER,
        ...     fee_recipient=platform_trader
        ... )
        >>>
        >>> # 使用匿名函数为 VIP 交易者提供折扣
        >>> def vip_discount(trader, is_buyer, is_taker):
        ...     if trader.name.startswith("VIP"):
        ...         return Decimal('0.5')  # VIP 交易者享受 5 折
        ...     return Decimal('1.0')    # 普通交易者无折扣
        >>> config = FeeConfig(
        ...     maker_rate=0.001,
        ...     taker_rate=0.002,
        ...     fee_modifier=vip_discount
        ... )
    """
    maker_rate: Decimal = D0
    taker_rate: Decimal = D0
    fee_type: FeeType = FeeType.PERCENTAGE
    direction: FeeDirection = FeeDirection.BOTH
    min_fee: Decimal = D0
    max_fee: Optional[Decimal] = None
    fee_recipient: Optional["Trader"] = None
    fee_modifier: Optional[Callable[["Trader", bool, bool], Decimal]] = None

    def __post_init__(self):
        """验证费率合法性"""
        self.maker_rate = to_decimal(self.maker_rate)
        self.taker_rate = to_decimal(self.taker_rate)
        self.min_fee = to_decimal(self.min_fee)
        if self.max_fee is not None:
            self.max_fee = to_decimal(self.max_fee)

        if self.maker_rate < D0 or self.taker_rate < D0:
            raise ValueError("费率不能为负数")
        if self.fee_type == FeeType.PERCENTAGE:
            if self.maker_rate > D0 or self.taker_rate > D0:
                if self.maker_rate > Decimal('1.0') or self.taker_rate > Decimal('1.0'):
                    raise ValueError("百分比费率不能超过 100%")
        if self.min_fee < D0:
            raise ValueError("最小手续费不能为负数")
        if self.max_fee is not None and self.max_fee < self.min_fee:
            raise ValueError("最大手续费不能小于最小手续费")


@dataclass
class FeeResult:
    """
    手续费计算结果

    Attributes:
        buyer_fee: 买方支付的手续费
        seller_fee: 卖方支付的手续费
        total_fee: 总手续费
        buyer_received: 买方实际收到的资产（扣除手续费后）
        seller_received: 卖方实际收到的资产（扣除手续费后）
    """
    buyer_fee: Decimal = D0
    seller_fee: Decimal = D0
    total_fee: Decimal = D0
    buyer_received: Decimal = D0
    seller_received: Decimal = D0


class FeeCalculator:
    """
    手续费计算器

    根据配置计算交易双方应支付的手续费。

    Examples:
        >>> config = FeeConfig(maker_rate=0.001, taker_rate=0.002)
        >>> calculator = FeeCalculator(config)
        >>>
        >>> # 计算吃单手续费（市价单）
        >>> result = calculator.calculate(
        ...     trade_amount=10000.0,  # 交易金额
        ...     is_taker=True,         # 是吃单
        ...     is_buyer=True          # 是买方
        ... )
    """

    def __init__(self, config: FeeConfig):
        """
        创建手续费计算器

        Args:
            config: 手续费配置
        """
        self.config = config

    def calculate(
        self,
        trade_amount,
        is_taker: bool = True,
        is_buyer: bool = True,
        trader: Optional["Trader"] = None
    ) -> Decimal:
        """
        计算单边手续费

        Args:
            trade_amount: 交易金额
            is_taker: 是否为吃单（Taker），False 表示挂单（Maker）
            is_buyer: 是否为买方（用于判断是否需要支付手续费）
            trader: 交易者对象，用于应用特殊手续费规则

        Returns:
            应支付的手续费金额
        """
        trade_amount = to_decimal(trade_amount)

        # 根据收取方向判断是否需要支付手续费
        direction = self.config.direction

        if direction == FeeDirection.BUYER and not is_buyer:
            return D0
        if direction == FeeDirection.SELLER and is_buyer:
            return D0

        # 选择费率
        rate = self.config.taker_rate if is_taker else self.config.maker_rate

        # 应用费率修饰器（匿名函数）
        if self.config.fee_modifier is not None and trader is not None:
            modifier = self.config.fee_modifier(trader, is_buyer, is_taker)
            if modifier is not None:
                rate = rate * modifier

        # 计算手续费
        if self.config.fee_type == FeeType.PERCENTAGE:
            fee = trade_amount * rate
        else:  # FIXED
            fee = rate

        # 应用最小/最大限制
        if fee < self.config.min_fee:
            fee = self.config.min_fee
        if self.config.max_fee is not None and fee > self.config.max_fee:
            fee = self.config.max_fee

        return fee

    def calculate_trade_fees(
        self,
        trade_amount,
        buyer_is_taker: bool = False,
        seller_is_taker: bool = False
    ) -> FeeResult:
        """
        计算完整交易的手续费（买卖双方）

        Args:
            trade_amount: 交易金额
            buyer_is_taker: 买方是否为吃单
            seller_is_taker: 卖方是否为吃单

        Returns:
            FeeResult 包含双方手续费详情
        """
        buyer_fee = self.calculate(trade_amount, is_taker=buyer_is_taker, is_buyer=True)
        seller_fee = self.calculate(trade_amount, is_taker=seller_is_taker, is_buyer=False)

        return FeeResult(
            buyer_fee=buyer_fee,
            seller_fee=seller_fee,
            total_fee=buyer_fee + seller_fee,
            buyer_received=D0,  # 需要外部计算
            seller_received=D0
        )


class FeeCollector:
    """
    手续费收集器

    负责收集手续费并支付给指定的接收者。

    Attributes:
        fee_recipient: 手续费接收者（Trader）
        collected_fees: 已收集的手续费总额 {Token: amount}
        fee_history: 手续费收取历史记录

    Examples:
        >>> collector = FeeCollector(platform_trader)
        >>>
        >>> # 收集手续费
        >>> collector.collect(usdt_token, 100.0)
        >>>
        >>> # 查询已收集的手续费
        >>> total = collector.get_collected(usdt_token)
    """

    def __init__(self, fee_recipient: Optional["Trader"] = None):
        """
        初始化手续费收集器

        Args:
            fee_recipient: 手续费接收者，None 表示手续费不支付给任何人
        """
        self.fee_recipient = fee_recipient
        self.collected_fees: Dict["Token", Decimal] = {}
        self.fee_history: list = []

    def collect(self, token: "Token", amount, trade_info: dict = None) -> None:
        """
        收集手续费并支付给接收者

        Args:
            token: Token 对象
            amount: 手续费金额
            trade_info: 可选的交易信息记录
        """
        amount = to_decimal(amount)
        if amount <= D0:
            return

        # 记录手续费（使用 Token 对象作为键）
        self.collected_fees[token] = self.collected_fees.get(token, D0) + amount

        # 如果有接收者，将手续费支付给接收者
        if self.fee_recipient is not None:
            self.fee_recipient.assets[token] = self.fee_recipient.assets.get(token, D0) + amount

        if trade_info:
            trade_info["fee_amount"] = amount
            trade_info["fee_token"] = token.name
            trade_info["fee_recipient"] = self.fee_recipient.name if self.fee_recipient else None
            self.fee_history.append(trade_info)

    def get_collected(self, token: Optional["Token"] = None):
        """
        获取已收集的手续费

        Args:
            token: Token 对象，None 则返回所有

        Returns:
            指定代币的手续费金额，或所有代币的手续费字典
        """
        if token:
            return self.collected_fees.get(token, D0)
        return self.collected_fees.copy()

    def reset(self, token: Optional["Token"] = None) -> None:
        """
        重置手续费记录

        Args:
            token: Token 对象，None 则重置所有
        """
        if token:
            self.collected_fees.pop(token, None)
        else:
            self.collected_fees.clear()
            self.fee_history.clear()
