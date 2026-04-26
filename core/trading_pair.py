"""
TradingPair 模块 - 普通交易对

管理基础代币/计价代币的交易对，提供：
- 限价单订单簿管理（买单簿、卖单簿）
- 订单撮合引擎（线程安全）
- 市价单执行
- 市场深度查询

线程安全说明：
- 所有订单簿操作都受 _lock 保护
- 撮合过程是原子操作
- 支持多线程并发访问
"""

import time
from typing import List, Dict, Tuple, Optional, Set
from threading import Lock
from decimal import Decimal

from .trader import Trader
from .order import Order
from .token import Token
from .utils import to_decimal, D0, D1
from .engine_node import EngineNode


class TradingPair(EngineNode):
    """
    普通交易对 - 管理订单簿和撮合逻辑

    交易对由基础代币(base)和计价代币(quote)组成，
    价格表示为 1 base = X quote。

    AMM 做市逻辑已提取到 ReflexiveMarketMaker (RMM) 中，
    通过 self.rmm 访问。

    Attributes:
        base_token: 基础代币（被交易的资产）
        quote_token: 计价代币（定价资产，如USDT）
        price: 当前市场价格
        log: 成交记录列表 [(timestamp, price, volume, buyer_fee, seller_fee), ...]
        buy_orders: 买单列表（按价格降序）
        sell_orders: 卖单列表（按价格升序）
        clients: 参与此交易对的交易者集合

    Examples:
        >>> pair = TradingPair(btc, usdt, 50000.0)
        >>> pair.submit_limit_order(trader, "buy", 49000.0, 1.0, 49000.0)
        >>> pair.execute_market_order(trader, "sell", 0.5)
    """

    def __init__(
        self,
        base_token: Token,
        quote_token: Token,
        initial_price,
    ):
        """
        创建交易对

        Args:
            base_token: 基础代币
            quote_token: 计价代币
            initial_price: 初始价格
        """
        super().__init__(f"{base_token.token_id}/{quote_token.token_id}")
        self.base_token = base_token
        self.quote_token = quote_token
        self.price = to_decimal(initial_price)
        self.log: List[Tuple[float, Decimal, Decimal, Decimal, Decimal]] = []
        self.buy_orders: List[Order] = []
        self.sell_orders: List[Order] = []
        self.clients: Set[Trader] = set()

        self._lock = Lock()

        self.consensus_price = self.price

    @property
    def rmm(self):
        """获取关联的反射性做市商实例"""
        if self._engine and hasattr(self._engine, "rmm"):
            return self._engine.rmm
        return None

    def get_amm_reserves(self) -> Tuple[Decimal, Decimal]:
        """
        获取 AMM 池储备

        Returns:
            (base_token 储备, quote_token 储备)
        """
        if self.rmm is None:
            return D0, D0
        return self.rmm.get_reserves(self)

    def get_amm_price(self) -> Decimal:
        """
        获取 AMM 池的隐含价格

        Returns:
            AMM 池的隐含价格（quote/base），如果储备为0返回共识价格
        """
        if self.rmm is None:
            return self.consensus_price
        return self.rmm.get_price(self)

    @property
    def amm_current_fee_rate(self) -> Decimal:
        """获取当前 AMM 手续费比例"""
        if self.rmm is None:
            return D0
        return self.rmm.get_current_fee_rate(self)

    def update_consensus_price(self) -> None:
        """
        更新共识价格为买卖盘口的平均价格

        当其中一方不存在时，设为另一方价格。
        当双方都不存在时，保持当前共识价格。
        """
        best_buy = self.buy_orders[0].price if self.buy_orders else None
        best_sell = self.sell_orders[0].price if self.sell_orders else None

        if best_buy is not None and best_sell is not None:
            self.consensus_price = (best_buy + best_sell) / to_decimal("2")
        elif best_buy is not None:
            self.consensus_price = best_buy
        elif best_sell is not None:
            self.consensus_price = best_sell

    def submit_limit_order(
        self, trader: Trader, direction: str, price, volume, frozen_amount
    ) -> None:
        """
        提交限价单到订单簿（线程安全）

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
                         注意：资金已在 Trader.submit_limit_order 中被扣除
        """
        with self._lock:
            frozen_amount = to_decimal(frozen_amount)
            # 注意：资金已经在 Trader.submit_limit_order 中被扣除
            # 这里只负责创建订单，不再重复扣除
            order = Order(trader, direction, price, volume, frozen_amount, self)

            if direction == "buy":
                self.buy_orders.append(order)
                self.buy_orders.sort(key=lambda x: (-x.price, x.time))
            else:
                self.sell_orders.append(order)
                self.sell_orders.sort(key=lambda x: (x.price, x.time))

            trader.orders.append(order)
            self.clients.add(trader)
            self._match_orders()

    def execute_market_order(
        self, trader: Trader, direction: str, volume
    ) -> Tuple[Decimal, List[Dict], Decimal]:
        """
        执行市价单 - 立即以最优价格成交（线程安全）

        市价单会遍历对手方订单簿，尽可能成交指定数量。
        如果市场深度不足，只成交可成交的部分。

        Args:
            trader: 下单交易者
            direction: 'buy' 或 'sell'
            volume: 目标成交量

        Returns:
            (实际成交量, 成交明细列表, 总手续费)
        """
        volume = to_decimal(volume)
        if volume <= D0:
            return D0, [], D0

        with self._lock:
            executed_volume = D0
            total_cost_or_revenue = D0
            total_fee = D0
            trade_details: List[Dict] = []
            counterparties: List[Tuple[Trader, Decimal]] = []

            if direction == "buy":
                while volume > D0 and self.sell_orders:
                    sell_order = self.sell_orders[0]
                    match_volume = min(volume, sell_order.remaining_volume)
                    match_price = sell_order.price
                    match_cost = match_volume * match_price

                    available = trader.assets.get(self.quote_token, D0)
                    if available < match_cost:
                        if available > D0:
                            match_volume = available / match_price
                            if match_volume > D0:
                                match_cost = match_volume * match_price
                            else:
                                break
                        else:
                            break

                    trader.assets[self.quote_token] = available - match_cost
                    trader.assets[self.base_token] = (
                        trader.assets.get(self.base_token, D0) + match_volume
                    )

                    seller = sell_order.trader
                    sell_order.remaining_frozen -= match_volume
                    seller.assets[self.quote_token] = (
                        seller.assets.get(self.quote_token, D0) + match_cost
                    )

                    self.log.append((time.time(), match_price, match_volume, D0, D0))
                    self.price = match_price
                    self.update_consensus_price()

                    trade_details.append({
                        "price": match_price,
                        "volume": match_volume,
                        "cost": match_cost,
                        "buyer_fee": D0,
                        "seller_fee": D0,
                        "counterparty": seller,
                    })

                    counterparties.append((seller, match_volume))

                    volume -= match_volume
                    executed_volume += match_volume
                    total_cost_or_revenue += match_cost
                    sell_order.executed += match_volume

                    if sell_order.remaining_volume <= D0:
                        if sell_order in seller.orders:
                            seller.orders.remove(sell_order)
                        self.sell_orders.remove(sell_order)

            else:  # sell
                while volume > D0 and self.buy_orders:
                    buy_order = self.buy_orders[0]
                    match_volume = min(volume, buy_order.remaining_volume)
                    match_price = buy_order.price
                    match_revenue = match_volume * match_price

                    available = trader.assets.get(self.base_token, D0)
                    if available < match_volume:
                        if available > D0:
                            match_volume = available
                            match_revenue = match_volume * match_price
                        else:
                            break

                    trader.assets[self.base_token] = available - match_volume
                    trader.assets[self.quote_token] = (
                        trader.assets.get(self.quote_token, D0) + match_revenue
                    )

                    buyer = buy_order.trader
                    buy_order.remaining_frozen -= match_revenue
                    buyer.assets[self.base_token] = (
                        buyer.assets.get(self.base_token, D0) + match_volume
                    )

                    self.log.append((time.time(), match_price, match_volume, D0, D0))
                    self.price = match_price
                    self.update_consensus_price()

                    trade_details.append({
                        "price": match_price,
                        "volume": match_volume,
                        "revenue": match_revenue,
                        "buyer_fee": D0,
                        "seller_fee": D0,
                        "counterparty": buyer,
                    })

                    counterparties.append((buyer, match_volume))

                    volume -= match_volume
                    executed_volume += match_volume
                    total_cost_or_revenue += match_revenue
                    buy_order.executed += match_volume

                    if buy_order.remaining_volume <= D0:
                        if buy_order in buyer.orders:
                            buyer.orders.remove(buy_order)
                        self.buy_orders.remove(buy_order)

            # 如果订单簿深度不足，使用 RMM 池完成剩余订单
            if volume > D0 and self.rmm and self.rmm.has_liquidity(self):
                amm_volume, amm_trade_details, amm_fee = self.rmm.execute_market_order(
                    self, trader, direction, volume
                )
                executed_volume += amm_volume
                trade_details.extend(amm_trade_details)
                total_fee += amm_fee

            # 市价单执行完成后，根据最新共识价格执行RMM套利
            if self.rmm:
                arbitrage_result = self.rmm.arbitrage_after_match(self)

                if executed_volume > D0 and arbitrage_result.get("direction") != "none":
                    self.rmm.charge_slippage_compensation_market_order(
                        self, trader, counterparties, executed_volume, direction, arbitrage_result
                    )

            return executed_volume, trade_details, total_fee

    def _match_orders(self) -> None:
        """
        撮合订单 - 匹配可成交的买卖单

        撮合规则：
        1. 取最优买单（最高价）和最优卖单（最低价）
        2. 如果买价 >= 卖价，可以成交
        3. 成交量为 min(买剩余, 卖剩余)
        4. 成交价为卖单价格（被动方价格）
        5. 双方都是 Maker（限价单撮合）
        6. 重复直到无法撮合
        """
        while self.buy_orders and self.sell_orders:
            best_buy = self.buy_orders[0]
            best_sell = self.sell_orders[0]

            if best_buy.price < best_sell.price:
                break

            match_volume = min(best_buy.remaining_volume, best_sell.remaining_volume)
            match_price = best_sell.price
            match_amount = match_volume * match_price

            buyer = best_buy.trader
            seller = best_sell.trader

            total_buyer_cost = match_amount
            seller_revenue = match_amount

            if best_buy.remaining_frozen < total_buyer_cost:
                best_buy.close(force=True)
                continue

            seller_base = seller.assets.get(self.base_token, D0)
            if seller_base < match_volume:
                if seller_base <= D0:
                    best_sell.close(force=True)
                    continue
                else:
                    match_volume = seller_base
                    match_amount = match_volume * match_price
                    total_buyer_cost = match_amount
                    seller_revenue = match_amount

            frozen_price = best_buy.price
            actual_cost = match_amount
            excess_frozen = (frozen_price * match_volume) - actual_cost

            buyer.assets[self.base_token] = (
                buyer.assets.get(self.base_token, D0) + match_volume
            )

            seller.assets[self.base_token] = seller_base - match_volume
            seller.assets[self.quote_token] = (
                seller.assets.get(self.quote_token, D0) + seller_revenue
            )

            best_buy.remaining_frozen -= actual_cost
            best_sell.remaining_frozen -= match_volume

            best_buy.executed += match_volume
            best_sell.executed += match_volume

            if excess_frozen > D0:
                buyer.assets[self.quote_token] = buyer.assets.get(self.quote_token, D0) + excess_frozen

            self.log.append((time.time(), match_price, match_volume, D0, D0))
            self.price = match_price
            self.update_consensus_price()

            # 撮合后立即执行RMM套利
            if self.rmm:
                arbitrage_result = self.rmm.arbitrage_after_match(self)

                self.rmm.charge_slippage_compensation(
                    self, buyer, seller, arbitrage_result, match_volume, match_price
                )

            if best_buy.remaining_volume <= D0:
                if best_buy in buyer.orders:
                    buyer.orders.remove(best_buy)
                if best_buy in self.buy_orders:
                    self.buy_orders.remove(best_buy)

            if best_sell.remaining_volume <= D0:
                if best_sell in seller.orders:
                    seller.orders.remove(best_sell)
                if best_sell in self.sell_orders:
                    self.sell_orders.remove(best_sell)

    def get_order_book(self, depth: int = 10) -> Tuple[List[Tuple[Decimal, Decimal]], List[Tuple[Decimal, Decimal]]]:
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

    def get_market_depth(self) -> Dict[str, Decimal]:
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

    def step(self, dt: Decimal) -> None:
        """
        市场模拟步进回调

        Args:
            dt: 时间步长（秒）
        """
        self.update_consensus_price()
