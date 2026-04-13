import math
from random import random, uniform, choice
from typing import List
from decimal import Decimal, getcontext

import utils
from trader import Trader
from finance import Token, TradingPair

# 设置decimal精度
getcontext().prec = 28

chip_distribution = utils.ChipDistribution(0.05, 1, alpha=0.6)


class RandomBot(Trader):
    """随机交易机器人，继承自Trader类"""

    def __init__(self, name: str, trend: float, view: float):
        super().__init__(name)
        self.trading_pairs: List[TradingPair] = []
        self.view: Decimal = Decimal(str(view))  # 视图时间（使用Decimal）
        self.trend: Decimal = Decimal(str(trend))  # 趋势因子（使用Decimal）
        self.k = 0

    def set_trading_pairs(self, trading_pairs: List[TradingPair]) -> None:
        """设置机器人可以交易的交易对"""
        self.trading_pairs = trading_pairs

    def act(self):
        """执行随机交易动作"""
        if not self.trading_pairs:
            return False

        if len(self.orders) > 10 * len(self.trading_pairs):
            self.orders[0].close()

        # 先处理所有普通交易对，计算k的平均值
        for trading_pair in self.trading_pairs:
            action = random()
            if trading_pair.log:
                # 使用二分查找找到视图时间范围内的起始索引
                current_time = trading_pair.log[-1][0]
                target_time = current_time - float(self.view)

                # 二分查找找到第一个时间大于等于target_time的索引
                left, right = 0, len(trading_pair.log) - 1
                start_index = 0

                while left <= right:
                    mid = (left + right) // 2
                    if trading_pair.log[mid][0] >= target_time:
                        start_index = mid
                        right = mid - 1
                    else:
                        left = mid + 1

                # 确保start_index在有效范围内
                start_index = min(start_index, len(trading_pair.log) - 1)

                # 使用Decimal进行精确计算
                current_price = Decimal(str(trading_pair.log[-1][1]))
                start_price = Decimal(str(trading_pair.log[start_index][1]))
                price_change = (current_price - start_price) / start_price / self.view * self.trend
                new_k = utils.sigmoid((utils.sigmoid(float(price_change)) - 0.5) * 10) - 0.5
                # print(new_k - self.k, self.k)
                sh = 0.5 + random() * 0.5
                action = action * 0.6 + float((new_k - self.k) * sh + new_k * (1 - sh))
                # print(action)
                ra = (random() + 3) / 4
                self.k = self.k * ra + new_k * (1 - ra)

            try:
                if action < 0.1:
                    self._place_market_order(trading_pair, "sell")
                elif action < 0.5:
                    self._place_limit_order(trading_pair, "sell")
                elif action < 0.9:
                    self._place_limit_order(trading_pair, "buy")
                else:
                    self._place_market_order(trading_pair, "buy")
            except Exception as e:
                print(f"机器人{self.name}普通交易失败: {e}")

    def _place_limit_order(self, trading_pair: TradingPair, direction: str) -> bool:
        """下单限价单"""
        if direction == "buy":
            # 买入：基于持有计价代币的1%-20%
            d = random()
            ori_price = float(trading_pair.price)
            price = ori_price * (1 + d * math.e ** (-d / 0.05))
            quote_amount = float(self.assets.get(trading_pair.quote_token, Decimal("0")))
            if quote_amount <= 0:
                return False
            max_volume = (
                quote_amount
                * chip_distribution.pdf(abs((price - ori_price) / ori_price))
                * (random() + 3)
                / 4
                / price
            )

        else:  # 'sell'
            # 卖出：基于持有基准代币的1%-20%
            d = random()
            ori_price = float(trading_pair.price)
            price = ori_price * (1 - d * math.e ** (-d / 0.05))
            base_amount = float(self.assets.get(trading_pair.base_token, Decimal("0")))
            if base_amount <= 0:
                return False
            max_volume = (
                base_amount
                * chip_distribution.pdf(abs((price - ori_price) / ori_price))
                * (random() + 3)
                / 4
            )

        volume = max(0.0001, max_volume)  # 确保交易量不为0
        # 提交订单
        order = self.submit(trading_pair, direction, price, volume)
        return order is not None

    def _place_market_order(self, trading_pair: TradingPair, direction: str) -> bool:
        """下单市价单"""
        # 根据持有资产计算随机交易量（市价单交易量较小）
        if direction == "buy":
            # 买入：基于持有计价代币的0.5%-5%
            quote_amount = float(self.assets.get(trading_pair.quote_token, Decimal("0")))
            if quote_amount <= 0:
                return False

            max_volume = quote_amount * (1 - random() ** 3.5) / float(trading_pair.price)
        else:  # 'sell'
            # 卖出：基于持有基准代币的0.5%-5%
            base_amount = float(self.assets.get(trading_pair.base_token, Decimal("0")))
            if base_amount <= 0:
                return False
            max_volume = base_amount * (1 - random() ** 3.5)

        volume = max(0.000001, max_volume)  # 确保交易量不为0

        # 提交市价单
        # print((1 if direction == 'buy' else -1) * volume)
        return self.submit_market(trading_pair, direction, volume)


class BotManager:
    """机器人管理器，用于管理多个交易机器人"""

    def __init__(self):
        self.bots: List[RandomBot] = []

    def __len__(self) -> int:
        """返回机器人数量"""
        return len(self.bots)

    def create_bots_batch(
        self,
        count: int,
        asset_configs: dict,
        name_prefix: str = "Bot",
        trend: float = 0,
        view: float = 10,
    ) -> List[RandomBot]:
        """
        批量创建机器人并分配多种资产

        Args:
            count: 要创建的机器人数量
            asset_configs: 资产配置字典，格式为 {Token: {"min": min_amount, "max": max_amount}}
            name_prefix: 机器人名称前缀
        """
        for i in range(count):
            # 创建机器人实例
            bot = RandomBot(
                f"{name_prefix}_{i+1:03d}",
                trend=trend * (-0.7 + random() * 1.5),
                view=view * (0.1 + random() * 1.9),
            )

            # 为每个代币分配随机资产
            for token, config in asset_configs.items():
                min_amount = config["min"]
                max_amount = config["max"]

                # 在最小值和最大值之间随机分配资产（将Decimal转换为float）
                amount = uniform(float(min_amount), float(max_amount))
                bot.add_asset(token, amount)

            self.bots.append(bot)

        return self.bots

    def create_bots(
        self,
        count: int,
        base_token: Token,
        quote_token: Token,
        initial_base_amount: float = 1.0,
        initial_quote_amount: float = 1000.0,
    ) -> List[RandomBot]:
        """创建指定数量的机器人（兼容旧版本）"""
        # 转换为新的资产配置格式
        asset_configs = {
            base_token: {"min": initial_base_amount * 0.9, "max": initial_base_amount * 1.1},
            quote_token: {"min": initial_quote_amount * 0.9, "max": initial_quote_amount * 1.1},
        }

        return self.create_bots_batch(count, asset_configs, "Bot")

    def set_trading_pairs(self, trading_pairs: List[TradingPair]) -> None:
        """为所有机器人设置可交易的交易对"""
        for bot in self.bots:
            bot.set_trading_pairs(trading_pairs)

    def step(self, trading_pairs: List[TradingPair]) -> None:
        """让所有机器人执行一步交易动作"""
        # 先设置交易对
        self.set_trading_pairs(trading_pairs)

        # 每个机器人执行交易动作
        for bot in self.bots:
            # 随机决定是否执行交易（70%概率）
            if random() < 0.7:
                bot.act()

    def get_average_asset_value(self, token: Token) -> float:
        """获取所有机器人某代币的平均持有量"""
        if not self.bots:
            return 0.0

        total = sum(bot.get_asset_value(token) for bot in self.bots)
        return total / len(self.bots)
