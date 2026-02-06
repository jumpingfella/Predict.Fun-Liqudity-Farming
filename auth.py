"""
Модуль для аутентификации в Predict Fun API
"""

import requests
import traceback
from predict_sdk import OrderBuilder, ChainId, OrderBuilderOptions
from config import API_BASE_URL, format_proxy


def get_auth_jwt(
    api_key: str,
    predict_account_address: str,
    privy_wallet_private_key: str,
    proxy: str = None,
    log_func=print
) -> str:
    """
    Получает JWT токен для аутентификации с Predict API.
    
    Args:
        api_key: API ключ для этого аккаунта
        predict_account_address: Адрес Predict Account (deposit address)
        privy_wallet_private_key: Приватный ключ Privy Wallet
        proxy: Прокси в формате user:pass@host:port
        log_func: Функция для логирования
        
    Returns:
        str: JWT токен для использования в последующих запросах
    """
    try:
        proxies = format_proxy(proxy)
        
        # Убираем 0x если есть
        if privy_wallet_private_key.startswith("0x"):
            privy_wallet_private_key = privy_wallet_private_key[2:]
        
        # Создаем OrderBuilder
        builder = OrderBuilder.make(
            ChainId.BNB_MAINNET,
            privy_wallet_private_key,
            OrderBuilderOptions(predict_account=predict_account_address),
        )
        
        # Получаем сообщение для подписи
        message_response = requests.get(
            f"{API_BASE_URL}/v1/auth/message",
            headers={"x-api-key": api_key},
            proxies=proxies,
        )
        
        if not message_response.ok:
            error_msg = f"✗ Ошибка получения сообщения: {message_response.status_code}"
            log_func(error_msg)
            print(error_msg)  # Дублируем в консоль
            error_text = f"Ответ: {message_response.text}"
            log_func(error_text)
            print(error_text)  # Дублируем в консоль
            message_response.raise_for_status()
        
        message_data = message_response.json()
        message = message_data["data"]["message"]
        
        # Подписываем сообщение
        signature = builder.sign_predict_account_message(message)
        
        # Формируем тело запроса для получения JWT
        body = {
            "signer": predict_account_address,
            "message": message,
            "signature": signature,
        }
        
        # Отправляем запрос для получения JWT
        jwt_response = requests.post(
            f"{API_BASE_URL}/v1/auth",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
            },
            json=body,
            proxies=proxies,
        )
        
        if not jwt_response.ok:
            error_msg = f"✗ Ошибка получения JWT: {jwt_response.status_code}"
            log_func(error_msg)
            print(error_msg)  # Дублируем в консоль
            error_text = f"Ответ: {jwt_response.text}"
            log_func(error_text)
            print(error_text)  # Дублируем в консоль
            jwt_response.raise_for_status()
        
        jwt_data = jwt_response.json()
        jwt = jwt_data["data"]["token"]
        
        success_msg = f"✓ Аутентификация успешна для {predict_account_address[:10]}..."
        log_func(success_msg)
        print(success_msg)  # Дублируем в консоль
        
        return jwt
        
    except Exception as e:
        from logger import log_error_to_file
        
        error_header = "\n✗ ОШИБКА АУТЕНТИФИКАЦИИ:"
        log_func(error_header)
        print(error_header)  # Дублируем в консоль
        
        error_type = f"Тип ошибки: {type(e).__name__}"
        log_func(error_type)
        print(error_type)  # Дублируем в консоль
        
        error_message = f"Сообщение: {str(e)}"
        log_func(error_message)
        print(error_message)  # Дублируем в консоль
        
        traceback.print_exc()
        
        # Записываем в файл
        log_error_to_file(
            "Ошибка аутентификации",
            exception=e,
            context=f"predict_account={predict_account_address[:20]}..."
        )
        
        raise


def get_auth_headers(jwt_token: str, api_key: str) -> dict:
    """
    Формирует заголовки для аутентифицированных запросов.
    
    Args:
        jwt_token: JWT токен для авторизации
        api_key: API ключ для этого аккаунта
        
    Returns:
        dict: Заголовки для запросов
    """
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {jwt_token}",
    }
