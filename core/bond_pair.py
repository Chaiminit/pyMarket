import time
import math
from typing import Dict, Set, List, Tuple, Callable

from .trader import Trader
from .order import BondOrder


class BondTradingPair:
    """债券交易对 - 管理债券订单簿、利息计算、清算，直接操作 Trader 对象"""

    def __init__(self, token_name: str, initial_rate: float):
        self.token_name = token_name
        self.current_rate = initial_rate
        self.log = []
        self.buy_orders: List[BondOrder] = []
        self.sell_orders: List[BondOrder] = []
        self.clients: Set[int] = set()
        self.bond_pair_id = -1

    def get_total_bonds(self, traders_map: Dict[int, Trader]) -> float:
        """统计所有clients的债券总和"""
        bond_key = f"BOND-{self.token_name}"
        total = 0.0
        for tid in self.clients:
            if tid in traders_map:
                total += traders_map[tid].bonds.get(bond_key, 0.0)
        return total

    def settle_interest_simple(
        self, traders_map: Dict[int, Trader], dt: float
    ) -> List[Tuple[Trader, float]]:
        """
        简单高频利息结算 - 每步直接转移
        从负债券持有者收取利息，支付给正债券持有者
        处理所有有债券的交易者（不只是clients）
        返回: 无法足额支付利息的债务人列表 [(trader, 缺口金额), ...]
        """
        bond_key = f"BOND-{self.token_name}"
        insolvent_debtors = []  # 无法偿债的债务人

        if self.current_rate == 0 or dt <= 0:
            return insolvent_debtors

        creditors = []  # (trader, bond_amount)
        debtors = []  # (trader, bond_amount)
        total_positive = 0.0
        total_negative = 0.0
        total_frozen_sell = 0.0  # 卖单冻结的债券

        all_trader_ids = set(traders_map.keys())

        for tid in all_trader_ids:
            if tid not in traders_map:
                continue
            trader = traders_map[tid]
            bond_amt = trader.bonds.get(bond_key, 0.0)

            # 计算该交易者的卖单冻结量
            frozen_sell = 0.0
            for order in self.sell_orders:
                if id(order.trader) == tid:
                    frozen_sell += order.volume - order.executed

            effective_bond = bond_amt + frozen_sell  # 持仓 + 冻结 = 有效债券

            if abs(effective_bond) < 0.000001:
                continue

            if effective_bond > 0.000001:
                creditors.append((trader, effective_bond))
                total_positive += effective_bond
            elif effective_bond < -0.000001:
                debtors.append((trader, -effective_bond))
                total_negative += -effective_bond

            total_frozen_sell += frozen_sell

        if total_positive < 0.000001 or total_negative < 0.000001:
            return insolvent_debtors

        # 计算总利息 = 平均债券 × 利率 × 时间
        # 使用债务和债权的较小值作为有效债券基数
        effective_bonds = min(total_positive, total_negative)
        total_interest = effective_bonds * self.current_rate * dt

        if total_interest < 0.000001:
            return insolvent_debtors

        # 从债务人收取利息
        collected = 0.0
        shortfall = 0.0  # 记录未收到的利息
        for debtor, debt_amt in debtors:
            ratio = debt_amt / total_negative
            interest_to_pay = total_interest * ratio
            # 确保不超额收取
            available = debtor.assets.get(self.token_name, 0.0)
            actual_pay = min(interest_to_pay, available)
            if actual_pay > 0:
                debtor.assets[self.token_name] -= actual_pay
                collected += actual_pay
            if actual_pay < interest_to_pay:
                shortfall += interest_to_pay - actual_pay
                # 记录无法足额支付的债务人
                insolvent_debtors.append((debtor, interest_to_pay - actual_pay))

        if collected < 0.000001:
            return insolvent_debtors

        # 支付给债权人（只支付实际收到的金额）
        distributed = 0.0
        for creditor, credit_amt in creditors:
            ratio = credit_amt / total_positive
            interest_to_receive = collected * ratio
            if interest_to_receive > 0:
                creditor.assets[self.token_name] = (
                    creditor.assets.get(self.token_name, 0.0) + interest_to_receive
                )
                distributed += interest_to_receive

        # 检查债券是否平衡 - 只输出警告，不立即调整
        bond_diff = abs(total_positive - total_negative)
        if bond_diff > 0.01:
            print(
                f"[债券不平衡] {self.token_name}: 债权={total_positive:.2f} 债务={total_negative:.2f}"
            )

        return insolvent_debtors

    def check_and_rebalance(self, traders_map: Dict[int, Trader]):
        bond_key = f"BOND-{self.token_name}"
        total_positive = 0.0
        total_negative = 0.0

        for tid, trader in traders_map.items():
            bond_amt = trader.bonds.get(bond_key, 0.0)
            frozen_sell = 0.0
            for order in self.sell_orders:
                if id(order.trader) == tid:
                    frozen_sell += order.volume - order.executed
            effective_bond = bond_amt + frozen_sell

            if effective_bond > 0.000001:
                total_positive += effective_bond
            elif effective_bond < -0.000001:
                total_negative += -effective_bond

        if abs(total_positive - total_negative) > 0.01:
            print(
                f"[债券不平衡] {self.token_name}: 债权={total_positive:.2f} 债务={total_negative:.2f}"
            )
            self._rebalance_bonds(traders_map, total_positive, total_negative)

    def _rebalance_bonds(
        self, traders_map: Dict[int, Trader], total_positive: float, total_negative: float
    ):
        bond_key = f"BOND-{self.token_name}"

        if abs(total_positive - total_negative) <= 0.000001:
            return

        if total_positive >= total_negative:
            excess = total_positive - total_negative
            reduce_ratio = excess / total_positive
        else:
            shortfall = total_negative - total_positive
            forgive_ratio = shortfall / total_negative

        for tid, trader in traders_map.items():
            bond_amt = trader.bonds.get(bond_key, 0.0)
            frozen_sell = 0.0
            for order in self.sell_orders:
                if id(order.trader) == tid:
                    frozen_sell += order.volume - order.executed
            effective_bond = bond_amt + frozen_sell

            if total_positive >= total_negative:
                if effective_bond > 0.000001:
                    write_off = effective_bond * reduce_ratio
                    bond_write_off = min(write_off, bond_amt)
                    frozen_write_off = write_off - bond_write_off
                    if bond_write_off > 0.000001:
                        trader.bonds[bond_key] = bond_amt - bond_write_off
                    if frozen_write_off > 0.000001:
                        for order in self.sell_orders:
                            if id(order.trader) == tid:
                                order_reduce = min(frozen_write_off, order.volume - order.executed)
                                order.volume -= order_reduce
                                order.remaining_frozen -= order_reduce
                                frozen_write_off -= order_reduce
                                if order.volume <= 0.000001:
                                    order.close()
            else:
                if effective_bond < -0.000001:
                    forgive_amount = (-effective_bond) * forgive_ratio
                    trader.bonds[bond_key] = bond_amt + forgive_amount

    def submit_limit_order(self, trader: Trader, direction: str, rate: float, volume: float):
        """提交债券限价单"""
        bond_key = f"BOND-{self.token_name}"
        if direction == "buy":
            # 买单：冻结 USDT
            required = volume
            if trader.assets.get(self.token_name, 0.0) < required:
                return
            trader.assets[self.token_name] -= required
            frozen_amount = required
        else:
            # 卖单：冻结债券（预扣持仓）
            trader.bonds[bond_key] = trader.bonds.get(bond_key, 0.0) - volume
            frozen_amount = volume

        order = BondOrder(trader, direction, rate, volume, frozen_amount, self.bond_pair_id, self)

        if direction == "buy":
            self.buy_orders.append(order)
            self.buy_orders.sort(
                key=lambda x: (x.interest_rate, x.time)
            )  # 买单按利率升序排列（利率低的在前，更容易成交）
        else:
            self.sell_orders.append(order)
            self.sell_orders.sort(
                key=lambda x: (-x.interest_rate, x.time)
            )  # 卖单按利率降序排列（利率高的在前，更容易成交）

        trader.bond_orders.append(order)
        self.clients.add(id(trader))
        self._match_orders()

    def execute_market_order(
        self, trader: Trader, direction: str, volume: float
    ) -> Tuple[float, List[Dict]]:
        """
        执行债券市价单，返回 (实际成交量，成交明细列表)
        如果请求量大于市场深度，会交易掉所有能交易的量
        """
        bond_key = f"BOND-{self.token_name}"
        now = time.time()

        # 市价单提交者自动加入clients
        self.clients.add(id(trader))

        executed_volume = 0.0
        trade_details = []

        if direction == "buy":
            while volume > 0.0001 and self.sell_orders:
                sell_order = self.sell_orders[0]  # 吃利率最高的卖单
                match_volume = min(volume, sell_order.volume - sell_order.executed)
                match_rate = sell_order.interest_rate

                available = trader.assets.get(self.token_name, 0.0)
                if available < match_volume:
                    if available > 0.0001:
                        match_volume = min(match_volume, available)
                    else:
                        break

                trader.assets[self.token_name] -= match_volume
                trader.bonds[bond_key] = trader.bonds.get(bond_key, 0.0) + match_volume

                seller = sell_order.trader
                seller.assets[self.token_name] = (
                    seller.assets.get(self.token_name, 0.0) + match_volume
                )
                # 卖家 bonds 已在下单时预扣，成交时减少冻结量
                sell_order.remaining_frozen -= match_volume

                self.current_rate = match_rate
                self.log.append((now, match_rate, match_volume))

                trade_details.append(
                    {"rate": match_rate, "volume": match_volume, "order_id": id(sell_order)}
                )

                volume -= match_volume
                executed_volume += match_volume
                sell_order.executed += match_volume

                if sell_order.executed >= sell_order.volume:
                    sell_order.close()  # 关闭卖单
        else:
            while volume > 0.0001 and self.buy_orders:
                buy_order = self.buy_orders[0]  # 吃利率最低的买单
                match_volume = min(volume, buy_order.volume - buy_order.executed)
                match_rate = buy_order.interest_rate

                trader.bonds[bond_key] = trader.bonds.get(bond_key, 0.0) - match_volume
                trader.assets[self.token_name] = (
                    trader.assets.get(self.token_name, 0.0) + match_volume
                )

                buyer = buy_order.trader
                buy_order.remaining_frozen -= match_volume
                buyer.bonds[bond_key] = buyer.bonds.get(bond_key, 0.0) + match_volume

                self.current_rate = match_rate
                self.log.append((now, match_rate, match_volume))

                trade_details.append(
                    {"rate": match_rate, "volume": match_volume, "order_id": id(buy_order)}
                )

                volume -= match_volume
                executed_volume += match_volume
                buy_order.executed += match_volume

                if buy_order.executed >= buy_order.volume:
                    buy_order.close()  # 关闭买单

        return executed_volume, trade_details

    def _match_orders(self):
        """撮合债券订单"""
        now = time.time()

        while self.buy_orders and self.sell_orders:
            buy_order = self.buy_orders[0]
            sell_order = self.sell_orders[0]

            buy_rate = buy_order.interest_rate
            sell_rate = sell_order.interest_rate

            if buy_rate > sell_rate:
                break

            match_rate = buy_rate if buy_order.time <= sell_order.time else sell_rate

            buy_remaining = buy_order.volume - buy_order.executed
            sell_remaining = sell_order.volume - sell_order.executed
            match_volume = min(buy_remaining, sell_remaining)

            buyer = buy_order.trader
            seller = sell_order.trader
            bond_key = f"BOND-{self.token_name}"

            buyer.bonds[bond_key] = buyer.bonds.get(bond_key, 0.0) + match_volume

            seller.assets[self.token_name] = seller.assets.get(self.token_name, 0.0) + match_volume
            # 卖家 bonds 已在下单时预扣，此处不再重复扣减

            buy_order.executed += match_volume
            buy_order.remaining_frozen -= match_volume
            sell_order.executed += match_volume
            sell_order.remaining_frozen -= match_volume

            self.current_rate = match_rate
            self.log.append((now, match_rate, match_volume))

            if buy_order.executed >= buy_order.volume:
                buy_order.close()  # 关闭买单（释放冻结并从列表移除）

            if sell_order.executed >= sell_order.volume:
                sell_order.close()  # 关闭卖单（释放冻结债券并从列表移除）

            if not self.buy_orders or not self.sell_orders:
                break

    def settle(self, all_traders: List[Trader] = None):
        bond_key = f"BOND-{self.token_name}"

        participants = {}
        seen_ids = set()

        for order in self.buy_orders + self.sell_orders:
            trader = order.trader
            tid = id(trader)
            if tid not in seen_ids:
                seen_ids.add(tid)
                participants[tid] = trader

        if all_traders:
            for trader in all_traders:
                tid = id(trader)
                if tid not in seen_ids:
                    amt = trader.bonds.get(bond_key, 0.0)
                    if abs(amt) > 0.000001:
                        participants[tid] = trader

        total_bonds = 0.0
        for trader in participants.values():
            total_bonds += trader.bonds.get(bond_key, 0.0)

        if abs(total_bonds) < 0.000001:
            return

        for trader in participants.values():
            trader.bonds[bond_key] = 0.0

    def liquidate_bonds(
        self,
        trader: Trader,
        assets: Dict[str, float],
        traders_map: Dict[int, Trader],
        price_oracle: Callable[[str, str], float] = None,
    ) -> Tuple[float, float]:
        """
        破产清算 - 原子操作，保证债券守恒

        1. 破产者的负债券(债务): 用资产补偿债权人 + 坏账核销
        2. 破产者的正债券(债权): 按比例豁免对应债务人的债务

        Args:
            trader: 破产者
            assets: 破产者的资产字典 {token_name: amount}
            traders_map: 所有交易者映射
            price_oracle: 价格查询函数 price_oracle(from_token, to_token) -> price

        Returns:
            (实际使用的资产价值(折算成债券代币), 未偿还的坏账)
        """
        bond_key = f"BOND-{self.token_name}"
        bankrupt_id = id(trader)

        b_amt = trader.bonds.get(bond_key, 0.0)
        frozen_sell = sum(
            o.volume - o.executed for o in self.sell_orders if id(o.trader) == id(trader)
        )
        effective = b_amt + frozen_sell

        debt_amount = -min(effective, 0.0)
        credit_amount = max(effective, 0.0)

        if debt_amount <= 0.000001 and credit_amount <= 0.000001:
            return 0.0, 0.0

        total_asset_value = 0.0
        for token_name, amount in assets.items():
            if amount <= 0.00001:
                continue
            if token_name == self.token_name:
                total_asset_value += amount
            elif price_oracle:
                price = price_oracle(token_name, self.token_name)
                if price > 0:
                    total_asset_value += amount * price

        used_token = 0.0
        bad_debt = 0.0

        if debt_amount > 0.000001:
            creditors = []
            total_credit = 0.0
            for tid, entity in traders_map.items():
                if tid == bankrupt_id:
                    continue
                b_amt = entity.bonds.get(bond_key, 0.0)
                frozen_sell = sum(
                    o.volume - o.executed for o in self.sell_orders if id(o.trader) == tid
                )
                effective = b_amt + frozen_sell
                if effective > 0.000001:
                    creditors.append((entity, b_amt, effective))
                    total_credit += effective

            if total_credit > 0.000001 and creditors:
                actual_pay = min(total_asset_value, debt_amount)

                for creditor, b_amt, effective in creditors:
                    ratio = effective / total_credit

                    creditor_token = actual_pay * ratio
                    if creditor_token > 0.00001:
                        creditor.assets[self.token_name] = (
                            creditor.assets.get(self.token_name, 0.0) + creditor_token
                        )

                    pay_ratio = actual_pay / debt_amount if debt_amount > 0 else 0
                    bad_debt = max(debt_amount - actual_pay, 0.0)
                    write_off_ratio = (
                        min(debt_amount / total_credit, 1.0) if total_credit > 0.000001 else 0
                    )
                    write_off = effective * write_off_ratio
                    bond_write_off = min(write_off, max(b_amt, 0))
                    frozen_write_off = write_off - bond_write_off

                    if bond_write_off > 0.000001:
                        creditor.bonds[bond_key] = b_amt - bond_write_off
                    if frozen_write_off > 0.000001:
                        for order in self.sell_orders:
                            if id(order.trader) == id(creditor):
                                order_reduce = min(frozen_write_off, order.volume - order.executed)
                                if order_reduce > 0.000001:
                                    order.volume -= order_reduce
                                    order.remaining_frozen -= order_reduce
                                    frozen_write_off -= order_reduce
                                    if order.volume <= 0.000001:
                                        order.close()

                used_token = actual_pay
                bad_debt = max(debt_amount - actual_pay, 0.0)

        if credit_amount > 0.000001:
            debtors = []
            total_debt = 0.0
            for tid, entity in traders_map.items():
                if tid == bankrupt_id:
                    continue
                b_amt = entity.bonds.get(bond_key, 0.0)
                frozen_sell = sum(
                    o.volume - o.executed for o in self.sell_orders if id(o.trader) == tid
                )
                effective = b_amt + frozen_sell
                if effective < -0.000001:
                    debtors.append((entity, b_amt, -effective))
                    total_debt += -effective

            if total_debt > 0.000001 and debtors:
                forgive_ratio = min(credit_amount / total_debt, 1.0)
                for debtor, b_amt, debt_val in debtors:
                    forgive = min(debt_val * forgive_ratio, debt_val)
                    debtor.bonds[bond_key] = b_amt + forgive

        trader.bonds[bond_key] = 0.0
        return used_token, bad_debt

    def cancel_orders_for_bot(self, trader: Trader):
        trader_id = id(trader)
        bond_key = f"BOND-{self.token_name}"

        for order in list(self.buy_orders):
            if id(order.trader) == trader_id:
                order.close()

        for order in list(self.sell_orders):
            if id(order.trader) == trader_id:
                order.close()

        if trader_id in self.clients:
            self.clients.discard(trader_id)
