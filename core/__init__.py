"""
Finance 核心模块 - 金融市场模拟引擎

提供完整的金融市场模拟功能，包括：
- 代币(Token)管理
- 普通交易对(TradingPair)的订单簿和撮合
- 债券交易对(BondTradingPair)的利息结算和债券交易
- 交易者(Trader)资产和债券持仓管理
- 市场引擎(MarketEngine)统一协调
- 手续费系统(FeeConfig, FeeCalculator等)

使用示例:
    from core import get_engine, Token, Trader, FeeConfig, FeeDirection

    engine = get_engine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")

    # 创建手续费接收者（如平台账户）
    platform = engine.create_trader("Platform")

    # 设置手续费配置（Maker 0.1%, Taker 0.2%，支付给平台）
    fee_config = FeeConfig(
        maker_rate=0.001,
        taker_rate=0.002,
        fee_recipient=platform
    )
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0, fee_config=fee_config)

    # 按标的代币数量下单
    trader.submit_market_order(pair, "buy", 1.0)  # 买入 1 BTC

    # 按计价代币金额下单
    trader.submit_market_order_by_quote(pair, "buy", 50000.0)  # 花费 50000 USDT
"""

from .engine import MarketEngine, get_engine, reset_engine
from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .trader import Trader
from .token import Token
from .order import Order, BondOrder
from .corp import Corp
from .liquidation import LiquidationEngine, LiquidationResult
from .utils import to_decimal, d, D0, D1

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
    # 工具函数
    "to_decimal",
    "d",
    "D0",
    "D1",
]
