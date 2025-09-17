import streamlit as st
import threading
import time
import sqlite3
import json
from datetime import datetime
import requests

# Streamlit sayfa ayarı
st.set_page_config(
    page_title="Telegram Bot Controller",
    page_icon="🤖",
    layout="wide"
)

# Başlık
st.title("🤖 Telegram Bot Kontrol Paneli")

# Basit bir bot sınıfı (aiohttp ve signalrcore olmadan)
class SimpleBot:
    def __init__(self):
        self.running = False
        self.listener_running = False
        self.thread = None
        self.listener_thread = None
        
    def start_bot(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._bot_worker)
            self.thread.daemon = True
            self.thread.start()
            return True
        return False
        
    def stop_bot(self):
        self.running = False
        return True
        
    def start_listener(self):
        if not self.listener_running:
            self.listener_running = True
            self.listener_thread = threading.Thread(target=self._listener_worker)
            self.listener_thread.daemon = True
            self.listener_thread.start()
            return True
        return False
        
    def stop_listener(self):
        self.listener_running = False
        return True
        
    def _bot_worker(self):
        """Basit bot çalışanı"""
        while self.running:
            try:
                # Simüle edilmiş bot aktivitesi
                time.sleep(5)
            except:
                pass
                
    def _listener_worker(self):
        """Basit dinleyici çalışanı"""
        while self.listener_running:
            try:
                # Simüle edilmiş dinleyici aktivitesi
                time.sleep(3)
            except:
                pass

# Global bot instance
bot = SimpleBot()

# Session state initialization
if 'bot_running' not in st.session_state:
    st.session_state.bot_running = False
if 'listener_running' not in st.session_state:
    st.session_state.listener_running = False
if 'api_key' not in st.session_state:
    st.session_state.api_key = ""
if 'telegram_chat_ids' not in st.session_state:
    st.session_state.telegram_chat_ids = ""
if 'notifications' not in st.session_state:
    st.session_state.notifications = []

# Veritabanı başlatma
def init_db():
    try:
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
    except Exception as e:
        st.error(f"Veritabanı hatası: {e}")

# Ayarları yükle
def load_settings():
    try:
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
    except Exception as e:
        st.error(f"Ayarlar yüklenirken hata: {e}")

# Ayarları kaydet
def save_setting(key, value):
    try:
        conn = sqlite3.connect('bot_data.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Ayar kaydedilirken hata: {e}")
        return False

# Bot fonksiyonları
def start_bot_thread():
    try:
        success = bot.start_bot()
        if success:
            st.session_state.bot_running = True
            st.success("✅ Bot başlatıldı!")
        else:
            st.warning("⚠️ Bot zaten çalışıyor!")
    except Exception as e:
        st.error(f"❌ Bot başlatılırken hata: {e}")

def stop_bot():
    try:
        success = bot.stop_bot()
        if success:
            st.session_state.bot_running = False
            st.info("⏹️ Bot durduruldu!")
        else:
            st.warning("⚠️ Bot zaten durdurulmuş!")
    except Exception as e:
        st.error(f"❌ Bot durdurulurken hata: {e}")

def get_bot_status():
    return {
        "running": st.session_state.bot_running,
        "status": "active" if st.session_state.bot_running else "inactive"
    }

def update_api_key(new_key):
    try:
        st.session_state.api_key = new_key
        save_setting('api_key', new_key)
        st.success("✅ API key güncellendi!")
    except Exception as e:
        st.error(f"❌ API key güncellenirken hata: {e}")

def start_withdrawal_listener():
    try:
        success = bot.start_listener()
        if success:
            st.session_state.listener_running = True
            st.success("✅ Para çekme dinleyicisi başlatıldı!")
        else:
            st.warning("⚠️ Dinleyici zaten çalışıyor!")
    except Exception as e:
        st.error(f"❌ Dinleyici başlatılırken hata: {e}")

def stop_withdrawal_listener():
    try:
        success = bot.stop_listener()
        if success:
            st.session_state.listener_running = False
            st.info("⏹️ Para çekme dinleyicisi durduruldu!")
        else:
            st.warning("⚠️ Dinleyici zaten durdurulmuş!")
    except Exception as e:
        st.error(f"❌ Dinleyici durdurulurken hata: {e}")

def get_withdrawal_listener_status():
    return {
        "running": st.session_state.listener_running,
        "status": "active" if st.session_state.listener_running else "inactive"
    }

def get_withdrawal_notifications():
    # Örnek bildirimler döndür
    return [
        {"id": 1, "message": "Örnek bildirim 1", "timestamp": datetime.now(), "is_read": False},
        {"id": 2, "message": "Örnek bildirim 2", "timestamp": datetime.now(), "is_read": True}
    ]

def update_telegram_chat_ids(new_ids):
    try:
        st.session_state.telegram_chat_ids = new_ids
        save_setting('telegram_chat_ids', new_ids)
        st.success("✅ Telegram Chat ID'leri güncellendi!")
    except Exception as e:
        st.error(f"❌ Chat ID'leri güncellenirken hata: {e}")

# Ayarları yükle
load_settings()

# Sidebar
with st.sidebar:
    st.header("⚙️ Ayarlar")
    
    api_key = st.text_input(
        "🔑 API Key",
        value=st.session_state.api_key,
        type="password",
        help="Sistem API anahtarınız"
    )
    
    if st.button("💾 API Key Kaydet"):
        update_api_key(api_key)
    
    st.divider()
    
    telegram_chat_ids = st.text_area(
        "📱 Telegram Chat ID'leri",
        value=st.session_state.telegram_chat_ids,
        help="Her satıra bir Telegram Chat ID yazın",
        height=100
    )
    
    if st.button("💾 Chat ID'leri Kaydet"):
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
    st.subheader("📊 Sistem Durumu")
    
    col1, col2 = st.columns(2)
    
    with col1:
        bot_status = get_bot_status()
        status_emoji = "🟢" if bot_status["running"] else "🔴"
        st.metric(
            "Ana Bot Durumu",
            f"{status_emoji} {'Çalışıyor' if bot_status['running'] else 'Durduruldu'}",
            help="Ana bot durumu"
        )
    
    with col2:
        listener_status = get_withdrawal_listener_status()
        status_emoji = "🟢" if listener_status["running"] else "🔴"
        st.metric(
            "Dinleyici Durumu",
            f"{status_emoji} {'Çalışıyor' if listener_status['running'] else 'Durduruldu'}"
        )
    
    # Sistem bilgileri
    st.subheader("ℹ️ Sistem Bilgileri")
    info_col1, info_col2, info_col3 = st.columns(3)
    
    with info_col1:
        st.info(f"**Son Güncelleme:**\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    with info_col2:
        st.info(f"**API Key:**\n{'✅ Kayıtlı' if st.session_state.api_key else '❌ Yok'}")
    
    with info_col3:
        chat_ids = st.session_state.telegram_chat_ids.split('\n')
        valid_ids = [id.strip() for id in chat_ids if id.strip()]
        st.info(f"**Chat ID'ler:**\n{len(valid_ids)} kayıtlı")

with tab3:
    st.subheader("📨 Son Bildirimler")
    
    # Örnek bildirimler
    notifications = get_withdrawal_notifications()
    
    if notifications:
        for notif in notifications:
            with st.expander(f"{'📩' if not notif['is_read'] else '📨'} {notif['message']}"):
                st.write(f"**Zaman:** {notif['timestamp']}")
                st.write(f"**Durum:** {'Okundu' if notif['is_read'] else 'Okunmadı'}")
                if st.button("✅ Okundu olarak işaretle", key=f"read_{notif['id']}"):
                    st.success("Bildirim okundu olarak işaretlendi!")
    else:
        st.info("📭 Henüz bildirim yok")

# Footer
st.divider()
st.caption(f"""
🤖 **Telegram Bot Kontrol Paneli** - v1.0
⏰ Son güncelleme: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🔧 Basit mod: aiohttp ve signalrcore olmadan çalışıyor
""")

# Streamlit Cloud için özel not
st.sidebar.info("""
**ℹ️ Streamlit Cloud Notu:**
- Bu basitleştirilmiş versiyon çalışacaktır
- Gerçek SignalR bağlantısı için local kurulum gerekli
""")
