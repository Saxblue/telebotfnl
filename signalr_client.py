import asyncio
import json
import logging
import websockets
import urllib.parse
from datetime import datetime
from typing import Callable, Optional, Dict, Any
import threading
import time

logger = logging.getLogger(__name__)

class BetConstructSignalRClient:
    def __init__(self, 
                 hub_access_token: str,
                 connection_token: str,
                 groups_token: str,
                 on_notification_callback: Optional[Callable] = None):
        """
        BetConstruct SignalR Client
        
        Args:
            hub_access_token: Hub erişim token'ı
            connection_token: Bağlantı token'ı
            groups_token: Grup token'ı
            on_notification_callback: Bildirim geldiğinde çağrılacak fonksiyon
        """
        self.hub_access_token = hub_access_token
        self.connection_token = connection_token
        self.groups_token = groups_token
        self.on_notification_callback = on_notification_callback
        
        self.websocket = None
        self.is_connected = False
        self.is_running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5  # saniye
        
        # SignalR protokol bilgileri
        self.client_protocol = "2.1"
        self.message_id = "d-41D89228-B,0|ZwZ,9|Zwa,7|Cax,0|C7P,0|C7Q,0"
        self.connection_data = '[{"name":"commonnotificationhub"}]'
        self.tid = 7
        
        # WebSocket URL'i oluştur
        self.base_url = "wss://backofficewebadmin.betconstruct.com/signalr/connect"
        self.websocket_url = self._build_websocket_url()
        
        # Bağlantı durumu
        self.connected = False
        
    def _build_websocket_url(self) -> str:
        """WebSocket URL'ini oluştur"""
        params = {
            'transport': 'webSockets',
            'groupsToken': self.groups_token,
            'messageId': self.message_id,
            'clientProtocol': self.client_protocol,
            'hubAccessToken': self.hub_access_token,
            'connectionToken': self.connection_token,
            'connectionData': self.connection_data,
            'tid': str(self.tid)
        }
        
        query_string = urllib.parse.urlencode(params)
        return f"{self.base_url}?{query_string}"
    
    async def connect(self):
        """SignalR hub'ına bağlan"""
        try:
            logger.info("SignalR hub'ına bağlanılıyor...")
            
            # WebSocket headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Origin': 'https://backoffice.betconstruct.com',
                'Referer': 'https://backoffice.betconstruct.com/',
                'Authorization': f'Bearer {self.hub_access_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            
            self.websocket = await websockets.connect(
                self.websocket_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10
            )
            
            self.is_connected = True
            self.reconnect_attempts = 0
            logger.info("✅ SignalR hub'ına başarıyla bağlandı!")
            
            # Bağlantı mesajı gönder
            await self._send_connection_message()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ SignalR bağlantı hatası: {e}")
            self.is_connected = False
            return False
    
    async def _send_connection_message(self):
        """Bağlantı kurulduktan sonra gerekli mesajları gönder"""
        try:
            # SignalR el sıkışma mesajı
            handshake = {
                "protocol": "json",
                "version": 1
            }
            
            await self.websocket.send(json.dumps(handshake))
            logger.info("SignalR el sıkışma mesajı gönderildi")
            
            # İlk mesajı bekle (bağlantı onayı)
            response = await self.websocket.recv()
            logger.info(f"SignalR bağlantı yanıtı: {response}")
            
            # Hub'a abone ol
            subscribe_message = {
                "H": "commonnotificationhub",
                "M": "Subscribe",
                "A": [],
                "I": 1
            }
            
            await self.websocket.send(json.dumps(subscribe_message))
            logger.info("Hub'a abone olma isteği gönderildi")
            
            # Bağlantı başarılı
            self.connected = True
            logger.info("✅ SignalR hub'ına başarıyla bağlanıldı")
            
        except Exception as e:
            logger.error(f"Bağlantı mesajı gönderme hatası: {e}")
    
    async def listen(self):
        """Gelen mesajları dinle"""
        try:
            while self.is_connected and self.websocket and self.connected:
                try:
                    # Heartbeat gönder
                    if self.connected:
                        heartbeat = {"C": "d-00000000-0000-0000-0000-000000000001"}
                        await self.websocket.send(json.dumps(heartbeat))
                    
                    message = await asyncio.wait_for(
                        self.websocket.recv(), 
                        timeout=30.0
                    )
                    
                    await self._handle_message(message)
                    
                except asyncio.TimeoutError:
                    # Heartbeat gönder
                    await self._send_heartbeat()
                    continue
                    
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("WebSocket bağlantısı kapandı")
                    self.is_connected = False
                    break
                    
        except Exception as e:
            logger.error(f"Mesaj dinleme hatası: {e}")
            self.is_connected = False
    
    async def _handle_message(self, message: str):
        """Gelen mesajı işle"""
        try:
            if not message.strip():
                return
            
            logger.debug(f"Gelen mesaj: {message}")
            
            # SignalR protokol mesajlarını filtrele
            if message.startswith('{"C":') or message.startswith('{"S":'):
                # Bağlantı durumu mesajları
                data = json.loads(message)
                if data.get("S") == 1:
                    logger.info("SignalR bağlantısı başarılı")
                return
            
            # Hub mesajlarını işle
            if message.startswith('{"M":'):
                data = json.loads(message)
                await self._process_hub_message(data)
                return
                
            # Diğer mesaj türleri
            try:
                data = json.loads(message)
                await self._process_notification(data)
            except json.JSONDecodeError:
                # JSON olmayan mesajlar (heartbeat vs.)
                pass
                
        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {e}")
    
    async def _process_hub_message(self, data: Dict[str, Any]):
        """Hub mesajını işle"""
        try:
            messages = data.get("M", [])
            
            # Tüm mesajları detaylı logla
            logger.info(f"📨 Hub mesajı alındı: {json.dumps(data, indent=2)}")
            
            for msg in messages:
                hub = msg.get("H", "").lower()
                method = msg.get("M", "")
                arguments = msg.get("A", [])
                
                if hub == "commonnotificationhub":
                    logger.info(f"🔔 Hub: {hub} | Method: {method} | Arguments: {json.dumps(arguments, indent=2)}")
                    
                    # Çekim talebi bildirimi kontrolü (daha geniş kapsamlı)
                    if any(key in method.lower() for key in ["withdrawal", "withdraw", "çekim", "para"]):
                        await self._handle_withdrawal_notification(method, arguments)
                    # Notification tipindeki mesajları da kontrol et
                    elif method.lower() == "notification" and arguments:
                        # Eğer bildirimde withdrawal geçiyorsa işle
                        if any("withdrawal" in str(arg).lower() or "çekim" in str(arg).lower() for arg in arguments):
                            await self._handle_withdrawal_notification(method, arguments)
                        else:
                            await self._handle_general_notification(method, arguments)
                    # Diğer tüm bildirimler
                    else:
                        await self._handle_general_notification(method, arguments)
                        
        except Exception as e:
            logger.error(f"Hub mesajı işleme hatası: {e}")
    
    async def _handle_withdrawal_notification(self, method: str, arguments: list):
        """Çekim talebi bildirimini işle"""
        try:
            logger.info(f"🔔 Çekim talebi bildirimi alındı! Method: {method}")
            
            notification_data = {
                "type": "withdrawal",
                "method": method,
                "timestamp": datetime.now().isoformat(),
                "data": arguments
            }
            
            if self.on_notification_callback:
                await self._safe_callback(notification_data)
                
        except Exception as e:
            logger.error(f"Çekim bildirimi işleme hatası: {e}")
    
    async def _handle_general_notification(self, method: str, arguments: list):
        """Genel bildirimi işle"""
        try:
            logger.info(f"📢 Genel bildirim alındı - Method: {method}")
            
            notification_data = {
                "type": "general",
                "method": method,
                "timestamp": datetime.now().isoformat(),
                "data": arguments
            }
            
            if self.on_notification_callback:
                await self._safe_callback(notification_data)
                
        except Exception as e:
            logger.error(f"Genel bildirim işleme hatası: {e}")
    
    async def _process_notification(self, data: Dict[str, Any]):
        """Bildirim mesajını işle"""
        try:
            # Bildirim türünü belirle
            if any(key in str(data).lower() for key in ["withdrawal", "çekim", "para"]):
                notification_data = {
                    "type": "withdrawal",
                    "method": "notification",
                    "timestamp": datetime.now().isoformat(),
                    "data": data
                }
            else:
                notification_data = {
                    "type": "general",
                    "method": "notification", 
                    "timestamp": datetime.now().isoformat(),
                    "data": data
                }
            
            if self.on_notification_callback:
                await self._safe_callback(notification_data)
                
        except Exception as e:
            logger.error(f"Bildirim işleme hatası: {e}")
    
    async def _safe_callback(self, notification_data: Dict[str, Any]):
        """Callback'i güvenli şekilde çağır"""
        try:
            if asyncio.iscoroutinefunction(self.on_notification_callback):
                await self.on_notification_callback(notification_data)
            else:
                self.on_notification_callback(notification_data)
        except Exception as e:
            logger.error(f"Callback hatası: {e}")
    
    async def _send_heartbeat(self):
        """Heartbeat mesajı gönder"""
        try:
            if self.websocket and self.is_connected:
                await self.websocket.ping()
                logger.debug("Heartbeat gönderildi")
        except Exception as e:
            logger.error(f"Heartbeat hatası: {e}")
    
    async def disconnect(self):
        """Bağlantıyı kapat"""
        try:
            self.is_connected = False
            self.is_running = False
            
            if self.websocket:
                await self.websocket.close()
                logger.info("SignalR bağlantısı kapatıldı")
                
        except Exception as e:
            logger.error(f"Bağlantı kapatma hatası: {e}")
    
    async def run_with_reconnect(self):
        """Otomatik yeniden bağlanma ile çalıştır"""
        self.is_running = True
        
        while self.is_running:
            try:
                if await self.connect():
                    await self.listen()
                
                # Bağlantı koptu, yeniden bağlanmayı dene
                if self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
                    self.reconnect_attempts += 1
                    logger.info(f"Yeniden bağlanma denemesi {self.reconnect_attempts}/{self.max_reconnect_attempts}")
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    logger.error("Maksimum yeniden bağlanma denemesi aşıldı")
                    break
                    
            except Exception as e:
                logger.error(f"SignalR çalıştırma hatası: {e}")
                if self.is_running:
                    await asyncio.sleep(self.reconnect_delay)

# Threading wrapper
class SignalRClientThread:
    def __init__(self, 
                 hub_access_token: str,
                 connection_token: str,
                 groups_token: str,
                 on_notification_callback: Optional[Callable] = None):
        
        self.signalr_client = BetConstructSignalRClient(
            hub_access_token=hub_access_token,
            connection_token=connection_token,
            groups_token=groups_token,
            on_notification_callback=on_notification_callback
        )
        
        self.thread = None
        self.loop = None
        self.is_running = False
    
    def start(self):
        """SignalR client'ı thread'de başlat"""
        if self.is_running:
            return
        
        self.is_running = True
        self.thread = threading.Thread(target=self._run_in_thread, daemon=True)
        self.thread.start()
        logger.info("SignalR client thread başlatıldı")
    
    def stop(self):
        """SignalR client'ı durdur"""
        self.is_running = False
        
        if self.loop and self.signalr_client:
            asyncio.run_coroutine_threadsafe(
                self.signalr_client.disconnect(), 
                self.loop
            )
        
        if self.thread:
            self.thread.join(timeout=5)
        
        logger.info("SignalR client durduruldu")
    
    def _run_in_thread(self):
        """Thread içinde asyncio loop çalıştır"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            self.loop.run_until_complete(
                self.signalr_client.run_with_reconnect()
            )
            
        except Exception as e:
            logger.error(f"SignalR thread hatası: {e}")
        finally:
            if self.loop:
                self.loop.close()
