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
import math
import random
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

        # AMM 恒定乘积做市商系统（始终启用，初始 0 资产）
        self.amm_reserve_base = D0  # base_token 储备
        self.amm_reserve_quote = D0  # quote_token 储备
        self.amm_k = D0  # 恒定乘积 k = reserve_base * reserve_quote

        # 共识价格（买卖盘口平均价格）
        self.consensus_price = self.price

    def inject_amm_liquidity(self, base_amount) -> None:
        """
        向 AMM 池注入流动性

        Args:
            base_amount: 注入的 base_token 数量
        """
        base_amount = to_decimal(base_amount)
        if base_amount <= D0:
            raise ValueError("注入量必须大于0")

        quote_amount = base_amount * self.consensus_price

        self.amm_reserve_base += base_amount
        self.amm_reserve_quote += quote_amount
        self.amm_k = self.amm_reserve_base * self.amm_reserve_quote
        
        # 更新市场价格为 AMM 隐含价格
        if self.amm_reserve_base > D0:
            self.price = self.get_amm_price()
            self.update_consensus_price()

    def withdraw_amm_liquidity(self, base_amount) -> Tuple[Decimal, Decimal]:
        """
        从 AMM 池提取流动性

        Args:
            base_amount: 提取的 base_token 数量

        Returns:
            (提取的 base_token 数量, 提取的 quote_token 数量)
        """
        base_amount = to_decimal(base_amount)
        if base_amount <= D0:
            return D0, D0

        if base_amount > self.amm_reserve_base:
            base_amount = self.amm_reserve_base

        quote_amount = base_amount * self.consensus_price
        if quote_amount > self.amm_reserve_quote:
            quote_amount = self.amm_reserve_quote
            base_amount = quote_amount / self.consensus_price if self.consensus_price > D0 else D0

        self.amm_reserve_base -= base_amount
        self.amm_reserve_quote -= quote_amount
        self.amm_k = self.amm_reserve_base * self.amm_reserve_quote
        
        # 更新市场价格为 AMM 隐含价格
        if self.amm_reserve_base > D0:
            self.price = self.get_amm_price()
            self.update_consensus_price()

        return base_amount, quote_amount

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
            AMM 池的隐含价格（quote/base），如果储备为0返回共识价格
        """
        if self.amm_reserve_base == D0:
            return self.consensus_price
        return self.amm_reserve_quote / self.amm_reserve_base

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
        # 双方都不存在时保持当前共识价格

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

                    # 检查买家余额
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

                    # 执行交易
                    trader.assets[self.quote_token] = available - match_cost
                    trader.assets[self.base_token] = (
                        trader.assets.get(self.base_token, D0) + match_volume
                    )

                    seller = sell_order.trader
                    sell_order.remaining_frozen -= match_volume
                    seller.assets[self.quote_token] = (
                        seller.assets.get(self.quote_token, D0) + match_cost
                    )

                    # 记录成交
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

                    volume -= match_volume
                    executed_volume += match_volume
                    total_cost_or_revenue += match_cost
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

                    # 执行交易
                    trader.assets[self.base_token] = available - match_volume
                    trader.assets[self.quote_token] = (
                        trader.assets.get(self.quote_token, D0) + match_revenue
                    )

                    buyer = buy_order.trader
                    buy_order.remaining_frozen -= match_revenue
                    buyer.assets[self.base_token] = (
                        buyer.assets.get(self.base_token, D0) + match_volume
                    )

                    # 记录成交
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

                    volume -= match_volume
                    executed_volume += match_volume
                    total_cost_or_revenue += match_revenue
                    buy_order.executed += match_volume

                    # 完成订单处理
                    if buy_order.remaining_volume <= D0:
                        if buy_order in buyer.orders:
                            buyer.orders.remove(buy_order)
                        self.buy_orders.remove(buy_order)

            # 如果订单簿深度不足，使用 AMM 池完成剩余订单
            if volume > D0 and self.amm_reserve_base > D0 and self.amm_reserve_quote > D0:
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

            total_buyer_cost = match_amount
            seller_revenue = match_amount

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
                    total_buyer_cost = match_amount
                    seller_revenue = match_amount

            # 计算多冻结的资金（实际成交价格 vs 冻结时价格）
            # 买家冻结时按limit_price，实际按match_price
            frozen_price = best_buy.price
            actual_cost = match_amount
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

            # 记录成交
            self.log.append((time.time(), match_price, match_volume, D0, D0))
            self.price = match_price
            self.update_consensus_price()

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

        此方法会自动：
        1. 更新共识价格（买卖盘口平均价格）
        2. 执行 AMM 套利逻辑，使 AMM 池的隐含价格向共识价格收敛。

        Args:
            dt: 时间步长（秒）

        Examples:
            >>> class MyTradingPair(TradingPair):
            ...     def step(self, dt):
            ...         # 每步更新价格
            ...         self.update_price()
        """
        self.update_consensus_price()
        self._amm_arbitrage(dt)

    def _amm_arbitrage(self, dt: Decimal) -> None:
        """
        AMM 套利逻辑

        当共识价格与 AMM 池隐含价格不一致时，通过订单簿进行套利交易
        使池子储备调整，直到隐含价格等于共识价格。

        套利方向：
        - 如果共识价格 > AMM 价格：从订单簿买入 base_token，
          池子 base 增加，quote 减少，AMM 价格下降
        - 如果共识价格 < AMM 价格：向订单簿卖出 base_token，
          池子 base 减少，quote 增加，AMM 价格上升

        注意：AMM 套利只能通过订单簿执行，不允许直接修改池子储备。

        Args:
            dt: 时间步长（秒），用于控制套利速度
        """
        if self.amm_reserve_base <= D0 or self.amm_reserve_quote <= D0:
            return

        amm_price = self.get_amm_price()
        consensus_price = self.consensus_price

        # 计算价格差异
        price_diff = consensus_price - amm_price

        # 如果价格差异很小，不需要套利
        if abs(price_diff) / consensus_price < to_decimal("0.001"):
            return

        # 套利速度参数（可根据需要调整）
        arbitrage_speed = to_decimal("0.01") * dt

        if price_diff > D0:
            # 共识价格 > AMM 价格，需要从订单簿买入 base_token
            # 这会使 AMM 池 base 增加，quote 减少，AMM 价格下降
            target_base = self.amm_reserve_base * arbitrage_speed
            if target_base > D0:
                self._amm_arbitrage_buy_from_orderbook(target_base)
        else:
            # 共识价格 < AMM 价格，需要向订单簿卖出 base_token
            # 这会使 AMM 池 base 减少，quote 增加，AMM 价格上升
            target_base = self.amm_reserve_base * arbitrage_speed
            if target_base > D0:
                self._amm_arbitrage_sell_from_orderbook(target_base)

    def _amm_arbitrage_buy_from_orderbook(self, target_volume: Decimal) -> Decimal:
        """
        AMM 套利：从订单簿买入 base_token

        通过消耗 AMM 池的 quote_token 储备来购买订单簿上的 base_token。
        交易完成后，池子储备自动更新以反映新的资产构成。

        Args:
            target_volume: 目标买入量

        Returns:
            未成交的剩余量
        """
        remaining = target_volume

        while remaining > D0 and self.sell_orders:
            sell_order = self.sell_orders[0]
            match_volume = min(remaining, sell_order.remaining_volume)
            match_price = sell_order.price

            # AMM 池支付 quote_token
            quote_needed = match_volume * match_price
            if quote_needed > self.amm_reserve_quote:
                match_volume = self.amm_reserve_quote / match_price
                if match_volume <= D0:
                    break
                quote_needed = match_volume * match_price

            # 卖方收到 quote_token
            seller = sell_order.trader
            seller.assets[self.quote_token] = seller.assets.get(self.quote_token, D0) + quote_needed

            # 更新卖单
            sell_order.executed += match_volume
            sell_order.remaining_volume -= match_volume
            sell_order.remaining_frozen -= match_volume

            # 更新市场价格
            self.price = match_price
            self.update_consensus_price()

            remaining -= match_volume

            # 移除已完成的订单
            if sell_order.remaining_volume <= D0:
                if sell_order in seller.orders:
                    seller.orders.remove(sell_order)
                self.sell_orders.remove(sell_order)

        # 计算实际成交量
        executed_volume = target_volume - remaining
        
        # AMM 池储备更新：用 quote_token 换取了 base_token
        if executed_volume > D0:
            quote_spent = executed_volume * self.price
            self.amm_reserve_quote = max(D0, self.amm_reserve_quote - quote_spent)
            self.amm_reserve_base += executed_volume
            self.amm_k = self.amm_reserve_base * self.amm_reserve_quote
            # 套利后更新共识价格
            self._update_consensus_price_after_match()

        return remaining

    def _amm_arbitrage_sell_from_orderbook(self, target_volume: Decimal) -> Decimal:
        """
        AMM 套利：向订单簿卖出 base_token

        通过消耗 AMM 池的 base_token 储备来向订单簿出售，换取 quote_token。
        交易完成后，池子储备自动更新以反映新的资产构成。

        Args:
            target_volume: 目标卖出量

        Returns:
            未成交的剩余量
        """
        remaining = target_volume

        while remaining > D0 and self.buy_orders:
            buy_order = self.buy_orders[0]
            match_volume = min(remaining, buy_order.remaining_volume)
            match_price = buy_order.price

            # 检查 AMM 池是否有足够的 base_token
            if match_volume > self.amm_reserve_base:
                match_volume = self.amm_reserve_base
                if match_volume <= D0:
                    break

            quote_received = match_volume * match_price

            # 买方收到 base_token
            buyer = buy_order.trader
            buyer.assets[self.base_token] = buyer.assets.get(self.base_token, D0) + match_volume

            # 更新买单
            buy_order.executed += match_volume
            buy_order.remaining_volume -= match_volume
            buy_order.remaining_frozen -= quote_received

            # 更新市场价格
            self.price = match_price
            self.update_consensus_price()

            remaining -= match_volume

            # 移除已完成的订单
            if buy_order.remaining_volume <= D0:
                if buy_order in buyer.orders:
                    buyer.orders.remove(buy_order)
                self.buy_orders.remove(buy_order)

        # 计算实际成交量
        executed_volume = target_volume - remaining
        
        # AMM 池储备更新：用 base_token 换取了 quote_token
        if executed_volume > D0:
            quote_earned = executed_volume * self.price
            self.amm_reserve_base -= executed_volume
            self.amm_reserve_quote = max(D0, self.amm_reserve_quote + quote_earned)
            self.amm_k = self.amm_reserve_base * self.amm_reserve_quote
            # 套利后更新共识价格
            self._update_consensus_price_after_match()

        return remaining

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
        if volume <= D0:
            return D0, [], D0

        trade_details = []
        total_fee = D0
        executed_volume = D0

        if direction == "buy":
            # 买入 base_token，支付 quote_token
            available_quote = trader.assets.get(self.quote_token, D0)
            
            if available_quote <= D0:
                return D0, [], D0

            # 计算可以购买的 base_token 数量
            max_base = self.amm_reserve_base - self.amm_k / (self.amm_reserve_quote + available_quote)
            
            # 限制购买量不超过储备的 95%（防止储备耗尽）
            max_base = min(max_base, self.amm_reserve_base * to_decimal("0.95"))
            
            if max_base <= D0:
                return D0, [], D0

            # 使用积分公式计算实际支付的 quote
            new_reserve_base = self.amm_reserve_base - max_base
            new_reserve_quote = self.amm_k / new_reserve_base
            quote_needed = new_reserve_quote - self.amm_reserve_quote
            
            # 执行交易
            trader.assets[self.quote_token] = trader.assets.get(self.quote_token, D0) - quote_needed
            trader.assets[self.base_token] = trader.assets.get(self.base_token, D0) + max_base
            
            # 更新 AMM 储备
            self.amm_reserve_base = self.amm_reserve_base - max_base
            self.amm_reserve_quote = self.amm_reserve_quote + quote_needed
            
            # 更新市场价格
            self.price = self.get_amm_price()
            self.update_consensus_price()
            
            # 记录成交
            self.log.append((time.time(), self.price, max_base, D0, D0))
            
            trade_details.append({
                "price": self.price,
                "volume": max_base,
                "cost": quote_needed,
                "buyer_fee": D0,
                "seller_fee": D0,
                "counterparty": "AMM",
            })
            
            executed_volume = max_base

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
            new_reserve_base = self.amm_reserve_base + max_sell
            new_reserve_quote = self.amm_k / new_reserve_base
            quote_received = self.amm_reserve_quote - new_reserve_quote
            
            if quote_received <= D0:
                return D0, [], D0

            # 执行交易
            trader.assets[self.base_token] = available_base - max_sell
            trader.assets[self.quote_token] = trader.assets.get(self.quote_token, D0) + quote_received
            
            # 更新 AMM 储备
            self.amm_reserve_base = self.amm_reserve_base + max_sell
            self.amm_reserve_quote = self.amm_reserve_quote - quote_received
            
            # 更新市场价格
            self.price = self.get_amm_price()
            self.update_consensus_price()
            
            # 记录成交
            self.log.append((time.time(), self.price, max_sell, D0, D0))
            
            trade_details.append({
                "price": self.price,
                "volume": max_sell,
                "revenue": quote_received,
                "buyer_fee": D0,
                "seller_fee": D0,
                "counterparty": "AMM",
            })
            
            executed_volume = max_sell

        return executed_volume, trade_details, total_fee
