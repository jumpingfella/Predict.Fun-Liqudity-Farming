"""
Модуль для логирования с временными метками
"""

import datetime
import sys
import builtins
import os
import traceback
from pathlib import Path

# Сохраняем оригинальный print ДО любых замен
_original_print = builtins.print

# Путь к файлу с ошибками
ERROR_LOG_FILE = "errors.log"


def get_timestamp() -> str:
    """Получает текущую временную метку в формате HH:MM:SS.mmm"""
    now = datetime.datetime.now()
    return now.strftime("%H:%M:%S.%f")[:-3]  # Миллисекунды


def log_print(*args, **kwargs):
    """Обертка над print с временной меткой"""
    timestamp = get_timestamp()
    # Добавляем временную метку к сообщению
    if args:
        # Если первый аргумент - строка, добавляем к ней timestamp
        if isinstance(args[0], str):
            message = f"[{timestamp}] {args[0]}"
            new_args = (message,) + args[1:]
        else:
            # Если не строка, добавляем timestamp как отдельный аргумент
            new_args = (f"[{timestamp}]",) + args
    else:
        new_args = (f"[{timestamp}]",)
    
    # Используем СОХРАНЕННЫЙ оригинальный print (не builtins.print, который может быть переопределен)
    _original_print(*new_args, **kwargs)


def log_error_to_file(error_message: str, exception: Exception = None, context: str = ""):
    """
    Записывает ошибку в файл errors.log
    
    Args:
        error_message: Текст сообщения об ошибке
        exception: Объект исключения (опционально)
        context: Дополнительный контекст (например, market_id, функция и т.д.)
    """
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Формируем запись
        log_entry = f"\n{'='*80}\n"
        log_entry += f"[{timestamp}]\n"
        
        if context:
            log_entry += f"Контекст: {context}\n"
        
        log_entry += f"Ошибка: {error_message}\n"
        
        if exception:
            log_entry += f"Тип исключения: {type(exception).__name__}\n"
            log_entry += f"Сообщение: {str(exception)}\n"
            log_entry += f"\nTraceback:\n"
            log_entry += "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        
        log_entry += f"{'='*80}\n"
        
        # Записываем в файл (добавляем в конец)
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
            
    except Exception as e:
        # Если не удалось записать в файл, выводим в консоль
        _original_print(f"[ERROR] Не удалось записать ошибку в файл: {e}")
