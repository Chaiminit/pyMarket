# pyMarket

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

一个功能强大的Python市场模拟库，支持交易机器人、债券交易对和实时GUI可视化。

## 功能特性

- **市场模拟引擎**：完整的市场模拟系统，支持多种交易对和债券
- **智能交易机器人**：基于神经网络的自动化交易策略
- **债券交易系统**：支持债券发行、交易和利息结算
- **实时GUI界面**：使用PyQt5构建的实时K线图和交易界面
- **玩家模式**：支持人类玩家参与市场交易
- **风险管理**：自动清算和破产处理机制

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
from pymarket import Market, MarketConfig

# 配置市场
cfg = MarketConfig()
cfg.name = "测试市场"
cfg.tokens = ["USDT", "ETH", "BTC"]
cfg.quote_token = "USDT"
cfg.trading_pairs = [("ETH", "USDT", 1.0), ("BTC", "USDT", 1.0)]
cfg.bond_pairs = [("USDT", 0.00001), ("ETH", 0.001)]
cfg.bot_count = 100

# 创建并启动市场
market = Market(cfg)
market.start()
```

### 交易者模式

```python
from pymarket import Market, MarketConfig

cfg = MarketConfig()
cfg.name = "交易者市场"
cfg.tokens = ["USDT", "ETH"]
cfg.quote_token = "USDT"
cfg.trading_pairs = [("ETH", "USDT", 1.0)]
cfg.bond_pairs = [("USDT", 0.00001)]
cfg.bot_count = 50
cfg.enable_gui = True

market = Market(cfg)

# 创建玩家
player_id = market.engine.create_player("Player")
market.engine.allocate_assets_to_bot(player_id, "USDT", 100000.0)

market.start_with_trader(player_id)
```

## API文档

### MarketConfig

市场配置类，用于设置市场参数。

```python
class MarketConfig:
    name: str = "市场"                    # 市场名称
    tokens: List[str] = None              # 代币列表
    quote_token: str = "USDT"             # 计价代币
    trading_pairs: List[Tuple] = []       # 交易对配置
    bond_pairs: List[Tuple] = []          # 债券对配置
    bot_count: int = 100                  # 机器人数量
    enable_gui: bool = True               # 启用GUI
    step_interval: float = 0.1            # 步进间隔
```

### Market

市场主类，控制市场运行。

```python
class Market:
    def __init__(self, config: MarketConfig)
    def start(self) -> None                # 启动市场
    def stop(self) -> None                 # 停止市场
    def start_with_trader(self, player_id: int) -> None  # 启动交易者模式
```

### MarketEngine

市场引擎，处理核心交易逻辑。

```python
class MarketEngine:
    def create_player(self, name: str) -> int
    def allocate_assets_to_bot(self, bot_id: int, token: str, amount: float)
    def set_bot_trading_pairs(self, bot_id: int, pair_ids: List[int])
    def place_order(self, pair_id: int, side: str, price: float, volume: float, bot_id: int)
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
│   ├── bot.py             # 交易机器人
│   ├── order.py           # 订单系统
│   └── utils.py           # 工具函数
├── gui/                    # GUI模块
│   ├── __init__.py
│   ├── charts.py          # K线图
│   └── trader_gui.py      # 交易界面
├── doc/                    # 文档
├── example.py              # 示例代码
├── market_framework.py     # 市场框架
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
