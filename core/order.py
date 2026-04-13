"""
Order 模块 - 订单定义

定义两种订单类型：
- Order: 普通交易对的限价单
- BondOrder: 债券交易对的限价单

订单封装了交易意图、冻结资金和取消逻辑。
"""

from typing import Optional, Callable, TYPE_CHECKING
from datetime import datetime
from decimal import Decimal

from .token import Token
from .utils import to_decimal, D0

if TYPE_CHECKING:
    from .trader import Trader
    from .trading_pair import TradingPair
    from .bond_pair import BondTradingPair


class Order:
    """
    普通限价单 - 用于普通交易对

    订单创建时会冻结交易者的相应资金/资产，
    取消或完成时返还剩余冻结部分。

    Attributes:
        trader: 下单交易者
        direction: 方向，'buy' 或 'sell'
        price: 限价
        volume: 订单总量
        executed: 已成交数量
        remaining_frozen: 剩余冻结资金/资产
        time: 订单创建时间
        pair: 所属交易对
        cancelled: 是否已取消
        cancellable: 是否可手动取消（默认为True，IPO订单为False）

    Examples:
        >>> order = Order(trader, "buy", 50000.0, 1.0, 50000.0, pair)
        >>> order.remaining_volume  # 未成交数量
        Decimal('1.0')
        >>> order.close()  # 取消订单，返还冻结资金
    """

    def __init__(
        self,
        trader: "Trader",
        direction: str,
        price,
        volume,
        frozen_amount,
        pair: "TradingPair",
    ):
        """
        创建普通限价单

        Args:
            trader: 交易者对象
            direction: 'buy' 或 'sell'
            price: 订单价格
            volume: 订单数量
            frozen_amount: 冻结的资金（买单）或资产（卖单）
            pair: 所属交易对
        """
        self.trader = trader
        self.direction = direction
        self.price = to_decimal(price)
        self.volume = to_decimal(volume)
        self.executed = D0
        self.remaining_frozen = to_decimal(frozen_amount)
        self.time = datetime.now()
        self.pair = pair
        self.cancelled = False
        self.cancellable = True  # 默认可手动取消

    def close(self, force: bool = False) -> None:
        """
        关闭订单 - 从订单簿移除并返还冻结资金

        Args:
            force: 是否强制关闭（用于内部成交清理），默认为False
                  当为False时，会检查cancellable属性

        执行流程：
        1. 检查是否可取消（非强制模式下）
        2. 从交易对的订单簿中移除
        3. 从交易者的订单列表中移除
        4. 返还剩余的冻结资金/资产
        """
        if self.cancelled:
            return

        # 非强制关闭时，检查是否可手动取消
        if not force and not self.cancellable:
            return

        self.cancelled = True

        # 从交易对订单簿中移除
        if self.direction == "buy":
            if self in self.pair.buy_orders:
                self.pair.buy_orders.remove(self)
        else:
            if self in self.pair.sell_orders:
                self.pair.sell_orders.remove(self)

        # 从交易者订单列表中移除
        if self in self.trader.orders:
            self.trader.orders.remove(self)

        # 返还冻结资金
        if self.remaining_frozen > D0:
            if self.direction == "buy":
                # 买单返还计价代币
                quote_token = self.pair.quote_token
                self.trader.assets[quote_token] = (
                    self.trader.assets.get(quote_token, D0) + self.remaining_frozen
                )
            else:
                # 卖单返还基础代币
                base_token = self.pair.base_token
                self.trader.assets[base_token] = (
                    self.trader.assets.get(base_token, D0) + self.remaining_frozen
                )
            self.remaining_frozen = D0

    @property
    def remaining_volume(self) -> Decimal:
        """获取剩余未成交数量"""
        return self.volume - self.executed

    def __repr__(self) -> str:
        return (
            f"Order({self.direction}, {self.price}, vol={self.volume})"
        )


class BondOrder:
    """
    债券限价单 - 用于债券交易对

    与普通订单的区别：
    - 使用利率(interest_rate)代替价格
    - 卖单冻结的是债券持仓而非资产
    - 成交时转移债券所有权而非代币

    Attributes:
        trader: 下单交易者
        direction: 方向，'buy' 或 'sell'
        interest_rate: 目标利率
        volume: 债券数量
        executed: 已成交数量
        remaining_frozen: 剩余冻结资金（买单）或债券（卖单）
        time: 订单创建时间
        bond_pair: 所属债券交易对
        cancelled: 是否已取消
    """

    def __init__(
        self,
        trader: "Trader",
        direction: str,
        interest_rate,
        volume,
        frozen_amount,
        bond_pair: "BondTradingPair",
    ):
        """
        创建债券限价单

        Args:
            trader: 交易者对象
            direction: 'buy' 或 'sell'
            interest_rate: 目标利率（年化）
            volume: 债券数量
            frozen_amount: 冻结的资金（买单）或债券（卖单）
            bond_pair: 所属债券交易对
        """
        self.trader = trader
        self.direction = direction
        self.interest_rate = to_decimal(interest_rate)
        self.volume = to_decimal(volume)
        self.executed = D0
        self.remaining_frozen = to_decimal(frozen_amount)
        self.time = datetime.now()
        self.bond_pair = bond_pair
        self.cancelled = False

    def close(self) -> None:
        """
        关闭债券订单 - 从订单簿移除并返还冻结资金/债券

        执行流程：
        1. 从债券交易对的订单簿中移除
        2. 从交易者的债券订单列表中移除
        3. 买单：返还剩余冻结代币
           卖单：返还剩余冻结债券
        """
        if self.cancelled:
            return

        self.cancelled = True

        # 从债券订单簿中移除
        if self.direction == "buy":
            if self in self.bond_pair.buy_orders:
                self.bond_pair.buy_orders.remove(self)
        else:
            if self in self.bond_pair.sell_orders:
                self.bond_pair.sell_orders.remove(self)

        # 从交易者债券订单列表中移除
        if self in self.trader.bond_orders:
            self.trader.bond_orders.remove(self)

        # 返还冻结资金/债券
        if self.direction == "buy":
            # 买单：返还剩余的冻结代币
            if self.remaining_frozen > D0:
                token = self.bond_pair.token
                self.trader.assets[token] = (
                    self.trader.assets.get(token, D0) + self.remaining_frozen
                )
                self.remaining_frozen = D0
        else:
            # 卖单：返还剩余的冻结债券
            bond_token = self.bond_pair.token
            remaining_bonds = self.volume - self.executed
            if remaining_bonds > D0:
                self.trader.bonds[bond_token] = self.trader.bonds.get(bond_token, D0) + remaining_bonds

    @property
    def remaining_volume(self) -> Decimal:
        """获取剩余未成交数量"""
        return self.volume - self.executed

    def __repr__(self) -> str:
        return f"BondOrder({self.direction}, rate={self.interest_rate}, vol={self.volume})"
