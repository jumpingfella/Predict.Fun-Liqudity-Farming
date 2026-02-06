"""
Модуль для управления настройками токенов
"""

import json
import os
from typing import Dict, Optional
from config import (
    SETTINGS_FILE, DEFAULT_SPREAD_PERCENT, DEFAULT_POSITION_SIZE_USDT, 
    DEFAULT_POSITION_SIZE_SHARES, DEFAULT_MIN_LIQUIDITY_USDT, DEFAULT_MIN_SPREAD, 
    DEFAULT_ENABLED, DEFAULT_AUTO_SPREAD_ENABLED, DEFAULT_TARGET_LIQUIDITY, 
    DEFAULT_MAX_AUTO_SPREAD
)


class TokenSettings:
    """Класс для хранения настроек токена"""
    
    def __init__(
        self,
        market_id: str,
        spread_percent: float = DEFAULT_SPREAD_PERCENT,
        position_size_usdt: Optional[float] = DEFAULT_POSITION_SIZE_USDT,
        position_size_shares: Optional[float] = DEFAULT_POSITION_SIZE_SHARES,
        min_liquidity_usdt: Optional[float] = DEFAULT_MIN_LIQUIDITY_USDT,
        min_spread: Optional[float] = DEFAULT_MIN_SPREAD,
        enabled: bool = DEFAULT_ENABLED,
        auto_spread_enabled: bool = DEFAULT_AUTO_SPREAD_ENABLED,
        target_liquidity: float = DEFAULT_TARGET_LIQUIDITY,
        max_auto_spread: float = DEFAULT_MAX_AUTO_SPREAD,
        is_custom: bool = False
    ):
        self.market_id = market_id
        self.spread_percent = spread_percent
        self.position_size_usdt = position_size_usdt
        self.position_size_shares = position_size_shares
        self.min_liquidity_usdt = min_liquidity_usdt
        self.min_spread = min_spread
        self.enabled = enabled
        self.auto_spread_enabled = auto_spread_enabled
        self.target_liquidity = target_liquidity
        self.max_auto_spread = max_auto_spread
        self.is_custom = is_custom  # Флаг, что настройки были изменены пользователем
    
    def to_dict(self) -> Dict:
        """Преобразует настройки в словарь"""
        return {
            "market_id": self.market_id,
            "spread_percent": self.spread_percent,
            "position_size_usdt": self.position_size_usdt,
            "position_size_shares": self.position_size_shares,
            "min_liquidity_usdt": self.min_liquidity_usdt,
            "min_spread": self.min_spread,
            "enabled": self.enabled,
            "auto_spread_enabled": self.auto_spread_enabled,
            "target_liquidity": self.target_liquidity,
            "max_auto_spread": self.max_auto_spread,
            "is_custom": self.is_custom,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TokenSettings':
        """Создает объект настроек из словаря"""
        return cls(
            market_id=data.get("market_id", ""),
            spread_percent=data.get("spread_percent", DEFAULT_SPREAD_PERCENT),
            position_size_usdt=data.get("position_size_usdt", DEFAULT_POSITION_SIZE_USDT),
            position_size_shares=data.get("position_size_shares", DEFAULT_POSITION_SIZE_SHARES),
            min_liquidity_usdt=data.get("min_liquidity_usdt", DEFAULT_MIN_LIQUIDITY_USDT),
            min_spread=data.get("min_spread", DEFAULT_MIN_SPREAD),
            enabled=data.get("enabled", DEFAULT_ENABLED),
            auto_spread_enabled=data.get("auto_spread_enabled", DEFAULT_AUTO_SPREAD_ENABLED),
            target_liquidity=data.get("target_liquidity", DEFAULT_TARGET_LIQUIDITY),
            max_auto_spread=data.get("max_auto_spread", DEFAULT_MAX_AUTO_SPREAD),
            is_custom=data.get("is_custom", False),
        )


class SettingsManager:
    """Менеджер для сохранения и загрузки настроек токенов"""
    
    def __init__(self, settings_file: str = SETTINGS_FILE):
        self.settings_file = settings_file
        self.settings: Dict[str, TokenSettings] = {}
        self.load_settings()
    
    def load_settings(self):
        """Загружает настройки из файла"""
        if not os.path.exists(self.settings_file):
            self.settings = {}
            return
        
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.settings = {
                    market_id: TokenSettings.from_dict(settings_data)
                    for market_id, settings_data in data.items()
                }
        except Exception as e:
            error_msg = f"⚠️  Ошибка загрузки настроек: {e}"
            print(error_msg)
            import traceback
            traceback.print_exc()
            self.settings = {}
    
    def save_settings(self):
        """Сохраняет настройки в файл"""
        try:
            data = {
                market_id: settings.to_dict()
                for market_id, settings in self.settings.items()
            }
            
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            error_msg = f"✗ Ошибка сохранения настроек: {e}"
            print(error_msg)
            import traceback
            traceback.print_exc()
    
    def get_settings(self, market_id: str, use_defaults_if_not_custom: bool = False) -> TokenSettings:
        """
        Получает настройки для токена. Если настроек нет, создает с дефолтными значениями.
        
        Args:
            market_id: ID рынка/токена
            use_defaults_if_not_custom: Если True и настройки не кастомные, возвращает дефолтные
            
        Returns:
            TokenSettings: Настройки токена
        """
        if market_id not in self.settings:
            self.settings[market_id] = TokenSettings(market_id=market_id)
        elif use_defaults_if_not_custom and not self.settings[market_id].is_custom:
            # Если настройки не кастомные, возвращаем дефолтные (но не сохраняем их)
            return TokenSettings(market_id=market_id)
        
        return self.settings[market_id]
    
    def update_settings(
        self,
        market_id: str,
        spread_percent: Optional[float] = None,
        position_size_usdt: Optional[float] = None,
        position_size_shares: Optional[float] = None,
        min_liquidity_usdt: Optional[float] = None,
        min_spread: Optional[float] = None,
        enabled: Optional[bool] = None,
        auto_spread_enabled: Optional[bool] = None,
        target_liquidity: Optional[float] = None,
        max_auto_spread: Optional[float] = None
    ):
        """
        Обновляет настройки для токена.
        
        Args:
            market_id: ID рынка/токена
            spread_percent: Спред в процентах
            position_size_usdt: Размер позиции в USDT (если указан, shares обнуляется)
            position_size_shares: Размер позиции в shares (если указан, usdt обнуляется)
            min_liquidity_usdt: Минимальная ликвидность перед нашим ордером в USDT
            min_spread: Минимальный спред между mid price и ценой ордера (в долларах)
            enabled: Включен ли токен
            auto_spread_enabled: Включен ли автоспред
            target_liquidity: Целевая ликвидность для автоспреда в USDT
            max_auto_spread: Максимальный спред от mid-price в центах
        """
        settings = self.get_settings(market_id)
        
        if spread_percent is not None:
            settings.spread_percent = spread_percent
            settings.is_custom = True
        
        # Если указан usdt, обнуляем shares
        if position_size_usdt is not None:
            settings.position_size_usdt = position_size_usdt
            settings.position_size_shares = None  # Явно обнуляем shares
            settings.is_custom = True
        
        # Если указан shares, обнуляем usdt
        if position_size_shares is not None:
            settings.position_size_shares = position_size_shares
            settings.position_size_usdt = None  # Явно обнуляем usdt
            settings.is_custom = True
        
        if min_liquidity_usdt is not None:
            settings.min_liquidity_usdt = min_liquidity_usdt
            settings.is_custom = True
        
        if min_spread is not None:
            settings.min_spread = min_spread
            settings.is_custom = True
        
        if enabled is not None:
            settings.enabled = enabled
            settings.is_custom = True

        if auto_spread_enabled is not None:
            settings.auto_spread_enabled = auto_spread_enabled
            settings.is_custom = True
            
        if target_liquidity is not None:
            settings.target_liquidity = target_liquidity
            settings.is_custom = True
            
        if max_auto_spread is not None:
            settings.max_auto_spread = max_auto_spread
            settings.is_custom = True
        
        self.save_settings()
    
    def reset_to_defaults(self, market_id: str):
        """
        Сбрасывает настройки токена к значениям по умолчанию.
        
        Args:
            market_id: ID рынка/токена
        """
        if market_id in self.settings:
            del self.settings[market_id]
            self.save_settings()
