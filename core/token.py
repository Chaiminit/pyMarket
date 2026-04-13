"""
Token 模块 - 代币定义

定义金融市场中的代币(资产)类型，作为交易和持仓的基本单位。
每个代币具有唯一ID和名称，可用于哈希和相等性比较。
"""

from typing import Optional


class Token:
    """
    代币类 - 代表金融市场中的可交易资产

    代币是资产的基本标识单位，用于：
    - 标识交易对中的基础/计价资产
    - 记录交易者的资产持仓
    - 作为字典的键进行哈希比较

    Attributes:
        name: 代币名称，如 "BTC", "USDT"
        token_id: 全局唯一标识符
        is_quote: 是否为计价代币（如USDT）

    Examples:
        >>> btc = Token("BTC", 0)
        >>> usdt = Token("USDT", 1, is_quote=True)
        >>> btc == Token("BTC", 0)  # 基于token_id比较
        True
    """

    def __init__(self, name: str, token_id: int, is_quote: bool = False):
        """
        创建代币实例

        Args:
            name: 代币名称
            token_id: 唯一标识符
            is_quote: 是否为计价代币
        """
        self.name = name
        self.token_id = token_id
        self.is_quote = is_quote

    def __hash__(self) -> int:
        """基于token_id的哈希，支持作为字典键"""
        return hash(self.token_id)

    def __eq__(self, other: object) -> bool:
        """基于token_id的相等性比较"""
        if isinstance(other, Token):
            return self.token_id == other.token_id
        return False

    def __repr__(self) -> str:
        """字符串表示"""
        return f"Token({self.name})"
