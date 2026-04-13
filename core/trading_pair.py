import time
import math
import random
from typing import List, Dict, Tuple, Optional

from .trader import Trader
from .order import Order


class TradingPair:
    """普通交易对 - 管理订单簿和撮合，直接操作 Trader 对象"""

    def __init__(self, base_token: str, quote_token: str, initial_price: float):
        self.base_token = base_token
        self.quote_token = quote_token
        self.price = initial_price
        self.log = []
        self.buy_orders: List[Order] = []
        self.sell_orders: List[Order] = []
        self.clients = set()
        self.pair_id = -1  # 由 Engine 在创建时设置

    def submit_limit_order(
        self, trader: Trader, direction: str, price: float, volume: float, frozen_amount: float
    ):
        """提交限价单"""
        order = Order(trader, direction, price, volume, frozen_amount, self.pair_id, self)

        if direction == "buy":
            self.buy_orders.append(order)
            self.buy_orders.sort(key=lambda x: (-x.price, x.time))
        else:
            self.sell_orders.append(order)
            self.sell_orders.sort(key=lambda x: (x.price, x.time))

        trader.orders.append(order)
        self.clients.add(id(trader))
        self._match_orders()

    def execute_market_order(
        self, trader: Trader, direction: str, volume: float
    ) -> Tuple[float, List[Dict]]:
        """
        执行市价单，返回 (实际成交量，成交明细列表)
        如果请求量大于市场深度，会交易掉所有能交易的量
        """
        if volume <= 0:
            return 0.0, []

        executed_volume = 0.0
        total_cost_or_revenue = 0.0
        trade_details = []

        if direction == "buy":
            while volume > 0 and self.sell_orders:
                sell_order = self.sell_orders[0]
                match_volume = min(volume, sell_order.volume - sell_order.executed)
                match_price = sell_order.price
                match_cost = match_volume * match_price

                # 检查余额是否足够
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
                trader.assets[self.quote_token] = (
                    trader.assets.get(self.quote_token, 0.0) - match_cost
                )
                trader.assets[self.base_token] = (
                    trader.assets.get(self.base_token, 0.0) + match_volume
                )

                seller = sell_order.trader
                sell_order.remaining_frozen -= match_volume
                seller.assets[self.quote_token] = (
                    seller.assets.get(self.quote_token, 0.0) + match_cost
                )

                self.log.append((time.time(), match_price, match_volume))
                self.price = match_price

                trade_details.append(
                    {
                        "price": match_price,
                        "volume": match_volume,
                        "cost": match_cost,
                        "order_id": id(sell_order),
                    }
                )

                volume -= match_volume
                executed_volume += match_volume
                total_cost_or_revenue += match_cost
                sell_order.executed += match_volume

                if sell_order.executed >= sell_order.volume:
                    sell_order.close()  # 关闭卖单
        else:
            while volume > 0 and self.buy_orders:
                buy_order = self.buy_orders[0]
                match_volume = min(volume, buy_order.volume - buy_order.executed)
                match_price = buy_order.price
                match_revenue = match_volume * match_price

                # 检查余额是否足够
                available = trader.assets.get(self.base_token, 0.0)
                if available < match_volume:
                    if available > 0.000001:
                        match_volume = min(match_volume, available)
                        match_revenue = match_volume * match_price
                    else:
                        break

                # 执行交易
                trader.assets[self.base_token] = (
                    trader.assets.get(self.base_token, 0.0) - match_volume
                )
                trader.assets[self.quote_token] = (
                    trader.assets.get(self.quote_token, 0.0) + match_revenue
                )

                buyer = buy_order.trader
                frozen_used = match_volume * match_price
                buy_order.remaining_frozen -= frozen_used
                buyer.assets[self.base_token] = (
                    buyer.assets.get(self.base_token, 0.0) + match_volume
                )

                self.log.append((time.time(), match_price, match_volume))
                self.price = match_price

                trade_details.append(
                    {
                        "price": match_price,
                        "volume": match_volume,
                        "revenue": match_revenue,
                        "order_id": id(buy_order),
                    }
                )

                volume -= match_volume
                executed_volume += match_volume
                total_cost_or_revenue += match_revenue
                buy_order.executed += match_volume

                if buy_order.executed >= buy_order.volume:
                    buy_order.close()  # 关闭买单

        self.clients.add(id(trader))
        return executed_volume, trade_details

    def _match_orders(self):
        """撮合订单"""
        while self.buy_orders and self.sell_orders:
            buy_order = self.buy_orders[0]
            sell_order = self.sell_orders[0]

            if buy_order.price < sell_order.price:
                break

            match_price = buy_order.price if buy_order.time <= sell_order.time else sell_order.price

            buy_remaining = buy_order.volume - buy_order.executed
            sell_remaining = sell_order.volume - sell_order.executed
            match_volume = min(buy_remaining, sell_remaining)
            match_cost = match_volume * match_price

            buy_order.executed += match_volume
            sell_order.executed += match_volume

            buyer = buy_order.trader
            seller = sell_order.trader

            # 买方获得 base_token
            buyer.assets[self.base_token] = buyer.assets.get(self.base_token, 0.0) + match_volume
            buy_order.remaining_frozen -= match_cost

            # 卖方获得 quote_token
            seller.assets[self.quote_token] = seller.assets.get(self.quote_token, 0.0) + match_cost
            sell_order.remaining_frozen -= match_volume

            self.log.append((time.time(), match_price, match_volume))
            self.price = match_price

            if buy_order.executed >= buy_order.volume:
                buy_order.close()  # 关闭买单

            if sell_order.executed >= sell_order.volume:
                sell_order.close()  # 关闭卖单

    def cancel_orders_for_bot(self, trader: Trader):
        """取消某交易者的所有订单"""
        bot_id = id(trader)

        # 关闭该交易者的所有订单
        for order in list(self.buy_orders):
            if id(order.trader) == bot_id:
                order.close()

        for order in list(self.sell_orders):
            if id(order.trader) == bot_id:
                order.close()
