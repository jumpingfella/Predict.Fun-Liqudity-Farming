"""
GUI интерфейс для управления ликвидностью на Predict Fun
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from typing import Dict, List, Optional, Callable
from threading import Thread
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from logger import log_error_to_file


class TokenFrame(ttk.Frame):
    """Фрейм для отображения информации о токене"""
    
    def __init__(
        self,
        parent,
        market_id: str,
        market_info: Dict,
        settings_manager,
        update_callback: Callable,
        initial_balance: float = 0.0,
        api_key: Optional[str] = None,
        jwt_token: Optional[str] = None,
        predict_account_address: Optional[str] = None,
        privy_wallet_private_key: Optional[str] = None,
        proxy: Optional[str] = None
    ):
        # Фиксируем размер фрейма, чтобы он не пересчитывался при обновлении цен
        super().__init__(parent, relief=tk.RIDGE, borderwidth=2, width=450, height=780)
        self.grid_propagate(False) # Запрещаем менять размер под содержимое
        self.pack_propagate(False) # Для pack тоже
        
        self.market_id = market_id
        self.market_info = market_info
        self.settings_manager = settings_manager
        self.update_callback = update_callback
        self.root = parent.winfo_toplevel()  # Сохраняем ссылку на root для root.after
        
        # Получаем настройки, если они были сохранены пользователем - используем их, иначе дефолтные
        self.settings = settings_manager.get_settings(market_id)
        
        # Инициализируем last_orderbook для пересчета
        self.last_orderbook = None
        
        # Сохраняем последний order_info для подсчета предварительных ордеров
        self.last_order_info = None
        
        # Сохраняем баланс
        self.current_balance = initial_balance
        
        # Время последнего обновления стакана
        self.last_orderbook_update_time = None
        
        # Флаг, были ли реально выставлены ордера (по умолчанию False)
        self.orders_placed = False
        
        # Флаг видимости лога (по умолчанию скрыт)
        self.log_visible = False
        
        # Флаги для отслеживания процесса отмены ордеров (чтобы не дублировать)
        self.cancelling_yes = False
        self.cancelling_no = False
        
        # Флаги для отслеживания процесса выставления ордеров (чтобы не дублировать)
        self.placing_yes = False
        self.placing_no = False
        
        # Общий флаг процесса выставления (для предотвращения одновременных вызовов)
        self.placing_orders = False
        
        # OrderManager для управления ордерами
        self.order_manager = None
        if api_key and jwt_token and predict_account_address and privy_wallet_private_key:
            from order_manager import OrderManager
            self.order_manager = OrderManager(
                market_id=market_id,
                api_key=api_key,
                jwt_token=jwt_token,
                predict_account_address=predict_account_address,
                privy_wallet_private_key=privy_wallet_private_key,
                market_info=market_info,
                proxy=proxy,
                log_func=self.market_log
            )
        
        self.create_widgets()
        self.update_display()
        
        # Привязываем прокрутку мыши к фрейму токена и всем его дочерним элементам
        self._bind_mousewheel()
    
    def _bind_mousewheel(self):
        """Привязывает прокрутку колесиком мыши к фрейму токена и всем его дочерним элементам"""
        # Находим canvas через root window (MainWindow хранит ссылку на canvas)
        canvas = None
        root = self.root
        if hasattr(root, 'canvas'):
            canvas = root.canvas
        else:
            # Пробуем найти через родительские элементы
            parent = self.master
            while parent:
                if isinstance(parent, tk.Canvas):
                    canvas = parent
                    break
                parent = parent.master
        
        if not canvas:
            return
        
        def on_mousewheel(event):
            """Обработчик прокрутки колесиком мыши"""
            # Для Windows и MacOS
            if hasattr(event, 'delta') and event.delta:
                # Windows: event.delta обычно 120 или -120
                # MacOS: event.delta может быть другим значением
                delta = -1 * (event.delta / 120)  # Нормализуем к шагам по 1
            elif hasattr(event, 'num'):
                # Linux: используем event.num
                if event.num == 4:
                    delta = -1
                elif event.num == 5:
                    delta = 1
                else:
                    return
            else:
                return
            
            # Прокручиваем canvas
            canvas.yview_scroll(int(delta), "units")
        
        # Привязываем события прокрутки к самому фрейму
        self.bind("<MouseWheel>", on_mousewheel)
        self.bind("<Button-4>", on_mousewheel)
        self.bind("<Button-5>", on_mousewheel)
        
        # Рекурсивно привязываем ко всем дочерним виджетам
        def bind_to_children(widget):
            """Рекурсивно привязывает события прокрутки к дочерним виджетам"""
            for child in widget.winfo_children():
                try:
                    # Пропускаем текстовые поля (ScrolledText), чтобы не мешать их собственной прокрутке
                    if isinstance(child, (tk.Text, scrolledtext.ScrolledText)):
                        continue
                    child.bind("<MouseWheel>", on_mousewheel)
                    child.bind("<Button-4>", on_mousewheel)
                    child.bind("<Button-5>", on_mousewheel)
                    # Рекурсивно для вложенных виджетов
                    bind_to_children(child)
                except:
                    pass
        
        # Привязываем к дочерним виджетам после создания (используем after_idle для гарантии)
        self.after_idle(lambda: bind_to_children(self))
    
    def market_log(self, message: str):
        """Логирование для конкретного маркета"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        # Если сообщение уже начинается с [market_id], не добавляем его снова
        if message.startswith(f"[{self.market_id}]"):
            log_message = f"[{timestamp}] {message}\n"
            print(f"{message}")
        else:
            log_message = f"[{timestamp}] [{self.market_id}] {message}\n"
            print(f"[{self.market_id}] {message}")
        
        # Добавляем в лог маркета через root.after() чтобы не блокировать поток
        # Это гарантирует, что обновление GUI происходит асинхронно
        if hasattr(self, 'market_log_text'):
            def update_log():
                # Убеждаемся что текстовое поле доступно для вставки
                current_state = self.market_log_text.cget('state')
                if current_state == tk.DISABLED:
                    self.market_log_text.config(state=tk.NORMAL)
                self.market_log_text.insert(tk.END, log_message)
                self.market_log_text.see(tk.END)
                # Оставляем NORMAL для возможности копирования
                self.market_log_text.config(state=tk.NORMAL)
            
            # Выполняем обновление GUI в главном потоке асинхронно
            self.root.after(0, update_log)
    
    def create_widgets(self):
        """Создает виджеты для отображения информации о токене"""
        # Заголовок с названием токена
        title_frame = ttk.Frame(self)
        title_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Название/вопрос рынка
        question = self.market_info.get("question", self.market_info.get("title", f"Market {self.market_id}"))
        self.title_label = ttk.Label(
            title_frame,
            text=question,
            font=("Arial", 10, "bold"),
            wraplength=400
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Кликабельная ссылка на рынок (используем categorySlug, slug, url или market_id)
        slug = (
            self.market_info.get("categorySlug") or 
            self.market_info.get("slug") or 
            self.market_info.get("url") or 
            str(self.market_id)
        )
        print(f"[DEBUG] TokenFrame для рынка {self.market_id}: categorySlug = {self.market_info.get('categorySlug')}, slug = {self.market_info.get('slug')}, url = {self.market_info.get('url')}")
        
        # Если slug содержит полный URL, извлекаем только slug
        if slug.startswith("http"):
            # Извлекаем slug из URL
            if "/market/" in slug:
                slug = slug.split("/market/")[-1]
        
        print(f"[DEBUG] TokenFrame для рынка {self.market_id}: финальный slug = {slug}")
        market_url = f"https://predict.fun/market/{slug}"
        self.link_label = ttk.Label(
            title_frame,
            text="🔗 Открыть рынок",
            font=("Arial", 8),
            foreground="blue",
            cursor="hand2"
        )
        self.link_label.pack(side=tk.LEFT, padx=5)
        self.link_label.bind("<Button-1>", lambda e: self.open_market_url(market_url))
        
        # Информация о рынке
        info_frame = ttk.LabelFrame(self, text="Информация о рынке")
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Yes: Mid-прайс | Bid/Ask
        self.yes_price_label = ttk.Label(
            info_frame,
            text="Yes: Mid -- | Bid/Ask -- / --",
            font=("Arial", 9, "bold")
        )
        self.yes_price_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # No: Mid-прайс | Bid/Ask
        self.no_price_label = ttk.Label(
            info_frame,
            text="No: Mid -- | Bid/Ask -- / --",
            font=("Arial", 9, "bold")
        )
        self.no_price_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Время последнего обновления стакана
        self.last_update_label = ttk.Label(
            info_frame,
            text="Последнее обновление: --",
            font=("Arial", 8),
            foreground="gray"
        )
        self.last_update_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Предварительные ордера
        orders_frame = ttk.LabelFrame(info_frame, text="Предварительные ордера")
        orders_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Yes ордер (цена покупки)
        self.yes_order_label = ttk.Label(
            orders_frame,
            text="Yes: --",
            font=("Arial", 9)
        )
        self.yes_order_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # No ордер (цена покупки)
        self.no_order_label = ttk.Label(
            orders_frame,
            text="No: --",
            font=("Arial", 9)
        )
        self.no_order_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Стоимость ордеров
        self.orders_value_label = ttk.Label(
            orders_frame,
            text="Стоимость: --",
            font=("Arial", 9)
        )
        self.orders_value_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Ликвидность Yes (скрыт, так как информация теперь в строке с ценой ордера)
        self.yes_liquidity_label = ttk.Label(
            orders_frame,
            text="",
            font=("Arial", 9)
        )
        # Не упаковываем, чтобы не занимал место
        
        # Ликвидность No (скрыт, так как информация теперь в строке с ценой ордера)
        self.no_liquidity_label = ttk.Label(
            orders_frame,
            text="",
            font=("Arial", 9)
        )
        # Не упаковываем, чтобы не занимал место
        
        # Баланс
        self.balance_label = ttk.Label(
            info_frame,
            text="Баланс: --",
            font=("Arial", 9)
        )
        self.balance_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Минимальные требования
        requirements_frame = ttk.LabelFrame(info_frame, text="Минимальные требования")
        requirements_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Spread threshold
        self.spread_threshold_label = ttk.Label(
            requirements_frame,
            text="Мин. спред: --",
            font=("Arial", 9)
        )
        self.spread_threshold_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Share threshold
        self.share_threshold_label = ttk.Label(
            requirements_frame,
            text="Мин. холд: --",
            font=("Arial", 9)
        )
        self.share_threshold_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Статус рынка
        self.status_label = ttk.Label(
            info_frame,
            text="Статус: --",
            font=("Arial", 9)
        )
        self.status_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Выставленные ордера
        orders_placed_frame = ttk.LabelFrame(self, text="Выставленные ордера")
        orders_placed_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Yes ордер
        self.yes_placed_label = ttk.Label(
            orders_placed_frame,
            text="Yes: --",
            font=("Arial", 9)
        )
        self.yes_placed_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # No ордер
        self.no_placed_label = ttk.Label(
            orders_placed_frame,
            text="No: --",
            font=("Arial", 9)
        )
        self.no_placed_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Статистика
        self.orders_stats_label = ttk.Label(
            orders_placed_frame,
            text="Выставлено ордеров: 0, Отменено ордеров: 0",
            font=("Arial", 8),
            foreground="gray"
        )
        self.orders_stats_label.pack(anchor=tk.W, padx=5, pady=2)
        
        # Лог для этого маркета
        market_log_frame = ttk.LabelFrame(self, text=f"Лог маркета {self.market_id}")
        market_log_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Кнопка для показа/скрытия лога
        log_header_frame = ttk.Frame(market_log_frame)
        log_header_frame.pack(fill=tk.X, padx=5, pady=2)
        
        self.log_visible = False  # Изначально лог скрыт
        
        self.toggle_log_btn = ttk.Button(
            log_header_frame,
            text="▼ Показать лог",
            command=self.toggle_market_log,
            width=15
        )
        self.toggle_log_btn.pack(side=tk.LEFT)
        
        # Контейнер для текстового поля лога (изначально скрыт)
        self.market_log_container = ttk.Frame(market_log_frame)
        # Не упаковываем его сразу - будет показываться при нажатии кнопки
        
        self.market_log_text = scrolledtext.ScrolledText(
            self.market_log_container,
            height=6,
            font=("Courier", 8),
            wrap=tk.WORD
        )
        self.market_log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Разрешаем выделение и копирование
        # Блокируем только прямое редактирование через обычные клавиши
        def on_key(event):
            # Разрешаем все комбинации с Control (Ctrl+C, Ctrl+A, Ctrl+V и т.д.)
            if event.state & 0x0004:  # Control key
                return None  # Полностью разрешаем все Ctrl+комбинации
            # Разрешаем все комбинации с Shift (выделение)
            if event.state & 0x0001:  # Shift key
                return None
            # Разрешаем функциональные и навигационные клавиши (без символов)
            if not event.char or len(event.char) == 0:
                return None
            # Блокируем только обычный ввод печатных символов (без модификаторов)
            if event.char.isprintable():
                return 'break'
            return None
        
        self.market_log_text.bind('<KeyPress>', on_key)
        
        # Добавляем контекстное меню для правого клика
        market_log_menu = tk.Menu(self.market_log_text, tearoff=0)
        market_log_menu.add_command(label="Копировать", command=lambda: self.market_log_text.event_generate("<<Copy>>"))
        market_log_menu.add_command(label="Выделить все", command=lambda: self.market_log_text.tag_add(tk.SEL, "1.0", tk.END))
        
        def show_market_log_menu(event):
            try:
                market_log_menu.tk_popup(event.x_root, event.y_root)
            finally:
                market_log_menu.grab_release()
        
        self.market_log_text.bind("<Button-3>", show_market_log_menu)  # Button-3 = правый клик
        
        # Настройки
        settings_frame = ttk.LabelFrame(self, text="Настройки")
        settings_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Спред
        spread_frame = ttk.Frame(settings_frame)
        spread_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(spread_frame, text="Спред (цент):").pack(side=tk.LEFT)
        self.spread_var = tk.StringVar(value=str(self.settings.spread_percent))
        spread_entry = ttk.Entry(spread_frame, textvariable=self.spread_var, width=10)
        spread_entry.pack(side=tk.LEFT, padx=5)
        spread_entry.bind("<FocusOut>", self.on_spread_changed)
        spread_entry.bind("<Return>", self.on_spread_changed)  # Enter для быстрого обновления
        
        # Размер позиции
        position_frame = ttk.Frame(settings_frame)
        position_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(position_frame, text="Размер позиции:").pack(side=tk.LEFT)
        
        self.position_type_var = tk.StringVar(value="usdt" if self.settings.position_size_usdt else "shares")
        position_type_combo = ttk.Combobox(
            position_frame,
            textvariable=self.position_type_var,
            values=["usdt", "shares"],
            state="readonly",
            width=8
        )
        position_type_combo.pack(side=tk.LEFT, padx=5)
        position_type_combo.bind("<<ComboboxSelected>>", self.on_position_type_changed)
        
        self.position_size_var = tk.StringVar(
            value=str(self.settings.position_size_usdt or self.settings.position_size_shares or "")
        )
        position_entry = ttk.Entry(position_frame, textvariable=self.position_size_var, width=10)
        position_entry.pack(side=tk.LEFT, padx=5)
        position_entry.bind("<FocusOut>", self.on_position_size_changed)
        position_entry.bind("<Return>", self.on_position_size_changed)  # Enter для быстрого обновления
        
        # Минимальная ликвидность
        liquidity_frame = ttk.Frame(settings_frame)
        liquidity_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(liquidity_frame, text="Мин. ликвидность ($):").pack(side=tk.LEFT)
        self.min_liquidity_var = tk.StringVar(value=str(self.settings.min_liquidity_usdt or 300.0))
        liquidity_entry = ttk.Entry(liquidity_frame, textvariable=self.min_liquidity_var, width=10)
        liquidity_entry.pack(side=tk.LEFT, padx=5)
        liquidity_entry.bind("<FocusOut>", self.on_min_liquidity_changed)
        liquidity_entry.bind("<Return>", self.on_min_liquidity_changed)  # Enter для быстрого обновления
        
        # Минимальный спред
        spread_frame = ttk.Frame(settings_frame)
        spread_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(spread_frame, text="Мин. разницу при мин. сумме ордера (¢):").pack(side=tk.LEFT)
        self.min_spread_var = tk.StringVar(value=str(self.settings.min_spread or 0.2))
        self.min_spread_entry = ttk.Entry(spread_frame, textvariable=self.min_spread_var, width=10)
        self.min_spread_entry.pack(side=tk.LEFT, padx=5)
        self.min_spread_entry.bind("<FocusOut>", self.on_min_spread_changed)
        self.min_spread_entry.bind("<Return>", self.on_min_spread_changed)  # Enter для быстрого обновления
        
        # --- Модуль Автоспред ---
        auto_spread_frame = ttk.LabelFrame(settings_frame, text="Автоспред")
        auto_spread_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Галочка включения
        self.auto_spread_var = tk.BooleanVar(value=self.settings.auto_spread_enabled)
        auto_spread_check = ttk.Checkbutton(
            auto_spread_frame, 
            text="Включить автоспред", 
            variable=self.auto_spread_var,
            command=self.on_auto_spread_toggled
        )
        auto_spread_check.pack(anchor=tk.W, padx=5, pady=2)
        
        # Контейнер для настроек автоспреда
        self.auto_spread_settings_container = ttk.Frame(auto_spread_frame)
        self.auto_spread_settings_container.pack(fill=tk.X, padx=5, pady=2)
        
        # Целевая ликвидность
        target_liq_frame = ttk.Frame(self.auto_spread_settings_container)
        target_liq_frame.pack(fill=tk.X, pady=2)
        ttk.Label(target_liq_frame, text="Целевая ликвидность ($):").pack(side=tk.LEFT)
        self.target_liquidity_var = tk.StringVar(value=str(self.settings.target_liquidity or 1000.0))
        self.target_liq_entry = ttk.Entry(target_liq_frame, textvariable=self.target_liquidity_var, width=10)
        self.target_liq_entry.pack(side=tk.LEFT, padx=5)
        self.target_liq_entry.bind("<FocusOut>", self.on_target_liquidity_changed)
        self.target_liq_entry.bind("<Return>", self.on_target_liquidity_changed)
        
        # Максимальный спред
        max_auto_spread_frame = ttk.Frame(self.auto_spread_settings_container)
        max_auto_spread_frame.pack(fill=tk.X, pady=2)
        ttk.Label(max_auto_spread_frame, text="Макс. спред (¢):").pack(side=tk.LEFT)
        self.max_auto_spread_var = tk.StringVar(value=str(self.settings.max_auto_spread or 6.0))
        self.max_s_entry = ttk.Entry(max_auto_spread_frame, textvariable=self.max_auto_spread_var, width=10)
        self.max_s_entry.pack(side=tk.LEFT, padx=5)
        self.max_s_entry.bind("<FocusOut>", self.on_max_auto_spread_changed)
        self.max_s_entry.bind("<Return>", self.on_max_auto_spread_changed)
        
        # Обновляем состояние полей
        self._update_auto_spread_ui_state()
        
        # Добавляем мгновенное применение при наборе (trace)
        self.spread_var.trace_add("write", lambda *args: self.on_spread_changed())
        self.position_size_var.trace_add("write", lambda *args: self.on_position_size_changed())
        self.min_liquidity_var.trace_add("write", lambda *args: self.on_min_liquidity_changed())
        self.min_spread_var.trace_add("write", lambda *args: self.on_min_spread_changed())
        self.target_liquidity_var.trace_add("write", lambda *args: self.on_target_liquidity_changed())
        self.max_auto_spread_var.trace_add("write", lambda *args: self.on_max_auto_spread_changed())

        # Кнопки управления
        buttons_frame = ttk.Frame(settings_frame)
        buttons_frame.pack(fill=tk.X, pady=5)
        
        # Кнопка управления ликвидностью (по умолчанию всегда "Выставить ликвидность")
        self.liquidity_btn = ttk.Button(
            buttons_frame,
            text="Выставить ликвидность",
            command=self.toggle_liquidity
        )
        self.liquidity_btn.pack(side=tk.LEFT, padx=5)
        
        # Кнопка сброса к дефолтам
        reset_btn = ttk.Button(
            buttons_frame,
            text="Сбросить к дефолтам",
            command=self.reset_to_defaults
        )
        reset_btn.pack(side=tk.LEFT, padx=5)
    
    def toggle_liquidity(self):
        """Переключает состояние ликвидности (выставить/убрать)"""
        if not self.orders_placed:
            # Выставляем ликвидность
            self.settings_manager.update_settings(self.market_id, enabled=True)
            self.settings = self.settings_manager.get_settings(self.market_id)
            self.orders_placed = True
            self.liquidity_btn.config(text="Убрать ликвидность")
            self.market_log(f"Выставляем ликвидность...")
            
            # Выставляем ордера на основе предварительных расчетов
            if self.order_manager and self.last_orderbook:
                from order_calculator import OrderCalculator
                decimal_precision = self.market_info.get("decimalPrecision", 3)
                
                # Получаем активные ордера для вычитания нашей ликвидности
                active_orders = None
                if self.order_manager:
                    try:
                        active_orders = self.order_manager.get_active_orders(timeout=0.1)
                    except Exception:
                        active_orders = None
                
                # Проверяем данные стакана перед расчетом
                bids = self.last_orderbook.get("bids", [])
                asks = self.last_orderbook.get("asks", [])
                
                if not bids or not asks:
                    reason = []
                    if not bids:
                        reason.append("нет bids")
                    if not asks:
                        reason.append("нет asks")
                    reason_str = ", ".join(reason)
                    self.market_log(f"✗ Не удалось рассчитать ордера: стакан пуст ({reason_str})")
                    print(f"[DEBUG] Не удалось рассчитать ордера для рынка {self.market_id}: стакан пуст ({reason_str})")
                    return
                
                best_bid = bids[0][0] if bids else None
                best_ask = asks[0][0] if asks else None
                
                if best_bid is None or best_ask is None:
                    reason = []
                    if best_bid is None:
                        reason.append("best_bid=None")
                    if best_ask is None:
                        reason.append("best_ask=None")
                    reason_str = ", ".join(reason)
                    self.market_log(f"✗ Не удалось рассчитать ордера: отсутствуют лучшие цены ({reason_str}), bids={len(bids)}, asks={len(asks)})")
                    print(f"[DEBUG] Не удалось рассчитать ордера для рынка {self.market_id}: отсутствуют лучшие цены ({reason_str}), bids={len(bids)}, asks={len(asks)})")
                    return
                
                order_info = OrderCalculator.calculate_limit_orders(
                    self.last_orderbook,
                    self.settings,
                    decimal_precision=decimal_precision,
                    active_orders=active_orders
                )
                
                if order_info:
                    # Проверяем ликвидность и спред перед выставлением
                    can_place_yes = order_info.get("can_place_yes", False)
                    can_place_no = order_info.get("can_place_no", False)
                    can_place_yes_liquidity = order_info.get("can_place_yes_liquidity", True)
                    can_place_no_liquidity = order_info.get("can_place_no_liquidity", True)
                    can_place_yes_spread = order_info.get("can_place_yes_spread", True)
                    can_place_no_spread = order_info.get("can_place_no_spread", True)
                    min_liquidity = order_info.get("min_liquidity", 300.0)
                    min_spread = order_info.get("min_spread", 0.2)
                    liquidity_yes = order_info.get("liquidity_yes", 0)
                    liquidity_no = order_info.get("liquidity_no", 0)
                    spread_yes = order_info.get("spread_yes", 0)
                    spread_no = order_info.get("spread_no", 0)
                    
                    if not can_place_yes and not can_place_no:
                        # Определяем причину
                        reasons = []
                        if not can_place_yes_liquidity or not can_place_no_liquidity:
                            reasons.append(f"ликвидность (Yes: ${liquidity_yes:.2f}, No: ${liquidity_no:.2f}, мин: ${min_liquidity:.2f})")
                        if not can_place_yes_spread or not can_place_no_spread:
                            # Конвертируем спреды из долларов в центы для отображения
                            spread_yes_cents = spread_yes * 100
                            spread_no_cents = spread_no * 100
                            min_spread_cents = min_spread
                            reasons.append(f"спред (Yes: {spread_yes_cents:.2f}¢, No: {spread_no_cents:.2f}¢, мин: {min_spread_cents:.2f}¢)")
                        reason_text = ", ".join(reasons) if reasons else "недостаточно условий"
                        self.market_log(f"✗ Недостаточно условий для выставления ордеров: {reason_text}")
                        self.orders_placed = False
                        self.liquidity_btn.config(text="Выставить ликвидность")
                        self.settings_manager.update_settings(self.market_id, enabled=False)
                        return
                    elif not can_place_yes:
                        reason = "ликвидность" if not can_place_yes_liquidity else "спред"
                        if not can_place_yes_liquidity:
                            value = f"${liquidity_yes:.2f} < ${min_liquidity:.2f}"
                        else:
                            spread_yes_cents = spread_yes * 100
                            value = f"{spread_yes_cents:.2f}¢ < {min_spread:.2f}¢"
                        self.market_log(f"⚠️ Недостаточно {reason} для Yes ({value}), выставляем только No")
                    elif not can_place_no:
                        reason = "ликвидность" if not can_place_no_liquidity else "спред"
                        if not can_place_no_liquidity:
                            value = f"${liquidity_no:.2f} < ${min_liquidity:.2f}"
                        else:
                            spread_no_cents = spread_no * 100
                            value = f"{spread_no_cents:.2f}¢ < {min_spread:.2f}¢"
                        self.market_log(f"⚠️ Недостаточно {reason} для No ({value}), выставляем только Yes")
                    
                    mid_price_yes = OrderCalculator.calculate_mid_price(best_bid, best_ask) if best_bid and best_ask else None
                    
                    if mid_price_yes:
                        # Выставляем ордера в отдельном потоке
                        threading.Thread(
                            target=self._place_orders_thread,
                            args=(order_info, mid_price_yes),
                            daemon=True
                        ).start()
                    else:
                        self.market_log(f"✗ Не удалось рассчитать mid_price (best_bid={best_bid}, best_ask={best_ask})")
                        print(f"[DEBUG] Не удалось рассчитать mid_price для рынка {self.market_id}: best_bid={best_bid}, best_ask={best_ask}")
                else:
                    # Детальная диагностика почему calculate_limit_orders вернул None
                    reason_parts = []
                    if not bids:
                        reason_parts.append("bids пуст")
                    if not asks:
                        reason_parts.append("asks пуст")
                    if best_bid is None:
                        reason_parts.append("best_bid=None")
                    if best_ask is None:
                        reason_parts.append("best_ask=None")
                    
                    if not reason_parts:
                        reason_parts.append("неизвестная причина (calculate_limit_orders вернул None)")
                    
                    reason_str = ", ".join(reason_parts)
                    self.market_log(f"✗ Не удалось рассчитать ордера для выставления: {reason_str} (bids={len(bids)}, asks={len(asks)}, best_bid={best_bid}, best_ask={best_ask})")
                    print(f"[DEBUG] Не удалось рассчитать ордера для рынка {self.market_id}: {reason_str} (bids={len(bids)}, asks={len(asks)}, best_bid={best_bid}, best_ask={best_ask})")
        else:
            # Убираем ликвидность
            self.settings_manager.update_settings(self.market_id, enabled=False)
            self.settings = self.settings_manager.get_settings(self.market_id)
            self.orders_placed = False
            self.liquidity_btn.config(text="Выставить ликвидность")
            self.market_log(f"Убираем ликвидность...")
            
            # Отменяем все ордера в отдельном потоке
            if self.order_manager:
                threading.Thread(
                    target=self._cancel_orders_thread,
                    daemon=True
                ).start()
    
    def _place_orders_thread(self, order_info: Dict, mid_price_yes: float, outcome: str = None):
        """Поток для выставления ордеров"""
        try:
            if self.order_manager:
                success = self.order_manager.place_orders_from_preliminary(order_info, mid_price_yes)
                # Сбрасываем флаги выставления после завершения
                self.placing_orders = False
                # Если outcome не указан, сбрасываем оба флага (метод мог выставить оба ордера)
                if outcome is None:
                    self.placing_yes = False
                    self.placing_no = False
                elif outcome == "yes":
                    self.placing_yes = False
                elif outcome == "no":
                    self.placing_no = False
                # Всегда обновляем отображение
                self.root.after(0, self._update_placed_orders_display)
                # Обновляем счетчики ордеров в главном окне
                if hasattr(self.root, '_update_orders_count'):
                    self.root.after(0, self.root._update_orders_count)
        except Exception as e:
            # Сбрасываем флаги выставления при ошибке
            self.placing_orders = False
            if outcome is None:
                self.placing_yes = False
                self.placing_no = False
            elif outcome == "yes":
                self.placing_yes = False
            elif outcome == "no":
                self.placing_no = False
            self.market_log(f"✗ Ошибка выставления ордеров: {e}")
            import traceback
            self.market_log(traceback.format_exc())
            self.root.after(0, self._update_placed_orders_display)
            # Обновляем счетчики ордеров в главном окне
            if hasattr(self.root, '_update_orders_count'):
                self.root.after(0, self.root._update_orders_count)
    
    def _cancel_orders_thread(self):
        """Поток для отмены ордеров"""
        try:
            if self.order_manager:
                success = self.order_manager.cancel_all_orders()
                # Всегда обновляем отображение
                self.root.after(0, self._update_placed_orders_display)
                # Обновляем счетчики ордеров в главном окне
                if hasattr(self.root, '_update_orders_count'):
                    self.root.after(0, self.root._update_orders_count)
        except Exception as e:
            self.market_log(f"✗ Ошибка отмены ордеров: {e}")
            import traceback
            self.market_log(traceback.format_exc())
            self.root.after(0, self._update_placed_orders_display)
            # Обновляем счетчики ордеров в главном окне
            if hasattr(self.root, '_update_orders_count'):
                self.root.after(0, self.root._update_orders_count)
    
    def _cancel_order_thread(self, outcome: str):
        """Поток для отмены одного ордера (yes или no)"""
        try:
            if self.order_manager:
                # Используем метод cancel_order из OrderManager
                # OrderManager уже логирует через log_func (который = market_log), поэтому не дублируем
                success = self.order_manager.cancel_order(outcome)
                if not success:
                    # Логируем только ошибки, успешные отмены уже залогированы в OrderManager
                    self.market_log(f"✗ Не удалось отменить ордер {outcome.upper()}")
                
                # Сбрасываем флаг отмены
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                
                # Всегда обновляем отображение
                self.root.after(0, self._update_placed_orders_display)
        except Exception as e:
            self.market_log(f"✗ Ошибка отмены ордера {outcome}: {e}")
            import traceback
            self.market_log(traceback.format_exc())
            self.root.after(0, self._update_placed_orders_display)
    
    def _recalculate_and_place_order_autospread(self, outcome: str, orderbook_data: Dict, order_info: Dict, mid_price_yes: float):
        """
        Пересчитывает цену ордера для автоспреда при падении ликвидности и выставляет новый ордер.
        
        Args:
            outcome: "yes" или "no"
            orderbook_data: Данные стакана
            order_info: Информация о расчетах ордеров
            mid_price_yes: Текущий mid-price для Yes
        """
        try:
            if not self.order_manager:
                return
            
            # Получаем старую цену ордера ДО отмены для сравнения
            old_price = None
            try:
                active_orders = self.order_manager.get_active_orders(timeout=0.1)
                if outcome.lower() == "yes":
                    old_order = active_orders.get("yes") if active_orders else None
                else:
                    old_order = active_orders.get("no") if active_orders else None
                if old_order:
                    old_price = old_order.get("price")
            except:
                pass
            
            # Отменяем текущий ордер
            success = self.order_manager.cancel_order(outcome)
            if not success:
                self.market_log(f"✗ Не удалось отменить ордер {outcome.upper()} для пересчета")
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                return
            
            # Небольшая задержка, чтобы ордер точно отменился и стакан обновился
            import time
            time.sleep(1.0)  # Увеличиваем задержку для обновления стакана
            
            # Используем последний актуальный стакан (если есть)
            if hasattr(self, 'last_orderbook') and self.last_orderbook:
                orderbook_data = self.last_orderbook
            
            # Получаем настройки
            settings = self.settings
            if not settings:
                self.market_log(f"✗ Не удалось получить настройки для пересчета {outcome.upper()}")
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                return
            
            # Получаем параметры для пересчета
            decimal_precision = self.market_info.get("decimalPrecision", 3)
            target_liquidity = settings.target_liquidity or 1000.0
            max_spread_dollars = (settings.max_auto_spread or 6.0) / 100.0
            
            # Пересчитываем цену по целевой ликвидности
            from order_calculator import OrderCalculator
            result = OrderCalculator.find_price_by_target_liquidity(
                orderbook_data,
                target_liquidity,
                outcome,
                decimal_precision,
                return_info=True
            )
            
            if isinstance(result, tuple):
                new_price, price_info = result
            else:
                new_price = result
                price_info = "Неизвестная причина"
            
            if new_price <= 0:
                self.market_log(f"✗ Не удалось найти цену для {outcome.upper()} с ликвидностью ${target_liquidity:.2f}: {price_info}")
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                return
            
            # Ограничиваем максимальным спредом от mid-price
            if outcome.lower() == "yes":
                mid_price = mid_price_yes
            else:
                mid_price = 1.0 - mid_price_yes
            
            new_price = max(new_price, mid_price - max_spread_dollars)
            new_price = OrderCalculator.round_price_by_precision(new_price, decimal_precision)
            new_price = max(min(new_price, 0.999), 0.001)  # Ограничиваем диапазоном
            
            # Проверяем, что цена не минимальная (0.001) - это означает, что ликвидности недостаточно
            MIN_PRICE = 0.001
            if new_price <= MIN_PRICE:
                self.market_log(f"✗ Недостаточно ликвидности для {outcome.upper()}: цена получилась минимальной (${target_liquidity:.2f} недостижимо)")
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                return
            
            # Проверяем ликвидность перед новой ценой (наш ордер уже отменен, поэтому не передаем active_orders)
            liquidity_before_new_price = OrderCalculator.calculate_liquidity_before_price(
                orderbook_data,
                new_price,
                outcome,
                None  # Наш ордер уже отменен, не учитываем его
            )
            
            # Если ликвидность все еще недостаточна, не выставляем ордер
            if liquidity_before_new_price < target_liquidity:
                self.market_log(f"✗ Ликвидность перед новой ценой {new_price:.4f} недостаточна: ${liquidity_before_new_price:.2f} < ${target_liquidity:.2f}")
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                return
            
            # Проверяем, что новая цена отличается от старой (если была)
            if old_price is not None:
                price_diff = abs(new_price - old_price)
                if price_diff < 0.0001:  # Если цена практически не изменилась
                    self.market_log(f"⚠️ Новая цена {new_price:.4f} совпадает со старой {old_price:.4f}, не переставляем")
                    if outcome.lower() == "yes":
                        self.cancelling_yes = False
                    elif outcome.lower() == "no":
                        self.cancelling_no = False
                    return
            
            # Рассчитываем количество shares
            if settings.position_size_usdt is not None:
                shares = OrderCalculator.calculate_shares_from_usdt(settings.position_size_usdt, new_price)
                shares = OrderCalculator.adjust_to_min_order_value(shares, new_price)
                shares = OrderCalculator.round_shares_to_tenths(shares, new_price)
            elif settings.position_size_shares is not None:
                shares = settings.position_size_shares
                shares = OrderCalculator.adjust_to_min_order_value(shares, new_price)
                shares = OrderCalculator.round_shares_to_tenths(shares, new_price)
            else:
                self.market_log(f"✗ Не задан размер позиции для {outcome.upper()}")
                if outcome.lower() == "yes":
                    self.cancelling_yes = False
                elif outcome.lower() == "no":
                    self.cancelling_no = False
                return
            
            # Выставляем новый ордер по пересчитанной цене
            if old_price is not None:
                self.market_log(f"✓ {outcome.upper()} ордер: пересчитана цена {old_price:.4f} → {new_price:.4f} для ликвидности ${target_liquidity:.2f}, выставляем")
            else:
                self.market_log(f"✓ {outcome.upper()} ордер: пересчитана цена {new_price:.4f} для ликвидности ${target_liquidity:.2f}, выставляем")
            
            result = self.order_manager.place_order(outcome, new_price, shares)
            
            if result:
                if old_price is not None:
                    self.market_log(f"✓ {outcome.upper()} ордер успешно выставлен: цена изменена {old_price:.4f} → {new_price:.4f}")
                else:
                    self.market_log(f"✓ {outcome.upper()} ордер успешно выставлен по новой цене: {new_price:.4f}")
            else:
                self.market_log(f"✗ Не удалось выставить {outcome.upper()} ордер по новой цене")
            
            # Сбрасываем флаги
            if outcome.lower() == "yes":
                self.cancelling_yes = False
            elif outcome.lower() == "no":
                self.cancelling_no = False
            
            # Обновляем отображение
            self.root.after(0, self._update_placed_orders_display)
            
        except Exception as e:
            self.market_log(f"✗ Ошибка пересчета и выставления {outcome.upper()} ордера: {e}")
            import traceback
            self.market_log(traceback.format_exc())
            if outcome.lower() == "yes":
                self.cancelling_yes = False
            elif outcome.lower() == "no":
                self.cancelling_no = False
            self.root.after(0, self._update_placed_orders_display)
    
    def _update_placed_orders_display(self):
        """Обновляет отображение выставленных ордеров"""
        if not self.order_manager:
            return
        
        # Получаем активные ордера с таймаутом для предотвращения зависания
        try:
            active_orders = self.order_manager.get_active_orders(timeout=0.1)
            stats = self.order_manager.get_stats(timeout=0.1)
        except Exception:
            # Если не удалось получить (таймаут или ошибка), используем пустые данные
            active_orders = {"yes": None, "no": None}
            stats = {"placed": 0, "cancelled": 0}
        
        # Yes ордер
        yes_order = active_orders.get("yes")
        if yes_order:
            price_cents = yes_order["price"] * 100
            shares = yes_order["shares"]
            order_id = yes_order.get("order_id", "N/A")
            order_id_short = str(order_id)[:20] + "..." if len(str(order_id)) > 20 else str(order_id)
            self.yes_placed_label.config(
                text=f"Yes. Цена: {price_cents:.2f}¢, shares: {shares:.1f}, order_id: {order_id_short}"
            )
        else:
            self.yes_placed_label.config(text="Yes: --")
        
        # No ордер
        no_order = active_orders.get("no")
        if no_order:
            price_cents = no_order["price"] * 100
            shares = no_order["shares"]
            order_id = no_order.get("order_id", "N/A")
            order_id_short = str(order_id)[:20] + "..." if len(str(order_id)) > 20 else str(order_id)
            self.no_placed_label.config(
                text=f"No. Цена: {price_cents:.2f}¢, shares: {shares:.1f}, order_id: {order_id_short}"
            )
        else:
            self.no_placed_label.config(text="No: --")
        
        # Статистика
        self.orders_stats_label.config(
            text=f"Выставлено ордеров: {stats['placed']}, Отменено ордеров: {stats['cancelled']}"
        )
        self.recalculate_orders()
    
    def on_spread_changed(self, event=None):
        """Обработчик изменения спреда"""
        try:
            val_str = self.spread_var.get().strip()
            if not val_str:
                return
            spread = float(val_str)
            self.settings_manager.update_settings(self.market_id, spread_percent=spread)
            self.settings = self.settings_manager.get_settings(self.market_id)
            self.recalculate_orders()
        except ValueError:
            pass
    
    def on_position_type_changed(self, event=None):
        """Обработчик изменения типа размера позиции"""
        # При изменении типа нужно пересчитать с новым типом
        # Сначала обновляем настройки, затем пересчитываем
        try:
            position_type = self.position_type_var.get()
            current_size_str = self.position_size_var.get().strip()
            
            if current_size_str:
                size = float(current_size_str)
                
                if position_type == "usdt":
                    # Явно обнуляем shares при выборе usdt
                    self.settings_manager.update_settings(
                        self.market_id,
                        position_size_usdt=size,
                        position_size_shares=None
                    )
                else:
                    # Явно обнуляем usdt при выборе shares
                    self.settings_manager.update_settings(
                        self.market_id,
                        position_size_usdt=None,
                        position_size_shares=size
                    )
                
                # Обновляем настройки
                self.settings = self.settings_manager.get_settings(self.market_id)
                print(f"[DEBUG] Изменен тип позиции на {position_type}, размер: {size}")
                print(f"[DEBUG] Новые настройки: usdt={self.settings.position_size_usdt}, shares={self.settings.position_size_shares}")
                
                # Пересчитываем ордера
                self.recalculate_orders()
            else:
                # Если значение пустое, обнуляем оба
                self.settings_manager.update_settings(
                    self.market_id,
                    position_size_usdt=None,
                    position_size_shares=None
                )
                self.settings = self.settings_manager.get_settings(self.market_id)
                self.recalculate_orders()
        except ValueError:
            # Если значение не число, просто обновляем настройки без размера
            position_type = self.position_type_var.get()
            if position_type == "usdt":
                self.settings_manager.update_settings(
                    self.market_id,
                    position_size_usdt=None,
                    position_size_shares=None
                )
            else:
                self.settings_manager.update_settings(
                    self.market_id,
                    position_size_usdt=None,
                    position_size_shares=None
                )
            self.settings = self.settings_manager.get_settings(self.market_id)
            self.recalculate_orders()
    
    def on_position_size_changed(self, event=None):
        """Обработчик изменения размера позиции"""
        try:
            size_str = self.position_size_var.get().strip()
            if not size_str:
                return  # Пустое значение - не обрабатываем
            
            size = float(size_str)
            position_type = self.position_type_var.get()
            
            if position_type == "usdt":
                self.settings_manager.update_settings(
                    self.market_id,
                    position_size_usdt=size,
                    position_size_shares=None  # Явно обнуляем shares
                )
            else:
                self.settings_manager.update_settings(
                    self.market_id,
                    position_size_usdt=None,  # Явно обнуляем usdt
                    position_size_shares=size
                )
            
            # Обновляем настройки
            self.settings = self.settings_manager.get_settings(self.market_id)
            
            # Пересчитываем ордера
            self.recalculate_orders()
            
        except ValueError:
            pass
        except Exception as e:
            error_msg = f"Ошибка при изменении размера позиции: {e}"
            log_error_to_file(
                error_msg,
                exception=e,
                context=f"market_id={self.market_id}, on_position_size_changed"
            )
    
    def on_min_liquidity_changed(self, event=None):
        """Обработчик изменения минимальной ликвидности"""
        try:
            liquidity_str = self.min_liquidity_var.get().strip()
            if not liquidity_str:
                return  # Пустое значение - не обрабатываем
            
            liquidity = float(liquidity_str)
            
            if liquidity < 0:
                return
            
            self.settings_manager.update_settings(
                self.market_id,
                min_liquidity_usdt=liquidity
            )
            
            # Обновляем настройки
            self.settings = self.settings_manager.get_settings(self.market_id)
            
            # Пересчитываем ордера
            self.recalculate_orders()
            
        except ValueError:
            pass
        except Exception as e:
            error_msg = f"Ошибка при изменении минимальной ликвидности: {e}"
            log_error_to_file(
                error_msg,
                exception=e,
                context=f"market_id={self.market_id}, on_min_liquidity_changed"
            )
    
    def on_min_spread_changed(self, event=None):
        """Обработчик изменения минимального спреда"""
        try:
            spread_str = self.min_spread_var.get().strip()
            if not spread_str:
                return  # Пустое значение - не обрабатываем
            
            spread = float(spread_str)
            
            self.settings_manager.update_settings(
                self.market_id,
                min_spread=spread
            )
            
            # Обновляем настройки
            self.settings = self.settings_manager.get_settings(self.market_id)
            
            # Пересчитываем ордера
            self.recalculate_orders()
            
        except ValueError:
            pass
        except Exception as e:
            error_msg = f"Ошибка при изменении минимального спреда: {e}"
            log_error_to_file(
                error_msg,
                exception=e,
                context=f"market_id={self.market_id}, on_min_spread_changed"
            )

    def on_auto_spread_toggled(self):
        """Обработка переключения автоспреда"""
        enabled = self.auto_spread_var.get()
        self.settings_manager.update_settings(self.market_id, auto_spread_enabled=enabled)
        self.settings = self.settings_manager.get_settings(self.market_id)
        
        self._update_auto_spread_ui_state()
        self.recalculate_orders()

    def on_target_liquidity_changed(self, event=None):
        """Обработка изменения целевой ликвидности"""
        try:
            val_str = self.target_liquidity_var.get().strip()
            if not val_str:
                return
            val = float(val_str)
            self.settings_manager.update_settings(self.market_id, target_liquidity=val)
            self.settings = self.settings_manager.get_settings(self.market_id)
            self.recalculate_orders()
        except ValueError:
            pass

    def on_max_auto_spread_changed(self, event=None):
        """Обработка изменения максимального спреда"""
        try:
            val_str = self.max_auto_spread_var.get().strip()
            if not val_str:
                return
            val = float(val_str)
            self.settings_manager.update_settings(self.market_id, max_auto_spread=val)
            self.settings = self.settings_manager.get_settings(self.market_id)
            self.recalculate_orders()
        except ValueError:
            pass

    def _update_auto_spread_ui_state(self):
        """Обновляет доступность полей в зависимости от состояния автоспреда"""
        is_auto = self.auto_spread_var.get()
        state = tk.NORMAL if is_auto else tk.DISABLED
        
        if hasattr(self, 'target_liq_entry'):
            self.target_liq_entry.configure(state=state)
        if hasattr(self, 'max_s_entry'):
            self.max_s_entry.configure(state=state)
        
        # Если включен автоспред, блокируем поле обычного спреда
        if hasattr(self, 'manual_spread_entry'):
            self.manual_spread_entry.configure(state=tk.DISABLED if is_auto else tk.NORMAL)

    def toggle_market_log(self):
        """Показывает/скрывает лог маркета"""
        if self.log_visible:
            # Скрываем лог
            self.market_log_container.pack_forget()
            self.toggle_log_btn.config(text="▼ Показать лог")
            self.log_visible = False
        else:
            # Показываем лог
            self.market_log_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.toggle_log_btn.config(text="▲ Скрыть лог")
            self.log_visible = True
    
    def reset_to_defaults(self):
        """Сбрасывает настройки к дефолтным значениям"""
        self.settings_manager.reset_to_defaults(self.market_id)
        self.settings = self.settings_manager.get_settings(self.market_id)
        self.update_display()
        self.recalculate_orders()
    
    def recalculate_orders(self):
        """Пересчитывает ордера на основе текущих настроек"""
        # Если есть последние данные стакана, пересчитываем
        if hasattr(self, 'last_orderbook') and self.last_orderbook:
            try:
                from order_calculator import OrderCalculator
                
                # Обновляем настройки перед пересчетом
                self.settings = self.settings_manager.get_settings(self.market_id)
                
                # Получаем decimalPrecision из market_info
                decimal_precision = self.market_info.get("decimalPrecision", 3)
                
                # Получаем активные ордера для вычитания нашей ликвидности
                active_orders = None
                if self.order_manager:
                    try:
                        active_orders = self.order_manager.get_active_orders(timeout=0.1)
                    except Exception:
                        active_orders = None
                
                # Проверяем данные стакана перед расчетом
                bids = self.last_orderbook.get("bids", [])
                asks = self.last_orderbook.get("asks", [])
                
                if not bids or not asks:
                    reason = []
                    if not bids:
                        reason.append("нет bids")
                    if not asks:
                        reason.append("нет asks")
                    print(f"[DEBUG] Не удалось рассчитать ордера для рынка {self.market_id}: стакан пуст ({', '.join(reason)})")
                    self.market_log(f"✗ Не удалось рассчитать ордера: стакан пуст ({', '.join(reason)})")
                else:
                    best_bid = bids[0][0] if bids else None
                    best_ask = asks[0][0] if asks else None
                    
                    if best_bid is None or best_ask is None:
                        reason = []
                        if best_bid is None:
                            reason.append("best_bid=None")
                        if best_ask is None:
                            reason.append("best_ask=None")
                        print(f"[DEBUG] Не удалось рассчитать ордера для рынка {self.market_id}: отсутствуют лучшие цены ({', '.join(reason)}), bids={len(bids)}, asks={len(asks)})")
                        self.market_log(f"✗ Не удалось рассчитать ордера: отсутствуют лучшие цены ({', '.join(reason)})")
                    else:
                        order_info = OrderCalculator.calculate_limit_orders(
                            self.last_orderbook, 
                            self.settings,
                            decimal_precision=decimal_precision,
                            active_orders=active_orders
                        )
                        if order_info:
                            mid_price = OrderCalculator.calculate_mid_price(best_bid, best_ask) if best_bid and best_ask else None
                            
                            buy_yes = order_info.get('buy_yes', {})
                            buy_no = order_info.get('buy_no', {})
                            
                            # Обновляем GUI напрямую (мы уже в главном потоке через root.after)
                            self.update_market_info(
                                mid_price=mid_price,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                order_info=order_info
                            )
                        else:
                            # Детальная диагностика почему calculate_limit_orders вернул None
                            reason_parts = []
                            if not bids:
                                reason_parts.append("bids пуст")
                            if not asks:
                                reason_parts.append("asks пуст")
                            if best_bid is None:
                                reason_parts.append("best_bid=None")
                            if best_ask is None:
                                reason_parts.append("best_ask=None")
                            
                            if not reason_parts:
                                reason_parts.append("неизвестная причина (calculate_limit_orders вернул None)")
                            
                            reason_str = ", ".join(reason_parts)
                            print(f"[DEBUG] Не удалось рассчитать ордера для рынка {self.market_id}: {reason_str} (bids={len(bids)}, asks={len(asks)}, best_bid={best_bid}, best_ask={best_ask})")
                            self.market_log(f"✗ Не удалось рассчитать ордера: {reason_str}")
            except Exception as e:
                error_msg = f"Ошибка при пересчете ордеров: {e}"
                print(error_msg)
                import traceback
                traceback.print_exc()
                log_error_to_file(
                    error_msg,
                    exception=e,
                    context=f"market_id={self.market_id}, recalculate_orders"
                )
        else:
            print(f"[DEBUG] Нет данных стакана для пересчета (рынок {self.market_id})")
    
    def open_market_url(self, url: str):
        """Открывает ссылку на рынок в браузере"""
        import webbrowser
        webbrowser.open(url)
    
    def update_display(self):
        """Обновляет отображение настроек"""
        # Обновляем текст кнопки ликвидности на основе реального состояния ордеров
        if hasattr(self, 'liquidity_btn'):
            if hasattr(self, 'orders_placed') and self.orders_placed:
                self.liquidity_btn.config(text="Убрать ликвидность")
            else:
                self.liquidity_btn.config(text="Выставить ликвидность")
        
        self.spread_var.set(str(self.settings.spread_percent))
        
        # Обновляем минимальную ликвидность
        if hasattr(self, 'min_liquidity_var'):
            self.min_liquidity_var.set(str(self.settings.min_liquidity_usdt or 300.0))
        
        # Обновляем минимальный спред
        if hasattr(self, 'min_spread_var'):
            self.min_spread_var.set(str(self.settings.min_spread or 0.005))
        
        if self.settings.position_size_usdt:
            self.position_type_var.set("usdt")
            self.position_size_var.set(str(self.settings.position_size_usdt))
        elif self.settings.position_size_shares:
            self.position_type_var.set("shares")
            self.position_size_var.set(str(self.settings.position_size_shares))
        
        # Минимальная ликвидность
        if hasattr(self, 'min_liquidity_var'):
            self.min_liquidity_var.set(str(self.settings.min_liquidity_usdt or 300.0))
        
        # Обновляем баланс при создании виджета
        if hasattr(self, 'current_balance') and self.current_balance is not None:
            balance = self.current_balance
            if balance < 1:
                balance_text = f"Баланс: {balance:.6f} shares"
            else:
                balance_text = f"Баланс: {balance:.2f} shares"
            
            # Проверяем shareThreshold
            share_threshold = self.market_info.get("shareThreshold")
            if share_threshold is not None:
                share_threshold = float(share_threshold)
                if balance >= share_threshold:
                    balance_text += " ✓"  # Проходим по холду
                else:
                    balance_text += f" ✗ (нужно {share_threshold:.1f})"  # Не проходим
            
            self.balance_label.config(text=balance_text)
        else:
            self.balance_label.config(text="Баланс: --")
        
        # Обновляем минимальные требования при создании виджета
        spread_threshold = self.market_info.get("spreadThreshold")
        if spread_threshold is not None:
            spread_threshold_cents = float(spread_threshold) * 100
            self.spread_threshold_label.config(text=f"Мин. спред: {spread_threshold_cents:.2f}¢")
        else:
            self.spread_threshold_label.config(text="Мин. спред: --")
        
        share_threshold = self.market_info.get("shareThreshold")
        if share_threshold is not None:
            self.share_threshold_label.config(text=f"Мин. холд: {share_threshold:.1f} shares")
        else:
            self.share_threshold_label.config(text="Мин. холд: --")
    
    def update_market_info(
        self,
        mid_price: Optional[float] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
        order_info: Optional[Dict] = None,
        balance: Optional[float] = None
    ):
        """Обновляет информацию о рынке"""
        from order_calculator import OrderCalculator
        
        # Yes: Mid-прайс | Bid/Ask
        if mid_price is not None and best_bid is not None and best_ask is not None:
            mid_price_yes_cents = mid_price * 100
            best_bid_yes_cents = best_bid * 100
            best_ask_yes_cents = best_ask * 100
            self.yes_price_label.config(
                text=f"Yes: Mid {mid_price_yes_cents:.2f}¢ | Bid/Ask {best_bid_yes_cents:.2f}¢ / {best_ask_yes_cents:.2f}¢"
            )
        elif mid_price is not None:
            mid_price_yes_cents = mid_price * 100
            self.yes_price_label.config(text=f"Yes: Mid {mid_price_yes_cents:.2f}¢ | Bid/Ask -- / --")
        elif best_bid is not None and best_ask is not None:
            best_bid_yes_cents = best_bid * 100
            best_ask_yes_cents = best_ask * 100
            self.yes_price_label.config(
                text=f"Yes: Mid -- | Bid/Ask {best_bid_yes_cents:.2f}¢ / {best_ask_yes_cents:.2f}¢"
            )
        
        # No: Mid-прайс | Bid/Ask
        if mid_price is not None and best_bid is not None and best_ask is not None:
            # Mid-прайс No (Yes + No = 1)
            mid_price_no = OrderCalculator.calculate_no_price(mid_price)
            mid_price_no_cents = mid_price_no * 100
            
            # Bid/Ask для No (Yes + No = 1)
            # Bid No = 1 - Ask Yes, Ask No = 1 - Bid Yes
            best_bid_no = 1.0 - best_ask
            best_ask_no = 1.0 - best_bid
            best_bid_no_cents = best_bid_no * 100
            best_ask_no_cents = best_ask_no * 100
            
            self.no_price_label.config(
                text=f"No: Mid {mid_price_no_cents:.2f}¢ | Bid/Ask {best_bid_no_cents:.2f}¢ / {best_ask_no_cents:.2f}¢"
            )
        elif mid_price is not None:
            mid_price_no = OrderCalculator.calculate_no_price(mid_price)
            mid_price_no_cents = mid_price_no * 100
            self.no_price_label.config(text=f"No: Mid {mid_price_no_cents:.2f}¢ | Bid/Ask -- / --")
        elif best_bid is not None and best_ask is not None:
            best_bid_no = 1.0 - best_ask
            best_ask_no = 1.0 - best_bid
            best_bid_no_cents = best_bid_no * 100
            best_ask_no_cents = best_ask_no * 100
            self.no_price_label.config(
                text=f"No: Mid -- | Bid/Ask {best_bid_no_cents:.2f}¢ / {best_ask_no_cents:.2f}¢"
            )
        
        # Обновляем время последнего обновления стакана
        if hasattr(self, 'last_orderbook_update_time') and self.last_orderbook_update_time:
            import datetime
            update_time = datetime.datetime.fromtimestamp(self.last_orderbook_update_time)
            time_str = update_time.strftime("%H:%M:%S")
            self.last_update_label.config(text=f"Последнее обновление: {time_str}")
        
        # Сохраняем последний стакан для пересчета (если передан order_info, значит есть стакан)
        # Это будет обновлено в on_orderbook_update
        
        # Обновляем предварительные ордера (только цены покупки)
        if order_info:
            # Сохраняем order_info для подсчета предварительных ордеров
            self.last_order_info = order_info
            
            buy_yes = order_info.get("buy_yes", {})
            buy_no = order_info.get("buy_no", {})
            
            # Получаем информацию о ликвидности
            liquidity_yes = order_info.get("liquidity_yes", 0)
            liquidity_no = order_info.get("liquidity_no", 0)
            can_place_yes = order_info.get("can_place_yes", False)
            can_place_no = order_info.get("can_place_no", False)
            min_liquidity = order_info.get("min_liquidity", 300.0)
            
            if buy_yes:
                buy_yes_price = buy_yes.get("price", 0)
                buy_yes_shares = buy_yes.get("shares", 0)
                
                # Конвертируем цену из долларов в центы
                buy_yes_price_cents = buy_yes_price * 100
                
                # Добавляем галочку или крестик в зависимости от ликвидности
                status_icon = "✓" if can_place_yes else "✗"
                
                # Форматируем ликвидность
                if liquidity_yes >= 1000:
                    liquidity_text = f"${liquidity_yes:,.2f}"
                elif liquidity_yes >= 1:
                    liquidity_text = f"${liquidity_yes:.2f}"
                else:
                    liquidity_text = f"${liquidity_yes:.4f}"
                
                # Показываем цену в центах, количество shares, ликвидность и статус
                yes_text = f"Yes: {buy_yes_price_cents:.2f}¢ ({buy_yes_shares:.1f} shares) | Ликвидность: {liquidity_text} {status_icon}"
                self.yes_order_label.config(text=yes_text)
            
            if buy_no:
                buy_no_price = buy_no.get("price", 0)
                buy_no_shares = buy_no.get("shares", 0)
                
                # Конвертируем цену из долларов в центы
                buy_no_price_cents = buy_no_price * 100
                
                # Добавляем галочку или крестик в зависимости от ликвидности
                status_icon = "✓" if can_place_no else "✗"
                
                # Форматируем ликвидность
                if liquidity_no >= 1000:
                    liquidity_text = f"${liquidity_no:,.2f}"
                elif liquidity_no >= 1:
                    liquidity_text = f"${liquidity_no:.2f}"
                else:
                    liquidity_text = f"${liquidity_no:.4f}"
                
                # Показываем цену в центах, количество shares, ликвидность и статус
                no_text = f"No: {buy_no_price_cents:.2f}¢ ({buy_no_shares:.1f} shares) | Ликвидность: {liquidity_text} {status_icon}"
                self.no_order_label.config(text=no_text)
            
            # Общая стоимость = максимальное значение из Yes и No
            # (потому что только один из ордеров исполнится)
            total_value = order_info.get("total_value_usd", 0)
            if total_value == 0:
                # Если total_value_usd не передан, вычисляем как максимум
                buy_yes_value = buy_yes.get("value_usd", 0) if buy_yes else 0
                buy_no_value = buy_no.get("value_usd", 0) if buy_no else 0
                total_value = max(buy_yes_value, buy_no_value)
            
            value_text = f"Общая стоимость: ${total_value:.2f}"
            self.orders_value_label.config(text=value_text)
            
            # Лейблы ликвидности не упакованы, поэтому не занимают место
            # Не вызываем update_idletasks() - это блокирующая операция, которая может задерживать логику
            # Tkinter сам обновит виджеты в главном цикле
        
        if balance is not None:
            self.current_balance = balance
        elif hasattr(self, 'current_balance') and self.current_balance is not None:
            balance = self.current_balance
        else:
            balance = None
        
        # Обновляем баланс с проверкой shareThreshold
        if balance is not None:
            # Форматируем баланс: если меньше 1, показываем больше знаков, иначе 2 знака
            if balance < 1:
                balance_text = f"Баланс: {balance:.6f} shares"
            else:
                balance_text = f"Баланс: {balance:.2f} shares"
            
            # Проверяем shareThreshold
            share_threshold = self.market_info.get("shareThreshold")
            if share_threshold is not None:
                share_threshold = float(share_threshold)
                if balance >= share_threshold:
                    balance_text += " ✓"  # Проходим по холду
                else:
                    balance_text += f" ✗ (нужно {share_threshold:.1f})"  # Не проходим
            
            self.balance_label.config(text=balance_text)
        
        # Обновляем минимальные требования
        spread_threshold = self.market_info.get("spreadThreshold")
        if spread_threshold is not None:
            spread_threshold_cents = float(spread_threshold) * 100
            self.spread_threshold_label.config(text=f"Мин. спред: {spread_threshold_cents:.2f}¢")
        else:
            self.spread_threshold_label.config(text="Мин. спред: --")
        
        share_threshold = self.market_info.get("shareThreshold")
        if share_threshold is not None:
            self.share_threshold_label.config(text=f"Мин. холд: {share_threshold:.1f} shares")
        else:
            self.share_threshold_label.config(text="Мин. холд: --")

        status = self.market_info.get("status", "UNKNOWN")
        self.status_label.config(text=f"Статус: {status}")


class MainWindow:
    """Главное окно приложения"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Predict Fun - Управление ликвидностью")
        self.root.geometry("1420x950")
        
        self.token_frames: Dict[str, TokenFrame] = {}
        self.settings_manager = None
        self.api_clients: Dict[str, any] = {}
        self.jwt_tokens: Dict[str, str] = {}  # predict_account_address -> jwt_token
        self.accounts: List[Dict] = []
        self.ws_client = None
        self.account_info: Dict[str, Dict] = {}  # predict_account_address -> {nickname, balance}
        self.balance_update_thread = None
        self.balance_update_running = False
        self.last_balance_update_time = None
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self._schedule_arrange())
        
        self._arrange_timer = None
        self._arrange_pending = False
        self._updating_scrollregion = False
        self._scrollregion_timer = None
        self._pending_gui_updates = {}
        self._gui_update_timer = None
        self._last_arrange_width = 0
        self._last_frames_per_row = 0
        self._last_search_query = ""
        self._last_ws_orderbook_update_time = None  # для отображения "Дата обновления"
        
        self.create_widgets()
        
        # Показываем информационное окно о разработчике после создания GUI
        # Используем after() чтобы окно успело отрисоваться
        self.root.after(100, lambda: show_about_dialog(self.root))
    
    def create_widgets(self):
        """Создает виджеты главного окна"""
        # Верхняя панель с кнопками
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.connect_btn = ttk.Button(
            top_frame,
            text="Подключиться к аккаунту",
            command=self.connect_accounts
        )
        self.connect_btn.pack(side=tk.LEFT, padx=5)
        
        # Кнопка массового выставления ликвидности
        self.place_all_btn = ttk.Button(
            top_frame,
            text="Выставить везде ликвидность",
            command=self.place_liquidity_all,
            state=tk.DISABLED
        )
        self.place_all_btn.pack(side=tk.LEFT, padx=5)
        
        # Кнопка массовой отмены ордеров
        self.cancel_all_btn = ttk.Button(
            top_frame,
            text="Убрать все ордера",
            command=self.cancel_orders_all,
            state=tk.DISABLED
        )
        self.cancel_all_btn.pack(side=tk.LEFT, padx=5)

        # Кнопка общих настроек (справа)
        self.common_settings_btn = ttk.Button(
            top_frame,
            text="⚙ Общие настройки",
            command=self.show_common_settings,
            state=tk.DISABLED
        )
        self.common_settings_btn.pack(side=tk.RIGHT, padx=5)
        
        # Информация об аккаунте (никнейм и баланс)
        self.account_info_frame = ttk.Frame(top_frame)
        self.account_info_frame.pack(side=tk.LEFT, padx=10)
        
        # Левая часть: вертикальное размещение (никнейм, баланс, время обновления)
        self.account_info_left_frame = ttk.Frame(self.account_info_frame)
        self.account_info_left_frame.pack(side=tk.LEFT, padx=(0, 6))
        
        self.account_info_label = ttk.Label(
            self.account_info_left_frame,
            text="",
            font=("Arial", 9)
        )
        self.account_info_label.pack()
        
        self.balance_update_time_label = ttk.Label(
            self.account_info_left_frame,
            text="",
            font=("Arial", 7),
            foreground="gray"
        )
        self.balance_update_time_label.pack()
        
        # Правая часть: вертикальное размещение (счетчики ордеров)
        self.account_info_right_frame = ttk.Frame(self.account_info_frame)
        self.account_info_right_frame.pack(side=tk.LEFT)
        
        # Количество предварительных ордеров (с галочкой)
        self.preliminary_orders_label = ttk.Label(
            self.account_info_right_frame,
            text="Можно выставить ордеров: 0",
            font=("Arial", 8)
        )
        self.preliminary_orders_label.pack(anchor=tk.W)
        
        # Количество выставленных ордеров
        self.placed_orders_label = ttk.Label(
            self.account_info_right_frame,
            text="Выставлено ордеров: 0",
            font=("Arial", 8)
        )
        self.placed_orders_label.pack(anchor=tk.W)

        # Панель поиска
        search_frame = ttk.Frame(top_frame)
        search_frame.pack(side=tk.LEFT, padx=(8, 0))
        
        ttk.Label(search_frame, text="🔍 Поиск:").pack(side=tk.LEFT, padx=(0, 3))
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(0, 3))
        
        # Кнопка очистки поиска
        def clear_search():
            self.search_var.set("")
            
        ttk.Button(search_frame, text="✖", width=2, command=clear_search).pack(side=tk.LEFT, padx=(2, 0))
        
        # Статус WebSocket и время обновления (справа от поиска, как у блока баланса: строка — под ней «Обновлено»)
        ws_status_frame = ttk.Frame(search_frame)
        ws_status_frame.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Separator(ws_status_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=2)
        ws_info_frame = ttk.Frame(ws_status_frame)
        ws_info_frame.pack(side=tk.LEFT)
        # Верхняя строка: WebSocket: и статус (✓ Live / —)
        ws_row1 = ttk.Frame(ws_info_frame)
        ws_row1.pack(anchor=tk.W)
        ttk.Label(ws_row1, text="WebSocket:", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 2))
        self.ws_status_label = ttk.Label(ws_row1, text="—", font=("Arial", 9))
        self.ws_status_label.pack(side=tk.LEFT)
        # Под ним — мелкий серый «Обновлено: HH:MM:SS»
        self.ws_update_label = ttk.Label(
            ws_info_frame,
            text="",
            font=("Arial", 7),
            foreground="gray"
        )
        self.ws_update_label.pack(anchor=tk.W)
        
        # Лог
        log_frame = ttk.LabelFrame(self.root, text="Лог")
        log_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Кнопка для показа/скрытия общего лога
        log_header_frame = ttk.Frame(log_frame)
        log_header_frame.pack(fill=tk.X, padx=5, pady=2)
        
        self.main_log_visible = False  # Изначально лог скрыт
        
        self.toggle_main_log_btn = ttk.Button(
            log_header_frame,
            text="▼ Показать лог",
            command=self.toggle_main_log,
            width=15
        )
        self.toggle_main_log_btn.pack(side=tk.LEFT)
        
        # Кнопка "Rudy vs Web3" для перехода в Telegram канал
        def open_telegram_channel():
            import webbrowser
            webbrowser.open("https://t.me/rudy_web3")
        
        telegram_btn = tk.Button(
            log_header_frame,
            text="Rudy vs Web3",
            command=open_telegram_channel,
            bg="#0088cc",  # Синий цвет Telegram
            fg="white",    # Белый текст
            font=("Arial", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=1,  # Уменьшил pady для совпадения высоты с ttk.Button
            cursor="hand2",
            activebackground="#006ba3",  # Темнее при наведении
            activeforeground="white",
            borderwidth=0,
            highlightthickness=0
        )
        telegram_btn.pack(side=tk.LEFT, padx=(8, 0))
        
        # Контейнер для текстового поля лога (изначально скрыт)
        self.main_log_container = ttk.Frame(log_frame)
        # Не упаковываем его сразу - будет показываться при нажатии кнопки
        
        self.log_text = scrolledtext.ScrolledText(
            self.main_log_container,
            height=10,
            wrap=tk.WORD
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Разрешаем выделение и копирование
        # Блокируем только прямое редактирование через обычные клавиши
        def on_key(event):
            # Разрешаем все комбинации с Control (Ctrl+C, Ctrl+A, Ctrl+V и т.д.)
            if event.state & 0x0004:  # Control key
                return None  # Полностью разрешаем все Ctrl+комбинации
            # Разрешаем все комбинации с Shift (выделение)
            if event.state & 0x0001:  # Shift key
                return None
            # Разрешаем функциональные и навигационные клавиши (без символов)
            if not event.char or len(event.char) == 0:
                return None
            # Блокируем только обычный ввод печатных символов (без модификаторов)
            if event.char.isprintable():
                return 'break'
            return None
        
        self.log_text.bind('<KeyPress>', on_key)
        
        # Добавляем контекстное меню для правого клика
        main_log_menu = tk.Menu(self.log_text, tearoff=0)
        main_log_menu.add_command(label="Копировать", command=lambda: self.log_text.event_generate("<<Copy>>"))
        main_log_menu.add_command(label="Выделить все", command=lambda: self.log_text.tag_add(tk.SEL, "1.0", tk.END))
        
        def show_main_log_menu(event):
            try:
                main_log_menu.tk_popup(event.x_root, event.y_root)
            finally:
                main_log_menu.grab_release()
        
        self.log_text.bind("<Button-3>", show_main_log_menu)  # Button-3 = правый клик
        
        # Область с токенами
        self.tokens_frame = ttk.LabelFrame(self.root, text="Токены[0]")
        self.tokens_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Canvas с прокруткой для токенов
        canvas = tk.Canvas(
            self.tokens_frame, 
            bg="#f0f0f0", 
            highlightthickness=0,
            borderwidth=0
        )
        scrollbar = ttk.Scrollbar(self.tokens_frame, orient="vertical", command=canvas.yview)
        self.tokens_container = ttk.Frame(canvas)
        
        def on_container_configure(event):
            """Обработчик изменения размера контейнера"""
            self._update_scrollregion_delayed()
        
        self.tokens_container.bind("<Configure>", on_container_configure)
        
        # Также отслеживаем изменение размера canvas для пересчета расположения
        def on_canvas_configure(event):
            """Обработчик изменения размера canvas"""
            # Обновляем ширину контейнера внутри Canvas
            canvas_width = event.width
            if canvas_width > 10 and hasattr(self, 'canvas_window_id'):
                canvas.itemconfig(self.canvas_window_id, width=canvas_width)
            # Запланировать пересчет расположения (debouncing)
            self._schedule_arrange()
        
        canvas.bind("<Configure>", on_canvas_configure)
        
        # Добавляем поддержку скролла колесиком мыши
        def on_mousewheel(event):
            if canvas.winfo_exists():
                # Для Windows: event.delta / 120
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        # Биндим на все приложение, чтобы скролл работал везде над окном
        self.root.bind_all("<MouseWheel>", on_mousewheel)
        
        # Настраиваем контейнер так, чтобы он растягивался по ширине Canvas
        # Сохраняем ID окна в Canvas для последующего обновления
        self.canvas_window_id = canvas.create_window((0, 0), window=self.tokens_container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.canvas = canvas
    
    def _process_gui_updates(self):
        """Пакетная обработка обновлений GUI из WebSocket"""
        if not self._pending_gui_updates:
            self._gui_update_timer = None
            return
            
        updates = self._pending_gui_updates.copy()
        self._pending_gui_updates.clear()
        self._gui_update_timer = None
        
        for market_id, data in updates.items():
            if market_id not in self.token_frames:
                continue
                
            token_frame = self.token_frames[market_id]
            try:
                token_frame.update_market_info(
                    mid_price=data['mid_price'],
                    best_bid=data['best_bid'],
                    best_ask=data['best_ask'],
                    order_info=data['order_info']
                )
                if token_frame.order_manager:
                    token_frame._update_placed_orders_display()
            except Exception as e:
                log_error_to_file(f"Ошибка пакетного обновления GUI: {e}", context=f"market_id={market_id}")
        
        # Обновляем общие счетчики один раз для всей пачки
        self._update_orders_count()

    def _schedule_arrange(self):
        """Запланировать пересчет расположения (debouncing)"""
        if self._arrange_timer:
            self.root.after_cancel(self._arrange_timer)
        self._arrange_timer = self.root.after(50, self._execute_arrange)

    def _execute_arrange(self):
        """Выполнить пересчет расположения"""
        self._arrange_timer = None
        self._arrange_token_frames()

    def _update_scrollregion_delayed(self):
        """Запланировать обновление области прокрутки (debouncing)"""
        if self._scrollregion_timer:
            self.root.after_cancel(self._scrollregion_timer)
        self._scrollregion_timer = self.root.after(100, self._update_scrollregion)

    def _update_scrollregion(self):
        """Обновить область прокрутки Canvas"""
        self._scrollregion_timer = None
        if hasattr(self, 'canvas') and self.canvas:
            bbox = self.canvas.bbox("all")
            if bbox:
                self.canvas.configure(scrollregion=bbox)

    def toggle_main_log(self):
        """Показывает/скрывает общий лог"""
        if self.main_log_visible:
            # Скрываем лог
            self.main_log_container.pack_forget()
            self.toggle_main_log_btn.config(text="▼ Показать лог")
            self.main_log_visible = False
        else:
            # Показываем лог
            self.main_log_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.toggle_main_log_btn.config(text="▲ Скрыть лог")
            self.main_log_visible = True
    
    def log(self, message: str):
        """Добавляет сообщение в лог (GUI и консоль)"""
        # Выводим в консоль
        print(message)
        
        # Выводим в GUI (простой текст для возможности копирования)
        # Убираем эмодзи для лучшей читаемости при копировании
        clean_message = message
        # Временно включаем редактирование для вставки текста
        current_state = self.log_text.cget('state')
        if current_state == tk.DISABLED:
            self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{clean_message}\n")
        self.log_text.see(tk.END)
        # Возвращаем состояние (но оставляем NORMAL для копирования)
        if current_state != tk.DISABLED:
            self.log_text.config(state=tk.NORMAL)
        # Не вызываем update_idletasks() - это блокирующая операция, которая может задерживать логику
        # Tkinter сам обновит виджеты в главном цикле
    
    def log_error(self, error: Exception, context: str = ""):
        """Логирует ошибку с полным traceback (копируемый формат)"""
        import traceback
        
        error_msg = f"\n{'='*60}\n"
        if context:
            error_msg += f"ОШИБКА: {context}\n"
        error_msg += f"Тип: {type(error).__name__}\n"
        error_msg += f"Сообщение: {str(error)}\n"
        error_msg += f"\nTraceback:\n"
        error_msg += f"{traceback.format_exc()}"
        error_msg += f"\n{'='*60}\n"
        
        # Выводим в консоль
        print(error_msg)
        
        # Записываем в файл
        log_error_to_file(
            f"Ошибка: {str(error)}",
            exception=error,
            context=context
        )
        
        # Выводим в GUI (простой текст для копирования)
        self.log_text.insert(tk.END, error_msg)
        self.log_text.see(tk.END)
        # Не вызываем update_idletasks() - это блокирующая операция
        # Tkinter сам обновит виджеты в главном цикле
    
    def show_common_settings(self):
        """Открывает окно для установки общих настроек для всех токенов"""
        if not self.token_frames:
            return
            
        dialog = tk.Toplevel(self.root)
        dialog.title("Общие настройки для всех токенов")
        dialog.geometry("450x550")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Центрируем окно относительно главного
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 225
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 275
        dialog.geometry(f"+{x}+{y}")
        
        content = ttk.Frame(dialog, padding="20")
        content.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(content, text="Эти настройки будут применены КО ВСЕМ токенам сразу.", font=("Arial", 9, "bold")).pack(pady=(0, 15))
        
        # --- Основные настройки ---
        basic_frame = ttk.LabelFrame(content, text="Основные параметры", padding="10")
        basic_frame.pack(fill=tk.X, pady=5)
        
        # Спред
        spread_f = ttk.Frame(basic_frame)
        spread_f.pack(fill=tk.X, pady=2)
        ttk.Label(spread_f, text="Спред (цент):").pack(side=tk.LEFT)
        spread_var = tk.StringVar(value="3.0")
        ttk.Entry(spread_f, textvariable=spread_var, width=10).pack(side=tk.RIGHT)
        
        # Размер позиции
        pos_f = ttk.Frame(basic_frame)
        pos_f.pack(fill=tk.X, pady=2)
        ttk.Label(pos_f, text="Размер позиции:").pack(side=tk.LEFT)
        pos_type_var = tk.StringVar(value="usdt")
        ttk.Combobox(pos_f, textvariable=pos_type_var, values=["usdt", "shares"], state="readonly", width=8).pack(side=tk.LEFT, padx=10)
        pos_size_var = tk.StringVar(value="100.0")
        ttk.Entry(pos_f, textvariable=pos_size_var, width=10).pack(side=tk.RIGHT)
        
        # Мин ликвидность
        liq_f = ttk.Frame(basic_frame)
        liq_f.pack(fill=tk.X, pady=2)
        ttk.Label(liq_f, text="Мин. ликвидность ($):").pack(side=tk.LEFT)
        min_liq_var = tk.StringVar(value="300.0")
        ttk.Entry(liq_f, textvariable=min_liq_var, width=10).pack(side=tk.RIGHT)
        
        # Мин разница
        diff_f = ttk.Frame(basic_frame)
        diff_f.pack(fill=tk.X, pady=2)
        ttk.Label(diff_f, text="Мин. разница (¢):").pack(side=tk.LEFT)
        min_diff_var = tk.StringVar(value="0.2")
        ttk.Entry(diff_f, textvariable=min_diff_var, width=10).pack(side=tk.RIGHT)
        
        # --- Автоспред ---
        auto_frame = ttk.LabelFrame(content, text="Автоспред", padding="10")
        auto_frame.pack(fill=tk.X, pady=10)
        
        auto_enabled_var = tk.BooleanVar(value=False)
        auto_check = ttk.Checkbutton(auto_frame, text="Включить автоспред для всех", variable=auto_enabled_var)
        auto_check.pack(anchor=tk.W, pady=2)
        
        target_liq_f = ttk.Frame(auto_frame)
        target_liq_f.pack(fill=tk.X, pady=2)
        ttk.Label(target_liq_f, text="Целевая ликвидность ($):").pack(side=tk.LEFT)
        target_liq_var = tk.StringVar(value="1000.0")
        target_liq_entry = ttk.Entry(target_liq_f, textvariable=target_liq_var, width=10)
        target_liq_entry.pack(side=tk.RIGHT)
        
        max_spread_f = ttk.Frame(auto_frame)
        max_spread_f.pack(fill=tk.X, pady=2)
        ttk.Label(max_spread_f, text="Максимальный спред (¢):").pack(side=tk.LEFT)
        max_spread_var = tk.StringVar(value="6.0")
        max_spread_entry = ttk.Entry(max_spread_f, textvariable=max_spread_var, width=10)
        max_spread_entry.pack(side=tk.RIGHT)
        
        # Логика блокировки полей автоспреда
        def update_auto_ui(*args):
            state = tk.NORMAL if auto_enabled_var.get() else tk.DISABLED
            target_liq_entry.config(state=state)
            max_spread_entry.config(state=state)
            
        auto_enabled_var.trace_add("write", update_auto_ui)
        update_auto_ui() # Инициализация
        
        def apply_settings():
            try:
                # Собираем данные
                s_val = float(spread_var.get())
                p_type = pos_type_var.get()
                p_val = float(pos_size_var.get())
                l_val = float(min_liq_var.get())
                d_val = float(min_diff_var.get())
                
                a_enabled = auto_enabled_var.get()
                a_target = float(target_liq_var.get())
                a_max = float(max_spread_var.get())
                
                # Применяем ко всем
                self.log(f"Применяем общие настройки ко всем токенам ({len(self.token_frames)} шт.)...")
                
                for market_id, frame in self.token_frames.items():
                    # Готовим аргументы для обновления
                    update_kwargs = {
                        "spread_percent": s_val,
                        "min_liquidity_usdt": l_val,
                        "min_spread": d_val,
                        "auto_spread_enabled": a_enabled,
                        "target_liquidity": a_target,
                        "max_auto_spread": a_max
                    }
                    
                    if p_type == "usdt":
                        update_kwargs["position_size_usdt"] = p_val
                        update_kwargs["position_size_shares"] = None
                    else:
                        update_kwargs["position_size_shares"] = p_val
                        update_kwargs["position_size_usdt"] = None
                        
                    # Обновляем через менеджер
                    self.settings_manager.update_settings(market_id, **update_kwargs)
                    
                    # Обновляем GUI самого фрейма
                    frame.settings = self.settings_manager.get_settings(market_id)
                    frame.update_display()
                    frame.recalculate_orders()
                
                self.log("✓ Общие настройки успешно применены.")
                dialog.destroy()
                
            except ValueError:
                messagebox.showerror("Ошибка", "Все значения должны быть числами", parent=dialog)
        
        btn_frame = ttk.Frame(content)
        btn_frame.pack(fill=tk.X, pady=20)
        
        ttk.Button(btn_frame, text="Применить ко всем", command=apply_settings).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Отмена", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    def _show_account_input_dialog(self):
        """Показывает диалоговое окно для ввода данных аккаунта"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Добавление аккаунта")
        dialog.geometry("600x350")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Центрируем окно
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (600 // 2)
        y = (dialog.winfo_screenheight() // 2) - (350 // 2)
        dialog.geometry(f"600x350+{x}+{y}")
        
        result = {"cancelled": True}
        
        # Основной фрейм с отступами
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Заголовок
        title_label = ttk.Label(
            main_frame,
            text="Введите данные аккаунта",
            font=("Arial", 12, "bold")
        )
        title_label.pack(pady=(0, 15))
        
        # Фрейм для полей ввода
        fields_frame = ttk.Frame(main_frame)
        fields_frame.pack(fill=tk.BOTH, expand=True)
        
        # API Key
        ttk.Label(fields_frame, text="API Key:", font=("Arial", 9)).grid(row=0, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        api_key_entry = ttk.Entry(fields_frame, width=50, font=("Arial", 9))
        api_key_entry.grid(row=0, column=1, sticky=tk.EW, pady=5)
        api_key_entry.focus()
        
        # Predict Account Address
        ttk.Label(fields_frame, text="Predict Account Address:", font=("Arial", 9)).grid(row=1, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        address_entry = ttk.Entry(fields_frame, width=50, font=("Arial", 9))
        address_entry.grid(row=1, column=1, sticky=tk.EW, pady=5)
        
        # Privy Wallet Private Key
        ttk.Label(fields_frame, text="Privy Wallet Private Key:", font=("Arial", 9)).grid(row=2, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        private_key_entry = ttk.Entry(fields_frame, width=50, font=("Arial", 9), show="*")
        private_key_entry.grid(row=2, column=1, sticky=tk.EW, pady=5)
        
        # Proxy (опционально)
        ttk.Label(fields_frame, text="Proxy (опционально):", font=("Arial", 9)).grid(row=3, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        proxy_entry = ttk.Entry(fields_frame, width=50, font=("Arial", 9))
        proxy_entry.grid(row=3, column=1, sticky=tk.EW, pady=5)
        ttk.Label(fields_frame, text="Формат: user:pass@host:port", font=("Arial", 7), foreground="gray").grid(row=4, column=1, sticky=tk.W, pady=(0, 10))
        
        # Настраиваем поддержку вставки через CTRL+V и ПКМ для всех полей
        entries = [api_key_entry, address_entry, private_key_entry, proxy_entry]
        for entry in entries:
            # CTRL+V через keycode (работает при любой раскладке)
            entry.bind("<Control-KeyPress>", lambda e, ent=entry: self._on_entry_control_key(ent, e))
            # ПКМ меню
            entry.bind("<Button-3>", lambda e, ent=entry: self._show_context_menu(ent, e))
        
        # Настраиваем растягивание колонки
        fields_frame.columnconfigure(1, weight=1)
        
        # Фрейм для кнопок
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X, pady=(15, 0))
        
        def on_ok():
            api_key = api_key_entry.get().strip()
            address = address_entry.get().strip()
            private_key = private_key_entry.get().strip()
            proxy = proxy_entry.get().strip() or None
            
            # Валидация
            if not api_key:
                messagebox.showerror("Ошибка", "Поле 'API Key' не может быть пустым", parent=dialog)
                return
            
            if not address:
                messagebox.showerror("Ошибка", "Поле 'Predict Account Address' не может быть пустым", parent=dialog)
                return
            
            if not address.startswith("0x"):
                messagebox.showerror("Ошибка", "Адрес должен начинаться с 0x", parent=dialog)
                return
            
            if not private_key:
                messagebox.showerror("Ошибка", "Поле 'Privy Wallet Private Key' не может быть пустым", parent=dialog)
                return
            
            result["cancelled"] = False
            result["api_key"] = api_key
            result["predict_account_address"] = address
            result["privy_wallet_private_key"] = private_key
            result["proxy"] = proxy
            dialog.destroy()
        
        def on_cancel():
            dialog.destroy()
        
        ttk.Button(buttons_frame, text="Отмена", command=on_cancel).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(buttons_frame, text="Сохранить", command=on_ok).pack(side=tk.RIGHT)
        
        # Поддержка Enter для сохранения
        dialog.bind("<Return>", lambda e: on_ok())
        dialog.bind("<Escape>", lambda e: on_cancel())
        
        # Ждем закрытия окна
        dialog.wait_window()
        
        if result["cancelled"]:
            return None
        return result
    
    def _on_entry_control_key(self, entry, event):
        """Обработка Ctrl+V по физической клавише (keycode), чтобы работало при любой раскладке."""
        # keycode 86 = физическая клавиша V (Windows/Linux), 54 = V на Mac
        if event.state & 0x4 and getattr(event, 'keycode', None) in (86, 54):
            entry.event_generate("<<Paste>>")
            return "break"
    
    def _show_context_menu(self, entry, event):
        """Показывает контекстное меню (ПКМ) для поля ввода"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Вставить", command=lambda: entry.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Вырезать", command=lambda: entry.event_generate("<<Cut>>"))
        menu.add_command(label="Копировать", command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: entry.select_range(0, tk.END))
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def _save_account_to_file(self, account_data, file_path):
        """Сохраняет данные аккаунта в файл accounts.txt"""
        import os
        
        # Если файл существует, добавляем в конец, иначе создаем новый
        file_exists = os.path.exists(file_path)
        
        with open(file_path, "a", encoding="utf-8") as f:
            if not file_exists:
                # Добавляем заголовок с комментариями
                f.write("# Формат: api_key,predict_account_address,privy_wallet_private_key,proxy\n")
                f.write("# Каждая строка - один аккаунт\n")
                f.write("# Строки начинающиеся с # игнорируются\n")
                f.write("# Прокси в формате: user:pass@host:port\n\n")
            
            # Формируем строку для записи
            line_parts = [
                account_data["api_key"],
                account_data["predict_account_address"],
                account_data["privy_wallet_private_key"]
            ]
            if account_data.get("proxy"):
                line_parts.append(account_data["proxy"])
            
            f.write(",".join(line_parts) + "\n")

    def connect_accounts(self):
        """Подключается к аккаунтам и загружает токены"""
        from config import ACCOUNTS_FILE
        import os
        
        # Проверяем существование файла accounts.txt
        if not os.path.exists(ACCOUNTS_FILE):
            # Показываем форму для ввода данных
            account_data = self._show_account_input_dialog()
            if account_data:
                # Сохраняем данные в файл
                self._save_account_to_file(account_data, ACCOUNTS_FILE)
                self.log("✓ Данные аккаунта сохранены в accounts.txt")
            else:
                # Пользователь отменил ввод
                return
        
        self.connect_btn.config(state=tk.DISABLED)
        self.log("Начало подключения к аккаунтам...")
        
        Thread(target=self._connect_accounts_thread, daemon=True).start()
    
    def _connect_accounts_thread(self):
        """Поток для подключения к аккаунтам"""
        try:
            from accounts import load_accounts_from_file
            from auth import get_auth_jwt
            from api_client import PredictAPIClient
            from settings_manager import SettingsManager
            
            # Загружаем аккаунты
            self.accounts = load_accounts_from_file()
            if not self.accounts:
                self.log("✗ Не найдено аккаунтов для подключения")
                self.root.after(0, lambda: self.connect_btn.config(state=tk.NORMAL))
                return
            
            self.log(f"Найдено аккаунтов: {len(self.accounts)}")
            
            # Инициализируем менеджер настроек
            self.settings_manager = SettingsManager()
            
            # Подключаемся к каждому аккаунту
            all_positions = []
            
            for i, account in enumerate(self.accounts):
                self.log(f"\nПодключение к аккаунту {i+1}/{len(self.accounts)}...")
                self.log(f"Адрес: {account['predict_account_address']}")
                
                try:
                    # Аутентификация
                    jwt_token = get_auth_jwt(
                        account["api_key"],
                        account["predict_account_address"],
                        account["privy_wallet_private_key"],
                        account.get("proxy"),
                        log_func=self.log
                    )
                    
                    # Создаем API клиент
                    api_client = PredictAPIClient(
                        account["api_key"],
                        jwt_token,
                        account.get("proxy")
                    )
                    
                    self.api_clients[account["predict_account_address"]] = api_client
                    self.jwt_tokens[account["predict_account_address"]] = jwt_token
                    
                    # Получаем информацию о пользователе (никнейм)
                    user_info = api_client.get_user_info()
                    nickname = None
                    if user_info:
                        nickname = user_info.get("nickname") or user_info.get("username") or user_info.get("name")
                    
                    # Получаем баланс USDT
                    balance_usdt = api_client.get_usdt_balance(
                        account["predict_account_address"],
                        account["privy_wallet_private_key"]
                    )
                    
                    # Сохраняем информацию об аккаунте
                    self.account_info[account["predict_account_address"]] = {
                        "nickname": nickname,
                        "balance": balance_usdt
                    }
                    
                    # Сохраняем время первого обновления баланса
                    import time
                    if self.last_balance_update_time is None:
                        self.last_balance_update_time = time.time()
                    
                    # Обновляем отображение информации об аккаунте
                    self.root.after(0, self._update_account_info_display)
                    
                    # Получаем позиции
                    positions = api_client.get_positions()
                    self.log(f"Найдено позиций: {len(positions)}")
                    
                    all_positions.extend(positions)
                    
                except Exception as e:
                    self.log(f"✗ Ошибка подключения к аккаунту: {e}")
                    continue
            
            # Обрабатываем позиции, получаем стаканы и создаем фреймы для токенов
            self.root.after(0, lambda: self._create_token_frames_with_orderbooks(all_positions))
            
        except Exception as e:
            self.log(f"✗ Критическая ошибка: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.root.after(0, lambda: self.connect_btn.config(state=tk.NORMAL))
    
    def _create_token_frames_with_orderbooks(self, positions: List[Dict]):
        """Создает фреймы для токенов с получением стаканов и расчетом ордеров"""
        Thread(target=self._create_token_frames_thread, args=(positions,), daemon=True).start()
    
    def _create_token_frames_thread(self, positions: List[Dict]):
        """Поток для создания фреймов токенов с получением стаканов"""
        try:
            from order_calculator import OrderCalculator
            
            # Очищаем старые фреймы
            self.root.after(0, lambda: self._clear_token_frames())
            
            if not positions:
                self.log("Нет позиций для отображения")
                self.root.after(0, lambda: self.connect_btn.config(state=tk.NORMAL))
                return
            
            # Группируем позиции по рынкам, фильтруем только REGISTERED
            # Собираем балансы для каждого рынка
            markets = {}
            market_balances = {}  # market_id -> total_balance в shares
            
            for position in positions:
                market = position.get("market", {})
                market_id = str(market.get("id", ""))
                status = market.get("status", "")
                
                # Показываем только REGISTERED рынки
                if market_id and status == "REGISTERED":
                    if market_id not in markets:
                        markets[market_id] = market
                        market_balances[market_id] = 0.0
                    
                    # Собираем баланс позиции (может быть в разных полях)
                    # Проверяем различные возможные поля для баланса
                    balance = (
                        position.get("balance") or 
                        position.get("shares") or 
                        position.get("amount") or 
                        position.get("quantity") or
                        0.0
                    )
                    
                    # Если balance это строка, пробуем преобразовать в число
                    if isinstance(balance, str):
                        try:
                            balance = float(balance)
                        except (ValueError, TypeError):
                            balance = 0.0
                    
                    if isinstance(balance, (int, float)) and balance > 0:
                        # Конвертируем из wei (10^18) в нормальные shares
                        # Если число очень большое (больше 10^10), значит это wei
                        WEI_DECIMALS = 10**18
                        if balance > 10**10:
                            balance_normalized = balance / WEI_DECIMALS
                            self.log(f"[DEBUG] Рынок {market_id}: баланс в wei {balance}, конвертирован в {balance_normalized:.6f} shares")
                        else:
                            balance_normalized = float(balance)
                        
                        old_balance = market_balances[market_id]
                        market_balances[market_id] += balance_normalized
                        self.log(f"[DEBUG] Рынок {market_id}: баланс позиции {balance_normalized:.6f}, общий баланс {old_balance:.6f} -> {market_balances[market_id]:.6f}")
            
            self.log(f"\nНайдено уникальных рынков: {len(markets)}")
            
            # Получаем полную информацию о рынках через API для slug, spreadThreshold, shareThreshold
            api_client = next(iter(self.api_clients.values())) if self.api_clients else None
            if api_client:
                self.log("Получение полной информации о рынках через API (многопоточный режим)...")
                market_ids = list(markets.keys())
                self.log(f"Загружаем информацию для {len(market_ids)} рынков параллельно...")
                
                # Блокировка для потокобезопасного обновления словаря markets
                markets_lock = threading.Lock()
                
                def fetch_market_info(market_id: str):
                    """Получает информацию о рынке"""
                    try:
                        full_market_info = api_client.get_market_info(market_id, log_func=self.log)
                        return market_id, full_market_info, None
                    except Exception as e:
                        return market_id, None, e
                
                # Используем ThreadPoolExecutor для параллельного выполнения запросов
                # Максимум 10 потоков одновременно, чтобы не перегружать сервер
                with ThreadPoolExecutor(max_workers=10) as executor:
                    # Запускаем все задачи
                    future_to_market = {
                        executor.submit(fetch_market_info, market_id): market_id 
                        for market_id in market_ids
                    }
                    
                    # Обрабатываем результаты по мере их поступления
                    completed = 0
                    for future in as_completed(future_to_market):
                        market_id, full_market_info, error = future.result()
                        completed += 1
                        
                        if error:
                            self.log(f"[DEBUG] Ошибка получения информации о рынке {market_id}: {error}")
                            continue
                        
                        if full_market_info:
                            # Обновляем информацию о рынке потокобезопасно
                            with markets_lock:
                                markets[market_id].update(full_market_info)
                            
                            category_slug = full_market_info.get("categorySlug")
                            slug = full_market_info.get("slug")
                            if category_slug:
                                self.log(f"[DEBUG] Рынок {market_id}: получен categorySlug '{category_slug}' ({completed}/{len(market_ids)})")
                            elif slug:
                                self.log(f"[DEBUG] Рынок {market_id}: получен slug '{slug}' ({completed}/{len(market_ids)})")
                            else:
                                self.log(f"[DEBUG] Рынок {market_id}: categorySlug и slug не найдены в API ответе ({completed}/{len(market_ids)})")
                        else:
                            self.log(f"[DEBUG] Рынок {market_id}: не удалось получить информацию через API ({completed}/{len(market_ids)})")
                
                self.log(f"Завершено получение информации о рынках: {completed}/{len(market_ids)}")
            
            # Создаем фреймы для всех рынков БЕЗ стакана
            # Стакан будет получен ТОЛЬКО через WebSocket
            self.log("Создание фреймов токенов...")
            self.log("Стакан будет получен через WebSocket (не используем REST API)")
            
            # Определяем, какой аккаунт использовать для каждого рынка
            # Пока используем первый аккаунт для всех рынков
            account_for_market = self.accounts[0] if self.accounts else None
            account_address_for_market = account_for_market["predict_account_address"] if account_for_market else None
            
            for market_id, market_info in markets.items():
                # Получаем баланс для этого рынка
                balance = market_balances.get(market_id, 0.0)
                
                # Получаем API клиент и JWT токен для этого аккаунта
                api_client = self.api_clients.get(account_address_for_market) if account_address_for_market else None
                jwt_token = self.jwt_tokens.get(account_address_for_market) if account_address_for_market else None
                
                # Создаем фрейм без стакана - данные придут через WebSocket
                def create_frame(
                    mid=market_id,
                    info=market_info,
                    bal=balance,
                    account=account_for_market,
                    jwt=jwt_token
                ):
                    frame = TokenFrame(
                        self.tokens_container,
                        mid,
                        info,
                        self.settings_manager,
                        self.update_callback,
                        initial_balance=bal,
                        api_key=account["api_key"] if account else None,
                        jwt_token=jwt,
                        predict_account_address=account["predict_account_address"] if account else None,
                        privy_wallet_private_key=account["privy_wallet_private_key"] if account else None,
                        proxy=account.get("proxy") if account else None
                    )
                    self.token_frames[mid] = frame
                
                self.root.after(0, create_frame)
            
            # Обновляем расположение фреймов после создания всех
            def final_setup():
                self._force_rearrange = True
                self._arrange_token_frames()
                self._update_account_info_display()
                self._update_tokens_count()
                self._update_orders_count()
                self.place_all_btn.config(state=tk.NORMAL)
                self.cancel_all_btn.config(state=tk.NORMAL)
                self.common_settings_btn.config(state=tk.NORMAL)
                
            self.root.after(200, final_setup)
            self.log("✓ Подключение завершено")
            
            # Запускаем периодическое обновление баланса
            self.start_balance_update_thread()
            
            # Запускаем WebSocket для мониторинга
            self.start_websocket_monitoring()
            
        except Exception as e:
            self.log_error(e, "Ошибка создания фреймов токенов")
            self.root.after(0, lambda: self.connect_btn.config(state=tk.NORMAL))
    
    def _update_tokens_count(self):
        """Обновляет количество токенов в заголовке"""
        count = len(self.token_frames)
        self.tokens_frame.config(text=f"Токены[{count}]")
    
    def _clear_token_frames(self):
        """Очищает фреймы токенов"""
        for frame in self.token_frames.values():
            frame.destroy()
        self.token_frames.clear()
        self._update_tokens_count()
    
    def _arrange_token_frames(self):
        """Располагает фреймы токенов горизонтально с переносом на новую строку"""
        if not self.token_frames:
            return
        
        # Получаем ширину canvas
        canvas_width = self.canvas.winfo_width()
        if canvas_width < 10:
            canvas_width = self.root.winfo_width() - 50
        
        # Минимальная ширина одного фрейма
        min_frame_width = 400
        container_width = canvas_width - 20
        frames_per_row = max(1, container_width // min_frame_width) if container_width > 0 else 1
        
        search_query = self.search_var.get().lower().strip()
        
        # ОПТИМИЗАЦИЯ: Если ширина не изменилась значительно и поиск тот же,
        # и количество фреймов в ряду не изменилось - ничего не делаем
        if (canvas_width == self._last_arrange_width and 
            frames_per_row == self._last_frames_per_row and 
            search_query == self._last_search_query and
            not hasattr(self, '_force_rearrange')):
            return
            
        self._last_arrange_width = canvas_width
        self._last_frames_per_row = frames_per_row
        self._last_search_query = search_query
        if hasattr(self, '_force_rearrange'):
            delattr(self, '_force_rearrange')
        
        # Обновляем ширину контейнера только если она реально изменилась
        if hasattr(self, 'canvas_window_id'):
            self.canvas.itemconfig(self.canvas_window_id, width=canvas_width)
        
        # Собираем список видимых фреймов
        visible_frames = []
        for market_id, frame in self.token_frames.items():
            question = frame.market_info.get("question", frame.market_info.get("title", "")).lower()
            if not search_query or search_query in question or search_query in str(market_id):
                visible_frames.append(frame)
            else:
                if frame.winfo_ismapped():
                    frame.grid_forget()

        # Располагаем только те, что изменили позицию
        for idx, frame in enumerate(visible_frames):
            row = idx // frames_per_row
            col = idx % frames_per_row
            
            info = frame.grid_info()
            if info.get('row') != str(row) or info.get('column') != str(col):
                # Убираем sticky="nsew", чтобы фреймы не растягивались, а сохраняли фиксированный размер
                frame.grid(row=row, column=col, padx=5, pady=5)
        
        # Настраиваем веса колонок (uniform убираем, чтобы не форсировать растяжение)
        for col in range(frames_per_row):
            self.tokens_container.grid_columnconfigure(col, weight=0)
        
        # Обновляем область прокрутки (с задержкой)
        self._update_scrollregion_delayed()
    
    def update_callback(self):
        """Callback для обновления данных"""
        pass
    
    def place_liquidity_all(self):
        """Выставляет ликвидность во всех рынках"""
        if not self.token_frames:
            self.log("Нет рынков для выставления ликвидности")
            return
        
        self.place_all_btn.config(state=tk.DISABLED)
        self.log("Начинаем массовое выставление ликвидности...")
        
        Thread(target=self._place_liquidity_all_thread, daemon=True).start()
    
    def _place_liquidity_all_thread(self):
        """Поток для массового выставления ликвидности"""
        try:
            from order_calculator import OrderCalculator
            
            placed_count = 0
            skipped_count = 0
            error_count = 0
            
            for market_id, token_frame in self.token_frames.items():
                try:
                    # Пропускаем, если ордера уже выставлены
                    if token_frame.orders_placed:
                        skipped_count += 1
                        continue
                    
                    # Пропускаем, если нет order_manager или стакана
                    if not token_frame.order_manager or not token_frame.last_orderbook:
                        skipped_count += 1
                        continue
                    
                    # Выставляем ликвидность для этого рынка
                    token_frame.settings_manager.update_settings(market_id, enabled=True)
                    token_frame.settings = token_frame.settings_manager.get_settings(market_id)
                    token_frame.orders_placed = True
                    
                    # Обновляем GUI
                    self.root.after(0, lambda tf=token_frame: tf.liquidity_btn.config(text="Убрать ликвидность"))
                    self.root.after(0, lambda tf=token_frame, mid=market_id: tf.market_log(f"Выставляем ликвидность..."))
                    
                    # Рассчитываем ордера
                    decimal_precision = token_frame.market_info.get("decimalPrecision", 3)
                    
                    # Получаем активные ордера для вычитания нашей ликвидности
                    active_orders = None
                    if token_frame.order_manager:
                        try:
                            active_orders = token_frame.order_manager.get_active_orders(timeout=0.1)
                        except Exception:
                            active_orders = None
                    
                    order_info = OrderCalculator.calculate_limit_orders(
                        token_frame.last_orderbook,
                        token_frame.settings,
                        decimal_precision=decimal_precision,
                        active_orders=active_orders
                    )
                    
                    if order_info:
                        # Проверяем ликвидность перед выставлением
                        can_place_yes = order_info.get("can_place_yes", False)
                        can_place_no = order_info.get("can_place_no", False)
                        min_liquidity = order_info.get("min_liquidity", 300.0)
                        liquidity_yes = order_info.get("liquidity_yes", 0)
                        liquidity_no = order_info.get("liquidity_no", 0)
                        
                        if not can_place_yes and not can_place_no:
                            self.log(f"[{market_id}] ✗ Недостаточно ликвидности (Yes: ${liquidity_yes:.2f}, No: ${liquidity_no:.2f}, мин: ${min_liquidity:.2f})")
                            token_frame.orders_placed = False
                            self.root.after(0, lambda tf=token_frame: tf.liquidity_btn.config(text="Выставить ликвидность"))
                            token_frame.settings_manager.update_settings(market_id, enabled=False)
                            skipped_count += 1
                            continue
                        
                        # Получаем mid_price
                        bids = token_frame.last_orderbook.get("bids", [])
                        asks = token_frame.last_orderbook.get("asks", [])
                        best_bid = bids[0][0] if bids else None
                        best_ask = asks[0][0] if asks else None
                        mid_price_yes = OrderCalculator.calculate_mid_price(best_bid, best_ask) if best_bid and best_ask else None
                        
                        if mid_price_yes:
                            # Выставляем ордера в отдельном потоке
                            threading.Thread(
                                target=token_frame._place_orders_thread,
                                args=(order_info, mid_price_yes),
                                daemon=True
                            ).start()
                            placed_count += 1
                        else:
                            skipped_count += 1
                    else:
                        skipped_count += 1
                        
                except Exception as e:
                    error_count += 1
                    self.log(f"[{market_id}] ✗ Ошибка выставления ликвидности: {e}")
                    import traceback
                    self.log(traceback.format_exc())
            
            self.log(f"✓ Массовое выставление завершено: выставлено {placed_count}, пропущено {skipped_count}, ошибок {error_count}")
            self.root.after(0, lambda: self.place_all_btn.config(state=tk.NORMAL))
            # Обновляем счетчики ордеров
            self.root.after(0, self._update_orders_count)
            
        except Exception as e:
            self.log(f"✗ Критическая ошибка при массовом выставлении: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.root.after(0, lambda: self.place_all_btn.config(state=tk.NORMAL))
            # Обновляем счетчики ордеров
            self.root.after(0, self._update_orders_count)
    
    def cancel_orders_all(self):
        """Отменяет все ордера во всех рынках"""
        if not self.token_frames:
            self.log("Нет рынков для отмены ордеров")
            return
        
        self.cancel_all_btn.config(state=tk.DISABLED)
        self.log("Начинаем массовую отмену ордеров...")
        
        Thread(target=self._cancel_orders_all_thread, daemon=True).start()
    
    def _cancel_orders_all_thread(self):
        """Поток для массовой отмены ордеров"""
        try:
            cancelled_count = 0
            skipped_count = 0
            error_count = 0
            
            for market_id, token_frame in self.token_frames.items():
                try:
                    # Пропускаем, если ордера не выставлены
                    if not token_frame.orders_placed:
                        skipped_count += 1
                        continue
                    
                    # Пропускаем, если нет order_manager
                    if not token_frame.order_manager:
                        skipped_count += 1
                        continue
                    
                    # Убираем ликвидность для этого рынка
                    token_frame.settings_manager.update_settings(market_id, enabled=False)
                    token_frame.settings = token_frame.settings_manager.get_settings(market_id)
                    token_frame.orders_placed = False
                    
                    # Обновляем GUI
                    self.root.after(0, lambda tf=token_frame: tf.liquidity_btn.config(text="Выставить ликвидность"))
                    self.root.after(0, lambda tf=token_frame, mid=market_id: tf.market_log(f"Убираем ликвидность..."))
                    
                    # Отменяем все ордера в отдельном потоке
                    threading.Thread(
                        target=token_frame._cancel_orders_thread,
                        daemon=True
                    ).start()
                    cancelled_count += 1
                    
                except Exception as e:
                    error_count += 1
                    self.log(f"[{market_id}] ✗ Ошибка отмены ордеров: {e}")
                    import traceback
                    self.log(traceback.format_exc())
            
            self.log(f"✓ Массовая отмена завершена: отменено {cancelled_count}, пропущено {skipped_count}, ошибок {error_count}")
            self.root.after(0, lambda: self.cancel_all_btn.config(state=tk.NORMAL))
            # Обновляем счетчики ордеров
            self.root.after(0, self._update_orders_count)
            
        except Exception as e:
            self.log(f"✗ Критическая ошибка при массовой отмене: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.root.after(0, lambda: self.cancel_all_btn.config(state=tk.NORMAL))
    
    def _update_ws_display(self, status=None, update_time=None):
        """Обновляет в интерфейсе статус WebSocket и время последнего обновления (вызывать из главного потока)."""
        import datetime
        try:
            if status is not None and hasattr(self, 'ws_status_label'):
                display = "✓ Live" if status == "Live" else status
                self.ws_status_label.config(text=display)
            if update_time is not None and hasattr(self, 'ws_update_label'):
                self._last_ws_orderbook_update_time = update_time
                dt = datetime.datetime.fromtimestamp(update_time)
                self.ws_update_label.config(text=f"Обновлено: {dt.strftime('%H:%M:%S')}")
        except Exception:
            pass
    
    def _update_account_info_display(self):
        """Обновляет отображение информации об аккаунте (никнейм и баланс)"""
        if not self.account_info:
            self.account_info_label.config(text="")
            self.balance_update_time_label.config(text="")
            return
        
        # Берем первый подключенный аккаунт (или можем показать все)
        account_address = next(iter(self.account_info.keys()), None)
        if not account_address:
            self.account_info_label.config(text="")
            self.balance_update_time_label.config(text="")
            return
        
        info = self.account_info[account_address]
        nickname = info.get("nickname")
        balance = info.get("balance")
        
        parts = []
        if nickname:
            parts.append(f"👤 {nickname}")
        elif account_address:
            # Если нет никнейма, показываем короткий адрес
            short_address = f"{account_address[:6]}...{account_address[-4:]}"
            parts.append(f"👤 {short_address}")
        
        if balance is not None:
            if balance >= 1000:
                balance_str = f"${balance:,.2f}"
            elif balance >= 1:
                balance_str = f"${balance:.2f}"
            else:
                balance_str = f"${balance:.4f}"
            parts.append(f"💰 {balance_str} USDT")
        elif len(parts) == 0:
            # Если нет ни никнейма, ни баланса, показываем короткий адрес
            short_address = f"{account_address[:6]}...{account_address[-4:]}"
            parts.append(f"👤 {short_address}")
        
        if parts:
            self.account_info_label.config(text=" | ".join(parts))
        else:
            self.account_info_label.config(text="")
        
        # Обновляем время последнего обновления баланса
        self._update_balance_time_display()
        
        # Обновляем счетчики ордеров
        self._update_orders_count()
    
    def _update_orders_count(self):
        """Обновляет счетчики предварительных и выставленных ордеров"""
        preliminary_count = 0
        placed_count = 0
        
        # Подсчитываем предварительные ордера (с галочкой)
        for market_id, token_frame in self.token_frames.items():
            if hasattr(token_frame, 'last_order_info') and token_frame.last_order_info:
                order_info = token_frame.last_order_info
                can_place_yes = order_info.get("can_place_yes", False)
                can_place_no = order_info.get("can_place_no", False)
                
                # Считаем количество ордеров (Yes и No отдельно)
                if can_place_yes:
                    preliminary_count += 1
                if can_place_no:
                    preliminary_count += 1
        
        # Подсчитываем выставленные ордера
        for market_id, token_frame in self.token_frames.items():
            if hasattr(token_frame, 'order_manager') and token_frame.order_manager:
                try:
                    active_orders = token_frame.order_manager.get_active_orders(timeout=0.1)
                    if active_orders:
                        # Считаем количество активных ордеров (Yes и No отдельно)
                        if active_orders.get("yes"):
                            placed_count += 1
                        if active_orders.get("no"):
                            placed_count += 1
                except Exception:
                    # Игнорируем ошибки при получении активных ордеров
                    pass
        
        # Обновляем отображение
        self.preliminary_orders_label.config(text=f"Можно выставить ордеров: {preliminary_count}")
        self.placed_orders_label.config(text=f"Выставлено ордеров: {placed_count}")
    
    def _update_balance_time_display(self):
        """Обновляет отображение времени последнего обновления баланса"""
        if self.last_balance_update_time:
            import datetime
            update_time = datetime.datetime.fromtimestamp(self.last_balance_update_time)
            time_str = update_time.strftime("%H:%M:%S")
            self.balance_update_time_label.config(text=f"Обновлено: {time_str}")
        else:
            self.balance_update_time_label.config(text="")
    
    def start_balance_update_thread(self):
        """Запускает поток для периодического обновления баланса"""
        if self.balance_update_running:
            return
        
        self.balance_update_running = True
        self.balance_update_thread = Thread(target=self._balance_update_worker, daemon=True)
        self.balance_update_thread.start()
    
    def _balance_update_worker(self):
        """Рабочий поток для периодического обновления баланса"""
        import time
        
        while self.balance_update_running:
            try:
                # Обновляем баланс для всех аккаунтов
                if self.accounts and self.api_clients:
                    for account in self.accounts:
                        account_address = account["predict_account_address"]
                        api_client = self.api_clients.get(account_address)
                        
                        if api_client:
                            try:
                                # Получаем баланс
                                balance_usdt = api_client.get_usdt_balance(
                                    account["predict_account_address"],
                                    account["privy_wallet_private_key"]
                                )
                                
                                # Обновляем информацию об аккаунте
                                if account_address in self.account_info:
                                    self.account_info[account_address]["balance"] = balance_usdt
                                
                                # Сохраняем время обновления
                                self.last_balance_update_time = time.time()
                                
                                # Обновляем отображение в GUI
                                self.root.after(0, self._update_account_info_display)
                                
                            except Exception as e:
                                # Ошибка при получении баланса для конкретного аккаунта
                                # Не останавливаем поток, просто пропускаем этот аккаунт
                                pass
                
                # Ждем 60 секунд до следующего обновления
                for _ in range(60):
                    if not self.balance_update_running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                # Критическая ошибка - логируем и продолжаем
                log_error_to_file(
                    "Ошибка в потоке обновления баланса",
                    exception=e,
                    context="balance_update_worker"
                )
                # Ждем перед следующей попыткой
                time.sleep(60)
    
    def start_websocket_monitoring(self):
        """Запускает мониторинг через WebSocket"""
        if not self.token_frames:
            return
        
        # Получаем API ключ из первого аккаунта
        api_key = self.accounts[0]["api_key"] if self.accounts else None
        
        from websocket_client import PredictWebSocketClient
        from order_calculator import OrderCalculator
        
        def on_orderbook_update(market_id: str, orderbook_data: Dict):
            """Обработчик обновления стакана через WebSocket"""
            if market_id not in self.token_frames:
                return
            
            token_frame = self.token_frames[market_id]
            settings = token_frame.settings
            
            # Сохраняем последний стакан для пересчета при изменении настроек
            if not hasattr(token_frame, 'last_orderbook'):
                token_frame.last_orderbook = None
            token_frame.last_orderbook = orderbook_data
            
            # Сохраняем время последнего обновления
            import time
            update_ts = time.time()
            token_frame.last_orderbook_update_time = update_ts
            
            # Обновляем статус WebSocket в GUI (время последнего обновления)
            if hasattr(self, 'ws_status_label'):
                self.root.after(0, lambda: self._update_ws_display(update_time=update_ts))
            
            # Получаем decimalPrecision из market_info
            decimal_precision = token_frame.market_info.get("decimalPrecision", 3)
            
            # Получаем активные ордера для вычитания нашей ликвидности
            active_orders = None
            if token_frame.order_manager:
                try:
                    active_orders = token_frame.order_manager.get_active_orders(timeout=0.1)
                except Exception:
                    active_orders = None
            
            # Рассчитываем предварительные ордера
            order_info = OrderCalculator.calculate_limit_orders(
                orderbook_data, 
                settings,
                decimal_precision=decimal_precision,
                active_orders=active_orders
            )
            
            if not order_info:
                return
            
            # Обновляем GUI
            bids = orderbook_data.get("bids", [])
            asks = orderbook_data.get("asks", [])
            best_bid = bids[0][0] if bids else None
            best_ask = asks[0][0] if asks else None
            mid_price_yes = OrderCalculator.calculate_mid_price(best_bid, best_ask) if best_bid and best_ask else None
            
            # Проверяем ликвидность и спред, если ордера уже выставлены
            if token_frame.orders_placed and token_frame.order_manager:
                # Проверяем ликвидность и спред для каждого ордера
                can_place_yes = order_info.get("can_place_yes", False)
                can_place_no = order_info.get("can_place_no", False)
                can_place_yes_liquidity = order_info.get("can_place_yes_liquidity", True)
                can_place_no_liquidity = order_info.get("can_place_no_liquidity", True)
                can_place_yes_spread = order_info.get("can_place_yes_spread", True)
                can_place_no_spread = order_info.get("can_place_no_spread", True)
                min_liquidity = order_info.get("min_liquidity", 300.0)
                min_spread = order_info.get("min_spread", 0.2)
                liquidity_yes = order_info.get("liquidity_yes", 0)
                liquidity_no = order_info.get("liquidity_no", 0)
                spread_yes = order_info.get("spread_yes", 0)
                spread_no = order_info.get("spread_no", 0)
                
                # Используем уже полученные активные ордера
                active_yes = active_orders.get("yes") if active_orders else None
                active_no = active_orders.get("no") if active_orders else None
                
                # Получаем настройки для проверки автоспреда
                settings = token_frame.settings
                auto_spread_enabled = settings.auto_spread_enabled if settings else False
                
                # Если ордер не может быть выставлен и он активен, отменяем его
                if active_yes and not can_place_yes and not token_frame.cancelling_yes:
                    # Определяем причину отмены
                    if not can_place_yes_liquidity:
                        reason = f"ликвидность упала ниже минимума (${liquidity_yes:.2f} < ${min_liquidity:.2f})"
                        # Если включен автоспред, пересчитываем цену и выставляем заново
                        if auto_spread_enabled:
                            token_frame.market_log(f"⚠️ Yes ордер: {reason}, пересчитываем цену ордера для достижения целевой ликвидности ${min_liquidity:.2f}")
                            token_frame.cancelling_yes = True
                            # Используем последний orderbook из token_frame
                            current_orderbook = token_frame.last_orderbook if hasattr(token_frame, 'last_orderbook') and token_frame.last_orderbook else orderbook_data
                            threading.Thread(
                                target=token_frame._recalculate_and_place_order_autospread,
                                args=("yes", current_orderbook, order_info, mid_price_yes),
                                daemon=True
                            ).start()
                        else:
                            token_frame.market_log(f"⚠️ Yes ордер: {reason}, отменяем")
                            token_frame.cancelling_yes = True
                            threading.Thread(
                                target=token_frame._cancel_order_thread,
                                args=("yes",),
                                daemon=True
                            ).start()
                    elif not can_place_yes_spread:
                        spread_yes_cents = spread_yes * 100
                        reason = f"спред недостаточен ({spread_yes_cents:.2f}¢ < {min_spread:.2f}¢)"
                        token_frame.market_log(f"⚠️ Yes ордер: {reason}, отменяем")
                        token_frame.cancelling_yes = True
                        threading.Thread(
                            target=token_frame._cancel_order_thread,
                            args=("yes",),
                            daemon=True
                        ).start()
                    else:
                        reason = "недостаточно условий"
                        token_frame.market_log(f"⚠️ Yes ордер: {reason}, отменяем")
                        token_frame.cancelling_yes = True
                        threading.Thread(
                            target=token_frame._cancel_order_thread,
                            args=("yes",),
                            daemon=True
                        ).start()
                
                if active_no and not can_place_no and not token_frame.cancelling_no:
                    # Определяем причину отмены
                    if not can_place_no_liquidity:
                        reason = f"ликвидность упала ниже минимума (${liquidity_no:.2f} < ${min_liquidity:.2f})"
                        # Если включен автоспред, пересчитываем цену и выставляем заново
                        if auto_spread_enabled:
                            token_frame.market_log(f"⚠️ No ордер: {reason}, пересчитываем цену ордера для достижения целевой ликвидности ${min_liquidity:.2f}")
                            token_frame.cancelling_no = True
                            # Используем последний orderbook из token_frame
                            current_orderbook = token_frame.last_orderbook if hasattr(token_frame, 'last_orderbook') and token_frame.last_orderbook else orderbook_data
                            threading.Thread(
                                target=token_frame._recalculate_and_place_order_autospread,
                                args=("no", current_orderbook, order_info, mid_price_yes),
                                daemon=True
                            ).start()
                        else:
                            token_frame.market_log(f"⚠️ No ордер: {reason}, отменяем")
                            token_frame.cancelling_no = True
                            threading.Thread(
                                target=token_frame._cancel_order_thread,
                                args=("no",),
                                daemon=True
                            ).start()
                    elif not can_place_no_spread:
                        spread_no_cents = spread_no * 100
                        reason = f"спред недостаточен ({spread_no_cents:.2f}¢ < {min_spread:.2f}¢)"
                        token_frame.market_log(f"⚠️ No ордер: {reason}, отменяем")
                        token_frame.cancelling_no = True
                        threading.Thread(
                            target=token_frame._cancel_order_thread,
                            args=("no",),
                            daemon=True
                        ).start()
                    else:
                        reason = "недостаточно условий"
                        token_frame.market_log(f"⚠️ No ордер: {reason}, отменяем")
                        token_frame.cancelling_no = True
                        threading.Thread(
                            target=token_frame._cancel_order_thread,
                            args=("no",),
                            daemon=True
                        ).start()
                
                # Если ордер был отменен (не активен) и теперь можно выставить, выставляем его снова
                # Проверяем, нужно ли выставить хотя бы один ордер
                need_place_yes = not active_yes and can_place_yes and not token_frame.cancelling_yes and not token_frame.placing_yes
                need_place_no = not active_no and can_place_no and not token_frame.cancelling_no and not token_frame.placing_no
                
                if (need_place_yes or need_place_no) and token_frame.orders_placed and not token_frame.placing_orders:
                    # Определяем причину выставления
                    if auto_spread_enabled:
                        # В режиме автоспреда - это пересчет после падения ликвидности
                        if need_place_yes:
                            token_frame.market_log(f"✓ Yes ордер: ликвидность достаточна (${liquidity_yes:.2f} >= ${min_liquidity:.2f}), выставляем по рассчитанной цене")
                        if need_place_no:
                            token_frame.market_log(f"✓ No ордер: ликвидность достаточна (${liquidity_no:.2f} >= ${min_liquidity:.2f}), выставляем по рассчитанной цене")
                    else:
                        # В обычном режиме - ликвидность восстановилась
                        if need_place_yes:
                            token_frame.market_log(f"✓ Yes ордер: ликвидность восстановилась (${liquidity_yes:.2f} >= ${min_liquidity:.2f}), выставляем снова")
                        if need_place_no:
                            token_frame.market_log(f"✓ No ордер: ликвидность восстановилась (${liquidity_no:.2f} >= ${min_liquidity:.2f}), выставляем снова")
                    
                    # Устанавливаем флаги перед вызовом
                    token_frame.placing_orders = True
                    if need_place_yes:
                        token_frame.placing_yes = True
                    if need_place_no:
                        token_frame.placing_no = True
                    
                    # Вызываем один раз - метод сам определит, какие ордера выставлять
                    threading.Thread(
                        target=token_frame._place_orders_thread,
                        args=(order_info, mid_price_yes),
                        daemon=True
                    ).start()
            
            # Вместо немедленного обновления, добавляем в очередь на пакетную обработку
            self._pending_gui_updates[market_id] = {
                'mid_price': mid_price_yes,
                'best_bid': best_bid,
                'best_ask': best_ask,
                'order_info': order_info
            }
            
            if not self._gui_update_timer:
                self._gui_update_timer = self.root.after(100, self._process_gui_updates)
        
        # Callback для изменения статуса подключения WebSocket
        def on_connection_change(connected: bool):
            """Обработчик изменения статуса подключения WebSocket"""
            if hasattr(self, 'ws_status_label'):
                status = "Live" if connected else "Отключен"
                self.root.after(0, lambda: self._update_ws_display(status=status))
        
        # Создаем WebSocket клиент
        self.ws_client = PredictWebSocketClient(
            api_key=api_key,
            on_orderbook_update=on_orderbook_update,
            on_connection_change=on_connection_change
        )
        
        # Сохраняем подписки ДО подключения
        market_ids = list(self.token_frames.keys())
        for market_id in market_ids:
            self.ws_client.subscribe_orderbook(market_id)
            self.log(f"Подписка на стакан для рынка {market_id} (будет отправлена после подключения)")
        
        # Подключаемся (подписки будут отправлены автоматически в _on_open)
        self.ws_client.connect()
        
        # Инициализируем статус WebSocket как "Отключен" (будет обновлен при подключении)
        if hasattr(self, 'ws_status_label'):
            self.root.after(0, lambda: self._update_ws_display(status="Отключен"))
        
        self.log("✓ WebSocket мониторинг запущен")
        self.log("Ожидание данных через WebSocket...")
        self.log(f"Подключение к WebSocket: {self.ws_client.ws_url}")
    
    def run(self):
        """Запускает главный цикл приложения"""
        # Обработчик закрытия окна - останавливаем поток обновления баланса
        def on_closing():
            self.balance_update_running = False
            self.root.destroy()
        
        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        self.root.mainloop()


def show_about_dialog(parent):
    """Показывает информационное окно о разработчике (модальное)"""
    dialog = tk.Toplevel(parent)
    dialog.title("О разработчике")
    dialog.geometry("450x220")
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.grab_set()
    
    # Настраиваем фон окна
    dialog.configure(bg="#f5f5f5")
    
    # Центрируем окно
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (450 // 2)
    y = (dialog.winfo_screenheight() // 2) - (220 // 2)
    dialog.geometry(f"450x220+{x}+{y}")
    
    # Основной фрейм с контентом (уменьшенные отступы)
    main_frame = tk.Frame(dialog, bg="#f5f5f5", padx=30, pady=25)
    main_frame.pack(fill=tk.BOTH, expand=True)
    
    # Текст "Этот софт разработан" (центрированный)
    title_label = tk.Label(
        main_frame,
        text="Этот софт разработан",
        font=("Arial", 11),
        bg="#f5f5f5",
        fg="#7f8c8d"
    )
    title_label.pack(pady=(0, 8))
    
    # Имя разработчика (крупно и ярко, центрированное)
    developer_label = tk.Label(
        main_frame,
        text="Rudy vs Web3",
        font=("Arial", 20, "bold"),
        bg="#f5f5f5",
        fg="#3498db"
    )
    developer_label.pack(pady=(0, 20))
    
    # Фрейм для кнопок с центрированием
    buttons_frame = tk.Frame(main_frame, bg="#f5f5f5")
    buttons_frame.pack()
    
    def open_telegram():
        """Открывает ссылку на Telegram канал"""
        import webbrowser
        telegram_url = "https://t.me/rudy_web3"
        webbrowser.open(telegram_url)
        dialog.destroy()
    
    def on_close():
        """Закрывает окно"""
        dialog.destroy()
    
    # Стилизованная кнопка Telegram (синяя, привлекательная)
    telegram_btn = tk.Button(
        buttons_frame,
        text="Перейти в Telegram канал",
        command=open_telegram,
        font=("Arial", 10, "bold"),
        bg="#3498db",
        fg="white",
        activebackground="#2980b9",
        activeforeground="white",
        relief=tk.FLAT,
        padx=18,
        pady=8,
        cursor="hand2",
        bd=0
    )
    telegram_btn.pack(side=tk.LEFT, padx=(0, 10))
    
    # Кнопка закрытия (серая, менее заметная)
    close_btn = tk.Button(
        buttons_frame,
        text="Закрыть",
        command=on_close,
        font=("Arial", 10),
        bg="#95a5a6",
        fg="white",
        activebackground="#7f8c8d",
        activeforeground="white",
        relief=tk.FLAT,
        padx=18,
        pady=8,
        cursor="hand2",
        bd=0
    )
    close_btn.pack(side=tk.LEFT)
    
    # Предупреждающий текст внизу
    warning_label = tk.Label(
        main_frame,
        text="Этот софт был навайбкоден. Используйте на свой страх и риск",
        font=("Arial", 8),
        bg="#f5f5f5",
        fg="#95a5a6"
    )
    warning_label.pack(pady=(18, 0))
    
    # Поддержка Escape для закрытия
    dialog.bind("<Escape>", lambda e: on_close())
    
    # Обработчик закрытия окна (крестик)
    dialog.protocol("WM_DELETE_WINDOW", on_close)
    
    # Обновляем окно перед ожиданием
    dialog.update()
    
    # Ждем закрытия диалога (блокирует выполнение до закрытия)
    dialog.wait_window()


def main():
    """Точка входа для GUI"""
    app = MainWindow()
    app.run()
    return app