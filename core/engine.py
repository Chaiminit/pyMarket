import time
import math
import random
from typing import List, Dict, Tuple, Optional

from .trading_pair import TradingPair
from .bond_pair import BondTradingPair
from .bot import BotManager
from .trader import Trader


class MarketEngine:
    """市场引擎 - 纯协调层：管理代币/交易对/债券对/机器人生命周期，不包含资产计算逻辑"""

    def __init__(self):
        self.tokens: Dict[str, int] = {}
        self.trading_pairs: Dict[int, TradingPair] = {}
        self.bond_trading_pairs: Dict[int, BondTradingPair] = {}
        self._token_counter = 0
        self._pair_counter = 0
        self._bond_pair_counter = 0
        self._quote_token: Optional[str] = None
        self.bot_manager: Optional[BotManager] = None
        self._step_counter = 0
        self._last_debug_time = 0
        self._initial_token_supply: Dict[str, float] = {}
        self._token_check_interval = 100

    def init_bot_manager(self):
        """初始化机器人管理器"""
        self.bot_manager = BotManager(self)

    # ====== 代币管理 ======

    def create_token(self, name: str, is_quote: bool = False) -> int:
        token_id = self._token_counter
        self.tokens[name] = token_id
        self._token_counter += 1

        if is_quote:
            if self._quote_token is not None:
                raise ValueError(f"全局计价代币已存在: {self._quote_token}")
            self._quote_token = name

        return token_id

    def set_quote_token(self, name: str):
        if name not in self.tokens:
            raise ValueError(f"代币 {name} 不存在")
        if self._quote_token is not None:
            raise ValueError(f"全局计价代币已存在: {self._quote_token}")
        self._quote_token = name

    def get_quote_token(self) -> Optional[str]:
        return self._quote_token

    # ====== 普通交易对 ======

    def create_trading_pair(self, base_token: str, quote_token: str, initial_price: float) -> int:
        pair_id = self._pair_counter
        pair = TradingPair(base_token, quote_token, initial_price)
        pair.pair_id = pair_id
        self.trading_pairs[pair_id] = pair
        self._pair_counter += 1
        return pair_id

    # ====== 债券交易对 ======

    def create_bond_trading_pair(self, token_name: str, initial_rate: float) -> Tuple[int, int]:
        bond_pair_id = self._bond_pair_counter
        bond_pair = BondTradingPair(token_name, initial_rate)
        bond_pair.bond_pair_id = bond_pair_id
        self.bond_trading_pairs[bond_pair_id] = bond_pair
        self._bond_pair_counter += 1
        return (bond_pair_id, bond_pair_id)

    def add_bond_client(self, bond_pair_id: int, trader: Trader):
        if bond_pair_id in self.bond_trading_pairs:
            self.bond_trading_pairs[bond_pair_id].clients.add(id(trader))

    def settle_bond(self, bond_pair_id: int):
        if bond_pair_id in self.bond_trading_pairs:
            all_traders = None
            if self.bot_manager:
                all_traders = list(self.bot_manager.bots.values()) + list(
                    self.bot_manager.players.values()
                )
            self.bond_trading_pairs[bond_pair_id].settle(all_traders)

    # ====== 机器人/玩家管理（委托给BotManager）======

    @property
    def bots(self) -> Dict[int, Trader]:
        result = {}
        if self.bot_manager:
            result.update(self.bot_manager.bots)
            result.update(self.bot_manager.players)
        return result

    def create_bot(self, name: str, trend: float, view: float) -> int:
        if self.bot_manager is None:
            self.init_bot_manager()
        return self.bot_manager.create_bot(name, trend, view)

    def create_player(self, name: str) -> int:
        if self.bot_manager is None:
            self.init_bot_manager()
        return self.bot_manager.create_player(name)

    def allocate_assets_to_bot(self, id: int, token_name: str, amount: float):
        if self.bot_manager:
            entity = self.bot_manager.get_bot(id)
            if entity:
                entity.add_asset(token_name, amount)

    def set_bot_trading_pairs(self, id: int, pair_ids: List[int]):
        if self.bot_manager:
            self.bot_manager.set_trading_pairs(id, pair_ids)

    def set_bot_bond_pairs(self, id: int, bond_pair_ids: List[int]):
        if self.bot_manager:
            self.bot_manager.set_bond_pairs(id, bond_pair_ids)

    def create_bots_batch(
        self,
        count: int,
        asset_configs: Dict[str, Tuple[float, float]],
        name_prefix: str,
        trend: float,
        view: float,
    ) -> List[int]:
        if self.bot_manager is None:
            self.init_bot_manager()
        return self.bot_manager.create_bots_batch(count, asset_configs, name_prefix, trend, view)

    # ====== 市场模拟 ======

    def step(self):
        import time

        self._step_counter += 1

        if self.bot_manager:
            all_entities = list(self.bot_manager.bots.values()) + list(
                self.bot_manager.players.values()
            )
            traders_map = {id(e): e for e in all_entities}

            # 高频利息结算 - 每步直接转移
            # 收集所有无法偿债的债务人
            all_insolvent = {}  # trader_id -> (trader, shortfall, token_name)
            for bp in self.bond_trading_pairs.values():
                insolvent = bp.settle_interest_simple(traders_map, 0.1)
                for debtor, shortfall in insolvent:
                    tid = id(debtor)
                    if tid in all_insolvent:
                        # 合并同一债务人在不同债券对的缺口
                        all_insolvent[tid] = (
                            debtor,
                            all_insolvent[tid][1] + shortfall,
                            all_insolvent[tid][2],
                        )
                    else:
                        all_insolvent[tid] = (debtor, shortfall, bp.token_name)

            # 处理无法偿债的债务人：尝试市价交易其他代币偿债，不足则破产
            for tid, (debtor, shortfall, token_name) in all_insolvent.items():
                self._handle_insolvency(debtor, token_name, shortfall)

            # 检查净资产为负的机器人
            for entity in all_entities:
                if entity in self.bot_manager.bots.values():
                    bot_id = next(
                        (k for k, v in self.bot_manager.bots.items() if v is entity), None
                    )
                    if bot_id is not None:
                        self.check_and_handle_bankruptcy(bot_id)

        if self.bot_manager:
            self.bot_manager.step()

        current_time = time.time()
        if current_time - self._last_debug_time >= 5:
            self.debug_order_book_depth()
            self._last_debug_time = current_time

        if self._step_counter % self._token_check_interval == 0 and self._initial_token_supply:
            self.check_token_supply_integrity()

    # ====== 破产处理 ======

    def _handle_insolvency(self, trader: Trader, target_token: str, needed: float):
        """
        处理无力偿债的债务人：
        1. 尝试市价交易其他代币获得偿债资金
        2. 如果还是不够，触发破产
        """
        if needed <= 0.00001:
            return

        # 1. 首先检查现有资产是否足够
        available = trader.assets.get(target_token, 0.0)
        if available >= needed:
            trader.assets[target_token] -= needed
            return

        # 2. 现有资产不足，尝试市价卖出其他代币
        remaining = needed - available
        converted = self._liquidate_assets_for_debt(trader, target_token, remaining)
        remaining -= converted

        if remaining <= 0.00001:
            trader.assets[target_token] -= needed
            return

        # 3. 还是不够，触发破产
        bot_id = None
        for bid, bot in self.bot_manager.bots.items():
            if bot is trader:
                bot_id = bid
                break

        if bot_id is not None:
            self._handle_bankruptcy(bot_id)
        else:
            self._handle_player_bankruptcy(trader, target_token, remaining)

    def _liquidate_assets_for_debt(self, trader: Trader, target_token: str, needed: float) -> float:
        """
        市价卖出交易者的其他代币来获得目标代币
        返回: 实际获得的目标代币数量
        """
        if needed <= 0.00001:
            return 0.0

        total_converted = 0.0

        # 策略：遍历所有交易对，找到可以卖出换目标代币的路径
        # 简化处理：直接卖出所有其他代币

        for token_name, amount in list(trader.assets.items()):
            if amount <= 0.00001 or token_name == target_token:
                continue

            # 尝试直接交易 token_name -> target_token
            converted = self._market_sell_for_target(trader, token_name, amount, target_token)
            if converted > 0:
                total_converted += converted
                if total_converted >= needed:
                    break

        return total_converted

    def _market_sell_for_target(
        self,
        trader: Trader,
        from_token: str,
        amount: float,
        target_token: str,
        intermediate_quote: Optional[str] = None,
    ) -> float:
        """
        市价卖出 from_token 换取 target_token
        返回: 获得的目标代币数量

        Args:
            intermediate_quote: 中转计价代币，不传则 fallback 到全局 _quote_token
        """
        if amount <= 0.00001:
            return 0.0

        # 情况1: 直接交易对 from_token/target_token（卖出 from_token）
        for pair in self.trading_pairs.values():
            if pair.base_token == from_token and pair.quote_token == target_token:
                executed, details = pair.execute_market_order(trader, "sell", amount)
                total_received = sum(d.get("revenue", 0) for d in details)
                return total_received

        # 情况2: 通过中转代币路由 from_token -> intermediate_quote -> target_token
        if intermediate_quote is None:
            intermediate_quote = self._quote_token
        if intermediate_quote and intermediate_quote != target_token:
            # 先卖出 from_token 获得 intermediate_quote
            for pair in self.trading_pairs.values():
                if pair.base_token == from_token and pair.quote_token == intermediate_quote:
                    executed, details = pair.execute_market_order(trader, "sell", amount)
                    intermediate = sum(d.get("revenue", 0) for d in details)
                    if intermediate > 0.00001:
                        # 再用 intermediate_quote 买入 target_token
                        for pair2 in self.trading_pairs.values():
                            if (
                                pair2.base_token == target_token
                                and pair2.quote_token == intermediate_quote
                            ):
                                max_buy = intermediate / pair2.price
                                executed2, details2 = pair2.execute_market_order(
                                    trader, "buy", max_buy
                                )
                                return executed2
                    break

        return 0.0

    def _estimate_asset_value(
        self,
        asset_name: str,
        amount: float,
        target_token: str,
        intermediate_quote: Optional[str] = None,
    ) -> float:
        """估算资产按当前市价折算成目标代币的价值

        Args:
            intermediate_quote: 中转计价代币，不传则 fallback 到全局 _quote_token
        """
        if amount <= 0.00001:
            return 0.0

        # 直接交易对
        for pair in self.trading_pairs.values():
            if pair.base_token == asset_name and pair.quote_token == target_token:
                return amount * pair.price

        # 通过中转代币路由
        if intermediate_quote is None:
            intermediate_quote = self._quote_token
        if intermediate_quote and intermediate_quote != target_token:
            for pair in self.trading_pairs.values():
                if pair.base_token == asset_name and pair.quote_token == intermediate_quote:
                    intermediate = amount * pair.price
                    for pair2 in self.trading_pairs.values():
                        if (
                            pair2.base_token == target_token
                            and pair2.quote_token == intermediate_quote
                        ):
                            return intermediate / pair2.price

        return 0.0

    def _handle_player_bankruptcy(self, player: Trader, token_name: str, remaining_debt: float):
        """处理玩家破产"""
        for pair in self.trading_pairs.values():
            pair.cancel_orders_for_bot(player)
        for bond_pair in self.bond_trading_pairs.values():
            bond_pair.cancel_orders_for_bot(player)
        bond_key = f"BOND-{token_name}"
        player.bonds[bond_key] = 0.0

    def check_and_handle_bankruptcy(self, bot_id: int):
        if not self.bot_manager or bot_id not in self.bot_manager.bots:
            return

        bot = self.bot_manager.bots[bot_id]
        net_assets = bot.get_net_assets()

        if net_assets <= 0:
            self._handle_bankruptcy(bot_id)

    def _handle_bankruptcy(self, bot_id: int, remaining_debt: Dict[str, float] = None):
        """
        处理机器人破产 - 使用 bond_pair.liquidate_bonds 原子操作保证债券守恒
        """
        if not self.bot_manager or bot_id not in self.bot_manager.bots:
            return

        bot = self.bot_manager.bots[bot_id]

        self._cancel_all_orders(bot_id)

        all_entities = list(self.bot_manager.bots.values()) + list(
            self.bot_manager.players.values()
        )
        traders_map = {id(e): e for e in all_entities}

        def price_oracle(from_token: str, to_token: str) -> float:
            if from_token == to_token:
                return 1.0
            for pair in self.trading_pairs.values():
                if pair.base_token == from_token and pair.quote_token == to_token:
                    return 1.0 / pair.price if pair.price > 0 else 0.0
                if pair.base_token == to_token and pair.quote_token == from_token:
                    return pair.price if pair.price > 0 else 0.0
            if from_token == self._quote_token:
                return 0.0
            for pair in self.trading_pairs.values():
                if pair.base_token == from_token:
                    intermediate_price = pair.price if pair.price > 0 else 0.0
                    if pair.quote_token == to_token:
                        return intermediate_price
                    quote_to_target = price_oracle(pair.quote_token, to_token)
                    if quote_to_target > 0:
                        return intermediate_price * quote_to_target
            return 0.0

        for bp in self.bond_trading_pairs.values():
            used, bad_debt = bp.liquidate_bonds(bot, dict(bot.assets), traders_map, price_oracle)

    def _cancel_all_orders(self, bot_id: int):
        if not self.bot_manager or bot_id not in self.bot_manager.bots:
            return
        bot = self.bot_manager.bots[bot_id]

        for pair in self.trading_pairs.values():
            pair.cancel_orders_for_bot(bot)

        for bond_pair in self.bond_trading_pairs.values():
            bond_pair.cancel_orders_for_bot(bot)

    def _convert_assets_for_debt(self, bot_id: int, target_token: str, needed: float) -> float:
        """为债务转换资产，返回剩余需要的金额"""
        if not self.bot_manager or bot_id not in self.bot_manager.bots or needed <= 0:
            return needed

        bot = self.bot_manager.bots[bot_id]
        initial_needed = needed

        for token_name, amount in list(bot.assets.items()):
            if amount <= 0 or token_name == target_token:
                continue

            if token_name == target_token:
                use_amount = min(amount, needed)
                bot.assets[token_name] -= use_amount
                needed -= use_amount
            else:
                converted = self._market_sell_asset(bot_id, token_name, min(amount, needed))
                needed -= converted

            if needed <= 0.00001:
                break

        return needed

    def _market_sell_asset(
        self, bot_id: int, token_name: str, volume: float, target_quote_token: Optional[str] = None
    ) -> float:
        """
        市价卖出指定代币，换为目标计价代币
        返回: 获得的目标计价代币数量

        Args:
            target_quote_token: 目标计价代币，不传则 fallback 到全局 _quote_token
        """
        if not self.bot_manager or bot_id not in self.bot_manager.bots or volume <= 0:
            return 0.0

        if target_quote_token is None:
            target_quote_token = self._quote_token
        if not target_quote_token:
            return 0.0

        bot = self.bot_manager.bots[bot_id]
        total_converted = 0.0
        remaining = volume

        for pair in self.trading_pairs.values():
            if pair.base_token == token_name and pair.quote_token == target_quote_token:
                while remaining > 0.0001 and pair.sell_orders:
                    sell_order = pair.sell_orders[0]
                    match_volume = min(remaining, sell_order.volume - sell_order.executed)
                    match_price = sell_order.price
                    match_cost = match_volume * match_price

                    bot.assets[token_name] = bot.assets.get(token_name, 0.0) - match_volume
                    bot.assets[target_quote_token] = (
                        bot.assets.get(target_quote_token, 0.0) + match_cost
                    )

                    seller = sell_order.trader
                    seller.assets[token_name] = seller.assets.get(token_name, 0.0) + match_volume
                    sell_order.remaining_frozen -= match_volume
                    sell_order.executed += match_volume

                    if sell_order.executed >= sell_order.volume:
                        sell_order.close()

                    remaining -= match_volume
                    total_converted += match_cost
                break

        return total_converted

    def _distribute_assets_to_creditors(self, bot_id: int, token_name: str, amount: float):
        """将破产者的资产按比例分配给债权人"""
        if not self.bot_manager or bot_id not in self.bot_manager.bots or amount <= 0.00001:
            return

        bot = self.bot_manager.bots[bot_id]
        bond_key = f"BOND-{token_name}"

        # 找到对应的债券对
        bp = None
        for bond_pair in self.bond_trading_pairs.values():
            if bond_pair.token_name == token_name:
                bp = bond_pair
                break

        if not bp:
            return

        # 收集所有债权人（持有正债券的交易者）
        total_positive = 0.0
        creditors = []
        for entity in list(self.bot_manager.bots.values()) + list(
            self.bot_manager.players.values()
        ):
            b_amt = entity.bonds.get(bond_key, 0.0)
            if b_amt > 0.000001:
                total_positive += b_amt
                creditors.append((entity, b_amt))

        if total_positive <= 0.000001 or not creditors:
            return

        distributed = 0.0
        bonds_written_off = 0.0
        for creditor, credit_amount in creditors:
            ratio = credit_amount / total_positive
            receive_amount = amount * ratio
            if receive_amount > 0.00001:
                creditor.assets[token_name] = creditor.assets.get(token_name, 0.0) + receive_amount
                distributed += receive_amount
                old_bond = creditor.bonds.get(bond_key, 0.0)
                write_off = min(receive_amount, old_bond)
                creditor.bonds[bond_key] = old_bond - write_off

    def _forgive_debtors_for_positive_bond(
        self, bot_id: int, token_name: str, positive_amount: float
    ):
        """破产者持有正债券消失时，按比例豁免对应债务人的债务"""
        if (
            not self.bot_manager
            or bot_id not in self.bot_manager.bots
            or positive_amount <= 0.00001
        ):
            return

        bond_key = f"BOND-{token_name}"

        bp = None
        for bp_id, bond_pair in self.bond_trading_pairs.items():
            if bond_pair.token_name == token_name:
                bp = bond_pair
                break

        if not bp:
            return

        total_negative = 0.0
        debtors_list = []
        for entity in list(self.bot_manager.bots.values()) + list(
            self.bot_manager.players.values()
        ):
            b_amt = entity.bonds.get(bond_key, 0.0)
            if b_amt < -0.000001:
                total_negative += -b_amt
                debtors_list.append((entity, -b_amt))

        if total_negative <= 0.000001 or not debtors_list:
            return

        for debtor, debt_amount in debtors_list:
            ratio = debt_amount / total_negative
            forgive = min(positive_amount * ratio, debt_amount)
            old_bond = debtor.bonds.get(bond_key, 0.0)
            debtor.bonds[bond_key] = old_bond + forgive

    def _distribute_debt_to_creditors(self, bot_id: int, token_name: str, unpaid_debt: float):
        """将坏账按比例核销债权人的债券"""
        if not self.bot_manager or bot_id not in self.bot_manager.bots or unpaid_debt <= 0.00001:
            return

        bot = self.bot_manager.bots[bot_id]
        bond_key = f"BOND-{token_name}"

        bp = None
        for bp_id, bond_pair in self.bond_trading_pairs.items():
            if bond_pair.token_name == token_name:
                bp = bond_pair
                break

        if not bp:
            return

        total_positive = 0.0
        creditors = []
        for entity in list(self.bot_manager.bots.values()) + list(
            self.bot_manager.players.values()
        ):
            b_amt = entity.bonds.get(bond_key, 0.0)
            if b_amt > 0:
                total_positive += b_amt
                creditors.append((entity, b_amt))

        if total_positive <= 0.000001 or not creditors:
            return

        total_write_off = 0.0
        for creditor, credit_amount in creditors:
            ratio = credit_amount / total_positive
            write_off = min(unpaid_debt * ratio, credit_amount)  # 不能超过债权人的持仓
            old_bond = creditor.bonds.get(bond_key, 0.0)
            creditor.bonds[bond_key] = old_bond - write_off
            total_write_off += write_off

        # 如果还有未核销的债务，按比例豁免债务人
        remaining_unpaid = unpaid_debt - total_write_off
        if remaining_unpaid > 0.000001:
            self._forgive_remaining_debt(token_name, remaining_unpaid)

    def _forgive_remaining_debt(self, token_name: str, remaining: float):
        """豁免剩余债务（当债权人不足以核销全部坏账时）"""
        bond_key = f"BOND-{token_name}"

        total_negative = 0.0
        debtors = []
        for entity in list(self.bot_manager.bots.values()) + list(
            self.bot_manager.players.values()
        ):
            b_amt = entity.bonds.get(bond_key, 0.0)
            if b_amt < -0.000001:
                total_negative += -b_amt
                debtors.append((entity, -b_amt))

        if total_negative <= 0.000001 or not debtors:
            return

        forgive_ratio = remaining / total_negative
        for debtor, debt_amount in debtors:
            forgive = debt_amount * forgive_ratio
            old_bond = debtor.bonds.get(bond_key, 0.0)
            debtor.bonds[bond_key] = old_bond + forgive

    # ====== 查询接口（只读，委托给底层对象）======

    def get_trading_pair_logs(self, pair_id: int) -> List[Tuple[float, float, float]]:
        if pair_id in self.trading_pairs:
            return self.trading_pairs[pair_id].log.copy()
        return []

    def get_current_price(self, pair_id: int) -> float:
        if pair_id in self.trading_pairs:
            return self.trading_pairs[pair_id].price
        return 0.0

    def get_bond_pair_logs(self, bond_pair_id: int) -> List[Tuple[float, float, float]]:
        if bond_pair_id in self.bond_trading_pairs:
            return self.bond_trading_pairs[bond_pair_id].log.copy()
        return []

    def get_current_rate(self, bond_pair_id: int) -> float:
        if bond_pair_id in self.bond_trading_pairs:
            return self.bond_trading_pairs[bond_pair_id].current_rate
        return 0.0

    def get_rate_integral(self, bond_pair_id: int) -> float:
        if bond_pair_id in self.bond_trading_pairs:
            return self.bond_trading_pairs[bond_pair_id].rate_integral
        return 0.0

    def get_bot_assets(self, id: int) -> Dict[str, float]:
        if self.bot_manager:
            entity = self.bot_manager.get_bot(id)
            if entity:
                return entity.assets.copy()
        return {}

    def get_bot_bonds(self, id: int) -> Dict[str, float]:
        if self.bot_manager:
            entity = self.bot_manager.get_bot(id)
            if entity:
                return entity.bonds.copy()
        return {}

    def get_all_pair_ids(self) -> List[int]:
        return list(self.trading_pairs.keys())

    def get_all_bond_pair_ids(self) -> List[int]:
        return list(self.bond_trading_pairs.keys())

    def get_all_bot_ids(self) -> List[int]:
        if self.bot_manager:
            return list(self.bot_manager.bots.keys())
        return []

    def get_trading_pair_info(self, pair_id: int) -> Optional[dict]:
        if pair_id in self.trading_pairs:
            pair = self.trading_pairs[pair_id]
            return {
                "base_token": pair.base_token,
                "quote_token": pair.quote_token,
                "price": pair.price,
                "log_count": len(pair.log),
            }
        return None

    def get_bond_pair_info(self, bond_pair_id: int) -> Optional[dict]:
        if bond_pair_id in self.bond_trading_pairs:
            bp = self.bond_trading_pairs[bond_pair_id]
            return {
                "token_name": bp.token_name,
                "current_rate": bp.current_rate,
                "rate_integral": bp.rate_integral,
                "log_count": len(bp.log),
            }
        return None

    # ====== 价格转换（仅用于给Trader设置转换函数引用）======

    def _convert_to_quote(
        self, from_token: str, amount: float, target_quote: Optional[str] = None
    ) -> float:
        """
        将代币金额转换为目标计价代币的等值金额（BFS 搜索最优路径）

        Args:
            target_quote: 目标计价代币，不传则 fallback 到全局 _quote_token
        """
        if target_quote is None:
            target_quote = self._quote_token
        if target_quote is None:
            return amount

        if from_token == target_quote:
            return amount

        visited = {from_token}
        queue = [(from_token, amount, 1.0)]

        while queue:
            current_token, current_amount, current_rate = queue.pop(0)

            for pair in self.trading_pairs.values():
                if pair.base_token == current_token:
                    new_amount = current_amount * pair.price
                    new_token = pair.quote_token
                    new_rate = current_rate * pair.price

                    if new_token == target_quote:
                        return new_amount

                    if new_token not in visited:
                        visited.add(new_token)
                        queue.append((new_token, new_amount, new_rate))

                elif pair.quote_token == current_token:
                    new_amount = current_amount / pair.price
                    new_token = pair.base_token
                    new_rate = current_rate / pair.price

                    if new_token == target_quote:
                        return new_amount

                    if new_token not in visited:
                        visited.add(new_token)
                        queue.append((new_token, new_amount, new_rate))

        return 0.0

    # ====== 调试接口 ======

    def get_total_usdt_supply(self) -> dict:
        if not self._quote_token:
            return {
                "held_by_bots": 0,
                "held_by_players": 0,
                "frozen_in_orders": 0,
                "total_supply": 0,
            }

        held_by_bots = 0.0
        held_by_players = 0.0
        frozen_in_orders = 0.0

        if self.bot_manager:
            for bot in self.bot_manager.bots.values():
                held_by_bots += bot.assets.get(self._quote_token, 0.0)

            for player in self.bot_manager.players.values():
                held_by_players += player.assets.get(self._quote_token, 0.0)

        for pair in self.trading_pairs.values():
            if pair.quote_token == self._quote_token:
                for order in pair.buy_orders:
                    frozen_in_orders += order.remaining_frozen

        for bond_pair in self.bond_trading_pairs.values():
            if bond_pair.token_name == self._quote_token:
                for order in bond_pair.buy_orders:
                    frozen_in_orders += order.remaining_frozen

        total_supply = held_by_bots + held_by_players + frozen_in_orders

        return {
            "held_by_bots": held_by_bots,
            "held_by_players": held_by_players,
            "frozen_in_orders": frozen_in_orders,
            "total_supply": total_supply,
        }

    def _calculate_market_leverage(self) -> Dict[str, float]:
        """
        计算市场总杠杆率

        杠杆率定义:
        - 总债务 = 所有交易者的负债券绝对值之和（按计价代币折算）
        - 总净资产 = 所有交易者净资产之和
        - 市场杠杆率 = 总债务 / 总净资产
        - 债务/净资产比 = 总债务 / 总净资产

        返回:
            {
                'total_net_assets': 总净资产,
                'total_debt': 总债务,
                'leverage_ratio': 杠杆率,
                'debt_to_equity': 债务净资产比,
                'insolvent_count': 资不抵债的机器人数量
            }
        """
        if not self.bot_manager:
            return {
                "total_net_assets": 0.0,
                "total_debt": 0.0,
                "leverage_ratio": 0.0,
                "debt_to_equity": 0.0,
                "insolvent_count": 0,
            }

        all_entities = list(self.bot_manager.bots.values()) + list(
            self.bot_manager.players.values()
        )

        total_net_assets = 0.0
        total_debt = 0.0
        insolvent_count = 0

        for entity in all_entities:
            net_assets = entity.get_net_assets()
            total_net_assets += max(0, net_assets)

            if net_assets <= 0:
                insolvent_count += 1

            # 计算债务（负债券）
            for bond_key, bond_amount in entity.bonds.items():
                if bond_amount < 0:
                    token_name = bond_key.replace("BOND-", "")
                    debt_value = abs(bond_amount)

                    # 转换为计价代币价值
                    if entity._quote_token and entity._price_converter:
                        if token_name == entity._quote_token:
                            total_debt += debt_value
                        else:
                            converted = entity._price_converter(token_name, debt_value)
                            total_debt += converted
                    else:
                        total_debt += debt_value

        # 计算杠杆率
        if total_net_assets > 0.00001:
            leverage_ratio = total_debt / total_net_assets
            debt_to_equity = total_debt / total_net_assets
        else:
            leverage_ratio = 0.0
            debt_to_equity = 0.0

        return {
            "total_net_assets": total_net_assets,
            "total_debt": total_debt,
            "leverage_ratio": leverage_ratio,
            "debt_to_equity": debt_to_equity,
            "insolvent_count": insolvent_count,
        }

    def debug_order_book_depth(self):
        """输出所有交易对的深度信息"""
        # 计算并输出市场杠杆率
        leverage_info = self._calculate_market_leverage()
        print(
            f"[市场杠杆率] 杠杆率={leverage_info['leverage_ratio']:.2f}x, 债务/净资产={leverage_info['debt_to_equity']:.2%}, 资不抵债={leverage_info['insolvent_count']}个"
        )

        for pair_id, pair in self.trading_pairs.items():
            buy_depth = sum(o.volume - o.executed for o in pair.buy_orders)
            sell_depth = sum(o.volume - o.executed for o in pair.sell_orders)
            print(
                f"[{pair.base_token}/{pair.quote_token}] 价格={pair.price:.4f}, 买深={buy_depth:.2f}, 卖深={sell_depth:.2f}"
            )

        for bp_id, bp in self.bond_trading_pairs.items():
            buy_depth = sum(o.volume - o.executed for o in bp.buy_orders)
            sell_depth = sum(o.volume - o.executed for o in bp.sell_orders)
            print(
                f"[BOND-{bp.token_name}] 利率={bp.current_rate:.6f}, 买深={buy_depth:.2f}, 卖深={sell_depth:.2f}"
            )

    def record_initial_token_supply(self):
        """
        记录所有代币的初始总量
        应在市场初始化完成后调用
        """
        self._initial_token_supply = self._calculate_all_token_supply()
        print(f"[代币总量] 已记录初始代币总量:")
        for token, amount in self._initial_token_supply.items():
            print(f"  {token}: {amount:.2f}")

    def _calculate_all_token_supply(self) -> Dict[str, float]:
        """
        计算所有代币的当前总量
        包括：所有人持仓中的代币 + 订单中冻结的代币
        """
        supply: Dict[str, float] = {}

        for token_name in self.tokens.keys():
            supply[token_name] = 0.0

        if self.bot_manager:
            for bot in self.bot_manager.bots.values():
                for token_name, amount in bot.assets.items():
                    supply[token_name] = supply.get(token_name, 0.0) + amount

            for player in self.bot_manager.players.values():
                for token_name, amount in player.assets.items():
                    supply[token_name] = supply.get(token_name, 0.0) + amount

        for pair in self.trading_pairs.values():
            for order in pair.buy_orders:
                supply[pair.quote_token] = (
                    supply.get(pair.quote_token, 0.0) + order.remaining_frozen
                )

            for order in pair.sell_orders:
                supply[pair.base_token] = supply.get(pair.base_token, 0.0) + order.remaining_frozen

        for bond_pair in self.bond_trading_pairs.values():
            for order in bond_pair.buy_orders:
                supply[bond_pair.token_name] = (
                    supply.get(bond_pair.token_name, 0.0) + order.remaining_frozen
                )

        return supply

    def check_token_supply_integrity(self) -> Dict[str, Dict[str, float]]:
        """
        检查每种代币的总量是否保持不变
        返回: {token_name: {'initial': 初始值, 'current': 当前值, 'diff': 差值}}
        """
        if not self._initial_token_supply:
            return {}

        current_supply = self._calculate_all_token_supply()
        result = {}

        for token_name, initial_amount in self._initial_token_supply.items():
            current_amount = current_supply.get(token_name, 0.0)
            diff = current_amount - initial_amount

            result[token_name] = {
                "initial": initial_amount,
                "current": current_amount,
                "diff": diff,
            }

            if abs(diff) > 0.01:
                self._print_token_supply_warning(token_name, initial_amount, current_amount, diff)

        for token_name, current_amount in current_supply.items():
            if token_name not in self._initial_token_supply:
                print(f"[代币总量警告] 发现新代币: {token_name}, 当前总量: {current_amount:.2f}")
                result[token_name] = {
                    "initial": 0.0,
                    "current": current_amount,
                    "diff": current_amount,
                }

        return result

    def _print_token_supply_warning(
        self, token_name: str, initial: float, current: float, diff: float
    ):
        """打印代币总量变化警告的详细信息"""
        print(f"\n{'='*60}")
        print(f"[代币总量警告] {token_name} 总量发生变化!")
        print(f"  初始总量: {initial:.2f}")
        print(f"  当前总量: {current:.2f}")
        print(f"  差异: {diff:+.2f}")
        print(f"{'='*60}")

        held_by_bots = 0.0
        held_by_players = 0.0
        frozen_in_normal_buy = 0.0
        frozen_in_normal_sell = 0.0
        frozen_in_bond_buy = 0.0

        if self.bot_manager:
            for bot in self.bot_manager.bots.values():
                held_by_bots += bot.assets.get(token_name, 0.0)
            for player in self.bot_manager.players.values():
                held_by_players += player.assets.get(token_name, 0.0)

        for pair in self.trading_pairs.values():
            if pair.quote_token == token_name:
                for order in pair.buy_orders:
                    frozen_in_normal_buy += order.remaining_frozen
            if pair.base_token == token_name:
                for order in pair.sell_orders:
                    frozen_in_normal_sell += order.remaining_frozen

        for bond_pair in self.bond_trading_pairs.values():
            if bond_pair.token_name == token_name:
                for order in bond_pair.buy_orders:
                    frozen_in_bond_buy += order.remaining_frozen

        total = (
            held_by_bots
            + held_by_players
            + frozen_in_normal_buy
            + frozen_in_normal_sell
            + frozen_in_bond_buy
        )
        print(
            f"  {token_name}: 持仓={held_by_bots + held_by_players:.2f}, 冻结={frozen_in_normal_buy + frozen_in_normal_sell + frozen_in_bond_buy:.2f}, 总计={total:.2f}"
        )

    def get_token_supply_report(self) -> Dict[str, Dict[str, float]]:
        """获取代币总量报告"""
        current_supply = self._calculate_all_token_supply()
        result = {}

        for token_name, current_amount in current_supply.items():
            initial_amount = self._initial_token_supply.get(token_name, 0.0)
            result[token_name] = {
                "initial": initial_amount,
                "current": current_amount,
                "diff": current_amount - initial_amount,
            }

        return result


_engine: Optional[MarketEngine] = None


def get_engine() -> MarketEngine:
    global _engine
    if _engine is None:
        _engine = MarketEngine()
    return _engine
