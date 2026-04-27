#!/usr/bin/env python3
"""
市场模拟系统 - 使用示例

直接运行：python example.py
"""

import time
import threading
from decimal import Decimal
from core.engine import MarketEngine, get_engine, reset_engine
from core.corp import Corp


def example_basic():
    """基础示例 - 创建市场、交易者和进行交易"""
    print("=" * 50)
    print("基础示例：创建市场和交易者")
    print("=" * 50)

    # 重置引擎
    reset_engine()
    engine = MarketEngine()

    # 创建代币
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")

    # 创建交易对
    pair = engine.create_trading_pair("BTC", "USDT", initial_price=Decimal("50000"))

    # 创建交易者
    trader1 = engine.create_trader("交易者 A")
    trader2 = engine.create_trader("交易者 B")

    # 分配初始资产
    engine.allocate_assets(trader1, btc, Decimal("10"))
    engine.allocate_assets(trader1, usdt, Decimal("100000"))
    engine.allocate_assets(trader2, btc, Decimal("10"))
    engine.allocate_assets(trader2, usdt, Decimal("100000"))

    # 进行交易
    print("\n--- 进行交易 ---")
    print("交易者 A 以 49000 USDT 买入 1 BTC")
    trader1.submit_limit_order(pair, "buy", Decimal("49000"), Decimal("1"))

    print("交易者 B 以 49000 USDT 卖出 1 BTC")
    trader2.submit_limit_order(pair, "sell", Decimal("49000"), Decimal("1"))

    # 查看订单簿
    buys, sells = pair.get_order_book()
    print(f"\n订单簿:")
    print(f"  买单：{buys}")
    print(f"  卖单：{sells}")

    # 查看交易者资产
    print(f"\n交易者 A 资产：{trader1.assets}")
    print(f"交易者 B 资产：{trader2.assets}")

    print("\n" + "=" * 50)


def example_bond_trading():
    """债券交易示例"""
    print("\n" + "=" * 50)
    print("债券交易示例")
    print("=" * 50)

    # 重置引擎
    reset_engine()
    engine = MarketEngine()

    # 创建 USDT 债券交易对
    usdt = engine.create_token("USDT", is_quote=True)
    bond_pair = engine.create_bond_trading_pair("USDT", Decimal("0.05"))

    # 创建交易者
    trader1 = engine.create_trader("交易者 A")
    trader2 = engine.create_trader("交易者 B")

    # 分配初始资产
    engine.allocate_assets(trader1, usdt, Decimal("100000"))
    engine.allocate_assets(trader2, usdt, Decimal("100000"))

    # 进行债券交易
    print("\n--- 进行债券交易 ---")
    print("交易者 A 以 5% 利率借出 10000 USDT（买入债券）")
    trader1.submit_bond_limit_order(bond_pair, "buy", Decimal("0.05"), Decimal("10000"))

    print("交易者 B 以 5% 利率借入 10000 USDT（卖出债券）")
    trader2.submit_bond_limit_order(bond_pair, "sell", Decimal("0.05"), Decimal("10000"))

    # 查看订单簿
    bond_buys, bond_sells = bond_pair.get_order_book()
    print(f"\n债券订单簿:")
    print(f"  买单：{bond_buys}")
    print(f"  卖单：{bond_sells}")

    # 查看债券持仓（债券代币直接保存在 assets 中）
    bond_token = bond_pair.base_token
    print(f"交易者 A 债券持仓：{trader1.assets.get(bond_token, Decimal('0'))}")
    print(f"交易者 B 债券持仓：{trader2.assets.get(bond_token, Decimal('0'))}")

    # 运行几步模拟（利息结算）
    print("\n--- 运行模拟步骤（债券利息结算）---")
    for i in range(3):
        engine.step()
        print(f"步骤 {i+1} 完成")

    print(f"\n交易者 A 当前资产：{trader1.assets}")
    print(f"交易者 A 债券持仓：{trader1.assets.get(bond_token, Decimal('0'))}")
    print(f"\n交易者 B 当前资产：{trader2.assets}")
    print(f"交易者 B 债券持仓：{trader2.assets.get(bond_token, Decimal('0'))}")

    print("\n" + "=" * 50)
    print("示例完成！")
    print("=" * 50)


def example_ipo():
    """IPO 示例 - 股份公司发行和交易"""
    print("\n" + "=" * 50)
    print("IPO 示例：股份公司发行和交易")
    print("=" * 50)

    # 重置引擎
    reset_engine()
    engine = MarketEngine()

    # 创建代币和交易对
    usdt = engine.create_token("USDT", is_quote=True)
    corp = Corp("TestCorp", engine, usdt, Decimal("100"), Decimal("1000"))

    # 创建交易者
    trader1 = engine.create_trader("投资者 A")
    trader2 = engine.create_trader("投资者 B")

    # 分配初始资金
    engine.allocate_assets(trader1, usdt, Decimal("50000"))
    engine.allocate_assets(trader2, usdt, Decimal("50000"))

    print(f"\n公司发行 {corp.total_shares} 股，初始价格 {corp.initial_price} USDT")
    print(f"IPO 前公司 USDT 资产：{corp.assets.get(usdt, Decimal('0'))}")

    # 投资者购买 IPO
    print("\n--- 投资者购买 IPO ---")
    print("投资者 A 购买 200 股")
    trader1.submit_limit_order(corp.trading_pair, "buy", corp.initial_price, Decimal("200"))

    print("投资者 B 购买 300 股")
    trader2.submit_limit_order(corp.trading_pair, "buy", corp.initial_price, Decimal("300"))

    # 查看 IPO 结果
    print(f"\nIPO 后公司 USDT 资产：{corp.assets.get(usdt, Decimal('0'))}")
    print(f"投资者 A 资产：{trader1.assets}")
    print(f"投资者 B 资产：{trader2.assets}")

    # 查看流通股
    print(f"\n流通股数：{corp.get_circulating_shares(engine.traders)}")

    print("\n" + "=" * 50)


def example_market_simulation():
    """市场模拟示例 - 多线程后台模拟"""
    print("\n" + "=" * 50)
    print("市场模拟示例：后台模拟 + 实时交易")
    print("=" * 50)

    # 重置引擎
    reset_engine()
    engine = MarketEngine()

    # 创建代币和交易对
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", initial_price=Decimal("50000"))

    # 创建多个交易者
    traders = []
    for i in range(5):
        trader = engine.create_trader(f"Trader_{i}")
        engine.allocate_assets(trader, btc, Decimal("10"))
        engine.allocate_assets(trader, usdt, Decimal("500000"))
        traders.append(trader)

    print(f"\n创建 {len(traders)} 个交易者，每个初始资产：10 BTC + 500000 USDT")

    # 启动后台模拟线程
    stop_event = threading.Event()

    def simulate_market():
        """后台模拟线程函数"""
        while not stop_event.is_set():
            engine.step()
            time.sleep(0.1)  # 每 0.1 秒执行一步

    sim_thread = threading.Thread(target=simulate_market, daemon=True)
    sim_thread.start()
    print("后台模拟线程已启动")

    # 进行一些随机交易
    print("\n--- 进行随机交易 ---")
    import random
    for i in range(10):
        trader = random.choice(traders)
        direction = random.choice(["buy", "sell"])
        price = Decimal(str(random.randint(49000, 51000)))
        volume = Decimal(str(random.randint(1, 5))) / Decimal("10")

        try:
            trader.submit_limit_order(pair, direction, price, volume)
            print(f"  {trader.name} {direction} {volume} BTC @ {price} USDT")
        except Exception as e:
            print(f"  {trader.name} 交易失败：{e}")

        time.sleep(0.05)

    # 停止模拟
    print("\n停止模拟...")
    stop_event.set()
    sim_thread.join(timeout=1.0)

    print("模拟完成！")

    # 查看最终状态
    for trader in traders:
        print(f"\n{trader.name}:")
        print(f"  资产：{trader.assets}")

    print("\n" + "=" * 50)


if __name__ == "__main__":
    # 运行基础示例
    example_basic()

    # 运行债券交易示例
    example_bond_trading()

    # 运行 IPO 示例
    example_ipo()

    # 运行市场模拟示例
    example_market_simulation()
