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
from typing import List, Dict, Tuple, Optional, Set, Type
from decimal import Decimal

from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .trader import Trader
from .token import Token
from .liquidation import LiquidationEngine, LiquidationResult
from .corp import Corp
from .rmm import ReflexiveMarketMaker
from .utils import to_decimal, D0
from .engine_node import EngineNode


class MarketEngine:
    """
    市场引擎 - 纯协调层

    管理代币、交易对、债券对、交易者的生命周期，
    提供统一的交易接口和市场模拟功能。

    支持自定义类型：可以通过设置 token_class、trading_pair_class 等
    来使用继承自基础类的自定义实现。

    Attributes:
        tokens: 代币映射 {id: Token}
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

    def __init__(
        self,
        token_class: Type[Token] = Token,
        trading_pair_class: Type[TradingPair] = TradingPair,
        bond_trading_pair_class: Type[BondTradingPair] = BondTradingPair,
        trader_class: Type[Trader] = Trader,
        corp_class: Type[Corp] = Corp,
    ):
        """
        初始化市场引擎

        Args:
            token_class: 代币类，必须继承自 Token
            trading_pair_class: 交易对类，必须继承自 TradingPair
            bond_trading_pair_class: 债券交易对类，必须继承自 BondTradingPair
            trader_class: 交易者类，必须继承自 Trader
            corp_class: 股份公司类，必须继承自 Corp
        """
        # 验证类型
        if not issubclass(token_class, Token):
            raise TypeError(f"token_class 必须继承自 Token，收到 {token_class}")
        if not issubclass(trading_pair_class, TradingPair):
            raise TypeError(f"trading_pair_class 必须继承自 TradingPair，收到 {trading_pair_class}")
        if not issubclass(bond_trading_pair_class, BondTradingPair):
            raise TypeError(f"bond_trading_pair_class 必须继承自 BondTradingPair，收到 {bond_trading_pair_class}")
        if not issubclass(trader_class, Trader):
            raise TypeError(f"trader_class 必须继承自 Trader，收到 {trader_class}")
        if not issubclass(corp_class, Corp):
            raise TypeError(f"corp_class 必须继承自 Corp，收到 {corp_class}")

        self._token_class = token_class
        self._trading_pair_class = trading_pair_class
        self._bond_trading_pair_class = bond_trading_pair_class
        self._trader_class = trader_class
        self._corp_class = corp_class

        self.tokens: Dict[Token, str] = {}  # Token -> token_id 的映射
        self._token_by_id: Dict[str, Token] = {}  # token_id -> Token 的映射（用于快速查找）
        self.trading_pairs: List[TradingPair] = []
        self.bond_trading_pairs: List[BondTradingPair] = []
        self.traders: List[Trader] = []
        self._quote_token: Optional[Token] = None
        self._step_counter = 0
        self._last_step_time: Optional[float] = None  # 上次调用 step 的时间戳
        self._liquidation_engine = LiquidationEngine(self)
        self._nodes: Set[EngineNode] = set()  # 所有引擎节点（自动注册）

        # 反射性做市商（全局唯一，所有交易对共用）
        self.rmm = ReflexiveMarketMaker()
        self._nodes.add(self.rmm)
        self.rmm._engine = self

    # ====== 代币管理 ======

    def create_token(self, token_id: str, is_quote: bool = False, **kwargs) -> Token:
        """
        创建新代币

        Args:
            token_id: 代币ID
            is_quote: 是否为计价代币（全局只能有一个）
            **kwargs: 传递给 token_class 的额外参数

        Returns:
            创建的Token实例

        Raises:
            ValueError: 如果计价代币已存在
        """
        token = self._token_class(token_id, is_quote, **kwargs)
        self.tokens[token] = token_id
        self._token_by_id[token_id] = token
        self._nodes.add(token)
        token._engine = self

        if is_quote:
            if self._quote_token is not None:
                raise ValueError(f"全局计价代币已存在: {self._quote_token}")
            self._quote_token = token

        return token

    def register_token(self, token: Token) -> None:
        """
        注册外部创建的代币

        允许从外部传入自定义 Token 实例（必须继承自 Token）

        Args:
            token: Token 实例

        Raises:
            TypeError: 如果 token 不是 Token 或其子类
            ValueError: 如果代币名称已存在
        """
        if not isinstance(token, Token):
            raise TypeError(f"token 必须是 Token 或其子类的实例，收到 {type(token)}")
        if token.token_id in self.tokens:
            raise ValueError(f"代币 {token.token_id} 已存在")

        self.tokens[token] = token.token_id
        self._token_by_id[token.token_id] = token
        self._nodes.add(token)
        token._engine = self

        if token.is_quote:
            if self._quote_token is not None:
                raise ValueError(f"全局计价代币已存在: {self._quote_token}")
            self._quote_token = token

    def get_token(self, token_id: str) -> Optional[Token]:
        """
        通过ID获取代币

        Args:
            token_id: 代币ID

        Returns:
            Token实例或None
        """
        return self._token_by_id.get(token_id)

    def set_quote_token(self, token_id: str) -> None:
        """
        设置计价代币

        Args:
            token_id: 代币ID

        Raises:
            ValueError: 如果代币不存在或计价代币已设置
        """
        if token_id not in self._token_by_id:
            raise ValueError(f"代币 {token_id} 不存在")

        if self._quote_token is not None:
            raise ValueError(f"全局计价代币已存在: {self._quote_token}")
        self._quote_token = self._token_by_id[token_id]

    def get_quote_token(self) -> Optional[Token]:
        """获取当前计价代币"""
        return self._quote_token

    # ====== 普通交易对 ======

    def create_trading_pair(
        self,
        base_token_id: str,
        quote_token_id: str,
        initial_price,
        **kwargs
    ) -> TradingPair:
        """
        创建普通交易对

        Args:
            base_token_id: 基础代币ID
            quote_token_id: 计价代币ID
            initial_price: 初始价格
            **kwargs: 传递给 trading_pair_class 的额外参数

        Returns:
            创建的TradingPair实例

        Raises:
            ValueError: 如果代币不存在
        """
        base_token = self._token_by_id.get(base_token_id)
        quote_token = self._token_by_id.get(quote_token_id)

        if base_token is None:
            raise ValueError(f"基础代币 {base_token_id} 不存在")
        if quote_token is None:
            raise ValueError(f"计价代币 {quote_token_id} 不存在")

        pair = self._trading_pair_class(base_token, quote_token, initial_price, **kwargs)
        self.trading_pairs.append(pair)
        self._nodes.add(pair)
        pair._engine = self
        self.rmm.register_pair(pair)
        return pair

    def register_trading_pair(self, pair: TradingPair) -> None:
        """
        注册外部创建的交易对

        允许从外部传入自定义 TradingPair 实例（必须继承自 TradingPair）

        Args:
            pair: TradingPair 实例

        Raises:
            TypeError: 如果 pair 不是 TradingPair 或其子类
        """
        if not isinstance(pair, TradingPair):
            raise TypeError(f"pair 必须是 TradingPair 或其子类的实例，收到 {type(pair)}")
        self.trading_pairs.append(pair)
        self._nodes.add(pair)
        pair._engine = self
        self.rmm.register_pair(pair)

    # ====== 债券交易对 ======

    def create_bond_trading_pair(
        self,
        token_id: str,
        initial_rate,
        **kwargs
    ) -> BondTradingPair:
        """
        创建债券交易对

        Args:
            token_id: 标的代币ID（如 USDT）
            initial_rate: 初始利率（年化）
            **kwargs: 传递给 bond_trading_pair_class 的额外参数

        Returns:
            创建的BondTradingPair实例

        Raises:
            ValueError: 如果代币不存在
        """
        token = self._token_by_id.get(token_id)
        if token is None:
            raise ValueError(f"代币 {token_id} 不存在")

        bond_token_id = f"BOND_{token_id}"
        bond_pair = self._bond_trading_pair_class(token, bond_token_id, initial_rate, **kwargs)
        self.bond_trading_pairs.append(bond_pair)
        self._nodes.add(bond_pair)
        bond_pair._engine = self

        # 注册债券代币到引擎
        self.tokens[bond_pair.base_token] = bond_token_id
        self._token_by_id[bond_token_id] = bond_pair.base_token

        return bond_pair

    def register_bond_trading_pair(self, bond_pair: BondTradingPair) -> None:
        """
        注册外部创建的债券交易对

        允许从外部传入自定义 BondTradingPair 实例（必须继承自 BondTradingPair）

        Args:
            bond_pair: BondTradingPair 实例

        Raises:
            TypeError: 如果 bond_pair 不是 BondTradingPair 或其子类
        """
        if not isinstance(bond_pair, BondTradingPair):
            raise TypeError(f"bond_pair 必须是 BondTradingPair 或其子类的实例，收到 {type(bond_pair)}")
        self.bond_trading_pairs.append(bond_pair)
        self._nodes.add(bond_pair)
        bond_pair._engine = self

    def add_bond_client(self, bond_pair: BondTradingPair, trader: Trader) -> None:
        """
        添加债券客户

        Args:
            bond_pair: 债券交易对
            trader: 交易者
        """
        bond_pair.clients.add(trader)

    # ====== 交易者管理 ======

    def create_trader(self, name: str, **kwargs) -> Trader:
        """
        创建交易者

        Args:
            name: 交易者名称
            **kwargs: 传递给 trader_class 的额外参数

        Returns:
            创建的Trader实例
        """
        trader = self._trader_class(name, **kwargs)
        self.traders.append(trader)
        self._nodes.add(trader)
        trader._engine = self

        # 设置价格转换器
        if self._quote_token:
            trader.set_price_converter(self._convert_price, self._quote_token)

        return trader

    def register_trader(self, trader: Trader) -> None:
        """
        注册外部创建的交易者

        允许从外部传入自定义 Trader 实例（必须继承自 Trader）

        Args:
            trader: Trader 实例

        Raises:
            TypeError: 如果 trader 不是 Trader 或其子类
        """
        if not isinstance(trader, Trader):
            raise TypeError(f"trader 必须是 Trader 或其子类的实例，收到 {type(trader)}")

        self.traders.append(trader)
        self._nodes.add(trader)
        trader._engine = self

        # 设置价格转换器
        if self._quote_token:
            trader.set_price_converter(self._convert_price, self._quote_token)

    def create_corp(
        self,
        name: str,
        total_shares,
        initial_price,
        quote_token: Token,
        **kwargs
    ) -> Corp:
        """
        创建股份公司

        Args:
            name: 公司名称
            total_shares: 初始发行的总股份数
            initial_price: 初始发行价格
            quote_token: 计价代币
            **kwargs: 传递给 corp_class 的额外参数

        Returns:
            创建的Corp实例
        """
        token_id = len(self.tokens)
        corp = self._corp_class(
            name=name,
            total_shares=total_shares,
            initial_price=initial_price,
            quote_token=quote_token,
            token_id=token_id,
            **kwargs
        )
        self.traders.append(corp)
        self._nodes.add(corp)
        corp._engine = self

        # 注册股份代币到引擎
        share_token_id = f"{token_id}_SHARE"
        self.tokens[corp.share_token] = share_token_id
        self._token_by_id[share_token_id] = corp.share_token

        # 设置价格转换器
        if self._quote_token:
            corp.set_price_converter(self._convert_price, self._quote_token)

        return corp

    def register_corp(self, corp: Corp) -> None:
        """
        注册外部创建的股份公司

        允许从外部传入自定义 Corp 实例（必须继承自 Corp）

        Args:
            corp: Corp 实例

        Raises:
            TypeError: 如果 corp 不是 Corp 或其子类
        """
        if not isinstance(corp, Corp):
            raise TypeError(f"corp 必须是 Corp 或其子类的实例，收到 {type(corp)}")

        self.traders.append(corp)
        self._nodes.add(corp)
        corp._engine = self

        # 注册股份代币到引擎
        share_token_id = f"{corp.token_id}_SHARE"
        self.tokens[corp.share_token] = share_token_id
        self._token_by_id[share_token_id] = corp.share_token

        # 设置价格转换器
        if self._quote_token:
            corp.set_price_converter(self._convert_price, self._quote_token)

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

        通过查找交易对进行价格转换，支持直接交易对和债券代币。

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

        for pair in self.trading_pairs:
            if pair.base_token == from_token and pair.quote_token == target_quote:
                return amount * pair.price
            if pair.base_token == target_quote and pair.quote_token == from_token:
                return amount / pair.price if pair.price > D0 else D0

        for bp in self.bond_trading_pairs:
            if bp.base_token == from_token and bp.quote_token == target_quote:
                return amount

        return D0

    # ====== 市场模拟 ======

    def step(self) -> None:
        """
        执行一步市场模拟

        当前执行的操作：
        - 计算时间步长（秒）
        - 调用所有引擎节点的 step 回调（包括交易对、债券对、交易者等）
        - 结算所有债券交易对的利息
        - 处理破产清算
        """
        self._step_counter += 1

        # 计算距离上次调用的时间（秒）
        current_time = time.time()
        if self._last_step_time is None:
            # 首次调用，不结算利息（dt=0）
            dt = D0
        else:
            # 计算实际经过的时间（秒）
            dt = Decimal(str(current_time - self._last_step_time))
        self._last_step_time = current_time

        # 调用所有引擎节点的 step 回调（使用秒为单位）
        for node in EngineNode.get_all_nodes():
            try:
                node.step(dt)
            except Exception as e:
                print(f"警告: 引擎节点 {node.__repr__()} 的 step 回调失败: {e}")

        # 结算债券利息（使用秒为单位）
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

    def get_all_collected_fees(self) -> Dict[Token, Decimal]:
        """
        获取所有交易对收集的手续费总额

        Returns:
            {Token: 手续费金额}
        """
        return {}

    def run(self, fps: float = 60.0) -> None:
        """
        运行引擎，阻塞当前线程

        以指定的帧率循环执行市场模拟步骤，直到被外部中断

        Args:
            fps: 帧率，每秒执行的步骤数
        """
        frame_duration = 1.0 / fps  # 每帧持续时间（秒）
        
        try:
            while True:
                start_time = time.time()
                self.step()
                end_time = time.time()
                
                # 计算实际执行时间并睡眠剩余时间
                execution_time = end_time - start_time
                sleep_time = frame_duration - execution_time
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
        except KeyboardInterrupt:
            # 捕获 Ctrl+C 中断
            print("引擎运行被用户中断")

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
