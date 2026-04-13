"""
pyMarket - A Python library for market simulation.

This library provides tools for simulating financial markets with trading bots,
bond pairs, and various trading strategies.
"""

__version__ = "0.1.0"
__author__ = "Chaiminit"
__email__ = "your.email@example.com"

from core.engine import MarketEngine
from core.trading_pair import TradingPair
from core.bond_pair import BondTradingPair
from core.trader import Trader
from core.bot import Bot, BotManager
from market_framework import Market, MarketConfig

__all__ = [
    "MarketEngine",
    "TradingPair",
    "BondTradingPair",
    "Trader",
    "Bot",
    "BotManager",
    "Market",
    "MarketConfig",
]
