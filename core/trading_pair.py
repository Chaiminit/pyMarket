"""
TradingPair 模块 - 普通交易对

管理基础代币/计价代币的交易对，提供：
- 限价单订单簿管理（买单簿、卖单簿）
- 订单撮合引擎（线程安全）
- 市价单执行
- 市场深度查询
- 手续费计算和收取

线程安全说明：
- 所有订单簿操作都受 _lock 保护
- 撮合过程是原子操作
- 支持多线程并发访问
"""

import time
import math
import random
from typing import List, Dict, Tuple, Optional, Set
from threading import Lock
from decimal import Decimal

from .trader import Trader
from .order import Order
from .token import Token
from .fees import FeeConfig, FeeCalculator, FeeCollector
from .utils import to_decimal, D0, D1


class TradingPair:
    """
    普通交易对 - 管理订单簿和撮合逻辑

    交易对由基础代币(base)和计价代币(quote)组成，
    价格表示为 1 base = X quote。

    Attributes:
        base_token: 基础代币（被交易的资产）
        quote_token: 计价代币（定价资产，如USDT）
        price: 当前市场价格
        log: 成交记录列表 [(timestamp, price, volume, buyer_fee, seller_fee), ...]
        buy_orders: 买单列表（按价格降序）
        sell_orders: 卖单列表（按价格升序）
        clients: 参与此交易对的交易者集合
        fee_config: 手续费配置
        fee_calculator: 手续费计算器
        fee_collector: 手续费收集器

    Examples:
        >>> pair = TradingPair(btc, usdt, 50000.0)
        >>> pair.set_fee_config(FeePresets.standard())  # 设置标准费率
        >>> pair.submit_limit_order(trader, "buy", 49000.0, 1.0, 49000.0)
        >>> pair.execute_market_order(trader, "sell", 0.5)
    """

    def __init__(
        self,
        base_token: Token,
        quote_token: Token,
        initial_price,
        fee_config: Optional[FeeConfig] = None
    ):
        """
        创建交易对

        Args:
            base_token: 基础代币
            quote_token: 计价代币
            initial_price: 初始价格
            fee_config: 手续费配置，默认零手续费
        """
        self.base_token = base_token
        self.quote_token = quote_token
        self.price = to_decimal(initial_price)
        self.log: List[Tuple[float, Decimal, Decimal, Decimal, Decimal]] = []
        self.buy_orders: List[Order] = []
        self.sell_orders: List[Order] = []
        self.clients: Set[Trader] = set()

        # 线程锁，用于保护订单簿和撮合操作
        self._lock = Lock()

        # 手续费系统（默认为零手续费）
        self.fee_config = fee_config or FeeConfig()
        self.fee_calculator = FeeCalculator(self.fee_config)
        self.fee_collector = FeeCollector(self.fee_config.fee_recipient)

    def set_fee_config(self, fee_config: FeeConfig) -> None:
        """
        设置手续费配置

        Args:
            fee_config: 新的手续费配置
        """
        self.fee_config = fee_config
        self.fee_calculator = FeeCalculator(fee_config)
        self.fee_collector = FeeCollector(fee_config.fee_recipient)

    def get_fee_config(self) -> FeeConfig:
        """
        获取当前手续费配置

        Returns:
            当前手续费配置
        """
        return self.fee_config

    def get_collected_fees(self, token: Optional[Token] = None):
        """
        获取已收集的手续费

        Args:
            token: Token 对象，None 则返回所有

        Returns:
            指定代币的手续费金额，或所有代币的手续费字典
        """
        return self.fee_collector.get_collected(token)

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
        """
        with self._lock:
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
            成交明细包含: price, volume, cost/revenue, counterparty, buyer_fee, seller_fee
        """
        volume = to_decimal(volume)
        if volume <= D0:
            return D0, [], D0

        with self._lock:
            executed_volume = D0
            total_cost_or_revenue = D0
            total_fee = D0
            trade_details: List[Dict] = []

            if direction == "buy":
                # 买入：与卖单簿撮合（市价买单是Taker，限价卖单是Maker）
                while volume > D0 and self.sell_orders:
                    sell_order = self.sell_orders[0]
                    match_volume = min(volume, sell_order.remaining_volume)
                    match_price = sell_order.price
                    match_cost = match_volume * match_price

                    # 检查买家余额（需要支付金额 + 手续费）
                    buyer_fee = self.fee_calculator.calculate(match_cost, is_taker=True, is_buyer=True)
                    total_required = match_cost + buyer_fee

                    available = trader.assets.get(self.quote_token, D0)
                    if available < total_required:
                        if available > D0:
                            # 重新计算可购买的量（考虑手续费）
                            fee_rate = buyer_fee / match_cost if match_cost > D0 else D0
                            max_cost = available / (D1 + fee_rate) if fee_rate > D0 else available
                            max_buy_volume = max_cost / match_price
                            if max_buy_volume > D0:
                                match_volume = min(match_volume, max_buy_volume)
                                match_cost = match_volume * match_price
                                buyer_fee = self.fee_calculator.calculate(match_cost, is_taker=True, is_buyer=True)
                                total_required = match_cost + buyer_fee
                            else:
                                break
                        else:
                            break

                    # 计算卖方手续费
                    seller_fee = self.fee_calculator.calculate(match_cost, is_taker=False, is_buyer=False)
                    seller_revenue = match_cost - seller_fee

                    # 执行交易
                    trader.assets[self.quote_token] = available - total_required
                    trader.assets[self.base_token] = (
                        trader.assets.get(self.base_token, D0) + match_volume
                    )

                    seller = sell_order.trader
                    sell_order.remaining_frozen -= match_volume
                    seller.assets[self.quote_token] = (
                        seller.assets.get(self.quote_token, D0) + seller_revenue
                    )

                    # 收取手续费
                    self.fee_collector.collect(self.quote_token, buyer_fee, {
                        "trader": trader.name,
                        "direction": "buy",
                        "is_taker": True,
                        "volume": match_volume,
                        "price": match_price
                    })
                    self.fee_collector.collect(self.quote_token, seller_fee, {
                        "trader": seller.name,
                        "direction": "sell",
                        "is_taker": False,
                        "volume": match_volume,
                        "price": match_price
                    })

                    # 记录成交
                    self.log.append((time.time(), match_price, match_volume, buyer_fee, seller_fee))
                    self.price = match_price

                    trade_details.append({
                        "price": match_price,
                        "volume": match_volume,
                        "cost": match_cost,
                        "buyer_fee": buyer_fee,
                        "seller_fee": seller_fee,
                        "counterparty": seller,
                    })

                    volume -= match_volume
                    executed_volume += match_volume
                    total_cost_or_revenue += match_cost
                    total_fee += buyer_fee + seller_fee
                    sell_order.executed += match_volume

                    # 完成订单处理
                    if sell_order.remaining_volume <= D0:
                        if sell_order in seller.orders:
                            seller.orders.remove(sell_order)
                        self.sell_orders.remove(sell_order)

            else:  # sell
                # 卖出：与买单簿撮合（市价卖单是Taker，限价买单是Maker）
                while volume > D0 and self.buy_orders:
                    buy_order = self.buy_orders[0]
                    match_volume = min(volume, buy_order.remaining_volume)
                    match_price = buy_order.price
                    match_revenue = match_volume * match_price

                    # 检查卖家余额
                    available = trader.assets.get(self.base_token, D0)
                    if available < match_volume:
                        if available > D0:
                            match_volume = available
                            match_revenue = match_volume * match_price
                        else:
                            break

                    # 计算手续费
                    seller_fee = self.fee_calculator.calculate(match_revenue, is_taker=True, is_buyer=False)
                    buyer_fee = self.fee_calculator.calculate(match_revenue, is_taker=False, is_buyer=True)

                    seller_net_revenue = match_revenue - seller_fee
                    buyer_cost = match_revenue + buyer_fee

                    # 执行交易
                    trader.assets[self.base_token] = available - match_volume
                    trader.assets[self.quote_token] = (
                        trader.assets.get(self.quote_token, D0) + seller_net_revenue
                    )

                    buyer = buy_order.trader
                    buy_order.remaining_frozen -= buyer_cost
                    buyer.assets[self.base_token] = (
                        buyer.assets.get(self.base_token, D0) + match_volume
                    )

                    # 收取手续费
                    self.fee_collector.collect(self.quote_token, seller_fee, {
                        "trader": trader.name,
                        "direction": "sell",
                        "is_taker": True,
                        "volume": match_volume,
                        "price": match_price
                    })
                    self.fee_collector.collect(self.quote_token, buyer_fee, {
                        "trader": buyer.name,
                        "direction": "buy",
                        "is_taker": False,
                        "volume": match_volume,
                        "price": match_price
                    })

                    # 记录成交
                    self.log.append((time.time(), match_price, match_volume, buyer_fee, seller_fee))
                    self.price = match_price

                    trade_details.append({
                        "price": match_price,
                        "volume": match_volume,
                        "revenue": match_revenue,
                        "buyer_fee": buyer_fee,
                        "seller_fee": seller_fee,
                        "counterparty": buyer,
                    })

                    volume -= match_volume
                    executed_volume += match_volume
                    total_cost_or_revenue += match_revenue
                    total_fee += buyer_fee + seller_fee
                    buy_order.executed += match_volume

                    # 完成订单处理
                    if buy_order.remaining_volume <= D0:
                        if buy_order in buyer.orders:
                            buyer.orders.remove(buy_order)
                        self.buy_orders.remove(buy_order)

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

            # 检查价格是否匹配
            if best_buy.price < best_sell.price:
                break

            match_volume = min(best_buy.remaining_volume, best_sell.remaining_volume)
            match_price = best_sell.price
            match_amount = match_volume * match_price

            buyer = best_buy.trader
            seller = best_sell.trader

            # 计算手续费（限价单撮合，双方都是 Maker）
            buyer_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=True)
            seller_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=False)

            total_buyer_cost = match_amount + buyer_fee
            seller_revenue = match_amount - seller_fee

            # 检查买家资金（使用剩余冻结资金）
            # 买家下单时已经冻结了资金，使用 remaining_frozen 检查是否足够
            if best_buy.remaining_frozen < total_buyer_cost:
                # 资金不足，强制取消买单
                best_buy.close(force=True)
                continue

            # 检查卖家资产
            seller_base = seller.assets.get(self.base_token, D0)
            if seller_base < match_volume:
                if seller_base <= D0:
                    # 资产不足，强制取消卖单
                    best_sell.close(force=True)
                    continue
                else:
                    # 部分成交
                    match_volume = seller_base
                    match_amount = match_volume * match_price
                    buyer_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=True)
                    seller_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=False)
                    total_buyer_cost = match_amount + buyer_fee
                    seller_revenue = match_amount - seller_fee

            # 计算多冻结的手续费（冻结时按Taker，实际按Maker）
            taker_fee_estimate = match_amount * self.fee_config.taker_rate
            maker_fee_actual = buyer_fee
            excess_frozen = taker_fee_estimate - maker_fee_actual

            # 执行交易
            # 从冻结资金中扣除成本，剩余部分（包括多余冻结的手续费）会返还
            buyer.assets[self.base_token] = (
                buyer.assets.get(self.base_token, D0) + match_volume
            )

            seller.assets[self.base_token] = seller_base - match_volume
            seller.assets[self.quote_token] = (
                seller.assets.get(self.quote_token, D0) + seller_revenue
            )

            best_buy.remaining_frozen -= total_buyer_cost
            best_sell.remaining_frozen -= match_volume

            best_buy.executed += match_volume
            best_sell.executed += match_volume

            # 返还多冻结的手续费到买家资产
            if excess_frozen > D0:
                buyer.assets[self.quote_token] = buyer.assets.get(self.quote_token, D0) + excess_frozen

            # 收取手续费
            self.fee_collector.collect(self.quote_token, buyer_fee, {
                "trader": buyer.name,
                "direction": "buy",
                "is_taker": False,
                "volume": match_volume,
                "price": match_price
            })
            self.fee_collector.collect(self.quote_token, seller_fee, {
                "trader": seller.name,
                "direction": "sell",
                "is_taker": False,
                "volume": match_volume,
                "price": match_price
            })

            # 记录成交
            self.log.append((time.time(), match_price, match_volume, buyer_fee, seller_fee))
            self.price = match_price

            # 完成订单处理
            if best_buy.remaining_volume <= D0:
                if best_buy in buyer.orders:
                    buyer.orders.remove(best_buy)
                self.buy_orders.remove(best_buy)

            if best_sell.remaining_volume <= D0:
                if best_sell in seller.orders:
                    seller.orders.remove(best_sell)
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

        每个模拟步进时由 Engine 调用，子类可以重写此方法
        来实现自定义的每步逻辑（如价格更新、订单检查等）。

        Args:
            dt: 时间步长（秒）

        Examples:
            >>> class MyTradingPair(TradingPair):
            ...     def step(self, dt):
            ...         # 每步更新价格
            ...         self.update_price()
        """
        pass
