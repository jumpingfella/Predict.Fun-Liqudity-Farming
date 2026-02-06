"""
Модуль для работы с Predict Fun API
"""

import requests
from typing import List, Dict, Optional
from config import API_BASE_URL, format_proxy
from auth import get_auth_headers
from logger import log_error_to_file


class PredictAPIClient:
    """Клиент для работы с Predict Fun API"""
    
    def __init__(self, api_key: str, jwt_token: str, proxy: Optional[str] = None):
        """
        Инициализация клиента API.
        
        Args:
            api_key: API ключ
            jwt_token: JWT токен для аутентификации
            proxy: Прокси в формате user:pass@host:port
        """
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.proxy = format_proxy(proxy)
        self.headers = get_auth_headers(jwt_token, api_key)
    
    def get_positions(self) -> List[Dict]:
        """
        Получает список позиций пользователя с поддержкой пагинации.
        Собирает все позиции, используя курсор для получения следующих страниц.
        
        Returns:
            Список словарей с информацией о позициях
        """
        all_positions = []
        cursor = None
        first = "100"  # Запрашиваем до 100 позиций за раз
        
        try:
            while True:
                params = {
                    "first": first
                }
                
                # Добавляем курсор для пагинации, если он есть
                if cursor:
                    params["after"] = cursor
                
                response = requests.get(
                    f"{API_BASE_URL}/v1/positions",
                    headers=self.headers,
                    proxies=self.proxy,
                    params=params,
                    timeout=30
                )
                
                if not response.ok:
                    response.raise_for_status()
                
                data = response.json()
                
                if data.get("success") and "data" in data:
                    positions = data["data"]
                    all_positions.extend(positions)
                    
                    # Проверяем, есть ли следующая страница
                    cursor = data.get("cursor")
                    if not cursor or len(positions) == 0:
                        # Нет курсора или пустой ответ - значит это последняя страница
                        break
                else:
                    # Если нет success или data, прекращаем
                    break
            
            return all_positions
            
        except Exception as e:
            error_msg = f"Ошибка получения позиций: {e}"
            print(f"✗ {error_msg}")
            import traceback
            traceback.print_exc()
            log_error_to_file(
                error_msg,
                exception=e,
                context="API get_positions"
            )
            # Возвращаем то, что успели собрать
            return all_positions
    
    def get_market_info(self, market_id: str, log_func=None) -> Optional[Dict]:
        """
        Получает информацию о рынке.
        
        Args:
            market_id: ID рынка
            log_func: Функция для логирования (опционально)
            
        Returns:
            Словарь с информацией о рынке или None
        """
        if log_func is None:
            log_func = print
        
        url = f"{API_BASE_URL}/v1/markets/{market_id}"
        max_attempts = 3
        
        for attempt in range(1, max_attempts + 1):
            try:
                log_func(f"[DEBUG] Запрос информации о рынке (попытка {attempt}/{max_attempts}): {url}")
                response = requests.get(
                    url,
                    headers=self.headers,
                    proxies=self.proxy,
                    timeout=10
                )
                log_func(f"[DEBUG] Статус ответа для рынка {market_id}: {response.status_code}")
                
                if not response.ok:
                    error_text = response.text[:500] if response.text else "Нет текста ошибки"
                    log_func(f"[DEBUG] Ошибка получения информации о рынке {market_id}: {response.status_code} - {error_text}")
                    response.raise_for_status()
                
                data = response.json()
                if data.get("success") and "data" in data:
                    market_data = data["data"]
                    log_func(f"[DEBUG] Информация о рынке {market_id} получена.")
                    log_func(f"[DEBUG] Поля в ответе: {list(market_data.keys())}")
                    if "slug" in market_data:
                        log_func(f"[DEBUG] Slug для рынка {market_id}: {market_data['slug']}")
                    return market_data
                log_func(f"[DEBUG] Неожиданный формат ответа для рынка {market_id}: {data}")
                return None
                
            except requests.exceptions.RequestException as e:
                log_func(f"[DEBUG] Ошибка сети при получении информации о рынке {market_id} (попытка {attempt}/{max_attempts}): {e}")
                if attempt < max_attempts:
                    log_func(f"[DEBUG] Повторная попытка через 1 секунду...")
                    import time
                    time.sleep(1)
                else:
                    log_func(f"[DEBUG] Рынок {market_id}: не удалось получить информацию через API после {max_attempts} попыток")
                    log_error_to_file(
                        f"Не удалось получить информацию о рынке {market_id} после {max_attempts} попыток",
                        context=f"market_id={market_id}"
                    )
                    return None
                    
            except Exception as e:
                error_msg = f"Ошибка получения информации о рынке {market_id}: {e}"
                log_func(f"✗ {error_msg}")
                import traceback
                log_func(traceback.format_exc())
                log_error_to_file(
                    error_msg,
                    exception=e,
                    context=f"market_id={market_id}"
                )
                return None
        
        return None
    
    def get_orderbook(self, market_id: str, use_public: bool = True, log_func=None) -> Optional[Dict]:
        """
        Получает стакан заявок для рынка.
        По умолчанию использует публичный endpoint без аутентификации.
        Стакан НЕ требует аутентификации и должен запрашиваться БЕЗ заголовков.
        
        Args:
            market_id: ID рынка
            use_public: Использовать публичный endpoint без аутентификации (всегда True для стакана)
            log_func: Функция для логирования (опционально)
            
        Returns:
            Словарь с информацией о стакане или None
        """
        if log_func is None:
            log_func = print
        
        try:
            # Стакан может требовать API ключ в query параметре, но не в заголовках
            # Пробуем сначала с API ключом в query параметре
            url = f"{API_BASE_URL}/v1/markets/{market_id}/orderbook"
            
            # Пробуем с API ключом в query параметре (если он есть)
            if self.api_key:
                url_with_key = f"{url}?apiKey={self.api_key}"
                log_func(f"[DEBUG] Запрос стакана с API ключом в query: {url_with_key}")
            else:
                url_with_key = url
                log_func(f"[DEBUG] Запрос стакана без API ключа: {url}")
            
            # Создаем новый сессию без заголовков аутентификации
            session = requests.Session()
            # Убираем все заголовки, которые могли быть установлены ранее
            session.headers.clear()
            # Устанавливаем только базовые заголовки
            session.headers.update({
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json'
            })
            
            # Пробуем сначала с API ключом в query
            response = session.get(
                url_with_key,
                proxies=self.proxy,
                timeout=10
            )
            
            # Если не получилось с ключом, пробуем без него
            if not response.ok and self.api_key:
                log_func(f"[DEBUG] Попытка без API ключа в query...")
                response = session.get(
                    url,
                    proxies=self.proxy,
                    timeout=10
                )
            
            log_func(f"[DEBUG] Статус ответа для рынка {market_id}: {response.status_code}")
            
            if not response.ok:
                error_text = response.text[:500] if response.text else "Нет текста ошибки"
                log_func(f"[DEBUG] Ошибка получения стакана для рынка {market_id}: {response.status_code} - {error_text}")
                return None
            
            try:
                data = response.json()
            except Exception as e:
                log_func(f"[DEBUG] Ошибка парсинга JSON для рынка {market_id}: {e}")
                log_func(f"[DEBUG] Ответ сервера: {response.text[:500]}")
                return None
            
            if data.get("success") and "data" in data:
                orderbook_data = data["data"]
                bids_count = len(orderbook_data.get("bids", []))
                asks_count = len(orderbook_data.get("asks", []))
                log_func(f"[DEBUG] Стакан получен для рынка {market_id}: {bids_count} bids, {asks_count} asks")
                return orderbook_data
            
            log_func(f"[DEBUG] Неожиданный формат ответа для рынка {market_id}: {data}")
            return None
            
        except requests.exceptions.RequestException as e:
            log_func(f"[DEBUG] Ошибка сети при получении стакана для рынка {market_id}: {e}")
            if not use_public:
                # Пробуем без аутентификации
                return self.get_orderbook(market_id, use_public=True, log_func=log_func)
            return None
        except Exception as e:
            log_func(f"[DEBUG] Неожиданная ошибка при получении стакана для рынка {market_id}: {e}")
            if not use_public:
                # Пробуем без аутентификации
                return self.get_orderbook(market_id, use_public=True, log_func=log_func)
            return None
    
    def calculate_mid_price(self, orderbook: Dict) -> Optional[float]:
        """
        Рассчитывает mid-прайс из стакана заявок.
        
        Args:
            orderbook: Данные стакана заявок
            
        Returns:
            Mid-прайс или None
        """
        try:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            
            if not bids or not asks:
                return None
            
            # Лучшая цена покупки (самая высокая bid)
            best_bid = bids[0][0] if bids else None
            
            # Лучшая цена продажи (самая низкая ask)
            best_ask = asks[0][0] if asks else None
            
            if best_bid is None or best_ask is None:
                return None
            
            # Mid-прайс = (best_bid + best_ask) / 2
            mid_price = (best_bid + best_ask) / 2
            
            return mid_price
            
        except Exception as e:
            error_msg = f"Ошибка расчета mid-прайса: {e}"
            print(f"✗ {error_msg}")
            log_error_to_file(
                error_msg,
                exception=e,
                context="calculate_mid_price"
            )
            return None
    
    def get_user_info(self) -> Optional[Dict]:
        """
        Получает информацию о пользователе.
        
        Returns:
            Словарь с информацией о пользователе или None
        """
        try:
            # Пробуем несколько возможных endpoints
            endpoints = [
                "/v1/user",
                "/v1/account",
                "/v1/profile",
                "/v1/me"
            ]
            
            for endpoint in endpoints:
                try:
                    response = requests.get(
                        f"{API_BASE_URL}{endpoint}",
                        headers=self.headers,
                        proxies=self.proxy,
                        timeout=5
                    )
                    
                    if response.ok:
                        data = response.json()
                        if data.get("success") and "data" in data:
                            return data["data"]
                except:
                    continue
            
            return None
            
        except Exception as e:
            error_msg = f"Ошибка получения информации о пользователе: {e}"
            print(f"✗ {error_msg}")
            log_error_to_file(
                error_msg,
                exception=e,
                context="get_user_info"
            )
            return None
    
    def get_usdt_balance(
        self,
        predict_account_address: str,
        privy_wallet_private_key: str
    ) -> Optional[float]:
        """
        Получает баланс USDT через OrderBuilder.
        
        Args:
            predict_account_address: Адрес Predict Account
            privy_wallet_private_key: Приватный ключ Privy Wallet
            
        Returns:
            Баланс USDT в долларах или None
        """
        from predict_sdk import OrderBuilder, ChainId, OrderBuilderOptions
        import time
        
        max_attempts = 3
        
        # Убираем 0x если есть
        privy_key = privy_wallet_private_key
        if privy_key.startswith("0x"):
            privy_key = privy_key[2:]
        
        for attempt in range(1, max_attempts + 1):
            try:
                # Создаем OrderBuilder
                builder = OrderBuilder.make(
                    ChainId.BNB_MAINNET,
                    privy_key,
                    OrderBuilderOptions(predict_account=predict_account_address),
                )
                
                # Получаем баланс в wei
                balance_wei = builder.balance_of()
                
                # Конвертируем из wei в доллары
                # USDT на BNB Chain имеет 18 decimals (1 USDT = 10^18 wei)
                balance_usdt = float(balance_wei) / (10**18)
                
                return balance_usdt
                
            except Exception as e:
                error_msg = f"Ошибка получения баланса USDT (попытка {attempt}/{max_attempts}): {e}"
                print(f"✗ {error_msg}")
                
                if attempt < max_attempts:
                    print(f"[DEBUG] Повторная попытка через 1 секунду...")
                    time.sleep(1)
                else:
                    # Логируем только после всех попыток
                    import traceback
                    traceback.print_exc()
                    log_error_to_file(
                        f"Ошибка получения баланса USDT после {max_attempts} попыток",
                        exception=e,
                        context=f"get_usdt_balance, predict_account={predict_account_address[:20]}..."
                    )
                    return None
        
        return None