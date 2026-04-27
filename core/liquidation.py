"""
Liquidation 模块 - 破产清算系统

提供金融市场中的破产处理功能：
- 资不抵债检测
- 债券清算（参考 bond_pair.py 实现）
- 资产拍卖
- 坏账核销

清算流程：
1. 检测交易者净资产是否为负
2. 取消所有未完成的订单
3. 清算债券持仓（债务用资产偿还，债权按比例豁免债务人）
4. 剩余资产按比例分配给债权人
5. 记录坏账
"""

from typing import Dict, List, Tuple, Optional, Callable, TYPE_CHECKING
from dataclasses import dataclass
from decimal import Decimal

from .utils import to_decimal, D0

if TYPE_CHECKING:
    from .trader import Trader
    from .token import Token
    from .bond_pair import BondTradingPair
    from .trading_pair import TradingPair


@dataclass
class LiquidationResult:
    """清算结果"""
    trader_name: str
    total_assets: Decimal  # 总资产价值
    total_liabilities: Decimal  # 总负债价值
    shortfall: Decimal  # 资金缺口（坏账）
    creditors_paid: Dict["Trader", Decimal]  # 各债权人获得的偿付
    bad_debt_written_off: Decimal  # 核销的坏账


class LiquidationEngine:
    """
    清算引擎 - 处理交易者破产

    当交易者净资产为负时触发清算流程：
    1. 取消所有挂单
    2. 清算所有债券持仓
    3. 按比例偿付债权人
    4. 记录坏账

    Attributes:
        engine: 市场引擎引用
        liquidation_history: 清算历史记录
    """

    def __init__(self, engine):
        """
        创建清算引擎

        Args:
            engine: 市场引擎实例
        """
        self.engine = engine
        self.liquidation_history: List[LiquidationResult] = []

    def check_solvency(self, trader: "Trader", quote_token: Optional["Token"] = None) -> bool:
        """
        检查交易者是否资不抵债

        Args:
            trader: 待检查的交易者
            quote_token: 计价代币

        Returns:
            True 表示 solvent（有偿付能力），False 表示资不抵债
        """
        net_assets = trader.get_net_assets(quote_token)
        return net_assets >= D0

    def liquidate_trader(
        self,
        trader: "Trader",
        price_oracle: Optional[Callable[["Token", Decimal, Optional["Token"]], Decimal]] = None
    ) -> LiquidationResult:
        """
        执行交易者破产清算

        清算流程：
        1. 取消所有普通订单和债券订单
        2. 清算所有债券持仓
        3. 按比例偿付债权人
        4. 清空交易者资产和债券

        Args:
            trader: 破产的交易者
            price_oracle: 价格查询函数 (from_token, amount, to_token) -> value

        Returns:
            清算结果
        """
        quote_token = self.engine.get_quote_token()

        # 1. 计算破产前的资产和负债
        total_assets = trader.get_total_assets(quote_token)
        total_liabilities = total_assets - trader.get_net_assets(quote_token)

        # 2. 取消所有订单
        self._cancel_all_orders(trader)

        # 3. 清算所有债券持仓
        total_bad_debt = D0
        creditors_paid: Dict[str, Decimal] = {}

        for bond_pair in self.engine.bond_trading_pairs:
            used_assets, bad_debt = self._liquidate_bonds_for_pair(
                trader, bond_pair, price_oracle
            )
            total_bad_debt += bad_debt

        # 4. 清算剩余资产（如果有的话）
        remaining_assets = self._distribute_remaining_assets(trader, creditors_paid)

        # 5. 清空交易者
        self._clear_trader(trader)

        # 6. 记录清算结果
        result = LiquidationResult(
            trader_name=trader.name,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            shortfall=max(D0, total_liabilities - total_assets),
            creditors_paid=creditors_paid,
            bad_debt_written_off=total_bad_debt
        )
        self.liquidation_history.append(result)

        return result

    def _cancel_all_orders(self, trader: "Trader") -> None:
        """取消交易者的所有订单"""
        # 取消所有订单（包括普通订单和债券订单）
        for order in list(trader.orders):
            # 债券订单使用 close()，普通订单使用 close(force=True)
            if hasattr(order, 'bond_pair'):
                order.close()
            else:
                order.close(force=True)

    def _liquidate_bonds_for_pair(
        self,
        trader: "Trader",
        bond_pair: "BondTradingPair",
        price_oracle: Optional[Callable[["Token", "Token"], Decimal]]
    ) -> Tuple[Decimal, Decimal]:
        """
        清算特定债券交易对的持仓

        参考 bond_pair.py 的 liquidate_bonds 实现：
        1. 负债券（债务）：用资产补偿债权人
        2. 正债券（债权）：按比例豁免对应债务人的债务

        Args:
            trader: 破产交易者
            bond_pair: 债券交易对
            price_oracle: 价格查询函数

        Returns:
            (实际使用的资产价值, 未偿还的坏账)
        """
        base_token = bond_pair.base_token  # 债券代币
        quote_token = bond_pair.quote_token  # 标的代币
        bankrupt_id = id(trader)

        # 计算有效债券持仓（持仓 + 冻结）
        # 债券持仓现在在 assets 中，正数为债权，负数为债务
        b_amt = trader.assets.get(base_token, D0)
        frozen_sell = sum(
            o.remaining_volume for o in bond_pair.sell_orders
            if id(o.trader) == bankrupt_id
        )
        effective = b_amt + frozen_sell

        debt_amount = -min(effective, D0)  # 负债券 = 债务
        credit_amount = max(effective, D0)  # 正债券 = 债权

        if debt_amount <= D0 and credit_amount <= D0:
            return D0, D0

        # 计算破产者的总资产价值（以标的代币计价）
        total_asset_value = self._calculate_asset_value_in_token(
            trader, quote_token, price_oracle
        )

        used_token = D0
        bad_debt = D0

        # 处理债务：用资产补偿债权人
        if debt_amount > D0:
            used_token, bad_debt = self._compensate_creditors(
                trader, bond_pair, debt_amount, total_asset_value
            )

        # 处理债权：按比例豁免债务人的债务
        if credit_amount > D0:
            self._forgive_debtors(trader, bond_pair, credit_amount)

        # 清空破产者的债券持仓
        trader.assets[base_token] = D0

        return used_token, bad_debt

    def _calculate_asset_value_in_token(
        self,
        trader: "Trader",
        target_token: "Token",
        price_oracle: Optional[Callable[["Token", Decimal, Optional["Token"]], Decimal]]
    ) -> Decimal:
        """计算交易者总资产价值（以目标代币计价）"""
        total_value = D0

        for token, amount in trader.assets.items():
            if amount <= D0:
                continue

            if token == target_token:
                total_value += amount
            elif price_oracle:
                # price_oracle 签名: (from_token, amount, to_token) -> value
                value = price_oracle(token, amount, target_token)
                if value > D0:
                    total_value += value

        return total_value

    def _compensate_creditors(
        self,
        bankrupt_trader: "Trader",
        bond_pair: "BondTradingPair",
        debt_amount: Decimal,
        available_assets: Decimal
    ) -> Tuple[Decimal, Decimal]:
        """
        用资产补偿债权人

        Args:
            bankrupt_trader: 破产交易者
            bond_pair: 债券交易对
            debt_amount: 债务总额
            available_assets: 可用资产

        Returns:
            (实际支付的资产, 坏账金额)
        """
        base_token = bond_pair.base_token  # 债券代币
        quote_token = bond_pair.quote_token  # 标的代币
        bankrupt_id = id(bankrupt_trader)

        # 收集所有债权人
        creditors: List[Tuple["Trader", Decimal, Decimal]] = []  # (trader, bond_amt, effective)
        total_credit = D0

        for trader in self.engine.traders:
            if id(trader) == bankrupt_id:
                continue

            # 从 assets 中获取债券持仓
            b_amt = trader.assets.get(base_token, D0)
            frozen_sell = sum(
                o.remaining_volume for o in bond_pair.sell_orders
                if id(o.trader) == id(trader)
            )
            effective = b_amt + frozen_sell

            if effective > D0:
                creditors.append((trader, b_amt, effective))
                total_credit += effective

        if total_credit <= D0 or not creditors:
            return D0, debt_amount  # 没有债权人，全部成为坏账

        # 实际可支付的金额（以 quote_token 计价）
        actual_pay = min(available_assets, debt_amount)
        bad_debt = max(debt_amount - actual_pay, D0)

        # 按比例支付给债权人（使用 quote_token）
        for creditor, b_amt, effective in creditors:
            ratio = effective / total_credit
            creditor_payment = actual_pay * ratio

            if creditor_payment > D0:
                creditor.assets[quote_token] = creditor.assets.get(quote_token, D0) + creditor_payment

            # 核销部分债权（对应坏账部分）- 从 assets 中扣除债券代币
            if bad_debt > D0:
                write_off_ratio = min(debt_amount / total_credit, Decimal('1.0'))
                write_off = effective * write_off_ratio

                # 先核销持仓债券
                bond_write_off = min(write_off, max(b_amt, D0))
                frozen_write_off = write_off - bond_write_off

                if bond_write_off > D0:
                    creditor.assets[base_token] = b_amt - bond_write_off

                # 再核销冻结的卖单债券
                if frozen_write_off > D0:
                    for order in bond_pair.sell_orders:
                        if id(order.trader) == id(creditor):
                            order_reduce = min(frozen_write_off, order.remaining_volume)
                            if order_reduce > D0:
                                order.volume -= order_reduce
                                order.remaining_frozen -= order_reduce
                                frozen_write_off -= order_reduce
                                if order.remaining_volume <= D0:
                                    order.close()

        return actual_pay, bad_debt

    def _forgive_debtors(
        self,
        bankrupt_trader: "Trader",
        bond_pair: "BondTradingPair",
        credit_amount: Decimal
    ) -> None:
        """
        按比例豁免债务人的债务

        当破产者持有正债券（是债权人）时，其债权无法收回，
        对应需要豁免相应债务人的债务。

        Args:
            bankrupt_trader: 破产交易者
            bond_pair: 债券交易对
            credit_amount: 债权金额
        """
        base_token = bond_pair.base_token  # 债券代币
        bankrupt_id = id(bankrupt_trader)

        # 收集所有债务人
        debtors: List[Tuple["Trader", Decimal, Decimal]] = []  # (trader, bond_amt, debt_val)
        total_debt = D0

        for trader in self.engine.traders:
            if id(trader) == bankrupt_id:
                continue

            # 从 assets 中获取债券持仓
            b_amt = trader.assets.get(base_token, D0)
            frozen_sell = sum(
                o.remaining_volume for o in bond_pair.sell_orders
                if id(o.trader) == id(trader)
            )
            effective = b_amt + frozen_sell

            if effective < D0:
                debtors.append((trader, b_amt, -effective))
                total_debt += -effective

        if total_debt <= D0 or not debtors:
            return

        # 按比例豁免债务（增加负持仓 = 减少债务）
        forgive_ratio = min(credit_amount / total_debt, Decimal('1.0'))

        for debtor, b_amt, debt_val in debtors:
            forgive = min(debt_val * forgive_ratio, debt_val)
            # 负持仓增加 = 债务减少（豁免）
            debtor.assets[base_token] = b_amt - forgive

    def _distribute_remaining_assets(
        self,
        trader: "Trader",
        creditors_paid: Dict["Trader", Decimal]
    ) -> Decimal:
        """
        分配剩余资产给债权人

        Args:
            trader: 破产交易者
            creditors_paid: 已记录的偿付记录

        Returns:
            分配的剩余资产价值
        """
        # 简化处理：剩余资产留在交易者账户中
        # 实际系统中可能会拍卖资产
        return sum(trader.assets.values(), D0)

    def _clear_trader(self, trader: "Trader") -> None:
        """清空交易者的所有持仓"""
        # 清空资产
        trader.assets.clear()

        # 清空债券
        trader.bonds.clear()

        # 清空订单列表
        trader.orders.clear()

    def get_insolvent_traders(self) -> List["Trader"]:
        """
        获取所有资不抵债的交易者

        Returns:
            资不抵债的交易者列表
        """
        quote_token = self.engine.get_quote_token()
        insolvent = []

        for trader in self.engine.traders:
            if not self.check_solvency(trader, quote_token):
                insolvent.append(trader)

        return insolvent

    def process_all_liquidations(
        self,
        price_oracle: Optional[Callable[["Token", Decimal, Optional["Token"]], Decimal]] = None
    ) -> List[LiquidationResult]:
        """
        处理所有资不抵债交易者的清算

        Args:
            price_oracle: 价格查询函数

        Returns:
            所有清算结果列表
        """
        results = []
        insolvent = self.get_insolvent_traders()

        for trader in insolvent:
            result = self.liquidate_trader(trader, price_oracle)
            results.append(result)

        return results
