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
        min_fee_rate="0.00001",
        max_fee_rate="0.001",
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

        Args:
            pair: 交易对
            dt: 时间步长（秒），用于控制套利速度

        Returns:
            包含套利成交信息的字典
        """
        pool = self._get_pool(pair)
        pre_consensus_price = pair.consensus_price

        if pool.reserve_base <= D0 or pool.reserve_quote <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        exact_volume, trade_price = self._calculate_exact_arbitrage_volume(pair)

        if exact_volume <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        amm_price = self.get_price(pair)
        consensus_price = pair.consensus_price
        price_diff = consensus_price - amm_price

        target_volume = exact_volume

        if price_diff > D0:
            return self._arbitrage_sell_from_orderbook_exact(
                pair, target_volume, trade_price, pre_consensus_price
            )
        else:
            return self._arbitrage_buy_from_orderbook_exact(
                pair, target_volume, trade_price, pre_consensus_price
            )

    def _arbitrage_buy_from_orderbook_exact(
        self, pair, target_volume: Decimal, expected_price: Decimal, pre_consensus_price: Decimal
    ) -> Dict[str, Decimal]:
        """
        AMM 精确套利：从订单簿买入 base_token

        核心原理：按照恒定乘积公式更新储备，而不是简单按成交价计算。
        当AMM买入Δbase时：
        - 新base储备：R_b' = R_b + Δ
        - 新quote储备：R_q' = k / R_b'（保持k不变）
        - 实际支付的quote：R_q - R_q'
        """
        pool = self._get_pool(pair)

        if target_volume <= D0 or not pair.sell_orders:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        new_reserve_base = pool.reserve_base + target_volume

        max_quote_to_spend = pool.reserve_quote * to_decimal("0.95")

        new_reserve_quote = pool.k / new_reserve_base
        quote_needed = pool.reserve_quote - new_reserve_quote

        if quote_needed > max_quote_to_spend:
            new_reserve_quote_limited = pool.reserve_quote - max_quote_to_spend
            new_reserve_base_limited = pool.k / new_reserve_quote_limited
            target_volume = new_reserve_base_limited - pool.reserve_base
            if target_volume <= D0:
                return {
                    "direction": "none",
                    "volume": D0,
                    "avg_price": D0,
                    "pre_consensus_price": pre_consensus_price,
                }
            new_reserve_base = new_reserve_base_limited
            quote_needed = max_quote_to_spend

        remaining_to_buy = target_volume
        total_quote_paid = D0
        actual_base_bought = D0

        while remaining_to_buy > D0 and pair.sell_orders:
            sell_order = pair.sell_orders[0]
            match_volume = min(remaining_to_buy, sell_order.remaining_volume)
            match_price = sell_order.price

            quote_paid = match_volume * match_price

            available_quote = pool.reserve_quote - total_quote_paid
            if quote_paid > available_quote:
                match_volume = available_quote / match_price
                if match_volume <= D0:
                    break
                quote_paid = match_volume * match_price

            seller = sell_order.trader
            seller.assets[pair.quote_token] = seller.assets.get(pair.quote_token, D0) + quote_paid

            sell_order.executed += match_volume
            sell_order.remaining_frozen -= match_volume

            total_quote_paid += quote_paid
            actual_base_bought += match_volume
            remaining_to_buy -= match_volume

            if sell_order.remaining_volume <= D0:
                if sell_order in seller.orders:
                    seller.orders.remove(sell_order)
                pair.sell_orders.remove(sell_order)

        if actual_base_bought > D0:
            pool.reserve_base += actual_base_bought
            pool.reserve_quote = pool.k / pool.reserve_base

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

    def _arbitrage_sell_from_orderbook_exact(
        self, pair, target_volume: Decimal, expected_price: Decimal, pre_consensus_price: Decimal
    ) -> Dict[str, Decimal]:
        """
        AMM 精确套利：向订单簿卖出 base_token

        核心原理：按照恒定乘积公式更新储备。
        当AMM卖出Δbase时：
        - 新base储备：R_b' = R_b - Δ
        - 新quote储备：R_q' = k / R_b'（保持k不变）
        - 实际获得的quote：R_q' - R_q
        """
        pool = self._get_pool(pair)

        if target_volume <= D0 or not pair.buy_orders:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        new_reserve_base = pool.reserve_base - target_volume

        if new_reserve_base <= D0:
            new_reserve_base = pool.reserve_base * to_decimal("0.05")
            target_volume = pool.reserve_base - new_reserve_base

        max_base_to_sell = pool.reserve_base * to_decimal("0.95")
        if target_volume > max_base_to_sell:
            target_volume = max_base_to_sell
            new_reserve_base = pool.reserve_base - target_volume

        new_reserve_quote = pool.k / new_reserve_base
        quote_to_receive = new_reserve_quote - pool.reserve_quote

        if quote_to_receive <= D0:
            return {
                "direction": "none",
                "volume": D0,
                "avg_price": D0,
                "pre_consensus_price": pre_consensus_price,
            }

        remaining_to_sell = target_volume
        total_quote_received = D0
        actual_base_sold = D0

        while remaining_to_sell > D0 and pair.buy_orders:
            buy_order = pair.buy_orders[0]
            match_volume = min(remaining_to_sell, buy_order.remaining_volume)
            match_price = buy_order.price

            quote_received = match_volume * match_price

            buyer = buy_order.trader
            buyer.assets[pair.base_token] = buyer.assets.get(pair.base_token, D0) + match_volume

            buy_order.executed += match_volume
            buy_order.remaining_frozen -= quote_received

            total_quote_received += quote_received
            actual_base_sold += match_volume
            remaining_to_sell -= match_volume

            if buy_order.remaining_volume <= D0:
                if buy_order in buyer.orders:
                    buyer.orders.remove(buy_order)
                pair.buy_orders.remove(buy_order)

        if actual_base_sold > D0:
            pool.reserve_base -= actual_base_sold
            pool.reserve_quote = pool.k / pool.reserve_base

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

        trade_details = []
        total_fee = D0
        executed_volume = D0

        if direction == "buy":
            available_quote = trader.assets.get(pair.quote_token, D0)

            if available_quote <= D0:
                return D0, [], D0

            max_base = pool.reserve_base - pool.k / (pool.reserve_quote + available_quote)

            max_base = min(max_base, pool.reserve_base * to_decimal("0.95"))

            if max_base <= D0:
                return D0, [], D0

            new_reserve_base = pool.reserve_base - max_base
            new_reserve_quote = pool.k / new_reserve_base
            quote_needed = new_reserve_quote - pool.reserve_quote

            trader.assets[pair.quote_token] = trader.assets.get(pair.quote_token, D0) - quote_needed
            trader.assets[pair.base_token] = trader.assets.get(pair.base_token, D0) + max_base

            pool.reserve_base = pool.reserve_base - max_base
            pool.reserve_quote = pool.reserve_quote + quote_needed

            pair.price = self.get_price(pair)
            pair.update_consensus_price()

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
            available_base = trader.assets.get(pair.base_token, D0)

            if available_base <= D0:
                return D0, [], D0

            max_sell = min(available_base, pool.reserve_base * to_decimal("0.95"))

            if max_sell <= D0:
                return D0, [], D0

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
            pair.update_consensus_price()

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

        total_fee_base = total_fee_quote / current_consensus_price

        buyer_fee_base = total_fee_base / to_decimal("2")
        seller_fee_quote = total_fee_quote / to_decimal("2")

        buyer_current_base = buyer.assets.get(pair.base_token, D0)
        if buyer_current_base >= buyer_fee_base:
            buyer.assets[pair.base_token] = buyer_current_base - buyer_fee_base
        else:
            buyer_fee_base = buyer_current_base
            buyer.assets[pair.base_token] = D0

        seller_current_quote = seller.assets.get(pair.quote_token, D0)
        if seller_current_quote >= seller_fee_quote:
            seller.assets[pair.quote_token] = seller_current_quote - seller_fee_quote
        else:
            seller_fee_quote = seller_current_quote
            seller.assets[pair.quote_token] = D0

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

        taker_share = taker_volume / total_participants_volume
        taker_fee_base = total_fee_base * taker_share
        taker_fee_quote = total_fee_quote * taker_share

        if direction == "buy":
            taker_current_base = taker.assets.get(pair.base_token, D0)
            if taker_current_base >= taker_fee_base:
                taker.assets[pair.base_token] = taker_current_base - taker_fee_base
            else:
                taker_fee_base = taker_current_base
                taker.assets[pair.base_token] = D0
        else:
            taker_current_quote = taker.assets.get(pair.quote_token, D0)
            if taker_current_quote >= taker_fee_quote:
                taker.assets[pair.quote_token] = taker_current_quote - taker_fee_quote
            else:
                taker_fee_quote = taker_current_quote
                taker.assets[pair.quote_token] = D0

        if taker_fee_base > D0:
            pool.reserve_base += taker_fee_base
        if taker_fee_quote > D0:
            pool.reserve_quote += taker_fee_quote

        for counterparty, cp_volume in counterparties:
            cp_share = cp_volume / total_participants_volume
            cp_fee_base = total_fee_base * cp_share
            cp_fee_quote = total_fee_quote * cp_share

            if direction == "buy":
                cp_current_quote = counterparty.assets.get(pair.quote_token, D0)
                if cp_current_quote >= cp_fee_quote:
                    counterparty.assets[pair.quote_token] = cp_current_quote - cp_fee_quote
                else:
                    cp_fee_quote = cp_current_quote
                    counterparty.assets[pair.quote_token] = D0
            else:
                cp_current_base = counterparty.assets.get(pair.base_token, D0)
                if cp_current_base >= cp_fee_base:
                    counterparty.assets[pair.base_token] = cp_current_base - cp_fee_base
                else:
                    cp_fee_base = cp_current_base
                    counterparty.assets[pair.base_token] = D0

            if cp_fee_base > D0:
                pool.reserve_base += cp_fee_base
            if cp_fee_quote > D0:
                pool.reserve_quote += cp_fee_quote

        pool.k = pool.reserve_base * pool.reserve_quote

    def step(self, dt: Decimal) -> None:
        pass
