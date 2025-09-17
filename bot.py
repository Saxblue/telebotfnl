import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from signalr_client import SignalRClient
from telebot import AsyncTeleBot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import sqlite3
import threading

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class NotificationBot:
    def __init__(self, token, authorized_users, database_path="notifications.db"):
        self.bot = AsyncTeleBot(token)
        self.authorized_users = authorized_users
        self.database_path = database_path
        self.signalr_client = None
        self.init_db()
        self.setup_handlers()

    def init_db(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                timestamp DATETIME,
                is_read BOOLEAN DEFAULT FALSE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                receive_notifications BOOLEAN DEFAULT TRUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def setup_handlers(self):
        """Setup Telegram bot handlers"""
        @self.bot.message_handler(commands=['start'])
        async def start_handler(message):
            if message.chat.id not in self.authorized_users:
                await self.bot.send_message(message.chat.id, "❌ Unauthorized access.")
                return
            
            user_id = message.chat.id
            self.add_user_to_db(user_id)
            
            keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
            keyboard.add(KeyboardButton("🔔 Enable Notifications"))
            keyboard.add(KeyboardButton("🔕 Disable Notifications"))
            keyboard.add(KeyboardButton("📊 Status"))
            
            welcome_msg = (
                "🤖 Welcome to Notification Bot!\n\n"
                "I will send you real-time notifications from the system.\n"
                "Use the buttons below to manage your notifications."
            )
            await self.bot.send_message(user_id, welcome_msg, reply_markup=keyboard)

        @self.bot.message_handler(func=lambda message: message.text == "🔔 Enable Notifications")
        async def enable_notifications(message):
            user_id = message.chat.id
            self.update_user_setting(user_id, 'receive_notifications', True)
            await self.bot.send_message(user_id, "✅ Notifications enabled!")

        @self.bot.message_handler(func=lambda message: message.text == "🔕 Disable Notifications")
        async def disable_notifications(message):
            user_id = message.chat.id
            self.update_user_setting(user_id, 'receive_notifications', False)
            await self.bot.send_message(user_id, "✅ Notifications disabled!")

        @self.bot.message_handler(func=lambda message: message.text == "📊 Status")
        async def status_handler(message):
            user_id = message.chat.id
            status = self.get_user_setting(user_id, 'receive_notifications')
            unread_count = self.get_unread_notifications_count(user_id)
            
            status_msg = (
                f"📊 Your Status:\n"
                f"• Notifications: {'✅ Enabled' if status else '❌ Disabled'}\n"
                f"• Unread messages: {unread_count}\n"
                f"• Connected to SignalR: {'✅ Yes' if self.signalr_client and self.signalr_client.is_connected else '❌ No'}"
            )
            await self.bot.send_message(user_id, status_msg)

        @self.bot.message_handler(commands=['latest'])
        async def latest_notifications(message):
            user_id = message.chat.id
            if user_id not in self.authorized_users:
                await self.bot.send_message(user_id, "❌ Unauthorized access.")
                return
            
            latest_notifs = self.get_latest_notifications(user_id, limit=5)
            if not latest_notifs:
                await self.bot.send_message(user_id, "No notifications yet.")
                return
            
            response = "📋 Latest Notifications:\n\n"
            for notif in latest_notifs:
                time = datetime.strptime(notif[3], '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                response += f"⏰ {time}: {notif[2]}\n"
            
            await self.bot.send_message(user_id, response)

    def add_user_to_db(self, user_id):
        """Add user to database if not exists"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)",
            (user_id,)
        )
        conn.commit()
        conn.close()

    def update_user_setting(self, user_id, setting, value):
        """Update user setting in database"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE user_settings SET {setting} = ? WHERE user_id = ?",
            (value, user_id)
        )
        conn.commit()
        conn.close()

    def get_user_setting(self, user_id, setting):
        """Get user setting from database"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {setting} FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def save_notification(self, user_id, message):
        """Save notification to database"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notifications (user_id, message, timestamp) VALUES (?, ?, ?)",
            (user_id, message, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()

    def get_unread_notifications_count(self, user_id):
        """Get count of unread notifications"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = FALSE",
            (user_id,)
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0

    def get_latest_notifications(self, user_id, limit=10):
        """Get latest notifications for user"""
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        )
        result = cursor.fetchall()
        conn.close()
        return result

    async def get_access_token(self):
        """Get access token for SignalR connection"""
        try:
            # BURAYI KENDİ TOKEN ALMA MANTIĞINIZA GÖRE DÜZENLEYİN
            # Örnek: Cookie'den token çekme veya API'den token alma
            # Şu an için sabit bir token döndürüyoruz
            return "your_actual_access_token_here"
        except Exception as e:
            logger.error(f"Error getting access token: {e}")
            return None

    async def initialize_signalr(self):
        """Initialize SignalR connection"""
        try:
            self.signalr_client = SignalRClient(
                url="wss://backofficewebadmin.betconstruct.com/signalr/connect",
                hub_name="commonnotificationhub",
                access_token_factory=self.get_access_token,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Origin": "https://backoffice.betconstruct.com"
                }
            )

            def handle_notification(message):
                """Handle incoming notifications"""
                try:
                    logger.info(f"Received notification: {message}")
                    
                    # Tüm authorized kullanıcılara bildirim gönder
                    for user_id in self.authorized_users:
                        # Kullanıcı bildirimleri açık mı kontrol et
                        if self.get_user_setting(user_id, 'receive_notifications'):
                            notification_text = f"🔔 New Notification:\n{json.dumps(message, indent=2)}"
                            
                            # Veritabanına kaydet
                            self.save_notification(user_id, notification_text)
                            
                            # Telegram'a gönder (async olarak)
                            asyncio.create_task(
                                self.send_telegram_message(user_id, notification_text)
                            )
                except Exception as e:
                    logger.error(f"Error handling notification: {e}")

            # Bağlantıyı başlat
            success = await self.signalr_client.start_connection()
            
            if success:
                # Event'lara subscribe ol
                self.signalr_client.subscribe("SendNotification", handle_notification)
                self.signalr_client.subscribe("Subscribe", handle_notification)
                self.signalr_client.subscribe("Notify", handle_notification)
                logger.info("SignalR connection established and subscriptions created")
            else:
                logger.error("Failed to establish SignalR connection")
                
            return success
            
        except Exception as e:
            logger.error(f"Error initializing SignalR: {e}")
            return False

    async def send_telegram_message(self, user_id, message):
        """Send message to Telegram user"""
        try:
            await self.bot.send_message(user_id, message)
        except Exception as e:
            logger.error(f"Error sending Telegram message to {user_id}: {e}")

    async def start(self):
        """Start the bot"""
        try:
            # SignalR bağlantısını başlat
            logger.info("Initializing SignalR connection...")
            signalr_success = await self.initialize_signalr()
            
            if signalr_success:
                logger.info("SignalR connection established successfully")
            else:
                logger.warning("SignalR connection failed, continuing without real-time notifications")
            
            # Telegram botunu başlat
            logger.info("Starting Telegram bot...")
            await self.bot.polling(non_stop=True)
            
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
        finally:
            # Temizlik
            if self.signalr_client:
                await self.signalr_client.stop()

# Bot configuration
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
AUTHORIZED_USER_IDS = [123456789, 987654321]  # Your Telegram user IDs

async def main():
    """Main function"""
    bot = NotificationBot(TELEGRAM_BOT_TOKEN, AUTHORIZED_USER_IDS)
    await bot.start()

if __name__ == "__main__":
    # Event loop'u başlat
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
