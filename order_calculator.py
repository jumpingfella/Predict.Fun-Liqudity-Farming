"""
Модуль для расчета лимитных ордеров с учетом всех правил
"""

from typing import Dict, Optional, Tuple
from settings_manager import TokenSettings


# Константы
MIN_ORDER_VALUE_USD = 1.0  # Минимальная сумма ордера в USD
MIN_ORDER_PRICE = 0.001  # Минимальная цена ордера (0.1 цента)


class OrderCalculator:
    """Калькулятор для расчета лимитных ордеров"""
    
    @staticmethod
    def calculate_liquidity_before_price(
        orderbook: Dict,
        our_price: float,
        outcome: str = "yes",
        our_active_order: Optional[Dict] = None
    ) -> float:
        """
        Рассчитывает ликвидность (в USDT) перед нашим ордером.
        
        Для Yes: берет все ордера в bids, которые выше нашей цены (price > our_price)
        Для No: берет все ордера в asks (которые ниже нашей цены для No)
        Вычитает нашу ликвидность из общей ликвидности, если наш ордер уже выставлен.
        
        Args:
            orderbook: Данные стакана заявок
            our_price: Наша цена ордера (в долларах, например 0.954)
            outcome: "yes" или "no"
            our_active_order: Информация о нашем активном ордере {"price": float, "shares": float} или None
            
        Returns:
            Ликвидность в USDT перед нашим ордером (с вычетом нашей ликвидности)
        """
        try:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            
            if not bids or not asks:
                return 0.0
            
            total_liquidity = 0.0
            
            if outcome.lower() == "yes":
                # Для Yes: берем bids, которые выше нашей цены
                # bids отсортированы по убыванию цены (от высокой к низкой)
                for price, shares in bids:
                    # Если цена выше нашей, значит это ордер перед нами
                    if price > our_price:
                        # Ликвидность = цена * количество shares
                        liquidity = float(price) * float(shares)
                        total_liquidity += liquidity
                    else:
                        # bids отсортированы, дальше цены только ниже
                        break
            
            elif outcome.lower() == "no":
                # Для No: мы выставляем ордер на покупку No по цене our_price_no (например, 0.046)
                # Нужно найти ликвидность ПЕРЕД нашим ордером
                # Ликвидность перед нами - это все ордера на продажу No по цене ВЫШЕ our_price_no
                # (например, если мы покупаем по 0.046, то ликвидность - это продажи по 0.05, 0.06, 0.07...)
                
                # Наша цена для покупки No
                our_price_no = our_price
                
                # Продажа No по цене выше our_price_no = продажа Yes по цене ниже (1 - our_price_no)
                # Например: если мы покупаем No по 0.046, то:
                #   - Продажа No по 0.05 = продажа Yes по 0.95
                #   - Продажа No по 0.06 = продажа Yes по 0.94
                #   - Продажа No по 0.07 = продажа Yes по 0.93
                
                # asks для Yes обычно отсортированы по возрастанию (от низкой к высокой: 0.95, 0.96, 0.97...)
                # Но нам нужны продажи No по цене ВЫШЕ our_price_no, что соответствует продажам Yes по цене НИЖЕ (1 - our_price_no)
                
                # Например: наша цена No = 0.046, значит порог = 1 - 0.046 = 0.954
                # - ask Yes = 0.95 -> продажа No = 0.05 (выше 0.046) ✓ - подходит
                # - ask Yes = 0.94 -> продажа No = 0.06 (выше 0.046) ✓ - подходит
                # - ask Yes = 0.96 -> продажа No = 0.04 (ниже 0.046) ✗ - не подходит
                
                # Проходим по всем asks для Yes
                # Для каждого ask конвертируем цену Yes в цену No и проверяем, выше ли она нашей цены покупки
                for yes_price, shares in asks:
                    yes_price_float = float(yes_price)
                    
                    # Конвертируем цену Yes в цену No
                    # Это и есть цена по которой считается ликвидность для No
                    no_price = 1.0 - yes_price_float
                    
                    # Проверяем, что цена No выше нашей цены покупки
                    # Это означает, что продажа No происходит по цене выше our_price_no (перед нами)
                    # Например: если мы покупаем No по 0.046, то ликвидность - это продажи по 0.05, 0.06, 0.07...
                    if no_price > our_price_no:
                        # Считаем ликвидность: цена No * количество shares
                        # Это правильный расчет - ликвидность считается по цене No (0.05, 0.06, 0.07...)
                        # Например: no_price = 0.05, shares = 100 -> liquidity = 5.0 USDT
                        liquidity = no_price * float(shares)
                        total_liquidity += liquidity
                    elif no_price <= our_price_no:
                        # Если цена No меньше или равна нашей цене покупки,
                        # это уже не ликвидность перед нами
                        # Так как asks для Yes отсортированы по возрастанию цены Yes,
                        # то цены No будут отсортированы по убыванию
                        # Поэтому дальше все цены No будут только меньше или равны, можем прервать
                        break
            
            # Вычитаем нашу ликвидность из общей ликвидности, если наш ордер уже выставлен
            # ВАЖНО: вычитаем только если наш ордер находится СТРОГО выше нашей цены покупки,
            # потому что только такие ордера учитываются в ликвидности выше
            if our_active_order:
                our_order_price = our_active_order.get("price", 0)
                our_order_shares = our_active_order.get("shares", 0)
                
                if outcome.lower() == "yes":
                    # Для Yes: мы считаем ликвидность только для ордеров с price > our_price
                    # Поэтому вычитаем наш ордер только если он находится СТРОГО выше нашей цены
                    if our_order_price > our_price:
                        # Наш ордер Yes находится выше нашей цены покупки, он учтен в ликвидности - вычитаем его
                        our_liquidity = our_order_price * our_order_shares
                        total_liquidity -= our_liquidity
                    # Если наш ордер по цене our_price или ниже, он не учитывается в ликвидности, вычитать не нужно
                elif outcome.lower() == "no":
                    # Для No: мы считаем ликвидность только для ордеров с no_price > our_price_no
                    # Поэтому вычитаем наш ордер только если он находится СТРОГО выше нашей цены
                    if our_order_price > our_price:
                        # Наш ордер No находится выше нашей цены покупки, он учтен в ликвидности - вычитаем его
                        our_liquidity = our_order_price * our_order_shares
                        total_liquidity -= our_liquidity
                    # Если наш ордер по цене our_price или ниже, он не учитывается в ликвидности, вычитать не нужно
            
            # Убеждаемся, что ликвидность не отрицательная
            return max(0.0, total_liquidity)
            
        except Exception as e:
            print(f"✗ Ошибка расчета ликвидности: {e}")
            import traceback
            traceback.print_exc()
            return 0.0
    
    @staticmethod
    def calculate_no_price(yes_price: float) -> float:
        """
        Рассчитывает цену No из цены Yes.
        Yes + No = 1
        
        Args:
            yes_price: Цена Yes (от 0 до 1)
            
        Returns:
            Цена No
        """
        return 1.0 - yes_price
    
    @staticmethod
    def calculate_yes_price(no_price: float) -> float:
        """
        Рассчитывает цену Yes из цены No.
        Yes + No = 1
        
        Args:
            no_price: Цена No (от 0 до 1)
            
        Returns:
            Цена Yes
        """
        return 1.0 - no_price
    
    @staticmethod
    def calculate_mid_price(best_bid: float, best_ask: float) -> float:
        """
        Рассчитывает mid-прайс.
        
        Args:
            best_bid: Лучшая цена покупки (Yes)
            best_ask: Лучшая цена продажи (Yes)
            
        Returns:
            Mid-прайс (Yes)
        """
        return (best_bid + best_ask) / 2
    
    @staticmethod
    def calculate_order_prices(
        mid_price_yes: float,
        spread_percent: float
    ) -> Tuple[float, float]:
        """
        Рассчитывает цены ордеров на основе mid-прайса и спреда.
        
        Args:
            mid_price_yes: Mid-прайс для Yes
            spread_percent: Спред в процентах
            
        Returns:
            Tuple (buy_price_yes, sell_price_yes)
        """
        spread = spread_percent / 100
        
        # Цена покупки Yes (ниже mid-прайса)
        buy_price_yes = mid_price_yes * (1 - spread)
        
        # Цена продажи Yes (выше mid-прайса)
        sell_price_yes = mid_price_yes * (1 + spread)
        
        # Применяем минимальную цену
        buy_price_yes = max(buy_price_yes, MIN_ORDER_PRICE)
        sell_price_yes = max(sell_price_yes, MIN_ORDER_PRICE)
        
        # Убеждаемся, что цена не превышает 1
        buy_price_yes = min(buy_price_yes, 1.0)
        sell_price_yes = min(sell_price_yes, 1.0)
        
        return buy_price_yes, sell_price_yes
    
    @staticmethod
    def calculate_shares_from_usdt(usdt_amount: float, price: float) -> float:
        """
        Рассчитывает количество shares из суммы в USDT.
        
        Args:
            usdt_amount: Сумма в USDT
            price: Цена одной share
            
        Returns:
            Количество shares
        """
        if price <= 0:
            return 0
        return usdt_amount / price
    
    @staticmethod
    def calculate_usdt_from_shares(shares: float, price: float) -> float:
        """
        Рассчитывает сумму в USDT из количества shares.
        
        Args:
            shares: Количество shares
            price: Цена одной share
            
        Returns:
            Сумма в USDT
        """
        return shares * price
    
    @staticmethod
    def adjust_to_min_order_value(
        shares: float,
        price: float
    ) -> float:
        """
        Корректирует количество shares до минимальной суммы ордера (1 USD).
        
        Args:
            shares: Исходное количество shares
            price: Цена одной share
            
        Returns:
            Скорректированное количество shares
        """
        # Проверяем, что цена не равна нулю
        if price <= 0:
            # Если цена 0 или отрицательная, возвращаем исходное количество
            # или минимальное значение, если исходное тоже 0
            return max(shares, MIN_ORDER_VALUE_USD / MIN_ORDER_PRICE)
        
        order_value = shares * price
        
        if order_value < MIN_ORDER_VALUE_USD:
            # Рассчитываем необходимое количество shares для 1 USD
            shares = MIN_ORDER_VALUE_USD / price
        
        return shares
    
    @staticmethod
    def round_price_by_precision(
        price: float,
        decimal_precision: int
    ) -> float:
        """
        Округляет цену в соответствии с decimalPrecision рынка.
        
        Args:
            price: Цена для округления (в долларах, например 0.951)
            decimal_precision: Точность цены (2 = целые центы, 3 = десятые центы)
            
        Returns:
            Округленная цена
        """
        if decimal_precision == 2:
            # Округляем до целых центов (2 знака после запятой: 0.34, 0.25, 0.48)
            return round(price, 2)
        elif decimal_precision == 3:
            # Округляем до десятых центов (3 знака после запятой: 0.951, 0.347, 0.852)
            return round(price, 3)
        else:
            # По умолчанию округляем до 3 знаков
            return round(price, 3)
    
    @staticmethod
    def round_shares_to_tenths(
        shares: float,
        price: float
    ) -> float:
        """
        Округляет количество shares до десятых (1 знак после запятой)
        с проверкой минимальной суммы ордера ($1).
        
        Args:
            shares: Количество shares для округления
            price: Цена одной share
            
        Returns:
            Округленное количество shares (до десятых)
        """
        # Округляем до десятых (1 знак после запятой)
        shares_rounded = round(shares, 1)
        
        # Проверяем, что после округления ордер >= $1
        order_value = shares_rounded * price
        
        # Если после округления ордер меньше $1, увеличиваем на 0.1
        while order_value < MIN_ORDER_VALUE_USD:
            shares_rounded += 0.1
            order_value = shares_rounded * price
        
        return shares_rounded

    @staticmethod
    def find_price_by_target_liquidity(
        orderbook: Dict,
        target_liquidity: float,
        outcome: str = "yes",
        decimal_precision: int = 3,
        return_info: bool = False
    ):
        """
        Ищет цену, перед которой стоит указанный объем ликвидности.
        
        Args:
            orderbook: Данные стакана
            target_liquidity: Целевая ликвидность
            outcome: "yes" или "no"
            decimal_precision: Точность цены
            return_info: Если True, возвращает кортеж (цена, информация), иначе только цену
            
        Returns:
            float или tuple: Цена или (цена, информация) если return_info=True
        """
        try:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            
            if not bids or not asks:
                info = "Стакан пуст (нет bids или asks)"
                if return_info:
                    return (0.0, info)
                return 0.0
                
            accumulated_liquidity = 0.0
            tick = 1 / (10 ** decimal_precision)
            
            if outcome.lower() == "yes":
                # Для Yes идем по bids (покупатели) сверху вниз
                for price, shares in bids:
                    price_float = float(price)
                    liquidity = price_float * float(shares)
                    accumulated_liquidity += liquidity
                    
                    if accumulated_liquidity >= target_liquidity:
                        found_price = round(price_float - tick, decimal_precision)
                        info = f"Найдена цена {found_price:.4f}, накопленная ликвидность: ${accumulated_liquidity:.2f}"
                        if return_info:
                            return (found_price, info)
                        return found_price
                
                # Если не нашли, значит ликвидности недостаточно
                last_price = float(bids[-1][0]) if bids else 0.0
                found_price = round(last_price - tick, decimal_precision)
                info = f"Недостаточно ликвидности: накоплено ${accumulated_liquidity:.2f} из ${target_liquidity:.2f}, минимальная цена: {found_price:.4f}"
                if return_info:
                    return (found_price, info)
                return found_price
                
            elif outcome.lower() == "no":
                # Для No идем по asks для Yes (продавцы Yes = покупатели No)
                for yes_price, shares in asks:
                    yes_price_float = float(yes_price)
                    no_price = round(1.0 - yes_price_float, 4)
                    
                    liquidity = no_price * float(shares)
                    accumulated_liquidity += liquidity
                    
                    if accumulated_liquidity >= target_liquidity:
                        found_price = round(no_price - tick, decimal_precision)
                        info = f"Найдена цена {found_price:.4f}, накопленная ликвидность: ${accumulated_liquidity:.2f}"
                        if return_info:
                            return (found_price, info)
                        return found_price
                        
                # Если не нашли, значит ликвидности недостаточно
                last_yes_price = float(asks[-1][0]) if asks else 1.0
                last_no_price = round(1.0 - last_yes_price, 4)
                found_price = round(last_no_price - tick, decimal_precision)
                info = f"Недостаточно ликвидности: накоплено ${accumulated_liquidity:.2f} из ${target_liquidity:.2f}, минимальная цена: {found_price:.4f}"
                if return_info:
                    return (found_price, info)
                return found_price
                
            info = f"Неизвестный outcome: {outcome}"
            if return_info:
                return (0.0, info)
            return 0.0
        except Exception as e:
            info = f"Ошибка при расчете: {str(e)}"
            if return_info:
                return (0.0, info)
            return 0.0

    @staticmethod
    def calculate_limit_orders(
        orderbook: Dict,
        settings: TokenSettings,
        decimal_precision: int = 3,
        active_orders: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Рассчитывает лимитные ордера на основе стакана и настроек.
        """
        try:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            
            if not bids or not asks:
                # Логируем причину возврата None
                reason = []
                if not bids:
                    reason.append("bids пуст")
                if not asks:
                    reason.append("asks пуст")
                return None
            
            best_bid_yes = bids[0][0] if bids else None
            best_ask_yes = asks[0][0] if asks else None
            
            if best_bid_yes is None or best_ask_yes is None:
                # Логируем причину возврата None
                reason = []
                if best_bid_yes is None:
                    reason.append("best_bid_yes=None")
                if best_ask_yes is None:
                    reason.append("best_ask_yes=None")
                return None
            
            # Рассчитываем mid-прайс
            mid_price_yes = (best_bid_yes + best_ask_yes) / 2
            mid_price_no = 1.0 - mid_price_yes
            
            # 1. Рассчитываем базовые цены покупки
            if settings.auto_spread_enabled:
                target_liq = settings.target_liquidity or 1000.0
                max_spread_dollars = (settings.max_auto_spread or 6.0) / 100.0
                
                buy_price_yes = OrderCalculator.find_price_by_target_liquidity(orderbook, target_liq, "yes", decimal_precision)
                buy_price_no = OrderCalculator.find_price_by_target_liquidity(orderbook, target_liq, "no", decimal_precision)
                
                # Ограничиваем максимальным спредом от мидпрайса
                buy_price_yes = max(buy_price_yes, mid_price_yes - max_spread_dollars)
                buy_price_no = max(buy_price_no, mid_price_no - max_spread_dollars)
            else:
                # ИСПРАВЛЕНИЕ: spread_percent - это ПРОЦЕНТ, а не абсолютное значение в центах!
                # Правильная формула: цена = mid_price * (1 - процент/100)
                # Например: mid_price = 0.50, spread = 3% -> цена = 0.50 * (1 - 0.03) = 0.485
                spread_fraction = settings.spread_percent / 100.0  # процент → доля
                buy_price_yes = mid_price_yes * (1 - spread_fraction)
                buy_price_no = mid_price_no * (1 - spread_fraction)

            # 2. Округляем и применяем лимиты
            buy_price_yes = OrderCalculator.round_price_by_precision(buy_price_yes, decimal_precision)
            buy_price_no = OrderCalculator.round_price_by_precision(buy_price_no, decimal_precision)

            buy_price_yes = max(min(buy_price_yes, 0.999), MIN_ORDER_PRICE)
            buy_price_no = max(min(buy_price_no, 0.999), MIN_ORDER_PRICE)

            # Определяем размер позиции (одинаковый для Yes и No)
            if settings.position_size_usdt is not None:
                # Размер в USDT - рассчитываем shares для Yes
                buy_shares_yes = OrderCalculator.calculate_shares_from_usdt(
                    settings.position_size_usdt,
                    buy_price_yes
                )

                # Корректируем до минимальной суммы
                buy_shares_yes = OrderCalculator.adjust_to_min_order_value(
                    buy_shares_yes,
                    buy_price_yes
                )

                # Округляем до десятых с проверкой минимальной суммы
                buy_shares_yes = OrderCalculator.round_shares_to_tenths(
                    buy_shares_yes,
                    buy_price_yes
                )

                # Рассчитываем итоговую стоимость для Yes
                buy_value_yes_usd = OrderCalculator.calculate_usdt_from_shares(
                    buy_shares_yes,
                    buy_price_yes
                )

                # Для No используем тот же размер в USDT
                buy_shares_no = OrderCalculator.calculate_shares_from_usdt(
                    settings.position_size_usdt,
                    buy_price_no
                )

                # Корректируем до минимальной суммы
                buy_shares_no = OrderCalculator.adjust_to_min_order_value(
                    buy_shares_no,
                    buy_price_no
                )

                # Округляем до десятых с проверкой минимальной суммы
                buy_shares_no = OrderCalculator.round_shares_to_tenths(
                    buy_shares_no,
                    buy_price_no
                )

                # Рассчитываем итоговую стоимость для No
                buy_value_no_usd = OrderCalculator.calculate_usdt_from_shares(
                    buy_shares_no,
                    buy_price_no
                )

            elif settings.position_size_shares is not None:
                # Размер в shares - одинаковый для Yes и No
                buy_shares_yes = settings.position_size_shares
                buy_shares_no = settings.position_size_shares

                # Корректируем до минимальной суммы
                buy_shares_yes = OrderCalculator.adjust_to_min_order_value(
                    buy_shares_yes,
                    buy_price_yes
                )
                buy_shares_no = OrderCalculator.adjust_to_min_order_value(
                    buy_shares_no,
                    buy_price_no
                )

                # Округляем до десятых с проверкой минимальной суммы
                buy_shares_yes = OrderCalculator.round_shares_to_tenths(
                    buy_shares_yes,
                    buy_price_yes
                )
                buy_shares_no = OrderCalculator.round_shares_to_tenths(
                    buy_shares_no,
                    buy_price_no
                )

                # Рассчитываем итоговую стоимость
                buy_value_yes_usd = OrderCalculator.calculate_usdt_from_shares(
                    buy_shares_yes,
                    buy_price_yes
                )
                buy_value_no_usd = OrderCalculator.calculate_usdt_from_shares(
                    buy_shares_no,
                    buy_price_no
                )
            else:
                return None

            # Общая стоимость = максимальное значение из Yes и No
            # (потому что только один из ордеров исполнится, а не оба)
            total_value_usd = max(buy_value_yes_usd, buy_value_no_usd)

            # Рассчитываем ликвидность перед нашими ордерами
            # Передаем информацию о наших активных ордерах для вычитания нашей ликвидности
            # (если ордера уже выставлены)
            our_active_order_yes = None
            our_active_order_no = None

            if active_orders:
                our_active_order_yes = active_orders.get("yes")
                our_active_order_no = active_orders.get("no")

            liquidity_yes = OrderCalculator.calculate_liquidity_before_price(
                orderbook, buy_price_yes, "yes", our_active_order_yes
            )
            liquidity_no = OrderCalculator.calculate_liquidity_before_price(
                orderbook, buy_price_no, "no", our_active_order_no
            )

            # Проверяем, достаточна ли ликвидность для выставления ордеров
            # Если включен АВТОСПРЕД, то минимальным порогом становится ЦЕЛЕВАЯ ликвидность
            if settings.auto_spread_enabled:
                min_liquidity = settings.target_liquidity or 1000.0
            else:
                min_liquidity = settings.min_liquidity_usdt or 0.0

            can_place_yes_liquidity = liquidity_yes >= min_liquidity
            can_place_no_liquidity = liquidity_no >= min_liquidity

            # Проверяем минимальный спред (только если цена ордера минимальная - 0.001)
            # Пользователь вводит значение в центах, конвертируем в доллары для сравнения
            min_spread_cents = settings.min_spread or 0.2
            min_spread_dollars = min_spread_cents / 100.0  # Конвертируем центы в доллары
            MIN_PRICE = 0.001  # Минимальная цена ордера

            can_place_yes_spread = True
            can_place_no_spread = True

            # Проверяем спред для Yes ордера
            if buy_price_yes <= MIN_PRICE:
                # Ордер по минимальной цене, проверяем спред (в долларах)
                spread_yes = abs(mid_price_yes - buy_price_yes)
                if spread_yes < min_spread_dollars:
                    can_place_yes_spread = False

            # Проверяем спред для No ордера
            if buy_price_no <= MIN_PRICE:
                # Ордер по минимальной цене, проверяем спред (в долларах)
                spread_no = abs(mid_price_no - buy_price_no)
                if spread_no < min_spread_dollars:
                    can_place_no_spread = False

            # Вычисляем спреды для отображения (если еще не вычислены)
            if 'spread_yes' not in locals():
                spread_yes = abs(mid_price_yes - buy_price_yes) if mid_price_yes else 0.0
            if 'spread_no' not in locals():
                spread_no = abs(mid_price_no - buy_price_no) if mid_price_no else 0.0

            # Финальное решение: можно выставить ордер только если прошли обе проверки
            can_place_yes = can_place_yes_liquidity and can_place_yes_spread
            can_place_no = can_place_no_liquidity and can_place_no_spread

            return {
                "mid_price_yes": mid_price_yes,
                "mid_price_no": mid_price_no,
                "best_bid_yes": best_bid_yes,
                "best_ask_yes": best_ask_yes,
                "buy_yes": {
                    "price": buy_price_yes,
                    "shares": buy_shares_yes,
                    "value_usd": buy_value_yes_usd
                },
                "buy_no": {
                    "price": buy_price_no,
                    "shares": buy_shares_no,
                    "value_usd": buy_value_no_usd
                },
                "total_value_usd": total_value_usd,
                "liquidity_yes": liquidity_yes,
                "liquidity_no": liquidity_no,
                "can_place_yes": can_place_yes,
                "can_place_no": can_place_no,
                "min_liquidity": min_liquidity,
                "spread_yes": spread_yes,
                "spread_no": spread_no,
                "min_spread": min_spread_cents,  # Сохраняем в центах для GUI
                "can_place_yes_liquidity": can_place_yes_liquidity,
                "can_place_no_liquidity": can_place_no_liquidity,
                "can_place_yes_spread": can_place_yes_spread,
                "can_place_no_spread": can_place_no_spread
            }

        except Exception as e:
            error_msg = f"✗ Ошибка расчета лимитных ордеров: {e}"
            print(error_msg)
            import traceback
            traceback.print_exc()
            return None