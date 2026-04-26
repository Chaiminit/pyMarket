# pyMarket

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

一个功能强大的Python市场模拟库，支持交易机器人、债券交易对、股份公司系统、治理投票系统和实时GUI可视化。

## 架构设计

pyMarket 采用模块化、层次化的架构设计，以引擎节点系统为核心，实现了高度可扩展的市场模拟系统。

### 核心架构层次

1. **引擎层** - MarketEngine 作为核心协调中心
   - 管理所有核心对象的生命周期
   - 提供统一的交易接口
   - 处理市场模拟步进
   - 协调各模块之间的交互

2. **节点层** - EngineNode 基类
   - 所有核心对象的统一基类
   - 提供标准的 step() 接口
   - 自动注册到引擎的节点列表
   - 支持自定义扩展

3. **业务层** - 核心功能模块
   - 交易对系统（TradingPair）
   - 债券交易系统（BondTradingPair）
   - 交易者系统（Trader）
   - 股份公司系统（Corp）
   - 治理投票系统（GovernanceProposal, GovernanceSystem）
   - 破产清算系统（LiquidationEngine）

4. **工具层** - 辅助功能
   - 高精度计算（Decimal）
   - 手续费系统（FeeConfig）
   - 订单系统（Order）
   - 代币系统（Token）

### 引擎节点系统

引擎节点系统是 pyMarket 的核心设计，它通过以下机制实现了高度的模块化和可扩展性：

- **自动注册**：所有继承自 EngineNode 的对象在创建时自动注册到引擎
- **统一接口**：所有节点实现标准的 step(dt) 方法，由引擎统一调用
- **事件驱动**：通过回调机制实现节点间的通信
- **可扩展性**：支持自定义节点类型和行为

### 数据流设计

1. **初始化流程**：
   - 创建 MarketEngine 实例
   - 创建 Token（代币）
   - 创建 TradingPair/BondTradingPair（交易对）
   - 创建 Trader/Corp（交易者/公司）
   - 分配初始资产

2. **交易流程**：
   - 交易者提交订单
   - 交易对执行撮合
   - 手续费计算和收取
   - 资产更新

3. **模拟流程**：
   - Engine.step() 被调用
   - 计算时间步长 dt
   - 调用所有节点的 step(dt) 方法
   - 结算债券利息
   - 处理破产清算

## 核心功能模块

### 1. 市场引擎（MarketEngine）
- 统一的市场协调中心
- 支持自定义类型和扩展
- 提供 run() 方法实现一键运行
- 内置破产清算机制

### 2. 交易对系统（TradingPair）
- 线程安全的订单撮合
- 支持限价单和市价单
- 灵活的手续费配置
- 支持按金额下单

### 3. 债券交易系统（BondTradingPair）
- 支持债券发行和交易
- 自动利息结算
- 线程安全的订单处理
- 支持不同利率的债券

### 4. 股份公司系统（Corp）
- IPO 发行和股份代币创建
- 增发股份和价格管理
- 分红系统
- 市值计算

### 5. 治理投票系统
- 加权投票机制
- 多选项提案支持
- 参与率检查
- 投票结果统计

### 6. 手续费系统
- Maker/Taker 费率支持
- 自定义手续费接收者
- 特殊费率规则（如 VIP 折扣）
- 手续费统计

### 7. 破产清算系统
- 资不抵债检测
- 债券清算
- 资产分配
- 坏账核销

## 功能示例

### 基础市场模拟

```python
from core import MarketEngine

# 创建市场引擎
engine = MarketEngine()

# 创建代币
usdt = engine.create_token("USDT", is_quote=True)
btc = engine.create_token("BTC")

# 创建交易对
pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

# 创建交易者
trader = engine.create_trader("Alice")
engine.allocate_assets(trader, btc, 10.0)

# 执行交易
trader.submit_market_order(pair, "sell", 1.0)

# 运行引擎（60 FPS）
# engine.run(fps=60.0)
```

### 债券交易示例

```python
from core import get_engine

engine = get_engine()
usdt = engine.create_token("USDT", is_quote=True)

# 创建债券交易对（年化利率 5%）
bond_pair = engine.create_bond_trading_pair("USDT", 0.05)

# 创建交易者
lender = engine.create_trader("Lender")
borrower = engine.create_trader("Borrower")

# 分配资产
engine.allocate_assets(lender, usdt, 10000.0)

# 出借人借出资金（获得正债券）
lender.submit_bond_limit_order(bond_pair, "buy", 0.05, 5000.0)

# 借款人借入资金（获得负债券）
borrower.submit_bond_limit_order(bond_pair, "sell", 0.05, 5000.0)

# 运行引擎，自动结算利息
# engine.run(fps=30.0)
```

### 股份公司（IPO）示例

```python
from core import get_engine

engine = get_engine()
usdt = engine.create_token("USDT", is_quote=True)

# 创建股份公司（IPO）
company = engine.create_corp(
    name="TechCorp",
    total_shares=1000000,    # 发行100万股
    initial_price=10.0,       # 每股10 USDT
    quote_token=usdt
)

# 获取交易对和股份代币
trading_pair, share_token = company.get_trading_info()

# 投资者购买股份
investor = engine.create_trader("Investor")
engine.allocate_assets(investor, usdt, 50000.0)
investor.submit_market_order(trading_pair, "buy", 1000.0)

# 公司增发股份
new_total = company.issue_shares(500000.0)  # 增发50万股

# 分红
engine.allocate_assets(company, usdt, 10000.0)
dividend_record = company.distribute_dividend(
    dividend_token=usdt,
    total_amount=10000.0,
    all_traders=engine.traders
)
```

### 治理投票示例

```python
from core import get_engine, GovernanceProposal

engine = get_engine()

# 创建股东
shareholder1 = engine.create_trader("Shareholder1")
shareholder2 = engine.create_trader("Shareholder2")
shareholder3 = engine.create_trader("Shareholder3")

# 创建治理提案（加权投票）
proposal = GovernanceProposal(
    title="是否增发100万股",
    description="公司计划增发100万股用于扩张",
    creator=shareholder1,
    options=["同意", "反对", "弃权"],
    participants={
        shareholder1: 0.5,   # 50% 权重
        shareholder2: 0.3,   # 30% 权重
        shareholder3: 0.2    # 20% 权重
    },
    min_participation_rate=0.6  # 最低60%参与率
)

# 投票
proposal.cast_vote(shareholder1, "同意")
proposal.cast_vote(shareholder2, "同意")
proposal.cast_vote(shareholder3, "反对")

# 统计结果
result = proposal.tally_votes()
print(f"获胜选项: {result['winner']}")
print(f"参与率: {result['participation_rate']:.2%}")
print(f"是否有效: {result['is_valid']}")
```

### 手续费系统示例

```python
from core import get_engine, FeeConfig, FeeDirection

engine = get_engine()
usdt = engine.create_token("USDT", is_quote=True)
btc = engine.create_token("BTC")

# 创建手续费接收者（如平台账户）
platform = engine.create_trader("Platform")

# 创建手续费配置
fee_config = FeeConfig(
    maker_rate=0.001,      # Maker 0.1%
    taker_rate=0.002,      # Taker 0.2%
    direction=FeeDirection.BOTH,
    fee_recipient=platform  # 手续费支付给平台
)

# 创建带手续费的交易对
pair = engine.create_trading_pair("BTC", "USDT", 50000.0, fee_config=fee_config)

# 进行交易，手续费自动计算和收取
alice = engine.create_trader("Alice")
engine.allocate_assets(alice, usdt, 100000.0)
alice.submit_market_order(pair, "buy", 1.0)

# 查看平台收到的手续费
print(f"平台手续费收入: {platform.assets.get(usdt, 0)} USDT")
```

## 快速开始

### 安装

```bash
# 从PyPI安装
pip install pymarket

# 或从源码安装
git clone https://github.com/Chaiminit/pyMarket.git
cd pyMarket
pip install -e .
```

### 基本使用

```python
from core import get_engine

# 获取引擎实例
engine = get_engine()

# 创建代币
usdt = engine.create_token("USDT", is_quote=True)
btc = engine.create_token("BTC")

# 创建交易对
pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

# 创建交易者
trader = engine.create_trader("Alice")
engine.allocate_assets(trader, usdt, 100000.0)

# 提交订单
trader.submit_market_order(pair, "buy", 1.0)

# 运行引擎
# engine.run(fps=60.0)
```

## 项目结构

```
pyMarket/
├── core/                   # 核心模块
│   ├── __init__.py
│   ├── engine.py          # 市场引擎
│   ├── trading_pair.py    # 交易对
│   ├── bond_pair.py       # 债券交易对
│   ├── trader.py          # 交易者
│   ├── corp.py            # 股份公司
│   ├── governance.py      # 治理投票系统
│   ├── liquidation.py     # 破产清算系统
│   ├── fees.py            # 手续费系统
│   ├── order.py           # 订单系统
│   ├── token.py           # 代币定义
│   ├── engine_node.py     # 引擎节点基类
│   └── utils.py           # 工具函数
├── gui/                    # GUI模块
│   ├── __init__.py
│   ├── charts.py          # K线图
│   └── trader_gui.py      # 交易界面
├── example.py              # 示例代码
├── pyproject.toml          # 项目配置
├── setup.py               # 安装脚本
└── README.md              # 项目说明
```

## 技术特性

- **高精度计算**：使用 Decimal 28位精度，避免浮点数精度问题
- **并发安全**：撮合引擎采用线程锁保护，支持多线程并发交易
- **可扩展性**：基于引擎节点系统，支持自定义扩展
- **模块化设计**：清晰的层次结构，易于维护和扩展
- **实时模拟**：支持指定帧率的实时市场模拟

## 开发指南

### 代码规范

本项目使用Black进行代码格式化：

```bash
black . --line-length 100
```

### 运行测试

```bash
pytest
```

### 类型检查

```bash
mypy core/
```

## 贡献指南

1. Fork本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建Pull Request

## 许可证

本项目采用MIT许可证 - 详见 [LICENSE](LICENSE) 文件

## 联系方式

- 项目主页: https://github.com/Chaiminit/pyMarket
- 问题反馈: https://github.com/Chaiminit/pyMarket/issues

## 致谢

感谢所有为本项目做出贡献的开发者！