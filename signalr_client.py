import asyncio
import json
import logging
import aiohttp
from signalrcore.hub_connection_builder import HubConnectionBuilder

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SignalRClient:
    def __init__(self, url, hub_name, access_token_factory=None, headers=None):
        self.url = url
        self.hub_name = hub_name
        self.access_token_factory = access_token_factory
        self.headers = headers or {}
        self.connection = None
        self.connection_token = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.ping_interval = 20  # seconds

    async def get_connection_token(self):
        """Yeni connection token al"""
        try:
            async with aiohttp.ClientSession() as session:
                # URL'deki /connect yerine /negotiate kullanıyoruz
                negotiate_url = f"{self.url.replace('/connect', '/negotiate')}?hub={self.hub_name}"
                
                if self.access_token_factory:
                    token = await self.access_token_factory()
                    negotiate_url += f"&access_token={token}"
                
                logger.info(f"Getting connection token from: {negotiate_url}")
                
                async with session.post(negotiate_url, headers=self.headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.connection_token = data.get('ConnectionToken')
                        if self.connection_token:
                            logger.info(f"New connection token received: {self.connection_token[:20]}...")
                            return self.connection_token
                        else:
                            logger.error("No ConnectionToken in response")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get connection token: {response.status} - {error_text}")
                        return None
        except Exception as e:
            logger.error(f"Error getting connection token: {e}")
            return None

    async def start_connection(self):
        """SignalR bağlantısını başlat"""
        try:
            # Önce yeni bir connection token al
            token = await self.get_connection_token()
            if not token:
                logger.error("Cannot start connection without a valid token")
                return False

            # WebSocket URL'ini oluştur
            ws_url = f"{self.url}?transport=webSockets&clientProtocol=2.1&hub={self.hub_name}&connectionToken={token}"
            
            if self.access_token_factory:
                access_token = await self.access_token_factory()
                ws_url += f"&access_token={access_token}"

            logger.info(f"Connecting to: {ws_url[:100]}...")

            # HubConnection oluştur
            self.connection = HubConnectionBuilder()\
                .with_url(ws_url, options={
                    "access_token_factory": self.access_token_factory,
                    "headers": self.headers
                })\
                .with_automatic_reconnect({
                    "type": "interval",
                    "keep_alive_interval": 10,
                    "reconnect_interval": 5,
                    "max_attempts": self.max_reconnect_attempts
                })\
                .build()

            # Event handler'ları ekle
            self.connection.on_open(self.on_connected)
            self.connection.on_close(self.on_disconnected)
            self.connection.on_error(self.on_error)

            # Bağlantıyı başlat
            await self.connection.start()
            self.is_connected = True
            
            # Ping gönderme task'ini başlat
            asyncio.create_task(self.ping_task())
            
            logger.info("SignalR connection started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start connection: {e}")
            self.is_connected = False
            return False

    async def ping_task(self):
        """Düzenli ping göndererek bağlantıyı canlı tut"""
        while self.is_connected:
            try:
                await asyncio.sleep(self.ping_interval)
                if self.connection and self.is_connected:
                    # SignalR ping mesajı (type: 6)
                    ping_msg = json.dumps({"type": 6})
                    await self.connection.send(ping_msg)
                    logger.debug("Ping sent to keep connection alive")
            except Exception as e:
                logger.error(f"Error in ping task: {e}")
                await self.reconnect()

    async def reconnect(self):
        """Yeniden bağlanmayı dene"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")
            return False

        self.reconnect_attempts += 1
        logger.info(f"Attempting to reconnect ({self.reconnect_attempts}/{self.max_reconnect_attempts})")
        
        # Önceki bağlantıyı kapat
        if self.connection:
            try:
                self.connection.stop()
            except:
                pass
        
        # Exponential backoff ile bekle
        wait_time = min(2 ** self.reconnect_attempts, 30)  # Max 30 saniye
        await asyncio.sleep(wait_time)
        
        # Yeni bağlantı kur
        success = await self.start_connection()
        
        if success:
            self.reconnect_attempts = 0  # Reset attempt counter
        
        return success

    def on_connected(self):
        """Bağlantı kurulduğunda çağrılır"""
        logger.info("Connected to SignalR hub")
        self.is_connected = True
        self.reconnect_attempts = 0

    def on_disconnected(self):
        """Bağlantı kesildiğinde çağrılır"""
        logger.warning("Disconnected from SignalR hub")
        self.is_connected = False
        # Yeniden bağlanmayı dene
        asyncio.create_task(self.reconnect())

    def on_error(self, error):
        """Hata oluştuğunda çağrılır"""
        logger.error(f"SignalR error: {error}")

    def subscribe(self, method_name, callback):
        """Hub metoduna subscribe ol"""
        if self.connection:
            self.connection.on(method_name, callback)
            logger.info(f"Subscribed to {method_name}")

    async def invoke(self, method_name, args=[]):
        """Hub metodunu çağır"""
        if self.connection and self.is_connected:
            try:
                return await self.connection.invoke(method_name, args)
            except Exception as e:
                logger.error(f"Error invoking {method_name}: {e}")
                return None
        else:
            logger.error("Cannot invoke method - not connected")
            return None

    async def stop(self):
        """Bağlantıyı durdur"""
        self.is_connected = False
        if self.connection:
            try:
                self.connection.stop()
            except:
                pass
        logger.info("SignalR connection stopped")
