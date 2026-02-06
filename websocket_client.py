"""
WebSocket клиент для мониторинга цен через Predict Fun WebSocket API
"""

import websocket
import json
import threading
import time
from typing import Dict, Callable, Optional
from config import API_BASE_URL
from logger import log_error_to_file


class PredictWebSocketClient:
    """WebSocket клиент для Predict Fun"""
    
    def __init__(self, api_key: Optional[str] = None, on_orderbook_update: Optional[Callable] = None, on_connection_change: Optional[Callable] = None):
        """
        Инициализация WebSocket клиента.
        
        Args:
            api_key: API ключ (опционально)
            on_orderbook_update: Callback функция для обновления стакана (market_id, orderbook_data)
            on_connection_change: Callback функция для изменения статуса подключения (connected: bool)
        """
        self.api_key = api_key
        self.on_orderbook_update = on_orderbook_update
        self.on_connection_change = on_connection_change
        self.ws = None
        self.connected = False
        self.subscriptions = {}  # market_id -> request_id
        self.request_id_counter = 0
        self.last_heartbeat_timestamp = None
        self.thread = None
        self.running = False
        
        # URL WebSocket
        ws_url = "wss://ws.predict.fun/ws"
        if api_key:
            ws_url += f"?apiKey={api_key}"
        
        self.ws_url = ws_url
    
    def _get_next_request_id(self) -> int:
        """Получает следующий ID запроса"""
        self.request_id_counter += 1
        return self.request_id_counter
    
    def _on_message(self, ws, message):
        """Обработчик входящих сообщений"""
        try:
            data = json.loads(message)
            
            # Логируем тип сообщения
            msg_type = data.get("type", "UNKNOWN")
            
            # Обработка heartbeat
            if data.get("type") == "M" and data.get("topic") == "heartbeat":
                timestamp = data.get("data")
                self.last_heartbeat_timestamp = timestamp
                # Отправляем ответ на heartbeat
                self._send_heartbeat(timestamp)
                return
            
            # Обработка ответов на подписки
            if data.get("type") == "R":
                request_id = data.get("requestId")
                success = data.get("success", False)
                if not success:
                    error = data.get("error", {})
                    error_msg = f"✗ Ошибка подписки (requestId={request_id}): {error.get('message', 'Unknown error')}"
                    print(error_msg)
                    print(f"[WebSocket] Полный ответ об ошибке: {data}")
                else:
                    success_msg = f"✓ Подписка успешна (requestId={request_id})"
                    print(success_msg)
                return
            
            # Обработка обновлений стакана
            if data.get("type") == "M":
                topic = data.get("topic", "")
                
                if topic.startswith("predictOrderbook/"):
                    market_id = topic.split("/")[1]
                    orderbook_data = data.get("data", {})
                    
                    # Проверяем, что данные стакана валидны
                    if orderbook_data and (orderbook_data.get("bids") or orderbook_data.get("asks")):
                        if self.on_orderbook_update:
                            try:
                                self.on_orderbook_update(market_id, orderbook_data)
                            except Exception as e:
                                error_msg = f"Ошибка в callback on_orderbook_update: {e}"
                                print(f"[WebSocket] ✗ {error_msg}")
                                import traceback
                                traceback.print_exc()
                                log_error_to_file(
                                    error_msg,
                                    exception=e,
                                    context=f"WebSocket callback, market_id={market_id}"
                                )
        
        except json.JSONDecodeError as e:
            error_msg = f"Ошибка парсинга JSON WebSocket сообщения: {e}"
            print(f"✗ {error_msg}")
            print(f"[WebSocket] Сырое сообщение: {message[:500]}")
            log_error_to_file(
                error_msg,
                exception=e,
                context=f"WebSocket JSON parsing, message_length={len(message)}"
            )
        except Exception as e:
            error_msg = f"Ошибка обработки WebSocket сообщения: {e}"
            print(f"✗ {error_msg}")
            import traceback
            traceback.print_exc()
            log_error_to_file(
                error_msg,
                exception=e,
                context="WebSocket message processing"
            )
    
    def _on_error(self, ws, error):
        """Обработчик ошибок"""
        error_msg = f"WebSocket ошибка: {error}"
        print(f"✗ {error_msg}")
        import traceback
        traceback.print_exc()
        log_error_to_file(
            error_msg,
            exception=error if isinstance(error, Exception) else None,
            context="WebSocket connection error"
        )
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Обработчик закрытия соединения"""
        self.connected = False
        close_msg_text = f"WebSocket соединение закрыто (код: {close_status_code}, сообщение: {close_msg})"
        print(close_msg_text)
        
        # Уведомляем о изменении статуса подключения
        if self.on_connection_change:
            try:
                self.on_connection_change(False)
            except Exception:
                pass
        
        # Переподключение
        if self.running:
            print("Попытка переподключения через 5 секунд...")
            time.sleep(5)
            self.connect()
    
    def _on_open(self, ws):
        """Обработчик открытия соединения"""
        self.connected = True
        success_msg = "✓ WebSocket соединение установлено"
        print(success_msg)
        
        # Уведомляем о изменении статуса подключения
        if self.on_connection_change:
            try:
                self.on_connection_change(True)
            except Exception:
                pass
        
        # Небольшая задержка перед подпиской, чтобы соединение полностью установилось
        import time
        time.sleep(0.5)
        
        # Переподписываемся на все рынки
        markets_count = len(self.subscriptions)
        if markets_count > 0:
            print(f"[WebSocket] Переподписка на {markets_count} рынков...")
        for market_id in list(self.subscriptions.keys()):
            self.subscribe_orderbook(market_id)
    
    def _send_heartbeat(self, timestamp):
        """Отправляет heartbeat ответ"""
        if self.ws and self.connected:
            message = {
                "method": "heartbeat",
                "data": timestamp
            }
            try:
                self.ws.send(json.dumps(message))
            except Exception as e:
                error_msg = f"✗ Ошибка отправки heartbeat: {e}"
                print(error_msg)
                import traceback
                traceback.print_exc()
    
    def connect(self):
        """Подключается к WebSocket"""
        if self.running:
            return
        
        self.running = True
        
        def run_ws():
            while self.running:
                try:
                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                        on_open=self._on_open
                    )
                    self.ws.run_forever()
                except Exception as e:
                    error_msg = f"✗ Ошибка WebSocket: {e}"
                    print(error_msg)
                    import traceback
                    traceback.print_exc()
                    if self.running:
                        print("Попытка переподключения через 5 секунд...")
                        time.sleep(5)
        
        self.thread = threading.Thread(target=run_ws, daemon=True)
        self.thread.start()
    
    def subscribe_orderbook(self, market_id: str):
        """
        Подписывается на обновления стакана для рынка.
        
        Args:
            market_id: ID рынка
        """
        # Сохраняем подписку в любом случае
        if market_id not in self.subscriptions:
            self.subscriptions[market_id] = None
        
        if not self.connected:
            # Сохраняем подписку для переподключения
            print(f"[WebSocket] Соединение еще не установлено, подписка на рынок {market_id} будет отправлена после подключения")
            return
        
        if not self.ws:
            print(f"[WebSocket] WebSocket объект не создан, подписка на рынок {market_id} отложена")
            return
        
        request_id = self._get_next_request_id()
        self.subscriptions[market_id] = request_id
        
        message = {
            "method": "subscribe",
            "requestId": request_id,
            "params": [f"predictOrderbook/{market_id}"]
        }
        
        try:
            message_str = json.dumps(message)
            print(f"[WebSocket] Отправка подписки на рынок {market_id}: {message_str}")
            self.ws.send(message_str)
            print(f"[WebSocket] ✓ Подписка на стакан для рынка {market_id} отправлена (requestId={request_id})")
        except Exception as e:
            error_msg = f"✗ Ошибка подписки на стакан для рынка {market_id}: {e}"
            print(error_msg)
            import traceback
            traceback.print_exc()
    
    def unsubscribe_orderbook(self, market_id: str):
        """
        Отписывается от обновлений стакана для рынка.
        
        Args:
            market_id: ID рынка
        """
        if market_id not in self.subscriptions:
            return
        
        request_id = self.subscriptions[market_id]
        del self.subscriptions[market_id]
        
        if not self.connected:
            return
        
        message = {
            "method": "unsubscribe",
            "requestId": self._get_next_request_id(),
            "params": [f"predictOrderbook/{market_id}"]
        }
        
        try:
            if self.ws:
                self.ws.send(json.dumps(message))
        except Exception as e:
            print(f"✗ Ошибка отписки от стакана для рынка {market_id}: {e}")
    
    def disconnect(self):
        """Отключается от WebSocket"""
        self.running = False
        if self.ws:
            self.ws.close()
        self.connected = False
