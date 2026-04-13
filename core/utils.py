"""
工具函数模块

提供 Decimal 精度计算工具函数
"""

from decimal import Decimal, ROUND_HALF_UP, getcontext

# 设置全局精度为28位（Decimal默认）
getcontext().prec = 28


def to_decimal(value) -> Decimal:
    """
    将数值转换为 Decimal 类型
    
    Args:
        value: 输入值（int, float, str, Decimal）
        
    Returns:
        Decimal 类型的值
        
    Examples:
        >>> to_decimal(100)
        Decimal('100')
        >>> to_decimal(0.1)
        Decimal('0.1')
        >>> to_decimal("123.456")
        Decimal('123.456')
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    if isinstance(value, float):
        # 将float转为字符串再转Decimal，避免精度问题
        return Decimal(str(value))
    raise TypeError(f"无法将 {type(value)} 转换为 Decimal")


def d(value) -> Decimal:
    """
    快捷转换函数，将数值转换为 Decimal
    
    Args:
        value: 输入值
        
    Returns:
        Decimal 类型的值
    """
    return to_decimal(value)


D0 = Decimal('0')
D1 = Decimal('1')
