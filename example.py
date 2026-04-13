#!/usr/bin/env python3
"""
市场模拟系统 - 交易者模式

直接运行：python example.py
"""

import threading
import time
from market_framework import Market, MarketConfig


def main():
    """交易者模式 - 你可以扮演交易者参与市场"""
    cfg = MarketConfig()
    cfg.name = "交易者市场"
    cfg.tokens = ["USDT", "ETH", "BTC"]
    cfg.quote_token = "USDT"
    cfg.trading_pairs = [("ETH", "USDT", 1.0)]
    cfg.bond_pairs = [("USDT", 0.00001), ("ETH", 0.001)]
    cfg.bot_count = 500
    cfg.enable_gui = False
    cfg.step_interval = 0.1

    market = Market(cfg)

    # 使用 create_player 而不是 create_bot
    player_id = market.engine.create_player("Player")
    market.engine.allocate_assets_to_bot(player_id, "USDT", 100000.0)
    pair_ids = list(market.engine.trading_pairs.keys())
    bond_pair_ids = list(market.engine.bond_trading_pairs.keys())
    if pair_ids:
        market.engine.set_bot_trading_pairs(player_id, pair_ids)
    if bond_pair_ids:
        market.engine.set_bot_bond_pairs(player_id, bond_pair_ids)

    market._stop_event.clear()
    market._sim_thread = threading.Thread(target=market._simulation_loop, daemon=True)
    market._sim_thread.start()
    market._is_running = True

    time.sleep(0.5)

    def start_trader():
        try:
            from gui.trader_gui import TraderGUI

            trader_gui = TraderGUI(market.engine, player_id)
            trader_gui.run()
        except Exception as e:
            print(f"交易者 GUI 错误：{e}")

    trader_thread = threading.Thread(target=start_trader, daemon=True)
    trader_thread.start()

    try:
        from gui.charts import start_gui

        all_pairs = list(market.engine.trading_pairs.values()) + list(
            market.engine.bond_trading_pairs.values()
        )
        start_gui(all_pairs, window_title=cfg.name)
    except Exception as e:
        print(f"GUI 错误：{e}")
    finally:
        market.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已退出")
    except Exception as e:
        print(f"错误：{e}")
        import traceback

        traceback.print_exc()
