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
from telebot import TeleBot
from telebot import apihelper

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 1800))
NEWS_PER_SOURCE = int(os.environ.get('NEWS_PER_SOURCE', 5))
MAX_DESCRIPTION_LENGTH = int(os.environ.get('MAX_DESCRIPTION_LENGTH', 600))
ENABLE_TRANSLATION = os.environ.get('ENABLE_TRANSLATION', 'true').lower() == 'true'
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
ENABLE_HASHTAGS = os.environ.get('ENABLE_HASHTAGS', 'true').lower() == 'true'
MIN_SCORE = int(os.environ.get('MIN_SCORE', 3))
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

# Email для MyMemory (увеличивает лимит с 5000 до 50000 символов/день)
MYMEMORY_EMAIL = os.environ.get('MYMEMORY_EMAIL', '')

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
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================

bot = TeleBot(BOT_TOKEN)
apihelper.ENABLE_MIDDLEWARE = True

# ============================================
# MYMEMORY ПЕРЕВОДЧИК (БЕСПЛАТНО, БЕЗ КАРТ, БЕЗ API-КЛЮЧЕЙ)
# ============================================

class MyMemoryTranslator:
    """
    Бесплатный переводчик MyMemory
    - Без регистрации
    - Без API-ключей
    - Без карт
    - Лимит: 5000 символов/день (без email) или 50000 (с email)
    """
    
    def __init__(self, email=''):
        self.translate_url = "https://api.mymemory.translated.net/get"
        self.email = email
        self.last_call_time = 0
        self.min_interval = 1.5  # 1.5 сек между запросами
        self.daily_chars_used = 0
        self.daily_limit = 50000 if email else 5000
        
    def translate(self, text, source_lang='en', target_lang='ru'):
        """Перевод текста"""
        if not text or len(text.strip()) == 0:
            return ""
        
        # Если текст уже на русском — не переводим
        if self._is_russian(text):
            return text
        
        # Проверка дневного лимита
        if self.daily_chars_used + len(text) > self.daily_limit:
            logger.warning(f"⚠️ Дневной лимит MyMemory исчерпан ({self.daily_limit} символов)")
            return text
        
        try:
            # Защита от частых запросов
            now = time.time()
            time_since_last = now - self.last_call_time
            if time_since_last < self.min_interval:
                time.sleep(self.min_interval - time_since_last)
            
            # MyMemory лимит: 500 символов за запрос
            if len(text) > 480:
                parts = self._split_text(text, 480)
                translated_parts = []
                for part in parts:
                    translated_part = self._translate_chunk(part, source_lang, target_lang)
                    translated_parts.append(translated_part)
                    time.sleep(0.5)
                return ' '.join(translated_parts)
            
            result = self._translate_chunk(text, source_lang, target_lang)
            return result
            
        except Exception as e:
            logger.warning(f"Ошибка MyMemory: {e}")
            return text
    
    def _translate_chunk(self, text, source_lang, target_lang):
        """Перевод одного куска текста"""
        params = {
            'q': text,
            'langpair': f'{source_lang}|{target_lang}'
        }
        
        # Добавляем email для увеличения лимита
        if self.email:
            params['de'] = self.email
        
        response = requests.get(
            self.translate_url,
            params=params,
            timeout=15
        )
        response.raise_for_status()
        
        result = response.json()
        
        if 'responseStatus' in result and result['responseStatus'] == 200:
            if 'responseData' in result and result['responseData']:
                translated = result['responseData']['translatedText']
                self.last_call_time = time.time()
                self.daily_chars_used += len(text)
                return translated
        
        # Если основная база не дала результат — пробуем альтернативные переводы
        if 'matches' in result and result['matches']:
            # Берём перевод с наибольшим совпадением
            best_match = max(result['matches'], key=lambda x: x.get('match', 0))
            if best_match.get('match', 0) > 0.5:
                translated = best_match.get('translation', text)
                self.last_call_time = time.time()
                self.daily_chars_used += len(text)
                return translated
        
        return text
    
    def _split_text(self, text, max_length):
        """Разбиение текста на части по предложениям"""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        parts = []
        current_part = ""
        
        for sentence in sentences:
            if len(current_part) + len(sentence) + 1 <= max_length:
                current_part = current_part + " " + sentence if current_part else sentence
            else:
                if current_part:
                    parts.append(current_part)
                current_part = sentence
        
        if current_part:
            parts.append(current_part)
        
        return parts if parts else [text[:max_length]]
    
    def _is_russian(self, text):
        """Проверка, что текст уже на русском"""
        if not text:
            return False
        cyrillic = sum(1 for c in text if 'а' <= c.lower() <= 'я')
        letters = sum(1 for c in text if c.isalpha())
        if letters == 0:
            return False
        return (cyrillic / letters) > 0.5

# ============================================
# ИНИЦИАЛИЗАЦИЯ ПЕРЕВОДЧИКА
# ============================================

translator = None

if ENABLE_TRANSLATION:
    try:
        translator = MyMemoryTranslator(email=MYMEMORY_EMAIL)
        test = translator.translate("Hello world", "en", "ru")
        logger.info(f"✅ MyMemory инициализирован. Тест: 'Hello world' → '{test}'")
        if MYMEMORY_EMAIL:
            logger.info(f"📧 Email для MyMemory: {MYMEMORY_EMAIL} (лимит 50000 символов/день)")
        else:
            logger.info(f"⚠️ Email не указан. Лимит MyMemory: 5000 символов/день")
            logger.info(f"💡 Добавьте MYMEMORY_EMAIL в Railway для увеличения лимита до 50000")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации MyMemory: {e}")
        translator = None
        ENABLE_TRANSLATION = False
else:
    logger.info("Перевод отключён")

# ============================================
# РАБОЧИЕ RSS-ИСТОЧНИКИ (проверенные)
# ============================================

RSS_FEEDS = [
    # 🇷 РОССИЙСКИЕ ИСТОЧНИКИ (на русском, не переводим)
    {
        'name': 'Авто.ру Журнал',
        'url': 'https://auto.ru/journal/export/rss/all.xml',
        'lang': 'ru',
        'region': '🇷',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'Автостат',
        'url': 'https://www.autostat.ru/feed/',
        'lang': 'ru',
        'region': '🇷',
        'priority': 'medium',
        'weight': 1.5
    },
    {
        'name': 'F1-News.ru',
        'url': 'https://www.f1news.ru/rss/',
        'lang': 'ru',
        'region': '🌍',
        'priority': 'high',
        'category': 'motorsport',
        'weight': 2
    },
    {
        'name': 'Колёса.ру',
        'url': 'https://www.kolesa.ru/news/rss/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'Motor.ru',
        'url': 'https://motor.ru/rss/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'РГ (Авто)',
        'url': 'https://rg.ru/rss/auto.xml',
        'lang': 'ru',
        'region': '🇷',
        'priority': 'medium',
        'weight': 1.5
    },
    
    # 🇬🇧 БРИТАНСКИЕ ИСТОЧНИКИ
    {
        'name': 'Autocar UK',
        'url': 'https://www.autocar.co.uk/rss',
        'lang': 'en',
        'region': '🇬',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'Auto Express',
        'url': 'https://www.autoexpress.co.uk/rss',
        'lang': 'en',
        'region': '🇬',
        'priority': 'medium',
        'weight': 1
    },
    {
        'name': 'Top Gear',
        'url': 'https://www.topgear.com/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'high',
        'weight': 2
    },
    
    # 🇺🇸 АМЕРИКАНСКИЕ ИСТОЧНИКИ
    {
        'name': 'Car and Driver',
        'url': 'https://www.caranddriver.com/rss/all.xml/',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'Motor1',
        'url': 'https://www.motor1.com/rss/news/all/',
        'lang': 'en',
        'region': '🇺',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'AutoBlog',
        'url': 'https://www.autoblog.com/rss.xml',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'medium',
        'weight': 1
    },
    {
        'name': 'Road & Track',
        'url': 'https://www.roadandtrack.com/rss/all.xml/',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'The Drive',
        'url': 'https://www.thedrive.com/rss',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high',
        'weight': 1.5
    },
    
    # 🇩 НЕМЕЦКИЕ ИСТОЧНИКИ
    {
        'name': 'Auto Bild',
        'url': 'https://www.autobild.de/rss/rss.xml',
        'lang': 'de',
        'region': '🇩🇪',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'Auto Motor Sport',
        'url': 'https://www.auto-motor-und-sport.de/rss/',
        'lang': 'de',
        'region': '🇩🇪',
        'priority': 'high',
        'weight': 1.5
    },
    
    # 🇯🇵 ЯПОНСКИЕ ИСТОЧНИКИ
    {
        'name': 'Best Car Web',
        'url': 'https://www.bestcarweb.jp/rss',
        'lang': 'ja',
        'region': '🇯🇵',
        'priority': 'high',
        'weight': 2
    },
    
    # 🇨 КИТАЙСКИЕ ИСТОЧНИКИ
    {
        'name': 'Gasgoo',
        'url': 'https://autonews.gasgoo.com/rss',
        'lang': 'en',
        'region': '🇨',
        'priority': 'high',
        'weight': 2
    },
    
    # 🌍 МЕЖДУНАРОДНЫЕ (ЭЛЕКТРОМОБИЛИ)
    {
        'name': 'Electrek',
        'url': 'https://electrek.co/feed/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric',
        'weight': 2
    },
    {
        'name': 'CleanTechnica',
        'url': 'https://cleantechnica.com/feed/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric',
        'weight': 2
    },
    {
        'name': 'Green Car Reports',
        'url': 'https://www.greencarreports.com/rss',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric',
        'weight': 2
    },
    {
        'name': 'InsideEVs',
        'url': 'https://insideevs.com/rss/all/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric',
        'weight': 2
    },
    
    # 🏁 АВТОСПОРТ
    {
        'name': 'Autosport',
        'url': 'https://www.autosport.com/rss/feed/all',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'motorsport',
        'weight': 1.5
    },
    {
        'name': 'Planet F1',
        'url': 'https://www.planetf1.com/rss',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'motorsport',
        'weight': 2
    },
    {
        'name': 'Crash.net',
        'url': 'https://www.crash.net/rss',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'motorsport',
        'weight': 1.5
    },
    
    # 💎 ЛЮКС И СУПЕРКАРЫ
    {
        'name': 'Supercar Blondie',
        'url': 'https://supercarblondie.com/feed/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'luxury',
        'weight': 2
    },
]
# ============================================
# СИСТЕМА РЕЙТИНГА
# ============================================

HOT_KEYWORDS = {
    # Топ-бренды
    'tesla': 3, 'bugatti': 3, 'ferrari': 3, 'lamborghini': 3, 'porsche': 2,
    'rolls-royce': 3, 'mclaren': 3, 'pagani': 3, 'koenigsegg': 3,
    
    # Электромобили и инновации
    'electric': 2, 'ev': 2, 'autonomous': 3, 'self-driving': 3, 'autopilot': 3,
    'battery': 2, 'charging': 2, 'hydrogen': 2, 'revolutionary': 3,
    
    # Премьеры
    'unveiled': 2, 'revealed': 2, 'launch': 2, 'debut': 2, 'premiere': 2,
    'concept': 2, 'prototype': 2, 'new model': 2,
    
    # Автоспорт
    'f1': 2, 'formula 1': 2, 'wrc': 2, 'le mans': 2, 'championship': 2,
    'victory': 2, 'record': 2,
    
    # Скандалы и проблемы
    'recall': 2, 'crash': 2, 'accident': 2, 'bankrupt': 3, 'scandal': 3,
    
    # Цифры
    'million': 2, 'billion': 2, 'fastest': 2, 'most expensive': 3,
    'best-selling': 2,
    
    # Русские слова
    'премьера': 2, 'новый': 1, 'электрич': 2, 'гибрид': 2,
    'lada': 2, 'ваз': 2, 'aurus': 3, 'камаз': 2, 'уаз': 2,
    'тесла': 3, 'феррари': 3, 'ламборгини': 3, 'порше': 2,
}

CATEGORIES = {
    'electric': {
        'keywords': ['tesla', 'electric', 'ev', 'battery', 'charging', 'электро', 'гибрид', 'ion', 'ioniq', 'тесла'],
        'emoji': '⚡',
        'name': 'Электрокары'
    },
    'motorsport': {
        'keywords': ['f1', 'formula', 'race', 'racing', 'wrc', 'гонк', 'чемпионат'],
        'emoji': '🏁',
        'name': 'Автоспорт'
    },
    'luxury': {
        'keywords': ['luxury', 'premium', 'rolls-royce', 'bentley', 'ferrari', 'lamborghini', 'mclaren', 'феррари', 'ламборгини'],
        'emoji': '💎',
        'name': 'Люкс'
    },
    'innovation': {
        'keywords': ['autonomous', 'self-driving', 'ai', 'innovation', 'technology', 'инноваци'],
        'emoji': '🚀',
        'name': 'Инновации'
    },
    'russia': {
        'keywords': ['lada', 'ваз', 'aurus', 'россия', 'russia', 'москв', 'уаз', 'камаз'],
        'emoji': '🇷🇺',
        'name': 'Россия'
    },
}

def calculate_news_score(entry, feed_info):
    """Рассчитывает рейтинг новости (0-10+)"""
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    
    score = 0
    matched_keywords = []
    
    for keyword, weight in HOT_KEYWORDS.items():
        if keyword in text:
            score += weight
            matched_keywords.append(keyword)
    
    if feed_info.get('priority') == 'high':
        score += 1
    
    score += feed_info.get('weight', 1) * 0.5
    
    if get_image_url(entry):
        score += 0.5
    
    if 20 < len(entry.get('title', '')) < 100:
        score += 0.5
    
    return round(score, 2), matched_keywords

def get_news_category(entry, feed_info):
    """Определяет категорию новости"""
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    
    for cat_id, cat_info in CATEGORIES.items():
        for keyword in cat_info['keywords']:
            if keyword in text:
                return cat_id, cat_info
    
    if 'category' in feed_info:
        cat_id = feed_info['category']
        if cat_id in CATEGORIES:
            return cat_id, CATEGORIES[cat_id]
    
    return 'general', {'emoji': '🚗', 'name': 'Новости'}

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
    """Перевод текста"""
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
    
    cat_id, cat_info = get_news_category(entry, feed_info)
    if cat_id != 'general':
        tags.append(f"#{cat_id}")
    
    region = feed_info.get('region', '')
    if '🇷🇺' in region:
        tags.append('#россия')
    elif '🇺🇸' in region:
        tags.append('#сша')
    elif '🇬🇧' in region:
        tags.append('#европа')
    
    brands = {
        'tesla': '#tesla', 'toyota': '#toyota', 'bmw': '#bmw', 
        'mercedes': '#mercedes', 'audi': '#audi', 'volkswagen': '#vw',
        'porsche': '#porsche', 'ferrari': '#ferrari', 'lamborghini': '#lamborghini',
        'ford': '#ford', 'honda': '#honda', 'nissan': '#nissan',
        'hyundai': '#hyundai', 'kia': '#kia', 'lada': '#лада',
        'bugatti': '#bugatti', 'mclaren': '#mclaren', 'rolls-royce': '#rollsroyce'
    }
    
    for brand, tag in brands.items():
        if brand in text:
            tags.append(tag)
            break
    
    return ' '.join(tags[:5])

def format_message(entry, feed_info, score, category):
    """Форматирование сообщения для Telegram"""
    original_title = entry.get('title', 'Без названия')
    link = entry.get('link', '')
    original_summary = entry.get('summary', '')
    
    # Переводим только если источник НЕ на русском
    source_lang = feed_info.get('lang', 'en')
    
    if ENABLE_TRANSLATION and translator and source_lang != 'ru':
        translated_title = translate_text(original_title, source_lang)
        translated_summary = translate_text(original_summary, source_lang)
    else:
        translated_title = original_title
        translated_summary = original_summary
    
    translated_summary = clean_html(translated_summary)
    
    if len(translated_summary) > MAX_DESCRIPTION_LENGTH:
        translated_summary = translated_summary[:MAX_DESCRIPTION_LENGTH] + '...'
    
    region = feed_info.get('region', '🌍')
    source_name = feed_info.get('name', 'Неизвестно')
    cat_emoji = category['emoji']
    cat_name = category['name']
    
    # Индикатор "горячести"
    if score >= 7:
        hot_indicator = "🔥🔥🔥 *ГОРЯЧАЯ НОВОСТЬ*\n\n"
    elif score >= 5:
        hot_indicator = "🔥🔥 *ТОП*\n\n"
    elif score >= 3:
        hot_indicator = "🔥 *ИНТЕРЕСНО*\n\n"
    else:
        hot_indicator = ""
    
    message = hot_indicator
    message += f"{cat_emoji} *{translated_title}*\n\n"
    
    if translated_summary:
        message += f"{translated_summary}\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━\n"
    message += f"📊 Рейтинг: {score}/10\n"
    message += f"📰 Источник: {source_name} {region}\n"
    message += f"🏷️ Категория: {cat_name}\n"
    message += f"\n🔗 [Читать полностью]({link})\n\n"
    
    hashtags = generate_hashtags(entry, feed_info)
    if hashtags:
        message += hashtags
    
    return message, translated_title

def send_news_to_channel(message, image_url=None):
    """Отправка новости в канал"""
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
    skipped_count = 0
    
    all_news = []
    
    logger.info(f"Начинаем проверку {len(RSS_FEEDS)} источников...")
    
    for feed_info in RSS_FEEDS:
        try:
            logger.info(f"Проверяем: {feed_info['name']}")
            
            feed = feedparser.parse(
                feed_info['url'],
                request_headers={'User-Agent': 'AutoImPulseBot/1.0'}
            )
            
            if feed.bozo and not feed.entries:
                logger.warning(f"Ошибка RSS {feed_info['name']}: {feed.bozo_exception}")
                continue
            
            for entry in feed.entries[:NEWS_PER_SOURCE]:
                news_id = get_news_id(entry)
                
                if news_id in published:
                    continue
                
                score, keywords = calculate_news_score(entry, feed_info)
                category_id, category_info = get_news_category(entry, feed_info)
                
                all_news.append({
                    'entry': entry,
                    'feed_info': feed_info,
                    'news_id': news_id,
                    'score': score,
                    'keywords': keywords,
                    'category': category_info
                })
                        
        except Exception as e:
            logger.error(f"Ошибка {feed_info['name']}: {e}")
            error_count += 1
            continue
    
    # Сортируем по рейтингу (от высокого к низкому)
    all_news.sort(key=lambda x: x['score'], reverse=True)
    
    logger.info(f"Собрано {len(all_news)} новостей")
    
    # Публикуем только новости с рейтингом >= MIN_SCORE
    for news_data in all_news:
        if news_data['score'] < MIN_SCORE:
            skipped_count += 1
            continue
        
        entry = news_data['entry']
        feed_info = news_data['feed_info']
        news_id = news_data['news_id']
        score = news_data['score']
        category = news_data['category']
        
        message, title = format_message(entry, feed_info, score, category)
        image_url = get_image_url(entry)
        
        if send_news_to_channel(message, image_url):
            save_published(news_id)
            new_count += 1
            logger.info(f"✅ Опубликована (рейтинг {score}): {title[:50]}...")
            time.sleep(3)
        else:
            error_count += 1
    
    logger.info(f"Итог: ✅{new_count} | ⏭️{skipped_count} | ❌{error_count}")
    return new_count, error_count

def send_startup_message():
    try:
        startup_message = (
            "🤖 *Auto imPulse News Bot запущен!*\n\n"
            f"📊 Источников: {len(RSS_FEEDS)}\n"
            f"⏱️ Интервал: {CHECK_INTERVAL // 60} мин\n"
            f"📈 Мин. рейтинг: {MIN_SCORE}/10\n"
            f"🌐 Перевод: {'✅ MyMemory' if ENABLE_TRANSLATION else '❌'}\n"
            f"🖼️ Изображения: {'✅' if ENABLE_IMAGES else '❌'}\n"
            f"🏷️ Хештеги: {'✅' if ENABLE_HASHTAGS else '❌'}\n\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
        logger.info(f"Проверяем канал: {CHANNEL_ID}")
        chat = bot.get_chat(CHANNEL_ID)
        logger.info(f"✅ Канал: {chat.title}")
        
        admins = bot.get_chat_administrators(CHANNEL_ID)
        bot_username = bot.get_me().username
        is_admin = any(admin.user.username == bot_username for admin in admins)
        
        if is_admin:
            logger.info(f"✅ Бот @{bot_username} — админ канала")
            return True
        else:
            logger.error(f"❌ Бот не админ!")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка канала: {e}")
        return False

def main():
    logger.info("=" * 50)
    logger.info("Auto imPulse News Bot запускается...")
    logger.info("=" * 50)
    
    if not check_channel_access():
        logger.error("❌ Нет доступа к каналу!")
        sys.exit(1)
    
    send_startup_message()
    
    while True:
        try:
            new_count, error_count = fetch_and_publish()
            logger.info(f"Следующая проверка через {CHECK_INTERVAL // 60} минут")
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Остановка")
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
