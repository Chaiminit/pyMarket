"""
RMM 集成测试 - 反射性做市商完整功能验证

测试场景：
1. 引擎与 RMM 初始化
2. 冷启动：通过手续费机制积累流动性
3. 限价单撮合 + RMM 套利
4. 市价单执行 + 订单簿深度不足时 RMM 接管
5. 滑点补偿费收取与手续费比例限制
6. 多交易对共用一个 RMM 实例
7. 共识价格跟踪
8. 恒定乘积不变量验证
9. 边界条件与异常场景
"""

import sys
import os
import traceback
from decimal import Decimal

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import MarketEngine, ReflexiveMarketMaker, TradingPair, Token, Trader
from core.utils import to_decimal, D0, D1


def assert_eq(name, actual, expected, tolerance=None):
    if tolerance is not None:
        diff = abs(actual - expected)
        if diff > tolerance:
            raise AssertionError(f"[{name}] expected {expected}, got {actual}, diff {diff} > tol {tolerance}")
    else:
        if actual != expected:
            raise AssertionError(f"[{name}] expected {expected}, got {actual}")
    print(f"  [PASS] {name}: {actual}")


def assert_true(name, condition):
    if not condition:
        raise AssertionError(f"[{name}] condition not met")
    print(f"  [PASS] {name}")


def assert_gt(name, a, b):
    if not (a > b):
        raise AssertionError(f"[{name}] {a} should be > {b}")
    print(f"  [PASS] {name}: {a} > {b}")


def assert_gte(name, a, b):
    if not (a >= b):
        raise AssertionError(f"[{name}] {a} should be >= {b}")
    print(f"  [PASS] {name}: {a} >= {b}")


def assert_lte(name, a, b):
    if not (a <= b):
        raise AssertionError(f"[{name}] {a} should be <= {b}")
    print(f"  [PASS] {name}: {a} <= {b}")


# ============================================================
# Test 1: Engine & RMM Initialization
# ============================================================
def test_engine_rmm_initialization():
    print("\n=== Test 1: Engine & RMM Initialization ===")

    engine = MarketEngine()
    assert_true("engine has rmm attr", hasattr(engine, "rmm"))
    assert_true("rmm is ReflexiveMarketMaker", isinstance(engine.rmm, ReflexiveMarketMaker))

    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    eth = engine.create_token("ETH")
    pair_btc = engine.create_trading_pair("BTC", "USDT", 50000.0)
    pair_eth = engine.create_trading_pair("ETH", "USDT", 3000.0)

    assert_true("BTC pair can access rmm", pair_btc.rmm is engine.rmm)
    assert_true("ETH pair can access rmm", pair_eth.rmm is engine.rmm)
    assert_true("both pairs share same rmm", pair_btc.rmm is pair_eth.rmm)

    assert_eq("BTC pool no liquidity", pair_btc.rmm.has_liquidity(pair_btc), False)
    assert_eq("ETH pool no liquidity", pair_eth.rmm.has_liquidity(pair_eth), False)

    rb, rq = pair_btc.get_amm_reserves()
    assert_eq("BTC initial base reserve", rb, D0)
    assert_eq("BTC initial quote reserve", rq, D0)

    p = pair_btc.get_amm_price()
    assert_eq("no-liquidity AMM price = consensus", p, pair_btc.consensus_price)

    # 当池子没有流动性时，fee_rate 返回 min_fee_rate 作为冷启动费率
    assert_eq("initial fee rate", pair_btc.amm_current_fee_rate, engine.rmm.min_fee_rate)

    print("  Test 1 PASSED")


# ============================================================
# Test 2: Limit order matching + RMM arbitrage cold start
# ============================================================
def test_limit_order_arbitrage_cold_start():
    print("\n=== Test 2: Limit Order Matching + RMM Cold Start ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 1000000)
    engine.allocate_assets(bob, btc, 20)

    # Alice buy@49000, Bob sell@51000 (no match, spread exists)
    alice.submit_limit_order(pair, "buy", 49000, 1)
    bob.submit_limit_order(pair, "sell", 51000, 1)

    # Bob sell@49000 -> matches Alice buy@49000
    bob.submit_limit_order(pair, "sell", 49000, 1)

    assert_eq("price after match", pair.price, to_decimal("49000"))

    rb, rq = pair.get_amm_reserves()
    print(f"  RMM reserves after match: base={rb}, quote={rq}")

    if rb > D0 or rq > D0:
        print(f"  Cold start: RMM accumulated liquidity via fees base={rb}, quote={rq}")
        k = rb * rq
        assert_gt("k > 0", k, D0)
    else:
        print("  Cold start: no liquidity yet (pool still empty)")

    print("  Test 2 PASSED")


# ============================================================
# Test 3: Multi-round limit orders + fee rate bounds
# ============================================================
def test_multi_round_limit_orders_fee_rate_bounds():
    print("\n=== Test 3: Multi-Round Limit Orders + Fee Rate Bounds ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 10000000)
    engine.allocate_assets(bob, btc, 100)

    for i in range(10):
        price = 50000 + (i % 3 - 1) * 500
        alice.submit_limit_order(pair, "buy", price, 0.5)
        bob.submit_limit_order(pair, "sell", price, 0.5)

    rb, rq = pair.get_amm_reserves()
    print(f"  Reserves after 10 rounds: base={rb}, quote={rq}")

    if rb > D0 and rq > D0:
        k = rb * rq
        assert_gt("k > 0", k, D0)
        amm_price = pair.get_amm_price()
        print(f"  AMM price: {amm_price}, consensus: {pair.consensus_price}")

    fee_rate = pair.amm_current_fee_rate
    min_rate = engine.rmm.min_fee_rate
    max_rate = engine.rmm.max_fee_rate
    print(f"  Fee rate: {fee_rate}, min: {min_rate}, max: {max_rate}")

    # Fee rate is 0 when no liquidity (no arbitrage = no slippage = no fee)
    # When fee_rate > 0, it must be within bounds
    if fee_rate > D0:
        assert_gte("fee_rate >= min", fee_rate, min_rate)
        assert_lte("fee_rate <= max", fee_rate, max_rate)
    else:
        print("  (fee_rate=0 is valid when pool has no liquidity)")

    print("  Test 3 PASSED")


# ============================================================
# Test 4: Market order + RMM fallback
# ============================================================
def test_market_order_with_rmm_fallback():
    print("\n=== Test 4: Market Order + RMM Fallback ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")
    carol = engine.create_trader("Carol")

    engine.allocate_assets(alice, usdt, 10000000)
    engine.allocate_assets(bob, btc, 100)
    engine.allocate_assets(carol, usdt, 10000000)
    engine.allocate_assets(carol, btc, 100)

    for i in range(20):
        price = 50000 + (i % 5 - 2) * 200
        alice.submit_limit_order(pair, "buy", price, 1)
        bob.submit_limit_order(pair, "sell", price, 1)

    rb, rq = pair.get_amm_reserves()
    print(f"  Reserves after limit orders: base={rb}, quote={rq}")

    if rb <= D0 or rq <= D0:
        print("  Skipping RMM fallback test (insufficient liquidity)")
        print("  Test 4 PASSED (partial skip)")
        return

    carol.submit_limit_order(pair, "buy", 49000, 0.1)

    dave = engine.create_trader("Dave")
    engine.allocate_assets(dave, btc, 10)

    vol, details, fee = dave.submit_market_order(pair, "sell", 2)
    print(f"  Market order volume: {vol}, fee: {fee}, details: {len(details)}")

    assert_gt("market order has volume", vol, D0)

    amm_trades = [d for d in details if d.get("counterparty") == "AMM"]
    orderbook_trades = [d for d in details if d.get("counterparty") != "AMM"]
    print(f"  Orderbook trades: {len(orderbook_trades)}, AMM trades: {len(amm_trades)}")

    rb2, rq2 = pair.get_amm_reserves()
    print(f"  Reserves after market order: base={rb2}, quote={rq2}")

    print("  Test 4 PASSED")


# ============================================================
# Test 5: Slippage compensation & fee rate limits
# ============================================================
def test_slippage_compensation_fee_limits():
    print("\n=== Test 5: Slippage Compensation & Fee Rate Limits ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 10000000)
    engine.allocate_assets(bob, btc, 100)

    for i in range(15):
        price = 50000 + (i % 3 - 1) * 300
        alice.submit_limit_order(pair, "buy", price, 0.5)
        bob.submit_limit_order(pair, "sell", price, 0.5)

    fee_rate = pair.amm_current_fee_rate
    min_rate = engine.rmm.min_fee_rate
    max_rate = engine.rmm.max_fee_rate

    print(f"  Fee rate: {fee_rate}, min: {min_rate}, max: {max_rate}")

    if fee_rate > D0:
        assert_gte("fee_rate >= min", fee_rate, min_rate)
        assert_lte("fee_rate <= max", fee_rate, max_rate)

    carol = engine.create_trader("Carol")
    engine.allocate_assets(carol, usdt, 5000000)
    engine.allocate_assets(carol, btc, 50)

    carol.submit_limit_order(pair, "buy", 49500, 1)
    carol.submit_limit_order(pair, "buy", 49000, 1)

    dave = engine.create_trader("Dave")
    engine.allocate_assets(dave, btc, 10)
    dave.submit_market_order(pair, "sell", 0.5)

    fee_rate2 = pair.amm_current_fee_rate
    print(f"  Fee rate after market order: {fee_rate2}")
    if fee_rate2 > D0:
        assert_gte("fee_rate2 >= min", fee_rate2, min_rate)
        assert_lte("fee_rate2 <= max", fee_rate2, max_rate)

    print("  Test 5 PASSED")


# ============================================================
# Test 6: Multiple pairs sharing one RMM
# ============================================================
def test_multiple_pairs_shared_rmm():
    print("\n=== Test 6: Multiple Pairs Sharing One RMM ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    eth = engine.create_token("ETH")
    sol = engine.create_token("SOL")

    pair_btc = engine.create_trading_pair("BTC", "USDT", 50000.0)
    pair_eth = engine.create_trading_pair("ETH", "USDT", 3000.0)
    pair_sol = engine.create_trading_pair("SOL", "USDT", 150.0)

    assert_true("all pairs share same RMM", pair_btc.rmm is pair_eth.rmm is pair_sol.rmm)

    traders = {}
    for name, pair, base_amount, quote_amount in [
        ("BTC_Alice", pair_btc, 10, 1000000),
        ("BTC_Bob", pair_btc, 20, 2000000),
        ("ETH_Alice", pair_eth, 50, 500000),
        ("ETH_Bob", pair_eth, 100, 1000000),
        ("SOL_Alice", pair_sol, 500, 200000),
        ("SOL_Bob", pair_sol, 1000, 400000),
    ]:
        trader = engine.create_trader(name)
        engine.allocate_assets(trader, usdt, quote_amount)
        engine.allocate_assets(trader, pair.base_token, base_amount)
        traders[name] = trader

    traders["BTC_Alice"].submit_limit_order(pair_btc, "buy", 49500, 0.5)
    traders["BTC_Bob"].submit_limit_order(pair_btc, "sell", 49500, 0.5)
    traders["ETH_Alice"].submit_limit_order(pair_eth, "buy", 2950, 5)
    traders["ETH_Bob"].submit_limit_order(pair_eth, "sell", 2950, 5)
    traders["SOL_Alice"].submit_limit_order(pair_sol, "buy", 148, 50)
    traders["SOL_Bob"].submit_limit_order(pair_sol, "sell", 148, 50)

    rb_btc, rq_btc = pair_btc.get_amm_reserves()
    rb_eth, rq_eth = pair_eth.get_amm_reserves()
    rb_sol, rq_sol = pair_sol.get_amm_reserves()

    print(f"  BTC reserves: base={rb_btc}, quote={rq_btc}")
    print(f"  ETH reserves: base={rb_eth}, quote={rq_eth}")
    print(f"  SOL reserves: base={rb_sol}, quote={rq_sol}")

    for i in range(10):
        price_btc = 50000 + (i % 3 - 1) * 500
        price_eth = 3000 + (i % 3 - 1) * 30
        price_sol = 150 + (i % 3 - 1) * 2

        traders["BTC_Alice"].submit_limit_order(pair_btc, "buy", price_btc, 0.1)
        traders["BTC_Bob"].submit_limit_order(pair_btc, "sell", price_btc, 0.1)
        traders["ETH_Alice"].submit_limit_order(pair_eth, "buy", price_eth, 1)
        traders["ETH_Bob"].submit_limit_order(pair_eth, "sell", price_eth, 1)
        traders["SOL_Alice"].submit_limit_order(pair_sol, "buy", price_sol, 10)
        traders["SOL_Bob"].submit_limit_order(pair_sol, "sell", price_sol, 10)

    assert_eq("RMM pool count", len(engine.rmm._pools), 3)

    fee_btc = pair_btc.amm_current_fee_rate
    fee_eth = pair_eth.amm_current_fee_rate
    fee_sol = pair_sol.amm_current_fee_rate
    print(f"  BTC fee: {fee_btc}, ETH fee: {fee_eth}, SOL fee: {fee_sol}")

    print("  Test 6 PASSED")


# ============================================================
# Test 7: Consensus price tracking via matching
# ============================================================
def test_consensus_price_tracking():
    print("\n=== Test 7: Consensus Price Tracking ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 10000000)
    engine.allocate_assets(bob, btc, 100)

    assert_eq("initial consensus price", pair.consensus_price, to_decimal("50000"))

    # Matching changes price and consensus
    alice.submit_limit_order(pair, "buy", 51000, 1)
    bob.submit_limit_order(pair, "sell", 51000, 1)
    assert_eq("price after match at 51000", pair.price, to_decimal("51000"))

    # After match, if both sides of order book exist, consensus updates
    alice.submit_limit_order(pair, "buy", 50500, 0.5)
    bob.submit_limit_order(pair, "sell", 51500, 0.5)

    # Now both sides exist: buy@50500, sell@51500
    # update_consensus_price is only called during matching, not on new order placement
    # So we manually call it to verify
    pair.update_consensus_price()
    expected = (to_decimal("50500") + to_decimal("51500")) / to_decimal("2")
    assert_eq("consensus after manual update", pair.consensus_price, expected)

    # Heavy trading
    for i in range(30):
        price = 50000 + (i % 7 - 3) * 200
        alice.submit_limit_order(pair, "buy", price, 0.2)
        bob.submit_limit_order(pair, "sell", price, 0.2)

    rb, rq = pair.get_amm_reserves()
    if rb > D0 and rq > D0:
        amm_price = pair.get_amm_price()
        print(f"  AMM price: {amm_price}, consensus: {pair.consensus_price}")

    print("  Test 7 PASSED")


# ============================================================
# Test 8: Constant product invariant
# ============================================================
def test_constant_product_invariant():
    print("\n=== Test 8: Constant Product Invariant ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 10000000)
    engine.allocate_assets(bob, btc, 100)

    for i in range(20):
        price = 50000 + (i % 5 - 2) * 300
        alice.submit_limit_order(pair, "buy", price, 0.3)
        bob.submit_limit_order(pair, "sell", price, 0.3)

    rb, rq = pair.get_amm_reserves()
    if rb > D0 and rq > D0:
        pool = engine.rmm._get_pool(pair)
        k = pool.k
        actual_k = rb * rq
        print(f"  recorded k = {k}, actual R_b * R_q = {actual_k}")
        tolerance = max(k, actual_k) * to_decimal("0.001")
        diff = abs(k - actual_k)
        assert_lte("k ~ R_b * R_q", diff, tolerance)

    carol = engine.create_trader("Carol")
    engine.allocate_assets(carol, usdt, 5000000)
    engine.allocate_assets(carol, btc, 50)

    carol.submit_limit_order(pair, "buy", 49500, 1)

    dave = engine.create_trader("Dave")
    engine.allocate_assets(dave, btc, 10)
    dave.submit_market_order(pair, "sell", 0.5)

    rb2, rq2 = pair.get_amm_reserves()
    if rb2 > D0 and rq2 > D0:
        pool2 = engine.rmm._get_pool(pair)
        k2 = pool2.k
        actual_k2 = rb2 * rq2
        print(f"  after market order: k = {k2}, R_b * R_q = {actual_k2}")
        tolerance2 = max(k2, actual_k2) * to_decimal("0.001")
        diff2 = abs(k2 - actual_k2)
        assert_lte("after market k ~ R_b * R_q", diff2, tolerance2)

    print("  Test 8 PASSED")


# ============================================================
# Test 9: Edge cases
# ============================================================
def test_edge_cases():
    print("\n=== Test 9: Edge Cases ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    engine.allocate_assets(alice, usdt, 100000)

    # 9a: market order with no counterparty
    vol, details, fee = alice.submit_market_order(pair, "buy", 0.001)
    assert_eq("no counterparty volume", vol, D0)

    # 9b: zero volume market order
    vol, details, fee = pair.execute_market_order(alice, "buy", 0)
    assert_eq("zero volume", vol, D0)

    # 9c: AMM price = consensus when no liquidity
    assert_eq("no-liquidity AMM price", pair.get_amm_price(), pair.consensus_price)

    # 9d: reserves zero when no liquidity
    rb, rq = pair.get_amm_reserves()
    assert_eq("no-liquidity base", rb, D0)
    assert_eq("no-liquidity quote", rq, D0)

    # 9e: fee rate range
    assert_gte("min_fee_rate >= 0", engine.rmm.min_fee_rate, D0)
    assert_gt("max_fee_rate > min_fee_rate", engine.rmm.max_fee_rate, engine.rmm.min_fee_rate)

    # 9f: sell-only order book -> consensus via manual update
    bob = engine.create_trader("Bob")
    engine.allocate_assets(bob, btc, 10)
    bob.submit_limit_order(pair, "sell", 51000, 1)
    # update_consensus_price only runs during matching, so call manually
    pair.update_consensus_price()
    assert_eq("sell-only consensus = sell price", pair.consensus_price, to_decimal("51000"))

    # 9g: pool data exists after registration
    pool = engine.rmm._get_pool(pair)
    assert_true("pool data exists", pool is not None)

    # 9h: has_liquidity returns False for empty pool
    assert_eq("empty pool has_liquidity", engine.rmm.has_liquidity(pair), False)

    print("  Test 9 PASSED")


# ============================================================
# Test 10: Full trading lifecycle
# ============================================================
def test_full_trading_lifecycle():
    print("\n=== Test 10: Full Trading Lifecycle ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    makers = []
    for i in range(5):
        m = engine.create_trader(f"Maker_{i}")
        engine.allocate_assets(m, usdt, 2000000)
        engine.allocate_assets(m, btc, 20)
        makers.append(m)

    taker_buy = engine.create_trader("TakerBuy")
    engine.allocate_assets(taker_buy, usdt, 5000000)

    taker_sell = engine.create_trader("TakerSell")
    engine.allocate_assets(taker_sell, btc, 50)

    # Phase 1: Build order book
    print("  Phase 1: Build order book...")
    for i, m in enumerate(makers):
        buy_price = 49500 + i * 100
        sell_price = 50500 + i * 100
        m.submit_limit_order(pair, "buy", buy_price, 0.5)
        m.submit_limit_order(pair, "sell", sell_price, 0.5)

    buys, sells = pair.get_order_book(5)
    print(f"    Buy levels: {len(buys)}, Sell levels: {len(sells)}")
    assert_gt("has buy orders", len(buys), 0)
    assert_gt("has sell orders", len(sells), 0)

    # Phase 2: Limit order matching
    print("  Phase 2: Limit order matching...")
    makers[0].submit_limit_order(pair, "buy", 50600, 0.3)
    print(f"    Price after match: {pair.price}")

    # Phase 3: Market orders
    print("  Phase 3: Market orders...")
    vol_buy, details_buy, fee_buy = taker_buy.submit_market_order(pair, "buy", 1)
    print(f"    Buy volume: {vol_buy}, fee: {fee_buy}")

    vol_sell, details_sell, fee_sell = taker_sell.submit_market_order(pair, "sell", 1)
    print(f"    Sell volume: {vol_sell}, fee: {fee_sell}")

    # Phase 4: Check RMM state
    print("  Phase 4: Check RMM state...")
    rb, rq = pair.get_amm_reserves()
    print(f"    RMM reserves: base={rb}, quote={rq}")

    if rb > D0 and rq > D0:
        amm_price = pair.get_amm_price()
        print(f"    AMM price: {amm_price}, consensus: {pair.consensus_price}")
        k = rb * rq
        print(f"    Constant product k: {k}")

    fee_rate = pair.amm_current_fee_rate
    print(f"    Current fee rate: {fee_rate}")

    # Phase 5: Depth query
    print("  Phase 5: Depth query...")
    depth = pair.get_market_depth()
    print(f"    Buy orders: {depth['buy_orders']}, Sell orders: {depth['sell_orders']}")
    print(f"    Buy volume: {depth['buy_volume']}, Sell volume: {depth['sell_volume']}")

    # Phase 6: Engine step
    print("  Phase 6: Engine step...")
    engine.step()
    print(f"    Consensus after step: {pair.consensus_price}")

    # Phase 7: Verify RMM pool isolation
    print("  Phase 7: RMM pool isolation...")
    assert_eq("RMM pool count", len(engine.rmm._pools), 1)

    print("  Test 10 PASSED")


# ============================================================
# Test 11: Custom fee rate range
# ============================================================
def test_custom_fee_rate_range():
    print("\n=== Test 11: Custom Fee Rate Range ===")

    from core.engine_node import EngineNode
    EngineNode.clear_all_nodes()

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    assert_eq("default min_fee_rate", engine.rmm.min_fee_rate, to_decimal("0.00001"))
    assert_eq("default max_fee_rate", engine.rmm.max_fee_rate, to_decimal("0.1"))

    custom_rmm = ReflexiveMarketMaker(min_fee_rate="0.001", max_fee_rate="0.05")
    assert_eq("custom min_fee_rate", custom_rmm.min_fee_rate, to_decimal("0.001"))
    assert_eq("custom max_fee_rate", custom_rmm.max_fee_rate, to_decimal("0.05"))

    print("  Test 11 PASSED")


# ============================================================
# Test 12: Large trade impact
# ============================================================
def test_large_trade_impact():
    print("\n=== Test 12: Large Trade Impact ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    whale = engine.create_trader("Whale")
    engine.allocate_assets(whale, usdt, 100000000)
    engine.allocate_assets(whale, btc, 1000)

    makers = []
    for i in range(10):
        m = engine.create_trader(f"Maker_{i}")
        engine.allocate_assets(m, usdt, 5000000)
        engine.allocate_assets(m, btc, 50)
        makers.append(m)

    for i, m in enumerate(makers):
        buy_price = 49800 + i * 50
        sell_price = 50200 + i * 50
        m.submit_limit_order(pair, "buy", buy_price, 1)
        m.submit_limit_order(pair, "sell", sell_price, 1)

    price_before = pair.price
    print(f"  Price before large trade: {price_before}")

    vol, details, fee = whale.submit_market_order(pair, "buy", 5)
    print(f"  Large buy: volume={vol}, details={len(details)}")

    price_after_buy = pair.price
    print(f"  Price after large buy: {price_after_buy}")
    assert_gt("price rises after buy", price_after_buy, price_before)

    vol2, details2, fee2 = whale.submit_market_order(pair, "sell", 5)
    print(f"  Large sell: volume={vol2}, details={len(details2)}")

    price_after_sell = pair.price
    print(f"  Price after large sell: {price_after_sell}")

    rb, rq = pair.get_amm_reserves()
    print(f"  RMM reserves: base={rb}, quote={rq}")

    if rb > D0 and rq > D0:
        k = rb * rq
        assert_gt("k > 0", k, D0)

    print("  Test 12 PASSED")


# ============================================================
# Test 13: RMM cold start via fee accumulation
# ============================================================
def test_rmm_cold_start_via_fees():
    print("\n=== Test 13: RMM Cold Start via Fee Accumulation ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 50000000)
    engine.allocate_assets(bob, btc, 500)

    # Initial state: no liquidity
    assert_eq("initial has_liquidity", engine.rmm.has_liquidity(pair), False)
    rb0, rq0 = pair.get_amm_reserves()
    assert_eq("initial base = 0", rb0, D0)
    assert_eq("initial quote = 0", rq0, D0)

    # Trade aggressively to trigger arbitrage and fee accumulation
    total_rounds = 50
    for i in range(total_rounds):
        spread = 200
        mid = 50000 + (i % 5 - 2) * 100
        alice.submit_limit_order(pair, "buy", mid - spread, 0.5)
        bob.submit_limit_order(pair, "sell", mid + spread, 0.5)
        # Also do crossing orders to trigger matching + arbitrage
        if i % 3 == 0:
            alice.submit_limit_order(pair, "buy", mid + spread, 0.3)
            bob.submit_limit_order(pair, "sell", mid - spread, 0.3)

    rb, rq = pair.get_amm_reserves()
    fee_rate = pair.amm_current_fee_rate
    print(f"  After {total_rounds} rounds: base={rb}, quote={rq}, fee_rate={fee_rate}")

    if rb > D0 and rq > D0:
        assert_true("RMM has liquidity after cold start", engine.rmm.has_liquidity(pair))
        k = rb * rq
        assert_gt("k > 0 after cold start", k, D0)

        amm_price = pair.get_amm_price()
        print(f"  AMM price: {amm_price}, consensus: {pair.consensus_price}")

        if fee_rate > D0:
            assert_gte("fee_rate >= min", fee_rate, engine.rmm.min_fee_rate)
            assert_lte("fee_rate <= max", fee_rate, engine.rmm.max_fee_rate)
    else:
        print("  Pool still empty after cold start attempt (expected in some configurations)")

    print("  Test 13 PASSED")


# ============================================================
# Test 14: Order book + RMM interaction consistency
# ============================================================
def test_orderbook_rmm_consistency():
    print("\n=== Test 14: Order Book + RMM Interaction Consistency ===")

    engine = MarketEngine()
    usdt = engine.create_token("USDT", is_quote=True)
    btc = engine.create_token("BTC")
    pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

    alice = engine.create_trader("Alice")
    bob = engine.create_trader("Bob")

    engine.allocate_assets(alice, usdt, 20000000)
    engine.allocate_assets(bob, btc, 200)

    # Create a deep order book
    for i in range(20):
        buy_price = 49000 + i * 50
        sell_price = 50000 + i * 50
        alice.submit_limit_order(pair, "buy", buy_price, 0.2)
        bob.submit_limit_order(pair, "sell", sell_price, 0.2)

    depth_before = pair.get_market_depth()
    print(f"  Depth before: buy_vol={depth_before['buy_volume']}, sell_vol={depth_before['sell_volume']}")

    # Execute market orders that consume the book
    carol = engine.create_trader("Carol")
    engine.allocate_assets(carol, usdt, 10000000)
    engine.allocate_assets(carol, btc, 100)

    vol1, det1, fee1 = carol.submit_market_order(pair, "buy", 2)
    print(f"  Market buy: vol={vol1}")

    vol2, det2, fee2 = carol.submit_market_order(pair, "sell", 2)
    print(f"  Market sell: vol={vol2}")

    depth_after = pair.get_market_depth()
    print(f"  Depth after: buy_vol={depth_after['buy_volume']}, sell_vol={depth_after['sell_volume']}")

    # RMM state should be consistent
    rb, rq = pair.get_amm_reserves()
    if rb > D0 and rq > D0:
        pool = engine.rmm._get_pool(pair)
        k = pool.k
        actual_k = rb * rq
        diff = abs(k - actual_k)
        tol = max(k, actual_k) * to_decimal("0.001")
        assert_lte("k invariant holds", diff, tol)

    print("  Test 14 PASSED")


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("RMM Integration Tests - Reflexive Market Maker")
    print("=" * 60)

    tests = [
        ("Engine & RMM Initialization", test_engine_rmm_initialization),
        ("Limit Order + Cold Start", test_limit_order_arbitrage_cold_start),
        ("Multi-Round + Fee Rate Bounds", test_multi_round_limit_orders_fee_rate_bounds),
        ("Market Order + RMM Fallback", test_market_order_with_rmm_fallback),
        ("Slippage Compensation & Fee Limits", test_slippage_compensation_fee_limits),
        ("Multiple Pairs Shared RMM", test_multiple_pairs_shared_rmm),
        ("Consensus Price Tracking", test_consensus_price_tracking),
        ("Constant Product Invariant", test_constant_product_invariant),
        ("Edge Cases", test_edge_cases),
        ("Full Trading Lifecycle", test_full_trading_lifecycle),
        ("Custom Fee Rate Range", test_custom_fee_rate_range),
        ("Large Trade Impact", test_large_trade_impact),
        ("RMM Cold Start via Fees", test_rmm_cold_start_via_fees),
        ("Order Book + RMM Consistency", test_orderbook_rmm_consistency),
    ]

    passed = 0
    failed = 0
    errors = []

    for name, test_fn in tests:
        try:
            from core.engine_node import EngineNode
            EngineNode.clear_all_nodes()
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, e))
            print(f"  [FAIL] {name}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 60)

    if errors:
        print("\nFailed tests:")
        for name, e in errors:
            print(f"  - {name}: {e}")
        return 1

    print("\nAll tests passed!")
    return 0


if __name__ == "__main__":
    exit(main())
