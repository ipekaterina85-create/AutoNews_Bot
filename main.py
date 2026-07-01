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

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 1800))
NEWS_PER_SOURCE = int(os.environ.get('NEWS_PER_SOURCE', 5))  # Увеличили до 5
MAX_DESCRIPTION_LENGTH = int(os.environ.get('MAX_DESCRIPTION_LENGTH', 600))
ENABLE_TRANSLATION = os.environ.get('ENABLE_TRANSLATION', 'false').lower() == 'true'
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
ENABLE_HASHTAGS = os.environ.get('ENABLE_HASHTAGS', 'true').lower() == 'true'
MIN_SCORE = int(os.environ.get('MIN_SCORE', 3))  # Минимальный рейтинг для публикации
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
# РАСШИРЕННЫЙ СПИСОК ИСТОЧНИКОВ (20+)
# ============================================

RSS_FEEDS = [
    # 🇷🇺 Российские источники
    {
        'name': 'Авторевю',
        'url': 'https://autoreview.ru/feed/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'За рулём',
        'url': 'https://www.zr.ru/rss/news/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'Драйв',
        'url': 'https://www.drive.ru/rss/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'Авто.ру Журнал',
        'url': 'https://journal.auto.ru/feed/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'medium',
        'weight': 1.5
    },
    
    # 🇬🇧 Британские источники
    {
        'name': 'Autocar UK',
        'url': 'https://www.autocar.co.uk/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'Top Gear',
        'url': 'https://www.topgear.com/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'high',
        'weight': 2
    },
    {
        'name': 'Auto Express',
        'url': 'https://www.autoexpress.co.uk/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'medium',
        'weight': 1
    },
    
    # 🇺🇸 Американские источники
    {
        'name': 'Car and Driver',
        'url': 'https://www.caranddriver.com/rss/all.xml/',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high',
        'weight': 1.5
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
    {
        'name': 'AutoBlog',
        'url': 'https://www.autoblog.com/rss.xml',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'medium',
        'weight': 1
    },
    {
        'name': 'Motor1',
        'url': 'https://www.motor1.com/rss/news/all/',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'medium',
        'weight': 1
    },
    
    # 🌍 Международные
    {
        'name': 'InsideEVs',
        'url': 'https://insideevs.com/rss/all/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric',
        'weight': 2
    },
    {
        'name': 'Electrek',
        'url': 'https://electrek.co/rss/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'electric',
        'weight': 2
    },
    
    # 🏁 Автоспорт
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
        'name': 'Motorsport',
        'url': 'https://www.motorsport.com/rss/all/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'motorsport',
        'weight': 1.5
    },
]

# ============================================
# СИСТЕМА РЕЙТИНГА НОВОСТЕЙ
# ============================================

# Ключевые слова для оценки интересности (вес слова)
HOT_KEYWORDS = {
    # Топ-бренды (высокий интерес)
    'tesla': 3, 'bugatti': 3, 'ferrari': 3, 'lamborghini': 3, 'porsche': 2,
    'rolls-royce': 3, 'mclaren': 2, 'pagani': 3, 'koenigsegg': 3,
    
    # Электромобили и инновации
    'electric': 2, 'ev': 2, 'autonomous': 3, 'self-driving': 3, 'autopilot': 3,
    'battery': 2, 'charging': 2, 'hydrogen': 2, 'innovation': 2, 'revolutionary': 3,
    
    # Премьеры и релизы
    'unveiled': 2, 'revealed': 2, 'launch': 2, 'debut': 2, 'premiere': 2,
    'new model': 2, 'concept': 2, 'prototype': 2, 'first look': 2,
    
    # Автоспорт
    'f1': 2, 'formula 1': 2, 'wrc': 2, 'le mans': 2, 'nascar': 2,
    'championship': 2, 'victory': 2, 'record': 2, 'race': 1,
    
    # Скандалы и проблемы
    'recall': 2, 'crash': 2, 'accident': 2, 'lawsuit': 2, 'bankrupt': 3,
    'scandal': 3, 'controversy': 2, 'investigation': 2, 'fine': 2,
    
    # Цифры и статистика (обычно интересные)
    'million': 2, 'billion': 2, 'percent': 1, '%': 1,
    'fastest': 2, 'cheapest': 2, 'most expensive': 3, 'best-selling': 2,
    
    # Российские бренды и события
    'lada': 2, 'ваз': 2, 'aurus': 3, 'камаз': 2, 'уаз': 2,
    'москва': 1, 'россия': 1,
}

# Категории для рубрик
CATEGORIES = {
    'electric': {
        'keywords': ['tesla', 'electric', 'ev', 'battery', 'charging', 'электро', 'гибрид'],
        'emoji': '⚡',
        'name': 'Электрокары'
    },
    'motorsport': {
        'keywords': ['f1', 'formula', 'race', 'racing', 'wrc', 'гонк', 'чемпионат'],
        'emoji': '🏁',
        'name': 'Автоспорт'
    },
    'luxury': {
        'keywords': ['luxury', 'premium', 'rolls-royce', 'bentley', 'ferrari', 'lamborghini'],
        'emoji': '💎',
        'name': 'Люкс'
    },
    'innovation': {
        'keywords': ['autonomous', 'self-driving', 'ai', 'innovation', 'technology', 'инноваци'],
        'emoji': '🚀',
        'name': 'Инновации'
    },
    'russia': {
        'keywords': ['lada', 'ваз', 'aurus', 'россия', 'russia', 'москов'],
        'emoji': '🇷🇺',
        'name': 'Россия'
    },
}

def calculate_news_score(entry, feed_info):
    """
    Рассчитывает рейтинг новости (0-10)
    """
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    
    score = 0
    matched_keywords = []
    
    # 1. Проверка по ключевым словам
    for keyword, weight in HOT_KEYWORDS.items():
        if keyword in text:
            score += weight
            matched_keywords.append(keyword)
    
    # 2. Бонус за приоритет источника
    if feed_info.get('priority') == 'high':
        score += 1
    
    # 3. Бонус за вес источника
    score += feed_info.get('weight', 1) * 0.5
    
    # 4. Бонус за наличие изображения
    if get_image_url(entry):
        score += 0.5
    
    # 5. Бонус за длину заголовка (не слишком короткий)
    if 20 < len(title) < 100:
        score += 0.5
    
    return round(score, 2), matched_keywords

def get_news_category(entry, feed_info):
    """Определяет категорию новости"""
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    
    # Проверяем категории
    for cat_id, cat_info in CATEGORIES.items():
        for keyword in cat_info['keywords']:
            if keyword in text:
                return cat_id, cat_info
    
    # Категория из источника
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
    
    # Категория
    cat_id, cat_info = get_news_category(entry, feed_info)
    if cat_id != 'general':
        tags.append(f"#{cat_id}")
    
    # Регион
    region = feed_info.get('region', '')
    if '🇷🇺' in region:
        tags.append('#россия')
    elif '🇺🇸' in region:
        tags.append('#сша')
    elif '🇬🇧' in region:
        tags.append('#европа')
    elif '🇩🇪' in region:
        tags.append('#германия')
    
    # Бренды
    brands = {
        'tesla': '#tesla', 'toyota': '#toyota', 'bmw': '#bmw', 
        'mercedes': '#mercedes', 'audi': '#audi', 'volkswagen': '#vw',
        'porsche': '#porsche', 'ferrari': '#ferrari', 'lamborghini': '#lamborghini',
        'ford': '#ford', 'honda': '#honda', 'nissan': '#nissan',
        'hyundai': '#hyundai', 'kia': '#kia', 'lada': '#лада',
        'bugatti': '#bugatti', 'mclaren': '#mclaren'
    }
    
    for brand, tag in brands.items():
        if brand in text:
            tags.append(tag)
    
    return ' '.join(tags[:5])  # Максимум 5 хештегов

def format_message(entry, feed_info, score, category):
    """Улучшенное форматирование сообщения"""
    original_title = entry.get('title', 'Без названия')
    link = entry.get('link', '')
    original_summary = entry.get('summary', '')
    
    translated_title = original_title
    translated_summary = original_summary
    
    translated_summary = clean_html(translated_summary)
    
    if len(translated_summary) > MAX_DESCRIPTION_LENGTH:
        translated_summary = translated_summary[:MAX_DESCRIPTION_LENGTH] + '...'
    
    region = feed_info.get('region', '🌍')
    source_name = feed_info.get('name', 'Неизвестно')
    cat_emoji = category['emoji']
    cat_name = category['name']
    
    # Определяем уровень "горячести"
    if score >= 7:
        hot_indicator = "🔥🔥🔥 *ГОРЯЧАЯ НОВОСТЬ*"
    elif score >= 5:
        hot_indicator = "🔥🔥 *ТОП*"
    elif score >= 3:
        hot_indicator = "🔥 *ИНТЕРЕСНО*"
    else:
        hot_indicator = ""
    
    # Формируем сообщение
    message = ""
    
    # Заголовок с индикатором
    if hot_indicator:
        message += f"{hot_indicator}\n\n"
    
    message += f"{cat_emoji} *{translated_title}*\n\n"
    
    # Описание
    if translated_summary:
        message += f"{translated_summary}\n\n"
    
    # Разделитель
    message += "━━━━━━━━━━━━━━━━━━━\n"
    
    # Метаданные
    message += f"📊 *Рейтинг:* {score}/10\n"
    message += f"📰 *Источник:* {source_name} {region}\n"
    message += f"🏷️ *Категория:* {cat_name}\n"
    
    # Ссылка
    message += f"\n🔗 [Читать полностью]({link})\n\n"
    
    # Хештеги
    hashtags = generate_hashtags(entry, feed_info)
    if hashtags:
        message += f"{hashtags}"
    
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
    skipped_count = 0
    
    # Собираем все новости с рейтингом
    all_news = []
    
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
                
                if news_id in published:
                    continue
                
                # Рассчитываем рейтинг
                score, keywords = calculate_news_score(entry, feed_info)
                category_id, category_info = get_news_category(entry, feed_info)
                
                # Добавляем в список
                all_news.append({
                    'entry': entry,
                    'feed_info': feed_info,
                    'news_id': news_id,
                    'score': score,
                    'keywords': keywords,
                    'category': category_info
                })
                        
        except Exception as e:
            logger.error(f"Ошибка обработки источника {feed_info['name']}: {e}")
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
            logger.info(f"✅ Опубликована новость (рейтинг {score}): {title[:50]}...")
            time.sleep(3)
        else:
            error_count += 1
    
    logger.info(f"Цикл завершён. Опубликовано: {new_count}, Пропущено (низкий рейтинг): {skipped_count}, Ошибок: {error_count}")
    return new_count, error_count

def send_startup_message():
    try:
        startup_message = (
            "🤖 *Auto imPulse News Bot запущен!*\n\n"
            f"📊 Источников: {len(RSS_FEEDS)}\n"
            f"⏱️ Интервал проверки: {CHECK_INTERVAL // 60} минут\n"
            f"📈 Минимальный рейтинг: {MIN_SCORE}/10\n"
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
        logger.error(f"❌ Ошибка доступа к каналу: {e}")
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
