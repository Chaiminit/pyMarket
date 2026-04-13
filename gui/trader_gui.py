"""
交易者 GUI - tkinter 窗口
允许用户扮演交易者进行交易
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
from typing import Optional
from datetime import datetime


class TraderGUI:
    """交易者 GUI 窗口 - 重新设计布局"""

    def __init__(self, engine, bot_id: int = 0):
        self.engine = engine
        self.bot_id = bot_id
        self.trader = None
        self.root: Optional[tk.Tk] = None
        self._stop_event = threading.Event()
        self._update_thread: Optional[threading.Thread] = None

        self._ensure_trader_exists()
        self._create_window()

    def _ensure_trader_exists(self):
        """确保交易者存在"""
        self.trader = self.engine.bot_manager.get_bot(self.bot_id)
        if self.trader is None:
            # 创建新玩家
            self.bot_id = self.engine.create_player("Player")
            self.trader = self.engine.bot_manager.get_bot(self.bot_id)
            quote_token = self.engine.get_quote_token()
            if quote_token:
                self.engine.allocate_assets_to_bot(self.bot_id, quote_token, 100000.0)
            pair_ids = list(self.engine.trading_pairs.keys())
            if pair_ids:
                self.engine.set_bot_trading_pairs(self.bot_id, pair_ids)
            bond_pair_ids = list(self.engine.bond_trading_pairs.keys())
            if bond_pair_ids:
                self.engine.set_bot_bond_pairs(self.bot_id, bond_pair_ids)

    def _create_window(self):
        """创建主窗口 - 新布局"""
        self.root = tk.Tk()
        self.root.title(f"交易者控制台 - {self.trader.name}")
        self.root.geometry("900x700")
        self.root.resizable(True, True)

        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 配置网格权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)

        # ===== 顶部：资产信息 =====
        self._create_assets_frame(main_frame)

        # ===== 中间：交易操作 =====
        self._create_trade_frame(main_frame)

        # ===== 底部：订单和日志 =====
        self._create_orders_frame(main_frame)
        self._create_log_frame(main_frame)

        # 启动自动更新
        self._start_auto_update()

        # 关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_assets_frame(self, parent):
        """创建资产显示区域"""
        assets_frame = ttk.LabelFrame(parent, text="资产持仓", padding="10")
        assets_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        assets_frame.columnconfigure(0, weight=1)

        # 总资产和净资产标签
        self.total_assets_var = tk.StringVar(value="总资产: 0.00 USDT")
        self.net_assets_var = tk.StringVar(value="净资产: 0.00 USDT")

        ttk.Label(
            assets_frame, textvariable=self.total_assets_var, font=("Consolas", 11, "bold")
        ).grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        ttk.Label(
            assets_frame, textvariable=self.net_assets_var, font=("Consolas", 11, "bold")
        ).grid(row=1, column=0, sticky=tk.W, pady=(0, 5))

        # 资产文本框
        self.assets_text = tk.Text(assets_frame, height=4, wrap=tk.WORD, font=("Consolas", 10))
        self.assets_text.grid(row=2, column=0, sticky=(tk.W, tk.E))

        scrollbar = ttk.Scrollbar(
            assets_frame, orient=tk.HORIZONTAL, command=self.assets_text.xview
        )
        scrollbar.grid(row=3, column=0, sticky=(tk.W, tk.E))
        self.assets_text.config(xscrollcommand=scrollbar.set)

    def _create_trade_frame(self, parent):
        """创建交易操作区域"""
        trade_frame = ttk.LabelFrame(parent, text="交易操作", padding="10")
        trade_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N), pady=(0, 10))

        # 交易对选择（合并普通和债券）
        ttk.Label(trade_frame, text="交易对:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.pair_var = tk.StringVar()
        self.pair_combo = ttk.Combobox(
            trade_frame, textvariable=self.pair_var, state="readonly", width=20
        )
        self.pair_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        self._update_pair_list()
        self.pair_combo.bind("<<ComboboxSelected>>", self._on_pair_selected)

        # 当前价格/利率显示（单独一行）
        ttk.Label(trade_frame, text="当前价格:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.current_price_var = tk.StringVar(value="--")
        self.current_price_label = ttk.Label(
            trade_frame,
            textvariable=self.current_price_var,
            font=("Consolas", 9),
            foreground="#0066CC",
        )
        self.current_price_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        # 方向
        ttk.Label(trade_frame, text="方向:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.direction_var = tk.StringVar(value="买入")
        direction_combo = ttk.Combobox(
            trade_frame,
            textvariable=self.direction_var,
            values=["买入", "卖出"],
            state="readonly",
            width=10,
        )
        direction_combo.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        # 订单类型
        ttk.Label(trade_frame, text="类型:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        self.order_type_var = tk.StringVar(value="市价")
        order_type_combo = ttk.Combobox(
            trade_frame,
            textvariable=self.order_type_var,
            values=["市价", "限价"],
            state="readonly",
            width=10,
        )
        order_type_combo.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)
        order_type_combo.bind("<<ComboboxSelected>>", self._on_order_type_change)

        # 价格（限价单）
        ttk.Label(trade_frame, text="价格/利率:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=2)
        self.price_var = tk.StringVar()
        self.price_entry = tk.Entry(
            trade_frame, textvariable=self.price_var, width=15, state="disabled"
        )
        self.price_entry.grid(row=4, column=1, sticky=tk.W, padx=5, pady=2)

        # 数量
        ttk.Label(trade_frame, text="数量:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=2)
        self.volume_var = tk.StringVar(value="1.0")
        self.volume_entry = tk.Entry(trade_frame, textvariable=self.volume_var, width=15)
        self.volume_entry.grid(row=5, column=1, sticky=tk.W, padx=5, pady=2)

        # 提交按钮
        ttk.Button(trade_frame, text="提交订单", command=self._submit_order).grid(
            row=6, column=0, columnspan=2, pady=10
        )

        # 快捷操作
        quick_frame = ttk.Frame(trade_frame)
        quick_frame.grid(row=7, column=0, columnspan=2, pady=5)
        ttk.Button(quick_frame, text="市价全买", command=self._market_buy_all).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(quick_frame, text="市价全卖", command=self._market_sell_all).pack(
            side=tk.LEFT, padx=2
        )

    def _create_orders_frame(self, parent):
        """创建当前订单显示区域"""
        orders_frame = ttk.LabelFrame(parent, text="当前订单", padding="10")
        orders_frame.grid(
            row=1, column=1, rowspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10), padx=(10, 0)
        )
        orders_frame.columnconfigure(0, weight=1)
        orders_frame.rowconfigure(0, weight=1)

        # 订单列表
        self.orders_text = tk.Text(orders_frame, height=15, wrap=tk.WORD, font=("Consolas", 9))
        self.orders_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        scrollbar = ttk.Scrollbar(orders_frame, orient=tk.VERTICAL, command=self.orders_text.yview)
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.orders_text.config(yscrollcommand=scrollbar.set)

        # 取消订单区域
        cancel_frame = ttk.Frame(orders_frame)
        cancel_frame.grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky=(tk.W, tk.E))
        ttk.Label(cancel_frame, text="订单ID:").pack(side=tk.LEFT, padx=(0, 5))
        self.cancel_order_id_var = tk.StringVar()
        ttk.Entry(cancel_frame, textvariable=self.cancel_order_id_var, width=10).pack(
            side=tk.LEFT, padx=(0, 5)
        )
        ttk.Button(cancel_frame, text="取消订单", command=self._cancel_order).pack(side=tk.LEFT)
        ttk.Button(cancel_frame, text="取消全部", command=self._cancel_all_orders).pack(
            side=tk.LEFT, padx=(5, 0)
        )

    def _create_log_frame(self, parent):
        """创建日志显示区域"""
        log_frame = ttk.LabelFrame(parent, text="交易日志", padding="10")
        log_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=8, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.log_text.config(yscrollcommand=scrollbar.set)

    def _update_pair_list(self):
        """更新交易对列表（合并普通和债券）"""
        pairs = []
        # 普通交易对
        for p in self.engine.trading_pairs.values():
            pairs.append(f"{p.base_token}/{p.quote_token}")
        # 债券交易对
        for b in self.engine.bond_trading_pairs.values():
            pairs.append(f"{b.token_name}-BOND")
        self.pair_combo["values"] = pairs
        if pairs and not self.pair_var.get():
            self.pair_var.set(pairs[0])

    def _on_pair_selected(self, event=None):
        """交易对选择改变时，更新当前价格显示"""
        pair_name = self.pair_var.get()
        if not pair_name:
            return

        # 立即更新价格显示
        self._update_current_price()

    def _on_order_type_change(self, event=None):
        """订单类型改变时启用/禁用价格输入"""
        if self.order_type_var.get() == "限价":
            self.price_entry.config(state="normal")
        else:
            self.price_entry.config(state="disabled")

    def _get_pair_info(self, pair_name: str) -> tuple:
        """根据名称获取交易对信息，返回 (类型, id)"""
        # 检查普通交易对
        for pid, pair in self.engine.trading_pairs.items():
            if f"{pair.base_token}/{pair.quote_token}" == pair_name:
                return ("spot", pid)
        # 检查债券交易对
        for bid, bond in self.engine.bond_trading_pairs.items():
            if f"{bond.token_name}-BOND" == pair_name:
                return ("bond", bid)
        return ("", 0)

    def _update_current_price(self):
        """更新当前交易对的价格/利率显示"""
        pair_name = self.pair_var.get()
        if not pair_name:
            self.current_price_var.set("--")
            return

        market_type, pair_id = self._get_pair_info(pair_name)

        if market_type == "spot":
            pair = self.engine.trading_pairs.get(pair_id)
            if pair:
                price = pair.price
                self.current_price_var.set(f"{price:.8f}")
                self.current_price_label.config(foreground="#0066CC")
        elif market_type == "bond":
            bond = self.engine.bond_trading_pairs.get(pair_id)
            if bond:
                rate = bond.current_rate
                rate_percent = rate * 100
                self.current_price_var.set(f"{rate_percent:.6f}%")
                self.current_price_label.config(foreground="#CC6600")
        else:
            self.current_price_var.set("--")

    def _submit_order(self):
        """提交订单"""
        try:
            pair_name = self.pair_var.get()
            if not pair_name:
                self._log("错误：请选择交易对")
                return

            volume_str = self.volume_var.get().strip()
            if not volume_str:
                self._log("错误：请输入数量")
                return

            volume = float(volume_str)
            if volume <= 0:
                self._log("错误：数量必须大于 0")
                return

            direction = self.direction_var.get()
            order_type = self.order_type_var.get()
            market_type, pair_id = self._get_pair_info(pair_name)

            if market_type == "spot":
                pair = self.engine.trading_pairs[pair_id]
                if order_type == "市价":
                    dir_code = "buy" if direction == "买入" else "sell"
                    executed, trade_details = pair.execute_market_order(
                        self.trader, dir_code, volume
                    )
                    self._log_market_result(pair.base_token, direction, executed, trade_details)
                else:
                    # 限价单
                    price_str = self.price_var.get().strip()
                    if not price_str:
                        self._log("错误：请输入价格")
                        return
                    price = float(price_str)
                    dir_code = "buy" if direction == "买入" else "sell"

                    if dir_code == "buy":
                        required = price * volume
                        if self.trader.assets.get(pair.quote_token, 0.0) < required:
                            self._log("错误：余额不足")
                            return
                        self.trader.assets[pair.quote_token] -= required
                    else:
                        if self.trader.assets.get(pair.base_token, 0.0) < volume:
                            self._log("错误：余额不足")
                            return
                        self.trader.assets[pair.base_token] -= volume

                    pair.submit_limit_order(
                        self.trader,
                        dir_code,
                        price,
                        volume,
                        price * volume if dir_code == "buy" else volume,
                    )
                    self._log(
                        f"✓ 限价单已提交：{direction} {pair_name}, 价格：{price:.4f}, 量：{volume:.4f}"
                    )
            elif market_type == "bond":
                # 债券交易
                bond = self.engine.bond_trading_pairs[pair_id]
                if order_type == "市价":
                    dir_code = "buy" if direction == "买入" else "sell"
                    executed, trade_details = bond.execute_market_order(
                        self.trader, dir_code, volume
                    )
                    self._log_bond_result(bond.token_name, direction, executed, trade_details)
                else:
                    # 债券限价单
                    rate_str = self.price_var.get().strip()
                    if not rate_str:
                        self._log("错误：请输入利率")
                        return
                    rate = float(rate_str)
                    dir_code = "buy" if direction == "买入" else "sell"

                    if dir_code == "buy":
                        if self.trader.assets.get(bond.token_name, 0.0) < volume:
                            self._log("错误：余额不足")
                            return

                    bond.submit_limit_order(self.trader, dir_code, rate, volume)
                    self._log(
                        f"✓ 债券限价单已提交：{direction} {pair_name}, 利率：{rate:.6f}, 量：{volume:.4f}"
                    )

        except Exception as e:
            self._log(f"交易失败：{e}")

    def _log_market_result(self, base_token: str, direction: str, executed: float, details: list):
        """记录市价单结果"""
        if executed > 0:
            if len(details) == 1:
                d = details[0]
                if direction == "买入":
                    self._log(
                        f"✓ 买入成交：{executed:.4f} {base_token}, 均价：{d['price']:.4f}, 总成本：{d['cost']:.2f}"
                    )
                else:
                    self._log(
                        f"✓ 卖出成交：{executed:.4f} {base_token}, 均价：{d['price']:.4f}, 总收入：{d['revenue']:.2f}"
                    )
            else:
                total = sum(d.get("cost", d.get("revenue", 0)) for d in details)
                avg = total / executed if executed > 0 else 0
                self._log(
                    f"✓ {direction}成交：{executed:.4f} {base_token}, 均价：{avg:.4f} ({len(details)}笔)"
                )
        else:
            self._log(f"✗ 市价单未成交")

    def _log_bond_result(self, token_name: str, direction: str, executed: float, details: list):
        """记录债券市价单结果"""
        if executed > 0:
            self._log(f"✓ 债券{direction}成交：{executed:.4f} {token_name}")
        else:
            self._log(f"✗ 债券市价单未成交")

    def _market_buy_all(self):
        """市价买入全部"""
        try:
            pair_name = self.pair_var.get()
            if not pair_name:
                return
            market_type, pair_id = self._get_pair_info(pair_name)

            if market_type == "spot":
                pair = self.engine.trading_pairs[pair_id]
                available = self.trader.assets.get(pair.quote_token, 0.0)
                if available > 0 and pair.price > 0:
                    volume = available / pair.price * 0.99  # 留一点余量
                    executed, details = pair.execute_market_order(self.trader, "buy", volume)
                    self._log_market_result(pair.base_token, "买入", executed, details)
            elif market_type == "bond":
                bond = self.engine.bond_trading_pairs[pair_id]
                available = self.trader.assets.get(bond.token_name, 0.0)
                if available > 0:
                    executed, details = bond.execute_market_order(
                        self.trader, "buy", available * 0.99
                    )
                    self._log_bond_result(bond.token_name, "买入", executed, details)
        except Exception as e:
            self._log(f"市价全买失败：{e}")

    def _market_sell_all(self):
        """市价卖出全部"""
        try:
            pair_name = self.pair_var.get()
            if not pair_name:
                return
            market_type, pair_id = self._get_pair_info(pair_name)

            if market_type == "spot":
                pair = self.engine.trading_pairs[pair_id]
                available = self.trader.assets.get(pair.base_token, 0.0)
                if available > 0:
                    executed, details = pair.execute_market_order(self.trader, "sell", available)
                    self._log_market_result(pair.base_token, "卖出", executed, details)
            elif market_type == "bond":
                bond = self.engine.bond_trading_pairs[pair_id]
                bond_key = f"BOND-{bond.token_name}"
                available = self.trader.bonds.get(bond_key, 0.0)
                if available > 0:
                    executed, details = bond.execute_market_order(self.trader, "sell", available)
                    self._log_bond_result(bond.token_name, "卖出", executed, details)
        except Exception as e:
            self._log(f"市价全卖失败：{e}")

    def _cancel_order(self):
        """取消指定订单"""
        try:
            order_id_str = self.cancel_order_id_var.get().strip()
            if not order_id_str:
                self._log("错误：请输入订单 ID")
                return

            order_id = int(order_id_str)
            cancelled = False

            # 在普通交易对中查找
            for pair in self.engine.trading_pairs.values():
                for order in pair.buy_orders + pair.sell_orders:
                    if id(order) == order_id and order.trader is self.trader:
                        # 返还冻结资金
                        if order.direction == "buy":
                            self.trader.assets[pair.quote_token] = (
                                self.trader.assets.get(pair.quote_token, 0.0)
                                + order.remaining_frozen
                            )
                        else:
                            self.trader.assets[pair.base_token] = (
                                self.trader.assets.get(pair.base_token, 0.0)
                                + order.remaining_frozen
                            )

                        if order in pair.buy_orders:
                            pair.buy_orders.remove(order)
                        if order in pair.sell_orders:
                            pair.sell_orders.remove(order)
                        if order in self.trader.orders:
                            self.trader.orders.remove(order)

                        cancelled = True
                        self._log(f"✓ 已取消订单 ID:{order_id}")
                        break
                if cancelled:
                    break

            # 在债券交易对中查找
            if not cancelled:
                for bond in self.engine.bond_trading_pairs.values():
                    for order in bond.buy_orders + bond.sell_orders:
                        if id(order) == order_id and order.trader is self.trader:
                            if order.direction == "buy":
                                self.trader.assets[bond.token_name] = (
                                    self.trader.assets.get(bond.token_name, 0.0)
                                    + order.remaining_frozen
                                )

                            if order in bond.buy_orders:
                                bond.buy_orders.remove(order)
                            if order in bond.sell_orders:
                                bond.sell_orders.remove(order)
                            if order in self.trader.bond_orders:
                                self.trader.bond_orders.remove(order)

                            cancelled = True
                            self._log(f"✓ 已取消订单 ID:{order_id}")
                            break
                    if cancelled:
                        break

            if not cancelled:
                self._log(f"未找到订单 ID:{order_id}")

        except Exception as e:
            self._log(f"取消订单失败：{e}")

    def _cancel_all_orders(self):
        """取消所有订单"""
        try:
            count = 0
            # 取消普通订单
            for pair in self.engine.trading_pairs.values():
                for order in list(pair.buy_orders + pair.sell_orders):
                    if order.trader is self.trader:
                        if order.direction == "buy":
                            self.trader.assets[pair.quote_token] = (
                                self.trader.assets.get(pair.quote_token, 0.0)
                                + order.remaining_frozen
                            )
                        else:
                            self.trader.assets[pair.base_token] = (
                                self.trader.assets.get(pair.base_token, 0.0)
                                + order.remaining_frozen
                            )

                        if order in pair.buy_orders:
                            pair.buy_orders.remove(order)
                        if order in pair.sell_orders:
                            pair.sell_orders.remove(order)
                        count += 1

            # 取消债券订单
            for bond in self.engine.bond_trading_pairs.values():
                for order in list(bond.buy_orders + bond.sell_orders):
                    if order.trader is self.trader:
                        if order.direction == "buy":
                            self.trader.assets[bond.token_name] = (
                                self.trader.assets.get(bond.token_name, 0.0)
                                + order.remaining_frozen
                            )

                        if order in bond.buy_orders:
                            bond.buy_orders.remove(order)
                        if order in bond.sell_orders:
                            bond.sell_orders.remove(order)
                        count += 1

            self.trader.orders.clear()
            self.trader.bond_orders.clear()
            self._log(f"✓ 已取消 {count} 个订单")

        except Exception as e:
            self._log(f"取消全部订单失败：{e}")

    def _update_display(self):
        """更新显示"""
        try:
            # 更新当前交易对价格/利率
            self._update_current_price()

            # 计算总资产和净资产（使用 Trader 的方法）
            total_assets = self.trader.get_total_assets()
            net_assets = self.trader.get_net_assets()
            quote_token = self.engine.get_quote_token() or "USDT"

            self.total_assets_var.set(f"总资产: {total_assets:,.2f} {quote_token}")
            self.net_assets_var.set(f"净资产: {net_assets:,.2f} {quote_token}")

            # 更新资产显示
            self.assets_text.delete(1.0, tk.END)
            assets_str = ""
            for token, amount in sorted(self.trader.assets.items()):
                if amount != 0:
                    assets_str += f"{token}: {amount:,.4f}  "

            # 添加债券持仓
            for bond in self.engine.bond_trading_pairs.values():
                bond_key = f"BOND-{bond.token_name}"
                amount = self.trader.bonds.get(bond_key, 0.0)
                if amount != 0:
                    assets_str += f"{bond_key}: {amount:,.4f}  "

            self.assets_text.insert(tk.END, assets_str if assets_str else "无持仓")

            # 更新订单显示
            self.orders_text.delete(1.0, tk.END)
            has_orders = False

            # 普通订单
            for pair_id, pair in self.engine.trading_pairs.items():
                my_buy = [o for o in pair.buy_orders if o.trader is self.trader]
                my_sell = [o for o in pair.sell_orders if o.trader is self.trader]

                if my_buy or my_sell:
                    has_orders = True
                    self.orders_text.insert(
                        tk.END, f"=== {pair.base_token}/{pair.quote_token} ===\n"
                    )
                    for o in my_buy:
                        remaining = o.volume - o.executed
                        self.orders_text.insert(
                            tk.END, f"[买] ID:{id(o)} 价:{o.price:.4f} 量:{remaining:.4f}\n"
                        )
                    for o in my_sell:
                        remaining = o.volume - o.executed
                        self.orders_text.insert(
                            tk.END, f"[卖] ID:{id(o)} 价:{o.price:.4f} 量:{remaining:.4f}\n"
                        )

            # 债券订单
            for bond_id, bond in self.engine.bond_trading_pairs.items():
                my_buy = [o for o in bond.buy_orders if o.trader is self.trader]
                my_sell = [o for o in bond.sell_orders if o.trader is self.trader]

                if my_buy or my_sell:
                    has_orders = True
                    self.orders_text.insert(tk.END, f"=== {bond.token_name}-BOND ===\n")
                    for o in my_buy:
                        remaining = o.volume - o.executed
                        self.orders_text.insert(
                            tk.END,
                            f"[买] ID:{id(o)} 利率:{o.interest_rate:.6f} 量:{remaining:.4f}\n",
                        )
                    for o in my_sell:
                        remaining = o.volume - o.executed
                        self.orders_text.insert(
                            tk.END,
                            f"[卖] ID:{id(o)} 利率:{o.interest_rate:.6f} 量:{remaining:.4f}\n",
                        )

            if not has_orders:
                self.orders_text.insert(tk.END, "暂无挂单")

        except Exception as e:
            print(f"更新显示错误：{e}")

    def _log(self, message: str):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def _start_auto_update(self):
        """启动自动更新线程"""
        self._update_thread = threading.Thread(target=self._auto_update_loop, daemon=True)
        self._update_thread.start()

    def _auto_update_loop(self):
        """自动更新循环"""
        while not self._stop_event.is_set():
            try:
                if self.root and self.root.winfo_exists():
                    self.root.after(0, self._update_display)
            except Exception as e:
                print(f"自动更新错误：{e}")
            time.sleep(1)

    def _on_close(self):
        """关闭窗口"""
        self._stop_event.set()
        if self.root:
            self.root.destroy()

    def run(self):
        """运行 GUI"""
        if self.root:
            self.root.mainloop()


def start_trader_gui(engine, bot_id: int = 0):
    """启动交易者 GUI"""
    gui = TraderGUI(engine, bot_id)
    gui.run()


if __name__ == "__main__":
    from core.engine import get_engine

    engine = get_engine()
    engine.create_token("USDT", is_quote=True)
    engine.create_token("ETH")
    engine.create_trading_pair("ETH", "USDT", 190.0)
    engine.create_bond_trading_pair("USDT", 0.0005)

    start_trader_gui(engine)
