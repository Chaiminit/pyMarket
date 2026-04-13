import datetime
import math
from typing import List, Tuple, Callable, Any, Dict, Optional
from decimal import Decimal

try:
    from PyQt5.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QComboBox,
        QFrame,
    )
    from PyQt5.QtCore import Qt, QTimer, QRectF
    from PyQt5.QtGui import (
        QPainter,
        QColor,
        QPen,
        QBrush,
        QFont,
        QPainterPath,
        QLinearGradient,
        QRadialGradient,
    )
except ImportError:
    try:
        from PySide6.QtWidgets import (
            QApplication,
            QMainWindow,
            QWidget,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QComboBox,
            QFrame,
        )
        from PySide6.QtCore import Qt, QTimer, QRectF
        from PySide6.QtGui import (
            QPainter,
            QColor,
            QPen,
            QBrush,
            QFont,
            QPainterPath,
            QLinearGradient,
            QRadialGradient,
        )
    except ImportError:
        raise ImportError("需要安装 PyQt5 或 PySide6: pip install PyQt5")

import pandas as pd
import numpy as np


def calculate_candles(
    trade_log: List[Tuple[float, Any, Any]], period: float, max_candles: int
) -> pd.DataFrame:
    if not trade_log:
        return pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])

    if not hasattr(calculate_candles, "_cache"):
        calculate_candles._cache = {}

    cache_key = (id(trade_log), period, max_candles)
    if cache_key in calculate_candles._cache:
        cached_result, cached_length = calculate_candles._cache[cache_key]
        if len(trade_log) == cached_length:
            return cached_result.copy()

    sorted_log = sorted(trade_log, key=lambda x: x[0])

    candles = []
    current_candle_start = None
    current_candle_data = []
    accumulated_error = 0.0

    open_price = close_price = high_price = low_price = total_volume = 0.0

    for trade in sorted_log:
        trade_time, price, volume = trade

        price_float = float(price) if isinstance(price, Decimal) else float(price)
        volume_float = float(volume) if isinstance(volume, Decimal) else float(volume)

        if current_candle_start is None:
            current_candle_start = trade_time
            current_candle_data = [(trade_time, price_float, volume_float)]
            continue

        time_diff = trade_time - current_candle_start + accumulated_error

        if time_diff < period:
            current_candle_data.append((trade_time, price_float, volume_float))
        else:
            if current_candle_data:
                open_price = current_candle_data[0][1]
                close_price = current_candle_data[-1][1]
                high_price = low_price = open_price
                total_volume = 0

                for _, p, v in current_candle_data:
                    if p > high_price:
                        high_price = p
                    if p < low_price:
                        low_price = p
                    total_volume += v

                candle = {
                    "time": datetime.datetime.fromtimestamp(current_candle_start),
                    "open": open_price,
                    "close": close_price,
                    "high": high_price,
                    "low": low_price,
                    "volume": total_volume,
                }
                candles.append(candle)

            ideal_end_time = current_candle_start + period
            error_time = trade_time - ideal_end_time
            accumulated_error = max(0, error_time)

            current_candle_start = trade_time
            current_candle_data = [(trade_time, price_float, volume_float)]

    if current_candle_data:
        open_price = current_candle_data[0][1]
        close_price = current_candle_data[-1][1]
        high_price = low_price = open_price
        total_volume = 0

        for _, p, v in current_candle_data:
            if p > high_price:
                high_price = p
            if p < low_price:
                low_price = p
            total_volume += v

        candle = {
            "time": datetime.datetime.fromtimestamp(current_candle_start),
            "open": open_price,
            "close": close_price,
            "high": high_price,
            "low": low_price,
            "volume": total_volume,
        }
        candles.append(candle)

    candles_df = pd.DataFrame(candles)

    if len(candles_df) > max_candles:
        candles_df = candles_df.iloc[-max_candles:].reset_index(drop=True)

    calculate_candles._cache[cache_key] = (candles_df.copy(), len(trade_log))

    if len(calculate_candles._cache) > 10:
        oldest_key = next(iter(calculate_candles._cache.keys()))
        del calculate_candles._cache[oldest_key]

    return candles_df


def get_pair_name(pair) -> str:
    if hasattr(pair, "base_token") and hasattr(pair, "quote_token"):
        base = pair.base_token.name if hasattr(pair.base_token, "name") else str(pair.base_token)
        quote = (
            pair.quote_token.name if hasattr(pair.quote_token, "name") else str(pair.quote_token)
        )
        return f"{base}/{quote}"
    if hasattr(pair, "token_name"):
        return f"{pair.token_name}-BOND"
    return str(pair)


class CandlestickWidget(QWidget):
    """K线图绘制组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.candles_df = pd.DataFrame()
        self.volume_df = pd.DataFrame()
        self.padding = 50
        self.candle_width_ratio = 0.7
        self.price_min = 0
        self.price_max = 0
        self.volume_max = 0
        self.visible_candles = 100
        self.scroll_offset = 0
        self.zoom_level = 1.0
        self.last_mouse_pos = None
        self.setMouseTracking(True)
        self.setMinimumSize(600, 400)
        self.crosshair_pos = None
        self.current_price_line = None

        # 动画相关
        self.animated_candles = []  # 动画中的K线数据
        self.animated_price_min = 0
        self.animated_price_max = 0
        self.animated_volume_max = 0
        self.animated_current_price = None
        self.animation_speed = 0.1  # 缓动速度，0-1之间

    def set_data(self, candles_df: pd.DataFrame):
        """设置K线数据"""
        if candles_df.empty or len(candles_df) < 2:
            return

        self.candles_df = candles_df.copy()

        # 计算价格范围
        if not self.candles_df.empty:
            target_price_min = self.candles_df["low"].min()
            target_price_max = self.candles_df["high"].max()
            price_range = target_price_max - target_price_min
            if price_range == 0:
                price_range = target_price_max * 0.01
            target_price_min -= price_range * 0.05
            target_price_max += price_range * 0.05

            # 计算成交量范围
            self.volume_df = self.candles_df[["time", "open", "close", "volume"]].copy()
            target_volume_max = self.candles_df["volume"].max() if len(self.candles_df) > 0 else 0

            # 设置当前价线
            target_current_price = (
                self.candles_df.iloc[-1]["close"] if len(self.candles_df) > 0 else None
            )

            # 初始化动画数据
            target_candles = self.candles_df.to_dict("records")

            if not self.animated_candles:
                # 第一次初始化
                self.animated_price_min = target_price_min
                self.animated_price_max = target_price_max
                self.animated_volume_max = target_volume_max
                self.animated_current_price = target_current_price
                self.animated_candles = target_candles.copy()
            else:
                # 检测是否是滚动更新（K线数量不变但内容变化）
                is_scroll_update = False
                if len(target_candles) == len(self.animated_candles) and len(target_candles) > 0:
                    # 比较时间戳，判断是否是滚动更新
                    # 滚动更新时，时间戳列表会整体向前移动
                    target_times = [c.get("time") for c in target_candles]
                    anim_times = [c.get("time") for c in self.animated_candles]

                    # 检查是否是滚动（除了最后一个K线，其他K线都不同）
                    if target_times != anim_times:
                        # 检查是否有重叠的K线（即滚动更新）
                        # 滚动时，target_candles[0] 应该等于 anim_times[1]
                        if len(target_candles) > 1 and anim_times[1:] == target_times[:-1]:
                            is_scroll_update = True

                if is_scroll_update:
                    # 是滚动更新，将动画数据向前移动
                    # 这样animated_candles[i] 对应 target_candles[i]
                    # 只有最后一个K线是新的，需要动画
                    self.animated_candles = self.animated_candles[1:]
                    # 复制倒数第二个K线的状态作为新K线的初始状态
                    if len(self.animated_candles) > 0:
                        new_candle = self.animated_candles[-1].copy()
                        self.animated_candles.append(new_candle)
                    else:
                        self.animated_candles.append({})
                else:
                    # 不是滚动更新，正常处理长度变化
                    while len(self.animated_candles) < len(target_candles):
                        # 新增K线时，添加空对象
                        self.animated_candles.append({})
                    while len(self.animated_candles) > len(target_candles):
                        # 减少K线时，保留末尾的
                        self.animated_candles = self.animated_candles[-len(target_candles) :]

                # 更新目标值
                self.price_min = target_price_min
                self.price_max = target_price_max
                self.volume_max = target_volume_max
                self.current_price_line = target_current_price

            self.adjust_view_to_show_all()
            self.update()

    def adjust_view_to_show_all(self):
        """调整视图显示所有数据"""
        if self.candles_df is None or len(self.candles_df) == 0:
            return
        total_candles = len(self.candles_df)
        visible_width = self.width() - 2 * self.padding
        if visible_width <= 0:
            return
        candle_spacing = visible_width / min(total_candles, self.visible_candles)
        self.zoom_level = candle_spacing / (visible_width / self.visible_candles)
        self.scroll_offset = max(0, total_candles - self.visible_candles)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # 背景
        painter.fillRect(0, 0, w, h, QColor(25, 25, 35))

        # K线区域和成交量区域 (K线占85%，成交量占15%)
        chart_height = int(h * 0.85)
        volume_height = h - chart_height - 5

        if self.candles_df is None or len(self.candles_df) < 2:
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Consolas", 12))
            painter.drawText(w // 2 - 60, h // 2, "等待数据...")
            return

        # 执行缓动动画
        self._update_animation()

        # 绘制网格
        self._draw_grid(painter, self.padding, 20, w - self.padding - 20, chart_height - 30)

        # 计算可见的K线范围
        total_candles = len(self.candles_df)
        visible_count = min(total_candles, self.visible_candles)
        start_idx = max(0, int(self.scroll_offset))
        end_idx = min(total_candles, start_idx + visible_count + 1)

        chart_w = w - 2 * self.padding
        if chart_w <= 0 or end_idx <= start_idx:
            return

        candle_spacing = chart_w / visible_count
        candle_width = candle_spacing * self.candle_width_ratio

        # 绘制K线
        for i in range(start_idx, end_idx):
            row = self.candles_df.iloc[i]
            x = self.padding + (i - start_idx) * candle_spacing + candle_spacing / 2

            # 获取当前K线数据
            open_p = row["open"]
            close_p = row["close"]
            high_p = row["high"]
            low_p = row["low"]

            # 缓动到目标值
            if i < len(self.animated_candles):
                anim_candle = self.animated_candles[i]
                open_p = self._ease_value(anim_candle.get("open", open_p), open_p)
                close_p = self._ease_value(anim_candle.get("close", close_p), close_p)
                high_p = self._ease_value(anim_candle.get("high", high_p), high_p)
                low_p = self._ease_value(anim_candle.get("low", low_p), low_p)

            # 坐标转换
            y_open = self._price_to_y(open_p, chart_height)
            y_close = self._price_to_y(close_p, chart_height)
            y_high = self._price_to_y(high_p, chart_height)
            y_low = self._price_to_y(low_p, chart_height)

            # 判断涨跌
            is_up = close_p >= open_p

            if is_up:
                color = QColor(0, 200, 120)  # 绿色上涨
            else:
                color = QColor(230, 70, 70)  # 红色下跌

            # 绘制影线
            painter.setPen(QPen(color, 1))
            center_x = x
            painter.drawLine(int(center_x), int(y_high), int(center_x), int(y_low))

            # 绘制实体
            body_top = min(y_open, y_close)
            body_bottom = max(y_open, y_close)
            body_height = abs(body_bottom - body_top)

            if body_height < 1:
                body_height = 1

            rect = QRectF(center_x - candle_width / 2, body_top, candle_width, body_height)

            if is_up:
                painter.setBrush(QBrush(Qt.NoBrush))
                painter.drawRect(rect)
            else:
                painter.setBrush(QBrush(color))
                painter.drawRect(rect)

        # 绘制成交量
        vol_top = chart_height + 35
        for i in range(start_idx, end_idx):
            row = self.candles_df.iloc[i]
            x = self.padding + (i - start_idx) * candle_spacing + candle_spacing / 2

            vol = row["volume"]
            open_p = row["open"]
            close_p = row["close"]

            # 缓动成交量
            if i < len(self.animated_candles):
                anim_candle = self.animated_candles[i]
                vol = self._ease_value(anim_candle.get("volume", vol), vol)

            if self.animated_volume_max > 0:
                vol_h = (vol / self.animated_volume_max) * (volume_height - 20)
            else:
                vol_h = 0

            is_up = close_p >= open_p
            color = QColor(0, 200, 120, 180) if is_up else QColor(230, 70, 70, 180)

            rect = QRectF(
                x - candle_width / 2, vol_top + (volume_height - 20) - vol_h, candle_width, vol_h
            )
            painter.fillRect(rect, color)

        # 绘制当前价格线
        if self.animated_current_price is not None and len(self.candles_df) > 0:
            y = self._price_to_y(self.animated_current_price, chart_height)
            painter.setPen(QPen(QColor(255, 200, 0), 1, Qt.DashLine))
            painter.drawLine(self.padding, int(y), w - self.padding - 20, int(y))

            # 价格标签
            price_str = f"{self.animated_current_price:.4f}"
            painter.setPen(QPen(QColor(255, 200, 0)))
            painter.setFont(QFont("Consolas", 9))
            painter.fillRect(w - self.padding - 18, int(y) - 8, 55, 16, QColor(40, 40, 50))
            painter.drawText(w - self.padding - 15, int(y) + 4, price_str)

        # 绘制十字光标
        if self.crosshair_pos:
            mx, my = self.crosshair_pos
            painter.setPen(QPen(QColor(100, 100, 100), 1, Qt.DashLine))
            painter.drawLine(mx, 20, mx, chart_height - 10)
            painter.drawLine(self.padding, my, w - self.padding - 20, my)

        # 绘制Y轴价格标签
        self._draw_price_labels(painter, chart_height, w)

        # 绘制X轴时间标签
        self._draw_time_labels(painter, chart_height, volume_height)

        # 即使没有新数据，也继续刷新以保持动画
        if self.candles_df is not None and len(self.candles_df) >= 2:
            self.update()

    def _update_animation(self):
        """更新动画状态"""
        # 缓动价格范围
        if hasattr(self, "price_min") and hasattr(self, "price_max"):
            self.animated_price_min = self._ease_value(self.animated_price_min, self.price_min)
            self.animated_price_max = self._ease_value(self.animated_price_max, self.price_max)

        # 缓动成交量最大值
        if hasattr(self, "volume_max"):
            self.animated_volume_max = self._ease_value(self.animated_volume_max, self.volume_max)

        # 缓动当前价格
        if hasattr(self, "current_price_line") and self.current_price_line is not None:
            self.animated_current_price = self._ease_value(
                self.animated_current_price, self.current_price_line
            )

        # 缓动K线数据
        if len(self.candles_df) > 0:
            target_candles = self.candles_df.to_dict("records")

            # 确保animated_candles长度匹配
            while len(self.animated_candles) < len(target_candles):
                self.animated_candles.append({})
            while len(self.animated_candles) > len(target_candles):
                self.animated_candles.pop()

            # 缓动每个K线的属性
            for i, target_candle in enumerate(target_candles):
                if i < len(self.animated_candles):
                    anim_candle = self.animated_candles[i]
                    for key in ["open", "close", "high", "low", "volume"]:
                        if key in target_candle:
                            # 确保动画值平滑过渡
                            current_value = anim_candle.get(key, target_candle[key])
                            anim_candle[key] = self._ease_value(current_value, target_candle[key])

    def _ease_value(self, current, target):
        """缓动值到目标值"""
        if current is None:
            return target
        return current + (target - current) * self.animation_speed

    def _price_to_y(self, price, chart_height) -> float:
        """将价格转换为Y坐标"""
        if self.animated_price_max == self.animated_price_min:
            return chart_height / 2
        ratio = (price - self.animated_price_min) / (
            self.animated_price_max - self.animated_price_min
        )
        return chart_height - 30 - ratio * (chart_height - 50)

    def _draw_grid(self, painter, x, y, w, h):
        """绘制网格"""
        painter.setPen(QPen(QColor(45, 45, 55), 1))

        # 水平线
        for i in range(5):
            line_y = y + i * h / 4
            painter.drawLine(x, int(line_y), int(x + w), int(line_y))

        # 垂直线
        for i in range(6):
            line_x = x + i * w / 5
            painter.drawLine(int(line_x), y, int(line_x), int(y + h))

    def _draw_price_labels(self, painter, chart_height, w):
        """绘制Y轴价格标签"""
        painter.setFont(QFont("Consolas", 8))
        painter.setPen(QColor(140, 140, 140))

        for i in range(5):
            ratio = i / 4
            price = self.animated_price_min + ratio * (
                self.animated_price_max - self.animated_price_min
            )
            y = chart_height - 30 - ratio * (chart_height - 50)
            price_str = f"{price:.4f}"
            painter.drawText(w - self.padding - 15, int(y) + 4, price_str)

    def _draw_time_labels(self, painter, chart_height, volume_height):
        """绘制X轴时间标签"""
        if self.candles_df is None or len(self.candles_df) < 2:
            return

        painter.setFont(QFont("Consolas", 8))
        painter.setPen(QColor(140, 140, 140))

        total_candles = len(self.candles_df)
        visible_count = min(total_candles, self.visible_candles)
        start_idx = max(0, int(self.scroll_offset))

        chart_w = self.width() - 2 * self.padding
        candle_spacing = chart_w / visible_count

        # 显示5个时间标签
        label_count = 5
        step = max(1, visible_count // label_count)

        for i in range(0, visible_count, step):
            idx = start_idx + i
            if idx >= len(self.candles_df):
                break

            row = self.candles_df.iloc[idx]
            time_val = row["time"]

            if isinstance(time_val, datetime.datetime):
                time_str = time_val.strftime("%H:%M")
            else:
                time_str = str(time_val)[:5]

            x = self.padding + i * candle_spacing + candle_spacing / 2
            y = self.height() - 15
            painter.drawText(int(x) - 15, int(y), time_str)

    def mouseMoveEvent(self, event):
        self.crosshair_pos = (event.x(), event.y())
        self.update()

    def leaveEvent(self, event):
        self.crosshair_pos = None
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        old_zoom = self.zoom_level

        if delta > 0:
            self.zoom_level *= 1.1
        else:
            self.zoom_level /= 1.1

        self.zoom_level = max(0.3, min(5.0, self.zoom_level))
        self.update()


class ChartWindow(QMainWindow):
    """主图表窗口"""

    def __init__(
        self,
        trading_pairs: List[Any],
        window_title: str = "市场模拟",
        candle_period: float = 5.0,
        max_candles: int = 100,
    ):
        super().__init__()
        self.trading_pairs = trading_pairs
        self.current_pair = trading_pairs[0] if trading_pairs else None
        self.last_lengths = {pair: 0 for pair in trading_pairs}
        self.max_candles = max_candles
        self.candle_period = candle_period

        self.setWindowTitle(window_title)
        self.setGeometry(100, 100, 1000, 700)

        self._setup_ui()
        self._setup_timer()

        if self.current_pair:
            self._initial_plot()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # 顶部工具栏
        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("交易对:"))
        self.pair_combo = QComboBox()
        pair_names = [get_pair_name(p) for p in self.trading_pairs]
        self.pair_combo.addItems(pair_names)
        self.pair_combo.currentIndexChanged.connect(self._on_pair_changed)
        toolbar.addWidget(self.pair_combo)

        toolbar.addStretch()

        # 当前价格显示
        self.price_label = QLabel("--")
        self.price_label.setFont(QFont("Consolas", 11, QFont.Bold))
        self.price_label.setStyleSheet("color: #00CC66; padding: 2px 8px;")
        toolbar.addWidget(QLabel("最新价:"))
        toolbar.addWidget(self.price_label)

        toolbar_layout = QWidget()
        toolbar_layout.setLayout(toolbar)
        toolbar_layout.setMaximumHeight(60)  # 增加工具栏高度
        layout.addWidget(toolbar_layout)

        # K线图
        self.chart_widget = CandlestickWidget()
        layout.addWidget(self.chart_widget, 1)  # 添加 stretch factor 让K线图占据剩余空间

    def _setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_callback)
        self.timer.start(16)  # ~60 FPS

    def _initial_plot(self):
        if not self.current_pair:
            return

        current_log = self.current_pair.log if hasattr(self.current_pair, "log") else []
        candles_df = calculate_candles(current_log, self.candle_period, self.max_candles)

        if not candles_df.empty:
            self.chart_widget.set_data(candles_df)
            self._update_price_display()

    def _on_pair_changed(self, index):
        if 0 <= index < len(self.trading_pairs):
            self.switch_pair(self.trading_pairs[index])

    def switch_pair(self, pair):
        """切换到指定交易对"""
        if pair not in self.trading_pairs:
            return

        self.current_pair = pair
        self.last_lengths[pair] = -1

        # 更新下拉框
        pair_name = get_pair_name(pair)
        idx = self.pair_combo.findText(pair_name)
        if idx >= 0:
            self.pair_combo.blockSignals(True)
            self.pair_combo.setCurrentIndex(idx)
            self.pair_combo.blockSignals(False)

        self.setWindowTitle(f"{pair_name}")
        self._initial_plot()

    def _update_callback(self):
        """定时更新回调"""
        if not self.current_pair:
            return

        try:
            current_log = self.current_pair.log if hasattr(self.current_pair, "log") else []
            current_length = len(current_log)
            last_length = self.last_lengths.get(self.current_pair, 0)

            if current_length == last_length and last_length != -1:
                return

            if current_length < 2:
                return

            candles_df = calculate_candles(current_log, self.candle_period, self.max_candles)

            if candles_df.empty or len(candles_df) < 2:
                return

            if candles_df["open"].isna().any() or candles_df["close"].isna().any():
                return

            if (candles_df["close"] <= 0).any():
                return

            self.chart_widget.set_data(candles_df)
            self._update_price_display()

            self.last_lengths[self.current_pair] = current_length
        except Exception as e:
            pass

    def _update_price_display(self):
        """更新价格显示"""
        if self.current_pair and hasattr(self.current_pair, "price"):
            price = self.current_pair.price
            self.price_label.setText(f"{price:.4f}")
            self.price_label.setStyleSheet("color: #00CC66; padding: 2px 8px;")
        elif self.current_pair and hasattr(self.current_pair, "current_rate"):
            rate = self.current_pair.current_rate * 100
            self.price_label.setText(f"{rate:.4f}%")
            self.price_label.setStyleSheet("color: #FF9933; padding: 2px 8px;")


# 全局状态（保持接口兼容）
_current_pair = None
_all_pairs = []
_last_lengths = {}
_chart_window = None


def switch_pair(pair):
    """切换到指定交易对的数据源"""
    global _current_pair

    if _chart_window:
        _chart_window.switch_pair(pair)

    _current_pair = pair
    if pair in _last_lengths:
        _last_lengths[pair] = -1


def start_gui(
    trading_pairs: List[Any],
    max_candles: int = 100,
    candle_period: float = 1,
    window_title: str = "市场模拟",
) -> Callable:
    """
    启动 GUI，使用 PyQt 实现
    """
    global _current_pair, _all_pairs, _last_lengths, _chart_window

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    _all_pairs = trading_pairs
    _last_lengths = {pair: 0 for pair in trading_pairs}

    if trading_pairs:
        _current_pair = trading_pairs[0]

    _chart_window = ChartWindow(trading_pairs, window_title, candle_period, max_candles)
    _chart_window.show()

    def update_func():
        if _chart_window:
            _chart_window._update_callback()

    app.exec_()

    return update_func


def get_current_pair():
    """获取当前显示的交易对"""
    global _current_pair, _chart_window
    if _chart_window:
        return _chart_window.current_pair
    return _current_pair


def get_all_pairs():
    """获取所有交易对"""
    return _all_pairs
