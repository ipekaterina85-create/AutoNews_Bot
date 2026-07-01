import os
import time
import hashlib
import re
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

import feedparser
import requests
from telebot import TeleBot, types
from telebot import apihelper
from deep_translator import GoogleTranslator

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 1800))
NEWS_PER_SOURCE = int(os.environ.get('NEWS_PER_SOURCE', 3))
MAX_DESCRIPTION_LENGTH = int(os.environ.get('MAX_DESCRIPTION_LENGTH', 500))
ENABLE_TRANSLATION = os.environ.get('ENABLE_TRANSLATION', 'true').lower() == 'true'
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
ENABLE_HASHTAGS = os.environ.get('ENABLE_HASHTAGS', 'true').lower() == 'true'
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не установлен!")
if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID не установлен!")

# ============================================
# ЛОГИРОВАНИЕ
# ============================================

Path('logs').mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================

bot = TeleBot(BOT_TOKEN)
apihelper.ENABLE_MIDDLEWARE = True

PROXY_URL = os.environ.get('PROXY_URL')
if PROXY_URL:
    apihelper.proxy = {'https': PROXY_URL}
    logger.info(f"Используется прокси: {PROXY_URL}")

if ENABLE_TRANSLATION:
    translator = GoogleTranslator(source='auto', target='ru')
    logger.info("Перевод включён")
else:
    translator = None
    logger.info("Перевод отключён")

# ============================================
# РАБОЧИЕ RSS-ИСТОЧНИКИ (проверенные)
# ============================================

RSS_FEEDS = [
    # Европейские источники
    {
        'name': 'Autocar UK',
        'url': 'https://www.autocar.co.uk/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'high'
    },
    {
        'name': 'Top Gear',
        'url': 'https://www.topgear.com/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'medium'
    },
    
    # Американские источники
    {
        'name': 'Car and Driver',
        'url': 'https://www.caranddriver.com/rss/all.xml/',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high'
    },
    {
        'name': 'Road & Track',
        'url': 'https://www.roadandtrack.com/rss/all.xml/',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'medium'
    },
    {
        'name': 'The Drive',
        'url': 'https://www.thedrive.com/rss',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high'
    },
    {
        'name': 'AutoBlog',
        'url': 'https://www.autoblog.com/rss.xml',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'medium'
    },
    
    # Электромобили
    {
        'name': 'InsideEVs',
        'url': 'https://insideevs.com/rss/all/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric'
    },
    {
        'name': 'Electrek',
        'url': 'https://electrek.co/rss/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric'
    },
    {
        'name': 'CleanTechnica',
        'url': 'https://cleantechnica.com/feed/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'electric'
    },
    
    # Автоспорт
    {
        'name': 'Autosport',
        'url': 'https://www.autosport.com/rss/feed/all',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'motorsport'
    },
    {
        'name': 'Motorsport',
        'url': 'https://www.motorsport.com/rss/all/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'motorsport'
    },
]

# ============================================
# ХРАНИЛИЩЕ
# ============================================

PUBLISHED_FILE = 'published_news.txt'
MAX_PUBLISHED_HISTORY = 10000

def load_published():
    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            logger.error(f"Ошибка загрузки published_news.txt: {e}")
            return set()
    return set()

def save_published(news_id):
    try:
        with open(PUBLISHED_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{news_id}\n")
        cleanup_published_file()
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def cleanup_published_file():
    try:
        if not os.path.exists(PUBLISHED_FILE):
            return
        with open(PUBLISHED_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) > MAX_PUBLISHED_HISTORY:
            with open(PUBLISHED_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines[-MAX_PUBLISHED_HISTORY:])
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")

# ============================================
# УТИЛИТЫ
# ============================================

def get_news_id(entry):
    unique_str = f"{entry.get('title', '')}{entry.get('link', '')}"
    return hashlib.md5(unique_str.encode('utf-8')).hexdigest()

def translate_text(text, source_lang='en'):
    if not ENABLE_TRANSLATION or not translator:
        return text
    if not text or len(text.strip()) == 0:
        return ""
    try:
        if len(text) > 5000:
            text = text[:5000]
        translated = translator.translate(text)
        return translated if translated else text
    except Exception as e:
        logger.warning(f"Ошибка перевода: {e}")
        return text

def clean_html(text):
    if not text:
        return ""
    clean_text = re.sub('<[^<]+?>', '', text)
    clean_text = re.sub(r'\s+', ' ', clean_text)
    return clean_text.strip()

def get_image_url(entry):
    if not ENABLE_IMAGES:
        return None
    try:
        if 'media_content' in entry and entry.media_content:
            for media in entry.media_content:
                if media.get('url'):
                    return media['url']
        if 'enclosures' in entry and entry.enclosures:
            for enclosure in entry.enclosures:
                if enclosure.get('href'):
                    return enclosure['href']
        if 'media_thumbnail' in entry and entry.media_thumbnail:
            for thumbnail in entry.media_thumbnail:
                if thumbnail.get('url'):
                    return thumbnail['url']
        if 'content' in entry:
            for content in entry.content:
                if content.get('value'):
                    img_match = re.search(r'<img[^>]+src="([^"]+)"', content['value'])
                    if img_match:
                        return img_match.group(1)
        return None
    except Exception as e:
        logger.warning(f"Ошибка получения изображения: {e}")
        return None

def generate_hashtags(entry, feed_info):
    if not ENABLE_HASHTAGS:
        return ""
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    tags = ['#автоновости']
    
    if feed_info.get('category') == 'electric' or any(word in text for word in 
        ['tesla', 'electric', 'ev', 'battery', 'charging', 'электро', 'электромобил']):
        tags.append('#электрокары')
    
    if feed_info.get('category') == 'motorsport' or any(word in text for word in 
        ['f1', 'formula', 'racing', 'wrc', 'motogp', 'le mans', 'гонк']):
        tags.append('#автоспорт')
    
    region = feed_info.get('region', '')
    if '🇺🇸' in region:
        tags.append('#сша')
    elif '🇬🇧' in region:
        tags.append('#европа')
    elif '🇩🇪' in region:
        tags.append('#германия')
    
    brands = {
        'tesla': '#tesla', 'toyota': '#toyota', 'bmw': '#bmw', 
        'mercedes': '#mercedes', 'audi': '#audi', 'volkswagen': '#vw',
        'porsche': '#porsche', 'ferrari': '#ferrari', 'lamborghini': '#lamborghini',
        'ford': '#ford', 'honda': '#honda', 'nissan': '#nissan',
        'hyundai': '#hyundai', 'kia': '#kia'
    }
    
    for brand, tag in brands.items():
        if brand in text:
            tags.append(tag)
            break
    
    return ' '.join(tags)

def format_message(entry, feed_info):
    original_title = entry.get('title', 'Без названия')
    link = entry.get('link', '')
    original_summary = entry.get('summary', '')
    
    if ENABLE_TRANSLATION:
        translated_title = translate_text(original_title, feed_info.get('lang', 'en'))
        translated_summary = translate_text(original_summary, feed_info.get('lang', 'en'))
    else:
        translated_title = original_title
        translated_summary = original_summary
    
    translated_summary = clean_html(translated_summary)
    
    if len(translated_summary) > MAX_DESCRIPTION_LENGTH:
        translated_summary = translated_summary[:MAX_DESCRIPTION_LENGTH] + '...'
    
    region = feed_info.get('region', '🌍')
    source_name = feed_info.get('name', 'Неизвестно')
    
    message = f"{region} *{translated_title}*\n\n"
    
    if translated_summary:
        message += f"{translated_summary}\n\n"
    
    message += f"🔗 [Читать оригинал]({link})\n"
    message += f"📰 Источник: {source_name}"
    
    hashtags = generate_hashtags(entry, feed_info)
    if hashtags:
        message += f"\n\n{hashtags}"
    
    return message, translated_title

def send_news_to_channel(message, image_url=None):
    """ИСПРАВЛЕННАЯ ФУНКЦИЯ - убран disable_web_page_preview из send_photo"""
    try:
        if image_url and ENABLE_IMAGES:
            # ВАЖНО: disable_web_page_preview НЕ поддерживается в send_photo!
            bot.send_photo(
                CHANNEL_ID,
                image_url,
                caption=message,
                parse_mode='Markdown'
            )
        else:
            bot.send_message(
                CHANNEL_ID,
                message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки в канал: {e}")
        return False

# ============================================
# ОСНОВНАЯ ЛОГИКА
# ============================================

def fetch_and_publish():
    published = load_published()
    new_count = 0
    error_count = 0
    
    logger.info(f"Начинаем проверку {len(RSS_FEEDS)} источников новостей...")
    
    for feed_info in RSS_FEEDS:
        try:
            logger.info(f"Проверяем источник: {feed_info['name']}")
            
            feed = feedparser.parse(
                feed_info['url'],
                request_headers={'User-Agent': 'AutoImPulseBot/1.0'}
            )
            
            if feed.bozo and not feed.entries:
                logger.warning(f"Ошибка парсинга RSS {feed_info['name']}: {feed.bozo_exception}")
                continue
            
            for entry in feed.entries[:NEWS_PER_SOURCE]:
                news_id = get_news_id(entry)
                
                if news_id not in published:
                    message, title = format_message(entry, feed_info)
                    image_url = get_image_url(entry)
                    
                    if send_news_to_channel(message, image_url):
                        save_published(news_id)
                        new_count += 1
                        logger.info(f"✅ Опубликована новость: {title[:50]}...")
                        time.sleep(3)
                    else:
                        error_count += 1
                        
        except Exception as e:
            logger.error(f"Ошибка обработки источника {feed_info['name']}: {e}")
            error_count += 1
            continue
    
    logger.info(f"Цикл завершён. Опубликовано: {new_count}, Ошибок: {error_count}")
    return new_count, error_count

def send_startup_message():
    try:
        startup_message = (
            "🤖 *Auto imPulse News Bot запущен!*\n\n"
            f"📊 Источников: {len(RSS_FEEDS)}\n"
            f"⏱️ Интервал проверки: {CHECK_INTERVAL // 60} минут\n"
            f"🌐 Перевод: {'✅ Включён' if ENABLE_TRANSLATION else '❌ Выключен'}\n"
            f"🖼️ Изображения: {'✅ Включены' if ENABLE_IMAGES else '❌ Выключены'}\n"
            f"🏷️ Хештеги: {'✅ Включены' if ENABLE_HASHTAGS else '❌ Выключены'}\n\n"
            f"🕐 Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info("Бот успешно запущен!")
        return True
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return False

def graceful_shutdown(signum, frame):
    logger.info("Останавливаем бота...")
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

# ============================================
# ПРОВЕРКА ПОДКЛЮЧЕНИЯ К КАНАЛУ
# ============================================

def check_channel_access():
    """Проверяем, что бот имеет доступ к каналу"""
    try:
        logger.info(f"Проверяем доступ к каналу: {CHANNEL_ID}")
        chat = bot.get_chat(CHANNEL_ID)
        logger.info(f"✅ Канал найден: {chat.title}")
        
        # Проверяем, что бот админ
        admins = bot.get_chat_administrators(CHANNEL_ID)
        bot_username = bot.get_me().username
        is_admin = any(admin.user.username == bot_username for admin in admins)
        
        if is_admin:
            logger.info(f"✅ Бот @{bot_username} является администратором канала")
            return True
        else:
            logger.error(f"❌ Бот @{bot_username} НЕ является администратором канала!")
            logger.error("Добавьте бота в канал как администратора с правом 'Публикация сообщений'")
            return False
            
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к каналу: {e}")
        logger.error("Возможные причины:")
        logger.error("1. Бот не добавлен в канал")
        logger.error("2. Неправильный CHANNEL_ID")
        logger.error("3. Канал приватный, а указан username (нужен ID)")
        return False

# ============================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================

def main():
    logger.info("=" * 50)
    logger.info("Auto imPulse News Bot запускается...")
    logger.info("=" * 50)
    
    # ВАЖНО: Проверяем доступ к каналу перед запуском
    if not check_channel_access():
        logger.error("❌ Нет доступа к каналу! Останавливаем бота.")
        logger.error("Исправьте проблему и перезапустите бота.")
        sys.exit(1)
    
    send_startup_message()
    
    while True:
        try:
            new_count, error_count = fetch_and_publish()
            logger.info(f"Следующая проверка через {CHECK_INTERVAL} секунд ({CHECK_INTERVAL // 60} минут)")
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
            time.sleep(60)
    
    logger.info("Бот остановлен")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}", exc_info=True)
        sys.exit(1)
