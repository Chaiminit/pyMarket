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
from .engine_node import EngineNode


class TradingPair(EngineNode):
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
        super().__init__(f"{base_token.name}/{quote_token.name}")
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

        # AMM 恒定乘积做市商系统
        self.amm_enabled = False
        self.amm_reserve_base = D0  # base_token 储备
        self.amm_reserve_quote = D0  # quote_token 储备
        self.amm_k = D0  # 恒定乘积 k = reserve_base * reserve_quote

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

    def enable_amm(
        self,
        base_amount,
        quote_amount,
    ) -> None:
        """
        启用恒定乘积做市商模型（AMM）

        AMM 池作为最后的做市商，在市场流动性枯竭时提供流动性。
        储备比例必须等于当前价格，即 reserve_quote / reserve_base = price

        Args:
            base_amount: base_token 的储备量
            quote_amount: quote_token 的储备量

        Raises:
            ValueError: 如果储备比例不等于当前价格
        """
        base_amount = to_decimal(base_amount)
        quote_amount = to_decimal(quote_amount)

        if base_amount <= D0 or quote_amount <= D0:
            raise ValueError("储备量必须大于0")

        # 检查储备比例是否等于当前价格
        implied_price = quote_amount / base_amount
        if implied_price != self.price:
            raise ValueError(
                f"储备比例必须等于当前价格: "
                f"储备比例={implied_price}, 当前价格={self.price}"
            )

        self.amm_enabled = True
        self.amm_reserve_base = base_amount
        self.amm_reserve_quote = quote_amount
        self.amm_k = base_amount * quote_amount

    def disable_amm(self) -> None:
        """
        禁用 AMM 做市商模型
        """
        self.amm_enabled = False
        self.amm_reserve_base = D0
        self.amm_reserve_quote = D0
        self.amm_k = D0

    def get_amm_reserves(self) -> Tuple[Decimal, Decimal]:
        """
        获取 AMM 池储备

        Returns:
            (base_token 储备, quote_token 储备)
        """
        return self.amm_reserve_base, self.amm_reserve_quote

    def get_amm_price(self) -> Decimal:
        """
        获取 AMM 池的隐含价格

        Returns:
            AMM 池的隐含价格（quote/base），如果 AMM 未启用返回 0
        """
        if not self.amm_enabled or self.amm_reserve_base == D0:
            return D0
        return self.amm_reserve_quote / self.amm_reserve_base

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
            # 真正冻结资金/资产
            frozen_amount = to_decimal(frozen_amount)
            if direction == "buy":
                # 买单：冻结计价代币(USDT)
                if trader.assets.get(self.quote_token, D0) < frozen_amount:
                    raise ValueError(
                        f"余额不足：需要 {frozen_amount} {self.quote_token.name}，"
                        f"可用 {trader.assets.get(self.quote_token, D0)}"
                    )
                trader.assets[self.quote_token] = trader.assets.get(self.quote_token, D0) - frozen_amount
            else:
                # 卖单：冻结基础代币
                if trader.assets.get(self.base_token, D0) < frozen_amount:
                    raise ValueError(
                        f"余额不足：需要 {frozen_amount} {self.base_token.name}，"
                        f"可用 {trader.assets.get(self.base_token, D0)}"
                    )
                trader.assets[self.base_token] = trader.assets.get(self.base_token, D0) - frozen_amount

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
                    buyer_fee = self.fee_calculator.calculate(match_cost, is_taker=True, is_buyer=True, trader=trader)
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
                                buyer_fee = self.fee_calculator.calculate(match_cost, is_taker=True, is_buyer=True, trader=trader)
                                total_required = match_cost + buyer_fee
                            else:
                                break
                        else:
                            break

                    # 计算卖方手续费
                    seller_fee = self.fee_calculator.calculate(match_cost, is_taker=False, is_buyer=False, trader=sell_order.trader)
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
                    seller_fee = self.fee_calculator.calculate(match_revenue, is_taker=True, is_buyer=False, trader=trader)
                    buyer_fee = self.fee_calculator.calculate(match_revenue, is_taker=False, is_buyer=True, trader=buy_order.trader)

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

            # 如果订单簿深度不足且 AMM 已启用，使用 AMM 池完成剩余订单
            if volume > D0 and self.amm_enabled:
                amm_volume, amm_trade_details, amm_fee = self._execute_amm_market_order(
                    trader, direction, volume
                )
                executed_volume += amm_volume
                trade_details.extend(amm_trade_details)
                total_fee += amm_fee

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
            buyer_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=True, trader=buyer)
            seller_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=False, trader=seller)

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
                    buyer_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=True, trader=buyer)
                    seller_fee = self.fee_calculator.calculate(match_amount, is_taker=False, is_buyer=False, trader=seller)
                    total_buyer_cost = match_amount + buyer_fee
                    seller_revenue = match_amount - seller_fee

            # 计算多冻结的资金（实际成交价格 vs 冻结时价格）
            # 买家冻结时按limit_price，实际按match_price
            frozen_price = best_buy.price
            actual_cost = match_amount + buyer_fee
            excess_frozen = (frozen_price * match_volume) - actual_cost

            # 执行交易
            # 1. 买家收到商品（基础代币）
            buyer.assets[self.base_token] = (
                buyer.assets.get(self.base_token, D0) + match_volume
            )

            # 2. 卖家减少商品，收到USDT
            seller.assets[self.base_token] = seller_base - match_volume
            seller.assets[self.quote_token] = (
                seller.assets.get(self.quote_token, D0) + seller_revenue
            )

            # 3. 更新冻结资金计数（扣除实际成交成本）
            best_buy.remaining_frozen -= actual_cost
            best_sell.remaining_frozen -= match_volume

            best_buy.executed += match_volume
            best_sell.executed += match_volume

            # 4. 返还买家多冻结的资金（按实际成交价格计算）
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

        如果 AMM 已启用，此方法会自动执行套利逻辑，
        使 AMM 池的隐含价格向市场价格收敛。

        Args:
            dt: 时间步长（秒）

        Examples:
            >>> class MyTradingPair(TradingPair):
            ...     def step(self, dt):
            ...         # 每步更新价格
            ...         self.update_price()
        """
        if self.amm_enabled:
            self._amm_arbitrage(dt)

    def _amm_arbitrage(self, dt: Decimal) -> None:
        """
        AMM 套利逻辑

        当市场价格与 AMM 池隐含价格不一致时，通过模拟套利交易
        使池子储备调整，直到隐含价格等于市场价格。

        套利方向：
        - 如果市场价格 > AMM 价格：套利者买入 base_token，
          池子 base 减少，quote 增加，AMM 价格上升
        - 如果市场价格 < AMM 价格：套利者卖出 base_token，
          池子 base 增加，quote 减少，AMM 价格下降

        Args:
            dt: 时间步长（秒），用于控制套利速度
        """
        if self.amm_reserve_base <= D0 or self.amm_reserve_quote <= D0:
            return

        amm_price = self.get_amm_price()
        market_price = self.price

        # 计算价格差异
        price_diff = market_price - amm_price

        # 如果价格差异很小，不需要套利
        if abs(price_diff) < D0:
            return

        # 套利速度参数（可根据需要调整）
        arbitrage_speed = to_decimal("0.01") * dt

        if price_diff > D0:
            # 市场价格 > AMM 价格，需要买入 base_token 提高 AMM 价格
            # 计算需要买入的 base_token 数量
            # 使用恒定乘积公式：k = reserve_base * reserve_quote
            # 买入 Δbase 后：new_base = reserve_base - Δbase
            #                 new_quote = k / new_base
            # 目标：new_quote / new_base = market_price
            
            # 解方程：k / (reserve_base - Δbase)² = market_price
            # 得到：Δbase = reserve_base - sqrt(k / market_price)
            target_base = (self.amm_k / market_price).sqrt()
            delta_base = self.amm_reserve_base - target_base
            
            # 限制套利量，避免过度调整
            max_delta = self.amm_reserve_base * arbitrage_speed
            delta_base = min(delta_base, max_delta)
            
            if delta_base > D0:
                self._amm_swap_base_for_quote(delta_base)
        else:
            # 市场价格 < AMM 价格，需要卖出 base_token 降低 AMM 价格
            # 卖出 Δbase 后：new_base = reserve_base + Δbase
            #              new_quote = k / new_base
            # 目标：new_quote / new_base = market_price
            
            # 解方程：k / (reserve_base + Δbase)² = market_price
            # 得到：Δbase = sqrt(k / market_price) - reserve_base
            target_base = (self.amm_k / market_price).sqrt()
            delta_base = target_base - self.amm_reserve_base
            
            # 限制套利量
            max_delta = self.amm_reserve_base * arbitrage_speed
            delta_base = min(delta_base, max_delta)
            
            if delta_base > D0:
                self._amm_swap_quote_for_base(delta_base * amm_price)

    def _amm_swap_base_for_quote(self, base_amount: Decimal) -> Decimal:
        """
        AMM 池：用 quote_token 购买 base_token

        根据恒定乘积公式计算需要支付的 quote_token 数量。

        Args:
            base_amount: 要购买的 base_token 数量

        Returns:
            需要支付的 quote_token 数量
        """
        if base_amount <= D0 or base_amount >= self.amm_reserve_base:
            return D0

        # 恒定乘积公式：k = reserve_base * reserve_quote
        # 购买后：new_base = reserve_base - base_amount
        #       new_quote = k / new_base
        # 需要支付的 quote = new_quote - reserve_quote
        
        new_reserve_base = self.amm_reserve_base - base_amount
        new_reserve_quote = self.amm_k / new_reserve_base
        quote_amount = new_reserve_quote - self.amm_reserve_quote

        # 更新储备
        self.amm_reserve_base = new_reserve_base
        self.amm_reserve_quote = new_reserve_quote

        # 更新市场价格为 AMM 隐含价格
        self.price = self.get_amm_price()

        return quote_amount

    def _amm_swap_quote_for_base(self, quote_amount: Decimal) -> Decimal:
        """
        AMM 池：用 base_token 购买 quote_token

        根据恒定乘积公式计算可以获得的 base_token 数量。

        Args:
            quote_amount: 要支付的 quote_token 数量

        Returns:
            可以获得的 base_token 数量
        """
        if quote_amount <= D0:
            return D0

        # 恒定乘积公式：k = reserve_base * reserve_quote
        # 支付后：new_quote = reserve_quote + quote_amount
        #       new_base = k / new_quote
        # 可以获得的 base = reserve_base - new_base
        
        new_reserve_quote = self.amm_reserve_quote + quote_amount
        new_reserve_base = self.amm_k / new_reserve_quote
        base_amount = self.amm_reserve_base - new_reserve_base

        # 更新储备
        self.amm_reserve_base = new_reserve_base
        self.amm_reserve_quote = new_reserve_quote

        # 更新市场价格为 AMM 隐含价格
        self.price = self.get_amm_price()

        return base_amount

    def _execute_amm_market_order(
        self, trader: Trader, direction: str, volume: Decimal
    ) -> Tuple[Decimal, List[Dict], Decimal]:
        """
        使用 AMM 池执行市价单

        当订单簿深度不足时，使用 AMM 池作为最后的做市商。
        通过微积分方法连续根据当前储备调整做市成交价，
        使得市价订单的冻结量被消耗光，而不是满足其预期成交量。

        恒定乘积做市商模型：
        - k = reserve_base * reserve_quote (恒定)
        - 价格 = reserve_quote / reserve_base
        - 买入 base_token：支付 quote_token，reserve_base 减少，reserve_quote 增加
        - 卖出 base_token：获得 quote_token，reserve_base 增加，reserve_quote 减少

        积分定价方法：
        当购买 Δbase 时，支付的 quote 为积分：
        ∫(k / (R_base - x)²)dx from 0 to Δbase
        = k * (1/(R_base - Δbase) - 1/R_base)

        Args:
            trader: 下单交易者
            direction: 'buy' 或 'sell'
            volume: 剩余未成交的成交量

        Returns:
            (实际成交量, 成交明细列表, 总手续费)
        """
        if not self.amm_enabled or volume <= D0:
            return D0, [], D0

        trade_details = []
        total_fee = D0
        executed_volume = D0

        if direction == "buy":
            # 买入 base_token，支付 quote_token
            available_quote = trader.assets.get(self.quote_token, D0)
            
            if available_quote <= D0:
                return D0, [], D0

            # 使用积分公式计算可以购买的 base_token 数量
            # 支付 quote_amount 后，可以获得的 base = R_base - k/(R_quote + quote_amount)
            # 但需要考虑手续费
            
            # 计算最大可购买的 base_token（不考虑手续费）
            # 从积分公式：quote_amount = k * (1/(R_base - Δbase) - 1/R_base)
            # 解得：Δbase = R_base - k/(R_quote + quote_amount)
            
            # 先计算手续费
            fee_rate = self.fee_config.taker_rate if self.fee_config else D0
            available_for_swap = available_quote / (D1 + fee_rate) if fee_rate > D0 else available_quote
            
            # 计算可以购买的 base_token 数量
            max_base = self.amm_reserve_base - self.amm_k / (self.amm_reserve_quote + available_for_swap)
            
            # 限制购买量不超过储备的 95%（防止储备耗尽）
            max_base = min(max_base, self.amm_reserve_base * to_decimal("0.95"))
            
            if max_base <= D0:
                return D0, [], D0

            # 使用积分公式计算实际支付的 quote
            # quote_needed = k * (1/(R_base - Δbase) - 1/R_base)
            new_reserve_base = self.amm_reserve_base - max_base
            new_reserve_quote = self.amm_k / new_reserve_base
            quote_needed = new_reserve_quote - self.amm_reserve_quote
            
            # 计算手续费
            fee = self.fee_calculator.calculate(quote_needed, is_taker=True, is_buyer=True, trader=trader)
            total_cost = quote_needed + fee
            
            # 检查余额是否足够
            if total_cost > available_quote:
                # 重新计算可购买量
                available_for_swap = available_quote - fee
                # 从积分公式反推
                new_reserve_quote = self.amm_reserve_quote + available_for_swap
                new_reserve_base = self.amm_k / new_reserve_quote
                max_base = self.amm_reserve_base - new_reserve_base
                
                if max_base <= D0:
                    return D0, [], D0
                
                quote_needed = available_for_swap
                fee = self.fee_calculator.calculate(quote_needed, is_taker=True, is_buyer=True, trader=trader)
                total_cost = quote_needed + fee
            
            # 执行交易
            trader.assets[self.quote_token] = trader.assets.get(self.quote_token, D0) - total_cost
            trader.assets[self.base_token] = trader.assets.get(self.base_token, D0) + max_base
            
            # 更新 AMM 储备
            self.amm_reserve_base = self.amm_reserve_base - max_base
            self.amm_reserve_quote = self.amm_reserve_quote + quote_needed
            
            # 更新市场价格
            self.price = self.get_amm_price()
            
            # 收取手续费
            self.fee_collector.collect(self.quote_token, fee, {
                "trader": trader.name,
                "direction": "buy",
                "is_taker": True,
                "volume": max_base,
                "price": self.price,
                "source": "AMM"
            })
            
            # 记录成交
            self.log.append((time.time(), self.price, max_base, fee, D0))
            
            trade_details.append({
                "price": self.price,
                "volume": max_base,
                "cost": quote_needed,
                "buyer_fee": fee,
                "seller_fee": D0,
                "counterparty": "AMM",
            })
            
            executed_volume = max_base
            total_fee = fee

        else:  # sell
            # 卖出 base_token，获得 quote_token
            available_base = trader.assets.get(self.base_token, D0)
            
            if available_base <= D0:
                return D0, [], D0

            # 限制卖出量不超过储备的 95%
            max_sell = min(available_base, self.amm_reserve_base * to_decimal("0.95"))
            
            if max_sell <= D0:
                return D0, [], D0

            # 使用积分公式计算可以获得的 quote_token
            # 卖出 Δbase 后：new_base = R_base + Δbase
            #              new_quote = k / new_base
            # 获得的 quote = R_quote - new_quote
            
            new_reserve_base = self.amm_reserve_base + max_sell
            new_reserve_quote = self.amm_k / new_reserve_base
            quote_received = self.amm_reserve_quote - new_reserve_quote
            
            if quote_received <= D0:
                return D0, [], D0

            # 计算手续费
            fee = self.fee_calculator.calculate(quote_received, is_taker=True, is_buyer=False, trader=trader)
            net_revenue = quote_received - fee
            
            # 执行交易
            trader.assets[self.base_token] = available_base - max_sell
            trader.assets[self.quote_token] = trader.assets.get(self.quote_token, D0) + net_revenue
            
            # 更新 AMM 储备
            self.amm_reserve_base = self.amm_reserve_base + max_sell
            self.amm_reserve_quote = self.amm_reserve_quote - quote_received
            
            # 更新市场价格
            self.price = self.get_amm_price()
            
            # 收取手续费
            self.fee_collector.collect(self.quote_token, fee, {
                "trader": trader.name,
                "direction": "sell",
                "is_taker": True,
                "volume": max_sell,
                "price": self.price,
                "source": "AMM"
            })
            
            # 记录成交
            self.log.append((time.time(), self.price, max_sell, fee, D0))
            
            trade_details.append({
                "price": self.price,
                "volume": max_sell,
                "revenue": quote_received,
                "buyer_fee": D0,
                "seller_fee": fee,
                "counterparty": "AMM",
            })
            
            executed_volume = max_sell
            total_fee = fee

        return executed_volume, trade_details, total_fee
