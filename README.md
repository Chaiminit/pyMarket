# pyMarket

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

一个功能强大的Python市场模拟库，支持交易机器人、债券交易对和实时GUI可视化。

## 功能特性

- **市场模拟引擎**：完整的市场模拟系统，支持多种交易对和债券
- **债券交易系统**：支持债券发行、交易和利息结算
- **股份公司系统**：支持IPO发行、股份代币创建和一级市场交易
- **实时GUI界面**：使用PyQt5构建的实时K线图和交易界面
- **玩家模式**：支持人类玩家参与市场交易
- **风险管理**：自动清算和破产处理机制
- **破产清算系统**：完整的资不抵债检测、债券清算、资产分配和坏账核销

## 安装

### 从PyPI安装（推荐）

```bash
pip install pymarket
```

### 从源码安装

```bash
git clone https://github.com/Chaiminit/pyMarket.git
cd pyMarket
pip install -e .
```

### 开发安装

```bash
git clone https://github.com/Chaiminit/pyMarket.git
cd pyMarket
pip install -e ".[dev]"
```

## 快速开始

### 基础示例

```python
from core import MarketEngine, Token, Trader

# 创建市场引擎
engine = MarketEngine()

# 创建代币
usdt = engine.create_token("USDT", is_quote=True)
btc = engine.create_token("BTC")

# 创建交易对
pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

# 创建交易者
trader = engine.create_trader("Alice")
engine.allocate_assets(trader, "BTC", 10.0)

print(f"Alice 总资产: {trader.get_total_assets(usdt)}")
```

### 债券交易示例

```python
from core import MarketEngine

engine = MarketEngine()
usdt = engine.create_token("USDT", is_quote=True)

# 创建债券交易对
bond_pair = engine.create_bond_trading_pair("USDT", 0.05)

# 创建交易者
trader = engine.create_trader("Bob")
engine.allocate_assets(trader, "USDT", 10000.0)

# 添加债券客户
engine.add_bond_client(bond_pair, trader)
```

### 股份公司（IPO）示例

```python
from core import MarketEngine, Corp

engine = MarketEngine()
usdt = engine.create_token("USDT", is_quote=True)

# 创建股份公司（IPO）
company = Corp(
    name="TechCorp",
    total_shares=1000000,    # 发行100万股
    initial_price=10.0,       # 每股10 USDT
    quote_token=usdt,
    token_id=100
)

# 获取交易对和股份代币
trading_pair, share_token = company.get_trading_info()

print(f"股份代币: {share_token.name}")
print(f"剩余股份: {company.get_remaining_shares()}")
print(f"已募集资金: {company.get_raised_funds()}")
```

## API文档

### MarketEngine

市场引擎，处理核心交易逻辑。

```python
class MarketEngine:
    # 代币管理
    def create_token(self, name: str, is_quote: bool = False) -> Token
    def get_token(self, name: str) -> Optional[Token]
    
    # 交易对管理
    def create_trading_pair(self, base_name: str, quote_name: str, initial_price: float) -> TradingPair
    def create_bond_trading_pair(self, token_name: str, initial_rate: float) -> BondTradingPair
    
    # 交易者管理
    def create_trader(self, name: str) -> Trader
    def allocate_assets(self, trader: Trader, token_name: str, amount: float)
    def set_trader_pairs(self, trader: Trader, pairs: List[TradingPair])
    def set_trader_bond_pairs(self, trader: Trader, bond_pairs: List[BondTradingPair])
    
    # 市场模拟
    def step(self) -> None
```

### Trader

交易者类，代表市场参与者。

```python
class Trader:
    def __init__(self, name: str)
    def add_asset(self, token: Token, amount: float)
    def get_total_assets(self, quote_token: Optional[Token] = None) -> float
    def get_net_assets(self, quote_token: Optional[Token] = None) -> float
```

### Corp

股份公司类，继承自 Trader，用于IPO发行。

```python
class Corp(Trader):
    def __init__(
        self,
        name: str,
        total_shares: float,
        initial_price: float,
        quote_token: Token,
        token_id: int
    )
    def get_trading_info(self) -> Tuple[TradingPair, Token]
    def get_remaining_shares(self) -> float
    def get_raised_funds(self) -> float
```

### Token

代币类，代表可交易资产。

```python
class Token:
    def __init__(self, name: str, token_id: int, is_quote: bool = False)
```

### TradingPair

普通交易对类。

```python
class TradingPair:
    def submit_limit_order(self, trader: Trader, direction: str, price: float, volume: float, frozen_amount: float)
    def execute_market_order(self, trader: Trader, direction: str, volume: float) -> Tuple[float, List[Dict]]
```

### BondTradingPair

债券交易对类。

```python
class BondTradingPair:
    def submit_limit_order(self, trader: Trader, direction: str, interest_rate: float, volume: float, frozen_amount: float)
    def settle_interest_simple(self, traders: Set[Trader], dt: float) -> List[Tuple[Trader, float]]
```

### LiquidationEngine

破产清算引擎，处理交易者资不抵债时的清算流程。

```python
class LiquidationEngine:
    def check_solvency(self, trader: Trader, quote_token: Optional[Token] = None) -> bool
    def liquidate_trader(self, trader: Trader, price_oracle: Optional[Callable] = None) -> LiquidationResult
    def get_insolvent_traders(self) -> List[Trader]
```

**清算流程：**
1. 检测净资产是否为负
2. 取消所有未完成订单
3. 清算债券持仓（债务用资产偿还，债权按比例豁免）
4. 按比例分配剩余资产给债权人
5. 记录坏账并清空交易者账户

**债券方向规则：**
- **买单（buy）**：借出资金，支付代币，获得**正债券**（债权）
- **卖单（sell）**：借入资金，获得代币，获得**负债券**（债务）

## 项目结构

```
pyMarket/
├── core/                   # 核心模块
│   ├── __init__.py
│   ├── engine.py          # 市场引擎
│   ├── trading_pair.py    # 交易对
│   ├── bond_pair.py       # 债券交易对
│   ├── trader.py          # 交易者
│   ├── corp.py  # 股份公司 (Corp)
│   ├── liquidation.py     # 破产清算系统
│   ├── order.py           # 订单系统
│   ├── token.py           # 代币定义
│   └── utils.py           # 工具函数
├── gui/                    # GUI模块
│   ├── __init__.py
│   ├── charts.py          # K线图
│   └── trader_gui.py      # 交易界面
├── doc/                    # 文档
├── example.py              # 示例代码
├── test_liquidation_branches.py  # 破产清算分支测试
├── pyproject.toml          # 项目配置
├── setup.py               # 安装脚本
└── README.md              # 项目说明
```

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
