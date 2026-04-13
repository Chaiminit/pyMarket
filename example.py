#!/usr/bin/env python3
"""
市场模拟系统 - 使用示例

直接运行：python example.py
"""

import time
import threading
from core.engine import MarketEngine, get_engine, reset_engine
from core.corp import Corp


def example_basic():
    """基础示例 - 创建市场、交易者和进行交易"""
    print("=" * 50)
    print("基础示例：创建市场和交易者")
    print("=" * 50)

    # 重置引擎
    reset_engine()

    # 创建引擎
    engine = get_engine()

    # 创建代币
    usdt = engine.create_token("USDT", is_quote=True)
    eth = engine.create_token("ETH")
    btc = engine.create_token("BTC")

    # 创建交易对
    eth_pair = engine.create_trading_pair("ETH", "USDT", 2000.0)
    btc_pair = engine.create_trading_pair("BTC", "USDT", 60000.0)

    # 创建债券对
    bond_pair = engine.create_bond_trading_pair("USDT", 0.0001)

    # 创建交易者
    trader1 = engine.create_trader("交易者A")
    trader2 = engine.create_trader("交易者B")

    # 分配资产
    engine.allocate_assets(trader1, "USDT", 10000.0)
    engine.allocate_assets(trader1, "ETH", 5.0)
    engine.allocate_assets(trader2, "USDT", 10000.0)
    engine.allocate_assets(trader2, "BTC", 0.5)

    print(f"\n交易者A初始资产: {trader1.assets}")
    print(f"交易者B初始资产: {trader2.assets}")

    # 提交限价单 - 使用 Trader 的方法
    print("\n--- 提交限价单 ---")
    success = trader1.submit_limit_order(eth_pair, "sell", 2100.0, 2.0)
    print(f"交易者A提交卖单(ETH @ 2100): {'成功' if success else '失败'}")

    success = trader2.submit_limit_order(eth_pair, "buy", 2000.0, 1.0)
    print(f"交易者B提交买单(ETH @ 2000): {'成功' if success else '失败'}")

    # 查看订单簿
    buys, sells = eth_pair.get_order_book()
    print(f"\nETH/USDT 订单簿:")
    print(f"  买单: {buys}")
    print(f"  卖单: {sells}")

    # 执行市价单 - 使用 Trader 的方法
    print("\n--- 执行市价单 ---")
    volume, details = trader2.submit_market_order(eth_pair, "buy", 1.0)
    print(f"交易者B市价买入ETH: 成交量={volume}")

    print(f"\n交易者A当前资产: {trader1.assets}")
    print(f"交易者B当前资产: {trader2.assets}")

    # 债券交易示例 - 使用 Trader 的方法
    print("\n--- 债券交易 ---")

    # 交易者A卖出债券（做空债券，相当于借出USDT）
    success = trader1.submit_bond_limit_order(bond_pair, "sell", 0.0001, 1000.0)
    print(f"交易者A卖出债券(做空): {'成功' if success else '失败'}")

    # 交易者B买入债券（做多债券）
    success = trader2.submit_bond_limit_order(bond_pair, "buy", 0.0001, 500.0)
    print(f"交易者B买入债券(做多): {'成功' if success else '失败'}")

    # 查看债券订单簿
    bond_buys, bond_sells = bond_pair.get_order_book()
    print(f"\n债券订单簿:")
    print(f"  买单: {bond_buys}")
    print(f"  卖单: {bond_sells}")

    # 查看债券持仓
    print(f"交易者A债券持仓: {trader1.bonds}")
    print(f"交易者B债券持仓: {trader2.bonds}")

    # 运行几步模拟（利息结算）
    print("\n--- 运行模拟步骤（债券利息结算）---")
    for i in range(3):
        engine.step()
        print(f"步骤 {i+1} 完成")

    print(f"\n交易者A当前资产: {trader1.assets}")
    print(f"交易者A债券持仓: {trader1.bonds}")
    print(f"\n交易者B当前资产: {trader2.assets}")
    print(f"交易者B债券持仓: {trader2.bonds}")

    print("\n" + "=" * 50)
    print("示例完成！")
    print("=" * 50)


def example_ipo():
    """IPO示例 - 股份公司发行和交易"""
    print("\n" + "=" * 50)
    print("IPO示例：股份公司发行和交易")
    print("=" * 50)

    reset_engine()
    engine = get_engine()

    # 创建计价代币
    usdt = engine.create_token("USDT", is_quote=True)

    # 创建股份公司（IPO）
    company = Corp(
        name="TechCorp",
        total_shares=10000,      # 发行1万股
        initial_price=10.0,       # 每股10 USDT
        quote_token=usdt,
        token_id=100
    )

    # 获取交易对和股份代币
    trading_pair, share_token = company.get_trading_info()

    print(f"\n公司: {company.name}")
    print(f"股份代币: {share_token.name}")
    print(f"总股本: {company.total_shares}")
    print(f"发行价: {company.initial_price} USDT")
    print(f"市值: {company.total_shares * company.initial_price} USDT")

    # 创建投资者
    investor = engine.create_trader("投资者")
    engine.allocate_assets(investor, "USDT", 50000.0)

    print(f"\n投资者初始资金: {investor.assets.get(usdt, 0)} USDT")

    # 投资者购买股份 - 使用 Trader 的方法
    print("\n--- 投资者购买股份 ---")
    volume, details = investor.submit_market_order(trading_pair, "buy", 1000.0)
    print(f"购买股份: {volume} 股")

    print(f"\n公司剩余股份: {company.get_remaining_shares()}")
    print(f"公司已募集资金: {company.get_raised_funds()} USDT")
    print(f"投资者持有股份: {investor.assets.get(share_token, 0)}")
    print(f"投资者剩余资金: {investor.assets.get(usdt, 0)} USDT")

    # 尝试取消IPO订单（应该失败）
    print("\n--- 尝试取消IPO订单 ---")
    result = company.cancel_order(company.ipo_order)
    print(f"取消IPO订单: {'成功' if result else '失败（预期）'}")
    print(f"剩余股份: {company.get_remaining_shares()}")

    print("\n" + "=" * 50)
    print("IPO示例完成！")
    print("=" * 50)


def example_market_simulation():
    """市场模拟示例 - 运行一个简单的市场模拟"""
    print("\n" + "=" * 50)
    print("市场模拟示例")
    print("=" * 50)

    reset_engine()
    engine = get_engine()

    # 创建代币
    usdt = engine.create_token("USDT", is_quote=True)
    eth = engine.create_token("ETH")

    # 创建交易对
    eth_pair = engine.create_trading_pair("ETH", "USDT", 2000.0)

    # 创建债券对
    bond_pair = engine.create_bond_trading_pair("USDT", 0.0001)

    # 创建几个交易者并分配资产
    traders = []
    for i in range(3):
        trader = engine.create_trader(f"交易者{i+1}")
        engine.allocate_assets(trader, "USDT", 5000.0)
        engine.allocate_assets(trader, "ETH", 2.0)
        traders.append(trader)

    print(f"\n创建了 {len(engine.traders)} 个交易者")
    print("运行市场模拟 3 秒...")

    # 启动模拟线程
    stop_event = threading.Event()

    def simulation_loop():
        while not stop_event.is_set():
            engine.step()
            time.sleep(0.5)

    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()

    # 运行3秒
    time.sleep(3)
    stop_event.set()
    sim_thread.join(timeout=1.0)

    print("模拟完成！")

    # 查看最终状态
    for trader in traders:
        print(f"\n{trader.name}:")
        print(f"  资产: {trader.assets}")
        print(f"  债券: {trader.bonds}")

    print("\n" + "=" * 50)


if __name__ == "__main__":
    # 运行基础示例
    example_basic()

    # 运行IPO示例
    example_ipo()

    # 运行市场模拟示例
    example_market_simulation()
