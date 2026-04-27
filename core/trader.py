"""
Trader 模块 - 交易者定义

定义金融市场中的交易者实体，管理：
- 资产持仓 (assets): 各代币的余额
- 债券持仓 (bonds): 各代币的债券头寸（正为债权，负为债务）
- 活跃订单 (orders/bond_orders): 当前挂单列表

支持总资产和净资产的计算（按计价代币换算）。
"""

from typing import Dict, List, Optional, Callable, Tuple, Any, TYPE_CHECKING
from decimal import Decimal

from .token import Token
from .utils import to_decimal, D0, D1
from .engine_node import EngineNode

if TYPE_CHECKING:
    from .trading_pair import TradingPair
    from .bond_pair import BondTradingPair
    from .order import Order, BondOrder


class Trader(EngineNode):
    """
    交易者类 - 金融市场参与者

    交易者持有资产和债券，可以提交订单参与市场交易。
    债券系统允许交易者进行借贷：正债券为借出（收利息），
    负债券为借入（付利息）。

    Attributes:
        name: 交易者名称/标识
        assets: 资产持仓映射 {Token: amount}
        bonds: 债券持仓映射 {Token: amount}（正为债权，负为债务）
        k: 策略参数
        orders: 普通订单列表
        bond_orders: 债券订单列表
        last_bond_calc_time: 上次债券计算时间
        is_player: 是否为玩家控制
        trading_pairs: 可交易的普通交易对列表
        bond_pairs: 可交易的债券交易对列表
        _price_converter: 价格转换函数（由Engine注入）
        _quote_token: 默认计价代币

    Examples:
        >>> trader = Trader("Alice")
        >>> trader.add_asset(btc_token, 10.0)
        >>> total = trader.get_total_assets(usdt_token)
    """

    def __init__(self, name: str):
        """
        创建交易者

        Args:
            name: 交易者名称
        """
        super().__init__(name)
        self.name = name
        self.assets: Dict[Token, Decimal] = {}
        self.bonds: Dict[Token, Decimal] = {}
        self.k = D0
        self.orders: List = []
        self.bond_orders: List = []
        self.last_bond_calc_time: Dict[Token, float] = {}
        self.is_player = False
        self.trading_pairs: List["TradingPair"] = []
        self.bond_pairs: List["BondTradingPair"] = []

        # 价格转换函数，由 Engine 注入
        self._price_converter: Optional[Callable[[Token, Decimal, Optional[Token]], Decimal]] = None
        self._quote_token: Optional[Token] = None

    def add_asset(self, token: Token, amount) -> None:
        """
        添加资产到持仓

        Args:
            token: 代币类型
            amount: 增加数量
        """
        self.assets[token] = self.assets.get(token, D0) + to_decimal(amount)

    def set_price_converter(
        self, converter: Callable[[Token, Decimal, Optional[Token]], Decimal], quote_token: Token
    ) -> None:
        """
        设置价格转换函数，用于计算总资产价值

        Args:
            converter: 价格转换函数，签名 (from_token, amount, target_quote) -> Decimal
            quote_token: 默认计价代币
        """
        self._price_converter = converter
        self._quote_token = quote_token

    def get_total_assets(self, quote_token: Optional[Token] = None) -> Decimal:
        """
        计算总资产价值（以计价代币为单位）

        计算包括：
        - 所有正资产持仓
        - 所有正债券持仓（借出债权）

        Args:
            quote_token: 指定计价代币，默认使用 self._quote_token

        Returns:
            总资产价值（计价代币单位）
        """
        if quote_token is None:
            quote_token = self._quote_token
        if quote_token is None or self._price_converter is None:
            return sum((amount for amount in self.assets.values() if amount > D0), D0)

        total = D0

        # 累加资产
        for token, amount in self.assets.items():
            if amount <= D0:
                continue

            if token == quote_token:
                total += amount
            else:
                total += self._price_converter(token, amount, quote_token)

        # 累加债券债权（正债券）
        for bond_token, bond_amount in self.bonds.items():
            if bond_amount <= D0:
                continue
            if bond_token == quote_token:
                total += bond_amount
            else:
                total += self._price_converter(bond_token, bond_amount, quote_token)

        return total

    def get_effective_bond(self, token: Token) -> Decimal:
        """
        计算有效债券持仓 = bonds持仓 + 订单冻结值

        对于卖单：bonds已预扣负值，加上订单冻结值（正值）相互抵消
        例如：卖出100，bonds=-100，冻结=100，有效债券=0

        对于买单：bonds不变，冻结资金不影响债券计算
        例如：买入100，bonds=0，有效债券=0（成交后才变为100）

        Args:
            token: 债券代币

        Returns:
            有效债券持仓（正为债权，负为债务）
        """
        bond_amt = self.bonds.get(token, D0)

        # 加上卖单中冻结的债券值（抵消预扣的负债券）
        for order in self.bond_orders:
            if order.bond_pair.token == token and order.direction == "sell":
                bond_amt += order.remaining_volume

        return bond_amt

    def get_net_assets(self, quote_token: Optional[Token] = None) -> Decimal:
        """
        计算净资产 = 总资产 - 债券债务

        债券债务指负债券持仓的绝对值，包括订单中预扣的部分。

        Args:
            quote_token: 指定计价代币，默认使用 self._quote_token

        Returns:
            净资产价值（计价代币单位）
        """
        total_assets = self.get_total_assets(quote_token)

        if quote_token is None:
            quote_token = self._quote_token
        if quote_token is None or self._price_converter is None:
            return total_assets

        liabilities = D0

        # 累加债券债务（使用有效债券持仓）
        # 获取所有相关的债券代币
        bond_tokens = set(self.bonds.keys())
        for order in self.bond_orders:
            bond_tokens.add(order.bond_pair.token)

        for bond_token in bond_tokens:
            effective_bond = self.get_effective_bond(bond_token)
            if effective_bond < D0:
                liability_value = abs(effective_bond)
                if bond_token == quote_token:
                    liabilities += liability_value
                else:
                    liabilities += self._price_converter(bond_token, liability_value, quote_token)

        return total_assets - liabilities

    # ====== 交易接口 ======

    def submit_limit_order(self, pair: "TradingPair", direction: str, price, volume) -> bool:
        """
        提交普通限价单

        Args:
            pair: 交易对
            direction: 'buy' 或 'sell'
            price: 限价
            volume: 数量

        Returns:
            是否提交成功（资金/资产充足时成功）
        """
        price = to_decimal(price)
        volume = to_decimal(volume)

        if direction == "buy":
            trade_amount = price * volume
            available = self.assets.get(pair.quote_token, D0)

            if available < trade_amount:
                return False

            try:
                self.assets[pair.quote_token] = available - trade_amount
                pair.submit_limit_order(self, direction, price, volume, trade_amount)
            except ValueError:
                # 返还资金，恢复状态
                self.assets[pair.quote_token] = available
                raise
        else:  # sell
            available = self.assets.get(pair.base_token, D0)
            if available < volume:
                return False
            try:
                self.assets[pair.base_token] = available - volume
                pair.submit_limit_order(self, direction, price, volume, volume)
            except ValueError:
                # 返还资金，恢复状态
                self.assets[pair.base_token] = available
                raise

        return True

    def submit_market_order(self, pair: "TradingPair", direction: str, volume) -> Tuple[Decimal, List[Dict], Decimal]:
        """
        提交普通市价单（按标的代币数量）

        Args:
            pair: 交易对
            direction: 'buy' 或 'sell'
            volume: 标的代币数量

        Returns:
            (实际成交量, 成交明细列表, 总手续费)
        """
        return pair.execute_market_order(self, direction, volume)

    def submit_market_order_by_quote(self, pair: "TradingPair", direction: str, quote_amount) -> Tuple[Decimal, List[Dict], Decimal]:
        """
        提交普通市价单（按计价代币金额）

        买入时：指定花费多少计价代币（如 USDT）
        卖出时：指定期望获得多少计价代币（系统会计算需要卖出的标的代币数量）

        Args:
            pair: 交易对
            direction: 'buy' 或 'sell'
            quote_amount: 计价代币金额

        Returns:
            (实际成交量, 成交明细列表, 总手续费)
        """
        quote_amount = to_decimal(quote_amount)

        if direction == "buy":
            volume = quote_amount / pair.price
        else:
            volume = quote_amount / pair.price

        return pair.execute_market_order(self, direction, volume)

    def submit_bond_limit_order(self, bond_pair: "BondTradingPair", direction: str,
                                 interest_rate, volume) -> bool:
        """
        提交债券限价单

        债券方向说明：
        - 买单（buy）：借出 quote_token，获得 bond_token（债权）
        - 卖单（sell）：借入 quote_token，付出 bond_token（债务）

        Args:
            bond_pair: 债券交易对
            direction: 'buy' 或 'sell'
            interest_rate: 目标利率
            volume: 债券数量

        Returns:
            是否提交成功
        """
        interest_rate = to_decimal(interest_rate)
        volume = to_decimal(volume)

        if direction == "buy":
            # 买单：借出资金，冻结 quote_token
            available = self.assets.get(bond_pair.quote_token, D0)

            if available < volume:
                return False

            self.assets[bond_pair.quote_token] = available - volume
            bond_pair.submit_limit_order(self, direction, interest_rate, volume, volume)
        else:  # sell
            # 卖单：借入资金，冻结 bond_token（债券代币）
            # 允许持仓为负（债务），所以不需要检查余额
            available = self.assets.get(bond_pair.base_token, D0)
            self.assets[bond_pair.base_token] = available - volume
            bond_pair.submit_limit_order(self, direction, interest_rate, volume, volume)

        return True

    def cancel_order(self, order: "Order") -> bool:
        """
        取消订单

        Args:
            order: Order或BondOrder实例

        Returns:
            是否成功取消（不可取消的订单返回False）
        """
        if order and not order.cancelled:
            # 检查订单是否可手动取消
            if hasattr(order, 'cancellable') and not order.cancellable:
                return False
            order.close()
            return True
        return False
