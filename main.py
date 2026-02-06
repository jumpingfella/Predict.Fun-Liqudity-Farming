"""
Главный файл для запуска софта предоставления ликвидности Predict Fun
"""

# Импортируем logger для добавления временных меток
import logger

from gui import main

if __name__ == "__main__":
    # Заменяем стандартный print на версию с timestamp
    import builtins
    builtins.print = logger.log_print
    
    main()
