"""
Конфигурация для софта предоставления ликвидности Predict Fun
"""

# Базовый URL API
API_BASE_URL = "https://api.predict.fun"

# Путь к файлу с аккаунтами
ACCOUNTS_FILE = "accounts.txt"

# Путь к файлу с настройками токенов
SETTINGS_FILE = "token_settings.json"

# Общие настройки по умолчанию
DEFAULT_SPREAD_PERCENT = 3.0  # Спред от mid-прайса в процентах
DEFAULT_POSITION_SIZE_USDT = 100.0  # Размер позиции в USDT по умолчанию
DEFAULT_POSITION_SIZE_SHARES = None  # Размер позиции в shares (если None, используется USDT)
DEFAULT_MIN_LIQUIDITY_USDT = 300.0  # Минимальная ликвидность перед нашим ордером в USDT
DEFAULT_MIN_SPREAD = 0.2  # Минимальный спред между mid price и ценой ордера (в центах, например 0.2 = 0.2 цента)
DEFAULT_ENABLED = True  # По умолчанию все токены активны

# --- Настройки Автоспреда ---
DEFAULT_AUTO_SPREAD_ENABLED = False
DEFAULT_TARGET_LIQUIDITY = 1000.0
DEFAULT_MAX_AUTO_SPREAD = 6.0


def format_proxy(proxy_string) -> dict:
    """
    Форматирует строку прокси в формат для requests.
    
    Args:
        proxy_string: Прокси в формате user:pass@host:port или уже отформатированный dict
        
    Returns:
        dict: Словарь прокси для requests или None
    """
    if not proxy_string:
        return None
    
    # Если уже словарь, возвращаем как есть
    if isinstance(proxy_string, dict):
        return proxy_string
    
    # Если строка, форматируем
    if isinstance(proxy_string, str):
        # Добавляем протокол если его нет
        if not proxy_string.startswith("http://"):
            proxy_string = f"http://{proxy_string}"
        
        return {
            "http": proxy_string,
            "https": proxy_string,
        }
    
    # Для других типов возвращаем None
    return None
