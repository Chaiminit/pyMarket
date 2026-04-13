"""
Corp 模块 - 股份公司类

提供股份公司的核心功能：
- 继承 Trader 类，作为市场参与者
- 自动生成股份代币
- 自动创建股份/计价代币交易对
- 提交不可撤回的一级市场卖单（IPO）

股份公司机制：
- 初始发行一定数量的股份
- 设定初始发行价格
- 所有股份以限价单形式挂出，不可撤回
- 模拟一级市场（IPO）发行
"""

from typing import Tuple, Optional
from .trader import Trader
from .token import Token
from .trading_pair import TradingPair
from .order import Order


class Corp(Trader):
    """
    股份公司类 - 代表发行股票的公司

    继承自 Trader，与普通交易者的区别：
    - 自动生成股份代币（股票）
    - 自动创建股份交易对
    - 自动提交不可撤回的一级市场卖单

    Attributes:
        name: 公司名称
        share_token: 股份代币（股票）
        trading_pair: 股份交易对
        total_shares: 总股本
        initial_price: 初始发行价
        ipo_order: IPO卖单（不可撤回）

    Examples:
        >>> company = Corp(
        ...     name="TechCorp",
        ...     total_shares=1000000,
        ...     initial_price=10.0,
        ...     quote_token=usdt,
        ...     token_id=100
        ... )
        >>> pair, share_token = company.get_trading_info()
    """

    def __init__(
        self,
        name: str,
        total_shares: float,
        initial_price: float,
        quote_token: Token,
        token_id: int,
    ):
        """
        创建股份公司

        流程：
        1. 调用父类 Trader 的初始化
        2. 创建股份代币（股票）
        3. 创建股份/计价代币交易对
        4. 添加股份到资产持仓
        5. 提交不可撤回的一级市场卖单

        Args:
            name: 公司名称
            total_shares: 初始发行的总股份数
            initial_price: 初始发行价格（以计价代币为单位）
            quote_token: 计价代币（如 USDT）
            token_id: 股份代币的唯一标识符
        """
        super().__init__(name)

        self.total_shares = total_shares
        self.initial_price = initial_price
        self._quote_token = quote_token

        # 创建股份代币
        self.share_token = Token(
            name=f"{name}_SHARE",
            token_id=token_id,
            is_quote=False
        )

        # 创建股份交易对
        self.trading_pair = TradingPair(
            base_token=self.share_token,
            quote_token=quote_token,
            initial_price=initial_price
        )

        # 添加股份到资产持仓
        self.add_asset(self.share_token, total_shares)

        # 提交不可撤回的一级市场卖单
        self.ipo_order = self._submit_ipo_order()

    def _submit_ipo_order(self) -> Order:
        """
        提交 IPO 卖单（不可手动撤回）

        创建一个特殊的限价卖单，设置为不可手动取消。
        这是模拟一级市场的发行行为，但订单仍可通过成交正常完成。

        Returns:
            创建的 IPO 卖单
        """
        # 计算需要冻结的资产数量（卖单冻结基础代币）
        frozen_amount = self.total_shares

        # 创建订单对象
        order = Order(
            trader=self,
            direction="sell",
            price=self.initial_price,
            volume=self.total_shares,
            frozen_amount=frozen_amount,
            pair=self.trading_pair
        )

        # 设置为不可手动取消（但可以通过成交正常完成）
        order.cancellable = False

        # 添加到交易对的卖单簿
        self.trading_pair.sell_orders.append(order)
        # 价格升序，同价格按时间升序
        self.trading_pair.sell_orders.sort(key=lambda x: (x.price, x.time))

        # 添加到交易者的订单列表
        self.orders.append(order)

        return order

    def get_trading_info(self) -> Tuple[TradingPair, Token]:
        """
        获取交易相关信息

        Returns:
            (交易对, 股份代币) 元组
        """
        return self.trading_pair, self.share_token

    def get_remaining_shares(self) -> float:
        """
        获取剩余未售出的股份数量

        Returns:
            剩余股份数量
        """
        if self.ipo_order:
            return self.ipo_order.remaining_volume
        return 0.0

    def get_raised_funds(self) -> float:
        """
        获取已募集的资金总额

        Returns:
            已募集的计价代币金额
        """
        if self.ipo_order:
            sold_shares = self.ipo_order.executed
            return sold_shares * self.initial_price
        return 0.0
