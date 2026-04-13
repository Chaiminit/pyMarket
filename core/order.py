from typing import Optional, Callable
from datetime import datetime


class Order:
    """订单对象 - 封装订单数据和取消逻辑"""

    def __init__(
        self,
        trader: "Trader",
        direction: str,
        price: float,
        volume: float,
        frozen_amount: float,
        pair_id: int,
        pair: object,
    ):
        """
        创建订单

        Args:
            trader: 交易者对象
            direction: 'buy' 或 'sell'
            price: 订单价格
            volume: 订单数量
            frozen_amount: 冻结的资金/资产
            pair_id: 交易对ID
            pair: 交易对对象（用于取消时访问）
        """
        self.trader = trader
        self.direction = direction
        self.price = price
        self.volume = volume
        self.executed = 0.0
        self.remaining_frozen = frozen_amount
        self.time = datetime.now()
        self.pair_id = pair_id
        self.pair = pair
        self.cancelled = False

    def close(self):
        """关闭订单：从交易簿和交易者订单列表中移除，并返还冻结"""
        if self.cancelled:
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
        if self.remaining_frozen > 0:
            if self.direction == "buy":
                quote_token = self.pair.quote_token
                self.trader.assets[quote_token] = (
                    self.trader.assets.get(quote_token, 0.0) + self.remaining_frozen
                )
            else:
                base_token = self.pair.base_token
                self.trader.assets[base_token] = (
                    self.trader.assets.get(base_token, 0.0) + self.remaining_frozen
                )
            self.remaining_frozen = 0

    @property
    def remaining_volume(self) -> float:
        """获取剩余未成交数量"""
        return self.volume - self.executed

    def __repr__(self):
        return (
            f"Order({self.direction}, {self.price:.4f}, vol={self.volume:.4f}, pair={self.pair_id})"
        )


class BondOrder:
    """债券订单对象"""

    def __init__(
        self,
        trader: "Trader",
        direction: str,
        interest_rate: float,
        volume: float,
        frozen_amount: float,
        bond_pair_id: int,
        bond_pair: object,
    ):
        """
        创建债券订单

        Args:
            trader: 交易者对象
            direction: 'buy' 或 'sell'
            interest_rate: 利率
            volume: 订单数量
            frozen_amount: 冻结的资金
            bond_pair_id: 债券交易对ID
            bond_pair: 债券交易对对象
        """
        self.trader = trader
        self.direction = direction
        self.interest_rate = interest_rate
        self.volume = volume
        self.executed = 0.0
        self.remaining_frozen = frozen_amount
        self.time = datetime.now()
        self.bond_pair_id = bond_pair_id
        self.bond_pair = bond_pair
        self.cancelled = False

    def close(self):
        """关闭订单：从交易簿和交易者订单列表中移除，并返还冻结"""
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
            # 买单：返还剩余的冻结 USDT
            if self.remaining_frozen > 0:
                token_name = self.bond_pair.token_name
                self.trader.assets[token_name] = (
                    self.trader.assets.get(token_name, 0.0) + self.remaining_frozen
                )
                self.remaining_frozen = 0
        else:
            # 卖单：返还剩余的冻结债券
            bond_key = f"BOND-{self.bond_pair.token_name}"
            remaining_bonds = self.volume - self.executed
            if remaining_bonds > 0:
                self.trader.bonds[bond_key] = self.trader.bonds.get(bond_key, 0.0) + remaining_bonds

    @property
    def remaining_volume(self) -> float:
        """获取剩余未成交数量"""
        return self.volume - self.executed

    def __repr__(self):
        return f"BondOrder({self.direction}, rate={self.interest_rate:.4f}, vol={self.volume:.4f})"
