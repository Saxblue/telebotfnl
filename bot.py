import os
import json
import asyncio
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import threading
import time
import pytz
from dotenv import load_dotenv
from signalr_client import SignalRClientThread
import websocket
import urllib.parse

# .env dosyasını güvenli şekilde yükle
try:
    load_dotenv()
except UnicodeDecodeError:
    # Encoding hatası varsa varsayılan değerlerle devam et
    print("Warning: .env file encoding error, using environment variables only")
except Exception as e:
    print(f"Warning: Error loading .env file: {e}")

# Logging ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class WithdrawalListener:
    """BetConstruct çekim taleplerini dinleyen sınıf"""
    
    def __init__(self, bot_instance=None):
        self.bot_instance = bot_instance
        self.connected = False
        self.ws = None
        self.connection_token = ""
        
        # Config değerleri - .env'den alınacak
        self.hub_access_token = os.getenv('WITHDRAWAL_HUB_ACCESS_TOKEN', 'hat_09B5BF6E3727F5D7CB5525B5E69CD65B')
        self.cookie = os.getenv('WITHDRAWAL_COOKIE', 'aOcY0ZdVaO82BpNTRVzU_SidWLt2CzTVzc_WspMvv4U-1758013288-1.0.1.1-0aTc0yBNWmoTR7VHIFJk3tEyeWVlZB7337RuvCxEyG0HNf9wDASeukHVcK8oDd6_3PQo3b4uHYR5B2clUf0z_q1PEwCoF50eghQpjKnuWnUvVKFeXtfITHSTYH3wIwJW')
        self.subscribe_token = os.getenv('WITHDRAWAL_SUBSCRIBE_TOKEN', 'cd39f2aa7eef4cd1882b94099916443622ebdda141d8c93258c780905aa47ad2')
        self.subscription_ids = [2, 3, 50]
        self.base_url = "https://backofficewebadmin.betconstruct.com"
        
        self.withdrawal_notifications = []
        self.deposit_notifications = []  # Yatırım bildirimleri için
        self.is_running = False
        
        # Ping/Pong ve token yenileme için
        self.last_ping_time = 0
        self.last_pong_time = 0
        self.ping_interval = 30  # 30 saniyede bir ping gönder
        self.token_refresh_interval = 300  # 5 dakikada bir token'ları yenile
        self.last_token_refresh = 0
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.ping_thread = None
        self.token_refresh_thread = None
        
        # Yatırım bildirimi için
        self.deposit_check_interval = 60  # 60 saniyede bir yatırım kontrolü
        self.last_deposit_check = 0
        self.deposit_check_thread = None
        self.last_processed_deposits = set()  # İşlenmiş yatırımları takip et
        
    def log_message(self, message):
        """Log mesajı"""
        logger.info(f"[WithdrawalListener] {message}")
        
    def start_ping_thread(self):
        """Ping thread'ini başlat"""
        if self.ping_thread and self.ping_thread.is_alive():
            return
            
        def ping_loop():
            while self.is_running and self.connected:
                try:
                    current_time = time.time()
                    
                    # Ping gönderme zamanı geldi mi?
                    if current_time - self.last_ping_time >= self.ping_interval:
                        if self.ws and self.connected:
                            # SignalR ping mesajı gönder
                            ping_message = json.dumps({"H": "commonnotificationhub", "M": "ping", "A": [], "I": int(current_time)})
                            self.ws.send(ping_message)
                            self.last_ping_time = current_time
                            self.log_message(f"📡 Ping gönderildi: {current_time}")
                    
                    # Pong kontrolü - 60 saniye içinde pong gelmezse yeniden bağlan
                    if (current_time - self.last_pong_time > 60 and 
                        self.last_pong_time > 0 and 
                        self.connected):
                        self.log_message("⚠️ Pong timeout - yeniden bağlanılıyor...")
                        self.reconnect()
                    
                    time.sleep(5)  # 5 saniyede bir kontrol et
                    
                except Exception as e:
                    self.log_message(f"❌ Ping thread hatası: {str(e)}")
                    time.sleep(10)
        
        self.ping_thread = threading.Thread(target=ping_loop, daemon=True)
        self.ping_thread.start()
        self.log_message("🏓 Ping thread başlatıldı")
    
    def start_token_refresh_thread(self):
        """Token yenileme thread'ini başlat"""
        if self.token_refresh_thread and self.token_refresh_thread.is_alive():
            return
            
        def token_refresh_loop():
            while self.is_running:
                try:
                    current_time = time.time()
                    
                    # Token yenileme zamanı geldi mi?
                    if current_time - self.last_token_refresh >= self.token_refresh_interval:
                        self.log_message("🔄 Token'lar yenileniyor...")
                        
                        # Yeni token'ları al
                        if self.refresh_tokens():
                            self.last_token_refresh = current_time
                            self.log_message("✅ Token'lar başarıyla yenilendi")
                            
                            # Bağlantı varsa yeniden bağlan
                            if self.connected:
                                self.log_message("🔄 Yeni token'larla yeniden bağlanılıyor...")
                                self.reconnect()
                        else:
                            self.log_message("❌ Token yenileme başarısız")
                    
                    time.sleep(30)  # 30 saniyede bir kontrol et
                    
                except Exception as e:
                    self.log_message(f"❌ Token refresh thread hatası: {str(e)}")
                    time.sleep(60)
        
        self.token_refresh_thread = threading.Thread(target=token_refresh_loop, daemon=True)
        self.token_refresh_thread.start()
        self.log_message("🔑 Token refresh thread başlatıldı")
    
    def start_deposit_check_thread(self):
        """Yatırım kontrolü thread'ini başlat"""
        if self.deposit_check_thread and self.deposit_check_thread.is_alive():
            return
            
        def deposit_check_loop():
            while self.is_running:
                try:
                    current_time = time.time()
                    
                    # Yatırım kontrolü zamanı geldi mi?
                    if current_time - self.last_deposit_check >= self.deposit_check_interval:
                        self.log_message("💰 Yeni yatırım talepleri kontrol ediliyor...")
                        
                        # Yeni yatırım taleplerini kontrol et
                        self.check_deposit_requests()
                        
                        self.last_deposit_check = current_time
                    
                    time.sleep(10)  # 10 saniyede bir kontrol et
                    
                except Exception as e:
                    self.log_message(f"❌ Deposit check thread hatası: {str(e)}")
                    time.sleep(30)
        
        self.deposit_check_thread = threading.Thread(target=deposit_check_loop, daemon=True)
        self.deposit_check_thread.start()
        self.log_message("💰 Deposit check thread başlatıldı")
    
    def refresh_tokens(self):
        """Token'ları yenile"""
        try:
            # Negotiate işlemini tekrar yap
            if self.negotiate_connection():
                self.log_message("🔑 Connection token yenilendi")
                return True
            else:
                self.log_message("❌ Token yenileme başarısız")
                return False
        except Exception as e:
            self.log_message(f"❌ Token yenileme hatası: {str(e)}")
            return False
    
    def reconnect(self):
        """WebSocket bağlantısını yeniden kur"""
        try:
            if self.reconnect_attempts >= self.max_reconnect_attempts:
                self.log_message(f"❌ Maksimum yeniden bağlanma denemesi aşıldı ({self.max_reconnect_attempts})")
                return False
            
            self.reconnect_attempts += 1
            self.log_message(f"🔄 Yeniden bağlanma denemesi {self.reconnect_attempts}/{self.max_reconnect_attempts}")
            
            # Mevcut bağlantıyı kapat
            if self.ws:
                self.ws.close()
                time.sleep(2)
            
            self.connected = False
            
            # Yeni bağlantı kur
            if self.connect_signalr():
                self.reconnect_attempts = 0  # Başarılı olursa sayacı sıfırla
                return True
            else:
                return False
                
        except Exception as e:
            self.log_message(f"❌ Yeniden bağlanma hatası: {str(e)}")
            return False
    
    def check_deposit_requests(self):
        """Yeni yatırım taleplerini kontrol et"""
        try:
            if not self.api_key:
                self.log_message("❌ API key bulunamadı, yatırım kontrolü atlanıyor")
                return
            
            self.log_message("🔍 Yatırım talepleri kontrol ediliyor...")
            
            # API çağrısı yap
            url = "https://backofficewebadmin.betconstruct.com/ApiRequest/GetClientDepositRequestsWithTotals"
            headers = {
                'Authentication': self.api_key,
                'Content-Type': 'application/json'
            }
            
            # Bugünün tarihini al
            today = datetime.now().strftime("%Y-%m-%d")
            payload = {
                "FromDate": today,
                "ToDate": today,
                "WithTotals": True
            }
            
            self.log_message(f"📡 API çağrısı yapılıyor: {url}")
            self.log_message(f"📅 Tarih aralığı: {today}")
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            self.log_message(f"📊 API Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                deposits = data.get("Objects", [])
                
                self.log_message(f"📋 Toplam yatırım talebi sayısı: {len(deposits)}")
                
                if not deposits:
                    self.log_message("ℹ️ Bugün yatırım talebi bulunamadı")
                    return
                
                # İlk birkaç deposit'i log'la (debug için)
                for i, deposit in enumerate(deposits[:3]):
                    self.log_message(f"🔍 Deposit {i+1}: ID={deposit.get('Id')}, State={deposit.get('StateName')}, Client={deposit.get('ClientName')}")
                
                # Son kontrol zamanını güncelle
                self.last_deposit_check = datetime.now().isoformat()
                
                # Yeni yatırım taleplerini bul
                current_time = datetime.now()
                new_deposits = []
                yeni_state_count = 0
                
                for deposit in deposits:
                    deposit_id = deposit.get("Id")
                    request_time_str = deposit.get("RequestTime", "")
                    state_name = deposit.get("StateName", "")
                    
                    # "Yeni" durumundaki talepleri say
                    if state_name == "Yeni":
                        yeni_state_count += 1
                    
                    # Sadece "Yeni" durumundaki talepleri işle
                    if state_name != "Yeni":
                        continue
                    
                    # Daha önce işlenmiş mi kontrol et
                    if deposit_id in self.last_processed_deposits:
                        self.log_message(f"⏭️ Deposit {deposit_id} daha önce işlenmiş, atlanıyor")
                        continue
                    
                    # Son 10 dakikada mı kontrol et
                    try:
                        # ISO format: "2025-09-18T01:08:17.692+04:00"
                        request_time = datetime.fromisoformat(request_time_str.replace('+04:00', ''))
                        time_diff = (current_time - request_time).total_seconds()
                        
                        self.log_message(f"⏰ Deposit {deposit_id} zaman farkı: {time_diff:.0f} saniye")
                        
                        # Son 10 dakikada oluşturulmuş mu?
                        if time_diff <= 600:  # 10 dakika = 600 saniye
                            self.log_message(f"✅ Deposit {deposit_id} son 10 dakikada oluşturulmuş!")
                            new_deposits.append(deposit)
                            self.last_processed_deposits.add(deposit_id)
                        else:
                            self.log_message(f"⏳ Deposit {deposit_id} çok eski ({time_diff:.0f}s)")
                            
                    except Exception as e:
                        self.log_message(f"⚠️ Tarih parse hatası: {str(e)}")
                        continue
                
                self.log_message(f"📊 'Yeni' durumunda toplam: {yeni_state_count}")
                self.log_message(f"🆕 Son 10 dakikada yeni: {len(new_deposits)}")
                
                # Yeni yatırım taleplerini işle
                if new_deposits:
                    self.log_message(f"🚀 {len(new_deposits)} yeni yatırım talebi işleniyor!")
                    for deposit in new_deposits:
                        self.process_deposit_notification(deposit)
                else:
                    self.log_message("ℹ️ İşlenecek yeni yatırım talebi bulunamadı")
                    
            else:
                self.log_message(f"❌ Yatırım API hatası: {response.status_code}")
                self.log_message(f"📄 Response: {response.text[:500]}")
                
        except Exception as e:
            self.log_message(f"❌ Yatırım kontrolü hatası: {str(e)}")
            import traceback
            self.log_message(f"🔍 Detay: {traceback.format_exc()}")

    def process_deposit_notification(self, deposit_data):
        """Yatırım bildirimini işle ve Telegram'a gönder"""
        try:
            # Yatırım bilgilerini çıkar
            client_name = deposit_data.get("ClientName", "")
            client_login = deposit_data.get("ClientLogin", "")
            amount = deposit_data.get("Amount", 0)
            currency = deposit_data.get("CurrencyId", "TRY")
            btag = deposit_data.get("BTag", "")
            info = deposit_data.get("Info", "")
            
            # M.Notu'nu Info alanından çıkar
            customer_note = ""
            if info and ":" in info:
                # "BANKA HAVALE MUSTERI NOTU:fast" -> "fast"
                parts = info.split(":", 1)
                if len(parts) > 1:
                    customer_note = parts[1].strip()
            
            # Telegram mesajı oluştur (istenen şablon)
            message = "🔔 Yeni yatırım talebi geldi!🔔\n"
            message += f"👤 Müşteri: {client_name}\n"
            message += f"🆔 Kullanıcı Adı: {client_login}\n"
            
            # BTag varsa ekle
            if btag:
                message += f"🏷️ B. Tag: {btag}\n"
            
            message += f"💰 Miktar: {amount:,.2f} {currency}\n"
            
            # M.Notu varsa ekle
            if customer_note:
                message += f"📝M.Notu: {customer_note}"
            
            # Telegram'a gönder
            self.send_telegram_notification(message)
            
            # Bildirimi kaydet
            notification_info = {
                "type": "deposit",
                "client_id": deposit_data.get("ClientId", ""),
                "client_name": client_name,
                "client_login": client_login,
                "amount": amount,
                "currency": currency,
                "btag": btag,
                "customer_note": customer_note,
                "timestamp": datetime.now().isoformat(),
                "message": message
            }
            
            self.deposit_notifications.append(notification_info)
            
            # Son 100 bildirimi tut
            if len(self.deposit_notifications) > 100:
                self.deposit_notifications = self.deposit_notifications[-100:]
            
            self.log_message(f"💰 Yatırım bildirimi gönderildi: {client_name} - {amount} {currency}")
            
        except Exception as e:
            self.log_message(f"❌ Yatırım bildirimi işleme hatası: {str(e)}")

    def start_deposit_check_thread(self):
        """Yatırım kontrol thread'ini başlat"""
        try:
            if hasattr(self, 'deposit_check_thread') and self.deposit_check_thread and self.deposit_check_thread.is_alive():
                self.log_message("⚠️ Yatırım kontrol thread'i zaten çalışıyor")
                return
            
            self.deposit_check_thread = threading.Thread(target=self.deposit_check_loop, daemon=True)
            self.deposit_check_thread.start()
            self.log_message("🚀 Yatırım kontrol thread'i başlatıldı")
            
        except Exception as e:
            self.log_message(f"❌ Yatırım kontrol thread başlatma hatası: {str(e)}")

    def deposit_check_loop(self):
        """Yatırım kontrol döngüsü (60 saniyede bir çalışır)"""
        while self.is_running:
            try:
                self.check_deposit_requests()
                time.sleep(60)  # 60 saniye bekle
            except Exception as e:
                self.log_message(f"❌ Yatırım kontrol döngüsü hatası: {str(e)}")
                time.sleep(60)

    def start_ping_thread(self):
        """Ping thread'ini başlat"""
        try:
            if hasattr(self, 'ping_thread') and self.ping_thread and self.ping_thread.is_alive():
                self.log_message("⚠️ Ping thread'i zaten çalışıyor")
                return
            
            self.ping_thread = threading.Thread(target=self.ping_loop, daemon=True)
            self.ping_thread.start()
            self.log_message("🏓 Ping thread'i başlatıldı")
            
        except Exception as e:
            self.log_message(f"❌ Ping thread başlatma hatası: {str(e)}")

    def ping_loop(self):
        """Ping döngüsü (30 saniyede bir ping gönderir)"""
        while self.is_running and self.connected:
            try:
                time.sleep(30)  # 30 saniye bekle
                if self.connected and self.ws:
                    # Ping gönder
                    ping_msg = {"H": "commonnotificationhub", "M": "Ping", "A": [], "I": 999}
                    self.ws.send(json.dumps(ping_msg))
                    self.log_message("🏓 Ping gönderildi")
                    
                    # Pong kontrolü (60 saniye timeout)
                    time.sleep(60)
                    if time.time() - self.last_pong_time > 90:  # 90 saniye pong gelmezse
                        self.log_message("⚠️ Pong timeout! Yeniden bağlanma deneniyor...")
                        self.reconnect()
                        
            except Exception as e:
                self.log_message(f"❌ Ping döngüsü hatası: {str(e)}")
                time.sleep(30)

    def start_token_refresh_thread(self):
        """Token yenileme thread'ini başlat"""
        try:
            if hasattr(self, 'token_refresh_thread') and self.token_refresh_thread and self.token_refresh_thread.is_alive():
                self.log_message("⚠️ Token refresh thread'i zaten çalışıyor")
                return
            
            self.token_refresh_thread = threading.Thread(target=self.token_refresh_loop, daemon=True)
            self.token_refresh_thread.start()
            self.log_message("🔑 Token refresh thread'i başlatıldı")
            
        except Exception as e:
            self.log_message(f"❌ Token refresh thread başlatma hatası: {str(e)}")

    def token_refresh_loop(self):
        """Token yenileme döngüsü (5 dakikada bir token'ları yeniler)"""
        while self.is_running:
            try:
                time.sleep(300)  # 5 dakika bekle
                if self.is_running:
                    self.log_message("🔄 Token'lar yenileniyor...")
                    # Token'ları yenile (bu fonksiyon global token updater'dan çağrılacak)
                    # Burada sadece log veriyoruz, gerçek yenileme external script'te
                    
            except Exception as e:
                self.log_message(f"❌ Token refresh döngüsü hatası: {str(e)}")
                time.sleep(300)
        
    def negotiate_connection(self):
        """SignalR negotiate işlemi"""
        try:
            headers = {
                'Cookie': self.cookie,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            url = f"{self.base_url}/signalr/negotiate"
            params = {
                'hubAccessToken': self.hub_access_token,
                'clientProtocol': '2.1',
                '_': str(int(time.time() * 1000))
            }
            
            response = requests.get(url, params=params, headers=headers)
            if response.status_code == 200:
                data = response.json()
                self.connection_token = data.get('ConnectionToken', '')
                self.log_message(f"Negotiate başarılı: {self.connection_token[:20]}...")
                return True
            else:
                self.log_message(f"Negotiate hatası: {response.status_code}")
                return False
        except Exception as e:
            self.log_message(f"Negotiate exception: {str(e)}")
            return False
            
    def connect_signalr(self):
        """SignalR bağlantısı kur"""
        try:
            self.log_message("SignalR bağlantısı kuruluyor...")
            
            if not self.negotiate_connection():
                self.log_message("Negotiate hatası")
                return False
                
            # WebSocket URL'i oluştur
            ws_url = f"{self.base_url.replace('https://', 'wss://')}/signalr/connect"
            params = {
                'transport': 'webSockets',
                'clientProtocol': '2.1',
                'hubAccessToken': self.hub_access_token,
                'connectionToken': self.connection_token,
                'connectionData': '[{"name":"commonnotificationhub"}]',
                'tid': '10'
            }
            
            full_url = f"{ws_url}?{urllib.parse.urlencode(params)}"
            
            # WebSocket bağlantısı
            self.ws = websocket.WebSocketApp(
                full_url,
                header=[f"Cookie: {self.cookie}"],
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            # Thread'de çalıştır
            def run_websocket():
                self.ws.run_forever()
                
            threading.Thread(target=run_websocket, daemon=True).start()
            return True
            
        except Exception as e:
            self.log_message(f"Bağlantı hatası: {str(e)}")
            return False
            
    def on_open(self, ws):
        """WebSocket açıldığında"""
        self.connected = True
        self.log_message("WebSocket bağlantısı kuruldu")
        
        # Subscribe mesajı gönder - Browser'dan alınan doğru format
        subscribe_data = []
        for sub_id in self.subscription_ids:
            subscribe_data.append({"Subscription": sub_id})
        
        # Browser'da çalışan exact format
        subscribe_msg = {
            "H": "commonnotificationhub",
            "M": "Subscribe",
            "A": [{
                "Data": subscribe_data,
                "Token": self.subscribe_token
            }],
            "I": 0
        }
        
        # Gönderilecek mesajı logla
        subscribe_json = json.dumps(subscribe_msg)
        self.log_message(f"📡 Subscribe mesajı gönderiliyor (Browser formatı):")
        self.log_message(f"📋 Mesaj: {subscribe_json}")
        
        ws.send(subscribe_json)
        self.log_message("✅ Subscribe mesajı gönderildi - Browser formatı kullanıldı")
        
        # Alternatif subscription ID'leri de dene
        def try_alternative_subscriptions():
            time.sleep(3)
            if self.connected:
                # Sadece ID 2 ve 3'ü dene (browser loglarından)
                alt_subscribe_msg = {
                    "H": "commonnotificationhub",
                    "M": "Subscribe",
                    "A": [{
                        "Data": [{"Subscription": 2}, {"Subscription": 3}],
                        "Token": self.subscribe_token
                    }],
                    "I": 1
                }
                alt_json = json.dumps(alt_subscribe_msg)
                self.log_message(f"🔄 Alternatif subscription deneniyor (ID 2,3): {alt_json}")
                ws.send(alt_json)
                
            time.sleep(3)
            if self.connected:
                # Tüm ID'leri ayrı ayrı dene
                for i, sub_id in enumerate([1, 4, 5, 10, 20, 30, 40, 50]):
                    if self.connected:
                        single_sub_msg = {
                            "H": "commonnotificationhub",
                            "M": "Subscribe",
                            "A": [{
                                "Data": [{"Subscription": sub_id}],
                                "Token": self.subscribe_token
                            }],
                            "I": 10 + i
                        }
                        single_json = json.dumps(single_sub_msg)
                        self.log_message(f"🎯 Tek subscription deneniyor (ID {sub_id}): {single_json}")
                        ws.send(single_json)
                        time.sleep(1)
                        
        threading.Thread(target=try_alternative_subscriptions, daemon=True).start()
        
    def on_message(self, ws, message):
        """WebSocket mesajı geldiğinde"""
        try:
            data = json.loads(message)
            
            # Boş mesajları atla ama log'la
            if message.strip() == '{}':
                self.log_message("📭 Boş mesaj alındı (heartbeat)")
                self.last_pong_time = time.time()  # Heartbeat'i pong olarak say
                return
            
            # Pong mesajını yakala
            if 'R' in data and 'I' in data:
                self.last_pong_time = time.time()
                self.log_message(f"🏓 Pong alındı: {data.get('I', 'unknown')}")
                return
                
            # TÜM mesajları logla (debug için)
            self.log_message(f"📨 Gelen mesaj: {message[:200]}{'...' if len(message) > 200 else ''}")
            
            # Hata kontrolü
            if 'E' in data and data['E']:
                self.log_message(f"❌ HATA: {data['E']}")
                return
            
            # Başarılı subscription kontrolü
            if 'R' in data and data.get('I') is not None:
                self.log_message(f"✅ Başarılı yanıt alındı! ID: {data['I']}")
                return
            
            # Çekim verisi kontrolü - sadece log için (işleme SignalR method'unda yapılacak)
            message_str = message.lower()
            withdrawal_keywords = ['clientid', 'amount', 'withdrawal', 'payout', 'state', 'requesttime', 
                                 'çekim', 'para', 'client', 'btag', 'paymentsystem', 'currency']
            
            if any(keyword in message_str for keyword in withdrawal_keywords):
                self.log_message("🚨 ÇEKIM VERİSİ TESPİT EDİLDİ! 🚨")
                self.log_message(f"🔍 Bulunan keyword'ler: {[kw for kw in withdrawal_keywords if kw in message_str]}")
                # NOT: İşleme SignalR Notification method'unda yapılacak, burada çift işlem engellemek için çağırmıyoruz
            
            # SignalR mesajlarını kontrol et
            if 'M' in data and data['M']:
                self.log_message(f"📡 SignalR mesaj grubu bulundu: {len(data['M'])} mesaj")
                for i, msg in enumerate(data['M']):
                    method = msg.get('M', '')
                    args = msg.get('A', [])
                    
                    self.log_message(f"📋 Mesaj {i+1}: Method='{method}', Args sayısı={len(args)}")
                    
                    # Notification method'unu yakala
                    if method.lower() == 'notification':
                        self.log_message("🔔 Notification method yakalandı!")
                        if args:
                            for j, arg in enumerate(args):
                                self.log_message(f"📄 Arg {j+1} tipi: {type(arg)}")
                                if isinstance(arg, str):
                                    try:
                                        notification_data = json.loads(arg)
                                        self.log_message(f"🔍 Notification data: Type={notification_data.get('Type')}, OpType={notification_data.get('OperationType')}")
                                        
                                        # Type 3 ve OperationType 1 çekim bildirimi
                                        if (notification_data.get('Type') == 3 and 
                                            notification_data.get('OperationType') == 1 and 
                                            'Object' in notification_data):
                                            
                                            self.log_message("🎯 Çekim bildirimi tespit edildi!")
                                            withdrawal_data = notification_data['Object']
                                            self.process_withdrawal_notification(arg, withdrawal_data)
                                        else:
                                            self.log_message(f"ℹ️ Çekim bildirimi değil: Type={notification_data.get('Type')}, OpType={notification_data.get('OperationType')}")
                                            
                                    except Exception as e:
                                        self.log_message(f"❌ Notification parse hatası: {str(e)}")
                                        self.log_message(f"🔍 Ham arg: {str(arg)[:100]}...")
                                elif isinstance(arg, dict):
                                    self.log_message(f"📊 Dict arg: {str(arg)[:100]}...")
                                    # Dict formatında da kontrol et ama işleme (çift bildirim engellemek için)
                                    if (arg.get('Type') == 3 and 
                                        arg.get('OperationType') == 1 and 
                                        'Object' in arg):
                                        self.log_message("🎯 Dict formatında çekim bildirimi tespit edildi!")
                                        self.log_message("ℹ️ Dict format çift bildirim engellemek için işlenmiyor (String format tercih ediliyor)")
                                        # withdrawal_data = arg['Object']
                                        # self.process_withdrawal_notification(str(arg), withdrawal_data)
                    else:
                        self.log_message(f"📝 Diğer method: '{method}'")
            else:
                self.log_message("📭 SignalR mesaj grubu yok")
                                        
        except Exception as e:
            self.log_message(f"❌ Mesaj işleme hatası: {str(e)}")
            self.log_message(f"🔍 Ham mesaj: {message[:200]}...")
            
    def process_withdrawal_notification(self, raw_message, withdrawal_data):
        """Çekim bildirimini işle ve Telegram'a gönder"""
        try:
            # Geçerli veri kontrolü - boş veya eksik veriler için işlem yapma
            if not withdrawal_data or not isinstance(withdrawal_data, dict):
                self.log_message("⚠️ Geçersiz withdrawal_data, işlem atlanıyor")
                return
                
            # Temel alanları kontrol et
            withdrawal_id = withdrawal_data.get('Id')
            amount = withdrawal_data.get('Amount', 0)
            state = withdrawal_data.get('State', -1)
            
            # Geçersiz veri kontrolü
            if not withdrawal_id or amount <= 0:
                self.log_message(f"⚠️ Geçersiz çekim verisi (ID: {withdrawal_id}, Amount: {amount}), atlanıyor")
                return
            
            # GLOBAL çift bildirim kontrolü - GEÇİCİ OLARAK DEVRE DIŞI (debug için)
            # with GLOBAL_WITHDRAWAL_LOCK:
            #     if withdrawal_id in GLOBAL_PROCESSED_WITHDRAWALS:
            #         self.log_message(f"🚫 GLOBAL: Çekim ID {withdrawal_id} zaten işlendi, atlanıyor")
            #         return
            #     GLOBAL_PROCESSED_WITHDRAWALS.add(withdrawal_id)
            self.log_message(f"🔍 DEBUG: Global kontrol geçici olarak devre dışı - ID: {withdrawal_id}")
            
            # Local çift bildirim kontrolü - aynı ID'yi tekrar işleme
            processed_ids = getattr(self, 'processed_withdrawal_ids', set())
            if not hasattr(self, 'processed_withdrawal_ids'):
                self.processed_withdrawal_ids = set()
                
            if withdrawal_id in processed_ids:
                self.log_message(f"⚠️ LOCAL: Çekim ID {withdrawal_id} zaten işlendi, atlanıyor")
                return
                
            # Sadece yeni çekim talepleri için bildirim gönder (State = 0: New)
            if state != 0:
                state_names = {0: "Yeni", 1: "Onaylandı", 2: "İptal", 3: "Ödendi", 4: "Reddedildi"}
                state_name = state_names.get(state, f"Bilinmeyen({state})")
                self.log_message(f"ℹ️ Çekim talebi durumu '{state_name}' olduğu için bildirim gönderilmiyor (ID: {withdrawal_id})")
                return
            
            # Withdrawal bilgilerini çıkar
            client_name = f"{withdrawal_data.get('ClientFirstName', '')} {withdrawal_data.get('ClientLastName', '')}".strip()
            client_login = withdrawal_data.get('ClientLogin', 'N/A')
            currency = withdrawal_data.get('CurrencyId', 'TRY')
            payment_system = withdrawal_data.get('PaymentSystemName', 'N/A')
            account_holder = withdrawal_data.get('AccountHolder', 'N/A')
            request_time = withdrawal_data.get('RequestTimeLocal', withdrawal_data.get('RequestTime', 'N/A'))
            
            # Info'dan IBAN bilgisini çıkar
            info = withdrawal_data.get('Info', '')
            iban_info = ""
            if 'IBAN:' in info:
                try:
                    iban_start = info.find('IBAN:') + 5
                    iban_end = info.find(',', iban_start)
                    if iban_end == -1:
                        iban_end = iban_start + 26  # IBAN genellikle 26 karakter
                    iban = info[iban_start:iban_end]
                    iban_info = f"🏦 **IBAN:** {iban}\n"
                except:
                    pass
            
            # Temiz format - fraud kontrolü için üye ID'si eklendi
            client_id = withdrawal_data.get('ClientId', 'N/A')
            btag = withdrawal_data.get('BTag', 'N/A')
            telegram_message = f"""🚨 **YENİ ÇEKİM TALEBİ** 🚨

👤 **Müşteri:** {client_name or account_holder}
🆔 **Kullanıcı Adı:** {client_login}
💰 **Miktar:** {amount:.2f} {currency}
🏦 **Ödeme Sistemi:** {payment_system}
🏷️ **B. Tag:** {btag}
🕐 **Talep Zamanı:** {request_time}
{iban_info}
🆔 **Çekim ID:** {withdrawal_id}

🔍 `fraud {client_id}`"""

            # Withdrawal bildirimini kaydet
            notification_info = {
                'timestamp': datetime.now().isoformat(),
                'withdrawal_id': withdrawal_id,
                'client_name': client_name or account_holder,
                'client_login': client_login,
                'amount': amount,
                'currency': currency,
                'payment_system': payment_system,
                'account_holder': account_holder,
                'state': state,
                'request_time': request_time,
                'telegram_message': telegram_message,
                'raw_data': withdrawal_data
            }
            
            self.withdrawal_notifications.append(notification_info)
            self.processed_withdrawal_ids.add(withdrawal_id)
            self.log_message(f"✅ Yeni çekim bildirimi kaydedildi: {client_name or account_holder} - {amount} {currency} (ID: {withdrawal_id})")
            
            # Bot instance varsa Telegram'a gönder
            if self.bot_instance:
                # Async task'ı thread-safe şekilde çalıştır
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Event loop çalışıyorsa task oluştur
                        asyncio.create_task(self.send_telegram_notification(telegram_message))
                    else:
                        # Event loop çalışmıyorsa yeni thread'de çalıştır
                        threading.Thread(
                            target=lambda: asyncio.run(self.send_telegram_notification(telegram_message)),
                            daemon=True
                        ).start()
                except RuntimeError:
                    # Event loop yoksa yeni thread'de çalıştır
                    threading.Thread(
                        target=lambda: asyncio.run(self.send_telegram_notification(telegram_message)),
                        daemon=True
                    ).start()
                    
                self.log_message("📤 Telegram bildirim gönderimi başlatıldı")
                
        except Exception as e:
            self.log_message(f"❌ Çekim bildirimi işleme hatası: {str(e)}")
            
    async def send_telegram_notification(self, message):
        """Telegram'a bildirim gönder"""
        try:
            if self.bot_instance and self.bot_instance.application:
                # Bot instance'dan chat ID'leri al
                chat_ids = getattr(self, 'telegram_chat_ids', []) or getattr(self.bot_instance, 'telegram_chat_ids', [])
                
                # Eğer chat ID'leri yoksa log'a yaz
                if not chat_ids:
                    self.log_message("⚠️ Telegram grup chat ID'leri tanımlanmamış!")
                    self.log_message("💡 .env dosyasına TELEGRAM_CHAT_IDS=-1001234567890,-1001234567891 şeklinde ekleyin")
                    self.log_message(f"📤 Gönderilecek mesaj: {message}")
                    return
                
                for chat_id in chat_ids:
                    try:
                        await self.bot_instance.application.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            parse_mode='Markdown'
                        )
                        self.log_message(f"✅ Telegram bildirimi gönderildi: {chat_id}")
                    except Exception as e:
                        self.log_message(f"❌ Telegram gönderim hatası ({chat_id}): {str(e)}")
                        
        except Exception as e:
            self.log_message(f"❌ Telegram bildirim hatası: {str(e)}")
            
    def on_error(self, ws, error):
        """WebSocket hatası"""
        self.log_message(f"WebSocket hatası: {str(error)}")
        
    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket kapandığında"""
        self.connected = False
        self.log_message("WebSocket bağlantısı kesildi")
        
    def start(self):
        """Withdrawal listener'ı başlat"""
        try:
            self.log_message("Withdrawal listener başlatılıyor...")
            self.is_running = True
            self.last_token_refresh = time.time()  # İlk token refresh zamanını ayarla
            self.last_pong_time = time.time()  # İlk pong zamanını ayarla
            
            if self.connect_signalr():
                # Ping/Pong, token refresh ve deposit check thread'lerini başlat
                self.start_ping_thread()
                self.start_token_refresh_thread()
                self.start_deposit_check_thread()
                
                self.log_message("✅ Withdrawal listener başarıyla başlatıldı!")
                self.log_message("🏓 Ping/Pong mekanizması aktif")
                self.log_message("🔑 Otomatik token yenileme aktif")
                self.log_message("💰 Otomatik yatırım bildirimi aktif")
                return True
            else:
                self.log_message("❌ Withdrawal listener başlatılamadı!")
                self.is_running = False
                return False
                
        except Exception as e:
            self.log_message(f"❌ Withdrawal listener başlatma hatası: {str(e)}")
            self.is_running = False
            return False
            
    def stop(self):
        """Withdrawal listener'ı durdur"""
        self.is_running = False
        self.connected = False
        if self.ws:
            self.ws.close()
        self.log_message("Withdrawal listener durduruldu")
        
    def get_status(self):
        """Withdrawal listener durumunu al"""
        return {
            'is_running': self.is_running,
            'is_connected': self.connected,
            'notifications_count': len(self.withdrawal_notifications),
            'last_notification': self.withdrawal_notifications[-1] if self.withdrawal_notifications else None
        }

class KPIBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_TOKEN') or '8355199755:AAGojbMeqN-Zxd3nTuRJPlqu15ZfuePUxgY'
        self.kpi_api_key = os.getenv('KPI_API_KEY', 'aad90bbaa5bc1dd7901df0879f7f4a16ab392fb02b036f07cd2a6bee2aecdfb3')
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.github_repo = os.getenv('GITHUB_REPO', 'https://github.com/Saxblue/telebot')
        
        self.api_settings = {
            "api_url": "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClientById?id={}",
            "kpi_url": "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClientKpi?id={}",
            "login_url": "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClientLogins",
            "token": self.kpi_api_key,  # Token'ı ekle
            "headers": {
                "Authentication": self.kpi_api_key,
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://backoffice.betconstruct.com/",
                "Origin": "https://backoffice.betconstruct.com",
                "Content-Type": "application/json"
            }
        }
        
        self.application = None
        self.is_running = False
        
        # SignalR client için token'lar (gerçek değerler .env'den alınacak)
        self.signalr_tokens = {
            'hub_access_token': os.getenv('HUB_ACCESS_TOKEN', 'hat_C18474C327B7C8E44F143642197E9E1E'),
            'connection_token': os.getenv('CONNECTION_TOKEN', 'cdXG9dFB3rrPjlpqtFf9fGGoo7RMq8pdgFt6rM5Mcy7sBlVYDxAZU7OA42EWwoxkaKhXEpjT893gtq3O9YNHR8JBX3ri/WkF+I53yUEooKfJlihh'),
            'groups_token': os.getenv('GROUPS_TOKEN', 'gtd2MYbGSA1A4Ix7hMpN/h4sXgjSVfrXaC7b2L9M6xyKwr6mSHng7c1F4YfnYhm3UE4psYLGqNJHFgPg21R0TNJoWjkYmWk6WhNteUl1K0xqk48E5Q/SmYAn95aR9jD0jgiWZDHCwKy5nXkDL1JlYcvzEXKnr9YQmEWSVN+ItRJ+yZf9uTCHt4EqSq9lfGzYjGEBq7mEHGnyVnFR2sC7nP9Cbv6nEnphaFZjs4WXkPiCmN0jwWYEBGahZO49Qrco6L5dWg==')
        }
        
        self.signalr_client = None
        self.withdrawal_notifications = []  # Çekim bildirimlerini sakla
        
        # Withdrawal Listener entegrasyonu
        self.withdrawal_listener = WithdrawalListener(bot_instance=self)
        
        # Telegram grup chat ID'leri - .env'den alınacak
        self.telegram_chat_ids = self.load_telegram_chat_ids()
        
    def load_telegram_chat_ids(self):
        """Telegram grup chat ID'lerini .env'den yükle"""
        chat_ids_str = os.getenv('TELEGRAM_CHAT_IDS', '')
        if chat_ids_str:
            try:
                # Virgül ile ayrılmış chat ID'leri parse et
                chat_ids = [int(id.strip()) for id in chat_ids_str.split(',') if id.strip()]
                logger.info(f"Telegram chat ID'leri yüklendi: {chat_ids}")
                return chat_ids
            except Exception as e:
                logger.error(f"Telegram chat ID'leri parse hatası: {e}")
                return []
        else:
            logger.warning("TELEGRAM_CHAT_IDS çevre değişkeni bulunamadı")
            return []
            
    def start_withdrawal_listener(self):
        """Withdrawal listener'ı başlat"""
        if self.withdrawal_listener:
            # Chat ID'leri güncelle
            if hasattr(self.withdrawal_listener, 'send_telegram_notification'):
                # Chat ID'leri withdrawal listener'a aktar
                self.withdrawal_listener.telegram_chat_ids = self.telegram_chat_ids
            
            result = self.withdrawal_listener.start()
            if result:
                logger.info("✅ Withdrawal listener başlatıldı")
            else:
                logger.error("❌ Withdrawal listener başlatılamadı")
            return result
        return False
        
    def stop_withdrawal_listener(self):
        """Withdrawal listener'ı durdur"""
        if self.withdrawal_listener:
            self.withdrawal_listener.stop()
            logger.info("🛑 Withdrawal listener durduruldu")
            return True
        return False
        
    def get_withdrawal_listener_status(self):
        """Withdrawal listener durumunu al"""
        if self.withdrawal_listener:
            return self.withdrawal_listener.get_status()
        return {'is_running': False, 'is_connected': False, 'notifications_count': 0}
        
    def get_withdrawal_notifications(self, limit=10):
        """Son withdrawal bildirimlerini al"""
        if self.withdrawal_listener:
            notifications = self.withdrawal_listener.withdrawal_notifications
            return notifications[-limit:] if notifications else []
        return []
        
    def fmt_tl(self, val):
        """Para formatı"""
        try:
            n = float(val)
            s = f"{n:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
            return f"{s} TL"
        except Exception:
            return str(val)

    def fmt_dt(self, s):
        """Tarih formatı"""
        if not s or s == 'Bilinmiyor':
            return 'Bilinmiyor'
        try:
            dt = datetime.fromisoformat(str(s).split('+')[0])
            return dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            return str(s)

    def on_signalr_notification(self, notification_data):
        """SignalR bildirimini işle"""
        try:
            notification_type = notification_data.get('type', '')
            method = notification_data.get('method', '')
            data = notification_data.get('data', {})
            timestamp = notification_data.get('timestamp', datetime.now().isoformat())
            
            # Ham bildirimi logla
            logger.info(f"🔔 SignalR Bildirimi - Type: {notification_type}, Method: {method}")
            logger.info(f"📄 Ham bildirim verisi: {json.dumps(notification_data, indent=2, ensure_ascii=False)}")
            
            # ESKİ ÇEKİM BİLDİRİM SİSTEMİ - DEVRE DIŞI (Çift bildirim engellemek için)
            # Çekim bildirimi kontrolü (daha geniş kapsamlı) - KAPALI
            is_withdrawal = (
                notification_type == 'withdrawal' or 
                'withdrawal' in str(method).lower() or 
                'withdraw' in str(method).lower() or
                'çekim' in str(data).lower() or
                'para' in str(method).lower()
            )
            
            if is_withdrawal:
                # ESKİ SİSTEM - DEVRE DIŞI (Çift bildirim engellemek için)
                logger.info(f"🚫 ESKİ SİSTEM: Çekim bildirimi tespit edildi ama işlenmiyor (çift bildirim engellemek için)")
                logger.info(f"ℹ️ Çekim işleme YENİ SİSTEM'de (WithdrawalListener) yapılıyor")
                # Eski kod devre dışı:
                # withdrawal_info = {...}
                # self.withdrawal_notifications.append(withdrawal_info)
                # loop.create_task(self.send_withdrawal_alert(withdrawal_info))
            
            # Genel bildirimler
            else:
                logger.info(f"📢 Genel bildirim: {method}")
                logger.debug(f"Genel bildirim detayı: {json.dumps(data, indent=2, ensure_ascii=False)}")
                
        except Exception as e:
            logger.error(f"SignalR bildirim işleme hatası: {e}")
    
    async def send_withdrawal_alert(self, withdrawal_info):
        """Çekim talebi uyarısı gönder"""
        try:
            # Admin kullanıcılarına bildirim gönder (örnek)
            admin_chat_ids = [123456789]  # Gerçek admin chat ID'leri buraya
            
            # Çekim verisinden kullanıcı ID'sini çıkarmaya çalış
            user_id = self.extract_user_id_from_withdrawal(withdrawal_info)
            
            alert_message = f"🚨 **YENİ ÇEKİM TALEBİ**\n\n"
            alert_message += f"⏰ Zaman: {self.fmt_dt(withdrawal_info['timestamp'])}\n"
            alert_message += f"📋 Method: {withdrawal_info['method']}\n"
            
            if user_id:
                alert_message += f"👤 Kullanıcı ID: {user_id}\n"
                alert_message += f"📊 Veri: {str(withdrawal_info['data'])[:150]}...\n\n"
                alert_message += "🔄 **Otomatik fraud raporu hazırlanıyor...**"
            else:
                alert_message += f"📊 Veri: {str(withdrawal_info['data'])[:200]}..."
            
            for chat_id in admin_chat_ids:
                try:
                    # İlk bildirimi gönder
                    sent_message = await self.application.bot.send_message(
                        chat_id=chat_id,
                        text=alert_message,
                        parse_mode='Markdown'
                    )
                    
                    # Eğer kullanıcı ID'si varsa otomatik fraud raporu oluştur
                    if user_id:
                        await self.send_auto_fraud_report(chat_id, user_id, sent_message.message_id)
                        
                except Exception as e:
                    logger.error(f"Admin bildirim gönderme hatası: {e}")
                    
        except Exception as e:
            logger.error(f"Çekim uyarısı gönderme hatası: {e}")
    
    def extract_user_id_from_withdrawal(self, withdrawal_info):
        """Çekim bildiriminden kullanıcı ID'sini çıkar"""
        try:
            data = withdrawal_info.get('data', {})
            
            # Farklı olası alanları kontrol et
            possible_fields = ['ClientId', 'UserId', 'Id', 'client_id', 'user_id', 'id']
            
            for field in possible_fields:
                if isinstance(data, dict) and field in data:
                    return str(data[field])
                elif isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], dict) and field in data[0]:
                        return str(data[0][field])
            
            # String içinde ID aramaya çalış
            data_str = str(data)
            import re
            id_match = re.search(r'"(?:ClientId|UserId|Id)"\s*:\s*"?(\d+)"?', data_str)
            if id_match:
                return id_match.group(1)
                
            return None
            
        except Exception as e:
            logger.error(f"User ID çıkarma hatası: {e}")
            return None
    
    async def send_auto_fraud_report(self, chat_id, user_id, original_message_id):
        """Otomatik fraud raporu gönder"""
        try:
            logger.info(f"🚨 Otomatik fraud raporu oluşturuluyor - User ID: {user_id}")
            
            # Fraud raporu oluştur
            fraud_report = await self.create_fraud_report(user_id)
            
            if fraud_report:
                # Fraud raporunu gönder
                fraud_message = f"🚨 **OTOMATIK FRAUD RAPORU**\n"
                fraud_message += f"👤 Kullanıcı ID: {user_id}\n\n"
                fraud_message += f"```\n{fraud_report}\n```"
                
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=fraud_message,
                    parse_mode='Markdown',
                    reply_to_message_id=original_message_id
                )
                
                logger.info(f"✅ Otomatik fraud raporu gönderildi - User ID: {user_id}")
            else:
                # Fraud raporu oluşturulamadıysa hata mesajı gönder
                error_message = f"❌ **Fraud raporu oluşturulamadı**\n"
                error_message += f"👤 Kullanıcı ID: {user_id}\n"
                error_message += f"Manuel olarak `/fraud {user_id}` komutunu deneyin."
                
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=error_message,
                    parse_mode='Markdown',
                    reply_to_message_id=original_message_id
                )
                
        except Exception as e:
            logger.error(f"Otomatik fraud raporu hatası: {e}")
            
            # Hata durumunda bilgi mesajı gönder
            try:
                error_message = f"⚠️ **Otomatik fraud raporu hatası**\n"
                error_message += f"👤 Kullanıcı ID: {user_id}\n"
                error_message += f"Hata: {str(e)[:100]}...\n"
                error_message += f"Manuel olarak `/fraud {user_id}` komutunu deneyin."
                
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=error_message,
                    parse_mode='Markdown',
                    reply_to_message_id=original_message_id
                )
            except:
                pass
    
    def start_signalr_client(self):
        """SignalR client'ı başlat"""
        try:
            if self.signalr_client:
                logger.info("SignalR client zaten çalışıyor")
                return
            
            self.signalr_client = SignalRClientThread(
                hub_access_token=self.signalr_tokens['hub_access_token'],
                connection_token=self.signalr_tokens['connection_token'],
                groups_token=self.signalr_tokens['groups_token'],
                on_notification_callback=self.on_signalr_notification
            )
            
            self.signalr_client.start()
            logger.info("✅ SignalR client başlatıldı - Real-time bildirimler aktif!")
            
        except Exception as e:
            logger.error(f"SignalR client başlatma hatası: {e}")
    
    def stop_signalr_client(self):
        """SignalR client'ı durdur"""
        try:
            if self.signalr_client:
                self.signalr_client.stop()
                self.signalr_client = None
                logger.info("SignalR client durduruldu")
        except Exception as e:
            logger.error(f"SignalR client durdurma hatası: {e}")
    
    def get_pending_withdrawals(self):
        """Bekleyen çekim taleplerini getir"""
        return [w for w in self.withdrawal_notifications if not w.get('processed', False)]
    
    def mark_withdrawal_processed(self, index):
        """Çekim talebini işlendi olarak işaretle"""
        try:
            if 0 <= index < len(self.withdrawal_notifications):
                self.withdrawal_notifications[index]['processed'] = True
                return True
            return False
        except Exception as e:
            logger.error(f"Çekim işaretleme hatası: {e}")
            return False

    def log_query(self, user_id, username, user_ids_queried, response_time):
        """Sorgu logunu kaydet"""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "user_id": user_id,
                "username": username,
                "user_ids_queried": user_ids_queried,
                "response_time": response_time,
                "query_count": len(user_ids_queried)
            }
            
            # logs.json dosyasını oku veya oluştur
            logs_file = "logs.json"
            if os.path.exists(logs_file):
                with open(logs_file, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            else:
                logs = {"queries": []}
            
            logs["queries"].append(log_entry)
            
            # Dosyaya kaydet
            with open(logs_file, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
                
            # GitHub'a push et
            self.push_logs_to_github()
            
        except Exception as e:
            logger.error(f"Log kaydetme hatası: {e}")

    def push_logs_to_github(self):
        """Logları GitHub'a push et"""
        try:
            if not self.github_token:
                return
                
            # logs.json dosyasını oku
            with open("logs.json", 'r', encoding='utf-8') as f:
                content = f.read()
            
            # GitHub API kullanarak dosyayı güncelle
            repo_parts = self.github_repo.replace('https://github.com/', '').split('/')
            owner, repo = repo_parts[0], repo_parts[1]
            
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/logs.json"
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            # Mevcut dosyayı al (SHA için)
            response = requests.get(url, headers=headers)
            sha = None
            if response.status_code == 200:
                sha = response.json().get('sha')
            
            # Dosyayı güncelle
            import base64
            encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            data = {
                "message": f"Log güncelleme - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "content": encoded_content
            }
            
            if sha:
                data["sha"] = sha
            
            requests.put(url, headers=headers, json=data)
            
        except Exception as e:
            logger.error(f"GitHub push hatası: {e}")

    def search_user_by_username(self, username):
        """Kullanıcı adına göre arama yap"""
        try:
            search_url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClients"
            
            payload = {
                "Id": "",
                "FirstName": "",
                "LastName": "",
                "PersonalId": "",
                "Email": "",
                "Phone": "",
                "ZipCode": None,
                "AMLRisk": "",
                "AffilateId": None,
                "AffiliatePlayerType": None,
                "BTag": None,
                "BetShopGroupId": "",
                "BirthDate": None,
                "CashDeskId": None,
                "CasinoProfileId": None,
                "CasinoProfitnessFrom": None,
                "CasinoProfitnessTo": None,
                "City": "",
                "ClientCategory": None,
                "CurrencyId": None,
                "DocumentNumber": "",
                "ExternalId": "",
                "Gender": None,
                "IBAN": None,
                "IsEmailSubscribed": None,
                "IsLocked": None,
                "IsOrderedDesc": True,
                "IsSMSSubscribed": None,
                "IsSelfExcluded": None,
                "IsStartWithSearch": False,
                "IsTest": None,
                "IsVerified": None,
                "Login": username.strip(),
                "MaxBalance": None,
                "MaxCreatedLocal": None,
                "MaxCreatedLocalDisable": True,
                "MaxFirstDepositDateLocal": None,
                "MaxLastTimeLoginDateLocal": None,
                "MaxLastWrongLoginDateLocal": None,
                "MaxLoyaltyPointBalance": None,
                "MaxRows": 20,
                "MaxVerificationDateLocal": None,
                "MaxWrongLoginAttempts": None,
                "MiddleName": "",
                "MinBalance": None,
                "MinCreatedLocal": None,
                "MinCreatedLocalDisable": True,
                "MinFirstDepositDateLocal": None,
                "MinLastTimeLoginDateLocal": None,
                "MinLastWrongLoginDateLocal": None,
                "MinLoyaltyPointBalance": None,
                "MinVerificationDateLocal": None,
                "MinWrongLoginAttempts": None,
                "MobilePhone": "",
                "NickName": "",
                "OrderedItem": 1,
                "OwnerId": None,
                "PartnerClientCategoryId": None,
                "RegionId": None,
                "RegistrationSource": None,
                "SelectedPepStatuses": "",
                "SkeepRows": 0,
                "SportProfitnessFrom": None,
                "SportProfitnessTo": None,
                "Status": None,
                "Time": "",
                "TimeZone": "",
            }
            
            headers = dict(self.api_settings["headers"])
            response = requests.post(search_url, headers=headers, json=payload)
            
            if response.status_code == 401:
                headers["Authorization"] = f"Bearer {self.kpi_api_key}"
                response = requests.post(search_url, headers=headers, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                if data and not data.get('HasError'):
                    users = data.get('Data', {}).get('Objects', [])
                    return users
                else:
                    logger.error(f"API Error: {data.get('AlertMessage', 'Unknown error')}")
                    return []
            else:
                logger.error(f"Search API error: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Username search error: {e}")
            return []

    async def fetch_single_user_detailed(self, user_id):
        """Tek kullanıcı için detaylı veri çek"""
        try:
            headers = dict(self.api_settings["headers"])
            
            # Ana kullanıcı bilgilerini çek
            user_url = self.api_settings["api_url"].format(user_id)
            user_response = requests.get(user_url, headers=headers)
            
            if user_response.status_code == 401:
                headers["Authorization"] = f"Bearer {self.kpi_api_key}"
                user_response = requests.get(user_url, headers=headers)
            
            if user_response.status_code != 200:
                return None
            
            user_data = user_response.json().get('Data', {})
            
            # KPI verilerini çek
            kpi_url = self.api_settings["kpi_url"].format(user_id)
            kpi_response = requests.get(kpi_url, headers=headers)
            
            kpi_data = {}
            if kpi_response.status_code == 200:
                kpi_data = kpi_response.json().get('Data', {})
            
            # Verileri birleştir
            combined_data = {
                'user': user_data,
                'kpi': kpi_data
            }
            
            return combined_data
            
        except Exception as e:
            logger.error(f"Detailed user fetch error: {e}")
            return None

    def format_user_response(self, user_data):
        """Kullanıcı yanıtını formatla"""
        try:
            user = user_data.get('user', {})
            kpi = user_data.get('kpi', {})
            
            # Temel bilgiler
            user_id = user.get('Id', 'Bilinmiyor')
            username = user.get('Login', 'Bilinmiyor')
            
            first_name = user.get('FirstName', '').strip() if user.get('FirstName') else ''
            last_name = user.get('LastName', '').strip() if user.get('LastName') else ''
            full_name = f"{first_name} {last_name}".strip() or 'Bilinmiyor'
            
            btag = user.get('BTag', 'Bilinmiyor')
            balance = f"{user.get('Balance', 0)} {user.get('CurrencyId', 'TRY')}"
            
            # KPI bilgileri
            total_deposit_amount = self.fmt_tl(kpi.get('DepositAmount', 0)) if kpi.get('DepositAmount') else 'Bilinmiyor'
            total_withdrawal_amount = self.fmt_tl(kpi.get('WithdrawalAmount', 0)) if kpi.get('WithdrawalAmount') else 'Bilinmiyor'
            total_deposit_count = kpi.get('DepositCount', 'Bilinmiyor')
            total_withdrawal_count = kpi.get('WithdrawalCount', 'Bilinmiyor')
            
            last_deposit_amount = self.fmt_tl(kpi.get('LastDepositAmount', 0)) if kpi.get('LastDepositAmount') else 'Bilinmiyor'
            last_deposit_date = self.fmt_dt(kpi.get('LastDepositTimeLocal') or kpi.get('LastDepositTime')) if kpi.get('LastDepositTimeLocal') or kpi.get('LastDepositTime') else 'Bilinmiyor'
            
            last_withdrawal_amount = self.fmt_tl(kpi.get('LastWithdrawalAmount', 0)) if kpi.get('LastWithdrawalAmount') else 'Bilinmiyor'
            last_withdrawal_date = self.fmt_dt(kpi.get('LastWithdrawalTimeLocal') or kpi.get('LastWithdrawalTime')) if kpi.get('LastWithdrawalTimeLocal') or kpi.get('LastWithdrawalTime') else 'Bilinmiyor'
            
            last_login = self.fmt_dt(user.get('LastLoginLocalDate')) if user.get('LastLoginLocalDate') else 'Bilinmiyor'
            last_bet_date = self.fmt_dt(user.get('LastCasinoBetTimeLocal')) if user.get('LastCasinoBetTimeLocal') else 'Bilinmiyor'
            
            # Yanıt formatı
            return f"""🔍 **Kullanıcı Bilgileri**

**ID:** `{user_id}`
**Kullanıcı Adı:** `{username}`
**Ad Soyad:** `{full_name}`
**BTag:** `{btag}`
**Bakiye:** `{balance}`

💰 **KPI Bilgileri**

**Toplam Yatırım:** `{total_deposit_amount}`
**Toplam Çekim:** `{total_withdrawal_amount}`
**Çekim Sayısı:** `{total_withdrawal_count}`
**Yatırım Sayısı:** `{total_deposit_count}`
**Son Yatırım:** `{last_deposit_amount}`
**Son Yatırım Tarihi:** `{last_deposit_date}`
**Son Çekim:** `{last_withdrawal_amount}`
**Son Çekim Tarihi:** `{last_withdrawal_date}`
**Son Giriş:** `{last_login}`
**Son Bahis:** `{last_bet_date}`"""
            
        except Exception as e:
            logger.error(f"Response formatting error: {e}")
            return "❌ Yanıt formatlanırken hata oluştu."

    def fetch_client_logins(self, client_id):
        """Client login verilerini API'den getir"""
        try:
            payload = {
                "ClientId": int(client_id),
                "StartDate": (datetime.now() - timedelta(days=90)).strftime("%d-%m-%y - %H:%M:%S"),
                "EndDate": datetime.now().strftime("%d-%m-%y - %H:%M:%S"),
                "MaxRows": 1000,
                "SkipRows": 0
            }
            
            response = requests.post(
                self.api_settings["login_url"],
                json=payload,
                headers=self.api_settings["headers"],
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("HasError", False):
                    logger.error(f"Login API Error: {data.get('AlertMessage', 'Unknown error')}")
                    return []
                
                if "Data" in data and "ClientLogins" in data["Data"]:
                    return data["Data"]["ClientLogins"]
                else:
                    return []
            else:
                logger.error(f"Login API HTTP Error: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Login fetch error: {e}")
            return []

    async def get_turnover_analysis(self, user_id):
        """Çevrim analizi yap ve açıklama metni döndür"""
        try:
            # İşlemleri getir (90 gün)
            url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClientTransactionsByAccount"
            headers = {
                "authentication": self.api_settings.get("token", ""),
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://backoffice.betconstruct.com/",
                "Origin": "https://backoffice.betconstruct.com"
            }
            
            from datetime import datetime, timedelta
            date_to = datetime.now()
            date_from = date_to - timedelta(days=90)
            
            payload = {
                "StartTimeLocal": date_from.strftime("%d-%m-%y"),
                "EndTimeLocal": date_to.strftime("%d-%m-%y"),
                "ClientId": int(user_id),
                "CurrencyId": "TRY",
                "BalanceTypeId": "5211",
                "DocumentTypeIds": [],
                "GameId": None
            }
            
            # Debug logging
            logger.info(f"TURNOVER DEBUG: URL: {url}")
            logger.info(f"TURNOVER DEBUG: Payload: {payload}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            logger.info(f"TURNOVER DEBUG: Response status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"TURNOVER DEBUG: API Error - Status: {response.status_code}, Response: {response.text[:500]}")
                return f"Çevrim analizi yapılamadı (API hatası: {response.status_code})"
            
            try:
                data = response.json()
            except Exception as json_error:
                logger.error(f"TURNOVER DEBUG: JSON Parse Error: {json_error}")
                return "Çevrim analizi yapılamadı (JSON hatası)"
                
            logger.info(f"TURNOVER DEBUG: Data keys: {list(data.keys()) if isinstance(data, dict) else 'Not dict'}")
            
            if data.get("HasError"):
                error_msg = data.get('AlertMessage', 'Bilinmeyen hata')
                logger.error(f"TURNOVER DEBUG: API returned error: {error_msg}")
                return f"Çevrim analizi yapılamadı (API: {error_msg})"
                
            if "Data" not in data:
                logger.error(f"TURNOVER DEBUG: No Data field in response: {data}")
                return "Çevrim analizi yapılamadı (Veri alanı yok)"
                
            # İşlemleri al
            transactions = []
            if isinstance(data["Data"], dict):
                if "Objects" in data["Data"]:
                    transactions = data["Data"]["Objects"]
            elif isinstance(data["Data"], list):
                transactions = data["Data"]
            
            if not transactions:
                return "İşlem geçmişi bulunamadı"
            
            # Analiz yap (Üye Çevrim Analizi mantığı)
            import pandas as pd
            df = pd.DataFrame(transactions)
            df['Date'] = pd.to_datetime(df['CreatedLocal'].str.split('.').str[0])
            
            # Yatırım bul
            deposits = df[df['DocumentTypeName'].isin(['Yatırım', 'Yatırım Talebi Ödemesi', 'CashBack Düzeltmesi', 'Tournament Win'])]
            if deposits.empty:
                return "Son dönemde yatırım bulunamadı"
            
            last_deposit = deposits.sort_values('Date', ascending=False).iloc[0]
            deposit_date = last_deposit['Date']
            
            # Base type belirle
            if last_deposit['DocumentTypeName'] == 'CashBack Düzeltmesi':
                base_type = 'Kayıp Bonusu'
            elif last_deposit['DocumentTypeName'] == 'Tournament Win':
                base_type = 'Turnuva Kazancı'
            else:
                base_type = 'Yatırım'
            
            base_amount = float(last_deposit['Amount'])
            
            # Yatırım sonrası işlemleri filtrele
            df_after = df[df['Date'] >= deposit_date].copy()
            df_bets = df_after[df_after['DocumentTypeName'] == 'Bahis']
            df_wins = df_after[df_after['DocumentTypeName'] == 'Kazanç Artar']
            
            total_bet = df_bets['Amount'].sum()
            total_win = df_wins['Amount'].sum()
            net_profit = total_win - total_bet
            turnover_ratio = total_bet / base_amount if base_amount else 0
            
            # Oyun bazında analiz yap (analiz.py mantığı)
            game_analysis = None
            game_text = ""
            
            if not df_bets.empty or not df_wins.empty:
                # Bahis ve kazanç verilerini oyun bazında grupla
                if not df_bets.empty:
                    game_bets = df_bets.groupby('Game')['Amount'].sum().reset_index()
                    game_bets.columns = ['Oyun', 'Toplam_Bahis']
                else:
                    game_bets = pd.DataFrame(columns=['Oyun', 'Toplam_Bahis'])
                
                if not df_wins.empty:
                    game_wins = df_wins.groupby('Game')['Amount'].sum().reset_index()
                    game_wins.columns = ['Oyun', 'Toplam_Kazanc']
                else:
                    game_wins = pd.DataFrame(columns=['Oyun', 'Toplam_Kazanc'])
                
                # Birleştir ve net karı hesapla
                if not game_bets.empty or not game_wins.empty:
                    game_analysis = pd.merge(game_bets, game_wins, on='Oyun', how='outer').fillna(0)
                    game_analysis['Net_Kar'] = game_analysis['Toplam_Kazanc'] - game_analysis['Toplam_Bahis']
                    game_analysis = game_analysis.sort_values('Net_Kar', ascending=False)
                    
                    # En çok kazandıran oyunları bul
                    profitable_games = game_analysis[game_analysis['Net_Kar'] > 0]
                    
                    if not profitable_games.empty:
                        # Ana kazancı oluşturan oyunları bul (toplam karın en az %10'unu kazandıran)
                        total_net_profit = game_analysis['Net_Kar'].sum()
                        main_profit = profitable_games[profitable_games['Net_Kar'] > total_net_profit * 0.1]
                        
                        if len(main_profit) == 1:
                            game = main_profit.iloc[0]
                            game_text = f"{game['Oyun']} oyunundan {game['Net_Kar']:,.2f} TL"
                        elif len(main_profit) > 1:
                            games_list = ", ".join([game['Oyun'] for _, game in main_profit.iterrows()])
                            total_main_profit = main_profit['Net_Kar'].sum()
                            game_text = f"{games_list} oyunlarından toplam {total_main_profit:,.2f} TL"
            
            # Bonus bilgilerini al
            bonus_info = None
            if base_type == 'Yatırım':
                try:
                    bonus_url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClientBonuses"
                    bonus_payload = {"ClientId": int(user_id), "SkipCount": 0, "TakeCount": 10}
                    bonus_response = requests.post(bonus_url, headers=headers, json=bonus_payload, timeout=30)
                    if bonus_response.status_code == 200:
                        bonus_data = bonus_response.json()
                        if not bonus_data.get("HasError") and "Data" in bonus_data:
                            bonuses = bonus_data["Data"].get("Objects", [])
                            if bonuses:
                                latest_bonus = bonuses[0]
                                if latest_bonus.get('ResultType') == 1:  # Kazanıldı
                                    bonus_info = {
                                        'name': latest_bonus.get('Name', 'Bonus'),
                                        'amount': float(latest_bonus.get('Amount', 0))
                                    }
                except:
                    pass
            
            # Kaynak türünü belirle
            if base_type == 'Kayıp Bonusu':
                kaynak = "Kayıp Bonusu"
            elif base_type == 'Turnuva Kazancı':
                kaynak = "Turnuva Kazancı"
            else:
                kaynak = "Ana Para"
            
            # Çevrim durumu
            cevrim_durum = "Tamamlandı" if turnover_ratio >= 1 else "Tamamlanmadı"
            
            # Açıklama metni oluştur
            if bonus_info:
                # Bonus varsa
                if game_text:
                    return f"{kaynak} ile ({base_amount:,.2f} TL) Aldığı {bonus_info['name']} ile ({bonus_info['amount']:,.2f} TL) {game_text} net kar elde edilmiştir. Çevrim: {turnover_ratio:.2f}x ({cevrim_durum})"
                else:
                    return f"{kaynak} ile ({base_amount:,.2f} TL) Aldığı {bonus_info['name']} ile ({bonus_info['amount']:,.2f} TL) toplam {net_profit:,.2f} TL net kar elde edilmiştir. Çevrim: {turnover_ratio:.2f}x ({cevrim_durum})"
            else:
                # Bonus yoksa
                if game_text:
                    return f"{kaynak} ile ({base_amount:,.2f} TL) {game_text} net kar elde edilmiştir. Çevrim: {turnover_ratio:.2f}x ({cevrim_durum})"
                else:
                    return f"{kaynak} ile ({base_amount:,.2f} TL) toplam {net_profit:,.2f} TL net kar elde edilmiştir. Çevrim: {turnover_ratio:.2f}x ({cevrim_durum})"
                    
        except Exception as e:
            logger.error(f"Turnover analysis error for user {user_id}: {str(e)}")
            return "Çevrim analizi yapılamadı (Sistem hatası)"

    async def fetch_latest_withdrawal_request(self, user_id):
        """Fetch the latest withdrawal request for a user"""
        try:
            # API endpoint
            url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClientTransactionsByAccount"
            
            # Headers
            headers = {
                "authentication": self.api_settings.get("token", ""),
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://backoffice.betconstruct.com/",
                "Origin": "https://backoffice.betconstruct.com"
            }
            
            # Date range - last 90 days
            date_to = datetime.now()
            date_from = date_to - timedelta(days=90)
            
            # Payload
            payload = {
                "StartTimeLocal": date_from.strftime("%d-%m-%y"),
                "EndTimeLocal": date_to.strftime("%d-%m-%y"),
                "ClientId": int(user_id),
                "CurrencyId": "TRY",
                "BalanceTypeId": "5211",
                "DocumentTypeIds": [],
                "GameId": None
            }
            
            logger.info(f"DEBUG: Fetching withdrawal requests for user {user_id}")
            logger.info(f"DEBUG: Date range: {date_from.strftime('%d-%m-%y')} to {date_to.strftime('%d-%m-%y')}")
            logger.info(f"DEBUG: Using token: {self.api_settings.get('token', 'NOT_SET')[:20]}...")
            
            # Make request
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            logger.info(f"DEBUG: API Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"DEBUG: API Response Keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                
                if not data.get("HasError") and "Data" in data:
                    # Try different possible response structures
                    transactions = []
                    if isinstance(data["Data"], dict):
                        if "ClientRequests" in data["Data"]:
                            transactions = data["Data"]["ClientRequests"]
                            logger.info(f"DEBUG: Found {len(transactions)} transactions in ClientRequests")
                        elif "Objects" in data["Data"]:
                            transactions = data["Data"]["Objects"]
                            logger.info(f"DEBUG: Found {len(transactions)} transactions in Objects")
                    elif isinstance(data["Data"], list):
                        transactions = data["Data"]
                        logger.info(f"DEBUG: Found {len(transactions)} transactions in Data list")
                    
                    
                    # Filter for withdrawal requests
                    withdrawal_requests = [
                        tx for tx in transactions 
                        if tx.get("DocumentTypeName") == "Çekim Talebi"
                    ]
                    
                    logger.info(f"DEBUG: Found {len(withdrawal_requests)} withdrawal requests")
                    
                    if withdrawal_requests:
                        # Sort by creation date (most recent first)
                        withdrawal_requests.sort(key=lambda x: x.get('Created', ''), reverse=True)
                        latest_request = withdrawal_requests[0]
                        logger.info(f"DEBUG: Latest withdrawal request: Amount={latest_request.get('Amount')}, Date={latest_request.get('Created')}")
                        return latest_request
                    else:
                        logger.warning(f"DEBUG: No withdrawal requests found for user {user_id}")
                        return None
                else:
                    logger.error(f"DEBUG: API Error - HasError: {data.get('HasError')}, Data present: {'Data' in data}")
                    return None
            else:
                logger.error(f"DEBUG: API request failed with status {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching withdrawal request for user {user_id}: {str(e)}")
            return None

    def parse_api_datetime(self, date_str):
        """API tarih formatını parse et"""
        try:
            if not date_str:
                return None
            
            # Timezone bilgisini kaldır
            clean_date = date_str.split('+')[0]
            
            # Farklı formatları dene
            try:
                return datetime.strptime(clean_date, '%Y-%m-%dT%H:%M:%S.%f')
            except ValueError:
                return datetime.strptime(clean_date, '%Y-%m-%dT%H:%M:%S')
        except Exception as e:
            logger.error(f"Date parsing error for '{date_str}': {e}")
            return None

    def format_turkish_currency(self, amount):
        """Türk Lirası formatı"""
        try:
            if not amount or amount == 0:
                return "0,00 TL"
            
            formatted = f"{float(amount):,.2f} TL"
            return formatted.replace(",", "X").replace(".", ",").replace("X", ".")
        except:
            return "0,00 TL"

    def fetch_user_data(self, user_ids):
        """Kullanıcı verilerini çek"""
        user_data_list = []
        
        for user_id in user_ids:
            try:
                # Ana kullanıcı bilgilerini çek
                headers = dict(self.api_settings["headers"])
                url = self.api_settings["api_url"].format(user_id.strip())
                
                response = requests.get(url, headers=headers)
                
                if response.status_code == 401:
                    # Authorization header ekleyerek tekrar dene
                    headers["Authorization"] = f"Bearer {self.kpi_api_key}"
                    response = requests.get(url, headers=headers)
                
                if response.status_code == 200:
                    try:
                        user_data = response.json()
                        data = user_data.get('Data', {})
                        
                        # Debug: API yanıtını logla
                        logger.info(f"API Response for ID {user_id}: {data}")
                        
                    except Exception as e:
                        logger.error(f"JSON parse error for ID {user_id}: {e}")
                        data = {}
                    
                    # KPI verilerini çek
                    kpi_dep_amt = None
                    kpi_wd_amt = None
                    kpi_last_dep = None
                    
                    try:
                        kpi_url = self.api_settings["kpi_url"].format(user_id.strip())
                        kpi_response = requests.get(kpi_url, headers=headers)
                        
                        if kpi_response.status_code == 200:
                            kpi_json = kpi_response.json()
                            kpi_data = kpi_json.get('Data', {})
                            
                            # Debug: KPI yanıtını logla
                            logger.info(f"KPI Response for ID {user_id}: {kpi_data}")
                            
                            kpi_dep_amt = kpi_data.get('DepositAmount') or kpi_data.get('TotalDeposit') or 0
                            kpi_wd_amt = kpi_data.get('WithdrawalAmount') or kpi_data.get('TotalWithdrawal') or 0
                            kpi_last_dep = (kpi_data.get('LastDepositTimeLocal') or 
                                          kpi_data.get('LastDepositTime') or 
                                          kpi_data.get('LastDepositDateLocal') or 
                                          kpi_data.get('LastDepositDate') or 'Bilinmiyor')
                        else:
                            logger.warning(f"KPI API error for ID {user_id}: {kpi_response.status_code}")
                    except Exception as e:
                        logger.error(f"KPI fetch error for ID {user_id}: {e}")
                    
                    # Verileri formatla - null/empty değerleri kontrol et
                    first_name = data.get('FirstName', '').strip() if data.get('FirstName') else ''
                    last_name = data.get('LastName', '').strip() if data.get('LastName') else ''
                    full_name = f"{first_name} {last_name}".strip() or 'Bilinmiyor'
                    
                    user_info = {
                        'ID': user_id,
                        'Kullanıcı Adı': data.get('Login') or 'Bilinmiyor',
                        'İsim': full_name,
                        'Telefon': data.get('Phone') or 'Bilinmiyor',
                        'E-posta': data.get('Email') or 'Bilinmiyor',
                        'Doğum Tarihi': self.fmt_dt(data.get('BirthDate')) if data.get('BirthDate') else 'Bilinmiyor',
                        'Partner': data.get('PartnerName') or 'Bilinmiyor',
                        'Bakiye': f"{data.get('Balance', 0)} {data.get('CurrencyId', 'TRY')}",
                        'Kayıt Tarihi': self.fmt_dt(data.get('CreatedLocalDate')) if data.get('CreatedLocalDate') else 'Bilinmiyor',
                        'Son Giriş': self.fmt_dt(data.get('LastLoginLocalDate')) if data.get('LastLoginLocalDate') else 'Bilinmiyor',
                        'Son Para Yatırma': self.fmt_dt(data.get('LastDepositDateLocal')) if data.get('LastDepositDateLocal') else 'Bilinmiyor',
                        'Son Casino Bahis': self.fmt_dt(data.get('LastCasinoBetTimeLocal')) if data.get('LastCasinoBetTimeLocal') else 'Bilinmiyor',
                        'Toplam Yatırım': (self.fmt_tl(kpi_dep_amt) if kpi_dep_amt is not None and kpi_dep_amt > 0 else 'Bilinmiyor'),
                        'Toplam Çekim': (self.fmt_tl(kpi_wd_amt) if kpi_wd_amt is not None and kpi_wd_amt > 0 else 'Bilinmiyor'),
                        'Son Yatırım': (self.fmt_dt(kpi_last_dep) if kpi_last_dep and kpi_last_dep != 'Bilinmiyor' else 'Bilinmiyor'),
                    }
                    user_data_list.append(user_info)
                    
                else:
                    # Hata durumu
                    user_info = {
                        'ID': user_id,
                        'Kullanıcı Adı': 'HATA',
                        'İsim': f'API yanıt kodu: {response.status_code}',
                        'Telefon': 'Bilinmiyor',
                        'E-posta': 'Bilinmiyor',
                        'Doğum Tarihi': 'Bilinmiyor',
                        'Partner': 'Bilinmiyor',
                        'Bakiye': 'Bilinmiyor',
                        'Kayıt Tarihi': 'Bilinmiyor',
                        'Son Giriş': 'Bilinmiyor',
                        'Son Para Yatırma': 'Bilinmiyor',
                        'Son Casino Bahis': 'Bilinmiyor',
                        'Toplam Yatırım': 'Bilinmiyor',
                        'Toplam Çekim': 'Bilinmiyor',
                        'Son Yatırım': 'Bilinmiyor',
                    }
                    user_data_list.append(user_info)
                    
            except Exception as e:
                # Bağlantı hatası
                user_info = {
                    'ID': user_id,
                    'Kullanıcı Adı': 'HATA',
                    'İsim': f'Bağlantı hatası: {str(e)}',
                    'Telefon': 'Bilinmiyor',
                    'E-posta': 'Bilinmiyor',
                    'Doğum Tarihi': 'Bilinmiyor',
                    'Partner': 'Bilinmiyor',
                    'Bakiye': 'Bilinmiyor',
                    'Kayıt Tarihi': 'Bilinmiyor',
                    'Son Giriş': 'Bilinmiyor',
                    'Son Para Yatırma': 'Bilinmiyor',
                    'Son Casino Bahis': 'Bilinmiyor',
                    'Toplam Yatırım': 'Bilinmiyor',
                    'Toplam Çekim': 'Bilinmiyor',
                    'Son Yatırım': 'Bilinmiyor',
                }
                user_data_list.append(user_info)
        
        return user_data_list

    def create_excel_file(self, user_data_list):
        """Excel dosyası oluştur"""
        try:
            df = pd.DataFrame(user_data_list)
            
            # Kolon sırası
            preferred = [
                'ID','Kullanıcı Adı','İsim','Telefon','E-posta','Bakiye','Son Giriş',
                'Toplam Yatırım','Toplam Çekim','Son Yatırım',
                'Kayıt Tarihi','Doğum Tarihi','Partner','Son Para Yatırma','Son Casino Bahis'
            ]
            cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
            df = df.loc[:, cols]
            
            # Excel dosyasını memory'de oluştur
            output = BytesIO()
            
            try:
                # XlsxWriter ile şık formatla
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    sheet_name = 'Kullanıcılar'
                    df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1, header=False)
                    workbook = writer.book
                    worksheet = writer.sheets[sheet_name]

                    # Biçimler
                    header_fmt = workbook.add_format({
                        'bold': True,
                        'text_wrap': True,
                        'valign': 'vcenter',
                        'align': 'center',
                        'bg_color': '#1E88E5',
                        'font_color': '#FFFFFF',
                        'border': 1
                    })
                    cell_fmt = workbook.add_format({
                        'align': 'center',
                        'valign': 'vcenter',
                        'border': 1
                    })

                    # Tablo ekle
                    nrows, ncols = df.shape
                    columns = [{'header': col, 'header_format': header_fmt} for col in df.columns]

                    worksheet.add_table(0, 0, nrows+1, ncols-1, {
                        'style': 'Table Style Medium 9',
                        'columns': columns
                    })

                    # Sütun genişlikleri
                    for idx, col in enumerate(df.columns):
                        maxlen = max([len(str(col))] + [len(str(x)) for x in df[col].astype(str).tolist()])
                        width = min(60, max(12, maxlen + 2))
                        worksheet.set_column(idx, idx, width, cell_fmt)

                    # Başlık satırını sabitle
                    worksheet.freeze_panes(1, 0)
                    
            except Exception:
                # XlsxWriter yoksa basit Excel
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Kullanıcılar')
            
            output.seek(0)
            return output
            
        except Exception as e:
            logger.error(f"Excel oluşturma hatası: {e}")
            return None

    async def withdrawals_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bekleyen çekim taleplerini göster"""
        try:
            pending_withdrawals = self.get_pending_withdrawals()
            
            if not pending_withdrawals:
                await update.message.reply_text(
                    "✅ Şu anda bekleyen çekim talebi bulunmuyor."
                )
                return
            
            message = f"💰 **Bekleyen Çekim Talepleri ({len(pending_withdrawals)})**\n\n"
            
            for i, withdrawal in enumerate(pending_withdrawals[:10]):  # Son 10 tane
                message += f"**{i+1}.** "
                message += f"⏰ {self.fmt_dt(withdrawal['timestamp'])}\n"
                message += f"📋 Method: {withdrawal['method']}\n"
                message += f"📊 Veri: {str(withdrawal['data'])[:100]}...\n\n"
            
            if len(pending_withdrawals) > 10:
                message += f"... ve {len(pending_withdrawals) - 10} tane daha"
            
            await update.message.reply_text(
                message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Withdrawals command error: {e}")
            await update.message.reply_text(
                "❌ Çekim talepleri getirilirken hata oluştu."
            )
    
    async def signalr_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """SignalR bağlantı durumunu göster"""
        try:
            if self.signalr_client and self.signalr_client.signalr_client.is_connected:
                status = "🟢 **Bağlı** - Real-time bildirimler aktif"
            else:
                status = "🔴 **Bağlantı Yok** - Polling modunda çalışıyor"
            
            pending_count = len(self.get_pending_withdrawals())
            
            message = f"📡 **SignalR Durumu**\n\n"
            message += f"Bağlantı: {status}\n"
            message += f"💰 Bekleyen Çekim: {pending_count} adet\n"
            message += f"📊 Toplam Bildirim: {len(self.withdrawal_notifications)} adet"
            
            await update.message.reply_text(
                message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"SignalR status command error: {e}")
            await update.message.reply_text(
                "❌ SignalR durumu kontrol edilirken hata oluştu."
            )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start komutu"""
        welcome_text = """
🤖 **KPI Bot'a Hoş Geldiniz!**

Bu bot, kullanıcı KPI verilerini çekmenize yardımcı olur.

📋 **Kullanım:**
• `id 201190504` - Tek kullanıcı KPI'sı
• `id 9470204, 9436169, 9220936` - Çoklu kullanıcı Excel raporu
• `kadı johndoe` - Kullanıcı adıyla arama
• `/fraud 201190504` - Fraud raporu oluştur
• `/şifretc selimyunus01` - TC şifre değiştir

🔍 **Kullanıcı Adı Arama:**
`kadı` komutu ile kullanıcı adına göre arama yapabilir ve detaylı bilgileri görüntüleyebilirsiniz.

🚨 **Fraud Raporu:**
`fraud` komutu ile kullanıcı ID'sine göre detaylı fraud analizi raporu oluşturabilirsiniz.

🔐 **TC Şifre Değiştirme:**
`/şifretc` komutu ile üyenin TC numarasını yeni şifre olarak ayarlayabilirsiniz.

📊 **Excel Raporu:**
Birden fazla ID girdiğinizde otomatik olarak Excel raporu oluşturulur.

❓ Yardım için: /help
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help komutu"""
        help_text = """
🤖 **KPI Bot Yardım**

📋 **Kullanılabilir Komutlar:**

🔍 **Kullanıcı Adı Arama:**
• `kadı johndoe` - Kullanıcı adıyla detaylı arama

📊 **ID ile Sorgu:**
• `id 201190504` - Tek kullanıcı KPI'sı
• `id 9470204, 9436169` - Çoklu kullanıcı Excel raporu

🚨 **Fraud Raporu:**
• `fraud 201190504` - Detaylı fraud analizi raporu

🔐 **TC Şifre Değiştirme:**
• `/şifretc selimyunus01` - Üye TC'si ile şifre değiştir

❓ **Diğer Komutlar:**
• `/start` - Bot'u başlat
• `/help` - Bu yardım mesajı

📝 **Örnekler:**
```
kadı testuser
id 201190504
fraud 201190504
/şifretc selimyunus01
```

💡 **İpuçları:**
- Kullanıcı adı araması detaylı bilgi verir
- ID sorgusu Excel dosyası oluşturur
- Fraud raporu kapsamlı analiz sağlar
- TC şifre değiştirme üyenin TC'sini yeni şifre yapar
    """
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bot başlatma komutu"""
        keyboard = [[InlineKeyboardButton("📊 KPI Sorgusu", callback_data='kpi_query')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🤖 **BetConstruct KPI Bot'a Hoş Geldiniz!**\n\n"
            "📊 Bu bot ile kullanıcı KPI verilerini sorgulayabilirsiniz.\n\n"
            "**Kullanım:**\n"
            "• Tek ID: `12345`\n"
            "• Çoklu ID: `12345,67890,11111`\n"
            "• Username: `@kullaniciadi`\n\n"
            "**Komutlar:**\n"
            "/start - Bot'u başlat\n"
            "/help - Yardım menüsü\n"
            "/withdrawals - Bekleyen çekim talepleri\n"
            "/signalr - SignalR bağlantı durumu\n\n"
            "💡 ID'leri virgülle ayırarak gönderebilirsiniz.\n\n"
            "🔔 **Real-time Bildirimler Aktif!**\n"
            "Yeni çekim talepleri anında bildirilecek.",
            "9220936\n"
            "```\n\n"
            "🔍 Kullanıcı adı araması detaylı bilgi verir\n"
            "📋 ID sorgusu Excel dosyası oluşturur",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def kpi_query_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """KPI sorgusu butonu callback"""
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "🤖 BetConstruct KPI Bot'a Hoş Geldiniz!\n\n"
            "Merhaba! Size KPI verilerini sorgulamada yardımcı olabilirim.\n\n"
            "Kullanım:\n"
            "• Mesajınızı 'id' ile başlatın\n\n"
            "📝 Örnekler:\n"
            "• Tek ID: `id 201190504`\n"
            "• Çoklu ID:\n"
            "```\n"
            "id 9470204\n"
            "9436169\n"
            "9220936\n"
            "9089661\n"
            "1886573848\n"
            "```\n\n"
            "📋 KPI verilerini Excel dosyası olarak alacaksınız!\n\n"
            "Lütfen 'id' ile başlayan mesajınızı gönderin:",
            parse_mode='Markdown'
        )

    async def handle_username_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Kullanıcı adı arama işleyici"""
        text = update.message.text.strip()
        user = update.effective_user
        
        # 'kadı' tetikleyicisi kontrolü
        if not text.lower().startswith('kadı'):
            return
        
        # 'kadı' kelimesini kaldır ve kullanıcı adını al
        username_text = text[4:].strip()  # 'kadı' kelimesini kaldır
        
        if not username_text:
            await update.message.reply_text(
                "❌ Kullanıcı adı belirtilmedi.\n\n"
                "📝 Doğru format:\n"
                "• `kadı johndoe`\n"
                "• `kadı testuser123`",
                parse_mode='Markdown'
            )
            return
        
        # İşlem başladı mesajı
        processing_msg = await update.message.reply_text(
            f"🔄 '{username_text}' kullanıcı adı aranıyor...\n"
            "Lütfen bekleyin..."
        )
        
        start_time = time.time()
        
        try:
            # Kullanıcı adına göre ara
            users = self.search_user_by_username(username_text)
            
            if not users:
                await processing_msg.edit_text(
                    f"❌ '{username_text}' kullanıcı adı bulunamadı!\n\n"
                    "Lütfen kullanıcı adını kontrol edin ve tekrar deneyin."
                )
                return
            
            # İlk bulunan kullanıcıyı al
            found_user = users[0]
            user_id = found_user.get('Id')
            
            if not user_id:
                await processing_msg.edit_text("❌ Kullanıcı ID'si alınamadı.")
                return
            
            # Kullanıcı verilerini çek
            user_data = await self.fetch_single_user_detailed(user_id)
            
            if not user_data:
                await processing_msg.edit_text("❌ Kullanıcı verileri çekilemedi.")
                return
            
            # Formatlanmış yanıt oluştur
            try:
                response = self.format_user_response(user_data)
                await processing_msg.edit_text(response, parse_mode='Markdown')
            except Exception as format_error:
                logger.error(f"Response formatting error: {format_error}")
                # Markdown olmadan gönder
                response = self.format_user_response(user_data)
                await processing_msg.edit_text(response)
            
            # Log kaydet
            response_time = time.time() - start_time
            self.log_query(user.id, user.username or user.first_name, [str(user_id)], response_time)
            
        except Exception as e:
            logger.error(f"Username search error: {e}")
            await processing_msg.edit_text(f"❌ Bir hata oluştu: {str(e)}")

    async def handle_fraud_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Fraud raporu arama işleyici"""
        text = update.message.text.strip()
        user = update.effective_user
        
        # 'fraud' tetikleyicisi kontrolü
        if not text.lower().startswith('fraud'):
            return
        
        # 'fraud' kelimesini kaldır ve user ID'yi al
        user_id_text = text[5:].strip()  # 'fraud' kelimesini kaldır
        
        if not user_id_text:
            await update.message.reply_text(
                "❌ Kullanıcı ID'si belirtilmedi.\n\n"
                "📝 Doğru format:\n"
                "• `fraud 201190504`\n"
                "• `fraud 9470204`",
                parse_mode='Markdown'
            )
            return
        
        if not user_id_text.isdigit():
            await update.message.reply_text(
                "❌ Geçerli bir kullanıcı ID'si girin!\n\n"
                "📝 Örnek: `fraud 201190504`",
                parse_mode='Markdown'
            )
            return
        
        # İşlem başladı mesajı
        processing_msg = await update.message.reply_text(
            f"🚨 Kullanıcı ID: {user_id_text} için fraud raporu hazırlanıyor...\n"
            "Bu işlem biraz zaman alabilir, lütfen bekleyin..."
        )
        
        start_time = time.time()
        
        try:
            # Fraud raporu oluştur
            fraud_report = await self.create_fraud_report(user_id_text)
            
            if fraud_report:
                # Raporu mesaj olarak gönder
                await processing_msg.edit_text(
                    f"🚨 **Fraud Raporu**\n\n```\n{fraud_report}\n```",
                    parse_mode='Markdown'
                )
                
                # Log kaydet
                response_time = time.time() - start_time
                self.log_query(user.id, user.username or user.first_name, [user_id_text], response_time)
            else:
                await processing_msg.edit_text("❌ Fraud raporu oluşturulamadı.")
                
        except Exception as e:
            logger.error(f"Fraud report error: {e}")
            await processing_msg.edit_text(f"❌ Bir hata oluştu: {str(e)}")

    async def create_fraud_report(self, user_id):
        """Fraud raporu oluştur"""
        try:
            # Kullanıcı verilerini çek
            user_data = await self.fetch_single_user_detailed(user_id)
            
            # Çekim talebi bilgilerini getir
            withdrawal_request = await self.fetch_latest_withdrawal_request(user_id)
            
            # Çevrim analizi yap
            turnover_analysis = await self.get_turnover_analysis(user_id)
            
            # Talep bilgileri - withdrawal_request'den al
            if not withdrawal_request:
                logger.error(f"FRAUD DEBUG: No withdrawal request found for user {user_id}")
            else:
                logger.info(f"FRAUD DEBUG: Found withdrawal request for user {user_id}: {withdrawal_request}")
            
            if not user_data:
                return None
            
            user = user_data.get('user', {})
            kpi = user_data.get('kpi', {})
            
            # Login verilerini çek
            login_data = self.fetch_client_logins(user_id)
            
            # Temel bilgiler - Soyisim İsim formatında
            first_name = user.get('FirstName', '').strip()
            last_name = user.get('LastName', '').strip()
            full_name = f"{last_name} {first_name}".strip()
            username = user.get('Login', user.get('UserName', 'Bilinmiyor'))
            current_balance = float(user.get('Balance', 0))
            
            # KPI verileri - doğru field adlarını kullan
            total_deposits = float(kpi.get('DepositAmount', kpi.get('TotalDeposit', 0)))
            total_withdrawals = float(kpi.get('WithdrawalAmount', kpi.get('TotalWithdrawal', 0)))
            withdrawal_count = int(kpi.get('WithdrawalCount', 0))
            deposit_count = int(kpi.get('DepositCount', 0))
            last_deposit = float(kpi.get('LastDepositAmount', 0))
            
            # Aktivite analizi
            last_casino_bet = kpi.get('LastCasinoBetTime', '')
            last_sport_bet = kpi.get('LastSportBetTime', '')
            last_login = user.get('LastLoginLocalDate', '')
            
            # Oyun türü belirleme
            game_type = "Bilinmiyor"
            game_status = "Bilinmiyor"
            
            try:
                casino_active = False
                sport_active = False
                
                if last_casino_bet:
                    casino_time = self.parse_api_datetime(last_casino_bet)
                    if casino_time:
                        casino_active = (datetime.now() - casino_time).days < 30
                
                if last_sport_bet:
                    sport_time = self.parse_api_datetime(last_sport_bet)
                    if sport_time:
                        sport_active = (datetime.now() - sport_time).days < 30
                
                if casino_active and sport_active:
                    game_type = "Casino & Spor"
                elif casino_active:
                    game_type = "Casino"
                elif sport_active:
                    game_type = "Spor Bahis"
                else:
                    game_type = "Pasif"
                
                # Oyuna devam durumu
                if last_login:
                    login_time = self.parse_api_datetime(last_login)
                    if login_time:
                        days_diff = (datetime.now() - login_time).days
                        game_status = "Evet" if days_diff <= 3 else "Hayır"
                
            except Exception as e:
                logger.error(f"Activity analysis error: {e}")
            
            # Aktivite süresi hesaplama
            active_days = -1
            if last_login:
                login_time = self.parse_api_datetime(last_login)
                reg_date = self.parse_api_datetime(user.get('RegistrationDate', ''))
                
                if login_time and reg_date:
                    active_days = (login_time - reg_date).days
                elif login_time:
                    active_days = (datetime.now() - login_time).days
            
            # Login analizi
            avg_daily_play = 0.0
            ip_changes = 0
            most_active_hour = "Bilinmiyor"
            most_used_device = "Bilinmiyor"
            avg_session_duration = 0.0
            most_active_period = "Bilinmiyor"
            
            if login_data:
                # IP analizi
                unique_ips = set()
                thirty_days_ago = datetime.now() - timedelta(days=30)
                
                login_hours = []
                session_durations = []
                device_sources = {}
                
                for login in login_data:
                    try:
                        start_time = self.parse_api_datetime(login.get('StartTime', ''))
                        if start_time and start_time >= thirty_days_ago:
                            unique_ips.add(login.get('LoginIP', ''))
                            login_hours.append(start_time.hour)
                            
                            # Cihaz analizi
                            source = login.get('SourceName', 'Bilinmiyor')
                            device_sources[source] = device_sources.get(source, 0) + 1
                            
                            # Session süresi
                            end_time = self.parse_api_datetime(login.get('EndTime', ''))
                            if end_time:
                                duration = (end_time - start_time).total_seconds() / 3600
                                session_durations.append(duration)
                    except:
                        continue
                
                ip_changes = len(unique_ips)
                
                # En yoğun saat
                if login_hours:
                    hour_counts = {}
                    for hour in login_hours:
                        hour_counts[hour] = hour_counts.get(hour, 0) + 1
                    most_active_hour_num, count = max(hour_counts.items(), key=lambda x: x[1])
                    most_active_hour = f"{most_active_hour_num}:00 ({count} kez)"
                
                # En çok kullanılan cihaz
                if device_sources:
                    most_used_device = max(device_sources.items(), key=lambda x: x[1])[0]
                
                # Ortalama session süresi
                if session_durations:
                    avg_session_duration = sum(session_durations) / len(session_durations)
                    avg_daily_play = avg_session_duration
                
                # Zaman dilimi analizi
                time_periods = {
                    (0, 6): "Gece",
                    (6, 12): "Sabah", 
                    (12, 18): "Öğleden sonra",
                    (18, 24): "Akşam"
                }
                
                period_counts = {period: 0 for period in time_periods.values()}
                for hour in login_hours:
                    for (start, end), period in time_periods.items():
                        if start <= hour < end:
                            period_counts[period] += 1
                            break
                
                if period_counts:
                    most_active_period = max(period_counts.items(), key=lambda x: x[1])[0]
            
            # Detaylı analiz metni
            game_desc = f"- Ağırlıklı {game_type.lower()} oyuncusu\n"
            game_desc += f"- Ortalama günlük oyun süresi: {avg_daily_play:.1f} saat\n"
            game_desc += f"- Son 30 günde {ip_changes} farklı IP kullanımı\n"
            
            if game_type == "Casino":
                # Casino detayları
                if kpi.get('TotalCasinoStakes', 0) > 0:
                    game_desc += f"- Casino oyun oranı: %100\n"
                    
                    # Slot vs Live Casino
                    slot_stakes = float(kpi.get('TotalSlotStakes', 0))
                    live_stakes = float(kpi.get('TotalLiveCasinoStakes', 0))
                    
                    if slot_stakes > live_stakes:
                        game_desc += "- Ağırlıklı Slot oyunları\n"
                    else:
                        game_desc += "- Ağırlıklı Live Casino\n"
            
            game_desc += f"- En yoğun giriş saatleri: {most_active_hour}\n"
            game_desc += f"- En çok kullanılan cihaz: {most_used_device}\n"
            game_desc += f"- Ortalama oturum süresi: {avg_session_duration:.1f} saat\n"
            game_desc += f"- Aktiflik süresi: {active_days} gün\n"
            game_desc += f"- En çok aktif zaman dilimi: {most_active_period}"
            
            # Talep bilgileri - withdrawal_request'den al
            if withdrawal_request:
                request_amount = self.format_turkish_currency(withdrawal_request.get("Amount", 0))
                payment_system = withdrawal_request.get("PaymentSystemName", "Bilinmiyor")
                # Payment system adını Türkçe'ye çevir
                payment_method_map = {
                    "BankTransferBME": "Banka Havalesi",
                    "HedefHavale": "HedefHavale",
                    "PapparaTransfer": "Papara",
                    "CreditCard": "Kredi Kartı"
                }
                request_method = payment_method_map.get(payment_system, payment_system)
                logger.info(f"DEBUG: Found withdrawal request - Amount: {withdrawal_request.get('Amount')}, PaymentSystem: {payment_system}")
            else:
                request_amount = "Talep bulunamadı"
                request_method = "Bilinmiyor"
                logger.warning(f"DEBUG: No withdrawal request found for user {user_id}")
            
            # Rapor formatı - tam format
            report = f"""İsim Soyisim   : {full_name.strip()}
K. Adı         : {username}
Talep Miktarı  : {request_amount}
Talep yöntemi  : {request_method}
Yatırım Miktarı : {self.format_turkish_currency(last_deposit)}
Oyun Türü      : {game_type}
Arka Bakiye    : {self.format_turkish_currency(current_balance)}
Oyuna Devam    : {game_status}

T. Yatırım Miktarı: {self.format_turkish_currency(total_deposits)}
T. Çekim Miktarı  : {self.format_turkish_currency(total_withdrawals)}
T. Çekim Adedi    : {withdrawal_count}
T. Yatırım Adedi  : {deposit_count}
Açıklama          : {turnover_analysis}"""
            
            return report
            
        except Exception as e:
            logger.error(f"Fraud report creation error: {e}")
            return None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mesaj işleyici - 'id', 'kadı', 'fraud' ve 'şifretc' ile başlayan mesajları işler"""
        text = update.message.text.strip()
        user = update.effective_user
        
        # 'kadı' tetikleyicisi kontrolü
        if text.lower().startswith('kadı'):
            await self.handle_username_search(update, context)
            return
        
        # 'fraud' tetikleyicisi kontrolü
        if text.lower().startswith('fraud'):
            await self.handle_fraud_search(update, context)
            return
        
        # 'şifretc' tetikleyicisi kontrolü
        if text.lower().startswith('şifretc'):
            await self.handle_tc_password_change(update, context)
            return
        
        # 'id' tetikleyicisi kontrolü - sessizce çık
        if not text.lower().startswith('id'):
            return
        
        # 'id' kelimesini kaldır ve ID'leri parse et
        id_text = text[2:].strip()  # 'id' kelimesini kaldır
        user_ids = []
        
        # Virgülle ayrılmış ID'ler
        if ',' in id_text:
            user_ids = [id.strip() for id in id_text.split(',') if id.strip() and id.strip().isdigit()]
        else:
            # Satır satır ID'ler
            user_ids = [id.strip() for id in id_text.split('\n') if id.strip() and id.strip().isdigit()]
        
        if not user_ids:
            await update.message.reply_text(
                "❌ Geçerli kullanıcı ID'si bulunamadı.\n\n"
                "📝 Doğru format:\n"
                "• `id 201190504`\n"
                "• `id 9470204, 9436169, 9220936`\n"
                "• Çoklu satır:\n"
                "```\n"
                "id 9470204\n"
                "9436169\n"
                "9220936\n"
                "```",
                parse_mode='Markdown'
            )
            return
        
        # İşlem başladı mesajı
        processing_msg = await update.message.reply_text(
            f"🔄 {len(user_ids)} kullanıcı için KPI verileri çekiliyor...\n"
            "Lütfen bekleyin..."
        )
        
        start_time = time.time()
        
        try:
            # Verileri çek
            user_data_list = self.fetch_user_data(user_ids)
            
            if not user_data_list:
                await processing_msg.edit_text("❌ Veri çekilemedi. Lütfen daha sonra tekrar deneyin.")
                return
            
            # Excel dosyası oluştur
            excel_file = self.create_excel_file(user_data_list)
            
            if excel_file is None:
                await processing_msg.edit_text("❌ Excel dosyası oluşturulamadı.")
                return
            
            # Dosyayı gönder
            filename = f"kpi_raporu_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            
            await update.message.reply_document(
                document=excel_file,
                filename=filename,
                caption=f"📊 KPI Raporu\n\n"
                       f"📋 Toplam {len(user_data_list)} kullanıcı\n"
                       f"🕐 İşlem süresi: {time.time() - start_time:.2f} saniye\n"
                       f"📅 Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
            
            await processing_msg.delete()
            
            # Log kaydet
            response_time = time.time() - start_time
            self.log_query(user.id, user.username or user.first_name, user_ids, response_time)
            
        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {e}")
            await processing_msg.edit_text(f"❌ Bir hata oluştu: {str(e)}")

    def update_kpi_api_key(self, new_key):
        """KPI API anahtarını güncelle"""
        self.kpi_api_key = new_key
        self.api_settings["headers"]["Authentication"] = new_key

    async def run(self):
        """Bot'u çalıştır"""
        if not self.token:
            logger.error("Telegram token bulunamadı!")
            return
        
        try:
            self.application = Application.builder().token(self.token).build()
            
            # Komutları ekle
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("withdrawals", self.withdrawals_command))
            self.application.add_handler(CommandHandler("signalr", self.signalr_status_command))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.application.add_handler(CallbackQueryHandler(self.kpi_query_callback, pattern="kpi_query"))
            
            # Bot'u başlat
            await self.application.initialize()
            await self.application.start()
            
            self.is_running = True
            logger.info("Bot başlatıldı!")
            
            # SignalR client'ı başlat
            self.start_signalr_client()
            
            # Polling başlat
            await self.application.updater.start_polling(drop_pending_updates=True)
            
            # Bot çalışırken bekle
            while self.is_running:
                await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Bot çalıştırma hatası: {e}")
            self.is_running = False
        finally:
            # SignalR client'ı durdur
            self.stop_signalr_client()
            
            # Bot'u düzgün şekilde durdur
            if self.application:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

    async def start_bot(self):
        """Bot'u başlat"""
        try:
            if not self.token:
                logger.error("Telegram token bulunamadı!")
                return False
            
            # Application oluştur
            self.application = Application.builder().token(self.token).build()
            
            # Handler'ları ekle
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("şifretc", self.tc_password_command))
            self.application.add_handler(CallbackQueryHandler(self.kpi_query_callback, pattern="kpi_query"))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            # Bot'u başlat
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            
            self.is_running = True
            logger.info("Bot başarıyla başlatıldı!")
            return True
            
        except Exception as e:
            logger.error(f"Bot başlatma hatası: {e}")
            return False

    def get_client_info_by_login(self, username):
        """Üye bilgilerini Login ile GetClients endpoint'i ile al"""
        try:
            url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClients"
            
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "Authentication": self.kpi_api_key,
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            # Request body - sadece Login ile arama
            payload = {
                "Id": "",
                "FirstName": "",
                "LastName": "",
                "PersonalId": "",
                "Email": "",
                "Phone": "",
                "ZipCode": None,
                "AMLRisk": "",
                "AffilateId": None,
                "AffiliatePlayerType": None,
                "BTag": None,
                "BetShopGroupId": "",
                "BirthDate": None,
                "CashDeskId": None,
                "CasinoProfileId": None,
                "CasinoProfitnessFrom": None,
                "CasinoProfitnessTo": None,
                "City": "",
                "ClientCategory": None,
                "CurrencyId": None,
                "DocumentNumber": "",
                "ExternalId": "",
                "Gender": None,
                "IBAN": None,
                "IsEmailSubscribed": None,
                "IsLocked": None,
                "IsOrderedDesc": True,
                "IsSMSSubscribed": None,
                "IsSelfExcluded": None,
                "IsStartWithSearch": False,
                "IsTest": None,
                "IsVerified": None,
                "Login": username,
                "MaxBalance": None,
                "MaxCreatedLocal": None,
                "MaxCreatedLocalDisable": True,
                "MaxFirstDepositDateLocal": None,
                "MaxLastTimeLoginDateLocal": None,
                "MaxLastWrongLoginDateLocal": None,
                "MaxLoyaltyPointBalance": None,
                "MaxRows": 20,
                "MaxVerificationDateLocal": None,
                "MaxWrongLoginAttempts": None,
                "MiddleName": "",
                "MinBalance": None,
                "MinCreatedLocal": None,
                "MinCreatedLocalDisable": True,
                "MinFirstDepositDateLocal": None,
                "MinLastTimeLoginDateLocal": None,
                "MinLastWrongLoginDateLocal": None,
                "MinLoyaltyPointBalance": None,
                "MinVerificationDateLocal": None,
                "MinWrongLoginAttempts": None,
                "MobilePhone": "",
                "NickName": "",
                "OrderedItem": 1,
                "OwnerId": None,
                "PartnerClientCategoryId": None,
                "RegionId": None,
                "RegistrationSource": None,
                "SelectedPepStatuses": "",
                "SkeepRows": 0,
                "SportProfitnessFrom": None,
                "SportProfitnessTo": None,
                "Status": None,
                "Time": "",
                "TimeZone": ""
            }
            
            logger.info(f"Üye bilgileri sorgulanıyor: {username}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("HasError", True):
                    logger.error(f"API Hatası: {data.get('AlertMessage', 'Bilinmeyen hata')}")
                    return None
                    
                objects = data.get("Data", {}).get("Objects", [])
                
                if not objects:
                    logger.warning(f"Üye bulunamadı: {username}")
                    return None
                    
                client = objects[0]
                client_id = client.get("Id")
                doc_number = client.get("DocNumber")
                first_name = client.get("FirstName", "")
                last_name = client.get("LastName", "")
                
                logger.info(f"Üye bulundu: {first_name} {last_name} (ID: {client_id})")
                logger.info(f"TC Numarası: {doc_number}")
                
                return {
                    "client_id": client_id,
                    "doc_number": doc_number,
                    "first_name": first_name,
                    "last_name": last_name
                }
                
            else:
                logger.error(f"HTTP Hatası: {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("İstek zaman aşımına uğradı")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Bağlantı hatası: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Beklenmeyen hata: {str(e)}")
            return None
            
    def reset_client_password(self, client_id, new_password):
        """ResetPassword endpoint'i ile şifreyi değiştir"""
        try:
            url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/ResetPassword"
            
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "Authentication": self.kpi_api_key,
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            payload = {
                "ClientId": client_id,
                "Password": new_password
            }
            
            logger.info(f"Şifre değiştiriliyor... (Client ID: {client_id})")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("HasError", True):
                    logger.error(f"Şifre değiştirme hatası: {data.get('AlertMessage', 'Bilinmeyen hata')}")
                    return False
                    
                logger.info("✅ Şifre başarıyla TC numarası olarak değiştirildi!")
                return True
                
            else:
                logger.error(f"HTTP Hatası: {response.status_code}")
                return False
                
        except requests.exceptions.Timeout:
            logger.error("Şifre değiştirme isteği zaman aşımına uğradı")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Bağlantı hatası: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Beklenmeyen hata: {str(e)}")
            return False

    async def tc_password_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """TC şifre değiştirme komutu: /şifretc <üye_adı>"""
        try:
            # Komut argümanlarını kontrol et
            if not context.args:
                await update.message.reply_text(
                    "❌ Kullanım: /şifretc <üye_adı>\n\n"
                    "Örnek: /şifretc selimyunus01"
                )
                return
            
            username = context.args[0].strip()
            
            if not username:
                await update.message.reply_text("❌ Üye adı boş olamaz!")
                return
            
            # İşlem başladığını bildir
            processing_msg = await update.message.reply_text(
                f"🔄 İşlem başlatıldı...\n"
                f"👤 Üye: {username}\n"
                f"⏳ Lütfen bekleyin..."
            )
            
            # 1. Üye bilgilerini al
            client_info = self.get_client_info_by_login(username)
            
            if not client_info:
                await processing_msg.edit_text(
                    f"❌ İşlem başarısız!\n"
                    f"👤 Üye: {username}\n"
                    f"📋 Sonuç: Üye bulunamadı veya API hatası"
                )
                return
                
            client_id = client_info["client_id"]
            doc_number = client_info["doc_number"]
            first_name = client_info["first_name"]
            last_name = client_info["last_name"]
            
            if not doc_number or doc_number == "TEST HESABI":
                await processing_msg.edit_text(
                    f"❌ İşlem başarısız!\n"
                    f"👤 Üye: {first_name} {last_name}\n"
                    f"📋 Sonuç: Geçerli TC numarası bulunamadı"
                )
                return
                
            # 2. Şifreyi TC numarası olarak değiştir
            success = self.reset_client_password(client_id, doc_number)
            
            if success:
                await processing_msg.edit_text(
                    f"✅ Şifre başarıyla değiştirildi!\n\n"
                    f"👤 Üye: {first_name} {last_name}\n"
                    f"🆔 ID: {client_id}\n"
                    f"🔐 Yeni Şifre: {doc_number}\n\n"
                    f"🎉 İşlem tamamlandı!"
                )
            else:
                await processing_msg.edit_text(
                    f"❌ Şifre değiştirme başarısız!\n"
                    f"👤 Üye: {first_name} {last_name}\n"
                    f"📋 Sonuç: API hatası veya bağlantı sorunu"
                )
                
        except Exception as e:
            logger.error(f"TC şifre değiştirme komutu hatası: {e}")
            await update.message.reply_text(
                f"❌ Beklenmeyen hata oluştu!\n"
                f"🔧 Hata: {str(e)}"
            )

    async def handle_tc_password_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """TC şifre değiştirme komutu - 'şifretc username' formatında"""
        text = update.message.text.strip()
        user = update.effective_user
        
        # Komutu parse et
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Kullanım hatası!\n\n"
                "📝 Doğru format:\n"
                "`şifretc kullaniciadi`\n\n"
                "Örnek: `şifretc selimyunus01`",
                parse_mode='Markdown'
            )
            return
        
        username = parts[1].strip()
        
        # İşlem başladı mesajı
        processing_msg = await update.message.reply_text(
            f"🔄 {username} kullanıcısı için şifre TC ile değiştiriliyor...\n"
            "Lütfen bekleyin..."
        )
        
        try:
            # KPI API anahtarını al
            api_key = self.kpi_api_key
            if not api_key:
                await processing_msg.edit_text(
                    "❌ KPI API anahtarı bulunamadı!\n"
                    "Lütfen önce API anahtarını ayarlayın."
                )
                return
            
            # 1. Üye bilgilerini al
            client_info = await self.get_client_info_for_tc(username, api_key)
            
            if not client_info:
                await processing_msg.edit_text(
                    f"❌ Kullanıcı bulunamadı!\n"
                    f"👤 Aranan: {username}\n"
                    f"📋 Sonuç: Üye bilgileri alınamadı"
                )
                return
            
            client_id = client_info["client_id"]
            doc_number = client_info["doc_number"]
            first_name = client_info["first_name"]
            last_name = client_info["last_name"]
            
            if not doc_number or doc_number == "TEST HESABI":
                await processing_msg.edit_text(
                    f"❌ Geçerli TC numarası bulunamadı!\n"
                    f"👤 Üye: {first_name} {last_name}\n"
                    f"📋 TC: {doc_number or 'Boş'}"
                )
                return
            
            # 2. Şifreyi TC numarası olarak değiştir
            success = await self.reset_password_with_tc(client_id, doc_number, api_key)
            
            if success:
                await processing_msg.edit_text(
                    f"✅ Şifre başarıyla değiştirildi!\n\n"
                    f"👤 Üye: {first_name} {last_name}\n"
                    f"🆔 ID: {client_id}\n"
                    f"🔐 Yeni Şifre: {doc_number}\n\n"
                    f"🎉 İşlem tamamlandı!"
                )
            else:
                await processing_msg.edit_text(
                    f"❌ Şifre değiştirme başarısız!\n"
                    f"👤 Üye: {first_name} {last_name}\n"
                    f"📋 Sonuç: API hatası veya bağlantı sorunu"
                )
                
        except Exception as e:
            logger.error(f"TC şifre değiştirme komutu hatası: {e}")
            await processing_msg.edit_text(
                f"❌ Beklenmeyen hata oluştu!\n"
                f"🔧 Hata: {str(e)}"
            )

    async def get_client_info_for_tc(self, username, api_key):
        """TC şifre değiştirme için üye bilgilerini al - TC.py ile aynı API kullanımı"""
        try:
            # TC.py ile aynı URL ve header yapısı
            url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/GetClients"
            
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "Authentication": api_key,  # TC.py'deki gibi Authentication header
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            # TC.py ile aynı payload yapısı
            payload = {
                "Id": "",
                "FirstName": "",
                "LastName": "",
                "PersonalId": "",
                "Email": "",
                "Phone": "",
                "ZipCode": None,
                "AMLRisk": "",
                "AffilateId": None,
                "AffiliatePlayerType": None,
                "BTag": None,
                "BetShopGroupId": "",
                "BirthDate": None,
                "CashDeskId": None,
                "CasinoProfileId": None,
                "CasinoProfitnessFrom": None,
                "CasinoProfitnessTo": None,
                "City": "",
                "ClientCategory": None,
                "CurrencyId": None,
                "DocumentNumber": "",
                "ExternalId": "",
                "Gender": None,
                "IBAN": None,
                "IsEmailSubscribed": None,
                "IsLocked": None,
                "IsOrderedDesc": True,
                "IsSMSSubscribed": None,
                "IsSelfExcluded": None,
                "IsStartWithSearch": False,
                "IsTest": None,
                "IsVerified": None,
                "Login": username,  # Sadece bu alanı dolduruyoruz
                "MaxBalance": None,
                "MaxCreatedLocal": None,
                "MaxCreatedLocalDisable": True,
                "MaxFirstDepositDateLocal": None,
                "MaxLastTimeLoginDateLocal": None,
                "MaxLastWrongLoginDateLocal": None,
                "MaxLoyaltyPointBalance": None,
                "MaxRows": 20,
                "MaxVerificationDateLocal": None,
                "MaxWrongLoginAttempts": None,
                "MiddleName": "",
                "MinBalance": None,
                "MinCreatedLocal": None,
                "MinCreatedLocalDisable": True,
                "MinFirstDepositDateLocal": None,
                "MinLastTimeLoginDateLocal": None,
                "MinLastWrongLoginDateLocal": None,
                "MinLoyaltyPointBalance": None,
                "MinVerificationDateLocal": None,
                "MinWrongLoginAttempts": None,
                "MobilePhone": "",
                "NickName": "",
                "OrderedItem": 1,
                "OwnerId": None,
                "PartnerClientCategoryId": None,
                "RegionId": None,
                "RegistrationSource": None,
                "SelectedPepStatuses": "",
                "SkeepRows": 0,
                "SportProfitnessFrom": None,
                "SportProfitnessTo": None,
                "Status": None,
                "Time": "",
                "TimeZone": ""
            }
            
            logger.info(f"TC şifre değiştirme için üye bilgileri sorgulanıyor: {username}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("HasError", True):
                    logger.error(f"GetClients API hatası: {data.get('AlertMessage', 'Bilinmeyen hata')}")
                    return None
                
                # TC.py ile aynı response yapısı
                objects = data.get("Data", {}).get("Objects", [])
                
                if not objects:
                    logger.error(f"Kullanıcı bulunamadı: {username}")
                    return None
                
                client = objects[0]
                client_id = client.get("Id")
                doc_number = client.get("DocNumber")  # TC.py'de DocNumber
                first_name = client.get("FirstName", "")
                last_name = client.get("LastName", "")
                
                logger.info(f"Üye bulundu: {first_name} {last_name} (ID: {client_id})")
                logger.info(f"TC Numarası: {doc_number}")
                
                return {
                    "client_id": client_id,
                    "first_name": first_name,
                    "last_name": last_name,
                    "doc_number": doc_number,
                    "username": username
                }
            else:
                logger.error(f"GetClients HTTP hatası: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"get_client_info_for_tc hatası: {str(e)}")
            return None

    async def reset_password_with_tc(self, client_id, new_password, api_key):
        """TC numarası ile şifre sıfırlama - TC.py ile aynı API kullanımı"""
        try:
            # TC.py ile aynı URL ve header yapısı
            url = "https://backofficewebadmin.betconstruct.com/api/tr/Client/ResetPassword"
            
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "Authentication": api_key,  # TC.py'deki gibi Authentication header
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            # TC.py ile aynı payload yapısı
            payload = {
                "ClientId": client_id,
                "Password": new_password  # TC.py'de "Password" key'i kullanılıyor
            }
            
            logger.info(f"Şifre değiştiriliyor... (Client ID: {client_id})")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("HasError", True):
                    logger.error(f"Şifre değiştirme hatası: {data.get('AlertMessage', 'Bilinmeyen hata')}")
                    return False
                
                logger.info(f"✅ Şifre başarıyla TC numarası olarak değiştirildi! (Client ID: {client_id})")
                return True
            else:
                logger.error(f"ResetPassword HTTP hatası: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"reset_password_with_tc hatası: {str(e)}")
            return False

    async def stop_bot(self):
        """Bot'u durdur"""
        if self.application and self.is_running:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
                self.is_running = False
                logger.info("Bot durduruldu!")
                return True
            except Exception as e:
                logger.error(f"Bot durdurma hatası: {e}")
                return False
        return True

# Global bot instance
bot_instance = None

# Withdrawal listener global fonksiyonları
def start_withdrawal_listener():
    """Global withdrawal listener başlatma fonksiyonu"""
    global bot_instance
    if bot_instance:
        return bot_instance.start_withdrawal_listener()
    return False

def stop_withdrawal_listener():
    """Global withdrawal listener durdurma fonksiyonu"""
    global bot_instance
    if bot_instance:
        return bot_instance.stop_withdrawal_listener()
    return False

def get_withdrawal_listener_status():
    """Global withdrawal listener durum fonksiyonu"""
    global bot_instance
    if bot_instance:
        return bot_instance.get_withdrawal_listener_status()
    return {'is_running': False, 'is_connected': False, 'notifications_count': 0, 'last_check_time': None, 'processed_withdrawals_count': 0}

def get_withdrawal_notifications(limit=10):
    """Global withdrawal bildirimleri alma fonksiyonu"""
    global bot_instance
    if bot_instance:
        return bot_instance.get_withdrawal_notifications(limit)
    return []

def get_deposit_notifications(limit=10):
    """Global yatırım bildirimleri alma fonksiyonu"""
    global bot_instance
    if bot_instance and bot_instance.withdrawal_listener:
        return bot_instance.withdrawal_listener.deposit_notifications[-limit:]
    return []

def get_deposit_listener_status():
    """Global yatırım listener durum fonksiyonu"""
    global bot_instance
    if bot_instance and bot_instance.withdrawal_listener:
        return {
            'is_running': bot_instance.withdrawal_listener.is_running,
            'deposit_check_active': bot_instance.withdrawal_listener.deposit_check_thread and bot_instance.withdrawal_listener.deposit_check_thread.is_alive(),
            'notifications_count': len(bot_instance.withdrawal_listener.deposit_notifications),
            'last_check_time': bot_instance.withdrawal_listener.last_deposit_check,
            'processed_deposits_count': len(bot_instance.withdrawal_listener.last_processed_deposits)
        }
    return {'is_running': False, 'deposit_check_active': False, 'notifications_count': 0}

def update_telegram_chat_ids(chat_ids_str):
    """Telegram chat ID'lerini güncelle"""
    global bot_instance
    if bot_instance:
        try:
            chat_ids = [int(id.strip()) for id in chat_ids_str.split(',') if id.strip()]
            bot_instance.telegram_chat_ids = chat_ids
            if bot_instance.withdrawal_listener:
                bot_instance.withdrawal_listener.telegram_chat_ids = chat_ids
            logger.info(f"Telegram chat ID'leri güncellendi: {chat_ids}")
            return True
        except Exception as e:
            logger.error(f"Chat ID güncelleme hatası: {e}")
            return False
    return False

def run_bot():
    """Bot'u çalıştır"""
    global bot_instance
    bot_instance = KPIBot()
    
    async def main():
        await bot_instance.run()
        
        # Bot çalışırken bekle
        while bot_instance.is_running:
            await asyncio.sleep(1)
    
    asyncio.run(main())

def start_bot_thread():
    """Bot'u thread'de başlat"""
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    return bot_thread

def stop_bot():
    """Bot'u durdur"""
    global bot_instance
    if bot_instance:
        asyncio.run(bot_instance.stop_bot())

def get_bot_status():
    """Bot durumunu al"""
    global bot_instance
    return bot_instance.is_running if bot_instance else False

def update_api_key(new_key):
    """API anahtarını güncelle"""
    global bot_instance
    if bot_instance:
        bot_instance.update_kpi_api_key(new_key)

if __name__ == "__main__":
    run_bot()
