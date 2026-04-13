from typing import Dict, List, Optional, Callable


class Trader:
    """交易者/机器人"""

    def __init__(self, name: str, trend: float = 0.0, view: float = 0.0):
        self.name = name
        self.trend = trend
        self.view = view
        self.assets: Dict[str, float] = {}
        self.bonds: Dict[str, float] = {}
        self.k = 0.0
        self.orders = []
        self.bond_orders = []
        self.last_bond_calc_time: Dict[str, float] = {}
        self.is_player = False
        self.trading_pairs: List[int] = []
        self.bond_pairs: List[int] = []

        # 价格转换函数，由 Engine 设置
        self._price_converter: Optional[Callable[[str, float], float]] = None
        self._quote_token: Optional[str] = None

    def add_asset(self, token: str, amount: float):
        self.assets[token] = self.assets.get(token, 0.0) + amount

    def set_price_converter(
        self, converter: Callable[[str, float, Optional[str]], float], quote_token: str
    ):
        """设置价格转换函数，用于计算总资产

        Args:
            converter: 价格转换函数，签名应为 (from_token, amount, target_quote=None) -> float
            quote_token: 默认计价代币
        """
        self._price_converter = converter
        self._quote_token = quote_token

    def get_total_assets(self, quote_token: Optional[str] = None) -> float:
        """计算总资产（以计价代币为单位）

        Args:
            quote_token: 指定计价代币，不传则使用默认的 self._quote_token
        """
        if quote_token is None:
            quote_token = self._quote_token
        if quote_token is None or self._price_converter is None:
            return sum(max(0, amount) for amount in self.assets.values())

        total = 0.0

        for token_name, amount in self.assets.items():
            if amount <= 0:
                continue

            if token_name == quote_token:
                total += amount
            else:
                total += self._price_converter(token_name, amount, quote_token)

        for bond_key, bond_amount in self.bonds.items():
            if bond_amount <= 0:
                continue
            token_name = bond_key.replace("BOND-", "")
            if token_name == quote_token:
                total += bond_amount
            else:
                total += self._price_converter(token_name, bond_amount, quote_token)

        return total

    def get_net_assets(self, quote_token: Optional[str] = None) -> float:
        """计算净资产（总资产 - 债券债务）

        Args:
            quote_token: 指定计价代币，不传则使用默认的 self._quote_token
        """
        total_assets = self.get_total_assets(quote_token)

        if quote_token is None:
            quote_token = self._quote_token
        if quote_token is None or self._price_converter is None:
            return total_assets

        liabilities = 0.0

        for bond_key, bond_amount in self.bonds.items():
            if bond_amount < 0:
                token_name = bond_key.replace("BOND-", "")
                liability_value = abs(bond_amount)
                if token_name == quote_token:
                    liabilities += liability_value
                else:
                    liabilities += self._price_converter(token_name, liability_value, quote_token)

        return total_assets - liabilities
