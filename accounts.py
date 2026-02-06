"""
Модуль для загрузки и управления аккаунтами
"""

from config import ACCOUNTS_FILE


def load_accounts_from_file(file_path: str = ACCOUNTS_FILE) -> list:
    """
    Загружает список аккаунтов из файла.
    
    Формат файла: api_key,predict_account_address,privy_wallet_private_key,proxy
    Каждая строка - один аккаунт
    Строки начинающиеся с # игнорируются
    Прокси в формате: user:pass@host:port
    
    Args:
        file_path: Путь к файлу с аккаунтами
        
    Returns:
        Список словарей с конфигурацией аккаунтов
    """
    accounts = []
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Пропускаем пустые строки и комментарии
                if not line or line.startswith("#"):
                    continue
                
                # Разделяем по запятой
                parts = [part.strip() for part in line.split(",")]
                
                if len(parts) < 3:
                    warning = f"⚠️  Пропущена строка {line_num}: неверный формат"
                    print(warning)
                    continue
                
                api_key = parts[0]
                predict_account_address = parts[1]
                privy_wallet_private_key = parts[2]
                proxy = parts[3] if len(parts) > 3 else None
                
                # Проверяем, что адрес начинается с 0x
                if not predict_account_address.startswith("0x"):
                    warning = f"⚠️  Пропущена строка {line_num}: адрес должен начинаться с 0x"
                    print(warning)
                    continue
                
                accounts.append({
                    "api_key": api_key,
                    "predict_account_address": predict_account_address,
                    "privy_wallet_private_key": privy_wallet_private_key,
                    "proxy": proxy,
                })
        
        return accounts
    
    except FileNotFoundError:
        error_msg = f"✗ Файл {file_path} не найден!"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return []
    except Exception as e:
        error_msg = f"✗ Ошибка при чтении файла {file_path}: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return []
