"""
EngineNode 模块 - 引擎节点基类

定义所有市场引擎核心对象的基类，
确保所有对象都实现 step 方法，供引擎统一调用。
"""

from decimal import Decimal
from typing import Set


class EngineNode:
    """
    引擎节点基类 - 所有核心对象的基类

    提供统一的 step 接口，让引擎可以在每一步调用所有对象的 step 方法。
    所有核心类（Trader, TradingPair, BondTradingPair, Corp 等）都应该继承此类。

    Attributes:
        name: 对象名称（可选）
        _engine: 关联的引擎实例（由引擎设置）

    Examples:
        >>> class MyObject(EngineNode):
        ...     def step(self, dt):
        ...         # 实现每步逻辑
        ...         pass
    """

    _all_nodes: Set["EngineNode"] = set()

    def __init__(self, name: str = ""):
        """
        创建引擎节点

        Args:
            name: 对象名称
        """
        self.name = name
        self._engine = None
        EngineNode._all_nodes.add(self)

    def step(self, dt: Decimal) -> None:
        """
        市场模拟步进回调

        每个模拟步进时由 Engine 调用，子类必须重写此方法
        来实现自定义的每步逻辑。

        Args:
            dt: 时间步长（秒）

        Examples:
            >>> class MyObject(EngineNode):
            ...     def step(self, dt):
            ...         # 每步执行逻辑
            ...         self.update(dt)
        """
        pass

    @classmethod
    def get_all_nodes(cls) -> Set["EngineNode"]:
        """
        获取所有引擎节点

        Returns:
            所有引擎节点的集合
        """
        return cls._all_nodes

    @classmethod
    def clear_all_nodes(cls) -> None:
        """
        清空所有引擎节点（用于测试或重置）
        """
        cls._all_nodes.clear()
