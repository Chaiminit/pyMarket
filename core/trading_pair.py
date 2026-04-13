"""
TradingPair 模块 - 普通交易对

管理基础代币/计价代币的交易对，提供：
- 限价单订单簿管理（买单簿、卖单簿）
- 订单撮合引擎
- 市价单执行
- 市场深度查询
"""

import time
import math
import random
from typing import List, Dict, Tuple, Optional, Set

from .trader import Trader
from .order import Order
from .token import Token


class TradingPair:
    """
    普通交易对 - 管理订单簿和撮合逻辑

    交易对由基础代币(base)和计价代币(quote)组成，
    价格表示为 1 base = X quote。

    Attributes:
        base_token: 基础代币（被交易的资产）
        quote_token: 计价代币（定价资产，如USDT）
        price: 当前市场价格
        log: 成交记录列表 [(timestamp, price, volume), ...]
        buy_orders: 买单列表（按价格降序）
        sell_orders: 卖单列表（按价格升序）
        clients: 参与此交易对的交易者集合

    Examples:
        >>> pair = TradingPair(btc, usdt, 50000.0)
        >>> pair.submit_limit_order(trader, "buy", 49000.0, 1.0, 49000.0)
        >>> pair.execute_market_order(trader, "sell", 0.5)
    """

    def __init__(self, base_token: Token, quote_token: Token, initial_price: float):
        """
        创建交易对

        Args:
            base_token: 基础代币
            quote_token: 计价代币
            initial_price: 初始价格
        """
        self.base_token = base_token
        self.quote_token = quote_token
        self.price = initial_price
        self.log: List[Tuple[float, float, float]] = []
        self.buy_orders: List[Order] = []
        self.sell_orders: List[Order] = []
        self.clients: Set[Trader] = set()

    def submit_limit_order(
        self, trader: Trader, direction: str, price: float, volume: float, frozen_amount: float
    ) -> None:
        """
        提交限价单到订单簿

        订单按价格优先、时间优先排序：
        - 买单：价格降序（高价优先）
        - 卖单：价格升序（低价优先）

        提交后立即尝试撮合。

        Args:
            trader: 下单交易者
            direction: 'buy' 或 'sell'
            price: 限价
            volume: 数量
            frozen_amount: 冻结资金（买单）或资产（卖单）
        """
        order = Order(trader, direction, price, volume, frozen_amount, self)

        if direction == "buy":
            self.buy_orders.append(order)
            # 价格降序，同价格按时间升序
            self.buy_orders.sort(key=lambda x: (-x.price, x.time))
        else:
            self.sell_orders.append(order)
            # 价格升序，同价格按时间升序
            self.sell_orders.sort(key=lambda x: (x.price, x.time))

        trader.orders.append(order)
        self.clients.add(trader)
        self._match_orders()

    def execute_market_order(
        self, trader: Trader, direction: str, volume: float
    ) -> Tuple[float, List[Dict]]:
        """
        执行市价单 - 立即以最优价格成交

        市价单会遍历对手方订单簿，尽可能成交指定数量。
        如果市场深度不足，只成交可成交的部分。

        Args:
            trader: 下单交易者
            direction: 'buy' 或 'sell'
            volume: 目标成交量

        Returns:
            (实际成交量, 成交明细列表)
            成交明细包含: price, volume, cost/revenue, counterparty
        """
        if volume <= 0:
            return 0.0, []

        executed_volume = 0.0
        total_cost_or_revenue = 0.0
        trade_details: List[Dict] = []

        if direction == "buy":
            # 买入：与卖单簿撮合
            while volume > 0 and self.sell_orders:
                sell_order = self.sell_orders[0]
                match_volume = min(volume, sell_order.remaining_volume)
                match_price = sell_order.price
                match_cost = match_volume * match_price

                # 检查买家余额
                available = trader.assets.get(self.quote_token, 0.0)
                if available < match_cost:
                    if available > 0:
                        max_buy_volume = available / match_price
                        if max_buy_volume > 0.000001:
                            match_volume = min(match_volume, max_buy_volume)
                            match_cost = match_volume * match_price
                        else:
                            break
                    else:
                        break

                # 执行交易
                trader.assets[self.quote_token] = available - match_cost
                trader.assets[self.base_token] = (
                    trader.assets.get(self.base_token, 0.0) + match_volume
                )

                seller = sell_order.trader
                sell_order.remaining_frozen -= match_volume
                seller.assets[self.quote_token] = (
                    seller.assets.get(self.quote_token, 0.0) + match_cost
                )

                # 记录成交
                self.log.append((time.time(), match_price, match_volume))
                self.price = match_price

                trade_details.append({
                    "price": match_price,
                    "volume": match_volume,
                    "cost": match_cost,
                    "counterparty": seller,
                })

                volume -= match_volume
                executed_volume += match_volume
                total_cost_or_revenue += match_cost
                sell_order.executed += match_volume

                # 完成订单处理
                if sell_order.remaining_volume < 0.000001:
                    if sell_order in seller.orders:
                        seller.orders.remove(sell_order)
                    self.sell_orders.remove(sell_order)

        else:  # sell
            # 卖出：与买单簿撮合
            while volume > 0 and self.buy_orders:
                buy_order = self.buy_orders[0]
                match_volume = min(volume, buy_order.remaining_volume)
                match_price = buy_order.price
                match_revenue = match_volume * match_price

                # 检查卖家余额
                available = trader.assets.get(self.base_token, 0.0)
                if available < match_volume:
                    if available > 0.000001:
                        match_volume = available
                        match_revenue = match_volume * match_price
                    else:
                        break

                # 执行交易
                trader.assets[self.base_token] = available - match_volume
                trader.assets[self.quote_token] = (
                    trader.assets.get(self.quote_token, 0.0) + match_revenue
                )

                buyer = buy_order.trader
                buy_order.remaining_frozen -= match_revenue
                buyer.assets[self.base_token] = (
                    buyer.assets.get(self.base_token, 0.0) + match_volume
                )

                # 记录成交
                self.log.append((time.time(), match_price, match_volume))
                self.price = match_price

                trade_details.append({
                    "price": match_price,
                    "volume": match_volume,
                    "revenue": match_revenue,
                    "counterparty": buyer,
                })

                volume -= match_volume
                executed_volume += match_volume
                total_cost_or_revenue += match_revenue
                buy_order.executed += match_volume

                # 完成订单处理
                if buy_order.remaining_volume < 0.000001:
                    if buy_order in buyer.orders:
                        buyer.orders.remove(buy_order)
                    self.buy_orders.remove(buy_order)

        return executed_volume, trade_details

    def _match_orders(self) -> None:
        """
        撮合订单 - 匹配可成交的买卖单

        撮合规则：
        1. 取最优买单（最高价）和最优卖单（最低价）
        2. 如果买价 >= 卖价，可以成交
        3. 成交量为 min(买剩余, 卖剩余)
        4. 成交价为卖单价格（被动方价格）
        5. 重复直到无法撮合
        """
        while self.buy_orders and self.sell_orders:
            best_buy = self.buy_orders[0]
            best_sell = self.sell_orders[0]

            # 检查价格是否匹配
            if best_buy.price < best_sell.price:
                break

            match_volume = min(best_buy.remaining_volume, best_sell.remaining_volume)
            match_price = best_sell.price

            buyer = best_buy.trader
            seller = best_sell.trader

            # 检查买家资金
            buyer_quote = buyer.assets.get(self.quote_token, 0.0)
            required_quote = match_volume * match_price
            if buyer_quote < required_quote:
                # 资金不足，强制取消买单
                best_buy.close(force=True)
                continue

            # 检查卖家资产
            seller_base = seller.assets.get(self.base_token, 0.0)
            if seller_base < match_volume:
                if seller_base <= 0:
                    # 资产不足，强制取消卖单
                    best_sell.close(force=True)
                    continue
                else:
                    # 部分成交
                    match_volume = seller_base
                    required_quote = match_volume * match_price

            # 执行交易
            buyer.assets[self.quote_token] = buyer_quote - required_quote
            buyer.assets[self.base_token] = (
                buyer.assets.get(self.base_token, 0.0) + match_volume
            )

            seller.assets[self.base_token] = seller_base - match_volume
            seller.assets[self.quote_token] = (
                seller.assets.get(self.quote_token, 0.0) + required_quote
            )

            best_buy.remaining_frozen -= required_quote
            best_sell.remaining_frozen -= match_volume

            best_buy.executed += match_volume
            best_sell.executed += match_volume

            # 记录成交
            self.log.append((time.time(), match_price, match_volume))
            self.price = match_price

            # 完成订单处理
            if best_buy.remaining_volume < 0.000001:
                if best_buy in buyer.orders:
                    buyer.orders.remove(best_buy)
                self.buy_orders.remove(best_buy)

            if best_sell.remaining_volume < 0.000001:
                if best_sell in seller.orders:
                    seller.orders.remove(best_sell)
                self.sell_orders.remove(best_sell)

    def get_order_book(self, depth: int = 10) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """
        获取订单簿快照

        Args:
            depth: 返回的档位深度

        Returns:
            (买单列表, 卖单列表)，每项为 (价格, 数量)
        """
        buys = [(order.price, order.remaining_volume) for order in self.buy_orders[:depth]]
        sells = [(order.price, order.remaining_volume) for order in self.sell_orders[:depth]]
        return buys, sells

    def get_market_depth(self) -> Dict[str, float]:
        """
        获取市场深度统计

        Returns:
            包含以下字段的字典:
            - buy_orders: 买单数量
            - sell_orders: 卖单数量
            - buy_volume: 买单总量
            - sell_volume: 卖单总量
            - buy_value: 买单总价值
            - sell_value: 卖单总价值
        """
        buy_volume = sum(o.remaining_volume for o in self.buy_orders)
        sell_volume = sum(o.remaining_volume for o in self.sell_orders)
        buy_value = sum(o.remaining_volume * o.price for o in self.buy_orders)
        sell_value = sum(o.remaining_volume * o.price for o in self.sell_orders)

        return {
            "buy_orders": len(self.buy_orders),
            "sell_orders": len(self.sell_orders),
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "buy_value": buy_value,
            "sell_value": sell_value,
        }
