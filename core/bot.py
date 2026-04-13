import numpy as np
import random
from typing import List, Dict, Optional

from .trader import Trader
from .utils import chip_distribution


class SimpleNN:
    """基于numpy的全连接神经网络 - 输入5个特征，输出0-1的action"""

    def __init__(self, input_size=5, hidden_size=8):
        self.input_size = input_size
        self.hidden_size = hidden_size
        np.random.seed(random.randint(0, 2**31))

        # Xavier初始化
        self.w1 = np.random.randn(input_size, hidden_size) * np.sqrt(
            2.0 / (input_size + hidden_size)
        )
        self.b1 = np.zeros(hidden_size)
        self.w2 = np.random.randn(hidden_size, hidden_size) * np.sqrt(
            2.0 / (hidden_size + hidden_size)
        )
        self.b2 = np.zeros(hidden_size)
        self.w3 = np.random.randn(hidden_size, 1) * np.sqrt(2.0 / (hidden_size + 1))
        self.b3 = np.zeros(1)

    def forward(self, x):
        # 全部使用tanh激活，支持负值
        h1 = np.tanh(x @ self.w1 + self.b1)
        h2 = np.tanh(h1 @ self.w2 + self.b2)
        raw = h2 @ self.w3 + self.b3

        # 输出层：tanh映射到[-1,1]，再线性变换到[0.1, 0.9]
        out = 0.5 + 0.4 * np.tanh(raw)
        return float(np.clip(out[0][0], 0.05, 0.95))


def _extract_features(log, view, n_points=5, noise_scale=0.1):
    """从log中均匀抽取n_points个价格点，计算对数收益率，加入随机扰动"""
    if not log or len(log) < 2:
        return None
    n = len(log)
    indices = [int(i * (n - 1) / (n_points - 1)) for i in range(n_points)]
    prices = [log[i][1] for i in indices]
    if any(p <= 0 for p in prices):
        return None

    # 计算对数收益率（差分）
    log_prices = np.log(prices)
    returns = np.diff(log_prices)  # 4个收益率值

    # 直接使用收益率，放大10倍，加入随机扰动
    features = []
    for r in returns[: n_points - 1]:
        noise = random.gauss(0, noise_scale)  # 每个Bot看到略有不同的输入
        features.append(r * 10 + 0.5 + noise)

    # 添加当前价格位置，也加入扰动
    current_price = prices[-1]
    p_min, p_max = min(prices), max(prices)
    if p_max > p_min:
        position = (current_price - p_min) / (p_max - p_min)
    else:
        position = 0.5
    features.append(position + random.gauss(0, noise_scale * 0.5))

    return features


class Bot(Trader):
    """交易机器人 - 继承自Trader，使用神经网络决策"""

    def __init__(self, bot_id: int, name: str, trend: float = 0.0, view: float = 30.0):
        super().__init__(name, trend, view)
        self.bot_id = bot_id
        self.nn = SimpleNN()
        self._mc_cache = {}
        self._mc_cache_step = -1

    def act(self, trading_pairs_map: Dict[int, object]):
        """执行一步交易动作 - 基于神经网络决策"""
        if not self.trading_pairs:
            return

        if len(self.orders) > 10 * len(self.trading_pairs):
            self._cancel_oldest_order()

        for pair_id in self.trading_pairs:
            if pair_id not in trading_pairs_map:
                continue

            pair = trading_pairs_map[pair_id]

            features = _extract_features(pair.log, self.view)
            action = (
                self.nn.forward(np.array([features]).astype(float))
                if features is not None
                else random.random()
            )

            try:
                if action < 0.1:
                    self._place_market_order(pair_id, pair, "sell")
                elif action < 0.5:
                    self._place_limit_order(pair_id, pair, "sell")
                elif action < 0.9:
                    self._place_limit_order(pair_id, pair, "buy")
                else:
                    self._place_market_order(pair_id, pair, "buy")
            except Exception as e:
                print(f"机器人{self.name}交易失败: {e}")

    def act_bond(self, bond_pairs_map: Dict[int, object], engine):
        """执行债券交易逻辑 - 使用同一神经网络，输入为市值归一化值"""
        if not self.bond_pairs:
            return

        if not hasattr(self, "bond_orders"):
            self.bond_orders = []

        if len(self.bond_orders) > 5 * len(self.bond_pairs):
            oldest = min(self.bond_orders, key=lambda o: getattr(o, "_created", 0))
            oldest.close()

        step = engine._step_counter
        if step != self._mc_cache_step:
            self._mc_cache = {}
            self._mc_cache_step = step

        for bond_pair_id in self.bond_pairs:
            if bond_pair_id not in bond_pairs_map:
                continue

            bond_pair = bond_pairs_map[bond_pair_id]
            cache_key = id(bond_pair)

            if cache_key not in self._mc_cache:
                features = self._calc_bond_features(bond_pair, engine)
                self._mc_cache[cache_key] = features
            else:
                features = self._mc_cache[cache_key]

            action = (
                self.nn.forward(np.array([features]).astype(float))
                if features is not None
                else random.random()
            )

            try:
                if action < 0.1:
                    self._place_bond_market_order(bond_pair_id, bond_pair, "buy", engine)
                elif action < 0.5:
                    self._place_bond_limit_order(bond_pair_id, bond_pair, "buy", engine)
                elif action < 0.9:
                    self._place_bond_limit_order(bond_pair_id, bond_pair, "sell", engine)
                else:
                    self._place_bond_market_order(bond_pair_id, bond_pair, "sell", engine)
            except Exception as e:
                print(f"机器人{self.name}债券交易失败: {e}")

    def _calc_bond_features(self, bond_pair, engine, n_points=5):
        """计算债券特征：所有代币用债券代币计价后的总市值"""
        log = bond_pair.log
        if not log or len(log) < 2:
            return None

        n = len(log)
        indices = [int(i * (n - 1) / (n_points - 1)) for i in range(n_points)]

        # 债券代币名称
        token_name = bond_pair.token_name

        # 计算每个时间点的总市值（用债券代币计价）
        market_caps = []
        for idx in indices:
            mc = 0.0
            for tp in engine.trading_pairs.values():
                if not tp.log:
                    continue
                # 获取该时间点的价格
                price_idx = min(idx, len(tp.log) - 1)
                base_price = tp.log[price_idx][1]  # 基础代币价格（以quote计价）
                token_supply = engine._initial_token_supply.get(tp.base_token, 0.0)

                # 将市值转换为债券代币计价
                if token_name == tp.quote_token:
                    # 债券代币就是计价代币，直接计算
                    mc += token_supply * base_price
                elif token_name == tp.base_token:
                    # 债券代币是基础代币，市值 = 供应量
                    mc += token_supply
                else:
                    # 需要通过价格换算：先转成quote，再转成token_name
                    # 简化为：市值(quote) / price(token_name/quote)
                    # 这里需要找到token_name/quote的交易对价格
                    quote_token = tp.quote_token
                    for tp2 in engine.trading_pairs.values():
                        if tp2.base_token == token_name and tp2.quote_token == quote_token:
                            bond_price_idx = min(idx, len(tp2.log) - 1) if tp2.log else 0
                            if tp2.log and bond_price_idx < len(tp2.log):
                                bond_price = tp2.log[bond_price_idx][1]
                                mc += (token_supply * base_price) / bond_price
                            break
            market_caps.append(mc)

        if len(market_caps) < 2 or all(v <= 0 for v in market_caps):
            return None

        # 计算市值变化率（差分）
        mc_arr = np.array(market_caps)
        returns = np.diff(np.log(mc_arr + 1e-10))

        # 直接使用收益率，放大10倍，加入随机扰动
        features = []
        for r in returns[: n_points - 1]:
            noise = random.gauss(0, 0.1)
            features.append(r * 10 + 0.5 + noise)

        # 添加当前市值位置，也加入扰动
        current_mc = market_caps[-1]
        mc_min, mc_max = min(market_caps), max(market_caps)
        if mc_max > mc_min:
            position = (current_mc - mc_min) / (mc_max - mc_min)
        else:
            position = 0.5
        features.append(position + random.gauss(0, 0.05))

        return features

    def _place_limit_order(self, pair_id: int, pair: object, direction: str) -> bool:
        MAX_ORDERS = 10

        if len(self.orders) >= MAX_ORDERS:
            order_to_cancel = random.choice(self.orders)
            order_to_cancel.close()

        base_token = pair.base_token
        quote_token = pair.quote_token
        current_price = pair.price

        price_deviation = chip_distribution.sample()
        volume_weight = float(chip_distribution.pdf(price_deviation))

        if direction == "buy":
            price = current_price * (1 + price_deviation)
            quote_balance = self.assets.get(quote_token, 0.0)
            if quote_balance <= 0:
                return False
            max_volume = quote_balance * volume_weight * (random.random() + 1) / 4 / price
        else:
            price = current_price * (1 - price_deviation)
            base_balance = self.assets.get(base_token, 0.0)
            if base_balance <= 1:
                return False
            max_volume = base_balance * volume_weight * (random.random() + 1) / 4

        volume = max(0.0001, float(max_volume))

        required = price * volume if direction == "buy" else volume
        asset_key = quote_token if direction == "buy" else base_token

        if self.assets.get(asset_key, 0.0) < required:
            return False

        self.assets[asset_key] -= required
        pair.submit_limit_order(self, direction, price, volume, required)
        return True

    def _place_market_order(self, pair_id: int, pair: object, direction: str) -> bool:
        base_token = pair.base_token
        quote_token = pair.quote_token
        current_price = pair.price

        volume_deviation = chip_distribution.sample()
        volume_weight = float(chip_distribution.pdf(volume_deviation))

        if direction == "buy":
            available = self.assets.get(quote_token, 0.0)
            if available <= 0:
                return False
            volume = available * volume_weight * (random.random()) / 5 / current_price
        else:
            available = self.assets.get(base_token, 0.0)
            if available <= 0:
                return False
            volume = available * volume_weight * (random.random()) / 5

        volume = max(0.000001, volume)
        pair.execute_market_order(self, direction, volume)
        return True

    def _cancel_oldest_order(self):
        if self.orders:
            self.orders[0].close()

    def _place_bond_limit_order(
        self, bond_pair_id: int, bond_pair: object, direction: str, engine
    ) -> bool:
        MAX_BOND_ORDERS = 5

        if len(self.bond_orders) >= MAX_BOND_ORDERS:
            order_to_cancel = random.choice(self.bond_orders)
            order_to_cancel.close()

        token_name = bond_pair.token_name
        current_rate = bond_pair.current_rate

        rate_deviation = chip_distribution.sample()
        volume_weight = float(chip_distribution.pdf(rate_deviation))

        if direction == "buy":
            token_balance = self.assets.get(token_name, 0.0)
            if token_balance <= 0.00001:
                return False

            rate = current_rate * (1 + rate_deviation)
            volume = token_balance * volume_weight * (random.random()) / 3
            volume = min(volume, token_balance)
        else:
            net_assets = self.get_net_assets(quote_token=token_name)
            if net_assets <= 0:
                return False

            rate = current_rate * (1 - rate_deviation)
            volume = net_assets * volume_weight * (random.random()) / 3

        volume = max(0.0001, float(volume))

        bond_pair.submit_limit_order(self, direction, rate, volume)
        return True

    def _place_bond_market_order(
        self, bond_pair_id: int, bond_pair: object, direction: str, engine
    ) -> bool:
        token_name = bond_pair.token_name

        volume_deviation = chip_distribution.sample()
        volume_weight = float(chip_distribution.pdf(volume_deviation))

        if direction == "buy":
            token_balance = self.assets.get(token_name, 0.0)
            if token_balance <= 0.00001:
                return False
            volume = token_balance * volume_weight * (random.random()) / 5
        else:
            net_assets = self.get_net_assets(quote_token=token_name)
            if net_assets <= 0:
                return False
            volume = net_assets * volume_weight * (random.random()) / 5

        volume = max(0.0001, float(volume))
        bond_pair.execute_market_order(self, direction, volume)
        return True


class BotManager:
    """机器人管理器 - 管理所有机器人的生命周期和交易行为"""

    def __init__(self, engine):
        self.engine = engine
        self.bots: Dict[int, Bot] = {}
        self.players: Dict[int, Trader] = {}
        self._bot_counter = 0
        self._player_counter = 0

    def __len__(self) -> int:
        return len(self.bots) + len(self.players)

    def create_bot(self, name: str, trend: float = 0.0, view: float = 30.0) -> int:
        bot_id = self._bot_counter
        bot = Bot(bot_id, name, trend, view)
        if self.engine:
            bot.set_price_converter(self.engine._convert_to_quote, self.engine.get_quote_token())
        self.bots[bot_id] = bot
        self._bot_counter += 1
        return bot_id

    def create_player(self, name: str) -> int:
        player_id = self._player_counter + 100000
        player = Trader(name)
        if self.engine:
            player.set_price_converter(self.engine._convert_to_quote, self.engine.get_quote_token())
        self.players[player_id] = player
        self._player_counter += 1
        return player_id

    def create_bots_batch(
        self,
        count: int,
        asset_configs: Dict[str, tuple],
        name_prefix: str = "Bot",
        trend: float = 50.0,
        view: float = 30.0,
    ) -> List[int]:
        bot_ids = []
        for i in range(count):
            bot_trend = trend * (-0.6 + random.random() * 1.5)
            bot_view = view * (0.1 + random.random() * 9.9)

            bot_id = self.create_bot(f"{name_prefix}_{i+1:03d}", bot_trend, bot_view)
            bot = self.bots[bot_id]

            for token_name, (min_amount, max_amount) in asset_configs.items():
                amount = random.uniform(min_amount, max_amount)
                bot.add_asset(token_name, amount)

            bot_ids.append(bot_id)

        return bot_ids

    def set_trading_pairs(self, id: int, pair_ids: List[int]):
        if id in self.bots:
            self.bots[id].trading_pairs = pair_ids
        elif id in self.players:
            self.players[id].trading_pairs = pair_ids

    def set_bond_pairs(self, id: int, bond_pair_ids: List[int]):
        if id in self.bots:
            self.bots[id].bond_pairs = bond_pair_ids
        elif id in self.players:
            self.players[id].bond_pairs = bond_pair_ids

    def get_bot(self, id: int) -> Optional[Trader]:
        if id in self.bots:
            return self.bots[id]
        elif id in self.players:
            return self.players[id]
        return None

    def step(self):
        trading_pairs_map = self.engine.trading_pairs
        bond_pairs_map = self.engine.bond_trading_pairs

        for bot_id, bot in self.bots.items():
            if random.random() >= 0.7:
                continue

            bot.act(trading_pairs_map)
            bot.act_bond(bond_pairs_map, self.engine)

    def get_average_asset_value(self, token_name: str) -> float:
        if not self.bots:
            return 0.0
        total = sum(bot.assets.get(token_name, 0.0) for bot in self.bots.values())
        return total / len(self.bots)
