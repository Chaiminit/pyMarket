from pipes import quote
import time
from decimal import Decimal, getcontext
from errors import TokenError
from config import FundConfig, TradingConfig, BankruptcyConfig

getcontext().prec = 28  # 设置Decimal运算精度

tokens = []
quote_T = None


def calculate_token_price(source_token, target_token, trading_pairs):
    """
    使用广度优先搜索法计算一个代币相对于另一个代币的价格

    Args:
        source_token: 要查询价格的代币
        target_token: 目标计价代币
        trading_pairs: 交易对列表

    Returns:
        Decimal: 1个source_token相对于target_token的价格，找不到返回None
    """
    if source_token == target_token:
        return Decimal("1")

    from collections import deque

    visited = set()
    queue = deque()

    visited.add(id(source_token))
    queue.append((source_token, Decimal("1")))

    while queue:
        current_token, current_price = queue.popleft()

        for pair in trading_pairs:
            if not isinstance(pair, TradingPair):
                continue

            if pair.base_token == current_token:
                next_token = pair.quote_token
                next_price = current_price * pair.price

                if id(next_token) not in visited:
                    if next_token == target_token:
                        return next_price
                    visited.add(id(next_token))
                    queue.append((next_token, next_price))

            if pair.quote_token == current_token:
                next_token = pair.base_token
                if pair.price > 0:
                    next_price = current_price / pair.price

                if id(next_token) not in visited:
                    if next_token == target_token:
                        return next_price
                    visited.add(id(next_token))
                    queue.append((next_token, next_price))

    return None


def calculate_assets_value(assets, trading_pairs, target_token=None):
    """
    计算资产总价值，使用广度优先搜索法

    Args:
        assets: 资产字典 {token: amount}
        trading_pairs: 交易对列表
        target_token: 目标计价代币，默认使用全局计价代币

    Returns:
        Decimal: 资产总价值
    """
    if target_token is None:
        target_token = quote_T

    total_value = Decimal("0")

    for token, amount in assets.items():
        if token == target_token:
            total_value += amount
        else:
            price = calculate_token_price(token, target_token, trading_pairs)
            if price is not None:
                total_value += amount * price

    return total_value


class Token:
    def __init__(self, name, is_quote=False):
        global quote_T
        if is_quote and any(token.is_quote for token in tokens):
            raise TokenError("Only one quote token is allowed")
        self.name = name
        self.is_quote = is_quote
        tokens.append(self)
        if is_quote:
            quote_T = self

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if not isinstance(other, Token):
            return False
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)


class FundShare(Token):
    """基金份额类，继承自Token"""

    def __init__(self, fund_name):
        super().__init__(f"{fund_name} Share", is_quote=False)
        self.fund_name = fund_name


class Order:
    def __init__(self, owner, trading_pair, direction, expected_volume, price, timestamp):
        self.owner = owner
        self.trading_pair = trading_pair
        self.direction = direction
        self.expected_volume = Decimal(expected_volume)
        self.unfilled_volume = Decimal(expected_volume)
        self.price = Decimal(price)
        self.total_frozen = (
            (self.price * self.expected_volume) if direction == "buy" else self.expected_volume
        )
        self.remaining_frozen = self.total_frozen
        self.timestamp = timestamp

    def close(self):
        if self in self.owner.orders:
            self.owner.orders.remove(self)

        if self.direction == "buy":
            if self in self.trading_pair.buy_queue:
                self.trading_pair.buy_queue.remove(self)
        else:
            if self in self.trading_pair.sell_queue:
                self.trading_pair.sell_queue.remove(self)

        token = (
            self.trading_pair.quote_token
            if self.direction == "buy"
            else self.trading_pair.base_token
        )
        self.owner.assets[token] = self.owner.assets.get(token, Decimal(0)) + self.remaining_frozen


class Trader:
    def __init__(self, name):
        self.name = name
        self.assets = {}
        self.orders = []
        self.is_bankrupt = False
        self.controlled_funds = []  # 存储控制的基金列表

    def submit_order(self, trading_pair, direction, volume, price):
        if direction not in ("buy", "sell"):
            return False

        required_token = trading_pair.quote_token if direction == "buy" else trading_pair.base_token
        required_amount = (
            Decimal(price) * Decimal(volume) if direction == "buy" else Decimal(volume)
        )

        if self.assets.get(required_token, Decimal(0)) < required_amount:
            return False

        self.assets[required_token] = self.assets.get(required_token, Decimal(0)) - required_amount

        order = Order(
            owner=self,
            trading_pair=trading_pair,
            direction=direction,
            expected_volume=volume,
            price=price,
            timestamp=time.time(),
        )

        self.orders.append(order)
        trading_pair.submit_order(order)
        return True

    def submit_market_order(self, trading_pair, direction, volume):
        volume = Decimal(volume)
        target_queue = trading_pair.sell_queue if direction == "buy" else trading_pair.buy_queue
        cumulative_volume = Decimal(0)
        price_level = Decimal(0)
        remaining_volume = volume

        # 遍历对手方队列寻找价格点位
        for order in target_queue:
            if direction == "buy" and order.price > price_level:
                price_level = order.price
            elif direction == "sell" and (price_level == 0 or order.price < price_level):
                price_level = order.price

            available = min(order.unfilled_volume, remaining_volume)
            cumulative_volume += available
            remaining_volume -= available

            if cumulative_volume >= volume:
                break

        if cumulative_volume == 0:
            return False, "市场深度不足，无可用订单"

        # 使用找到的价格点位提交限价单
        actual_volume = min(volume, cumulative_volume)
        success = self.submit_order(
            trading_pair=trading_pair, direction=direction, price=price_level, volume=actual_volume
        )
        if success:
            return True, "市价单成交"
        else:
            return False, "资金不足或持仓不足"

    def submit_primary_market_order(self, trading_pair, direction, volume):
        """在一级市场提交订单，直接支付股价买入基金股份"""
        if direction != "buy":
            return False, "一级市场只支持买入操作"

        # 检查交易对是否为一级市场
        if not trading_pair.is_primary_market:
            return False, "此交易对不是一级市场交易对"

        # 确保交易对基础资产是FundShare类型
        if not isinstance(trading_pair.base_token, FundShare):
            return False, "一级市场交易对必须基于FundShare资产"

        # 查找对应的Fund对象
        fund = None
        for client in trading_pair.clients:
            if (
                isinstance(client, Fund)
                and hasattr(client, "share_token")
                and client.share_token == trading_pair.base_token
            ):
                fund = client
                break

        if not fund:
            return False, "未找到对应的基金对象"

        # 检查基金是否已上市
        if fund.is_listed:
            return False, "基金已上市，不能在一级市场购买"

        # 检查是否有足够的IPO份额
        if Decimal(volume) > fund.ipo_shares:
            # 调整为购买所有剩余份额
            volume = fund.ipo_shares

        # 计算需要支付的金额
        cost = Decimal(volume) * trading_pair.price

        # 检查是否有足够的资金
        if self.assets.get(trading_pair.quote_token, Decimal(0)) < cost:
            return False, "资金不足"

        # 扣除资金
        self.assets[trading_pair.quote_token] -= cost
        # 将资金添加到基金资产
        fund.assets[trading_pair.quote_token] = (
            fund.assets.get(trading_pair.quote_token, Decimal(0)) + cost
        )
        # 分配股份给交易者
        if trading_pair.base_token not in self.assets:
            self.assets[trading_pair.base_token] = Decimal("0")
        self.assets[trading_pair.base_token] += Decimal(volume)

        # 更新基金股东记录
        if self in fund.shareholders:
            fund.shareholders[self] += Decimal(volume)
        else:
            fund.shareholders[self] = Decimal(volume)

        # 触发控制权检查
        fund.update_shareholders()

        # 更新基金剩余IPO份额
        fund.ipo_shares -= Decimal(volume)

        # 确保剩余IPO份额不为负数
        if fund.ipo_shares < Decimal("0"):
            fund.ipo_shares = Decimal("0")

        # 检查是否达到上市条件（所有一级市场份额已认购）
        if fund.ipo_shares <= Decimal("0"):
            fund.is_listed = True
            # 将交易对标记为非一级市场
            trading_pair.is_primary_market = False
            return True, "一级市场购买成功，基金已上市"

        return True, "一级市场购买成功"

    def calculate_total_assets(self, trading_pairs, target_token=None):
        """
        计算总资产，包括被冻结在订单中的资产

        Args:
            trading_pairs: 交易对列表
            target_token: 目标计价代币，默认使用全局计价代币

        Returns:
            Decimal: 资产总价值
        """
        if target_token is None:
            target_token = quote_T

        total_assets = calculate_assets_value(self.assets, trading_pairs, target_token)

        # 加上被冻结在订单中的资产
        for order in self.orders:
            if order.direction == "buy":
                # 买单被冻结的是计价货币
                frozen_token = order.trading_pair.quote_token
                frozen_amount = order.remaining_frozen
            else:
                # 卖单被冻结的是基础货币
                frozen_token = order.trading_pair.base_token
                frozen_amount = order.remaining_frozen

            if frozen_token == target_token:
                total_assets += frozen_amount
            else:
                price = calculate_token_price(frozen_token, target_token, trading_pairs)
                if price is not None:
                    total_assets += frozen_amount * price

        # 检查是否破产或资产为负
        if total_assets <= BankruptcyConfig.BANKRUPTCY_THRESHOLD and not self.is_bankrupt:
            # 记录凭空补充的金额
            if not hasattr(self, "total_supplemented"):
                self.total_supplemented = Decimal("0")

            # 计算需要补充的金额
            supplement_amount = BankruptcyConfig.ASSET_SUPPLEMENT_AMOUNT - total_assets
            self.total_supplemented += supplement_amount

            # 将资产设置为补充金额
            # 找到计价货币并补充资金
            for pair in trading_pairs:
                if hasattr(pair, "quote_token") and pair.quote_token.is_quote:
                    quote_token = pair.quote_token
                    # 在资产中添加补充的金额
                    if quote_token not in self.assets:
                        self.assets[quote_token] = Decimal("0")
                    self.assets[quote_token] += supplement_amount
                    # 更新全局差额
                    # 使用已有的TradingPair类引用
                    for p in trading_pairs:
                        if isinstance(p, TradingPair):
                            p.__class__.global_balance_shortfall += supplement_amount
                            break
                    # 重新计算总资产
                    total_assets = BankruptcyConfig.ASSET_SUPPLEMENT_AMOUNT
                    break
        return total_assets

    def create_fund(self, name, capital, total_shares, quote_token, trading_pairs):
        """创建一个新的基金"""
        # 创建基金对象
        fund = Fund(name, self, capital, total_shares, quote_token)
        # 添加基金交易对到市场
        trading_pairs.append(fund.trading_pair)
        return fund

    def calculate_net_assets(self, trading_pairs):
        """计算净资产(换算为全局计价货币)"""
        return self.calculate_total_assets(trading_pairs, quote_T)

    def calculate_leverage(self, trading_pairs):
        """计算杠杆率(总资产/净资产)"""
        total_assets = self.calculate_total_assets(trading_pairs)
        net_assets = self.calculate_net_assets(trading_pairs)

        # 防止除零错误
        if net_assets == Decimal("0"):
            return Decimal("0")

        # 计算杠杆率
        leverage = total_assets / net_assets
        return leverage

    def is_over_leveraged(self, trading_pairs, max_leverage=10000):
        """移除破产杠杆倍率限制，始终返回False"""
        return False

    def get_investable_funds(self, trading_pairs):
        """获取可投资的基金列表"""
        investable_funds = []
        for pair in trading_pairs:
            if isinstance(pair, TradingPair) and isinstance(pair.base_token, FundShare):
                # 查找对应的Fund对象
                for client in pair.clients:
                    if (
                        isinstance(client, Fund)
                        and hasattr(client, "share_token")
                        and client.share_token == pair.base_token
                    ):
                        investable_funds.append(client)
                        break
        return investable_funds

    def try_buy_fund_control(self, fund, trading_pairs):
        """尝试购买足够多的基金份额以成为最高持股人"""
        # 在新的机制下，控制权自动属于最高持股人
        # 这里改为尝试购买足够多的份额来成为最高持股人
        # 计算当前需要购买的份额以成为最高持股人
        current_max_shares = 0
        for shareholder, shares in fund.shareholders.items():
            if shareholder != self and shares > current_max_shares:
                current_max_shares = shares

        # 计算需要购买的份额以超过当前最高持股人
        shares_needed = current_max_shares - fund.shareholders.get(self, 0) + 1

        if shares_needed > 0:
            # 检查是否有足够的资金购买这些份额
            needed_funds = shares_needed * fund.trading_pair.price
            if self.balances.get(fund.quote_token, Decimal("0")) >= needed_funds:
                # 尝试购买这些份额
                from .order import Order, OrderType, OrderSide

                order = Order(
                    order_type=OrderType.MARKET,
                    side=OrderSide.BUY,
                    pair=fund.trading_pair,
                    amount=Decimal(str(shares_needed)),
                    price=fund.trading_pair.price,
                )

                # 简化处理，假设购买成功
                self.balances[fund.quote_token] -= needed_funds

                # 更新持股数量
                fund.shareholders[self] = fund.shareholders.get(self, 0) + shares_needed

                # 检查并自动更新控制权
                fund.update_shareholders()

                # 检查是否成功成为控制人
                if fund.controlling_shareholder == self and fund not in self.controlled_funds:
                    self.controlled_funds.append(fund)
                    return True, f"已购买足够份额成为{fund.name}基金的控制人"
                else:
                    return True, f"已购买{shares_needed}股{fund.name}基金份额，但尚未成为控制人"
            else:
                return (
                    False,
                    f"资金不足，需要{needed_funds} {fund.quote_token.name}才能购买足够份额",
                )
        else:
            # 已经是最高持股人
            if fund.controlling_shareholder == self and fund not in self.controlled_funds:
                self.controlled_funds.append(fund)
                return True, f"您已经是{fund.name}基金的控制人"
            else:
                return True, f"您已经持有足够份额成为{fund.name}基金的最高持股人"

    def try_sell_fund_control(self, fund, trading_pairs):
        """在新机制下，不再需要出售控制权，控制权会自动转移"""
        return True, f"控制权机制已变更：控制权自动属于最高持股人，不再需要手动出售控制权"

    def manage_controlled_funds(self, trading_pairs):
        """管理控制的基金"""
        for fund in self.controlled_funds:
            # 检查是否仍然控制该基金
            if fund.controlling_shareholder != self:
                self.controlled_funds.remove(fund)
                continue


class TradingPair:
    # 类级别的全局差额跟踪
    global_balance_shortfall = Decimal("0")
    # 基础手续费率
    base_fee_rate = Decimal("0.001")  # 0.1%
    # 最大手续费率
    max_fee_rate = Decimal("0.02")  # 2%

    def __init__(
        self,
        name,
        base_token,
        quote_token,
        initial_price,
        data_retention_seconds=None,
        is_primary_market=False,
    ):
        self.name = name
        self.base_token = base_token
        self.quote_token = quote_token
        self.price = Decimal(initial_price)
        self.records = []
        # 使用配置文件中的数据保留时间
        self.data_retention_seconds = (
            data_retention_seconds
            if data_retention_seconds is not None
            else TradingConfig.DATA_RETENTION_SECONDS
        )
        self.buy_queue = []
        self.sell_queue = []
        self.clients = []
        self.is_primary_market = is_primary_market  # 添加一级市场标记

    def get_current_fee_rate(self):
        """获取当前基于全局差额的手续费率"""
        if TradingPair.global_balance_shortfall > Decimal("0"):
            # 计算手续费调整因子 - 差额越大，手续费越高，但不超过最大手续费率
            # 差额大于1000时，手续费达到最大值
            adjustment_factor = min(
                TradingPair.global_balance_shortfall / Decimal("1000"), Decimal("1")
            )
            return TradingPair.base_fee_rate + adjustment_factor * (
                TradingPair.max_fee_rate - TradingPair.base_fee_rate
            )
        # 当全局差额为0时，不收取手续费
        return Decimal("0")

    def submit_order(self, order):
        if order.direction == "buy":
            self._insert_to_queue(order, self.buy_queue, reverse=True)
        else:
            self._insert_to_queue(order, self.sell_queue, reverse=False)
        self.match_orders()

    def _insert_to_queue(self, order, queue, reverse):
        price_order = -order.price if reverse else order.price
        for i, existing in enumerate(queue):
            current_price = -existing.price if reverse else existing.price
            if (price_order < current_price) or (
                price_order == current_price and order.timestamp < existing.timestamp
            ):
                queue.insert(i, order)
                return
        queue.append(order)

    def match_orders(self):
        while self.buy_queue and (
            self.sell_queue or (hasattr(self, "is_primary_market") and self.is_primary_market)
        ):
            top_buy = self.buy_queue[0]

            # 检查是否为一级市场交易对
            if hasattr(self, "is_primary_market") and self.is_primary_market:
                # 查找对应的Fund对象
                fund = None
                for client in self.clients:
                    if (
                        isinstance(client, Fund)
                        and hasattr(client, "share_token")
                        and client.share_token == self.base_token
                    ):
                        fund = client
                        break

                if fund and not fund.is_listed and fund.ipo_shares > Decimal("0"):
                    # 一级市场交易，直接使用Fund的IPO份额
                    trade_volume = min(top_buy.unfilled_volume, fund.ipo_shares)
                    trade_price = top_buy.price

                    # 更新订单状态
                    top_buy.unfilled_volume -= trade_volume
                    top_buy.remaining_frozen -= trade_volume * trade_price

                    # 如果订单已完成，关闭订单
                    if top_buy.unfilled_volume <= Decimal("0"):
                        top_buy.close()

                    # 检查基金是否已上市
                    if fund.ipo_shares <= Decimal("0"):
                        fund.is_listed = True
                        self.is_primary_market = False
                        # 清除所有一级市场订单
                        self.buy_queue = []
                        self.sell_queue = []
                        break

                    continue

            # 普通市场交易处理
            if not self.sell_queue:
                break

            top_sell = self.sell_queue[0]

            if top_buy.price < top_sell.price:
                break

            # 其余代码保持不变
            trade_price = (
                top_buy.price if top_buy.timestamp < top_sell.timestamp else top_sell.price
            )
            trade_volume = min(top_buy.unfilled_volume, top_sell.unfilled_volume)

            # 检查是否是基金份额交易
            is_fund_share_trade = isinstance(self.base_token, FundShare)
            fund = None
            if is_fund_share_trade:
                # 查找对应的Fund对象
                for client in self.clients:
                    if (
                        isinstance(client, Fund)
                        and hasattr(client, "share_token")
                        and client.share_token == self.base_token
                    ):
                        fund = client
                        break

            # 计算手续费
            current_fee_rate = self.get_current_fee_rate()
            fee_amount = trade_volume * trade_price * current_fee_rate

            # 处理买方
            buyer_paid = trade_price * trade_volume
            buyer_frozen_used = top_buy.price * trade_volume
            buyer_refund = buyer_frozen_used - buyer_paid

            # 实际支付金额（包含手续费）
            actual_buyer_paid = buyer_paid + fee_amount

            # 处理卖方
            seller_receive = buyer_paid  # 卖方获得的是不含手续费的金额

            # 更新买家资产 - 只添加购买的基础货币，不再次扣除报价货币
            # 因为报价货币已经在submit_order时被冻结扣除了
            top_buy.owner.assets[self.base_token] = (
                top_buy.owner.assets.get(self.base_token, Decimal(0)) + trade_volume
            )

            # 更新卖家资产
            if self.quote_token in top_sell.owner.assets:
                top_sell.owner.assets[self.quote_token] += seller_receive
            else:
                top_sell.owner.assets[self.quote_token] = seller_receive

            # 更新订单状态
            top_buy.unfilled_volume -= trade_volume
            top_buy.remaining_frozen -= buyer_frozen_used
            top_sell.unfilled_volume -= trade_volume
            top_sell.remaining_frozen -= trade_volume

            # 更新交易对价格 - 保持为Decimal类型
            self.price = trade_price

            # 将手续费加入全局差额池，用于填补之前的资金补充，不打印输出
            if fee_amount > Decimal("0"):
                TradingPair.global_balance_shortfall = max(
                    Decimal("0"), TradingPair.global_balance_shortfall - fee_amount
                )

            # 更新基金股东信息
            if is_fund_share_trade and fund:
                # 更新卖家的持股数量
                if top_sell.owner in fund.shareholders:
                    fund.shareholders[top_sell.owner] -= trade_volume
                    # 如果持股数量为0，移除该股东
                    if fund.shareholders[top_sell.owner] <= Decimal("0"):
                        del fund.shareholders[top_sell.owner]

                # 更新买家的持股数量
                if top_buy.owner not in fund.shareholders:
                    fund.shareholders[top_buy.owner] = Decimal("0")
                fund.shareholders[top_buy.owner] += trade_volume

                # 触发控制权检查
                fund.update_shareholders()

                # 检查并更新控股人
                fund.check_controlling_share()

            # 保存当前价格用于检测价格下跌
            prev_price = self.price

            # 移除多余的价格更新逻辑，避免类型转换错误

            # 检测价格下跌并进行杠杆率检查
            if hasattr(self, "price") and hasattr(self, "records") and len(self.records) > 1:
                # 转换为Decimal进行比较，避免浮点数精度问题
                current_price_decimal = Decimal(str(self.records[-1][1]))
                prev_price_decimal = Decimal(str(self.records[-2][1]))

                if current_price_decimal < prev_price_decimal:
                    # 使用Decimal计算价格变化百分比
                    price_change = (current_price_decimal - prev_price_decimal) / prev_price_decimal
                    # 如果价格下跌超过3%，检查交易双方的杠杆率（降低阈值以更早响应）
                    if price_change < Decimal("-0.03"):
                        # 收集所有参与交易的交易者
                        involved_traders = set()

                        # 从订单队列中收集交易者
                        for order in list(self.buy_queue) + list(self.sell_queue):
                            if hasattr(order, "owner"):
                                involved_traders.add(order.owner)

                        # 添加当前成交的买卖双方
                        involved_traders.add(top_buy.owner)
                        involved_traders.add(top_sell.owner)

                        # 检查所有相关交易者的杠杆率
                        for trader in involved_traders:
                            if not getattr(trader, "is_bankrupt", False):
                                # 优先使用is_over_leveraged方法
                                if hasattr(trader, "trading_pairs") and hasattr(
                                    trader, "is_over_leveraged"
                                ):
                                    if trader.is_over_leveraged(trader.trading_pairs):
                                        trader.declare_bankruptcy(trader.trading_pairs)
                                # 兼容旧版本的检查
                                elif hasattr(trader, "trading_pairs") and hasattr(
                                    trader, "calculate_leverage"
                                ):
                                    leverage = trader.calculate_leverage(trader.trading_pairs)
                                    if leverage > Decimal("10"):
                                        trader.declare_bankruptcy(trader.trading_pairs)

            current_time = time.time()
            # 存储为float进行数据处理，但确保self.price始终是Decimal
            self.records.append([current_time, float(trade_price), float(trade_volume)])
            # 只保留指定时间窗口内的记录
            cutoff_time = current_time - self.data_retention_seconds
            # 过滤并保留时间窗口内的记录
            self.records = [r for r in self.records if r[0] >= cutoff_time]

            # 关闭已完成订单
            if top_buy.unfilled_volume == 0:
                top_buy.close()
            if top_sell.unfilled_volume == 0:
                top_sell.close()

    def update(self, dt):
        # 根据全局差额调整手续费率，但不打印输出
        pass


class Fund(Trader):
    """基金类，继承自Trader类"""

    def __init__(
        self, name, controlling_shareholder, capital, total_shares, quote_token, trading_pairs=None
    ):
        super().__init__(f"Fund_{name}")
        self.name = name
        self.controlling_shareholder = controlling_shareholder  # 主控股者
        self.total_shares = Decimal(total_shares)  # 总股数
        self.initial_capital = Decimal(capital)  # 初始资本
        self.quote_token = quote_token  # 计价货币

        # 确保assets字典已初始化
        if not hasattr(self, "assets"):
            self.assets = {}

        # 创建基金份额token
        self.share_token = FundShare(name)

        # 计算初始股价
        self.initial_price = self.initial_capital / self.total_shares

        # 记录历史最高价格（用于回撤检测）
        self.highest_price = self.initial_price

        # 创建交易对
        self.trading_pair = TradingPair(
            name=f"{self.share_token.name}/{quote_token.name}",
            base_token=self.share_token,
            quote_token=quote_token,
            initial_price=self.initial_price,
            is_primary_market=True,  # 标记为一级市场
        )

        # 将基金添加到交易对的clients列表中（修复一级市场买入错误的关键）
        self.trading_pair.clients.append(self)

        # 通过函数参数添加交易对避免循环引用
        if trading_pairs is not None:
            if self.trading_pair not in trading_pairs:
                trading_pairs.append(self.trading_pair)

        # 分配股份
        # 主控股人获得默认50%的股份（可配置）
        controlling_shares = self.total_shares * FundConfig.CONTROLLING_SHARE_RATIO
        # 一级市场可供认购的股份（可配置）
        self.ipo_shares = self.total_shares * FundConfig.IPO_SHARES_RATIO
        self.ipo_price = self.initial_price  # IPO价格

        # 冻结主控股人的资金并分配股份
        # 从主控股人资产中扣除初始资本的对应比例
        controlling_capital = self.initial_capital * FundConfig.CONTROLLING_SHARE_RATIO
        if controlling_shareholder.assets.get(quote_token, Decimal(0)) >= controlling_capital:
            controlling_shareholder.assets[quote_token] -= controlling_capital
            # 将资金添加到基金资产
            self.assets[quote_token] = (
                self.assets.get(quote_token, Decimal(0)) + controlling_capital
            )
            # 给主控股人分配股份
            if self.share_token not in controlling_shareholder.assets:
                controlling_shareholder.assets[self.share_token] = Decimal("0")
            controlling_shareholder.assets[self.share_token] += controlling_shares

        self.is_listed = False  # 是否已上市
        self.shareholders = {controlling_shareholder: controlling_shares}  # 股东及其持股数量
        self.ipo_orders_placed = False  # 一级市场订单是否已放置
        self.ipo_order_amount = Decimal("0")  # 已放置的一级市场订单数量

    def auto_place_ipo_orders(self, trading_pairs=None):
        """自动在一级市场下卖单"""
        if self.ipo_orders_placed or self.is_listed:
            return False, "一级市场订单已放置或基金已上市"

        # 计算当前股价（基于基金净资产）
        if trading_pairs:
            # 使用净资产计算股价（以基金自己的计价货币计价）
            net_assets = self.calculate_total_assets(trading_pairs, self.quote_token)
            current_price = net_assets / self.total_shares
        else:
            # 使用初始价格
            current_price = self.initial_price

        # 设置IPO价格
        self.ipo_price = current_price

        # 确保基金有足够的份额用于出售
        if self.share_token not in self.assets:
            self.assets[self.share_token] = Decimal("0")

        # 一级市场交易不需要实际放置卖单，而是通过submit_primary_market_order直接处理
        # 这里只需要标记IPO订单已放置
        self.ipo_orders_placed = True
        self.ipo_order_amount = self.ipo_shares

        return True, f"成功设置一级市场IPO，可出售{self.ipo_shares}份额，价格为{current_price}"

    def subscribe_shares(self, subscriber, amount):
        """在一级市场认购基金份额"""
        if self.is_listed:
            return False, "基金已上市，不能在一级市场认购"

        # 计算需要支付的金额
        cost = amount * self.ipo_price

        # 检查认购者是否有足够的资金
        if subscriber.assets.get(self.quote_token, Decimal(0)) < cost:
            return False, "资金不足"

        # 检查是否有足够的IPO份额
        if amount > self.ipo_shares:
            # 调整为购买所有剩余份额
            amount = self.ipo_shares

        # 扣除认购者的资金
        subscriber.assets[self.quote_token] -= cost
        # 将资金添加到基金资产
        self.assets[self.quote_token] = self.assets.get(self.quote_token, Decimal(0)) + cost
        # 分配股份给认购者
        if self.share_token not in subscriber.assets:
            subscriber.assets[self.share_token] = Decimal("0")
        subscriber.assets[self.share_token] += amount
        # 更新剩余IPO份额
        self.ipo_shares -= amount
        # 更新股东记录
        if subscriber in self.shareholders:
            self.shareholders[subscriber] += amount
        else:
            self.shareholders[subscriber] = amount

        # 检查是否达到上市条件（所有一级市场份额已认购）
        if self.ipo_shares <= Decimal("0"):
            self.is_listed = True
            # 将交易对标记为非一级市场
            if hasattr(self, "trading_pair"):
                self.trading_pair.is_primary_market = False
            return True, "认购成功，基金已上市"

        return True, "认购成功"

    def buyback_shares(self, amount):
        """基金回购自己的份额来调控股价"""
        if not self.is_listed:
            return False, "基金尚未上市"

        # 计算回购所需资金
        cost = amount * self.trading_pair.price

        # 检查基金是否有足够的资金
        if self.assets.get(self.quote_token, Decimal(0)) < cost:
            return False, "基金资金不足"

        # 尝试从市场回购份额
        # 这里简化处理，直接减少总股数
        self.total_shares -= amount

        # 扣除基金资金
        self.assets[self.quote_token] -= cost

        # 由于简化处理，这里不实际从股东手中购买份额
        # 触发控制权检查
        self.update_shareholders()

        return True, "回购成功"

    def raise_funds(self, amount):
        """基金通过融资增加资本，以调控股价"""
        # 向主控股人融资
        # 检查主控股人是否有足够的资金
        if self.controlling_shareholder.assets.get(self.quote_token, Decimal(0)) < amount:
            return False, "主控股人资金不足"

        # 扣除主控股人的资金
        self.controlling_shareholder.assets[self.quote_token] -= amount
        # 将资金添加到基金资产
        self.assets[self.quote_token] = self.assets.get(self.quote_token, Decimal(0)) + amount

        # 增加总股数，稀释现有股东权益
        new_shares = amount / self.trading_pair.price
        self.total_shares += new_shares

        # 将新股份分配给主控股人
        self.controlling_shareholder.assets[self.share_token] += new_shares
        self.shareholders[self.controlling_shareholder] += new_shares

        # 触发控制权检查
        self.update_shareholders()

        return True, "融资成功"

    def _get_total_shares_with_frozen(self, shareholder):
        """获取股东的总持股数（包括实际持有和订单中冻结的）"""
        # 实际持有的股份
        total_shares = self.shareholders.get(shareholder, Decimal(0))

        # 加上订单中冻结的股份（卖单中冻结的基金份额）
        if hasattr(shareholder, "orders"):
            for order in shareholder.orders:
                # 检查是否是卖出本基金股份的订单
                # 订单如果在队列中就是活跃的（未成交完的）
                if (
                    order.trading_pair == self.trading_pair
                    and order.direction == "sell"
                    and order.unfilled_volume > 0
                ):
                    total_shares += order.remaining_frozen

        return total_shares

    def check_controlling_share(self):
        """检查当前控制人是否仍然是最高持股人（包含冻结订单）"""
        if not self.shareholders:
            return True

        # 获取当前控制人的总持股数（包含冻结）
        controlling_shares = self._get_total_shares_with_frozen(self.controlling_shareholder)

        # 检查是否有其他股东持股数更多（包含冻结）
        for shareholder in self.shareholders.keys():
            if shareholder != self.controlling_shareholder:
                shareholder_total = self._get_total_shares_with_frozen(shareholder)
                if shareholder_total > controlling_shares:
                    return False

        return True

    def transfer_control(self, new_controller):
        """转移基金控制权"""
        # 检查新控制者是否在股东列表中
        if new_controller not in self.shareholders:
            return False, "不是基金股东，无法获得控制权"

        # 保存旧控制人
        old_controller = self.controlling_shareholder

        # 转移控制权
        self.controlling_shareholder = new_controller

        # 更新旧控制人的受控基金列表
        if (
            old_controller
            and hasattr(old_controller, "controlled_funds")
            and self in old_controller.controlled_funds
        ):
            old_controller.controlled_funds.remove(self)

        # 如果新控制者是基金，则创建一个新的神经网络机器人来操作这个子基金
        if isinstance(new_controller, Fund):
            # 局部导入AIFundBot类，解决循环导入问题
            from bot import AIFundBot

            # 创建新的神经网络机器人
            bot_name = f"SubFundBot_{self.name}_{int(time.time())%1000}"
            bot = AIFundBot(bot_name, view=30)

            # 给机器人分配一些基础资产
            if hasattr(self, "quote_token"):
                bot.assets[self.quote_token] = Decimal("0")

            # 将子基金添加到机器人的受控基金列表
            bot.controlled_funds.append(self)

            # 将机器人设置为子基金的实际操作者
            self.actual_operator = bot

            # 查找并添加到AI基金机器人管理器，确保机器人能被触发运行
            from bot import AIFundBotManager

            if "ai_fund_bot_manager" in globals():
                ai_fund_bot_manager.bots.append(bot)
                # print(f"已为基金 {self.name} 创建神经网络机器人 {bot_name} 作为实际操作者，并添加到AI基金机器人管理器")
            else:
                # print(f"已为基金 {self.name} 创建神经网络机器人 {bot_name} 作为实际操作者")
                pass
        else:
            # 更新新控制人的受控基金列表
            if (
                new_controller
                and hasattr(new_controller, "controlled_funds")
                and self not in new_controller.controlled_funds
            ):
                new_controller.controlled_funds.append(self)

        return True, "控制权转移成功"

    def prepare_control_sale(self):
        """准备出售基金控制权"""
        # 检查是否为主控股人
        # 这里需要通过调用方确保是主控股人在操作
        # 控制权不再与特定持股比例挂钩，而是与最高持股人身份挂钩
        # 因此不需要计算特定的控股股份数量
        return Decimal("0"), Decimal("0")

    def try_buy_control(self, buyer):
        """尝试购买基金控制权 - 该方法已废弃，控制权现在基于最高持股人身份自动转移"""
        return False, "控制权机制已变更，现在基于最高持股人身份自动转移"

    def update_shareholders(self):
        """更新股东记录，这应该在每次交易后调用（包含冻结订单）"""
        # 检查主控股人是否仍然是最高持股人
        if not self.check_controlling_share():
            # 寻找新的最高持股人（排除基金自己，包含冻结订单）
            max_shares = Decimal(0)
            new_controller = None

            for shareholder in self.shareholders.keys():
                # 排除基金自己成为自己的控股者
                if shareholder == self:
                    continue
                # 获取包含冻结订单的总持股数
                total_shares = self._get_total_shares_with_frozen(shareholder)
                if total_shares > max_shares:
                    max_shares = total_shares
                    new_controller = shareholder

            # 如果找到新的最高持股人，转移控制权
            if new_controller and new_controller != self.controlling_shareholder:
                self.transfer_control(new_controller)
        else:
            # 确保当前控股人已将基金添加到其controlled_funds列表
            if (
                self.controlling_shareholder
                and hasattr(self.controlling_shareholder, "controlled_funds")
                and self not in self.controlling_shareholder.controlled_funds
            ):
                self.controlling_shareholder.controlled_funds.append(self)

    def trade(self, trading_pairs):
        """基金交易方法"""
        # 检查基金是否已被清算（交易对被删除）
        if not hasattr(self, "trading_pair") or self.trading_pair is None:
            return

        # 检查是否需要销毁基金（价格低于发行价50%或从最高点回撤超过50%）
        should_liquidate, reason = self._should_liquidate(trading_pairs)
        if should_liquidate:
            self._liquidate_fund(trading_pairs, reason)
            return

        # 如果有实际操作者（机器人），则委托给它进行交易
        if hasattr(self, "actual_operator") and self.actual_operator:
            # 让机器人为基金进行交易决策，传入基金自身作为交易者
            self.actual_operator.trade(trading_pairs, trader=self)

        # 清理过期订单
        self._cleanup_orders(trading_pairs)

    def _should_liquidate(self, trading_pairs=None):
        """检查基金是否应该被销毁（价格低于发行价阈值或从最高点回撤超过50%）"""
        current_price = self.trading_pair.price

        # 更新历史最高价格
        if current_price > self.highest_price:
            self.highest_price = current_price

        # 条件1：如果当前价格低于发行价阈值（默认50%，可配置），返回True
        if current_price < self.initial_price * FundConfig.LIQUIDATION_PRICE_THRESHOLD:
            return True, f"价格低于发行价{FundConfig.LIQUIDATION_PRICE_THRESHOLD * 100:.0f}%"

        # 条件2：如果从最高点回撤超过阈值（可配置，默认50%），返回True
        if self.highest_price > 0:
            drawdown = (self.highest_price - current_price) / self.highest_price
            if drawdown > FundConfig.LIQUIDATION_DRAWDOWN_THRESHOLD:
                return (
                    True,
                    f"从最高点{self.highest_price:.4f}回撤{drawdown * 100:.2f}%超过{FundConfig.LIQUIDATION_DRAWDOWN_THRESHOLD * 100:.0f}%",
                )

        # 条件3：如果至今利润率低于阈值（可配置，默认-50%），返回True
        current_net_value = self.calculate_total_assets(trading_pairs, self.quote_token)
        if self.initial_capital > 0:
            profit_ratio = (current_net_value - self.initial_capital) / self.initial_capital
            if profit_ratio < FundConfig.LIQUIDATION_PROFIT_RATIO_THRESHOLD:
                return (
                    True,
                    f"至今利润率{profit_ratio * 100:.2f}%低于{FundConfig.LIQUIDATION_PROFIT_RATIO_THRESHOLD * 100:.0f}%",
                )

        # 条件4：如果控股人持股占比低于阈值（可配置，默认5%），返回True
        if self.controlling_shareholder in self.shareholders and self.total_shares > 0:
            controlling_shares = self.shareholders[self.controlling_shareholder]
            controlling_ratio = controlling_shares / self.total_shares
            if controlling_ratio < FundConfig.MIN_CONTROLLING_SHARE_RATIO:
                return (
                    True,
                    f"控股人持股占比{controlling_ratio * 100:.2f}%低于{FundConfig.MIN_CONTROLLING_SHARE_RATIO * 100:.0f}%",
                )

        return False, None

    def calculate_nav_per_share(self, trading_pairs=None):
        """计算每股净值"""
        net_value = self.calculate_total_assets(trading_pairs, self.quote_token)
        if self.total_shares > 0:
            return net_value / self.total_shares
        return Decimal("0")

    def calculate_pb_ratio(self, trading_pairs=None):
        """计算市净率（股价 / 每股净值）"""
        nav_per_share = self.calculate_nav_per_share(trading_pairs)
        current_price = self.trading_pair.price if hasattr(self, "trading_pair") else Decimal("0")
        if nav_per_share > 0:
            return current_price / nav_per_share
        return Decimal("0")

    def get_fund_metrics(self, trading_pairs=None):
        """获取基金关键指标"""
        current_price = self.trading_pair.price if hasattr(self, "trading_pair") else Decimal("0")
        net_asset_value = self.calculate_total_assets(trading_pairs, self.quote_token)
        nav_per_share = self.calculate_nav_per_share(trading_pairs)
        pb_ratio = self.calculate_pb_ratio(trading_pairs)
        market_cap = self.total_shares * current_price

        return {
            "market_cap": market_cap,  # 市值
            "net_asset_value": net_asset_value,  # 净值
            "nav_per_share": nav_per_share,  # 每股净值
            "pb_ratio": pb_ratio,  # 市净率
            "current_price": current_price,  # 当前股价
            "total_shares": self.total_shares,  # 总股数
        }

    def _liquidate_fund(self, trading_pairs=None, reason=None):
        """销毁基金，清算资产"""
        if getattr(self, "is_liquidated", False):
            return  # 已经清算过了，避免重复清算

        if reason:
            print(f"基金 {self.name} {reason}，正在清算...")
        else:
            print(f"基金 {self.name} 触发清算条件，正在清算...")

        # 关闭所有未完成的订单
        for order in list(self.orders):
            order.close()

        # 将基金剩余资产按股份比例分配给股东
        total_shares = self.total_shares
        if total_shares > 0:
            for shareholder, shares in self.shareholders.items():
                # 计算该股东应得的资产比例
                share_ratio = shares / total_shares
                # 分配每种资产
                for token, amount in self.assets.items():
                    distribution = amount * share_ratio
                    if token not in shareholder.assets:
                        shareholder.assets[token] = Decimal("0")
                    shareholder.assets[token] += distribution

        # 从交易对中移除基金
        if self.trading_pair in self.trading_pair.clients:
            self.trading_pair.clients.remove(self)

        # 从全局交易对列表中移除（如果提供了trading_pairs）
        if trading_pairs and self.trading_pair in trading_pairs:
            trading_pairs.remove(self.trading_pair)

        # 标记基金为已清算
        self.is_liquidated = True
        # print(f"基金 {self.name} 已清算完成")

        # 删除对象引用
        try:
            # 从创建者的created_funds中移除
            if hasattr(self, "controlling_shareholder") and self.controlling_shareholder:
                if (
                    hasattr(self.controlling_shareholder, "created_funds")
                    and self in self.controlling_shareholder.created_funds
                ):
                    self.controlling_shareholder.created_funds.remove(self)
                if (
                    hasattr(self.controlling_shareholder, "controlled_funds")
                    and self in self.controlling_shareholder.controlled_funds
                ):
                    self.controlling_shareholder.controlled_funds.remove(self)

            # 删除交易对
            if hasattr(self, "trading_pair") and self.trading_pair:
                # 清理交易对的订单
                if hasattr(self.trading_pair, "buy_queue"):
                    for order in list(self.trading_pair.buy_queue):
                        order.close()
                if hasattr(self.trading_pair, "sell_queue"):
                    for order in list(self.trading_pair.sell_queue):
                        order.close()
                del self.trading_pair

            # 给share_token添加清算标记，然后删除
            if hasattr(self, "share_token") and self.share_token:
                self.share_token.is_liquidated = True
                del self.share_token
        except Exception:
            pass

    def _cleanup_orders(self, trading_pairs):
        """清理过期订单"""
        for order in list(self.orders):
            if order.trading_pair not in trading_pairs:
                order.close()
