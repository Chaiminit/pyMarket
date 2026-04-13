"""
Engine 模块 - 市场引擎

市场引擎是金融模拟系统的协调中心，负责：
- 代币生命周期管理
- 交易对（普通/债券）创建和管理
- 交易者创建和资产配置
- 价格转换服务
- 市场模拟步进（利息结算等）
- 统一交易接口

使用单例模式提供全局引擎实例。
"""

import time
import math
import random
from typing import List, Dict, Tuple, Optional, Set

from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .trader import Trader
from .token import Token
from .liquidation import LiquidationEngine, LiquidationResult


class MarketEngine:
    """
    市场引擎 - 纯协调层

    管理代币、交易对、债券对、交易者的生命周期，
    提供统一的交易接口和市场模拟功能。

    Attributes:
        tokens: 代币映射 {name: Token}
        trading_pairs: 普通交易对列表
        bond_trading_pairs: 债券交易对列表
        traders: 交易者列表
        _quote_token: 全局计价代币
        _step_counter: 模拟步数计数器

    Examples:
        >>> engine = MarketEngine()
        >>> usdt = engine.create_token("USDT", is_quote=True)
        >>> btc = engine.create_token("BTC")
        >>> pair = engine.create_trading_pair("BTC", "USDT", 50000.0)
        >>> trader = engine.create_trader("Alice")
        >>> engine.allocate_assets(trader, "BTC", 10.0)
    """

    def __init__(self):
        """初始化市场引擎"""
        self.tokens: Dict[str, Token] = {}
        self.trading_pairs: List[TradingPair] = []
        self.bond_trading_pairs: List[BondTradingPair] = []
        self.traders: List[Trader] = []
        self._quote_token: Optional[Token] = None
        self._step_counter = 0
        self._liquidation_engine = LiquidationEngine(self)

    # ====== 代币管理 ======

    def create_token(self, name: str, is_quote: bool = False) -> Token:
        """
        创建新代币

        Args:
            name: 代币名称
            is_quote: 是否为计价代币（全局只能有一个）

        Returns:
            创建的Token实例

        Raises:
            ValueError: 如果计价代币已存在
        """
        token_id = len(self.tokens)
        token = Token(name, token_id, is_quote)
        self.tokens[name] = token

        if is_quote:
            if self._quote_token is not None:
                raise ValueError(f"全局计价代币已存在: {self._quote_token}")
            self._quote_token = token

        return token

    def get_token(self, name: str) -> Optional[Token]:
        """
        通过名称获取代币

        Args:
            name: 代币名称

        Returns:
            Token实例或None
        """
        return self.tokens.get(name)

    def set_quote_token(self, name: str) -> None:
        """
        设置计价代币

        Args:
            name: 代币名称

        Raises:
            ValueError: 如果代币不存在或计价代币已设置
        """
        if name not in self.tokens:
            raise ValueError(f"代币 {name} 不存在")
        if self._quote_token is not None:
            raise ValueError(f"全局计价代币已存在: {self._quote_token}")
        self._quote_token = self.tokens[name]

    def get_quote_token(self) -> Optional[Token]:
        """获取当前计价代币"""
        return self._quote_token

    # ====== 普通交易对 ======

    def create_trading_pair(self, base_token_name: str, quote_token_name: str, initial_price: float) -> TradingPair:
        """
        创建普通交易对

        Args:
            base_token_name: 基础代币名称
            quote_token_name: 计价代币名称
            initial_price: 初始价格

        Returns:
            创建的TradingPair实例

        Raises:
            ValueError: 如果代币不存在
        """
        base_token = self.tokens.get(base_token_name)
        quote_token = self.tokens.get(quote_token_name)

        if base_token is None:
            raise ValueError(f"基础代币 {base_token_name} 不存在")
        if quote_token is None:
            raise ValueError(f"计价代币 {quote_token_name} 不存在")

        pair = TradingPair(base_token, quote_token, initial_price)
        self.trading_pairs.append(pair)
        return pair

    # ====== 债券交易对 ======

    def create_bond_trading_pair(self, token_name: str, initial_rate: float) -> BondTradingPair:
        """
        创建债券交易对

        Args:
            token_name: 标的代币名称
            initial_rate: 初始利率（年化）

        Returns:
            创建的BondTradingPair实例

        Raises:
            ValueError: 如果代币不存在
        """
        token = self.tokens.get(token_name)
        if token is None:
            raise ValueError(f"代币 {token_name} 不存在")

        bond_pair = BondTradingPair(token, initial_rate)
        self.bond_trading_pairs.append(bond_pair)
        return bond_pair

    def add_bond_client(self, bond_pair: BondTradingPair, trader: Trader) -> None:
        """
        添加债券客户

        Args:
            bond_pair: 债券交易对
            trader: 交易者
        """
        bond_pair.clients.add(trader)

    # ====== 交易者管理 ======

    def create_trader(self, name: str) -> Trader:
        """
        创建交易者

        Args:
            name: 交易者名称

        Returns:
            创建的Trader实例
        """
        trader = Trader(name)
        self.traders.append(trader)

        # 设置价格转换器
        if self._quote_token:
            trader.set_price_converter(self._convert_price, self._quote_token)

        return trader

    def allocate_assets(self, trader: Trader, token_name: str, amount: float) -> None:
        """
        分配资产给交易者

        Args:
            trader: 交易者
            token_name: 代币名称
            amount: 数量
        """
        token = self.tokens.get(token_name)
        if token:
            trader.add_asset(token, amount)

    def set_trader_pairs(self, trader: Trader, pairs: List[TradingPair]) -> None:
        """设置交易者的普通交易对列表"""
        trader.trading_pairs = pairs

    def set_trader_bond_pairs(self, trader: Trader, bond_pairs: List[BondTradingPair]) -> None:
        """设置交易者的债券交易对列表"""
        trader.bond_pairs = bond_pairs

    # ====== 价格转换 ======

    def _convert_price(self, from_token: Token, amount: float, target_quote: Optional[Token] = None) -> float:
        """
        转换代币价格到目标计价代币

        通过查找交易对进行价格转换，支持直接交易对。

        Args:
            from_token: 源代币
            amount: 数量
            target_quote: 目标计价代币，默认使用全局计价代币

        Returns:
            转换后的价值，无法转换返回0
        """
        if target_quote is None:
            target_quote = self._quote_token

        if from_token == target_quote:
            return amount

        # 查找直接交易对
        for pair in self.trading_pairs:
            if pair.base_token == from_token and pair.quote_token == target_quote:
                return amount * pair.price
            if pair.base_token == target_quote and pair.quote_token == from_token:
                return amount / pair.price if pair.price > 0 else 0

        # 无法直接转换
        return 0

    # ====== 市场模拟 ======

    def step(self) -> None:
        """
        执行一步市场模拟

        当前执行的操作：
        - 结算所有债券交易对的利息
        - 处理破产清算
        """
        self._step_counter += 1

        # 结算债券利息
        if self.traders and self.bond_trading_pairs:
            traders_set = set(self.traders)

            for bp in self.bond_trading_pairs:
                bp.settle_interest_simple(traders_set, 0.1)

        # 处理破产清算
        self.process_liquidations()

    # ====== 破产清算 ======

    def check_solvency(self, trader: Trader) -> bool:
        """
        检查交易者是否资不抵债

        Args:
            trader: 待检查的交易者

        Returns:
            True 表示有偿付能力，False 表示资不抵债
        """
        return self._liquidation_engine.check_solvency(trader, self._quote_token)

    def liquidate_trader(self, trader: Trader) -> LiquidationResult:
        """
        执行单个交易者的破产清算

        Args:
            trader: 破产的交易者

        Returns:
            清算结果
        """
        return self._liquidation_engine.liquidate_trader(trader, self._convert_price)

    def process_liquidations(self) -> List[LiquidationResult]:
        """
        处理所有资不抵债交易者的清算

        Returns:
            所有清算结果列表
        """
        return self._liquidation_engine.process_all_liquidations(self._convert_price)

    def get_insolvent_traders(self) -> List[Trader]:
        """
        获取所有资不抵债的交易者

        Returns:
            资不抵债的交易者列表
        """
        return self._liquidation_engine.get_insolvent_traders()

    def get_liquidation_history(self) -> List[LiquidationResult]:
        """
        获取清算历史记录

        Returns:
            清算结果列表
        """
        return self._liquidation_engine.liquidation_history

# 全局引擎实例（单例模式）
_engine_instance: Optional[MarketEngine] = None


def get_engine() -> MarketEngine:
    """
    获取全局引擎实例（单例模式）

    Returns:
        全局MarketEngine实例
    """
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = MarketEngine()
    return _engine_instance


def reset_engine() -> None:
    """重置全局引擎实例（创建新实例）"""
    global _engine_instance
    _engine_instance = MarketEngine()
