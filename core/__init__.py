"""
Finance 核心模块 - 金融市场模拟引擎

提供完整的金融市场模拟功能，包括：
- 代币(Token)管理
- 普通交易对(TradingPair)的订单簿和撮合
- 债券交易对(BondTradingPair)的利息结算和债券交易
- 交易者(Trader)资产和债券持仓管理
- 市场引擎(MarketEngine)统一协调

使用示例:
    from core.finance import get_engine, Token, Trader

    engine = get_engine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)
"""

from .engine import MarketEngine, get_engine, reset_engine
from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .trader import Trader
from .token import Token
from .order import Order, BondOrder
from .corp import Corp
from .liquidation import LiquidationEngine, LiquidationResult

__all__ = [
    # 引擎
    "MarketEngine",
    "get_engine",
    "reset_engine",
    # 交易对
    "TradingPair",
    "BondTradingPair",
    # 实体
    "Trader",
    "Token",
    "Corp",
    # 订单
    "Order",
    "BondOrder",
    # 清算
    "LiquidationEngine",
    "LiquidationResult",
]
