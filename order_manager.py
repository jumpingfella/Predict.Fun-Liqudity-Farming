"""
Модуль для управления лимитными ордерами
"""

import threading
import time
from typing import Dict, Optional, Callable, List
from predict_sdk import OrderBuilder, ChainId, OrderBuilderOptions, Side, BuildOrderInput, LimitHelperInput
from config import API_BASE_URL, format_proxy
from auth import get_auth_headers, get_auth_jwt
from logger import log_error_to_file
import requests


class OrderManager:
    """Менеджер для управления лимитными ордерами"""
    
    def __init__(
        self,
        market_id: str,
        api_key: str,
        jwt_token: str,
        predict_account_address: str,
        privy_wallet_private_key: str,
        market_info: Optional[Dict] = None,
        proxy: Optional[str] = None,
        log_func: Callable = print
    ):
        """
        Инициализация менеджера ордеров.
        
        Args:
            market_id: ID рынка
            api_key: API ключ
            jwt_token: JWT токен для аутентификации
            predict_account_address: Адрес Predict Account (deposit address)
            privy_wallet_private_key: Приватный ключ Privy Wallet
            market_info: Информация о рынке (для получения isNegRisk, isYieldBearing и т.д.)
            proxy: Прокси в формате user:pass@host:port
            log_func: Функция для логирования
        """
        self.market_id = market_id
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.predict_account_address = predict_account_address
        self.privy_wallet_private_key = privy_wallet_private_key
        self.market_info = market_info
        self.proxy_string = proxy  # Сохраняем оригинальную строку прокси
        self.proxy = format_proxy(proxy)  # Форматируем для requests
        self.log_func = log_func
        self.headers = get_auth_headers(jwt_token, api_key)
        
        # Получаем title для логирования
        self.market_title = None
        if market_info:
            self.market_title = market_info.get("title") or market_info.get("question") or market_id
        else:
            self.market_title = market_id
        
        # Логируем информацию о market_info для отладки
        if market_info:
            self.log_func(f"[{market_id}] [DEBUG] market_info получен, поля: {list(market_info.keys())}")
            if "outcomes" in market_info:
                outcomes = market_info.get("outcomes", [])
                self.log_func(f"[{market_id}] [DEBUG] Найдено outcomes: {len(outcomes)}")
                for i, out in enumerate(outcomes):
                    self.log_func(f"[{market_id}] [DEBUG] Outcome {i}: name={out.get('name')}, keys={list(out.keys())}")
            else:
                self.log_func(f"[{market_id}] [DEBUG] ⚠️ outcomes отсутствуют в market_info!")
        else:
            self.log_func(f"[{market_id}] [DEBUG] ⚠️ market_info не передан!")
        
        # Создаем OrderBuilder
        privy_key = privy_wallet_private_key
        if privy_key.startswith("0x"):
            privy_key = privy_key[2:]
        
        self.builder = OrderBuilder.make(
            ChainId.BNB_MAINNET,
            privy_key,
            OrderBuilderOptions(predict_account=predict_account_address),
        )
        
        # Текущие активные ордера
        self.active_orders = {
            "yes": None,  # {"order_id": str, "hash": str, "price": float, "shares": float}
            "no": None
        }
        
        # Статистика
        self.stats = {
            "placed": 0,  # Количество выставленных ордеров
            "cancelled": 0  # Количество отмененных ордеров
        }
        
        # Текущий mid_price для отслеживания изменений
        self.last_mid_price_yes = None
        
        # Блокировка для потокобезопасности
        self.lock = threading.Lock()
        
        # Флаг активности
        self.is_active = False
        
        # Флаг процесса выставления ордеров (чтобы не дублировать)
        self.placing_orders = False
    
    def _refresh_jwt(self) -> bool:
        """
        Обновляет JWT токен, получая новый как при первом запуске.
        
        Returns:
            True если успешно, False при ошибке
        """
        try:
            self.log_func(f"[{self.market_id}] Обновление JWT токена...")
            
            new_jwt = get_auth_jwt(
                self.api_key,
                self.predict_account_address,
                self.privy_wallet_private_key,
                self.proxy_string,  # Передаем оригинальную строку, а не отформатированный словарь
                log_func=self.log_func
            )
            
            if new_jwt:
                self.jwt_token = new_jwt
                self.headers = get_auth_headers(self.jwt_token, self.api_key)
                self.log_func(f"[{self.market_id}] ✓ JWT токен успешно обновлен")
                return True
            else:
                self.log_func(f"[{self.market_id}] ✗ Не удалось получить новый JWT токен")
                return False
                
        except Exception as e:
            self.log_func(f"[{self.market_id}] ✗ Ошибка обновления JWT токена: {e}")
            log_error_to_file(
                f"Ошибка обновления JWT токена",
                exception=e,
                context=f"market_id={self.market_id}, predict_account={self.predict_account_address[:20]}..."
            )
            return False
    
    def _get_market_params(self) -> Dict:
        """Получает параметры рынка"""
        if not self.market_info:
            return {
                "fee_rate_bps": 200,
                "is_neg_risk": False,
                "is_yield_bearing": True
            }
        
        return {
            "fee_rate_bps": self.market_info.get("feeRateBps", 200),
            "is_neg_risk": self.market_info.get("isNegRisk", False),
            "is_yield_bearing": self.market_info.get("isYieldBearing", True)
        }
    
    def _get_token_id(self, outcome: str) -> Optional[str]:
        """Получает tokenId для outcome"""
        if not self.market_info:
            self.log_func(f"[{self.market_id}] ✗ market_info отсутствует")
            return None
        
        outcomes = self.market_info.get("outcomes", [])
        if not outcomes:
            self.log_func(f"[{self.market_id}] ✗ outcomes отсутствуют в market_info")
            self.log_func(f"[{self.market_id}] Доступные поля в market_info: {list(self.market_info.keys())}")
            return None
        
        if len(outcomes) < 2:
            self.log_func(f"[{self.market_id}] ✗ Недостаточно outcomes: {len(outcomes)}")
            return None
        
        # Ищем outcome по имени (более надежно, чем по индексу)
        # API возвращает "Yes" и "No" с заглавной буквы
        outcome_lower = outcome.lower()
        target_outcome = None
        
        for out in outcomes:
            out_name = out.get("name", "")
            out_name_lower = out_name.lower()
            
            # Сравниваем имена (с учетом регистра и вариантов)
            if (outcome_lower == "yes" and out_name_lower in ["yes", "y"]) or \
               (outcome_lower == "no" and out_name_lower in ["no", "n"]) or \
               out_name_lower == outcome_lower:
                target_outcome = out
                break
        
        # Если не нашли по имени, используем индекс как fallback
        # Yes обычно первый (index 0), No второй (index 1)
        if not target_outcome:
            outcome_index = 0 if outcome_lower == "yes" else 1
            if outcome_index < len(outcomes):
                target_outcome = outcomes[outcome_index]
        
        if not target_outcome:
            self.log_func(f"[{self.market_id}] ✗ Не найден outcome для '{outcome}'")
            self.log_func(f"[{self.market_id}] Доступные outcomes: {[out.get('name', 'N/A') for out in outcomes]}")
            return None
        
        # Пробуем разные варианты названий полей для tokenId
        # Основное поле - onChainId (как показано в API ответе)
        token_id = (
            target_outcome.get("onChainId") or
            target_outcome.get("on_chain_id") or
            target_outcome.get("tokenId") or
            target_outcome.get("token_id") or
            target_outcome.get("id")
        )
        
        if not token_id:
            self.log_func(f"[{self.market_id}] ✗ Не найден tokenId в outcome '{target_outcome.get('name', 'N/A')}'")
            self.log_func(f"[{self.market_id}] Доступные поля в outcome: {list(target_outcome.keys())}")
            return None
        
        return str(token_id)
    
    def place_order(
        self,
        outcome: str,  # "yes" или "no"
        price: float,
        shares: float
    ) -> Optional[Dict]:
        """
        Выставляет лимитный ордер через OrderBuilder и API.
        
        Args:
            outcome: Исход ("yes" или "no")
            price: Цена ордера (в долларах, например 0.952)
            shares: Количество shares
        
        Returns:
            Информация об ордере или None при ошибке
        """
        try:
            self.log_func(f"[{self.market_id}] [{self.market_title}] Выставление ордера {outcome.upper()}: цена={price:.4f}, shares={shares:.1f}")
            
            # Получаем tokenId
            token_id = self._get_token_id(outcome)
            if not token_id:
                self.log_func(f"[{self.market_id}] ✗ Не найден tokenId для outcome {outcome}")
                return None
            
            # Получаем параметры рынка
            params = self._get_market_params()
            fee_rate_bps = params["fee_rate_bps"]
            is_neg_risk = params["is_neg_risk"]
            is_yield_bearing = params["is_yield_bearing"]
            
            # Примечание: approvals должны быть установлены заранее или через отдельный процесс
            # Убираем вызов set_approvals() здесь, так как он блокирует выполнение на ~6 секунд
            # и approvals обычно уже установлены при первой инициализации кошелька
            
            # Конвертируем цену и количество в wei
            WEI_DECIMALS = 10**18
            price_wei = int(price * WEI_DECIMALS)
            quantity_wei = int(shares * WEI_DECIMALS)
            
            # Рассчитываем суммы для LIMIT ордера
            amounts = self.builder.get_limit_order_amounts(
                LimitHelperInput(
                    side=Side.BUY,
                    price_per_share_wei=price_wei,
                    quantity_wei=quantity_wei,
                )
            )
            
            # Создаем ордер
            order = self.builder.build_order(
                "LIMIT",
                BuildOrderInput(
                    side=Side.BUY,
                    token_id=str(token_id),
                    maker_amount=str(amounts.maker_amount),
                    taker_amount=str(amounts.taker_amount),
                    fee_rate_bps=fee_rate_bps,
                ),
            )
            
            # Создаем typed data
            typed_data = self.builder.build_typed_data(
                order,
                is_neg_risk=is_neg_risk,
                is_yield_bearing=is_yield_bearing
            )
            
            # Подписываем ордер
            signed_order = self.builder.sign_typed_data_order(typed_data)
            
            # Вычисляем hash
            order_hash = self.builder.build_typed_data_hash(typed_data)
            
            # Конвертируем signed_order в словарь
            try:
                order_dict = signed_order.to_dict()
            except AttributeError:
                try:
                    order_dict = signed_order.dict()
                except AttributeError:
                    # Используем атрибуты напрямую
                    side_value = order.side.value if hasattr(order.side, 'value') else (0 if order.side == Side.BUY else 1)
                    sig_type_value = order.signature_type.value if hasattr(order.signature_type, 'value') else 0
                    
                    order_dict = {
                        "salt": str(order.salt),
                        "maker": order.maker,
                        "signer": order.signer,
                        "taker": order.taker,
                        "token_id": order.token_id,
                        "maker_amount": str(order.maker_amount),
                        "taker_amount": str(order.taker_amount),
                        "expiration": str(order.expiration),
                        "nonce": str(order.nonce),
                        "fee_rate_bps": order.fee_rate_bps,
                        "side": side_value,
                        "signature_type": sig_type_value,
                    }
                    
                    if hasattr(signed_order, 'signature'):
                        order_dict['signature'] = signed_order.signature
                    elif hasattr(signed_order, 'sig'):
                        order_dict['signature'] = signed_order.sig
            
            # Преобразуем в camelCase
            final_order = {}
            key_mapping = {
                'maker_amount': 'makerAmount',
                'taker_amount': 'takerAmount',
                'token_id': 'tokenId',
                'fee_rate_bps': 'feeRateBps',
                'signature_type': 'signatureType',
            }
            
            for key, value in order_dict.items():
                camel_key = key_mapping.get(key, key)
                
                if camel_key == 'side':
                    if isinstance(value, Side):
                        final_order[camel_key] = value.value
                    elif isinstance(value, int):
                        final_order[camel_key] = value
                    else:
                        final_order[camel_key] = 0 if value == Side.BUY else 1
                elif camel_key == 'signatureType':
                    final_order[camel_key] = value.value if hasattr(value, 'value') else int(value) if isinstance(value, str) else value
                elif camel_key == 'signature':
                    sig_str = str(value)
                    if not sig_str.startswith('0x'):
                        sig_str = '0x' + sig_str
                    final_order[camel_key] = sig_str
                elif camel_key in ['makerAmount', 'takerAmount']:
                    final_order[camel_key] = str(value)
                elif camel_key in ['salt', 'expiration', 'nonce', 'feeRateBps', 'tokenId']:
                    final_order[camel_key] = str(value)
                else:
                    final_order[camel_key] = value
            
            final_order['hash'] = order_hash
            
            # Формируем тело запроса
            price_per_share_wei = amounts.price_per_share
            body = {
                "data": {
                    "pricePerShare": str(price_per_share_wei),
                    "strategy": "LIMIT",
                    "slippageBps": "0",
                    "order": final_order,
                }
            }
            
            self.log_func(f"[{self.market_id}] [{self.market_title}] Отправка ордера в API...")
            
            # Отправляем ордер в API с повторными попытками
            max_attempts = 3
            import time
            
            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.post(
                        f"{API_BASE_URL}/v1/orders",
                        headers=self.headers,
                        json=body,
                        proxies=self.proxy,
                        timeout=30
                    )
                    
                    if not response.ok:
                        error_text = response.text[:500] if response.text else "Нет текста ошибки"
                        error_msg = f"Ошибка API при выставлении ордера (попытка {attempt}/{max_attempts}): {response.status_code} - {error_text}"
                        
                        # Проверяем на ошибку недостатка средств
                        if response.status_code == 400 and ("Insufficient collateral" in error_text or "CollateralPerMarketExceededError" in error_text):
                            self.log_func(f"[{self.market_id}] ✗ Недостаточно средств на аккаунте для выставления ордера")
                            
                            # Получаем активные ордера через API для этого рынка
                            if attempt == 1:  # Делаем только при первой попытке, чтобы избежать бесконечного цикла
                                market_orders = self._get_active_orders_from_api()
                                if market_orders:
                                    self.log_func(f"[{self.market_id}] ⚠️ Обнаружены {len(market_orders)} активных ордеров по этому рынку, возможно средства заморожены. Отменяем...")
                                    # Отменяем все ордера по этому рынку
                                    order_ids = [order.get("id") for order in market_orders if order.get("id")]
                                    if order_ids:
                                        self._cancel_orders_by_ids(order_ids)
                                        # Небольшая задержка для освобождения средств
                                        import time
                                        time.sleep(1.0)  # Увеличиваем задержку для освобождения средств
                                        # Пробуем выставить ордер снова (повторная попытка)
                                        self.log_func(f"[{self.market_id}] Повторная попытка выставления ордера после отмены...")
                                        continue  # Повторяем попытку выставления
                            
                            log_error_to_file(
                                f"Недостаточно средств на аккаунте: {error_text}",
                                context=f"market_id={self.market_id}, outcome={outcome}, price={price}, shares={shares}"
                            )
                            return None
                        
                        # Проверяем на ошибку Invalid JWT (401)
                        is_invalid_jwt = False
                        try:
                            if response.status_code == 401:
                                response_json = response.json()
                                if response_json.get("message") == "Invalid JWT":
                                    is_invalid_jwt = True
                        except:
                            pass
                        
                        if is_invalid_jwt:
                            # Обновляем JWT и повторяем попытку
                            self.log_func(f"[{self.market_id}] Обнаружена ошибка Invalid JWT, обновляем токен...")
                            if self._refresh_jwt():
                                self.log_func(f"[{self.market_id}] Повторная попытка выставления ордера с новым JWT...")
                                continue
                            else:
                                # Не удалось обновить JWT
                                log_error_to_file(
                                    f"Не удалось обновить JWT токен при выставлении ордера",
                                    context=f"market_id={self.market_id}, outcome={outcome}, price={price}, shares={shares}"
                                )
                                return None
                        
                        self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                        
                        # Определяем, нужно ли повторять попытку
                        should_retry = False
                        retry_delay = 1  # По умолчанию 1 секунда
                        
                        if response.status_code == 429:
                            # Rate limit exceeded - повторяем с увеличенной задержкой
                            should_retry = attempt < max_attempts
                            # Фиксированные задержки для 429: 30 секунд, затем 65 секунд
                            if attempt == 1:
                                retry_delay = 30
                            elif attempt == 2:
                                retry_delay = 65
                            else:
                                retry_delay = 65  # На всякий случай
                        elif response.status_code in [502, 503, 504] or response.status_code >= 500:
                            # Серверные ошибки - повторяем
                            should_retry = attempt < max_attempts
                            retry_delay = 1
                        
                        if should_retry:
                            self.log_func(f"[{self.market_id}] Повторная попытка через {retry_delay} секунд...")
                            time.sleep(retry_delay)
                            continue
                        else:
                            # Для других ошибок не повторяем
                            log_error_to_file(
                                error_msg,
                                context=f"market_id={self.market_id}, outcome={outcome}, price={price}, shares={shares}"
                            )
                            return None
                    
                    response_data = response.json()
                    
                    if response_data.get("success"):
                        order_data = response_data.get("data", {})
                        order_id = order_data.get("id") or order_data.get("orderId")
                        self.log_func(f"[{self.market_id}] [{self.market_title}] ✓ Ордер {outcome.upper()} успешно выставлен: hash={order_hash}, id={order_id}")
                        
                        order_info = {
                            "order_id": str(order_id) if order_id else None,
                            "hash": order_hash,
                            "price": price,
                            "shares": shares,
                            "outcome": outcome
                        }
                        
                        with self.lock:
                            self.active_orders[outcome] = order_info
                            self.stats["placed"] += 1
                        
                        return order_info
                    else:
                        error_msg = response_data.get("message", "Неизвестная ошибка")
                        # Для ошибок в ответе не повторяем (это логические ошибки, а не сетевые)
                        self.log_func(f"[{self.market_id}] ✗ Ошибка при выставлении ордера: {error_msg}")
                        log_error_to_file(
                            f"Ошибка при выставлении ордера: {error_msg}",
                            context=f"market_id={self.market_id}, outcome={outcome}, price={price}, shares={shares}"
                        )
                        return None
                        
                except requests.exceptions.RequestException as e:
                    error_msg = f"Ошибка сети при выставлении ордера (попытка {attempt}/{max_attempts}): {e}"
                    self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                    
                    if attempt < max_attempts:
                        self.log_func(f"[{self.market_id}] Повторная попытка через 1 секунду...")
                        time.sleep(1)
                    else:
                        log_error_to_file(
                            f"Ошибка сети при выставлении ордера после {max_attempts} попыток",
                            exception=e,
                            context=f"market_id={self.market_id}, outcome={outcome}, price={price}, shares={shares}"
                        )
                        return None
            
        except Exception as e:
            self.log_func(f"[{self.market_id}] ✗ Ошибка выставления ордера {outcome.upper()}: {e}")
            import traceback
            self.log_func(traceback.format_exc())
            log_error_to_file(
                f"Ошибка выставления ордера {outcome.upper()}",
                exception=e,
                context=f"market_id={self.market_id}, outcome={outcome}, price={price}, shares={shares}"
            )
            return None
    
    def cancel_order(self, outcome: str) -> bool:
        """
        Отменяет активный ордер.
        
        Args:
            outcome: Исход ("yes" или "no")
        
        Returns:
            True если успешно, False при ошибке
        """
        try:
            with self.lock:
                order = self.active_orders.get(outcome)
                if not order:
                    self.log_func(f"[{self.market_id}] Нет активного ордера {outcome.upper()} для отмены")
                    return False
                
                order_id = order.get("order_id")
                if not order_id:
                    self.log_func(f"[{self.market_id}] ✗ У ордера {outcome.upper()} нет order_id для отмены")
                    return False
            
            self.log_func(f"[{self.market_id}] [{self.market_title}] Отмена ордера {outcome.upper()}: ID={order_id}")
            
            # Отменяем ордер через API с повторными попытками
            max_attempts = 3
            import time
            
            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.post(
                        f"{API_BASE_URL}/v1/orders/remove",
                        headers=self.headers,
                        json={
                            "data": {
                                "ids": [str(order_id)]
                            }
                        },
                        proxies=self.proxy,
                        timeout=10
                    )
                    
                    if not response.ok:
                        if response.status_code == 404:
                            # 404 не повторяем - ордер уже не существует
                            self.log_func(f"[{self.market_id}] ⚠️ Ордер {outcome.upper()} не найден (возможно уже отменен/исполнен)")
                            # Считаем успешным, так как ордер уже не существует
                            with self.lock:
                                self.active_orders[outcome] = None
                                self.stats["cancelled"] += 1
                            return True
                        else:
                            error_text = response.text[:500] if response.text else "Нет текста ошибки"
                            error_msg = f"Ошибка отмены ордера {outcome.upper()} (попытка {attempt}/{max_attempts}): {response.status_code} - {error_text}"
                            self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                            
                            # Проверяем на ошибку Invalid JWT (401)
                            is_invalid_jwt = False
                            try:
                                if response.status_code == 401:
                                    response_json = response.json()
                                    if response_json.get("message") == "Invalid JWT":
                                        is_invalid_jwt = True
                            except:
                                pass
                            
                            if is_invalid_jwt:
                                # Обновляем JWT и повторяем попытку
                                self.log_func(f"[{self.market_id}] Обнаружена ошибка Invalid JWT, обновляем токен...")
                                if self._refresh_jwt():
                                    self.log_func(f"[{self.market_id}] Повторная попытка отмены ордера с новым JWT...")
                                    continue
                                else:
                                    # Не удалось обновить JWT
                                    log_error_to_file(
                                        f"Не удалось обновить JWT токен при отмене ордера",
                                        context=f"market_id={self.market_id}, outcome={outcome}, order_id={order_id}"
                                    )
                                    return False
                            
                            # Определяем, нужно ли повторять попытку
                            should_retry = False
                            retry_delay = 1  # По умолчанию 1 секунда
                            
                            if response.status_code == 429:
                                # Rate limit exceeded - повторяем с увеличенной задержкой
                                should_retry = attempt < max_attempts
                                # Фиксированные задержки для 429: 30 секунд, затем 65 секунд
                                if attempt == 1:
                                    retry_delay = 30
                                elif attempt == 2:
                                    retry_delay = 65
                                else:
                                    retry_delay = 65  # На всякий случай
                            elif response.status_code in [502, 503, 504] or response.status_code >= 500:
                                # Серверные ошибки - повторяем
                                should_retry = attempt < max_attempts
                                retry_delay = 1
                            
                            if should_retry:
                                self.log_func(f"[{self.market_id}] Повторная попытка через {retry_delay} секунд...")
                                time.sleep(retry_delay)
                                continue
                            else:
                                # Для других ошибок не повторяем
                                if attempt == max_attempts:
                                    log_error_to_file(
                                        error_msg,
                                        context=f"market_id={self.market_id}, outcome={outcome}, order_id={order_id}"
                                    )
                                return False
                    
                    response_data = response.json()
                    
                    if response_data.get("success"):
                        with self.lock:
                            self.active_orders[outcome] = None
                            self.stats["cancelled"] += 1
                        
                        self.log_func(f"[{self.market_id}] [{self.market_title}] ✓ Ордер {outcome.upper()} отменен: ID={order_id}")
                        return True
                    else:
                        error_msg = f"Не удалось отменить ордер {outcome.upper()}: ID={order_id}"
                        # Для ошибок в ответе не повторяем (это логические ошибки, а не сетевые)
                        self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                        if attempt == max_attempts:
                            log_error_to_file(
                                error_msg,
                                context=f"market_id={self.market_id}, outcome={outcome}, order_id={order_id}"
                            )
                        return False
                        
                except requests.exceptions.RequestException as e:
                    error_msg = f"Ошибка сети при отмене ордера (попытка {attempt}/{max_attempts}): {e}"
                    self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                    
                    if attempt < max_attempts:
                        self.log_func(f"[{self.market_id}] Повторная попытка через 1 секунду...")
                        time.sleep(1)
                    else:
                        log_error_to_file(
                            f"Ошибка сети при отмене ордера после {max_attempts} попыток",
                            exception=e,
                            context=f"market_id={self.market_id}, outcome={outcome}, order_id={order_id}"
                        )
                        return False
                
        except Exception as e:
            self.log_func(f"[{self.market_id}] ✗ Ошибка отмены ордера {outcome.upper()}: {e}")
            log_error_to_file(
                f"Ошибка отмены ордера {outcome.upper()}",
                exception=e,
                context=f"market_id={self.market_id}, outcome={outcome}, order_id={order_id}"
            )
            import traceback
            self.log_func(traceback.format_exc())
            return False
    
    def _get_active_orders_from_api(self) -> List[Dict]:
        """
        Получает список активных ордеров через API для текущего рынка.
        Использует endpoint /v1/orders с фильтром по статусу OPEN и marketId.
        Делает до 3 попыток при таймауте.
        
        Returns:
            Список словарей с информацией об ордерах
        """
        max_attempts = 3
        import time
        
        for attempt in range(1, max_attempts + 1):
            try:
                # Пробуем получить ордера с фильтром по marketId (если поддерживается)
                # По документации: https://dev.predict.fun/get-orders-25326902e0
                params = {
                    "status": "OPEN",
                    "first": "100"  # Получаем до 100 ордеров
                }
                
                # Пробуем добавить marketId как параметр (может поддерживаться, даже если не в документации)
                try:
                    market_id_int = int(self.market_id)
                    params["marketId"] = str(market_id_int)
                except:
                    pass
                
                response = requests.get(
                    f"{API_BASE_URL}/v1/orders",
                    headers=self.headers,
                    params=params,
                    proxies=self.proxy,
                    timeout=10
                )
                
                if response.ok:
                    data = response.json()
                    if data.get("success") and "data" in data:
                        orders = data["data"]
                        # Фильтруем ордера по market_id (в ответе поле marketId - integer)
                        # На случай, если API не отфильтровал по marketId
                        market_orders = [
                            order for order in orders 
                            if order.get("marketId") == int(self.market_id) or 
                               str(order.get("marketId")) == str(self.market_id)
                        ]
                        return market_orders
                else:
                    error_text = response.text[:500] if response.text else "Нет текста ошибки"
                    self.log_func(f"[{self.market_id}] ✗ Ошибка получения ордеров через API (попытка {attempt}/{max_attempts}): {response.status_code} - {error_text}")
                    # Для ошибок API не повторяем (это не таймаут)
                    return []
                
            except requests.exceptions.ReadTimeout as e:
                if attempt < max_attempts:
                    self.log_func(f"[{self.market_id}] ⚠️ Таймаут при получении ордеров (попытка {attempt}/{max_attempts}), повтор через 30 секунд...")
                    time.sleep(30)
                    continue
                else:
                    error_msg = f"Ошибка получения активных ордеров через API после {max_attempts} попыток (таймаут)"
                    self.log_func(f"[{self.market_id}] ✗ {error_msg}: {e}")
                    log_error_to_file(
                        error_msg,
                        exception=e,
                        context=f"market_id={self.market_id}, attempts={max_attempts}"
                    )
                    return []
            except requests.exceptions.RequestException as e:
                if attempt < max_attempts:
                    self.log_func(f"[{self.market_id}] ⚠️ Ошибка сети при получении ордеров (попытка {attempt}/{max_attempts}), повтор через 30 секунд...")
                    time.sleep(30)
                    continue
                else:
                    error_msg = f"Ошибка получения активных ордеров через API после {max_attempts} попыток (сеть)"
                    self.log_func(f"[{self.market_id}] ✗ {error_msg}: {e}")
                    log_error_to_file(
                        error_msg,
                        exception=e,
                        context=f"market_id={self.market_id}, attempts={max_attempts}"
                    )
                    return []
            except Exception as e:
                error_msg = f"Ошибка получения активных ордеров через API"
                self.log_func(f"[{self.market_id}] ✗ {error_msg}: {e}")
                import traceback
                self.log_func(traceback.format_exc())
                log_error_to_file(
                    error_msg,
                    exception=e,
                    context=f"market_id={self.market_id}"
                )
                return []
        
        return []
    
    def _cancel_orders_by_ids(self, order_ids: List[str]) -> bool:
        """
        Отменяет ордера по списку ID.
        
        Args:
            order_ids: Список ID ордеров для отмены
            
        Returns:
            True если все ордера отменены успешно
        """
        if not order_ids:
            return True
        
        try:
            # Отменяем ордера через API
            response = requests.post(
                f"{API_BASE_URL}/v1/orders/remove",
                headers=self.headers,
                json={
                    "data": {
                        "ids": [str(order_id) for order_id in order_ids]
                    }
                },
                proxies=self.proxy,
                timeout=10
            )
            
            if response.ok:
                data = response.json()
                if data.get("success"):
                    self.log_func(f"[{self.market_id}] ✓ Отменено {len(order_ids)} ордеров по рынку")
                    # Обновляем внутренний список активных ордеров
                    with self.lock:
                        # Сбрасываем ордера, которые были отменены
                        for order_id in order_ids:
                            order_id_str = str(order_id)
                            if self.active_orders.get("yes") and str(self.active_orders["yes"].get("order_id")) == order_id_str:
                                self.active_orders["yes"] = None
                            if self.active_orders.get("no") and str(self.active_orders["no"].get("order_id")) == order_id_str:
                                self.active_orders["no"] = None
                        self.stats["cancelled"] += len(order_ids)
                    return True
                else:
                    error_msg = f"Не удалось отменить ордера: {data.get('message', 'Unknown error')}"
                    self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                    log_error_to_file(
                        error_msg,
                        context=f"market_id={self.market_id}, order_ids={order_ids}, response={data}"
                    )
                    return False
            else:
                error_text = response.text[:500] if response.text else "Нет текста ошибки"
                error_msg = f"Ошибка отмены ордеров: {response.status_code} - {error_text}"
                self.log_func(f"[{self.market_id}] ✗ {error_msg}")
                log_error_to_file(
                    f"Ошибка отмены ордеров через API: {response.status_code}",
                    context=f"market_id={self.market_id}, order_ids={order_ids}, error_text={error_text}"
                )
                return False
                
        except Exception as e:
            error_msg = f"Ошибка отмены ордеров по ID"
            self.log_func(f"[{self.market_id}] ✗ {error_msg}: {e}")
            import traceback
            self.log_func(traceback.format_exc())
            log_error_to_file(
                error_msg,
                exception=e,
                context=f"market_id={self.market_id}, order_ids={order_ids}"
            )
            return False
    
    def cancel_all_orders(self) -> bool:
        """
        Отменяет все активные ордера параллельно.
        
        Returns:
            True если все отменены успешно
        """
        results = []
        
        def cancel_yes():
            results.append(self.cancel_order("yes"))
        
        def cancel_no():
            results.append(self.cancel_order("no"))
        
        # Параллельная отмена ордеров
        thread_yes = threading.Thread(target=cancel_yes, daemon=True)
        thread_no = threading.Thread(target=cancel_no, daemon=True)
        
        thread_yes.start()
        thread_no.start()
        
        thread_yes.join()
        thread_no.join()
        
        return all(results)
    
    def place_orders_from_preliminary(
        self,
        order_info: Dict,
        mid_price_yes: float
    ) -> bool:
        """
        Выставляет ордера на основе предварительных расчетов.
        
        Args:
            order_info: Информация о предварительных ордерах
            mid_price_yes: Текущий mid-прайс Yes
        
        Returns:
            True если все ордера выставлены успешно
        """
        # Проверяем, не идет ли уже процесс выставления
        with self.lock:
            if self.placing_orders:
                # Уже идет процесс выставления - пропускаем
                return True
            self.placing_orders = True
        
        try:
            # Проверяем, изменился ли mid_price
            if self.last_mid_price_yes is not None and abs(self.last_mid_price_yes - mid_price_yes) > 0.0001:
                self.log_func(f"[{self.market_id}] Mid-прайс изменился: {self.last_mid_price_yes:.4f} -> {mid_price_yes:.4f}")
                self.log_func(f"[{self.market_id}] Отменяем старые ордера...")
                self.cancel_all_orders()
            elif self.last_mid_price_yes is not None:
                # Mid_price не изменился, проверяем есть ли уже активные ордера
                # НО не возвращаемся сразу - нужно проверить, какие именно ордера нужно выставить
                # (может быть один ордер отменен, а другой активен)
                pass
            
            self.last_mid_price_yes = mid_price_yes
            
            buy_yes = order_info.get("buy_yes", {})
            buy_no = order_info.get("buy_no", {})
            
            if not buy_yes or not buy_no:
                self.log_func(f"[{self.market_id}] ✗ Недостаточно данных для выставления ордеров")
                return False
            
            # Проверяем ликвидность перед выставлением
            can_place_yes = order_info.get("can_place_yes", True)  # По умолчанию разрешаем, если не указано
            can_place_no = order_info.get("can_place_no", True)
            liquidity_yes = order_info.get("liquidity_yes", 0)
            liquidity_no = order_info.get("liquidity_no", 0)
            min_liquidity = order_info.get("min_liquidity", 300.0)
            
            results = []
            
            def place_yes():
                # Проверяем активные ордера непосредственно перед выставлением
                with self.lock:
                    has_active_yes = self.active_orders.get("yes") is not None
                
                # Выставляем только если можно выставить и ордер еще не активен
                if can_place_yes and not has_active_yes:
                    price = buy_yes.get("price", 0)
                    shares = buy_yes.get("shares", 0)
                    result = self.place_order("yes", price, shares)
                    results.append(result is not None)
                elif has_active_yes:
                    # Ордер уже активен - не перевыставляем
                    results.append(True)
                else:
                    self.log_func(f"[{self.market_id}] ✗ Пропускаем выставление Yes: недостаточно ликвидности (${liquidity_yes:.2f} < ${min_liquidity:.2f})")
                    results.append(True)  # Не считаем это ошибкой
            
            def place_no():
                # Проверяем активные ордера непосредственно перед выставлением
                with self.lock:
                    has_active_no = self.active_orders.get("no") is not None
                
                # Выставляем только если можно выставить и ордер еще не активен
                if can_place_no and not has_active_no:
                    price = buy_no.get("price", 0)
                    shares = buy_no.get("shares", 0)
                    result = self.place_order("no", price, shares)
                    results.append(result is not None)
                elif has_active_no:
                    # Ордер уже активен - не перевыставляем
                    results.append(True)
                else:
                    self.log_func(f"[{self.market_id}] ✗ Пропускаем выставление No: недостаточно ликвидности (${liquidity_no:.2f} < ${min_liquidity:.2f})")
                    results.append(True)  # Не считаем это ошибкой
        
            # Параллельное выставление ордеров
            thread_yes = threading.Thread(target=place_yes, daemon=True)
            thread_no = threading.Thread(target=place_no, daemon=True)
            
            thread_yes.start()
            thread_no.start()
            
            thread_yes.join()
            thread_no.join()
            
            return all(results)
        finally:
            # Всегда сбрасываем флаг после завершения
            with self.lock:
                self.placing_orders = False
    
    def get_active_orders(self, timeout: float = None) -> Dict:
        """Возвращает информацию об активных ордерах"""
        if timeout is not None:
            # Используем таймаут для блокировки
            acquired = self.lock.acquire(timeout=timeout)
            if not acquired:
                raise TimeoutError("Не удалось получить блокировку для get_active_orders")
            try:
                return {
                    "yes": self.active_orders["yes"].copy() if self.active_orders["yes"] else None,
                    "no": self.active_orders["no"].copy() if self.active_orders["no"] else None
                }
            finally:
                self.lock.release()
        else:
            # Без таймаута (для обратной совместимости)
            with self.lock:
                return {
                    "yes": self.active_orders["yes"].copy() if self.active_orders["yes"] else None,
                    "no": self.active_orders["no"].copy() if self.active_orders["no"] else None
                }
    
    def get_stats(self, timeout: float = None) -> Dict:
        """Возвращает статистику ордеров"""
        if timeout is not None:
            # Используем таймаут для блокировки
            acquired = self.lock.acquire(timeout=timeout)
            if not acquired:
                raise TimeoutError("Не удалось получить блокировку для get_stats")
            try:
                return self.stats.copy()
            finally:
                self.lock.release()
        else:
            # Без таймаута (для обратной совместимости)
            with self.lock:
                return self.stats.copy()
