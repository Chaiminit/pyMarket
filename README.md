# pyMarket

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

一个功能强大的Python市场模拟库，支持交易机器人、债券交易对、股份公司系统、治理投票系统和实时GUI可视化。

## 功能特性

- **市场模拟引擎**：完整的市场模拟系统，支持多种交易对和债券
- **债券交易系统**：支持债券发行、交易和利息结算
- **股份公司系统**：支持IPO发行、股份代币创建、一级市场交易、增发股份和分红
- **治理投票系统**：支持加权投票、多选项治理提案、参与率检查
- **手续费系统**：支持Maker/Taker费率、自定义手续费接收者
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
from core import get_engine, Token, Trader

# 获取市场引擎（单例）
engine = get_engine()

# 创建代币
usdt = engine.create_token("USDT", is_quote=True)
btc = engine.create_token("BTC")

# 创建交易对
pair = engine.create_trading_pair("BTC", "USDT", 50000.0)

# 创建交易者
trader = engine.create_trader("Alice")
engine.allocate_assets(trader, btc, 10.0)

print(f"Alice 总资产: {trader.get_total_assets(usdt)}")
```

### 债券交易示例

```python
from core import get_engine

engine = get_engine()
usdt = engine.create_token("USDT", is_quote=True)

# 创建债券交易对
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

print(f"股份代币: {share_token.name}")
print(f"剩余股份: {company.get_remaining_shares()}")
print(f"已募集资金: {company.get_raised_funds()}")
```

### 增发股份示例

```python
# 公司增发股份
new_total = company.issue_shares(500000.0)  # 增发50万股
print(f"增发后总股本: {new_total}")

# 以新价格增发
company.issue_shares(200000.0, issue_price=15.0)

# 查看公司持股
print(f"公司持股: {company.get_company_owned_shares()}")
print(f"市值: {company.get_market_cap()} USDT")

# 出售增发的股份
company.submit_limit_order(trading_pair, "sell", 12.0, 100000.0)
```

### 分红示例

```python
# 给公司分配利润
engine.allocate_assets(company, usdt, 10000.0)

# 分红：公司拿出10000 USDT按持股比例分配
dividend_record = company.distribute_dividend(
    dividend_token=usdt,
    total_amount=10000.0,
    all_traders=engine.traders
)

# 查看分红结果
for holder, amount in dividend_record.items():
    print(f"{holder.name} 获得分红: {amount} USDT")
```

### 治理投票示例

```python
from core import get_engine, GovernanceProposal, GovernanceSystem

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

# 使用治理系统管理多个提案
governance = GovernanceSystem()
prop1 = governance.create_proposal(
    title="预算审批",
    description="Q2预算100万",
    creator=shareholder1,
    options=["同意", "反对"],
    participants={shareholder1: 0.6, shareholder2: 0.4}
)

# 获取活跃提案
active_proposals = governance.get_active_proposals()
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
alice.submit_market_order(pair, "buy", 1.0)

# 查看平台收到的手续费
print(f"平台手续费收入: {platform.assets.get(usdt, 0)} USDT")
```

### 按金额下单示例

```python
# 方式1：按标的代币数量下单（原有方式）
alice.submit_market_order(pair, "buy", 1.0)  # 买入 1 BTC

# 方式2：按计价代币金额下单（新增）
alice.submit_market_order_by_quote(pair, "buy", 50000.0)  # 花费 50000 USDT
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
    def create_trading_pair(self, base_name: str, quote_name: str, 
                           initial_price: float, fee_config: Optional[FeeConfig] = None) -> TradingPair
    def create_bond_trading_pair(self, token_name: str, 
                                  initial_rate: float, fee_config: Optional[FeeConfig] = None) -> BondTradingPair
    
    # 交易者管理
    def create_trader(self, name: str) -> Trader
    def create_corp(self, name: str, total_shares: float, 
                    initial_price: float, quote_token: Token) -> Corp
    def allocate_assets(self, trader: Trader, token: Token, amount: float)
    
    # 手续费统计
    def get_all_collected_fees(self) -> Dict[Token, float]
    
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
    
    # 订单提交
    def submit_limit_order(self, pair: TradingPair, direction: str, 
                          price: float, volume: float) -> bool
    def submit_market_order(self, pair: TradingPair, direction: str, 
                           volume: float) -> Tuple[float, List[Dict], float]
    def submit_market_order_by_quote(self, pair: TradingPair, direction: str,
                                      quote_amount: float) -> Tuple[float, List[Dict], float]
    def submit_bond_limit_order(self, bond_pair: BondTradingPair, direction: str,
                                 interest_rate: float, volume: float) -> bool
```

### Corp

股份公司类，继承自 Trader，用于IPO发行、增发股份和分红。

```python
class Corp(Trader):
    def __init__(self, name: str, total_shares: float, 
                 initial_price: float, quote_token: Token, token_id: int)
    
    # 交易信息
    def get_trading_info(self) -> Tuple[TradingPair, Token]
    def get_remaining_shares(self) -> float
    def get_raised_funds(self) -> float
    
    # 增发股份
    def issue_shares(self, amount: float, issue_price: Optional[float] = None) -> float
    def get_company_owned_shares(self) -> float
    def get_market_cap(self, current_price: Optional[float] = None) -> float
    
    # 分红
    def distribute_dividend(self, dividend_token: Token, 
                           total_amount: float, all_traders: List[Trader]) -> Dict[Trader, float]
    def get_dividend_per_share(self, total_amount: float, 
                               all_traders: List[Trader]) -> float
    def get_share_holders(self, all_traders: List[Trader]) -> Dict[Trader, float]
    def get_circulating_shares(self, all_traders: List[Trader]) -> float
```

### GovernanceProposal

治理提案类，用于创建和管理投票。

```python
class GovernanceProposal:
    def __init__(
        self,
        title: str,
        description: str,
        creator: Trader,
        options: List[str],
        participants: Dict[Trader, float],
        end_time: Optional[datetime] = None,
        min_participation_rate: float = 0.0,
        proposal_id: Optional[str] = None
    )
    
    # 投票
    def cast_vote(self, voter: Trader, option: str) -> bool
    def change_vote(self, voter: Trader, new_option: str) -> bool
    
    # 统计
    def tally_votes(self) -> Dict[str, Any]
    def get_voter_choice(self, voter: Trader) -> Optional[str]
    def has_voted(self, voter: Trader) -> bool
    def get_pending_voters(self) -> List[Trader]
    
    # 状态管理
    def close_voting(self) -> None
    def execute(self) -> None
```

### GovernanceSystem

治理系统类，管理多个治理提案。

```python
class GovernanceSystem:
    def __init__(self)
    
    # 创建提案
    def create_proposal(self, title: str, description: str, creator: Trader,
                       options: List[str], participants: Dict[Trader, float], ...) -> GovernanceProposal
    
    # 查询
    def get_proposal(self, proposal_id: str) -> Optional[GovernanceProposal]
    def get_all_proposals(self) -> List[GovernanceProposal]
    def get_active_proposals(self) -> List[GovernanceProposal]
    def get_proposals_by_status(self, status: VoteStatus) -> List[GovernanceProposal]
    def get_proposals_by_participant(self, participant: Trader) -> List[GovernanceProposal]
    
    # 管理
    def close_expired_proposals(self) -> List[GovernanceProposal]
```

### FeeConfig

手续费配置类。

```python
class FeeConfig:
    def __init__(
        self,
        maker_rate: float = 0.0,          # Maker 费率
        taker_rate: float = 0.0,          # Taker 费率
        fee_type: FeeType = FeeType.PERCENTAGE,
        direction: FeeDirection = FeeDirection.BOTH,
        min_fee: float = 0.0,
        max_fee: Optional[float] = None,
        fee_recipient: Optional[Trader] = None  # 手续费接收者
    )
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
    def submit_limit_order(self, trader: Trader, direction: str, 
                          price: float, volume: float, frozen_amount: float)
    def execute_market_order(self, trader: Trader, direction: str, 
                            volume: float) -> Tuple[float, List[Dict], float]
    def set_fee_config(self, fee_config: FeeConfig) -> None
    def get_fee_config(self) -> FeeConfig
    def get_collected_fees(self, token: Optional[Token] = None) -> float | Dict[Token, float]
```

### BondTradingPair

债券交易对类。

```python
class BondTradingPair:
    def submit_limit_order(self, trader: Trader, direction: str, 
                          interest_rate: float, volume: float, frozen_amount: float)
    def settle_interest_simple(self, traders: Set[Trader], dt: float) -> List[Tuple[Trader, float]]
    def set_fee_config(self, fee_config: FeeConfig) -> None
    def get_fee_config(self) -> FeeConfig
    def get_collected_fees(self, token: Optional[Token] = None) -> float | Dict[Token, float]
```

### LiquidationEngine

破产清算引擎，处理交易者资不抵债时的清算流程。

```python
class LiquidationEngine:
    def check_solvency(self, trader: Trader, quote_token: Optional[Token] = None) -> bool
    def liquidate_trader(self, trader: Trader, price_oracle: Optional[Callable] = None) -> LiquidationResult
    def get_insolvent_traders(self) -> List[Trader]
    def process_all_liquidations(self, price_oracle: Optional[Callable] = None) -> List[LiquidationResult]
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

**治理投票规则：**
- **加权投票**：不同参与者可以有不同的投票权重，权重总和必须等于1
- **多选项支持**：支持二元投票或多元选择
- **参与率检查**：可设置最低参与率要求，未达到则投票无效
- **投票修改**：在投票结束前可以修改投票选择
- **自动过期**：支持设置投票截止时间，到期自动关闭

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
│   └── utils.py           # 工具函数
├── gui/                    # GUI模块
│   ├── __init__.py
│   ├── charts.py          # K线图
│   └── trader_gui.py      # 交易界面
├── doc/                    # 文档
├── example.py              # 示例代码
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
