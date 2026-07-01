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
# КОНФИГУРАЦИЯ (все из переменных окружения Railway)
# ============================================

# Обязательные переменные
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')  # @username или -100xxxxxxxxxx

# Опциональные переменные с дефолтными значениями
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 1800))  # 30 минут
NEWS_PER_SOURCE = int(os.environ.get('NEWS_PER_SOURCE', 3))   # новостей с источника
MAX_DESCRIPTION_LENGTH = int(os.environ.get('MAX_DESCRIPTION_LENGTH', 500))
ENABLE_TRANSLATION = os.environ.get('ENABLE_TRANSLATION', 'true').lower() == 'true'
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
ENABLE_HASHTAGS = os.environ.get('ENABLE_HASHTAGS', 'true').lower() == 'true'
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не установлен в переменных окружения Railway!")
if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID не установлен в переменных окружения Railway!")

# ============================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================

# Создаём папку для логов
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

# Настройка прокси (если нужно для обхода блокировок)
PROXY_URL = os.environ.get('PROXY_URL')
if PROXY_URL:
    apihelper.proxy = {'https': PROXY_URL}
    logger.info(f"Используется прокси: {PROXY_URL}")

# Инициализация переводчика
if ENABLE_TRANSLATION:
    translator = GoogleTranslator(source='auto', target='ru')
    logger.info("Перевод включён")
else:
    translator = None
    logger.info("Перевод отключён")

# ============================================
# ИСТОЧНИКИ НОВОСТЕЙ (RSS)
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
        'name': 'Auto Express',
        'url': 'https://www.autoexpress.co.uk/rss',
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
        'name': 'Motor Trend',
        'url': 'https://www.motortrend.com/rss/',
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
    
    # Немецкие источники
    {
        'name': 'Auto Motor und Sport',
        'url': 'https://www.auto-motor-und-sport.de/rss',
        'lang': 'de',
        'region': '🇩🇪',
        'priority': 'medium'
    },
    
    # Электромобили и технологии
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
        'name': 'Motorsport.com',
        'url': 'https://www.motorsport.com/rss/all/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'medium',
        'category': 'motorsport'
    },
]

# ============================================
# ХРАНИЛИЩЕ ОПУБЛИКОВАННЫХ НОВОСТЕЙ
# ============================================

PUBLISHED_FILE = 'published_news.txt'
MAX_PUBLISHED_HISTORY = 10000  # Максимум записей в файле

def load_published():
    """Загрузка списка уже опубликованных новостей"""
    if os.path.exists(PUBLISHED_FILE):
        try:
            with open(PUBLISHED_FILE, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            logger.error(f"Ошибка загрузки published_news.txt: {e}")
            return set()
    return set()

def save_published(news_id):
    """Сохранение ID опубликованной новости"""
    try:
        with open(PUBLISHED_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{news_id}\n")
        
        # Очистка старых записей если файл слишком большой
        cleanup_published_file()
    except Exception as e:
        logger.error(f"Ошибка сохранения published_news.txt: {e}")

def cleanup_published_file():
    """Очистка файла от старых записей"""
    try:
        if not os.path.exists(PUBLISHED_FILE):
            return
        
        with open(PUBLISHED_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if len(lines) > MAX_PUBLISHED_HISTORY:
            # Оставляем только последние записи
            with open(PUBLISHED_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines[-MAX_PUBLISHED_HISTORY:])
            logger.info(f"Очищен файл published_news.txt, оставлено {MAX_PUBLISHED_HISTORY} записей")
    except Exception as e:
        logger.error(f"Ошибка очистки файла: {e}")

# ============================================
# УТИЛИТЫ
# ============================================

def get_news_id(entry):
    """Генерация уникального ID для новости"""
    unique_str = f"{entry.get('title', '')}{entry.get('link', '')}"
    return hashlib.md5(unique_str.encode('utf-8')).hexdigest()

def translate_text(text, source_lang='en'):
    """Перевод текста на русский"""
    if not ENABLE_TRANSLATION or not translator:
        return text
    
    if not text or len(text.strip()) == 0:
        return ""
    
    try:
        # Ограничение на длину текста
        if len(text) > 5000:
            text = text[:5000]
        
        translated = translator.translate(text)
        return translated if translated else text
    except Exception as e:
        logger.warning(f"Ошибка перевода: {e}")
        return text  # Возвращаем оригинал при ошибке

def clean_html(text):
    """Очистка HTML-тегов из текста"""
    if not text:
        return ""
    clean_text = re.sub('<[^<]+?>', '', text)
    clean_text = re.sub(r'\s+', ' ', clean_text)
    return clean_text.strip()

def get_image_url(entry):
    """Извлечение изображения из новости"""
    if not ENABLE_IMAGES:
        return None
    
    try:
        # Проверяем media_content
        if 'media_content' in entry and entry.media_content:
            for media in entry.media_content:
                if media.get('url'):
                    return media['url']
        
        # Проверяем enclosures
        if 'enclosures' in entry and entry.enclosures:
            for enclosure in entry.enclosures:
                if enclosure.get('href'):
                    return enclosure['href']
        
        # Проверяем media_thumbnail
        if 'media_thumbnail' in entry and entry.media_thumbnail:
            for thumbnail in entry.media_thumbnail:
                if thumbnail.get('url'):
                    return thumbnail['url']
        
        # Ищем изображение в содержимом
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
    """Генерация хештегов на основе содержания новости"""
    if not ENABLE_HASHTAGS:
        return ""
    
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    
    tags = ['#автоновости']
    
    # Категория электромобилей
    if feed_info.get('category') == 'electric' or any(word in text for word in 
        ['tesla', 'electric', 'ev', 'battery', 'charging', 'электро', 'электромобил']):
        tags.append('#электрокары')
    
    # Автоспорт
    if feed_info.get('category') == 'motorsport' or any(word in text for word in 
        ['f1', 'formula', 'racing', 'wrc', 'motogp', 'le mans', 'гонк']):
        tags.append('#автоспорт')
    
    # Регионы
    region = feed_info.get('region', '')
    if '🇺🇸' in region:
        tags.append('#сша')
    elif '🇬🇧' in region:
        tags.append('#европа')
    elif '🇩🇪' in region:
        tags.append('#германия')
    elif '🇯🇵' in region:
        tags.append('#япония')
    elif '🇨🇳' in region:
        tags.append('#китай')
    
    # Бренды
    brands = {
        'tesla': '#tesla', 'toyota': '#toyota', 'bmw': '#bmw', 
        'mercedes': '#mercedes', 'audi': '#audi', 'volkswagen': '#vw',
        'porsche': '#porsche', 'ferrari': '#ferrari', 'lamborghini': '#lamborghini',
        'ford': '#ford', 'chevrolet': '#chevrolet', 'honda': '#honda',
        'nissan': '#nissan', 'mazda': '#mazda', 'subaru': '#subaru',
        'hyundai': '#hyundai', 'kia': '#kia', 'lexus': '#lexus'
    }
    
    for brand, tag in brands.items():
        if brand in text:
            tags.append(tag)
            break  # Только один бренд
    
    return ' '.join(tags)

def format_message(entry, feed_info):
    """Форматирование сообщения для Telegram"""
    # Оригинальные данные
    original_title = entry.get('title', 'Без названия')
    link = entry.get('link', '')
    original_summary = entry.get('summary', '')
    
    # Перевод
    if ENABLE_TRANSLATION:
        translated_title = translate_text(original_title, feed_info.get('lang', 'en'))
        translated_summary = translate_text(original_summary, feed_info.get('lang', 'en'))
    else:
        translated_title = original_title
        translated_summary = original_summary
    
    # Очистка HTML
    translated_summary = clean_html(translated_summary)
    
    # Ограничение длины описания
    if len(translated_summary) > MAX_DESCRIPTION_LENGTH:
        translated_summary = translated_summary[:MAX_DESCRIPTION_LENGTH] + '...'
    
    # Форматирование сообщения
    region = feed_info.get('region', '🌍')
    source_name = feed_info.get('name', 'Неизвестно')
    
    message = f"{region} *{translated_title}*\n\n"
    
    if translated_summary:
        message += f"{translated_summary}\n\n"
    
    message += f"🔗 [Читать оригинал]({link})\n"
    message += f"📰 Источник: {source_name}"
    
    # Добавляем хештеги
    hashtags = generate_hashtags(entry, feed_info)
    if hashtags:
        message += f"\n\n{hashtags}"
    
    return message, translated_title

def send_news_to_channel(message, image_url=None):
    """Отправка новости в канал"""
    try:
        if image_url and ENABLE_IMAGES:
            # Отправляем с изображением
            bot.send_photo(
                CHANNEL_ID,
                image_url,
                caption=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        else:
            # Отправляем только текст
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
    """Получение новостей и публикация в канал"""
    published = load_published()
    new_count = 0
    error_count = 0
    
    logger.info(f"Начинаем проверку {len(RSS_FEEDS)} источников новостей...")
    
    for feed_info in RSS_FEEDS:
        try:
            logger.info(f"Проверяем источник: {feed_info['name']}")
            
            # Парсинг RSS с таймаутом
            feed = feedparser.parse(
                feed_info['url'],
                request_headers={'User-Agent': 'AutoImPulseBot/1.0'}
            )
            
            if feed.bozo and not feed.entries:
                logger.warning(f"Ошибка парсинга RSS {feed_info['name']}: {feed.bozo_exception}")
                continue
            
            # Берём последние N новостей из каждого источника
            for entry in feed.entries[:NEWS_PER_SOURCE]:
                news_id = get_news_id(entry)
                
                if news_id not in published:
                    message, title = format_message(entry, feed_info)
                    image_url = get_image_url(entry)
                    
                    if send_news_to_channel(message, image_url):
                        save_published(news_id)
                        new_count += 1
                        logger.info(f"✅ Опубликована новость: {title[:50]}...")
                        
                        # Задержка между публикациями (чтобы не спамить)
                        time.sleep(3)
                    else:
                        error_count += 1
                else:
                    logger.debug(f"Новость уже опубликована: {title[:50]}...")
                    
        except Exception as e:
            logger.error(f"Ошибка обработки источника {feed_info['name']}: {e}")
            error_count += 1
            continue
    
    logger.info(f"Цикл завершён. Опубликовано: {new_count}, Ошибок: {error_count}")
    return new_count, error_count

def send_startup_message():
    """Отправка сообщения о запуске бота"""
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
        
        # Отправляем в канал (опционально, можно закомментировать)
        # bot.send_message(CHANNEL_ID, startup_message, parse_mode='Markdown')
        
        logger.info("Бот успешно запущен!")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки стартового сообщения: {e}")
        return False

def graceful_shutdown(signum, frame):
    """Корректное завершение работы"""
    logger.info("Получен сигнал завершения, останавливаем бота...")
    sys.exit(0)

# Регистрируем обработчики сигналов
signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

# ============================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================

def main():
    """Основной цикл работы бота"""
    logger.info("=" * 50)
    logger.info("Auto imPulse News Bot запускается...")
    logger.info("=" * 50)
    
    # Отправляем стартовое сообщение
    send_startup_message()
    
    # Основной цикл
    while True:
        try:
            new_count, error_count = fetch_and_publish()
            
            logger.info(f"Следующая проверка через {CHECK_INTERVAL} секунд ({CHECK_INTERVAL // 60} минут)")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("Получен сигнал остановки (Ctrl+C)")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в главном цикле: {e}", exc_info=True)
            logger.info("Пауза 60 секунд перед повторной попыткой...")
            time.sleep(60)
    
    logger.info("Бот остановлен")

# ============================================
# HEALTH CHECK (для Railway)
# ============================================

def health_check():
    """Проверка работоспособности для Railway"""
    try:
        # Проверяем подключение к Telegram API
        bot_info = bot.get_me()
        logger.info(f"Health check OK. Bot: @{bot_info.username}")
        return True
    except Exception as e:
        logger.error(f"Health check FAILED: {e}")
        return False

# ============================================
# ТОЧКА ВХОДА
# ============================================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}", exc_info=True)
        sys.exit(1)
