import streamlit as st
import threading
import time
import json
import sqlite3
from datetime import datetime
import requests
import asyncio
import sys
import os

# Streamlit sayfa ayarı
st.set_page_config(
    page_title="Telegram Bot Controller",
    page_icon="🤖",
    layout="wide"
)

# Başlık
st.title("🤖 Telegram Bot Kontrol Paneli")

# Hata yönetimi - aiohttp yoksa basit bir alternatif
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    st.warning("⚠️ aiohttp modülü kurulu değil. Bazı özellikler sınırlı olacaktır.")

try:
    from signalrcore.hub_connection_builder import HubConnectionBuilder
    SIGNALR_AVAILABLE = True
except ImportError:
    SIGNALR_AVAILABLE = False
    st.warning("⚠️ signalrcore modülü kurulu değil. SignalR özellikleri devre dışı.")

# Basit bir SignalR client (aiohttp yoksa)
class SimpleSignalRClient:
    def __init__(self):
        self.is_connected = False
        self.connection_token = None
        
    async def get_connection_token(self):
        """Basit token alma (aiohttp olmadan)"""
        try:
            # requests kullanarak token alma
            negotiate_url = "https://backofficewebadmin.betconstruct.com/signalr/negotiate?hub=commonnotificationhub"
            response = requests.post(negotiate_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.connection_token = data.get('ConnectionToken')
                return self.connection_token
        except Exception as e:
            st.error(f"Token alma hatası: {e}")
        return None

    async def start_connection(self):
        """Basit bağlantı (sadece simülasyon)"""
        self.is_connected = True
        return True

    async def stop(self):
        self.is_connected = False

# Bot durumu değişkenleri
if 'bot_running' not in st.session_state:
    st.session_state.bot_running = False
if 'withdrawal_listener_running' not in st.session_state:
    st.session_state.withdrawal_listener_running = False
if 'notifications' not in st.session_state:
    st.session_state.notifications = []
if 'api_key' not in st.session_state:
    st.session_state.api_key = ""
if 'telegram_chat_ids' not in st.session_state:
    st.session_state.telegram_chat_ids = ""

# Veritabanı başlatma
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            timestamp DATETIME,
            is_read BOOLEAN DEFAULT FALSE
        )
    ''')
    conn.commit()
    conn.close()

# Ayarları yükle
def load_settings():
    init_db()
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
    api_key_result = cursor.fetchone()
    if api_key_result:
        st.session_state.api_key = api_key_result[0]
    
    cursor.execute("SELECT value FROM settings WHERE key = 'telegram_chat_ids'")
    chat_ids_result = cursor.fetchone()
    if chat_ids_result:
        st.session_state.telegram_chat_ids = chat_ids_result[0]
    
    conn.close()

# Ayarları kaydet
def save_setting(key, value):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()

# Basit bot fonksiyonları (aiohttp olmadan)
def start_bot_thread():
    """Bot thread'ini başlat"""
    st.session_state.bot_running = True
    st.success("Bot başlatıldı! (Simülasyon modu)")

def stop_bot():
    """Bot'u durdur"""
    st.session_state.bot_running = False
    st.info("Bot durduruldu!")

def get_bot_status():
    """Bot durumunu döndür"""
    return {
        "running": st.session_state.bot_running,
        "status": "active" if st.session_state.bot_running else "inactive",
        "since": datetime.now().isoformat() if st.session_state.bot_running else None
    }

def update_api_key(new_key):
    """API key güncelle"""
    st.session_state.api_key = new_key
    save_setting('api_key', new_key)
    st.success("API key güncellendi!")

def start_withdrawal_listener():
    """Para çekme dinleyicisini başlat"""
    st.session_state.withdrawal_listener_running = True
    st.success("Para çekme dinleyicisi başlatıldı! (Simülasyon modu)")

def stop_withdrawal_listener():
    """Para çekme dinleyicisini durdur"""
    st.session_state.withdrawal_listener_running = False
    st.info("Para çekme dinleyicisi durduruldu!")

def get_withdrawal_listener_status():
    """Dinleyici durumunu döndür"""
    return {
        "running": st.session_state.withdrawal_listener_running,
        "status": "active" if st.session_state.withdrawal_listener_running else "inactive"
    }

def get_withdrawal_notifications():
    """Bildirimleri getir"""
    return st.session_state.notifications

def update_telegram_chat_ids(new_ids):
    """Telegram chat ID'leri güncelle"""
    st.session_state.telegram_chat_ids = new_ids
    save_setting('telegram_chat_ids', new_ids)
    st.success("Telegram Chat ID'leri güncellendi!")

# Ayarları yükle
load_settings()

# Sidebar
with st.sidebar:
    st.header("⚙️ Ayarlar")
    
    api_key = st.text_input(
        "API Key",
        value=st.session_state.api_key,
        type="password",
        help="Sistem API anahtarınız"
    )
    
    if st.button("API Key Kaydet"):
        update_api_key(api_key)
    
    telegram_chat_ids = st.text_area(
        "Telegram Chat ID'leri",
        value=st.session_state.telegram_chat_ids,
        help="Her satıra bir Telegram Chat ID yazın"
    )
    
    if st.button("Chat ID'leri Kaydet"):
        update_telegram_chat_ids(telegram_chat_ids)

# Ana içerik
tab1, tab2, tab3 = st.tabs(["🤖 Bot Kontrolü", "📊 Durum", "📨 Bildirimler"])

with tab1:
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Ana Bot Kontrolü")
        
        if st.button("🚀 Bot'u Başlat", type="primary", use_container_width=True):
            start_bot_thread()
        
        if st.button("🛑 Bot'u Durdur", use_container_width=True):
            stop_bot()
    
    with col2:
        st.subheader("Para Çekme Dinleyicisi")
        
        if st.button("👂 Dinleyiciyi Başlat", type="primary", use_container_width=True):
            start_withdrawal_listener()
        
        if st.button("🔇 Dinleyiciyi Durdur", use_container_width=True):
            stop_withdrawal_listener()

with tab2:
    st.subheader("Sistem Durumu")
    
    col1, col2 = st.columns(2)
    
    with col1:
        bot_status = get_bot_status()
        st.metric(
            "Ana Bot Durumu",
            "🟢 Çalışıyor" if bot_status["running"] else "🔴 Durdu",
            help=f"Son durum: {bot_status['since']}" if bot_status["since"] else ""
        )
    
    with col2:
        listener_status = get_withdrawal_listener_status()
        st.metric(
            "Dinleyici Durumu",
            "🟢 Çalışıyor" if listener_status["running"] else "🔴 Durdu"
        )
    
    # Modül durumları
    st.subheader("Modül Durumları")
    modul_col1, modul_col2 = st.columns(2)
    
    with modul_col1:
        st.metric("aiohttp", "🟢 Kurulu" if AIOHTTP_AVAILABLE else "🔴 Eksik")
    
    with modul_col2:
        st.metric("signalrcore", "🟢 Kurulu" if SIGNALR_AVAILABLE else "🔴 Eksik")
    
    if not AIOHTTP_AVAILABLE or not SIGNALR_AVAILABLE:
        st.warning("""
        ⚠️ Bazı modüller eksik. Tam fonksiyonellik için:
        ```bash
        pip install aiohttp signalrcore websockets
        ```
        """)

with tab3:
    st.subheader("Son Bildirimler")
    
    # Örnek bildirimler
    sample_notifications = [
        {"message": "✅ Para çekme talebi onaylandı - 1000 TRY", "time": "2 dakika önce"},
        {"message": "⏳ Para çekme talebi bekleniyor - 500 TRY", "time": "5 dakika önce"},
        {"message": "❌ Para çekme talebi reddedildi - 200 TRY", "time": "10 dakika önce"}
    ]
    
    for notif in sample_notifications:
        with st.expander(f"{notif['message']} - {notif['time']}"):
            st.write("Bildirim detayları burada gösterilecek")
            st.json({"amount": "1000 TRY", "status": "approved", "user": "user123"})

# Footer
st.divider()
st.caption("""
🤖 Bu kontrol paneli Telegram botunu yönetmek için tasarlanmıştır.
⏰ Son güncelleme: {}
""".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

# Streamlit Cloud için özel not
if os.environ.get('STREAMLIT_CLOUD'):
    st.sidebar.info("""
    **Streamlit Cloud Notu:**
    - Gerçek SignalR bağlantısı için aiohttp kurulumu gerekli
    - requirements.txt dosyasını güncellemeyi unutmayın
    """)
