# 🚀 Streamlit Cloud Deployment Rehberi

## 📋 Deployment Adımları

### 1. GitHub Repository Oluşturma
1. GitHub'da yeni repository oluşturun
2. Bu klasördeki dosyaları repository'ye yükleyin
3. **ÖNEMLİ:** `.env` dosyasını yüklemeyin! (Güvenlik)

### 2. Streamlit Cloud Ayarları
1. [share.streamlit.io](https://share.streamlit.io) adresine gidin
2. GitHub hesabınızla giriş yapın
3. "New app" butonuna tıklayın
4. Repository'nizi seçin
5. **Main file:** `app.py`
6. **Branch:** `main`

### 3. Environment Variables (Secrets)
Streamlit Cloud'da **Secrets** bölümünde şu değişkenleri tanımlayın:

```toml
# Telegram Bot
TELEGRAM_BOT_TOKEN = "your_bot_token_here"
TELEGRAM_CHAT_IDS = "chat_id1,chat_id2,chat_id3"

# Withdrawal Listener
WITHDRAWAL_HUB_ACCESS_TOKEN = "your_hub_token_here"
WITHDRAWAL_COOKIE = "your_cookie_here"
WITHDRAWAL_SUBSCRIBE_TOKEN = "your_subscribe_token_here"

# KPI API (varsa)
KPI_API_TOKEN = "your_kpi_token_here"

# GitHub (varsa)
GITHUB_TOKEN = "your_github_token_here"
```

### 4. Deployment
1. **Deploy** butonuna tıklayın
2. Uygulama build edilecek (2-3 dakika)
3. Hazır olduğunda URL verilecek

## ⚠️ Önemli Notlar

### 🔐 Güvenlik
- `.env` dosyası GitHub'a yüklenmez (`.gitignore` ile korunur)
- Tüm hassas veriler Streamlit Secrets'ta saklanır
- Token'ları kimseyle paylaşmayın

### 🔄 Güncellemeler
- GitHub'a push yaptığınızda otomatik deploy olur
- Değişiklikler 1-2 dakika içinde yansır

### 🕐 Idle/Sleep
- Streamlit Cloud 30 dakika idle'da uyur
- İlk erişimde 10-15 saniye uyanma süresi
- 24/7 çalışması için ping mekanizması var

## 🎯 Beklenen Sonuç
- ✅ 24/7 çalışan web uygulaması
- ✅ Otomatik Telegram bildirimleri
- ✅ Web arayüzü ile kontrol
- ✅ Güvenli token yönetimi

## 🆘 Sorun Giderme
- **Build hatası:** requirements.txt kontrol edin
- **Token hatası:** Secrets'ı kontrol edin
- **Bağlantı hatası:** WebSocket timeout olabilir
- **Uygulama uyuyor:** URL'ye tekrar girin

## 📞 Destek
Sorun yaşarsanız Streamlit Community'ye başvurabilirsiniz.
