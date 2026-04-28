"""
RMM 模块 - 反射性做市商 (Reflexive Market Maker)

基于恒定乘积模型的做市商系统，通过套利机制使池子价格
反射性跟踪订单簿共识价格。

核心特性：
- 恒定乘积做市：k = reserve_base * reserve_quote
- 反射性套利：每次撮合后自动调整储备比例跟踪共识价格
- 手续费比例限制：min_fee_rate <= fee_rate <= max_fee_rate
- 积分定价：使用微积分方法精确计算大额交易的价格影响
"""

import time
from typing import List, Dict, Tuple, Optional
from decimal import Decimal

from .engine_node import EngineNode
from .utils import to_decimal, D0, D1


class _PoolData:
    """单个交易对的 AMM 池数据"""

    __slots__ = ("reserve_base", "reserve_quote", "k", "current_fee_rate")

    def __init__(self, initial_price: Decimal):
        self.reserve_base: Decimal = D0
        self.reserve_quote: Decimal = D0
        self.k: Decimal = D0
        self.current_fee_rate: Decimal = D0


class ReflexiveMarketMaker(EngineNode):
    """
    反射性做市商 (Reflexive Market Maker, RMM)

    基于恒定乘积公式 k = R_base * R_quote 的做市商系统。
    通过套利机制使每个池子的隐含价格反射性跟踪对应交易对的共识价格。

    所有注册在引擎上的交易对共用同一个 RMM 实例，
    每个交易对拥有独立的池子数据（储备、k值、手续费比例）。

    Attributes:
        min_fee_rate: 最小手续费比例
        max_fee_rate: 最大手续费比例

    Examples:
        >>> rmm = ReflexiveMarketMaker()
        >>> rmm.register_pair(pair)
        >>> rmm.get_price(pair)
    """

    def __init__(
        self,
        min_fee_rate="0.0001",
        max_fee_rate="0.01",
    ):
        """
        创建反射性做市商

        Args:
            min_fee_rate: 最小手续费比例（默认 0.001%）
            max_fee_rate: 最大手续费比例（默认 0.1%）
        """
        super().__init__("RMM")
        self.min_fee_rate = to_decimal(min_fee_rate)
        self.max_fee_rate = to_decimal(max_fee_rate)
        self._pools: Dict[int, _PoolData] = {}

    def _get_pool(self, pair) -> _PoolData:
        pair_id = id(pair)
        if pair_id not in self._pools:
            self._pools[pair_id] = _PoolData(pair.consensus_price)
        return self._pools[pair_id]

    def register_pair(self, pair) -> None:
        self._get_pool(pair)

    def has_liquidity(self, pair) -> bool:
        pool = self._get_pool(pair)
        return pool.reserve_base > D0 and pool.reserve_quote > D0

    def get_reserves(self, pair) -> Tuple[Decimal, Decimal]:
        pool = self._get_pool(pair)
        return pool.reserve_base, pool.reserve_quote

    def get_price(self, pair) -> Decimal:
        pool = self._get_pool(pair)
        if pool.reserve_base == D0:
            return pair.consensus_price
        return pool.reserve_quote / pool.reserve_base

    def get_current_fee_rate(self, pair) -> Decimal:
        pool = self._get_pool(pair)
        # 当池子没有流动性时，返回最小手续费率作为冷启动费率
        if pool.reserve_base <= D0 or pool.reserve_quote <= D0:
            return self.min_fee_rate
        return pool.current_fee_rate

    def arbitrage_after_match(self, pair) -> Dict[str, Decimal]:
        """
        撮合后执行AMM套利

        在每次订单撮合完成后，根据最新的共识价格
        立即执行AMM套利，使储备比例等于共识价格。

        Returns:
            包含套利成交信息的字典
        """
        return self._arbitrage(pair, to_decimal("1.0"))

    def _calculate_exact_arbitrage_volume(self, pair) -> Tuple[Decimal, Decimal]:
        """
        计算使得交易后共识价格等于储备比例的精确交易量

        数学原理：
        设当前储备为 R_b, R_q，恒定乘积 k = R_b * R_q
        当前AMM价格：P_amm = R_q / R_b = k / R_b^2
        当前共识价格：P_c = (P_buy + P_sell) / 2

        要使 P_amm' = P_c，需要调整 R_b 使得 k / R_b'^2 = P_c
        解得目标储备：R_b' = sqrt(k / P_c)

        Returns:
            (交易量Δ, 交易价格) 如果无法套利则返回 (0, 0)
        """
        pool = self._get_pool(pair)

        if pool.k <= D0:
            return D0, D0

        amm_price = self.get_price(pair)
        consensus_price = pair.consensus_price

        if consensus_price <= D0 or amm_price <= D0:
            return D0, D0

        price_diff = consensus_price - amm_price
        tolerance = consensus_price * to_decimal("0.0001")

        if abs(price_diff) <= tolerance:
            return D0, D0

        target_base = (pool.k / consensus_price).sqrt()

        if price_diff > D0:
            if not pair.buy_orders:
                return D0, D0
            trade_price = pair.buy_orders[0].price
            delta = pool.reserve_base - target_base
            if delta <= D0:
                return D0, D0
            return delta, trade_price
        else:
            if not pair.sell_orders:
                return D0, D0
            trade_price = pair.sell_orders[0].price
            delta = target_base - pool.reserve_base
            if delta <= D0:
                return D0, D0
            return delta, trade_price

    def _arbitrage(self, pair, dt: Decimal) -> Dict[str, Decimal]:
        """
        AMM 套利逻辑 - 精确版本

        当共识价格与 AMM 池隐含价格不一致时，通过订单簿进行套利交易
        使池子储备调整，直到隐含价格等于共识价格。

        套利方向：
        - P_amm > P_c: base贵了 → 买入base → _arbitrage_buy_from_orderbook_exact
        - P_amm < P_c: base便宜了 → 卖出base → _arbitrage_sell_from_orderbook_exact

        Args:
            pair: 交易对
            dt: 时间步长（秒），用于控制套利速度

        Returns:
            包含套利成交信息的字典
        """
        pool = self._get_pool(pair)
        pre_consensus_price = pair.consensus_price

        if pool.reserve_base <= D0 or pool.reserve_quote <= D0 or pool.k <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        amm_price = self.get_price(pair)
        consensus_price = pair.consensus_price

        if consensus_price <= D0 or amm_price <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        price_diff = amm_price - consensus_price
        tolerance = consensus_price * to_decimal("0.0001")

        if abs(price_diff) <= tolerance:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # P_amm > P_c: base贵了，需要买入base让价格下降
        if price_diff > D0:
            if not pair.sell_orders:
                return {
                    "direction": "none",
                    "volume": D0,
                    "avg_price": D0,
                    "pre_consensus_price": pre_consensus_price,
                }
            return self._arbitrage_buy_from_orderbook_exact(pair, pre_consensus_price)
        # P_amm < P_c: base便宜了，需要卖出base让价格上升
        else:
            if not pair.buy_orders:
                return {
                    "direction": "none",
                    "volume": D0,
                    "avg_price": D0,
                    "pre_consensus_price": pre_consensus_price,
                }
            return self._arbitrage_sell_from_orderbook_exact(pair, pre_consensus_price)

    def _simulate_buy_arbitrage(self, pair, buy_volume: Decimal, pool, best_buy_price: Decimal):
        """
        模拟买入套利交易后的状态

        Returns:
            (quote_needed, new_pool_price, new_consensus_price, actual_volume)
            如果池子储备不足，new_pool_price 返回 D0
        """
        if buy_volume <= D0 or not pair.sell_orders:
            return D0, D0, D0, D0

        max_quote = pool.reserve_quote * to_decimal("0.95")

        remaining = buy_volume
        total_quote = D0
        actual_volume = D0
        last_price = D0

        for order in pair.sell_orders:
            if remaining <= D0:
                break
            match = min(remaining, order.remaining_volume)
            if match <= D0:
                continue
            quote = match * order.price
            if total_quote + quote > max_quote:
                remaining_quote = max_quote - total_quote
                if order.price > D0 and remaining_quote > D0:
                    match = remaining_quote / order.price
                    quote = match * order.price
                else:
                    break
            total_quote += quote
            actual_volume += match
            remaining -= match
            last_price = order.price

        if actual_volume <= D0:
            return D0, D0, D0, D0

        new_reserve_base = pool.reserve_base + actual_volume
        new_reserve_quote = pool.reserve_quote - total_quote

        if new_reserve_base <= D0 or new_reserve_quote <= D0:
            return total_quote, D0, D0, actual_volume

        new_pool_price = new_reserve_quote / new_reserve_base

        remaining_after = buy_volume
        new_best_sell = D0
        for order in pair.sell_orders:
            if remaining_after <= D0:
                new_best_sell = order.price
                break
            order_remaining = order.remaining_volume
            if remaining_after < order_remaining:
                new_best_sell = order.price
                break
            remaining_after -= order_remaining

        if new_best_sell <= D0:
            new_best_sell = last_price if last_price > D0 else (
                pair.sell_orders[-1].price if pair.sell_orders else D0
            )

        if best_buy_price > D0 and new_best_sell > D0:
            new_consensus = (best_buy_price + new_best_sell) / to_decimal("2")
        elif new_best_sell > D0:
            new_consensus = new_best_sell
        elif best_buy_price > D0:
            new_consensus = best_buy_price
        else:
            new_consensus = D0

        return total_quote, new_pool_price, new_consensus, actual_volume

    def _arbitrage_buy_from_orderbook_exact(
        self, pair, pre_consensus_price: Decimal
    ) -> Dict[str, Decimal]:
        """
        AMM 精确套利：从订单簿买入 base_token，使池子价格等于市场共识价格

        触发条件：P_amm > P_c（池子价格高于市场共识价格，base贵了）
        操作效果：买入 base → R_b↑, R_q↓ → P_amm↓

        使用二分法精确求解交易量，确保 P_amm' = P_c'
        """
        pool = self._get_pool(pair)

        if not pair.sell_orders:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 获取当前最佳买价
        best_buy_price = pair.buy_orders[0].price if pair.buy_orders else D0

        # 计算最大可买入量（受限于池子95%的quote储备）
        max_quote = pool.reserve_quote * to_decimal("0.95")
        total_sell_volume = sum(o.remaining_volume for o in pair.sell_orders)

        # 二分法搜索范围
        low = D0
        high = total_sell_volume

        # 先估计high上限：根据资金限制
        quote_accum = D0
        volume_accum = D0
        for order in pair.sell_orders:
            order_cost = order.remaining_volume * order.price
            if quote_accum + order_cost > max_quote:
                # 在这个订单内达到限制
                remaining_quote = max_quote - quote_accum
                if order.price > D0:
                    volume_accum += remaining_quote / order.price
                break
            quote_accum += order_cost
            volume_accum += order.remaining_volume
        high = min(high, volume_accum)

        if high <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 二分法搜索精确交易量
        target_volume = D0
        target_quote = D0
        iterations = 20  # 足够精度

        for _ in range(iterations):
            mid = (low + high) / to_decimal("2")
            if mid <= D0:
                break

            quote_needed, pool_price, consensus_price, _ = self._simulate_buy_arbitrage(
                pair, mid, pool, best_buy_price
            )

            if pool_price <= D0 or consensus_price <= D0:
                high = mid
                continue

            # 误差 = 池子价格 - 共识价格
            # 我们希望池子价格 <= 共识价格（买入后价格下降）
            error = pool_price - consensus_price

            if error > D0:
                # 池子价格还太高，需要买入更多
                low = mid
            else:
                # 池子价格已经低于或等于共识价格
                high = mid
                target_volume = mid
                target_quote = quote_needed

        if target_volume <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 执行实际交易
        remaining_to_buy = target_volume
        total_quote_paid = D0
        actual_base_bought = D0
        orders_to_remove = []

        for order in pair.sell_orders:
            if remaining_to_buy <= D0:
                break

            match_volume = min(remaining_to_buy, order.remaining_volume)
            if match_volume <= D0:
                continue

            match_price = order.price
            quote_paid = match_volume * match_price

            seller = order.trader

            # 将 quote 从池子转给卖家
            seller.assets[pair.quote_token] = seller.assets.get(pair.quote_token, D0) + quote_paid

            # 更新订单状态
            order.executed += match_volume
            order.remaining_frozen -= match_volume

            total_quote_paid += quote_paid
            actual_base_bought += match_volume
            remaining_to_buy -= match_volume

            if order.remaining_volume <= D0:
                orders_to_remove.append(order)

        # 清理已完成的订单
        for order in orders_to_remove:
            seller = order.trader
            if order in seller.orders:
                seller.orders.remove(order)
            if order in pair.sell_orders:
                pair.sell_orders.remove(order)

        if actual_base_bought > D0:
            if pool.reserve_quote - total_quote_paid <= D0:
                return {
                    "direction": "none",
                    "volume": D0,
                    "avg_price": D0,
                    "pre_consensus_price": pre_consensus_price,
                }

            pool.reserve_base += actual_base_bought
            pool.reserve_quote -= total_quote_paid
            pool.k = pool.reserve_base * pool.reserve_quote

            pair.price = self.get_price(pair)
            pair.update_consensus_price()

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

    def _simulate_sell_arbitrage(self, pair, sell_volume: Decimal, pool, best_sell_price: Decimal):
        """
        模拟卖出套利交易后的状态

        Returns:
            (quote_received, new_pool_price, new_consensus_price, actual_volume)
            如果池子储备不足，new_pool_price 返回 D0
        """
        if sell_volume <= D0 or not pair.buy_orders:
            return D0, D0, D0, D0

        max_base = pool.reserve_base * to_decimal("0.95")

        remaining = sell_volume
        total_quote = D0
        actual_volume = D0
        last_price = D0

        for order in pair.buy_orders:
            if remaining <= D0:
                break
            match = min(remaining, order.remaining_volume)
            if match <= D0:
                continue
            if actual_volume + match > max_base:
                match = max_base - actual_volume
                if match <= D0:
                    break
                quote = match * order.price
            else:
                quote = match * order.price
            total_quote += quote
            actual_volume += match
            remaining -= match
            last_price = order.price

        if actual_volume <= D0:
            return D0, D0, D0, D0

        new_reserve_base = pool.reserve_base - actual_volume
        new_reserve_quote = pool.reserve_quote + total_quote

        if new_reserve_base <= D0 or new_reserve_quote <= D0:
            return total_quote, D0, D0, actual_volume

        new_pool_price = new_reserve_quote / new_reserve_base

        remaining_after = sell_volume
        new_best_buy = D0
        for order in pair.buy_orders:
            if remaining_after <= D0:
                new_best_buy = order.price
                break
            order_remaining = order.remaining_volume
            if remaining_after < order_remaining:
                new_best_buy = order.price
                break
            remaining_after -= order_remaining

        if new_best_buy <= D0:
            new_best_buy = last_price if last_price > D0 else (
                pair.buy_orders[-1].price if pair.buy_orders else D0
            )

        if new_best_buy > D0 and best_sell_price > D0:
            new_consensus = (new_best_buy + best_sell_price) / to_decimal("2")
        elif new_best_buy > D0:
            new_consensus = new_best_buy
        elif best_sell_price > D0:
            new_consensus = best_sell_price
        else:
            new_consensus = D0

        return total_quote, new_pool_price, new_consensus, actual_volume

    def _arbitrage_sell_from_orderbook_exact(
        self, pair, pre_consensus_price: Decimal
    ) -> Dict[str, Decimal]:
        """
        AMM 精确套利：向订单簿卖出 base_token，使池子价格等于市场共识价格

        触发条件：P_amm < P_c（池子价格低于市场共识价格，base便宜了）
        操作效果：卖出 base → R_b↓, R_q↑ → P_amm↑

        使用二分法精确求解交易量，确保 P_amm' = P_c'
        """
        pool = self._get_pool(pair)

        if not pair.buy_orders:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 获取当前最佳卖价
        best_sell_price = pair.sell_orders[0].price if pair.sell_orders else D0

        # 计算最大可卖出量（受限于池子95%的base储备和订单簿深度）
        max_base = pool.reserve_base * to_decimal("0.95")
        total_buy_volume = sum(o.remaining_volume for o in pair.buy_orders)

        # 二分法搜索范围
        low = D0
        high = min(max_base, total_buy_volume)

        if high <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 二分法搜索精确交易量
        target_volume = D0
        target_quote = D0
        iterations = 20  # 足够精度

        for _ in range(iterations):
            mid = (low + high) / to_decimal("2")
            if mid <= D0:
                break

            quote_received, pool_price, consensus_price, _ = self._simulate_sell_arbitrage(
                pair, mid, pool, best_sell_price
            )

            if pool_price <= D0 or consensus_price <= D0:
                high = mid
                continue

            # 误差 = 池子价格 - 共识价格
            # 我们希望池子价格 >= 共识价格（卖出后价格上升）
            error = pool_price - consensus_price

            if error < D0:
                # 池子价格还太低，需要卖出更多
                low = mid
            else:
                # 池子价格已经高于或等于共识价格
                high = mid
                target_volume = mid
                target_quote = quote_received

        if target_volume <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        # 执行实际交易
        remaining_to_sell = target_volume
        total_quote_received = D0
        actual_base_sold = D0
        orders_to_remove = []

        for order in pair.buy_orders:
            if remaining_to_sell <= D0:
                break

            match_volume = min(remaining_to_sell, order.remaining_volume)
            if match_volume <= D0:
                continue

            match_price = order.price
            quote_received = match_volume * match_price

            buyer = order.trader

            # 将 base_token 转给买家
            buyer.assets[pair.base_token] = buyer.assets.get(pair.base_token, D0) + match_volume

            # 从买家获得冻结的 quote
            order.executed += match_volume
            order.remaining_frozen -= quote_received

            total_quote_received += quote_received
            actual_base_sold += match_volume
            remaining_to_sell -= match_volume

            if order.remaining_volume <= D0:
                orders_to_remove.append(order)

        # 清理已完成的订单
        for order in orders_to_remove:
            buyer = order.trader
            if order in buyer.orders:
                buyer.orders.remove(order)
            if order in pair.buy_orders:
                pair.buy_orders.remove(order)

        if actual_base_sold > D0:
            if pool.reserve_base - actual_base_sold <= D0:
                return {
                    "direction": "none",
                    "volume": D0,
                    "avg_price": D0,
                    "pre_consensus_price": pre_consensus_price,
                }

            pool.reserve_base -= actual_base_sold
            pool.reserve_quote += total_quote_received
            pool.k = pool.reserve_base * pool.reserve_quote

            pair.price = self.get_price(pair)
            pair.update_consensus_price()

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

    def execute_market_order(
        self, pair, trader, direction: str, volume: Decimal
    ) -> Tuple[Decimal, List[Dict], Decimal]:
        """
        使用 AMM 池执行市价单

        当订单簿深度不足时，使用 AMM 池作为最后的做市商。
        通过微积分方法连续根据当前储备调整做市成交价。

        积分定价方法：
        当购买 Δbase 时，支付的 quote 为积分：
        ∫(k / (R_base - x)²)dx from 0 to Δbase
        = k * (1/(R_base - Δbase) - 1/R_base)

        Args:
            pair: 交易对
            trader: 下单交易者
            direction: 'buy' 或 'sell'
            volume: 剩余未成交的成交量

        Returns:
            (实际成交量, 成交明细列表, 总手续费)
        """
        pool = self._get_pool(pair)

        if volume <= D0:
            return D0, [], D0

        if pool.reserve_base <= D0 or pool.reserve_quote <= D0 or pool.k <= D0:
            return D0, [], D0

        trade_details = []
        total_fee = D0
        executed_volume = D0

        if direction == "buy":
            max_base = min(volume, pool.reserve_base * to_decimal("0.95"))

            if max_base <= D0:
                return D0, [], D0

            # 计算购买 max_base 需要多少 quote
            new_reserve_base = pool.reserve_base - max_base
            new_reserve_quote = pool.k / new_reserve_base
            quote_needed = new_reserve_quote - pool.reserve_quote

            # 检查交易者是否有足够的 quote
            available_quote = trader.assets.get(pair.quote_token, D0)
            if available_quote < quote_needed:
                # 如果资金不足，重新计算能买多少
                if available_quote <= D0:
                    return D0, [], D0
                # 根据可用 quote 反推能买多少 base
                new_reserve_quote = pool.reserve_quote + available_quote
                new_reserve_base = pool.k / new_reserve_quote
                max_base = pool.reserve_base - new_reserve_base
                if max_base <= D0:
                    return D0, [], D0
                quote_needed = available_quote

            trader.assets[pair.quote_token] = trader.assets.get(pair.quote_token, D0) - quote_needed
            trader.assets[pair.base_token] = trader.assets.get(pair.base_token, D0) + max_base

            pool.reserve_base = pool.reserve_base - max_base
            pool.reserve_quote = pool.reserve_quote + quote_needed

            pair.price = self.get_price(pair)
            pair.consensus_price = pair.price

            pair.log.append((time.time(), pair.price, max_base, D0, D0))

            trade_details.append({
                "price": pair.price,
                "volume": max_base,
                "cost": quote_needed,
                "buyer_fee": D0,
                "seller_fee": D0,
                "counterparty": "AMM",
            })

            executed_volume = max_base

        else:  # sell
            # 使用传入的 volume，而不是交易者的全部资产
            max_sell = min(volume, pool.reserve_base * to_decimal("0.95"))

            if max_sell <= D0:
                return D0, [], D0

            # 检查交易者是否有足够的 base
            available_base = trader.assets.get(pair.base_token, D0)
            if available_base < max_sell:
                if available_base <= D0:
                    return D0, [], D0
                max_sell = available_base
                max_sell = min(max_sell, pool.reserve_base * to_decimal("0.95"))

            # 计算卖出 max_sell 能获得多少 quote
            new_reserve_base = pool.reserve_base + max_sell
            new_reserve_quote = pool.k / new_reserve_base
            quote_received = pool.reserve_quote - new_reserve_quote

            if quote_received <= D0:
                return D0, [], D0

            trader.assets[pair.base_token] = available_base - max_sell
            trader.assets[pair.quote_token] = trader.assets.get(pair.quote_token, D0) + quote_received

            pool.reserve_base = pool.reserve_base + max_sell
            pool.reserve_quote = pool.reserve_quote - quote_received

            pair.price = self.get_price(pair)
            pair.consensus_price = pair.price

            pair.log.append((time.time(), pair.price, max_sell, D0, D0))

            trade_details.append({
                "price": pair.price,
                "volume": max_sell,
                "revenue": quote_received,
                "buyer_fee": D0,
                "seller_fee": D0,
                "counterparty": "AMM",
            })

            executed_volume = max_sell

        return executed_volume, trade_details, total_fee

    def charge_slippage_compensation(
        self,
        pair,
        buyer,
        seller,
        arbitrage_result: Dict[str, Decimal],
        match_volume: Decimal,
        match_price: Decimal,
    ) -> None:
        """
        收取滑点成本补偿费给AMM（限价单撮合场景）

        手续费比例限制：
        - 手续费比例必须在 min_fee_rate 和 max_fee_rate 之间
        - 实时收费换算为手续费比例并保存到池子数据中
        - 当池子没有流动性时，收取 min_fee_rate 作为冷启动手续费
        """
        pool = self._get_pool(pair)

        trade_value = match_volume * match_price
        if trade_value <= D0:
            return

        # 检查是否有套利发生
        has_arbitrage = arbitrage_result.get("direction") != "none"

        if has_arbitrage:
            # 有套利时，根据滑点计算手续费
            volume = arbitrage_result.get("volume", D0)
            avg_price = arbitrage_result.get("avg_price", D0)
            pre_consensus_price = arbitrage_result.get("pre_consensus_price", D0)

            if volume <= D0 or avg_price <= D0 or pre_consensus_price <= D0:
                return

            direction = arbitrage_result["direction"]
            if direction == "buy":
                slippage_cost = (avg_price - pre_consensus_price) * volume
            else:
                slippage_cost = (pre_consensus_price - avg_price) * volume

            if slippage_cost <= D0:
                return

            raw_fee_rate = slippage_cost / trade_value
            fee_rate = max(self.min_fee_rate, min(self.max_fee_rate, raw_fee_rate))
        else:
            # 无套利时（池子为空），收取最小手续费作为冷启动资金
            fee_rate = self.min_fee_rate

        pool.current_fee_rate = fee_rate

        total_fee_quote = trade_value * fee_rate

        current_consensus_price = pair.consensus_price
        if current_consensus_price <= D0:
            return

        # 按 AMM 内部价格计算手续费分配
        # 总手续费价值 = total_fee_quote (以 USDT 计价)
        # 按当前储备比例分配：base_fee / quote_fee = reserve_base / reserve_quote
        amm_price = self.get_price(pair)
        if amm_price <= D0:
            return

        # 手续费按 50/50 由买卖双方分担，但按 AMM 价格换算
        # 买家付 base_token，卖家付 quote_token
        fee_value_each = total_fee_quote / to_decimal("2")  # 每人承担一半价值
        buyer_fee_base = fee_value_each / amm_price  # 买家付 ETH
        seller_fee_quote = fee_value_each  # 卖家付 USDT

        # 从买家扣除 base_token
        buyer_current_base = buyer.assets.get(pair.base_token, D0)
        if buyer_current_base >= buyer_fee_base:
            buyer.assets[pair.base_token] = buyer_current_base - buyer_fee_base
        else:
            buyer_fee_base = buyer_current_base
            buyer.assets[pair.base_token] = D0

        # 从卖家扣除 quote_token
        seller_current_quote = seller.assets.get(pair.quote_token, D0)
        if seller_current_quote >= seller_fee_quote:
            seller.assets[pair.quote_token] = seller_current_quote - seller_fee_quote
        else:
            seller_fee_quote = seller_current_quote
            seller.assets[pair.quote_token] = D0

        # RMM 池获得手续费
        if buyer_fee_base > D0:
            pool.reserve_base += buyer_fee_base
        if seller_fee_quote > D0:
            pool.reserve_quote += seller_fee_quote

        pool.k = pool.reserve_base * pool.reserve_quote

    def charge_slippage_compensation_market_order(
        self,
        pair,
        taker,
        counterparties: List[Tuple],
        total_volume: Decimal,
        direction: str,
        arbitrage_result: Dict[str, Decimal],
    ) -> None:
        """
        收取市价单滑点成本补偿费给AMM

        市价单场景下，滑点成本由市价单发起方（Taker）和所有参与交易的对手方（Makers）
        按各自的成交量比例分担。

        手续费比例限制：
        - 手续费比例必须在 min_fee_rate 和 max_fee_rate 之间
        - 实时收费换算为手续费比例并保存到池子数据中
        - 当池子没有流动性时，收取 min_fee_rate 作为冷启动手续费
        """
        pool = self._get_pool(pair)

        avg_price = arbitrage_result.get("avg_price", D0)
        if avg_price <= D0:
            return

        trade_value = total_volume * avg_price
        if trade_value <= D0:
            return

        # 检查是否有套利发生
        has_arbitrage = arbitrage_result.get("direction") != "none"

        if has_arbitrage:
            # 有套利时，根据滑点计算手续费
            volume = arbitrage_result.get("volume", D0)
            pre_consensus_price = arbitrage_result.get("pre_consensus_price", D0)

            if volume <= D0 or pre_consensus_price <= D0:
                return

            arb_direction = arbitrage_result["direction"]
            if arb_direction == "buy":
                slippage_cost = (avg_price - pre_consensus_price) * volume
            else:
                slippage_cost = (pre_consensus_price - avg_price) * volume

            if slippage_cost <= D0:
                return

            raw_fee_rate = slippage_cost / trade_value
            fee_rate = max(self.min_fee_rate, min(self.max_fee_rate, raw_fee_rate))
        else:
            # 无套利时（池子为空），收取最小手续费作为冷启动资金
            fee_rate = self.min_fee_rate

        pool.current_fee_rate = fee_rate

        current_consensus_price = pair.consensus_price
        if current_consensus_price <= D0:
            return

        total_fee_quote = trade_value * fee_rate
        total_fee_base = total_fee_quote / current_consensus_price

        taker_volume = total_volume
        total_participants_volume = taker_volume + sum(v for _, v in counterparties)

        if total_participants_volume <= D0:
            return

        # 按 AMM 内部价格计算手续费分配
        amm_price = self.get_price(pair)
        if amm_price <= D0:
            return

        # 手续费按成交比例分配给 taker 和 makers
        taker_share = taker_volume / total_participants_volume

        # 买入方向：taker 付 base，makers 付 quote
        # 卖出方向：taker 付 quote，makers 付 base
        if direction == "buy":
            # Taker (买家) 付 base_token
            taker_fee_value = total_fee_quote * taker_share / to_decimal("2")
            taker_fee_base = taker_fee_value / amm_price

            taker_current_base = taker.assets.get(pair.base_token, D0)
            if taker_current_base >= taker_fee_base:
                taker.assets[pair.base_token] = taker_current_base - taker_fee_base
            else:
                taker_fee_base = taker_current_base
                taker.assets[pair.base_token] = D0

            if taker_fee_base > D0:
                pool.reserve_base += taker_fee_base

            # Makers (卖家) 付 quote_token
            for counterparty, cp_volume in counterparties:
                cp_share = cp_volume / total_participants_volume
                cp_fee_value = total_fee_quote * cp_share / to_decimal("2")

                cp_current_quote = counterparty.assets.get(pair.quote_token, D0)
                if cp_current_quote >= cp_fee_value:
                    counterparty.assets[pair.quote_token] = cp_current_quote - cp_fee_value
                    pool.reserve_quote += cp_fee_value
                else:
                    pool.reserve_quote += cp_current_quote
                    counterparty.assets[pair.quote_token] = D0
        else:
            # Taker (卖家) 付 quote_token
            taker_fee_value = total_fee_quote * taker_share / to_decimal("2")

            taker_current_quote = taker.assets.get(pair.quote_token, D0)
            if taker_current_quote >= taker_fee_value:
                taker.assets[pair.quote_token] = taker_current_quote - taker_fee_value
            else:
                taker_fee_value = taker_current_quote
                taker.assets[pair.quote_token] = D0

            if taker_fee_value > D0:
                pool.reserve_quote += taker_fee_value

            # Makers (买家) 付 base_token
            for counterparty, cp_volume in counterparties:
                cp_share = cp_volume / total_participants_volume
                cp_fee_value = total_fee_quote * cp_share / to_decimal("2")
                cp_fee_base = cp_fee_value / amm_price

                cp_current_base = counterparty.assets.get(pair.base_token, D0)
                if cp_current_base >= cp_fee_base:
                    counterparty.assets[pair.base_token] = cp_current_base - cp_fee_base
                    pool.reserve_base += cp_fee_base
                else:
                    pool.reserve_base += cp_current_base
                    counterparty.assets[pair.base_token] = D0

        pool.k = pool.reserve_base * pool.reserve_quote

    def step(self, dt: Decimal) -> None:
        pass
