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
from decimal import Decimal

from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .trader import Trader
from .token import Token
from .liquidation import LiquidationEngine, LiquidationResult
from .fees import FeeConfig
from .corp import Corp
from .utils import to_decimal, D0


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
        self.tokens: Dict[Token, str] = {}  # Token -> name 的映射
        self._token_by_name: Dict[str, Token] = {}  # name -> Token 的映射（用于快速查找）
        self.trading_pairs: List[TradingPair] = []
        self.bond_trading_pairs: List[BondTradingPair] = []
        self.traders: List[Trader] = []
        self._quote_token: Optional[Token] = None
        self._step_counter = 0
        self._last_step_time: Optional[float] = None  # 上次调用 step 的时间戳
        self._liquidation_engine = LiquidationEngine(self)

        # 全局默认手续费配置（默认为零手续费）
        self._default_trading_fee_config: FeeConfig = FeeConfig()
        self._default_bond_fee_config: FeeConfig = FeeConfig()

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
        self.tokens[token] = name
        self._token_by_name[name] = token

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
        return self._token_by_name.get(name)

    def set_quote_token(self, name: str) -> None:
        """
        设置计价代币

        Args:
            name: 代币名称

        Raises:
            ValueError: 如果代币不存在或计价代币已设置
        """
        if name not in self._token_by_name:
            raise ValueError(f"代币 {name} 不存在")
        if self._quote_token is not None:
            raise ValueError(f"全局计价代币已存在: {self._quote_token}")
        self._quote_token = self._token_by_name[name]

    def get_quote_token(self) -> Optional[Token]:
        """获取当前计价代币"""
        return self._quote_token

    # ====== 普通交易对 ======

    def create_trading_pair(self, base_token_name: str, quote_token_name: str, initial_price, fee_config: Optional[FeeConfig] = None) -> TradingPair:
        """
        创建普通交易对

        Args:
            base_token_name: 基础代币名称
            quote_token_name: 计价代币名称
            initial_price: 初始价格
            fee_config: 手续费配置，None 则使用全局默认配置

        Returns:
            创建的TradingPair实例

        Raises:
            ValueError: 如果代币不存在
        """
        base_token = self._token_by_name.get(base_token_name)
        quote_token = self._token_by_name.get(quote_token_name)

        if base_token is None:
            raise ValueError(f"基础代币 {base_token_name} 不存在")
        if quote_token is None:
            raise ValueError(f"计价代币 {quote_token_name} 不存在")

        # 使用传入的配置或全局默认配置
        config = fee_config if fee_config is not None else self._default_trading_fee_config
        pair = TradingPair(base_token, quote_token, initial_price, config)
        self.trading_pairs.append(pair)
        return pair

    # ====== 债券交易对 ======

    def create_bond_trading_pair(self, token_name: str, initial_rate, fee_config: Optional[FeeConfig] = None) -> BondTradingPair:
        """
        创建债券交易对

        Args:
            token_name: 标的代币名称
            initial_rate: 初始利率（年化）
            fee_config: 手续费配置，None 则使用全局默认配置

        Returns:
            创建的BondTradingPair实例

        Raises:
            ValueError: 如果代币不存在
        """
        token = self._token_by_name.get(token_name)
        if token is None:
            raise ValueError(f"代币 {token_name} 不存在")

        # 使用传入的配置或全局默认配置
        config = fee_config if fee_config is not None else self._default_bond_fee_config
        bond_pair = BondTradingPair(token, initial_rate, config)
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

    def create_corp(
        self,
        name: str,
        total_shares,
        initial_price,
        quote_token: Token
    ) -> Corp:
        """
        创建股份公司

        Args:
            name: 公司名称
            total_shares: 初始发行的总股份数
            initial_price: 初始发行价格
            quote_token: 计价代币

        Returns:
            创建的Corp实例
        """
        token_id = len(self.tokens)
        corp = Corp(
            name=name,
            total_shares=total_shares,
            initial_price=initial_price,
            quote_token=quote_token,
            token_id=token_id
        )
        self.traders.append(corp)

        # 注册股份代币到引擎
        share_token_name = f"{name}_SHARE"
        self.tokens[corp.share_token] = share_token_name
        self._token_by_name[share_token_name] = corp.share_token

        # 设置价格转换器
        if self._quote_token:
            corp.set_price_converter(self._convert_price, self._quote_token)

        return corp

    def allocate_assets(self, trader: Trader, token: Token, amount) -> None:
        """
        分配资产给交易者

        Args:
            trader: 交易者
            token: Token 对象
            amount: 数量
        """
        trader.add_asset(token, amount)

    def set_trader_pairs(self, trader: Trader, pairs: List[TradingPair]) -> None:
        """设置交易者的普通交易对列表"""
        trader.trading_pairs = pairs

    def set_trader_bond_pairs(self, trader: Trader, bond_pairs: List[BondTradingPair]) -> None:
        """设置交易者的债券交易对列表"""
        trader.bond_pairs = bond_pairs

    # ====== 价格转换 ======

    def _convert_price(self, from_token: Token, amount, target_quote: Optional[Token] = None) -> Decimal:
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
        amount = to_decimal(amount)

        if target_quote is None:
            target_quote = self._quote_token

        if from_token == target_quote:
            return amount

        # 查找直接交易对
        for pair in self.trading_pairs:
            if pair.base_token == from_token and pair.quote_token == target_quote:
                return amount * pair.price
            if pair.base_token == target_quote and pair.quote_token == from_token:
                return amount / pair.price if pair.price > D0 else D0

        # 无法直接转换
        return D0

    # ====== 市场模拟 ======

    def step(self) -> None:
        """
        执行一步市场模拟

        当前执行的操作：
        - 结算所有债券交易对的利息（基于距离上次调用的时间）
        - 处理破产清算
        """
        self._step_counter += 1

        # 计算距离上次调用的时间（年化单位）
        current_time = time.time()
        if self._last_step_time is None:
            # 首次调用，不结算利息（dt=0）
            dt = D0
        else:
            # 计算实际经过的时间（秒）并转换为年化
            elapsed_seconds = current_time - self._last_step_time
            dt = Decimal(str(elapsed_seconds)) / Decimal('31536000')  # 365天 = 31536000秒
        self._last_step_time = current_time

        # 结算债券利息
        if self.traders and self.bond_trading_pairs:
            traders_set = set(self.traders)

            for bp in self.bond_trading_pairs:
                bp.settle_interest_simple(traders_set, dt)

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

    # ====== 手续费配置 ======

    def set_default_trading_fee_config(self, fee_config: FeeConfig) -> None:
        """
        设置默认现货交易手续费配置

        Args:
            fee_config: 手续费配置
        """
        self._default_trading_fee_config = fee_config

    def set_default_bond_fee_config(self, fee_config: FeeConfig) -> None:
        """
        设置默认债券交易手续费配置

        Args:
            fee_config: 手续费配置
        """
        self._default_bond_fee_config = fee_config

    def get_default_trading_fee_config(self) -> FeeConfig:
        """
        获取默认现货交易手续费配置

        Returns:
            默认手续费配置
        """
        return self._default_trading_fee_config

    def get_default_bond_fee_config(self) -> FeeConfig:
        """
        获取默认债券交易手续费配置

        Returns:
            默认手续费配置
        """
        return self._default_bond_fee_config

    def get_all_collected_fees(self) -> Dict[Token, Decimal]:
        """
        获取所有交易对收集的手续费总额

        Returns:
            {Token: 手续费金额}
        """
        total_fees: Dict[Token, Decimal] = {}

        # 汇总现货交易手续费
        for pair in self.trading_pairs:
            fees = pair.get_collected_fees()
            if isinstance(fees, dict):
                for token, amount in fees.items():
                    total_fees[token] = total_fees.get(token, D0) + amount

        # 汇总债券交易手续费
        for bond_pair in self.bond_trading_pairs:
            fees = bond_pair.get_collected_fees()
            if isinstance(fees, dict):
                for token, amount in fees.items():
                    total_fees[token] = total_fees.get(token, D0) + amount

        return total_fees

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
