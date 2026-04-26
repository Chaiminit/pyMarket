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
        super().__init__(f"{base_token.token_id}/{quote_token.token_id}")
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

        # AMM 手续费比例设置
        self.amm_min_fee_rate = to_decimal("0.00001")  # 最小手续费比例 0.001%
        self.amm_max_fee_rate = to_decimal("0.1")   # 最大手续费比例 10%
        self.amm_current_fee_rate = self.amm_min_fee_rate  # 当前手续费比例

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
                        f"余额不足：需要 {frozen_amount} {self.quote_token.token_id}，"
                        f"可用 {trader.assets.get(self.quote_token, D0)}"
                    )
                trader.assets[self.quote_token] = trader.assets.get(self.quote_token, D0) - frozen_amount
            else:
                # 卖单：冻结基础代币
                if trader.assets.get(self.base_token, D0) < frozen_amount:
                    raise ValueError(
                        f"余额不足：需要 {frozen_amount} {self.base_token.token_id}，"
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

        滑点成本处理：
        市价单执行完成后，AMM会执行套利跟踪共识价格。滑点成本由市价单发起方
        和所有参与交易的对手方按成交量比例分担，以手续费形式支付给AMM。

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
            # 记录所有参与交易的对手方及其成交量（用于后续滑点成本分摊）
            counterparties: List[Tuple[Trader, Decimal]] = []

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

                    # 记录对手方及其成交量
                    counterparties.append((seller, match_volume))

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

                    # 记录对手方及其成交量
                    counterparties.append((buyer, match_volume))

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

            # 市价单执行完成后，根据最新共识价格执行AMM套利
            arbitrage_result = self._arbitrage_after_match()

            # 计算并收取滑点成本补偿费（由市价单发起方和所有对手方按成交量比例分担）
            if executed_volume > D0 and arbitrage_result.get("direction") != "none":
                self._charge_slippage_compensation_market_order(
                    trader, counterparties, executed_volume, direction, arbitrage_result
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

        滑点成本处理：
        每笔撮合后，AMM会执行套利跟踪共识价格。由于AMM作为最后流动性提供者，
        其套利成交价格与前共识价格存在滑点。该滑点成本由买卖双方按最新共识价格
        比例支付手续费给AMM作为补偿。
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

            # 撮合后立即执行AMM套利，使储备比例等于新的共识价格
            arbitrage_result = self._arbitrage_after_match()

            # 计算并收取滑点成本手续费
            self._charge_slippage_compensation(
                buyer, seller, arbitrage_result, match_volume, match_price
            )

            # 完成订单处理
            # 注意：AMM套利可能已经将订单从订单簿中移除，需要检查
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

    def _charge_slippage_compensation(
        self,
        buyer: Trader,
        seller: Trader,
        arbitrage_result: Dict[str, Decimal],
        match_volume: Decimal,
        match_price: Decimal,
    ) -> None:
        """
        收取滑点成本补偿费给AMM

        当AMM为了跟踪共识价格进行套利时，由于作为最后流动性提供者，
        其成交价格与前共识价格存在滑点。该滑点成本由买卖双方按最新
        共识价格比例支付手续费给AMM作为补偿。

        手续费比例限制：
        - 手续费比例必须在 amm_min_fee_rate 和 amm_max_fee_rate 之间
        - 实时收费换算为手续费比例并保存到 amm_current_fee_rate

        Args:
            buyer: 买方交易者
            seller: 卖方交易者
            arbitrage_result: AMM套利结果字典
            match_volume: 撮合成交量
            match_price: 撮合成交价格
        """
        # 检查是否有套利交易
        if arbitrage_result.get("direction") == "none":
            return

        volume = arbitrage_result.get("volume", D0)
        avg_price = arbitrage_result.get("avg_price", D0)
        pre_consensus_price = arbitrage_result.get("pre_consensus_price", D0)

        if volume <= D0 or avg_price <= D0 or pre_consensus_price <= D0:
            return

        # 计算成交额
        trade_value = match_volume * match_price
        if trade_value <= D0:
            return

        # 计算原始滑点成本
        direction = arbitrage_result["direction"]
        if direction == "buy":
            slippage_cost = (avg_price - pre_consensus_price) * volume
        else:  # sell
            slippage_cost = (pre_consensus_price - avg_price) * volume

        if slippage_cost <= D0:
            return

        # 计算原始手续费比例
        raw_fee_rate = slippage_cost / trade_value

        # 限制手续费比例在最小和最大之间
        fee_rate = max(self.amm_min_fee_rate, min(self.amm_max_fee_rate, raw_fee_rate))

        # 保存当前手续费比例
        self.amm_current_fee_rate = fee_rate

        # 根据限制后的比例计算实际手续费
        total_fee_quote = trade_value * fee_rate

        # 获取最新共识价格用于计算资产比例
        current_consensus_price = self.consensus_price
        if current_consensus_price <= D0:
            return

        # 按共识价格换算成base数量
        total_fee_base = total_fee_quote / current_consensus_price

        # 按比例分摊给买卖双方（各承担一半）
        buyer_fee_base = total_fee_base / to_decimal("2")
        seller_fee_quote = total_fee_quote / to_decimal("2")

        # 从买家资产中扣除base手续费
        buyer_current_base = buyer.assets.get(self.base_token, D0)
        if buyer_current_base >= buyer_fee_base:
            buyer.assets[self.base_token] = buyer_current_base - buyer_fee_base
        else:
            buyer_fee_base = buyer_current_base
            buyer.assets[self.base_token] = D0

        # 从卖家资产中扣除quote手续费
        seller_current_quote = seller.assets.get(self.quote_token, D0)
        if seller_current_quote >= seller_fee_quote:
            seller.assets[self.quote_token] = seller_current_quote - seller_fee_quote
        else:
            seller_fee_quote = seller_current_quote
            seller.assets[self.quote_token] = D0

        # 将手续费加到AMM储备中（纯利润）
        if buyer_fee_base > D0:
            self.amm_reserve_base += buyer_fee_base
        if seller_fee_quote > D0:
            self.amm_reserve_quote += seller_fee_quote

        # 更新k值（因为储备增加了）
        self.amm_k = self.amm_reserve_base * self.amm_reserve_quote

    def _charge_slippage_compensation_market_order(
        self,
        taker: Trader,
        counterparties: List[Tuple[Trader, Decimal]],
        total_volume: Decimal,
        direction: str,
        arbitrage_result: Dict[str, Decimal],
    ) -> None:
        """
        收取市价单滑点成本补偿费给AMM

        市价单场景下，滑点成本由市价单发起方（Taker）和所有参与交易的对手方（Makers）
        按各自的成交量比例分担。

        手续费比例限制：
        - 手续费比例必须在 amm_min_fee_rate 和 amm_max_fee_rate 之间
        - 实时收费换算为手续费比例并保存到 amm_current_fee_rate

        Args:
            taker: 市价单发起方
            counterparties: 对手方列表 [(trader, volume), ...]
            total_volume: 总成交量
            direction: 'buy' 或 'sell'（市价单方向）
            arbitrage_result: AMM套利结果字典
        """
        volume = arbitrage_result.get("volume", D0)
        avg_price = arbitrage_result.get("avg_price", D0)
        pre_consensus_price = arbitrage_result.get("pre_consensus_price", D0)

        if volume <= D0 or avg_price <= D0 or pre_consensus_price <= D0:
            return

        # 计算成交额
        trade_value = total_volume * avg_price
        if trade_value <= D0:
            return

        # 计算原始滑点成本
        arb_direction = arbitrage_result["direction"]
        if arb_direction == "buy":
            slippage_cost = (avg_price - pre_consensus_price) * volume
        else:  # sell
            slippage_cost = (pre_consensus_price - avg_price) * volume

        if slippage_cost <= D0:
            return

        # 计算原始手续费比例
        raw_fee_rate = slippage_cost / trade_value

        # 限制手续费比例在最小和最大之间
        fee_rate = max(self.amm_min_fee_rate, min(self.amm_max_fee_rate, raw_fee_rate))

        # 保存当前手续费比例
        self.amm_current_fee_rate = fee_rate

        # 获取最新共识价格用于计算资产比例
        current_consensus_price = self.consensus_price
        if current_consensus_price <= D0:
            return

        # 根据限制后的比例计算实际手续费
        total_fee_quote = trade_value * fee_rate
        total_fee_base = total_fee_quote / current_consensus_price

        # 计算每个参与者应该承担的份额
        # 市价单发起方承担与其成交量成比例的份额
        taker_volume = total_volume  # Taker的成交量就是总成交量
        total_participants_volume = taker_volume + sum(v for _, v in counterparties)

        if total_participants_volume <= D0:
            return

        # Taker承担的手续费
        taker_share = taker_volume / total_participants_volume
        taker_fee_base = total_fee_base * taker_share
        taker_fee_quote = total_fee_quote * taker_share

        # 从Taker资产中扣除
        if direction == "buy":
            # 买入市价单：Taker收到base，扣除base手续费
            taker_current_base = taker.assets.get(self.base_token, D0)
            if taker_current_base >= taker_fee_base:
                taker.assets[self.base_token] = taker_current_base - taker_fee_base
            else:
                taker_fee_base = taker_current_base
                taker.assets[self.base_token] = D0
        else:
            # 卖出市价单：Taker收到quote，扣除quote手续费
            taker_current_quote = taker.assets.get(self.quote_token, D0)
            if taker_current_quote >= taker_fee_quote:
                taker.assets[self.quote_token] = taker_current_quote - taker_fee_quote
            else:
                taker_fee_quote = taker_current_quote
                taker.assets[self.quote_token] = D0

        # 将Taker的手续费加到AMM储备
        if taker_fee_base > D0:
            self.amm_reserve_base += taker_fee_base
        if taker_fee_quote > D0:
            self.amm_reserve_quote += taker_fee_quote

        # 对手方承担的手续费
        for counterparty, cp_volume in counterparties:
            cp_share = cp_volume / total_participants_volume
            cp_fee_base = total_fee_base * cp_share
            cp_fee_quote = total_fee_quote * cp_share

            if direction == "buy":
                # 买入市价单：对手方（卖家）收到quote，扣除quote手续费
                cp_current_quote = counterparty.assets.get(self.quote_token, D0)
                if cp_current_quote >= cp_fee_quote:
                    counterparty.assets[self.quote_token] = cp_current_quote - cp_fee_quote
                else:
                    cp_fee_quote = cp_current_quote
                    counterparty.assets[self.quote_token] = D0
            else:
                # 卖出市价单：对手方（买家）收到base，扣除base手续费
                cp_current_base = counterparty.assets.get(self.base_token, D0)
                if cp_current_base >= cp_fee_base:
                    counterparty.assets[self.base_token] = cp_current_base - cp_fee_base
                else:
                    cp_fee_base = cp_current_base
                    counterparty.assets[self.base_token] = D0

            # 将对手方的手续费加到AMM储备
            if cp_fee_base > D0:
                self.amm_reserve_base += cp_fee_base
            if cp_fee_quote > D0:
                self.amm_reserve_quote += cp_fee_quote

        # 更新k值
        self.amm_k = self.amm_reserve_base * self.amm_reserve_quote

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

        注意：AMM套利现在在每个撮合完成后立即执行，
        不再在step中统一执行。

        Args:
            dt: 时间步长（秒）

        Examples:
            >>> class MyTradingPair(TradingPair):
            ...     def step(self, dt):
            ...         # 每步更新价格
            ...         self.update_price()
        """

    def _arbitrage_after_match(self) -> Dict[str, Decimal]:
        """
        撮合后执行AMM套利

        在每次订单撮合完成后，根据最新的共识价格
        立即执行AMM套利，使储备比例等于共识价格。

        Returns:
            包含套利成交信息的字典:
            - direction: 'buy' 或 'sell' 或 'none'
            - volume: 实际成交数量
            - avg_price: 加权平均成交价格
            - pre_consensus_price: 套利前的共识价格
        """
        # 使用dt=1.0表示一次性完成套利（不限制速度）
        return self._amm_arbitrage(to_decimal("1.0"))

    def _calculate_exact_arbitrage_volume(self) -> Tuple[Decimal, Decimal]:
        """
        计算使得交易后共识价格等于储备比例的精确交易量

        数学原理：
        设当前储备为 R_b, R_q，恒定乘积 k = R_b * R_q
        当前AMM价格：P_amm = R_q / R_b = k / R_b^2
        当前共识价格：P_c = (P_buy + P_sell) / 2

        要使 P_amm' = P_c，需要调整 R_b 使得 k / R_b'^2 = P_c
        解得目标储备：R_b' = sqrt(k / P_c)

        关键洞察：
        - 由 P = k / R_b^2 可知，R_b 越小，P 越大（价格与base储备成反比）
        - 共识价格 > AMM价格：需要提高AMM价格 → 减少base储备 → 卖出base
        - 共识价格 < AMM价格：需要降低AMM价格 → 增加base储备 → 买入base

        交易方向：
        - P_c > P_amm：卖出 Δ = R_b - R_b' = R_b - sqrt(k / P_c)
        - P_c < P_amm：买入 Δ = R_b' - R_b = sqrt(k / P_c) - R_b

        Returns:
            (交易量Δ, 交易价格) 如果无法套利则返回 (0, 0)
        """
        if self.amm_k <= D0:
            return D0, D0

        amm_price = self.get_amm_price()
        consensus_price = self.consensus_price

        # 检查价格差异
        if consensus_price <= D0 or amm_price <= D0:
            return D0, D0

        price_diff = consensus_price - amm_price
        tolerance = consensus_price * to_decimal("0.0001")  # 0.01%容差

        if abs(price_diff) <= tolerance:
            return D0, D0

        # 计算目标储备量（保持k不变）
        # 由 P = k / R_b^2 得 R_b = sqrt(k / P)
        target_base = (self.amm_k / consensus_price).sqrt()

        if price_diff > D0:
            # 共识价格 > AMM价格，需要卖出base（减少储备来提高价格）
            # 检查买单簿
            if not self.buy_orders:
                return D0, D0
            trade_price = self.buy_orders[0].price
            delta = self.amm_reserve_base - target_base
            if delta <= D0:
                return D0, D0
            return delta, trade_price
        else:
            # 共识价格 < AMM价格，需要买入base（增加储备来降低价格）
            # 检查卖单簿
            if not self.sell_orders:
                return D0, D0
            trade_price = self.sell_orders[0].price
            delta = target_base - self.amm_reserve_base
            if delta <= D0:
                return D0, D0
            return delta, trade_price

    def _amm_arbitrage(self, dt: Decimal) -> Dict[str, Decimal]:
        """
        AMM 套利逻辑 - 精确版本

        当共识价格与 AMM 池隐含价格不一致时，通过订单簿进行套利交易
        使池子储备调整，直到隐含价格等于共识价格。

        套利方向（修正后）：
        - 如果共识价格 > AMM 价格：AMM卖出 base_token（减少储备），
          使AMM价格上升向共识价格收敛
        - 如果共识价格 < AMM 价格：AMM买入 base_token（增加储备），
          使AMM价格下降向共识价格收敛

        注意：AMM 套利只能通过订单簿执行，不允许直接修改池子储备。

        Args:
            dt: 时间步长（秒），用于控制套利速度

        Returns:
            包含套利成交信息的字典:
            - direction: 'buy' 或 'sell' 或 'none'
            - volume: 实际成交数量
            - avg_price: 加权平均成交价格
            - pre_consensus_price: 套利前的共识价格
        """
        # 记录套利前的共识价格
        pre_consensus_price = self.consensus_price

        if self.amm_reserve_base <= D0 or self.amm_reserve_quote <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 计算精确套利交易量
        exact_volume, trade_price = self._calculate_exact_arbitrage_volume()

        if exact_volume <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 根据价格差异方向选择套利操作
        amm_price = self.get_amm_price()
        consensus_price = self.consensus_price
        price_diff = consensus_price - amm_price

        # 不限制交易速度，一次性完成精确套利
        target_volume = exact_volume

        if price_diff > D0:
            # 共识价格 > AMM价格，AMM向订单簿卖出base
            return self._amm_arbitrage_sell_from_orderbook_exact(
                target_volume, trade_price, pre_consensus_price
            )
        else:
            # 共识价格 < AMM价格，AMM从订单簿买入base
            return self._amm_arbitrage_buy_from_orderbook_exact(
                target_volume, trade_price, pre_consensus_price
            )

    def _amm_arbitrage_buy_from_orderbook_exact(
        self, target_volume: Decimal, expected_price: Decimal, pre_consensus_price: Decimal
    ) -> Dict[str, Decimal]:
        """
        AMM 精确套利：从订单簿买入 base_token，使得储备比例等于共识价格

        核心原理：按照恒定乘积公式更新储备，而不是简单按成交价计算。
        当AMM买入Δbase时：
        - 新base储备：R_b' = R_b + Δ
        - 新quote储备：R_q' = k / R_b'（保持k不变）
        - 实际支付的quote：R_q - R_q'

        Args:
            target_volume: 目标买入量（已经过精确计算）
            expected_price: 预期成交价格（当前卖一价）
            pre_consensus_price: 套利前的共识价格（用于计算滑点成本）

        Returns:
            包含套利成交信息的字典
        """
        if target_volume <= D0 or not self.sell_orders:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 按照恒定乘积计算新的储备
        # R_b' = R_b + Δ
        new_reserve_base = self.amm_reserve_base + target_volume

        # 检查是否超出储备限制（最多使用95%的quote储备）
        max_quote_to_spend = self.amm_reserve_quote * to_decimal("0.95")

        # 计算实际需要的quote（保持k不变）
        new_reserve_quote = self.amm_k / new_reserve_base
        quote_needed = self.amm_reserve_quote - new_reserve_quote

        if quote_needed > max_quote_to_spend:
            # 需要限制交易量
            # 设最多使用 max_quote，则 R_q' = R_q - max_quote
            # R_b' = k / R_q'
            # Δ = R_b' - R_b
            new_reserve_quote_limited = self.amm_reserve_quote - max_quote_to_spend
            new_reserve_base_limited = self.amm_k / new_reserve_quote_limited
            target_volume = new_reserve_base_limited - self.amm_reserve_base
            if target_volume <= D0:
                return {
                    "direction": "none",
                    "volume": D0,
                    "avg_price": D0,
                    "pre_consensus_price": pre_consensus_price,
                }
            new_reserve_base = new_reserve_base_limited
            quote_needed = max_quote_to_spend

        # 现在开始与订单簿交易，使用实际成交价格
        remaining_to_buy = target_volume
        total_quote_paid = D0
        actual_base_bought = D0

        while remaining_to_buy > D0 and self.sell_orders:
            sell_order = self.sell_orders[0]
            match_volume = min(remaining_to_buy, sell_order.remaining_volume)
            match_price = sell_order.price

            # AMM池支付quote，卖方收到quote
            quote_paid = match_volume * match_price

            # 检查AMM池是否有足够的quote
            available_quote = self.amm_reserve_quote - total_quote_paid
            if quote_paid > available_quote:
                match_volume = available_quote / match_price
                if match_volume <= D0:
                    break
                quote_paid = match_volume * match_price

            # 卖方收到quote
            seller = sell_order.trader
            seller.assets[self.quote_token] = seller.assets.get(self.quote_token, D0) + quote_paid

            # 更新卖单（注意：remaining_volume是计算属性，只能通过修改executed来改变）
            sell_order.executed += match_volume
            sell_order.remaining_frozen -= match_volume

            # 累计交易
            total_quote_paid += quote_paid
            actual_base_bought += match_volume
            remaining_to_buy -= match_volume

            # 移除已完成的订单
            if sell_order.remaining_volume <= D0:
                if sell_order in seller.orders:
                    seller.orders.remove(sell_order)
                self.sell_orders.remove(sell_order)

        # 更新AMM储备（使用恒定乘积公式）
        if actual_base_bought > D0:
            # 新的base储备
            self.amm_reserve_base += actual_base_bought
            # 新的quote储备（保持k不变）
            self.amm_reserve_quote = self.amm_k / self.amm_reserve_base

            # 更新市场价格和共识价格
            self.price = self.get_amm_price()
            self.update_consensus_price()

            # 计算加权平均成交价格
            avg_price = total_quote_paid / actual_base_bought if actual_base_bought > D0 else D0

            return {
                "direction": "buy",
                "volume": actual_base_bought,
                "avg_price": avg_price,
                "pre_consensus_price": pre_consensus_price,
            }

        return {
            "direction": "none",
            "volume": D0,
            "avg_price": D0,
            "pre_consensus_price": pre_consensus_price,
        }

    def _amm_arbitrage_sell_from_orderbook_exact(
        self, target_volume: Decimal, expected_price: Decimal, pre_consensus_price: Decimal
    ) -> Dict[str, Decimal]:
        """
        AMM 精确套利：向订单簿卖出 base_token，使得储备比例等于共识价格

        核心原理：按照恒定乘积公式更新储备。
        当AMM卖出Δbase时：
        - 新base储备：R_b' = R_b - Δ
        - 新quote储备：R_q' = k / R_b'（保持k不变）
        - 实际获得的quote：R_q' - R_q

        Args:
            target_volume: 目标卖出量（已经过精确计算）
            expected_price: 预期成交价格（当前买一价）
            pre_consensus_price: 套利前的共识价格（用于计算滑点成本）

        Returns:
            包含套利成交信息的字典
        """
        if target_volume <= D0 or not self.buy_orders:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 按照恒定乘积计算新的储备
        # R_b' = R_b - Δ
        new_reserve_base = self.amm_reserve_base - target_volume

        if new_reserve_base <= D0:
            # 不能卖出全部，保留最小储备
            new_reserve_base = self.amm_reserve_base * to_decimal("0.05")
            target_volume = self.amm_reserve_base - new_reserve_base

        # 检查是否超出储备限制（最多卖出95%的base储备）
        max_base_to_sell = self.amm_reserve_base * to_decimal("0.95")
        if target_volume > max_base_to_sell:
            target_volume = max_base_to_sell
            new_reserve_base = self.amm_reserve_base - target_volume

        # 计算实际能获得的quote（保持k不变）
        new_reserve_quote = self.amm_k / new_reserve_base
        quote_to_receive = new_reserve_quote - self.amm_reserve_quote

        if quote_to_receive <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 现在开始与订单簿交易
        remaining_to_sell = target_volume
        total_quote_received = D0
        actual_base_sold = D0

        while remaining_to_sell > D0 and self.buy_orders:
            buy_order = self.buy_orders[0]
            match_volume = min(remaining_to_sell, buy_order.remaining_volume)
            match_price = buy_order.price

            quote_received = match_volume * match_price

            # 买方收到base
            buyer = buy_order.trader
            buyer.assets[self.base_token] = buyer.assets.get(self.base_token, D0) + match_volume

            # 更新买单（注意：remaining_volume是计算属性，只能通过修改executed来改变）
            buy_order.executed += match_volume
            buy_order.remaining_frozen -= quote_received

            # 累计交易
            total_quote_received += quote_received
            actual_base_sold += match_volume
            remaining_to_sell -= match_volume

            # 移除已完成的订单
            if buy_order.remaining_volume <= D0:
                if buy_order in buyer.orders:
                    buyer.orders.remove(buy_order)
                self.buy_orders.remove(buy_order)

        # 更新AMM储备（使用恒定乘积公式）
        if actual_base_sold > D0:
            # 新的base储备
            self.amm_reserve_base -= actual_base_sold
            # 新的quote储备（保持k不变）
            self.amm_reserve_quote = self.amm_k / self.amm_reserve_base

            # 更新市场价格和共识价格
            self.price = self.get_amm_price()
            self.update_consensus_price()

            # 计算加权平均成交价格
            avg_price = total_quote_received / actual_base_sold if actual_base_sold > D0 else D0

            return {
                "direction": "sell",
                "volume": actual_base_sold,
                "avg_price": avg_price,
                "pre_consensus_price": pre_consensus_price,
            }

        return {
            "direction": "none",
            "volume": D0,
            "avg_price": D0,
            "pre_consensus_price": pre_consensus_price,
        }

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
            self.update_consensus_price()

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
            self.update_consensus_price()

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
