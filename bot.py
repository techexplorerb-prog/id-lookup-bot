import telebot
import requests
import logging
import re
import time
import json
import sys
import signal
from datetime import datetime
from functools import wraps
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ==================== কনফিগারেশন ক্লাস ====================
@dataclass
class BotConfig:
    """বটের সকল কনফিগারেশন সেটিংস"""
    API_TOKEN: str = '8751763218:AAELzZZ-09u8iTvrdGa_y3IFjmIvo3z_iZU'
    API_URL: str = "https://tgchatid.vercel.app/api/lookup"
    RATE_LIMIT_SECONDS: int = 5
    REQUEST_TIMEOUT: int = 15
    MAX_RETRIES: int = 3
    ADMIN_IDS: list = None  # আপনার এডমিন আইডি দিন যেমন: [123456789]
    ENABLE_LOGGING: bool = True
    LOG_LEVEL: str = "INFO"
    
    def __post_init__(self):
        if self.ADMIN_IDS is None:
            self.ADMIN_IDS = []

config = BotConfig()

# ==================== প্রফেশনাল লগিং সেটআপ ====================
def setup_logger(name: str = "ChatIDBot") -> logging.Logger:
    """প্রফেশনাল লগিং সেটআপ ফাইল রোটেশন সহ"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.LOG_LEVEL))
    
    # লগ ডিরেক্টরি তৈরি
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # ফরম্যাট
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # ফাইল হ্যান্ডলার (রোটেটিং)
    file_handler = RotatingFileHandler(
        log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log",
        maxBytes=10_485_760,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    # কনসোল হ্যান্ডলার
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # এরর ফাইল
    error_handler = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=5_242_880,
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.addHandler(error_handler)
    
    return logger

logger = setup_logger()

# ==================== রেট লিমিটার ক্লাস ====================
class RateLimiter:
    """রেট লিমিটিং ম্যানেজার"""
    def __init__(self, limit_seconds: int = 5):
        self.limit_seconds = limit_seconds
        self.user_commands: Dict[int, float] = {}
    
    def is_allowed(self, user_id: int) -> Tuple[bool, int]:
        """চেক করে ইউজার কমান্ড দিতে পারবে কিনা"""
        current_time = time.time()
        last_time = self.user_commands.get(user_id, 0)
        time_diff = current_time - last_time
        
        if time_diff < self.limit_seconds:
            return False, int(self.limit_seconds - time_diff)
        
        self.user_commands[user_id] = current_time
        return True, 0
    
    def cleanup(self, max_age: int = 3600):
        """পুরানো এন্ট্রি ক্লিনআপ"""
        current_time = time.time()
        expired = [uid for uid, ts in self.user_commands.items() 
                  if current_time - ts > max_age]
        for uid in expired:
            del self.user_commands[uid]

# ==================== ভ্যালিডেটর ক্লাস ====================
class Validator:
    """ইনপুট ভ্যালিডেশন"""
    
    @staticmethod
    def validate_chat_id(chat_id: str) -> bool:
        """চ্যাট আইডি ভ্যালিডেশন"""
        if not chat_id or not chat_id.strip():
            return False
        return chat_id.strip().isdigit()
    
    @staticmethod
    def sanitize_input(text: str) -> str:
        """ইনপুট স্যানিটাইজেশন - শুধু সংখ্যা রাখে"""
        if not text:
            return ""
        return re.sub(r'[^\d]', '', text).strip()

# ==================== মেসেজ ফরম্যাটার ====================
class MessageFormatter:
    """প্রফেশনাল মেসেজ ফরম্যাটিং"""
    
    @staticmethod
    def create_embed_info(data: dict) -> str:
        """সুন্দর মার্কডাউন ফরম্যাটে তথ্য দেখায়"""
        return f"""
🔍 **তথ্য অনুসন্ধান ফলাফল**
━━━━━━━━━━━━━━━━━━━━━
🆔 **Chat ID:** `{data.get('chat_id', 'N/A')}`
📞 **নম্বর:** `{data.get('number', 'N/A')}`
🌍 **দেশ:** {data.get('country', 'Unknown')}
📍 **কোড:** {data.get('country_code', 'N/A')}
📅 **সময়:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━
✅ তথ্য সফলভাবে প্রাপ্ত
"""
    
    @staticmethod
    def error_message(error_type: str, details: str = "") -> str:
        """এরর মেসেজ ফরম্যাট"""
        errors = {
            "invalid_id": "❌ **ত্রুটি:** সঠিক Chat ID দিন!\n\n✅ সঠিক ফরম্যাট: `123456789`\n💡 `/id` কমান্ড দিয়ে নিজের আইডি দেখুন",
            "not_found": f"❌ **তথ্য পাওয়া যায়নি!**\n\n{details}",
            "timeout": "⏰ **টাইমআউট!** সার্ভার রেসপন্স দিতে পারেনি।\n🔄 কিছুক্ষণ পর আবার চেষ্টা করুন।",
            "connection": "🌐 **কানেকশন এরর!** ইন্টারনেট চেক করে আবার চেষ্টা করুন।",
        }
        return errors.get(error_type, "⚠️ **অজানা ত্রুটি!**")

# ==================== মেট্রিক্স কালেক্টর ====================
class MetricsCollector:
    """পারফরম্যান্স মেট্রিক্স ট্র্যাকিং"""
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.start_time = datetime.now()
    
    def record_request(self, success: bool):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
    
    def get_stats(self) -> dict:
        uptime = (datetime.now() - self.start_time).total_seconds()
        return {
            "total": self.total_requests,
            "success": self.successful_requests,
            "failed": self.failed_requests,
            "uptime": uptime,
            "success_rate": (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0
        }

# ==================== বট ক্লাস ====================
class ChatIDBot:
    """মেইন বট ক্লাস"""
    
    def __init__(self):
        self.bot = telebot.TeleBot(config.API_TOKEN)
        self.rate_limiter = RateLimiter(config.RATE_LIMIT_SECONDS)
        self.metrics = MetricsCollector()
        self._register_handlers()
        self._setup_commands()
    
    def _setup_commands(self):
        """বট কমান্ড সেটআপ"""
        self.bot.set_my_commands([
            telebot.types.BotCommand("start", "🚀 বট চালু করুন"),
            telebot.types.BotCommand("help", "❓ সাহায্য দেখুন"),
            telebot.types.BotCommand("id", "🆔 নিজের আইডি দেখুন"),
            telebot.types.BotCommand("about", "ℹ️ বট সম্পর্কে"),
            telebot.types.BotCommand("ping", "🏓 লেটেন্সি চেক"),
            telebot.types.BotCommand("stats", "📊 পরিসংখ্যান"),
        ])
    
    def _register_handlers(self):
        """সকল হ্যান্ডলার রেজিস্টার"""
        
        @self.bot.message_handler(commands=['start'])
        def start_handler(message):
            self._handle_start(message)
        
        @self.bot.message_handler(commands=['help'])
        def help_handler(message):
            self._handle_help(message)
        
        @self.bot.message_handler(commands=['id'])
        def id_handler(message):
            self._handle_show_id(message)
        
        @self.bot.message_handler(commands=['about'])
        def about_handler(message):
            self._handle_about(message)
        
        @self.bot.message_handler(commands=['ping'])
        def ping_handler(message):
            self._handle_ping(message)
        
        @self.bot.message_handler(commands=['stats'])
        def stats_handler(message):
            self._handle_stats(message)
        
        @self.bot.message_handler(func=lambda m: not m.text.startswith('/'))
        def lookup_handler(message):
            self._handle_lookup(message)
    
    def _handle_start(self, message):
        """স্টার্ট কমান্ড"""
        user = message.from_user
        welcome_msg = f"""
🌟 **স্বাগতম {user.first_name}!** 🌟

আমি **Chat ID Lookup Bot Pro** - প্রফেশনাল চ্যাট আইডি লুকাপ সিস্টেম।

📌 **আপনার তথ্য:**
├ 🆔 আইডি: `{user.id}`
├ 👤 নাম: {user.first_name}
├ 🔖 ইউজারনেম: @{user.username or 'Not set'}
└ 🕐 সময়: {datetime.now().strftime('%I:%M %p')}

💡 **কমান্ডসমূহ:**
├ `/help` - সব কমান্ড দেখুন
├ `/id` - নিজের আইডি দেখুন
├ `/stats` - বট পরিসংখ্যান
└ `/ping` - লেটেন্সি চেক

⚙️ **সীমাবদ্ধতা:** প্রতি {config.RATE_LIMIT_SECONDS} সেকেন্ডে ১ বার

🔐 **গোপনীয়তা:** ব্যক্তিগত তথ্য সংরক্ষণ করি না।
"""
        self.bot.reply_to(message, welcome_msg, parse_mode="Markdown")
        logger.info(f"New user started bot: {user.id} | {user.first_name}")
    
    def _handle_help(self, message):
        """হেল্প কমান্ড"""
        help_text = f"""
📚 **কমান্ড লিস্ট ও সাহায্য**
━━━━━━━━━━━━━━━━━━━

🎮 **মৌলিক কমান্ড:**
├ `/start` - বট চালু করুন
├ `/id` - আপনার Chat ID দেখুন  
├ `/help` - এই মেনু দেখুন
├ `/about` - বট সম্পর্কে তথ্য
├ `/ping` - সার্ভার লেটেন্সি চেক
└ `/stats` - বট পরিসংখ্যান

🔍 **অনুসন্ধান:**
• সরাসরি Chat ID লিখে পাঠান
• উদাহরণ: `123456789`
• শুধু সংখ্যা গ্রহণযোগ্য

⚙️ **সীমাবদ্ধতা:**
• প্রতি {config.RATE_LIMIT_SECONDS} সেকেন্ডে ১ বার
• শুধু পাবলিক ডাটাবেজের আইডি

📞 **সাপোর্ট:** @TechExplorerBD
"""
        self.bot.reply_to(message, help_text, parse_mode="Markdown")
    
    def _handle_show_id(self, message):
        """আইডি দেখান"""
        user_id = message.from_user.id
        self.bot.reply_to(
            message, 
            f"🆔 **আপনার Chat ID:** `{user_id}`\n\n💡 এই আইডি দিয়ে অন্য আইডি অনুসন্ধান করুন।", 
            parse_mode="Markdown"
        )
    
    def _handle_about(self, message):
        """বট সম্পর্কে তথ্য"""
        about_text = f"""
🤖 **বট ইনফরমেশন**
━━━━━━━━━━━━━━

**নাম:** Chat ID Lookup Bot Pro
**ভার্সন:** 3.0 (Enterprise)
**নির্মাতা:** @TechExplorerBD
**প্ল্যাটফর্ম:** Telegram Bot API 6.0+

✨ **প্রো ফিচারস:**
✅ রেট লিমিটিং & স্প্যাম প্রটেকশন
✅ অ্যাডভান্সড লগিং সিস্টেম
✅ মেট্রিক্স কালেক্টর
✅ অটো এরর রিকভারি
✅ স্মার্ট ভ্যালিডেশন
✅ ফাইল রোটেশন লগ

📊 **স্ট্যাটাস:** 🟢 অনলাইন
📅 **লাস্ট আপডেট:** {datetime.now().strftime('%B %Y')}

🔧 **টেক স্ট্যাক:** Python 3.11+ | TeleBot | Requests
"""
        self.bot.reply_to(message, about_text, parse_mode="Markdown")
    
    def _handle_ping(self, message):
        """পিং চেক - লেটেন্সি মাপে"""
        start = time.time()
        self.bot.send_chat_action(message.chat.id, 'typing')
        latency = round((time.time() - start) * 1000)
        
        self.bot.reply_to(
            message,
            f"🏓 **পং!**\n━━━━━━━━━━━━\n📡 লেটেন্সি: `{latency}ms`\n🟢 স্ট্যাটাস: **অনলাইন**\n⏱️ সময়: `{datetime.now().strftime('%H:%M:%S')}`",
            parse_mode="Markdown"
        )
    
    def _handle_stats(self, message):
        """পরিসংখ্যান দেখান"""
        stats = self.metrics.get_stats()
        stats_msg = f"""
📊 **বট পারফরম্যান্স স্ট্যাটস**
━━━━━━━━━━━━━━━━━━━━

📈 **মোট রিকোয়েস্ট:** `{stats['total']}`
✅ **সফল:** `{stats['success']}`
❌ **ব্যর্থ:** `{stats['failed']}`
📊 **সাকসেস রেট:** `{stats['success_rate']:.1f}%`
⏱️ **আপটাইম:** `{stats['uptime'] / 3600:.1f} ঘন্টা`

🔄 **লাস্ট রিসেট:** {self.metrics.start_time.strftime('%Y-%m-%d %H:%M')}
"""
        self.bot.reply_to(message, stats_msg, parse_mode="Markdown")
    
    def _handle_lookup(self, message):
        """মেইন লুকাপ লজিক"""
        # রেট লিমিট চেক
        allowed, wait_time = self.rate_limiter.is_allowed(message.from_user.id)
        if not allowed:
            self.bot.reply_to(
                message,
                f"⏳ **রেট লিমিট!** দয়া করে {wait_time} সেকেন্ড অপেক্ষা করুন।",
                parse_mode="Markdown"
            )
            return
        
        # ইনপুট স্যানিটাইজ
        chat_id = Validator.sanitize_input(message.text)
        
        # ভ্যালিডেশন
        if not Validator.validate_chat_id(chat_id):
            self.bot.reply_to(
                message,
                MessageFormatter.error_message("invalid_id"),
                parse_mode="Markdown"
            )
            return
        
        # প্রসেসিং ইন্ডিকেটর
        status_msg = self.bot.reply_to(message, "🔍 **অনুসন্ধান চলছে...** দয়া করে অপেক্ষা করুন ⏳")
        self.bot.send_chat_action(message.chat.id, 'typing')
        
        try:
            # এপিআই কল
            response = requests.get(
                config.API_URL,
                params={"number": chat_id},
                timeout=config.REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "ChatIDBot/3.0",
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            
            data = response.json()
            self.metrics.record_request(success=True)
            logger.info(f"✓ Lookup success | User: {message.from_user.id} | ID: {chat_id}")
            
            # সাকসেস চেক
            if data.get("success") and data.get("data", {}).get("number"):
                info = data["data"]
                reply_text = MessageFormatter.create_embed_info(info)
                self.bot.edit_message_text(
                    reply_text,
                    message.chat.id,
                    status_msg.message_id,
                    parse_mode="Markdown"
                )
            else:
                error_detail = f"🔍 চাওয়া আইডি: `{chat_id}`\n📂 স্ট্যাটাস: ডাটাবেজে নেই\n💡 নোট: শুধু পাবলিক ডাটাবেজের আইডি পাওয়া যায়"
                self.bot.edit_message_text(
                    MessageFormatter.error_message("not_found", error_detail),
                    message.chat.id,
                    status_msg.message_id,
                    parse_mode="Markdown"
                )
                
        except requests.exceptions.Timeout:
            self.metrics.record_request(success=False)
            logger.error(f"⏰ Timeout | ID: {chat_id} | User: {message.from_user.id}")
            self.bot.edit_message_text(
                MessageFormatter.error_message("timeout"),
                message.chat.id,
                status_msg.message_id,
                parse_mode="Markdown"
            )
            
        except requests.exceptions.ConnectionError:
            self.metrics.record_request(success=False)
            logger.error(f"🌐 Connection error | ID: {chat_id} | User: {message.from_user.id}")
            self.bot.edit_message_text(
                MessageFormatter.error_message("connection"),
                message.chat.id,
                status_msg.message_id,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            self.metrics.record_request(success=False)
            logger.error(f"⚠️ Unexpected error: {e} | User: {message.from_user.id}", exc_info=True)
            self.bot.edit_message_text(
                f"⚠️ **সার্ভার ত্রুটি!**\n```\n{str(e)[:100]}\n```\n🔄 কিছুক্ষণ পর আবার চেষ্টা করুন।\n📞 সমস্যা থাকলে: @TechExplorerBD",
                message.chat.id,
                status_msg.message_id,
                parse_mode="Markdown"
            )
    
    def run(self):
        """বট রান করুন"""
        print("=" * 50)
        print("🤖 Chat ID Lookup Bot - Professional Edition")
        print(f"📅 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⚙️ Config: Rate Limit={config.RATE_LIMIT_SECONDS}s, Timeout={config.REQUEST_TIMEOUT}s")
        print(f"📁 Log directory: logs/")
        print("=" * 50)
        print("✅ বট সফলভাবে স্টার্ট হয়েছে!")
        print("🔄 Infinity polling চালু...\n")
        
        logger.info("🚀 Bot started successfully")
        
        try:
            self.bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except KeyboardInterrupt:
            print("\n⛔ বট বন্ধ করা হচ্ছে...")
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.critical(f"💥 Fatal error: {e}", exc_info=True)
            print(f"\n❌ FATAL ERROR: {e}")
            raise

# ==================== সিগনাল হ্যান্ডলার ====================
def signal_handler(sig, frame):
    """গ্রেসফুল শাটডাউন"""
    print("\n\n🛑 Shutdown signal received...")
    logger.info("Graceful shutdown initiated")
    sys.exit(0)

# ==================== মেইন এন্ট্রি পয়েন্ট ====================
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    bot = ChatIDBot()
    bot.run()
