"""
pyMarket - A Python library for market simulation.

This library provides tools for simulating financial markets with trading bots,
bond pairs, and various trading strategies.
"""

__version__ = "0.1.0"
__author__ = "Chaiminit"
__email__ = "zhongc.x@foxmail.com"

from .engine import MarketEngine, get_engine, reset_engine
from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .trader import Trader
from .token import Token
from .order import Order, BondOrder

__all__ = [
    "MarketEngine",
    "get_engine",
    "reset_engine",
    "TradingPair",
    "BondTradingPair",
    "Trader",
    "Token",
    "Order",
    "BondOrder",
]
