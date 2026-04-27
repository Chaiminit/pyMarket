"""
BondPair 模块 - 债券交易对

提供债券市场的核心功能：
- 债券限价单订单簿管理
- 债券订单撮合（转移债券所有权，线程安全）
- 利息结算系统（从债务人收取，支付给债权人）

债券系统机制：
- 正债券 = 债权（借出资金，收取利息）
- 负债券 = 债务（借入资金，支付利息）
- 债券交易转移的是债权/债务关系

线程安全说明：
- 所有订单簿操作都受 _lock 保护
- 撮合过程是原子操作
- 支持多线程并发访问
"""

import time
import math
from typing import Dict, Set, List, Tuple, Optional, TYPE_CHECKING
from threading import Lock
from decimal import Decimal

from .trader import Trader
from .order import BondOrder
from .token import Token
from .utils import to_decimal, D0, D1
from .engine_node import EngineNode

if TYPE_CHECKING:
    pass


class BondTradingPair(EngineNode):
    """
    债券交易对 - 管理债券订单簿和利息结算

    债券交易对以特定代币为标的，交易者可以通过：
    - 买入债券：支付代币，获得正债券（成为债权人）
    - 卖出债券：付出正债券，收回代币（转让债权）
    - 卖空债券：获得代币，获得负债券（成为债务人）

    利息结算：每步从负债券持有者收取利息，支付给正债券持有者。

    Attributes:
        base_token: 债券代币（如 bond_usdt）
        quote_token: 标的代币（如 usdt）
        token: 标的代币（同 quote_token，向后兼容）
        current_rate: 当前市场利率
        log: 成交记录 [(timestamp, rate, volume, buyer_fee, seller_fee), ...]
        buy_orders: 债券买单列表（按利率降序）
        sell_orders: 债券卖单列表（按利率升序）
        clients: 参与此债券市场的交易者集合

    Examples:
        >>> bond_pair = BondTradingPair(usdt, 100, 0.05)  # 5%年利率
        >>> bond_pair.submit_limit_order(trader, "buy", 0.04, 1000, 1000)
        >>> insolvent = bond_pair.settle_interest_simple(traders, dt=0.1)
    """

    def __init__(self, quote_token: Token, bond_token_id: int, initial_rate):
        """
        创建债券交易对

        Args:
            quote_token: 标的代币（如 USDT）
            bond_token_id: 债券代币的唯一标识符
            initial_rate: 初始市场利率（年化）
        """
        super().__init__(f"BOND_{quote_token.token_id}/{initial_rate}")
        self.quote_token = quote_token
        self.token = quote_token  # 向后兼容
        self.bond_token_id = bond_token_id

        # 创建债券代币
        self.base_token = Token(
            token_id=bond_token_id,
            is_quote=False
        )

        self.current_rate = to_decimal(initial_rate)
        self.consensus_rate = self.current_rate  # 共识利率（买卖盘口平均利率）
        self.log: List[Tuple[float, Decimal, Decimal, Decimal, Decimal]] = []
        self.buy_orders: List[BondOrder] = []
        self.sell_orders: List[BondOrder] = []
        self.clients: Set[Trader] = set()

        # 线程锁，用于保护订单簿和撮合操作
        self._lock = Lock()

    def get_total_bonds(self, traders: Set[Trader]) -> Decimal:
        """
        统计所有交易者的债券持仓总和

        Args:
            traders: 交易者集合

        Returns:
            债券总持仓（正债权 - 负债务的净值）
        """
        total = D0
        for trader in traders:
            total += trader.bonds.get(self.token, D0)
        return total

    def update_consensus_rate(self) -> None:
        """
        更新共识利率为买卖盘口的平均利率

        当其中一方不存在时，设为另一方利率。
        当双方都不存在时，保持当前共识利率。
        """
        best_buy = self.buy_orders[0].interest_rate if self.buy_orders else None
        best_sell = self.sell_orders[0].interest_rate if self.sell_orders else None

        if best_buy is not None and best_sell is not None:
            self.consensus_rate = (best_buy + best_sell) / to_decimal("2")
        elif best_buy is not None:
            self.consensus_rate = best_buy
        elif best_sell is not None:
            self.consensus_rate = best_sell
        # 双方都不存在时保持当前共识利率

    def settle_interest_simple(
        self, traders: Set[Trader], dt_seconds
    ) -> List[Tuple[Trader, Decimal]]:
        """
        简单高频利息结算

        结算逻辑：
        1. 识别所有债权人（正债券）和债务人（负债券）
        2. 计算有效债券基数 = min(总债权, 总债务)
        3. 总利息 = 有效债券 × 利率 × 时间(秒) / 一年的秒数
        4. 从债务人按比例收取利息
        5. 将收到的利息按比例支付给债权人

        Args:
            traders: 所有交易者集合
            dt_seconds: 时间步长（秒）

        Returns:
            无法足额支付利息的债务人列表 [(trader, 缺口金额), ...]
        """
        dt_seconds = to_decimal(dt_seconds)
        insolvent_debtors: List[Tuple[Trader, Decimal]] = []

        if self.current_rate == D0 or dt_seconds <= D0:
            return insolvent_debtors

        creditors: List[Tuple[Trader, Decimal]] = []  # (交易者, 有效债权)
        debtors: List[Tuple[Trader, Decimal]] = []    # (交易者, 有效债务)
        total_positive = D0
        total_negative = D0

        # 分类债权人和债务人
        for trader in traders:
            # 债券持仓 = 持有的债券代币数量（正为债权，负为债务）
            bond_holding = trader.assets.get(self.base_token, D0)

            if abs(bond_holding) <= D0:
                continue

            if bond_holding > D0:
                creditors.append((trader, bond_holding))
                total_positive += bond_holding
            elif bond_holding < D0:
                debtors.append((trader, -bond_holding))
                total_negative += -bond_holding

        # 没有对手方，无需结算
        if total_positive <= D0 or total_negative <= D0:
            return insolvent_debtors

        # 计算总利息（基于有效债券基数，时间单位为秒）
        # 年化利率需要转换为秒利率：rate_per_second = current_rate / 31536000
        effective_bonds = min(total_positive, total_negative)
        seconds_per_year = Decimal('31536000')  # 365天 = 31536000秒
        total_interest = effective_bonds * self.current_rate * dt_seconds / seconds_per_year

        if total_interest <= D0:
            return insolvent_debtors

        # 从债务人收取利息（使用 quote_token，即标的代币）
        collected = D0
        for debtor, debt_amt in debtors:
            ratio = debt_amt / total_negative
            interest_to_pay = total_interest * ratio

            available = debtor.assets.get(self.quote_token, D0)
            actual_pay = min(interest_to_pay, available)

            if actual_pay > D0:
                debtor.assets[self.quote_token] -= actual_pay
                collected += actual_pay

            if actual_pay < interest_to_pay:
                shortfall = interest_to_pay - actual_pay
                insolvent_debtors.append((debtor, shortfall))

        # 将收到的利息支付给债权人（使用 quote_token，即标的代币）
        if collected > D0:
            for creditor, credit_amt in creditors:
                ratio = credit_amt / total_positive
                interest_to_receive = collected * ratio
                creditor.assets[self.quote_token] = (
                    creditor.assets.get(self.quote_token, D0) + interest_to_receive
                )

        return insolvent_debtors

    def submit_limit_order(
        self,
        trader: Trader,
        direction: str,
        interest_rate,
        volume,
        frozen_amount,
    ) -> None:
        """
        提交债券限价单（线程安全）

        订单按利率优先、时间优先排序：
        - 买单：利率升序（高利率优先）
        - 卖单：利率降序（低利率优先）

        Args:
            trader: 下单交易者
            direction: 'buy' 或 'sell'
            interest_rate: 目标利率（年化）
            volume: 债券数量
            frozen_amount: 冻结资金（买单）或债券（卖单）
        """
        with self._lock:
            order = BondOrder(
                trader, direction, interest_rate, volume, frozen_amount, self
            )

            if direction == "buy":
                self.buy_orders.append(order)
                # 利率升序，同利率按时间升序
                self.buy_orders.sort(key=lambda x: (x.interest_rate, x.time))
            else:
                self.sell_orders.append(order)
                # 利率降序，同利率按时间升序
                self.sell_orders.sort(key=lambda x: (-x.interest_rate, x.time))

            trader.bond_orders.append(order)
            self.clients.add(trader)
            self._match_bond_orders()
            self.update_consensus_rate()

    def execute_market_bond_order(
        self, trader: Trader, direction: str, volume
    ) -> Tuple[Decimal, List[Dict], Decimal]:
        """
        执行债券市价单 - 立即以最优利率成交（线程安全）

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

            if direction == "buy":
                # 市价买单：遍历卖单簿，从高利率到低利率成交
                while volume > D0 and self.sell_orders:
                    sell_order = self.sell_orders[0]
                    match_volume = min(volume, sell_order.remaining_volume)
                    match_rate = sell_order.interest_rate
                    match_cost = match_volume

                    available = trader.assets.get(self.quote_token, D0)
                    if available < match_cost:
                        if available > D0:
                            match_volume = available
                            match_cost = match_volume
                        else:
                            break

                    # 买家支付 quote_token，获得债券代币
                    trader.assets[self.quote_token] = available - match_cost
                    trader.assets[self.base_token] = (
                        trader.assets.get(self.base_token, D0) + match_volume
                    )

                    # 卖家获得 quote_token，债券代币已冻结
                    seller = sell_order.trader
                    sell_order.remaining_frozen -= match_volume
                    seller.assets[self.quote_token] = (
                        seller.assets.get(self.quote_token, D0) + match_cost
                    )

                    # 记录成交
                    self.log.append((time.time(), match_rate, match_volume, D0, D0))
                    self.current_rate = match_rate
                    self.update_consensus_rate()

                    trade_details.append({
                        "rate": match_rate,
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

                    if sell_order.remaining_volume <= D0:
                        if sell_order in seller.bond_orders:
                            seller.bond_orders.remove(sell_order)
                        self.sell_orders.remove(sell_order)

            else:  # sell
                # 市价卖单：遍历买单簿，从低利率到高利率成交
                while volume > D0 and self.buy_orders:
                    buy_order = self.buy_orders[0]
                    match_volume = min(volume, buy_order.remaining_volume)
                    match_rate = buy_order.interest_rate
                    match_revenue = match_volume

                    # 市价卖单允许负持仓（债务），不需要检查余额

                    # 卖家获得 quote_token，付出债券代币
                    trader.assets[self.quote_token] = (
                        trader.assets.get(self.quote_token, D0) + match_revenue
                    )
                    trader.assets[self.base_token] = (
                        trader.assets.get(self.base_token, D0) - match_volume
                    )

                    # 买家支付 quote_token（已冻结），获得债券代币
                    buyer = buy_order.trader
                    buy_order.remaining_frozen -= match_volume
                    buyer.assets[self.base_token] = (
                        buyer.assets.get(self.base_token, D0) + match_volume
                    )

                    # 记录成交
                    self.log.append((time.time(), match_rate, match_volume, D0, D0))
                    self.current_rate = match_rate
                    self.update_consensus_rate()

                    trade_details.append({
                        "rate": match_rate,
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

                    if buy_order.remaining_volume <= D0:
                        if buy_order in buyer.bond_orders:
                            buyer.bond_orders.remove(buy_order)
                        self.buy_orders.remove(buy_order)

            return executed_volume, trade_details, total_fee

    def _match_bond_orders(self) -> None:
        """
        撮合债券订单

        撮合规则：
        1. 取最优买单（最低利率）和最优卖单（最高利率）
        2. 如果买利率 <= 卖利率，可以成交
        3. 成交量为 min(买剩余, 卖剩余)
        4. 成交利率为卖单利率（被动方利率）
        5. 债券方向：买单获得正债券（债权），卖单获得负债券（债务）
        6. 双方都是 Maker

        债券方向说明：
        - 买单（buy）：借出资金，支付代币，获得正债券（债权）
        - 卖单（sell）：借入资金，获得代币，获得负债券（债务）
        """
        while self.buy_orders and self.sell_orders:
            best_buy = self.buy_orders[0]
            best_sell = self.sell_orders[0]

            # 检查利率是否匹配（买利率 <= 卖利率）
            if best_buy.interest_rate > best_sell.interest_rate:
                break

            match_volume = min(best_buy.remaining_volume, best_sell.remaining_volume)
            match_rate = best_sell.interest_rate

            buyer = best_buy.trader
            seller = best_sell.trader

            total_buyer_cost = match_volume

            # 检查买家资金（使用剩余冻结资金）
            # 买家下单时已经冻结了资金，使用 remaining_frozen 检查是否足够
            if best_buy.remaining_frozen < total_buyer_cost:
                # 资金不足，取消买单
                best_buy.close()
                continue

            # 执行债券交易
            # 买家（买单）：借出资金（quote_token），获得债券代币（base_token）
            # 注意：资金已从冻结中扣除，这里只需获得债券代币
            buyer.assets[self.base_token] = buyer.assets.get(self.base_token, D0) + match_volume

            # 卖家（卖单）：借入资金，获得 quote_token，付出债券代币（base_token）
            # 注意：债券代币已从冻结中扣除，这里只需获得 quote_token
            seller.assets[self.quote_token] = (
                seller.assets.get(self.quote_token, D0) + match_volume
            )

            # 更新订单状态
            best_buy.remaining_frozen -= match_volume
            best_sell.remaining_frozen -= match_volume
            best_buy.executed += match_volume
            best_sell.executed += match_volume

            # 记录成交（包含手续费）
            self.log.append((time.time(), match_rate, match_volume, D0, D0))
            self.current_rate = match_rate
            self.update_consensus_rate()

            # 完成订单处理
            if best_buy.remaining_volume <= D0:
                if best_buy in buyer.bond_orders:
                    buyer.bond_orders.remove(best_buy)
                self.buy_orders.remove(best_buy)

            if best_sell.remaining_volume <= D0:
                if best_sell in seller.bond_orders:
                    seller.bond_orders.remove(best_sell)
                self.sell_orders.remove(best_sell)

    def get_order_book(self, depth: int = 10) -> Tuple[List[Tuple[Decimal, Decimal]], List[Tuple[Decimal, Decimal]]]:
        """
        获取债券订单簿快照

        Args:
            depth: 返回的档位深度

        Returns:
            (买单列表, 卖单列表)，每项为 (利率, 数量)
        """
        buys = [(order.interest_rate, order.remaining_volume) for order in self.buy_orders[:depth]]
        sells = [(order.interest_rate, order.remaining_volume) for order in self.sell_orders[:depth]]
        return buys, sells


