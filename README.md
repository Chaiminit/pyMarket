# pyMarket — 永续金融市场模拟引擎

## 项目概述

pyMarket 是一个以**永续（Perpetual）**为核心设计理念的金融市场模拟引擎。它通过**反射性做市商（Reflexive Market Maker, RMM）**、**债券永续借贷系统**和**实时清算机制**，构建了一个无需到期交割、可无限连续运转的金融市场。

本项目的核心创新在于：**所有交易本质上都是永续的**——无论是现货交易、借贷还是股份发行，都通过统一的引擎节点和套利机制实现价格的持续发现和流动性的自我维持。

---

## 核心架构

### 1. 引擎节点系统（EngineNode）

所有市场参与者继承自 `EngineNode` 基类，形成统一的模拟时钟：

- **Token** — 可交易资产（BTC、ETH、USDT 等）
- **TradingPair** — 普通交易对（订单簿 + AMM 双轨制）
- **BondTradingPair** — 债券交易对（永续借贷市场）
- **Trader** — 交易者（资产 + 债券持仓）
- **Corp** — 股份公司（IPO + 分红）
- **ReflexiveMarketMaker** — 反射性做市商

每个节点实现 `step(dt)` 方法，由 `MarketEngine` 以固定帧率统一驱动，实现真正的时间连续模拟。

### 2. 反射性做市商（RMM）— 永续流动性的核心

RMM 是 pyMarket 实现"永续交易"的关键机制。它不是一个传统的独立 AMM，而是**嵌入在订单簿内部的反射性价格跟踪器**。

#### 核心公式：恒定乘积模型

```
k = reserve_base × reserve_quote
price_amm = reserve_quote / reserve_base
```

#### 反射性套利机制

每次订单撮合后，RMM 立即执行套利：

1. **检测价格偏离**：比较 AMM 隐含价格 `price_amm` 与订单簿共识价格 `consensus_price`
2. **计算精确套利量**：
   ```
   target_base = √(k / consensus_price)
   ```
3. **通过订单簿套利**：
   - 若 `price_amm < consensus_price`：AMM 从订单簿买入 base，推高 AMM 价格
   - 若 `price_amm > consensus_price`：AMM 向订单簿卖出 base，压低 AMM 价格
4. **储备更新**：严格保持 `k` 不变，使用积分定价精确计算

#### 滑点补偿费 — 冷启动与永续资金积累

RMM 不依赖外部 LP 提供流动性，而是通过**滑点成本补偿机制**自我积累资金：

- **限价单撮合**：买卖双方各付一半手续费，按滑点成本动态计算
- **市价单执行**：Taker 和所有 Makers 按成交量比例分担
- **手续费率限制**：`min_fee_rate ≤ fee_rate ≤ max_fee_rate`（默认 0.001% ~ 0.1%）
- **手续费注入池子**：收取的 base/quote 代币直接加入 AMM 储备，增加 `k`

这意味着：**市场交易量越大，RMM 流动性越深，系统越稳定**——一个自我强化的永续循环。

#### 积分定价 — 大额交易的精确处理

当市价单触及 AMM 时，使用微积分方法计算价格影响：

```
买入 Δbase 时支付的 quote = ∫(k / (R_base - x)²)dx from 0 to Δbase
                            = k × (1/(R_base - Δbase) - 1/R_base)
```

这确保了大额交易不会破坏恒定乘积不变量，实现了**连续价格曲线上的永续交易**。

### 3. 债券系统 — 永续借贷市场

pyMarket 的债券系统实现了**无固定期限的永续借贷**：

- **正债券（+）**：债权——借出资金，持续收取利息
- **负债券（-）**：债务——借入资金，持续支付利息
- **债券交易**：债权/债务可以在二级市场自由转让

#### 利息结算机制

引擎每步调用 `settle_interest_simple()`，按秒级精度结算：

```
总利息 = 有效债券基数 × 年化利率 × dt(秒) / 31536000
```

- 有效债券基数 = min(总债权, 总债务)
- 债务人按比例支付，债权人按比例收取
- 资不抵债的债务人触发清算

这创造了一个**永续滚动的借贷市场**，没有到期日，没有交割，利息实时流付。

### 4. 股份公司（Corp）— 永续股权发行

`Corp` 继承自 `Trader`，代表发行股票的公司：

- **IPO 卖单**：创建时自动提交不可撤销的一级市场卖单
- **股份代币**：自动生成对应的 `Token` 作为股票
- **增发机制**：`issue_shares()` 可随时增加总股本
- **分红功能**：`distribute_dividend()` 按持股比例实时分配利润

股份一旦发行就在二级市场永续流通，没有退市机制，形成一个**永续的股权交易市场**。

### 5. 破产清算系统 — 永续市场的安全阀

`LiquidationEngine` 确保系统在任何情况下都能自我净化：

1. **资不抵债检测**：`net_assets < 0` 时触发
2. **取消所有订单**：释放冻结资金
3. **债券清算**：
   - 债务用资产补偿债权人
   - 债权按比例豁免对应债务人
4. **坏账记录**：无法覆盖的缺口记为 `bad_debt_written_off`

清算后的交易者资产归零，但市场继续运转——**永续市场不会因为个体破产而停止**。

---

## 项目结构

```
pyMarket/
├── core/
│   ├── __init__.py          # 包初始化与核心类导出
│   ├── engine.py            # MarketEngine: 市场引擎协调中心
│   ├── engine_node.py       # EngineNode: 所有节点基类
│   ├── trading_pair.py      # TradingPair: 普通交易对（订单簿 + RMM 接入）
│   ├── bond_pair.py         # BondTradingPair: 债券交易对（永续借贷）
│   ├── rmm.py               # ReflexiveMarketMaker: 反射性做市商
│   ├── trader.py            # Trader: 交易者（资产 + 债券 + 订单）
│   ├── corp.py              # Corp: 股份公司（IPO + 分红）
│   ├── token.py             # Token: 代币定义
│   ├── order.py             # Order / BondOrder: 订单类型
│   ├── liquidation.py       # LiquidationEngine: 破产清算
│   └── utils.py             # Decimal 精度工具
├── tests/
│   └── test_rmm_integration.py  # RMM 完整功能测试套件
├── example.py               # 使用示例（基础交易 / IPO / 市场模拟）
├── pyproject.toml           # 项目配置
├── requirements.txt         # 依赖
└── setup.py                 # 安装脚本
```

---

## 快速开始

### 安装

```bash
pip install -e .
```

### 基础示例

```python
from core.engine import MarketEngine, get_engine, reset_engine

# 重置并获取引擎
reset_engine()
engine = get_engine()

# 创建代币
usdt = engine.create_token("USDT", is_quote=True)
btc = engine.create_token("BTC")

# 创建交易对
pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

# 创建交易者并分配资产
alice = engine.create_trader("Alice")
engine.allocate_assets(alice, usdt, 1000000)
engine.allocate_assets(alice, btc, 10)

# 提交限价单
alice.submit_limit_order(pair, "buy", 49000, 1.0)

# 执行市价单
vol, details, fee = alice.submit_market_order(pair, "sell", 0.5)

# 运行市场模拟（结算利息、清算等）
engine.step()
```

### 运行示例

```bash
python example.py
```

---

## "永续"设计哲学

### 为什么一切都是永续的？

| 传统市场 | pyMarket 永续设计 |
|---------|------------------|
| 期货有到期交割日 | 无到期，价格通过 RMM 永续跟踪 |
| 借贷有固定期限 | 债券无期限，利息按秒实时结算 |
| LP 需要主动做市 | RMM 通过套利自我维持流动性 |
| 股权有退市风险 | 股份永续流通，分红实时分配 |
| 清算导致市场中断 | 自动清算，市场连续运转 |

### 永续性的三层保障

1. **价格永续**：RMM 套利确保 AMM 价格始终反射订单簿共识
2. **流动性永续**：滑点补偿费自动注入池子，交易量越大流动性越深
3. **市场永续**：引擎节点统一时钟，清算系统自我净化，市场永不停止

---

## 关键设计细节

### 双轨制交易

- **订单簿**：提供价格发现，撮合限价单
- **AMM**：提供保底流动性，执行市价单剩余部分

当订单簿深度不足时，市价单自动路由到 RMM 池，确保**任何规模的交易都能成交**。

### 共识价格机制

```python
def update_consensus_price(self):
    best_buy = self.buy_orders[0].price if self.buy_orders else None
    best_sell = self.sell_orders[0].price if self.sell_orders else None

    if best_buy and best_sell:
        self.consensus_price = (best_buy + best_sell) / 2
    elif best_buy:
        self.consensus_price = best_buy
    elif best_sell:
        self.consensus_price = best_sell
```

共识价格是 RMM 套利的锚定点，也是整个市场的"真实价格"。

### 线程安全

- 所有订单簿操作受 `threading.Lock` 保护
- 撮合过程是原子操作
- 支持多线程并发访问

---

## 测试

运行完整测试套件：

```bash
python tests/test_rmm_integration.py
```

测试覆盖：
- 引擎与 RMM 初始化
- 冷启动流动性积累
- 限价单撮合 + RMM 套利
- 市价单执行 + RMM 接管
- 滑点补偿费与手续费率限制
- 多交易对共享 RMM
- 共识价格跟踪
- 恒定乘积不变量验证
- 边界条件与异常场景
- 完整交易生命周期

---

## 技术栈

- **Python 3.8+**
- **Decimal** — 高精度金融计算，避免浮点误差
- **threading** — 订单簿并发安全
- **dataclasses** — 清算结果结构化

---

## 扩展方向

- **预言机接入**：将 `consensus_price` 替换为外部预言机喂价
- **杠杆交易**：在 Trader 中增加保证金仓位管理
- **多签治理**：Corp 增发和分红添加治理流程
- **链上部署**：将 RMM 逻辑移植到智能合约

---

## 许可证

MIT License
