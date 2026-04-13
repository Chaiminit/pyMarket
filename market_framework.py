"""
市场模拟框架 - 简化版
"""

import time
import threading
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

from core.engine import MarketEngine, get_engine
from core.bot import BotManager


@dataclass
class MarketConfig:
    """市场配置 - 所有参数集中在这里"""

    name: str = "市场模拟"
    quote_token: str = "USDT"
    tokens: List[str] = field(default_factory=lambda: ["USDT", "ETH"])
    trading_pairs: List[Tuple[str, str, float]] = field(
        default_factory=lambda: [("ETH", "USDT", 190.0)]
    )
    bond_pairs: List[Tuple[str, float]] = field(default_factory=lambda: [("USDT", 0.0005)])
    bot_count: int = 100
    bot_trend: float = 10.0
    bot_view: float = 5.0
    bot_assets: Dict[str, Tuple[float, float]] = field(
        default_factory=lambda: {
            "USDT": (1000.0, 100000.0),
            "ETH": (2.0, 200.0),
            "BTC": (0.01, 2.0),
        }
    )
    step_interval: float = 0.2
    enable_gui: bool = True


class Market:
    """市场类 - 简化版"""

    def __init__(self, config: MarketConfig = None):
        self.config = config or MarketConfig()
        self.engine = get_engine()
        self._stop_event = threading.Event()
        self._sim_thread = None
        self._is_running = False

        self._init_market()

    def _init_market(self):
        cfg = self.config

        for i, token_name in enumerate(cfg.tokens):
            is_quote = token_name == cfg.quote_token
            self.engine.create_token(token_name, is_quote=is_quote)

        for base, quote, price in cfg.trading_pairs:
            self.engine.create_trading_pair(base, quote, price)

        for token_name, initial_rate in cfg.bond_pairs:
            self.engine.create_bond_trading_pair(token_name, initial_rate)

        # 创建机器人
        bot_ids = self.engine.create_bots_batch(
            count=cfg.bot_count,
            asset_configs=cfg.bot_assets,
            name_prefix="Bot",
            trend=cfg.bot_trend,
            view=cfg.bot_view,
        )

        pair_ids = list(range(len(cfg.trading_pairs)))
        for bot_id in bot_ids:
            self.engine.set_bot_trading_pairs(bot_id, pair_ids)
            if self.engine.bond_trading_pairs:
                bond_pair_ids = list(self.engine.bond_trading_pairs.keys())
                self.engine.set_bot_bond_pairs(bot_id, bond_pair_ids)

        print(f"【{cfg.name}】初始化完成")
        print(f"  代币：{cfg.tokens}")
        print(f"  交易对：{len(cfg.trading_pairs)}个")
        print(f"  债券：{len(cfg.bond_pairs)}个")
        print(f"  机器人：{cfg.bot_count}个")

    def _simulation_loop(self):
        while not self._stop_event.is_set():
            self.engine.step()
            time.sleep(self.config.step_interval)

    def run(self, enable_trader: bool = False):
        self._stop_event.clear()
        self._sim_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._sim_thread.start()
        self._is_running = True

        time.sleep(0.5)

        if enable_trader:
            try:
                from gui.trader_gui import start_trader_gui

                start_trader_gui(self.engine)
            except Exception as e:
                print(f"交易者 GUI 错误：{e}")
            finally:
                self.stop()
        elif self.config.enable_gui:
            try:
                from gui.charts import start_gui

                all_pairs = list(self.engine.trading_pairs.values()) + list(
                    self.engine.bond_trading_pairs.values()
                )
                start_gui(all_pairs, window_title=self.config.name)
            except Exception as e:
                print(f"GUI 错误：{e}")
            finally:
                self.stop()
        else:
            try:
                while self._is_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()

    def stop(self):
        self._stop_event.set()
        if self._sim_thread:
            self._sim_thread.join(timeout=1.0)
        self._is_running = False


def create_market(
    name: str = "市场模拟",
    tokens: List[str] = None,
    pairs: List[Tuple[str, str, float]] = None,
    bonds: List[Tuple[str, float]] = None,
    bots: int = 100,
) -> Market:
    cfg = MarketConfig()
    cfg.name = name
    if tokens:
        cfg.tokens = tokens
    if pairs:
        cfg.trading_pairs = pairs
    if bonds:
        cfg.bond_pairs = bonds
    if bots:
        cfg.bot_count = bots

    return Market(cfg)


def quick_start():
    """一键启动默认市场"""
    Market().run()
