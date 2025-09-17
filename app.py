import streamlit as st
import json
import os
import time
import zipfile
import io
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from bot import start_bot_thread, stop_bot, get_bot_status, update_api_key, start_withdrawal_listener, stop_withdrawal_listener, get_withdrawal_listener_status, get_withdrawal_notifications, update_telegram_chat_ids
import requests
import base64
from dotenv import load_dotenv, set_key

# .env dosyasını güvenli şekilde yükle
# safe_load_dotenv() fonksiyonu main() içinde çağrılacak

# .env dosyası yönetimi için yardımcı fonksiyonlar
def safe_load_dotenv():
    """Güvenli .env dosyası yükleme - sorun varsa environment variables kullan"""
    try:
        if os.path.exists('.env'):
            # Önce dosyanın UTF-8 ile okunabilir olup olmadığını kontrol et
            with open('.env', 'r', encoding='utf-8') as f:
                content = f.read()
            # Dosya başarıyla okunabiliyorsa load_dotenv() çağır
            load_dotenv()
            st.success("✅ .env dosyası başarıyla yüklendi")
            return True
        else:
            # .env dosyası yoksa oluştur
            create_env_file_if_not_exists()
            load_dotenv()
            return True
    except UnicodeDecodeError:
        # Encoding hatası varsa .env dosyasını devre dışı bırak
        st.warning("⚠️ .env dosyası encoding hatası - sadece environment variables kullanılacak")
        backup_and_recreate_env_file()
        st.info("🔄 Lütfen token'larınızı aşağıdaki formdan tekrar girin")
        return True  # Hata olsa da devam et
    except Exception as e:
        st.warning(f"⚠️ .env dosyası yüklenemedi: {e} - Environment variables kullanılacak")
        return True  # Hata olsa da devam et

def backup_and_recreate_env_file():
    """Bozuk .env dosyasını yedekle ve yenisini oluştur"""
    try:
        # Eski dosyayı yedekle
        if os.path.exists('.env'):
            import shutil
            backup_name = f'.env.backup_{int(time.time())}'
            shutil.copy('.env', backup_name)
            st.info(f"📁 Eski .env dosyası {backup_name} olarak yedeklendi")
            
            # Eski dosyayı sil
            os.remove('.env')
            st.info("🗑️ Bozuk .env dosyası silindi")
        
        # Yeni dosya oluştur
        with open('.env', 'w', encoding='utf-8') as f:
            f.write('# BetConstruct KPI Bot Environment Variables\n')
            f.write('# Bu dosyayı güvenli bir yerde saklayın\n\n')
            f.write('TELEGRAM_TOKEN=\n')
            f.write('KPI_API_KEY=\n')
            f.write('GITHUB_TOKEN=\n')
            f.write('GITHUB_REPO=https://github.com/Saxblue/telebot\n')
        
        st.success("✅ Yeni .env dosyası UTF-8 encoding ile oluşturuldu")
        
    except Exception as e:
        st.error(f"Yedekleme/yeniden oluşturma hatası: {e}")

def update_env_variable(key, value):
    """Çevre değişkenini hem os.environ hem de .env dosyasında güncelle"""
    try:
        # Önce mevcut oturumda güncelle
        os.environ[key] = value
        
        # .env dosyasını güvenli şekilde güncelle
        env_content = {}
        
        # Mevcut .env dosyasını oku (varsa)
        if os.path.exists('.env'):
            try:
                with open('.env', 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            k, v = line.split('=', 1)
                            env_content[k.strip()] = v.strip()
            except UnicodeDecodeError:
                # Encoding hatası varsa boş başla
                env_content = {}
        
        # Yeni değeri ekle/güncelle
        env_content[key] = value
        
        # Dosyayı yeniden yaz
        with open('.env', 'w', encoding='utf-8') as f:
            f.write('# BetConstruct KPI Bot Environment Variables\n')
            f.write('# Bu dosyayı güvenli bir yerde saklayın\n\n')
            for k, v in env_content.items():
                f.write(f'{k}={v}\n')
        
        return True
        
    except Exception as e:
        st.error(f"Çevre değişkeni güncelleme hatası: {e}")
        return False

def create_env_file_if_not_exists():
    """Eğer .env dosyası yoksa UTF-8 encoding ile oluştur"""
    if not os.path.exists('.env'):
        try:
            with open('.env', 'w', encoding='utf-8') as f:
                f.write('# BetConstruct KPI Bot Environment Variables\n')
                f.write('# Bu dosyayı güvenli bir yerde saklayın\n\n')
                f.write('TELEGRAM_TOKEN=\n')
                f.write('KPI_API_KEY=\n')
                f.write('GITHUB_TOKEN=\n')
                f.write('GITHUB_REPO=https://github.com/Saxblue/telebot\n')
        except Exception as e:
            st.error(f".env dosyası oluşturma hatası: {e}")

# .env dosyasını oluştur (yoksa)
create_env_file_if_not_exists()

# Sayfa konfigürasyonu
st.set_page_config(
    page_title="BetConstruct KPI Bot Kontrol Paneli",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS stilleri
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1E88E5;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #1E88E5;
        margin: 0.5rem 0;
    }
    .status-running {
        color: #28a745;
        font-weight: bold;
    }
    .status-stopped {
        color: #dc3545;
        font-weight: bold;
    }
    .sidebar-section {
        margin-bottom: 2rem;
        padding: 1rem;
        background-color: #f8f9fa;
        border-radius: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)

class StreamlitControlPanel:
    def __init__(self):
        self.logs_file = "logs.json"
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.github_repo = os.getenv('GITHUB_REPO', 'https://github.com/Saxblue/telebot')
        
    def load_logs(self):
        """Logları yükle"""
        try:
            if os.path.exists(self.logs_file):
                with open(self.logs_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {"queries": []}
        except Exception as e:
            st.error(f"Log yükleme hatası: {e}")
            return {"queries": []}
    
    def save_logs(self, logs):
        """Logları kaydet"""
        try:
            with open(self.logs_file, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            st.error(f"Log kaydetme hatası: {e}")
    
    def push_logs_to_github(self):
        """Logları GitHub'a push et"""
        try:
            if not self.github_token:
                return False, "GitHub token bulunamadı"
                
            # logs.json dosyasını oku
            with open(self.logs_file, 'r', encoding='utf-8') as f:
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
            encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            data = {
                "message": f"Log güncelleme - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "content": encoded_content
            }
            
            if sha:
                data["sha"] = sha
            
            response = requests.put(url, headers=headers, json=data)
            
            if response.status_code in [200, 201]:
                return True, "Başarıyla GitHub'a yüklendi"
            else:
                return False, f"GitHub API hatası: {response.status_code}"
                
        except Exception as e:
            return False, f"GitHub push hatası: {e}"
    
    def get_daily_statistics(self, logs):
        """Günlük istatistikleri hesapla"""
        queries = logs.get("queries", [])
        
        if not queries:
            return {
                "total_queries": 0,
                "unique_users": 0,
                "total_ids_queried": 0,
                "avg_response_time": 0,
                "queries_today": 0,
                "top_users": [],
                "hourly_distribution": {}
            }
        
        # Bugünün tarihi
        today = datetime.now().date()
        
        # Bugünkü sorgular
        today_queries = [
            q for q in queries 
            if datetime.fromisoformat(q["timestamp"]).date() == today
        ]
        
        # İstatistikler
        total_queries = len(queries)
        unique_users = len(set(q["user_id"] for q in queries))
        total_ids_queried = sum(q["query_count"] for q in queries)
        avg_response_time = sum(q["response_time"] for q in queries) / len(queries) if queries else 0
        queries_today = len(today_queries)
        
        # En aktif kullanıcılar
        user_counts = {}
        for q in queries:
            user_id = q["user_id"]
            username = q.get("username", f"User_{user_id}")
            if user_id not in user_counts:
                user_counts[user_id] = {"username": username, "count": 0, "total_ids": 0}
            user_counts[user_id]["count"] += 1
            user_counts[user_id]["total_ids"] += q["query_count"]
        
        top_users = sorted(user_counts.values(), key=lambda x: x["count"], reverse=True)[:5]
        
        # Saatlik dağılım
        hourly_distribution = {}
        for q in today_queries:
            hour = datetime.fromisoformat(q["timestamp"]).hour
            hourly_distribution[hour] = hourly_distribution.get(hour, 0) + 1
        
        return {
            "total_queries": total_queries,
            "unique_users": unique_users,
            "total_ids_queried": total_ids_queried,
            "avg_response_time": avg_response_time,
            "queries_today": queries_today,
            "top_users": top_users,
            "hourly_distribution": hourly_distribution
        }
    
    def create_project_zip(self):
        """Proje ZIP dosyası oluştur"""
        try:
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Proje dosyalarını ekle
                files_to_include = [
                    'bot.py',
                    'app.py',
                    'requirements.txt',
                    'logs.json'
                ]
                
                for file_name in files_to_include:
                    if os.path.exists(file_name):
                        zip_file.write(file_name, file_name)
                
                # README dosyası ekle
                readme_content = """# BetConstruct KPI Bot

Bu proje, BetConstruct KPI verilerini sorgulayan bir Telegram bot ve Streamlit kontrol paneli içerir.

## Kurulum

1. Gerekli paketleri yükleyin:
```bash
pip install -r requirements.txt
```

2. Çevre değişkenlerini ayarlayın:
```bash
export TELEGRAM_TOKEN="your_telegram_token"
export KPI_API_KEY="your_kpi_api_key"
export GITHUB_TOKEN="your_github_token"
export GITHUB_REPO="your_github_repo_url"
```

3. Streamlit uygulamasını başlatın:
```bash
streamlit run app.py
```

## Özellikler

- Telegram bot ile KPI sorguları
- Streamlit kontrol paneli
- Excel rapor oluşturma
- GitHub entegrasyonu
- İstatistik takibi

## Kullanım

1. Telegram botunu başlatmak için kontrol panelini kullanın
2. Bot'a kullanıcı ID'lerini gönderin
3. Excel raporu alın
4. İstatistikleri kontrol panelinde takip edin
"""
                zip_file.writestr('README.md', readme_content)
            
            zip_buffer.seek(0)
            return zip_buffer
            
        except Exception as e:
            st.error(f"ZIP oluşturma hatası: {e}")
            return None

def main():
    # .env dosyasını güvenli şekilde yükle
    safe_load_dotenv()
    
    control_panel = StreamlitControlPanel()
    
    # Ana başlık
    st.markdown('<h1 class="main-header">🤖 BetConstruct KPI Bot Kontrol Paneli</h1>', unsafe_allow_html=True)
    
    # Sidebar
    with st.sidebar:
        st.markdown("## ⚙️ Bot Kontrolü")
        
        # Bot durumu
        bot_status = get_bot_status()
        telegram_token = os.getenv('TELEGRAM_TOKEN', '')
        
        if not telegram_token:
            status_text = "🔴 Token Eksik"
            status_class = "status-stopped"
            st.markdown(f'<p class="{status_class}">Bot Durumu: {status_text}</p>', unsafe_allow_html=True)
            st.warning("⚠️ Telegram token ayarlanmamış! Lütfen aşağıdan token'ınızı girin.")
        else:
            status_text = "🟢 Çalışıyor" if bot_status else "🔴 Durduruldu"
            status_class = "status-running" if bot_status else "status-stopped"
            st.markdown(f'<p class="{status_class}">Bot Durumu: {status_text}</p>', unsafe_allow_html=True)
        
        # Bot kontrol butonları
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("▶️ Başlat", disabled=bot_status or not telegram_token):
                if not telegram_token:
                    st.error("❌ Önce Telegram token'ını ayarlayın!")
                else:
                    with st.spinner("Bot başlatılıyor..."):
                        start_bot_thread()
                        time.sleep(3)
                        st.rerun()
        
        with col2:
            if st.button("⏹️ Durdur", disabled=not bot_status):
                with st.spinner("Bot durduruluyor..."):
                    stop_bot()
                    time.sleep(2)
                    st.rerun()
        
        st.markdown("---")
        
        # Withdrawal Listener Kontrolü
        st.markdown("## 💰 Çekim Talepleri İzleyici")
        
        # Withdrawal listener durumu
        withdrawal_status = get_withdrawal_listener_status()
        withdrawal_running = withdrawal_status.get('is_running', False)
        withdrawal_connected = withdrawal_status.get('is_connected', False)
        notifications_count = withdrawal_status.get('notifications_count', 0)
        
        # Durum göstergesi
        col1, col2, col3 = st.columns(3)
        with col1:
            if withdrawal_running:
                st.success("🟢 Çalışıyor")
            else:
                st.error("🔴 Durmuş")
        
        with col2:
            if withdrawal_connected:
                st.success("🔗 Bağlı")
            else:
                st.warning("⚠️ Bağlantı Yok")
        
        with col3:
            st.info(f"📊 {notifications_count} Bildirim")
        
        # Withdrawal listener kontrol butonları
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("▶️ Çekim İzleyiciyi Başlat", disabled=withdrawal_running or not bot_status):
                if not bot_status:
                    st.error("❌ Önce botu başlatın!")
                else:
                    with st.spinner("Çekim izleyici başlatılıyor..."):
                        if start_withdrawal_listener():
                            st.success("✅ Çekim izleyici başlatıldı!")
                        else:
                            st.error("❌ Çekim izleyici başlatılamadı!")
                        time.sleep(2)
                        st.rerun()
        
        with col2:
            if st.button("⏹️ Çekim İzleyiciyi Durdur", disabled=not withdrawal_running):
                with st.spinner("Çekim izleyici durduruluyor..."):
                    if stop_withdrawal_listener():
                        st.success("✅ Çekim izleyici durduruldu!")
                    else:
                        st.error("❌ Çekim izleyici durdurulamadı!")
                    time.sleep(2)
                    st.rerun()
        
        # Withdrawal Listener Token Ayarları
        st.markdown("### 🔐 Withdrawal Listener Token Ayarları")
        
        # Hub Access Token
        current_hub_token = os.getenv('WITHDRAWAL_HUB_ACCESS_TOKEN', '')
        new_hub_token = st.text_input(
            "Hub Access Token",
            value=current_hub_token,
            type="password",
            help="BetConstruct Hub Access Token (hat_... ile başlar)"
        )
        
        if st.button("🔄 Hub Access Token'ı Güncelle"):
            if new_hub_token and new_hub_token != current_hub_token:
                if update_env_variable('WITHDRAWAL_HUB_ACCESS_TOKEN', new_hub_token):
                    st.success("✅ Hub Access Token güncellendi!")
                    st.info("💾 Token .env dosyasına kaydedildi")
                    st.warning("🔄 Değişikliklerin etkili olması için çekim izleyiciyi yeniden başlatın!")
                    st.rerun()
                else:
                    st.error("❌ Token güncellenirken hata oluştu!")
            else:
                st.warning("Yeni bir token girin!")
        
        # Cookie
        current_cookie = os.getenv('WITHDRAWAL_COOKIE', '')
        new_cookie = st.text_area(
            "Cookie",
            value=current_cookie,
            height=100,
            help="BetConstruct session cookie değeri"
        )
        
        if st.button("🔄 Cookie'yi Güncelle"):
            if new_cookie and new_cookie != current_cookie:
                if update_env_variable('WITHDRAWAL_COOKIE', new_cookie):
                    st.success("✅ Cookie güncellendi!")
                    st.info("💾 Cookie .env dosyasına kaydedildi")
                    st.warning("🔄 Değişikliklerin etkili olması için çekim izleyiciyi yeniden başlatın!")
                    st.rerun()
                else:
                    st.error("❌ Cookie güncellenirken hata oluştu!")
            else:
                st.warning("Yeni bir cookie girin!")
        
        # Subscribe Token
        current_subscribe_token = os.getenv('WITHDRAWAL_SUBSCRIBE_TOKEN', '')
        new_subscribe_token = st.text_input(
            "Subscribe Token",
            value=current_subscribe_token,
            type="password",
            help="BetConstruct Subscribe Token"
        )
        
        if st.button("🔄 Subscribe Token'ı Güncelle"):
            if new_subscribe_token and new_subscribe_token != current_subscribe_token:
                if update_env_variable('WITHDRAWAL_SUBSCRIBE_TOKEN', new_subscribe_token):
                    st.success("✅ Subscribe Token güncellendi!")
                    st.info("💾 Token .env dosyasına kaydedildi")
                    st.warning("🔄 Değişikliklerin etkili olması için çekim izleyiciyi yeniden başlatın!")
                    st.rerun()
                else:
                    st.error("❌ Token güncellenirken hata oluştu!")
            else:
                st.warning("Yeni bir token girin!")
        
        # Telegram Chat ID'leri ayarı
        st.markdown("### 📱 Telegram Grup Ayarları")
        current_chat_ids = os.getenv('TELEGRAM_CHAT_IDS', '')
        new_chat_ids = st.text_input(
            "Telegram Grup Chat ID'leri (virgül ile ayırın)",
            value=current_chat_ids,
            help="Örnek: -1001234567890,-1001234567891"
        )
        
        if st.button("🔄 Chat ID'lerini Güncelle"):
            if new_chat_ids and new_chat_ids != current_chat_ids:
                if update_env_variable('TELEGRAM_CHAT_IDS', new_chat_ids):
                    if update_telegram_chat_ids(new_chat_ids):
                        st.success("✅ Telegram chat ID'leri güncellendi!")
                        st.info("💾 Chat ID'leri .env dosyasına kaydedildi")
                    else:
                        st.warning("⚠️ Chat ID'leri güncellendi ama bot'a aktarılamadı")
                    st.rerun()
                else:
                    st.error("❌ Chat ID'leri güncellenirken hata oluştu!")
            else:
                st.warning("Yeni chat ID'leri girin!")
        
        # Son çekim bildirimleri
        if notifications_count > 0:
            st.markdown("### 📋 Son Çekim Bildirimleri")
            notifications = get_withdrawal_notifications(5)
            
            for i, notification in enumerate(reversed(notifications)):
                with st.expander(f"🔔 {notification.get('client_name', 'N/A')} - {notification.get('amount', 0)} {notification.get('currency', 'TRY')}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**👤 Müşteri:** {notification.get('client_name', 'N/A')}")
                        st.write(f"**🆔 Kullanıcı:** {notification.get('client_login', 'N/A')}")
                        st.write(f"**💰 Miktar:** {notification.get('amount', 0)} {notification.get('currency', 'TRY')}")
                    with col2:
                        st.write(f"**🏦 Sistem:** {notification.get('payment_system', 'N/A')}")
                        st.write(f"**🏷️ BTag:** {notification.get('btag', 'N/A')}")
                        st.write(f"**📅 Zaman:** {notification.get('timestamp', 'N/A')}")
        
        st.markdown("---")
        
        # API Ayarları
        st.markdown("## 🔑 API Ayarları")
        
        # Telegram Token
        current_telegram_token = os.getenv('TELEGRAM_TOKEN', '')
        new_telegram_token = st.text_input(
            "Telegram Bot Token",
            value=current_telegram_token,
            type="password",
            help="BotFather'dan aldığınız Telegram bot token'ını girin"
        )
        
        if st.button("🔄 Telegram Token'ını Güncelle"):
            if new_telegram_token and new_telegram_token != current_telegram_token:
                if update_env_variable('TELEGRAM_TOKEN', new_telegram_token):
                    st.success("✅ Telegram token kalıcı olarak güncellendi!")
                    st.info("💾 Token .env dosyasına kaydedildi")
                    st.rerun()
                else:
                    st.error("❌ Token güncellenirken hata oluştu!")
            else:
                st.warning("Yeni bir token girin!")
        
        # KPI API Key
        current_key = os.getenv('KPI_API_KEY', '2d3bb9ccd0cecc72866bd0107be3ffc0a6eaa5e78e4d221f3db49e345cd1a054')
        new_key = st.text_input(
            "KPI API Anahtarı",
            value=current_key,
            type="password",
            help="KPI API anahtarını güncellemek için yeni anahtarı girin"
        )
        
        if st.button("🔄 API Anahtarını Güncelle"):
            if new_key and new_key != current_key:
                if update_env_variable('KPI_API_KEY', new_key):
                    update_api_key(new_key)
                    st.success("✅ API anahtarı kalıcı olarak güncellendi!")
                    st.info("💾 Anahtar .env dosyasına kaydedildi")
                    st.rerun()
                else:
                    st.error("❌ API anahtarı güncellenirken hata oluştu!")
            else:
                st.warning("Yeni bir anahtar girin!")
        
        st.markdown("---")
        
        # GitHub Ayarları
        st.markdown("## 📁 GitHub Entegrasyonu")
        
        if st.button("📤 Logları GitHub'a Yükle"):
            with st.spinner("GitHub'a yükleniyor..."):
                success, message = control_panel.push_logs_to_github()
                if success:
                    st.success(message)
                else:
                    st.error(message)
        
        st.markdown("---")
        
        # Proje İndirme
        st.markdown("## 📦 Proje İndirme")
        
        zip_file = control_panel.create_project_zip()
        if zip_file:
            st.download_button(
                label="📥 Projeyi ZIP olarak İndir",
                data=zip_file,
                file_name=f"telegram_kpi_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                mime="application/zip"
            )
    
    # Ana içerik
    # Logları yükle
    logs = control_panel.load_logs()
    stats = control_panel.get_daily_statistics(logs)
    
    # Genel istatistikler
    st.markdown("## 📊 Genel İstatistikler")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="📈 Toplam Sorgu",
            value=stats["total_queries"],
            delta=f"+{stats['queries_today']} bugün"
        )
    
    with col2:
        st.metric(
            label="👥 Benzersiz Kullanıcı",
            value=stats["unique_users"]
        )
    
    with col3:
        st.metric(
            label="🔍 Sorgulanan ID",
            value=stats["total_ids_queried"]
        )
    
    with col4:
        st.metric(
            label="⏱️ Ortalama Yanıt Süresi",
            value=f"{stats['avg_response_time']:.2f}s"
        )
    
    # Grafikler
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 📅 Bugünkü Saatlik Dağılım")
        if stats["hourly_distribution"]:
            hours = list(range(24))
            counts = [stats["hourly_distribution"].get(hour, 0) for hour in hours]
            
            fig = px.bar(
                x=hours,
                y=counts,
                labels={'x': 'Saat', 'y': 'Sorgu Sayısı'},
                title="Saatlik Sorgu Dağılımı"
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Bugün henüz sorgu yapılmamış")
    
    with col2:
        st.markdown("### 👑 En Aktif Kullanıcılar")
        if stats["top_users"]:
            user_data = []
            for user in stats["top_users"]:
                user_data.append({
                    "Kullanıcı": user["username"] or f"User_{user.get('user_id', 'Unknown')}",
                    "Sorgu Sayısı": user["count"],
                    "Toplam ID": user["total_ids"]
                })
            
            df_users = pd.DataFrame(user_data)
            
            fig = px.bar(
                df_users,
                x="Kullanıcı",
                y="Sorgu Sayısı",
                title="En Aktif Kullanıcılar",
                hover_data=["Toplam ID"]
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Henüz kullanıcı verisi yok")
    
    # Son sorgular tablosu
    st.markdown("## 📋 Son Sorgular")
    
    if logs["queries"]:
        # Son 20 sorguyu göster
        recent_queries = sorted(logs["queries"], key=lambda x: x["timestamp"], reverse=True)[:20]
        
        table_data = []
        for query in recent_queries:
            table_data.append({
                "Tarih": datetime.fromisoformat(query["timestamp"]).strftime("%d.%m.%Y %H:%M:%S"),
                "Kullanıcı": query.get("username", f"User_{query['user_id']}"),
                "Sorgu Sayısı": query["query_count"],
                "Yanıt Süresi": f"{query['response_time']:.2f}s",
                "Sorgulanan ID'ler": ", ".join(query["user_ids_queried"][:3]) + ("..." if len(query["user_ids_queried"]) > 3 else "")
            })
        
        df_queries = pd.DataFrame(table_data)
        st.dataframe(df_queries, use_container_width=True)
        
        # CSV indirme
        csv = df_queries.to_csv(index=False)
        st.download_button(
            label="📥 Tabloyu CSV olarak İndir",
            data=csv,
            file_name=f"kpi_bot_queries_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )
    else:
        st.info("Henüz sorgu kaydı bulunmuyor")
    
    # Sistem bilgileri
    with st.expander("🔧 Sistem Bilgileri"):
        st.markdown("### Çevre Değişkenleri")
        
        env_vars = {
            "TELEGRAM_TOKEN": "✅ Ayarlanmış" if os.getenv('TELEGRAM_TOKEN') else "❌ Ayarlanmamış",
            "KPI_API_KEY": "✅ Ayarlanmış" if os.getenv('KPI_API_KEY') else "❌ Ayarlanmamış",
            "GITHUB_TOKEN": "✅ Ayarlanmış" if os.getenv('GITHUB_TOKEN') else "❌ Ayarlanmamış",
            "GITHUB_REPO": os.getenv('GITHUB_REPO', 'Ayarlanmamış')
        }
        
        for var, status in env_vars.items():
            st.write(f"**{var}:** {status}")
        
        st.markdown("### Dosya Durumu")
        files_status = {
            "bot.py": "✅ Mevcut" if os.path.exists("bot.py") else "❌ Eksik",
            "logs.json": "✅ Mevcut" if os.path.exists("logs.json") else "❌ Eksik",
            "requirements.txt": "✅ Mevcut" if os.path.exists("requirements.txt") else "❌ Eksik"
        }
        
        for file, status in files_status.items():
            st.write(f"**{file}:** {status}")
    
    # Otomatik yenileme
    if st.checkbox("🔄 Otomatik Yenileme (30 saniye)", value=False):
        time.sleep(30)
        st.rerun()

if __name__ == "__main__":
    main()
