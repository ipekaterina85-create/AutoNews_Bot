import os
import time
import hashlib
import re
import logging
import signal
import sys
import base64
import json
from datetime import datetime
from pathlib import Path

import feedparser
import requests
from telebot import TeleBot, types
from telebot import apihelper
import jwt  # PyJWT для авторизации

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

# Яндекс Cloud credentials
YC_FOLDER_ID = os.environ.get('YC_FOLDER_ID')  # ID каталога
YC_SERVICE_ACCOUNT_ID = os.environ.get('YC_SERVICE_ACCOUNT_ID')  # ID сервисного аккаунта
YC_PRIVATE_KEY = os.environ.get('YC_PRIVATE_KEY')  # Приватный ключ (в формате PEM)

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

# ============================================
# ЯНДЕКС TRANSLATE API
# ============================================

class YandexTranslator:
    """Класс для работы с Яндекс Translate API"""
    
    def __init__(self, folder_id, service_account_id, private_key):
        self.folder_id = folder_id
        self.service_account_id = service_account_id
        self.private_key = private_key
        self.iam_token = None
        self.iam_token_expires = 0
        self.translate_url = "https://translate.api.cloud.yandex.net/translate/v2/translate"
        self.iam_url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
        
    def get_iam_token(self):
        """Получение IAM-токена для авторизации"""
        # Если токен ещё действителен — используем его
        if self.iam_token and time.time() < self.iam_token_expires - 60:
            return self.iam_token
        
        try:
            # Создаём JWT для получения IAM-токена
            now = int(time.time())
            payload = {
                'aud': 'https://iam.api.cloud.yandex.net/iam/v1/tokens',
                'iss': self.service_account_id,
                'iat': now,
                'exp': now + 3600
            }
            
            # Подписываем JWT
            encoded_token = jwt.encode(
                payload,
                self.private_key,
                algorithm='PS256',
                headers={'kid': self.service_account_id}
            )
            
            # Запрашиваем IAM-токен
            response = requests.post(
                self.iam_url,
                json={'jwt': encoded_token},
                timeout=10
            )
            response.raise_for_status()
            
            self.iam_token = response.json()['iamToken']
            self.iam_token_expires = time.time() + 3600  # Токен живёт 1 час
            
            logger.info("✅ IAM-токен Яндекс получен успешно")
            return self.iam_token
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения IAM-токена: {e}")
            raise
    
    def translate(self, text, source_lang='en', target_lang='ru'):
        """Перевод текста"""
        if not text or len(text.strip()) == 0:
            return ""
        
        try:
            # Ограничение на длину (Яндекс: 10000 символов за запрос)
            if len(text) > 9500:
                text = text[:9500]
            
            iam_token = self.get_iam_token()
            
            headers = {
                'Authorization': f'Bearer {iam_token}',
                'Content-Type': 'application/json'
            }
            
            data = {
                'folderId': self.folder_id,
                'texts': [text],
                'sourceLanguageCode': source_lang,
                'targetLanguageCode': target_lang
            }
            
            response = requests.post(
                self.translate_url,
                headers=headers,
                json=data,
                timeout=15
            )
            response.raise_for_status()
            
            result = response.json()
            if 'translations' in result and len(result['translations']) > 0:
                return result['translations'][0]['text']
            
            return text
            
        except Exception as e:
            logger.warning(f"Ошибка перевода Яндекс: {e}")
            return text

# Инициализация переводчика
if ENABLE_TRANSLATION:
    try:
        # Проверяем наличие всех необходимых переменных
        if not YC_FOLDER_ID or not YC_SERVICE_ACCOUNT_ID or not YC_PRIVATE_KEY:
            raise ValueError(
                "❌ Не настроены переменные Яндекс Cloud! "
                "Нужны: YC_FOLDER_ID, YC_SERVICE_ACCOUNT_ID, YC_PRIVATE_KEY"
            )
        
        # Преобразуем приватный ключ (может быть в base64)
        private_key = YC_PRIVATE_KEY
        if not private_key.startswith('-----BEGIN'):
            # Если ключ в base64 — декодируем
            try:
                private_key = base64.b64decode(private_key).decode('utf-8')
            except:
                pass
        
        translator = YandexTranslator(YC_FOLDER_ID, YC_SERVICE_ACCOUNT_ID, private_key)
        
        # Проверяем работоспособность
        test_translation = translator.translate("Hello world", "en", "ru")
        logger.info(f"✅ Яндекс Translate инициализирован. Тест: 'Hello world' → '{test_translation}'")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Яндекс Translate: {e}")
        raise
else:
    translator = None
    logger.info("Перевод отключён")

# ============================================
# RSS-ИСТОЧНИКИ
# ============================================

RSS_FEEDS = [
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
            logger.error(f"Ошибка загрузки: {e}")
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
    """Перевод текста через Яндекс Translate"""
    if not ENABLE_TRANSLATION or not translator:
        return text
    if not text or len(text.strip()) == 0:
        return ""
    
    try:
        return translator.translate(text, source_lang, 'ru')
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
    try:
        if image_url and ENABLE_IMAGES:
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
            f"🌐 Переводчик: Яндекс Translate {'✅' if ENABLE_TRANSLATION else '❌'}\n"
            f"🖼️ Изображения: {'✅' if ENABLE_IMAGES else '❌'}\n"
            f"🏷️ Хештеги: {'✅' if ENABLE_HASHTAGS else '❌'}\n\n"
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

def check_channel_access():
    try:
        logger.info(f"Проверяем доступ к каналу: {CHANNEL_ID}")
        chat = bot.get_chat(CHANNEL_ID)
        logger.info(f"✅ Канал найден: {chat.title}")
        
        admins = bot.get_chat_administrators(CHANNEL_ID)
        bot_username = bot.get_me().username
        is_admin = any(admin.user.username == bot_username for admin in admins)
        
        if is_admin:
            logger.info(f"✅ Бот @{bot_username} является администратором канала")
            return True
        else:
            logger.error(f"❌ Бот @{bot_username} НЕ является администратором канала!")
            return False
            
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к каналу:
