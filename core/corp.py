"""
Corp 模块 - 股份公司类

提供股份公司的核心功能：
- 继承 Trader 类，作为市场参与者
- 自动生成股份代币
- 自动创建股份/计价代币交易对
- 提交不可撤回的一级市场卖单（IPO）
- 分红功能：按持股比例分配利润

股份公司机制：
- 初始发行一定数量的股份
- 设定初始发行价格
- 所有股份以限价单形式挂出，不可撤回
- 模拟一级市场（IPO）发行
- 分红：将利润分配给所有持股人
"""

from typing import Tuple, Optional, Dict, List
from decimal import Decimal

from .trader import Trader
from .token import Token
from .trading_pair import TradingPair
from .order import Order
from .utils import to_decimal, D0


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
        total_shares,
        initial_price,
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

        self.total_shares = to_decimal(total_shares)
        self.initial_price = to_decimal(initial_price)
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

    def get_remaining_shares(self) -> Decimal:
        """
        获取剩余未售出的股份数量

        Returns:
            剩余股份数量
        """
        if self.ipo_order:
            return self.ipo_order.remaining_volume
        return D0

    def get_raised_funds(self) -> Decimal:
        """
        获取已募集的资金总额

        Returns:
            已募集的计价代币金额
        """
        if self.ipo_order:
            sold_shares = self.ipo_order.executed
            return sold_shares * self.initial_price
        return D0

    def get_share_holders(self, all_traders: List[Trader]) -> Dict[Trader, Decimal]:
        """
        获取所有股份持有人及其持股数量（不包括公司自己）

        Args:
            all_traders: 市场中所有交易者的列表

        Returns:
            {Trader: 持股数量} 的字典
        """
        holders: Dict[Trader, Decimal] = {}
        for trader in all_traders:
            # 排除公司自己
            if trader is not self:
                shares = trader.assets.get(self.share_token, D0)
                if shares > D0:
                    holders[trader] = shares
        return holders

    def get_circulating_shares(self, all_traders: List[Trader]) -> Decimal:
        """
        获取流通中的股份总数（已售出的股份，不包括公司自己持有的）

        Args:
            all_traders: 市场中所有交易者的列表

        Returns:
            流通股份总数
        """
        total = D0
        for trader in all_traders:
            # 排除公司自己
            if trader is not self:
                total += trader.assets.get(self.share_token, D0)
        return total

    def distribute_dividend(
        self,
        dividend_token: Token,
        total_amount,
        all_traders: List[Trader]
    ) -> Dict[Trader, Decimal]:
        """
        分红 - 按持股比例分配代币给所有股东

        公司从自己的资产中拿出一定数量的代币，
        按照各股东的持股比例进行分配。

        Args:
            dividend_token: 用于分红的代币（如 USDT）
            total_amount: 分红总额
            all_traders: 市场中所有交易者的列表

        Returns:
            {Trader: 分红金额} 的分红记录

        Raises:
            ValueError: 如果公司资产不足以支付分红

        Examples:
            >>> # 公司拿出 10000 USDT 分红
            >>> dividend_record = company.distribute_dividend(
            ...     dividend_token=usdt,
            ...     total_amount=10000.0,
            ...     all_traders=engine.traders
            ... )
            >>> for trader, amount in dividend_record.items():
            ...     print(f"{trader.name} 获得分红: {amount} USDT")
        """
        total_amount = to_decimal(total_amount)

        # 检查公司是否有足够的资产进行分红
        company_balance = self.assets.get(dividend_token, D0)
        if company_balance < total_amount:
            raise ValueError(
                f"公司 {self.name} 资产不足: "
                f"需要 {total_amount} {dividend_token.name}, "
                f"但仅有 {company_balance} {dividend_token.name}"
            )

        # 获取流通中的股份总数
        circulating_shares = self.get_circulating_shares(all_traders)
        if circulating_shares <= D0:
            # 没有流通股份，不进行分红
            return {}

        # 从公司资产中扣除分红总额
        self.assets[dividend_token] = company_balance - total_amount

        # 获取所有股东
        holders = self.get_share_holders(all_traders)

        # 按持股比例分配分红
        dividend_record: Dict[Trader, Decimal] = {}
        for holder, shares in holders.items():
            # 计算持股比例
            ratio = shares / circulating_shares
            # 计算分红金额
            dividend = total_amount * ratio
            # 发放分红
            holder.assets[dividend_token] = holder.assets.get(dividend_token, D0) + dividend
            # 记录
            dividend_record[holder] = dividend

        return dividend_record

    def get_dividend_per_share(
        self,
        total_amount,
        all_traders: List[Trader]
    ) -> Decimal:
        """
        计算每股分红金额

        Args:
            total_amount: 分红总额
            all_traders: 市场中所有交易者的列表

        Returns:
            每股分红金额（如果无流通股份则返回 0）
        """
        total_amount = to_decimal(total_amount)
        circulating_shares = self.get_circulating_shares(all_traders)
        if circulating_shares <= D0:
            return D0
        return total_amount / circulating_shares

    def issue_shares(self, amount, issue_price = None) -> Decimal:
        """
        增发股份 - 公司向自己发行新的股份

        增发会增加公司的总股本，新发行的股份直接添加给公司自己。
        公司可以选择将这些股份逐步出售到市场（通过交易对），
        或用于股权激励、并购等用途。

        Args:
            amount: 增发的股份数量（必须为正数）
            issue_price: 增发价格（可选，用于更新交易对价格参考）

        Returns:
            增发后的公司总股本

        Raises:
            ValueError: 如果增发数量不是正数

        Examples:
            >>> # 增发 100 万股
            >>> new_total = company.issue_shares(1000000.0)
            >>> print(f"增发后总股本: {new_total}")
            >>>
            >>> # 增发并更新发行价格
            >>> company.issue_shares(500000.0, issue_price=15.0)
        """
        amount = to_decimal(amount)
        if amount <= D0:
            raise ValueError(f"增发数量必须为正数，收到: {amount}")

        # 更新总股本
        self.total_shares += amount

        # 将新股份添加给公司自己
        current_shares = self.assets.get(self.share_token, D0)
        self.assets[self.share_token] = current_shares + amount

        # 如果提供了增发价格，更新交易对的当前价格参考
        if issue_price is not None:
            issue_price = to_decimal(issue_price)
            if issue_price > D0:
                self.trading_pair.price = issue_price
                # 同时更新初始价格（作为最新参考）
                self.initial_price = issue_price

        return self.total_shares

    def get_company_owned_shares(self) -> Decimal:
        """
        获取公司自己持有的股份数量（库藏股）

        Returns:
            公司持有的股份数量
        """
        return self.assets.get(self.share_token, D0)

    def get_market_cap(self, current_price = None) -> Decimal:
        """
        计算市值

        Args:
            current_price: 当前股价，None 则使用交易对的当前价格

        Returns:
            总市值（总股本 × 股价）
        """
        if current_price is not None:
            current_price = to_decimal(current_price)
        else:
            current_price = self.trading_pair.price
        return self.total_shares * current_price
