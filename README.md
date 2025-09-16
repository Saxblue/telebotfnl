# 🤖 BetConstruct KPI Bot - Telegram Bot + Streamlit Kontrol Paneli

Bu proje, mevcut Tkinter KPI uygulamasını Telegram bot ve Streamlit kontrol paneli kombinasyonuna dönüştürür.

## 📋 Özellikler

### Telegram Bot
- 📊 Statik "KPI Sorgusu" butonu
- Türkçe hoş geldin mesajı
- Çoklu kullanıcı ID desteği (virgül veya satır ayırımlı)
- Excel rapor oluşturma (orijinal format korunmuş)
- Otomatik log kaydı

### Streamlit Kontrol Paneli
- Bot başlatma/durdurma
- KPI API anahtarı güncelleme
- Günlük istatistikler ve grafikler
- GitHub log entegrasyonu
- Proje ZIP indirme özelliği

## 🚀 Kurulum

### 1. Gereksinimler
```bash
pip install -r requirements.txt
```

### 2. Çevre Değişkenleri
Aşağıdaki çevre değişkenlerini ayarlayın:

```bash
# Windows
set TELEGRAM_TOKEN=8355199755:AAGm7XOYoQ0Xkeq4t_dX33fDJAqnVOVEx4c
set KPI_API_KEY=2d3bb9ccd0cecc72866bd0107be3ffc0a6eaa5e78e4d221f3db49e345cd1a054
set GITHUB_TOKEN=your_github_token_here
set GITHUB_REPO=your_github_repo_url_here

# Linux/Mac
export TELEGRAM_TOKEN="8355199755:AAGm7XOYoQ0Xkeq4t_dX33fDJAqnVOVEx4c"
export KPI_API_KEY="2d3bb9ccd0cecc72866bd0107be3ffc0a6eaa5e78e4d221f3db49e345cd1a054"
export GITHUB_TOKEN="your_github_token_here"
export GITHUB_REPO="your_github_repo_url_here"
```

### 3. Uygulamayı Başlatma
```bash
streamlit run app.py
```

## 📁 Proje Yapısı

```
TelegramKPIBot/
├── bot.py              # Telegram bot mantığı + Excel oluşturma
├── app.py              # Streamlit kontrol paneli + GitHub log
├── logs.json           # Sorgu logları (GitHub'a push edilir)
├── requirements.txt    # Python bağımlılıkları
└── README.md          # Bu dosya
```

## 🔧 Kullanım

### Telegram Bot
1. Kontrol panelinden bot'u başlatın
2. Telegram'da `/start` komutuyla bot'u başlatın
3. "📊 KPI Sorgusu" butonuna basın
4. Kullanıcı ID'lerini gönderin:
   ```
   12345
   67890
   ```
   veya
   ```
   12345, 67890, 11111
   ```
5. Excel raporu alın

### Kontrol Paneli
- **Bot Kontrolü**: Başlat/Durdur butonları
- **API Ayarları**: KPI API anahtarını güncelleme
- **İstatistikler**: Günlük sorgu istatistikleri ve grafikler
- **GitHub**: Logları otomatik GitHub'a yükleme
- **İndirme**: Projeyi ZIP olarak indirme

## 📊 Excel Raporu

Orijinal Tkinter uygulamasıyla aynı format:
- ID, Kullanıcı Adı, İsim, Telefon, E-posta
- Bakiye, Son Giriş, Toplam Yatırım, Toplam Çekim
- Kayıt Tarihi, Doğum Tarihi, Partner bilgileri
- Şık tablo formatı ve otomatik filtreleme

## 🌐 24/7 Deployment

### Replit
1. Replit'te yeni Python projesi oluşturun
2. Dosyaları yükleyin
3. Çevre değişkenlerini Secrets'ta ayarlayın
4. `streamlit run app.py` komutunu çalıştırın

### Render
1. GitHub repo'yu Render'a bağlayın
2. Web Service olarak deploy edin
3. Çevre değişkenlerini ayarlayın
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `streamlit run app.py --server.port=$PORT`

## 🔒 Güvenlik

- API anahtarları çevre değişkenlerinde saklanır
- Kodda hiçbir token hardcode edilmemiştir
- GitHub token'ı sadece log yükleme için kullanılır

## 📈 İstatistikler

Kontrol paneli şunları takip eder:
- Toplam sorgu sayısı
- Benzersiz kullanıcı sayısı
- Sorgulanan toplam ID sayısı
- Ortalama yanıt süresi
- Saatlik sorgu dağılımı
- En aktif kullanıcılar

## 🛠️ Teknik Detaylar

- **Telegram Bot**: python-telegram-bot 20.6
- **Web Interface**: Streamlit 1.28.1
- **Excel**: pandas + xlsxwriter
- **Grafikler**: Plotly
- **Async**: asyncio ile asenkron bot işlemleri
- **Threading**: Bot arka planda çalışır, Streamlit ana thread'de

## 📞 Destek

Herhangi bir sorun için GitHub Issues kullanın veya doğrudan iletişime geçin.

---

**Not**: Bu uygulama BetConstruct KPI API'sini kullanır ve orijinal Tkinter uygulamasının tüm özelliklerini korur.
