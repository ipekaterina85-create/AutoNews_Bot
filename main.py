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
# MYMEMORY + АВТО-ГЛОССАРИЙ (УЛУЧШЕННЫЙ ПЕРЕВОД)
# ============================================

class MyMemoryTranslator:
    """Улучшенный переводчик MyMemory с глоссарием авто-терминов"""
    
    def __init__(self, email=''):
        self.translate_url = "https://api.mymemory.translated.net/get"
        self.email = email
        self.last_call_time = 0
        self.min_interval = 1.5
        self.daily_chars_used = 0
        self.daily_limit = 50000 if email else 5000
        
        # 🚗 ГЛОССАРИЙ АВТОМОБИЛЬНЫХ ТЕРМИНОВ
        self.auto_glossary = {
            # КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ (из анализа канала)
            'самосвал': 'внедорожник',
            'самосвала': 'внедорожника',
            'самосвалу': 'внедорожнику',
            'самосвалом': 'внедорожником',
            'самосвале': 'внедорожнике',
            'самосвалы': 'внедорожники',
            'самосвалов': 'внедорожников',
            'anti-ev': 'анти-электрического',
            'Anti-EV': 'анти-электрического',
            '1-из-1': 'единственный в своём роде',
            '1-of-1': 'единственный в своём роде',
            'one-of-one': 'единственный в своём роде',
            'tags nostalgia': 'отсылает к ностальгии',
            're-': '',  # Убираем обрезанные слова
            'ледяных': 'бензиновых',
            'ледяной': 'бензиновой',
            'ледяные': 'бензиновые',
            'ледяные панд': 'бензиновых Panda',
            
            'сломает крышку': 'будет представлен',
            'сломала крышку': 'была представлена',
            'сломало крышку': 'было представлено',
            
            'диапазон': 'запас хода',
            'диапазона': 'запаса хода',
            'диапазоне': 'запасе хода',
            'диапазоном': 'запасом хода',
            'диапазоны': 'запасы хода',
            'диапазонов': 'запасов хода',
            
            'фатальную': 'смертельную',
            'фатальной': 'смертельной',
            'фатальный': 'смертельный',
            'фатальная': 'смертельная',
            'фатальные': 'смертельные',
            
            'фунтов-футов': 'Нм',
            'фунт-футов': 'Нм',
            'фунтов-фут': 'Нм',
            'lb-ft': 'Нм',
            'lb ft': 'Нм',
            
            'священное имя': 'легендарное имя',
            'священное название': 'легендарное название',
            
            'свободной настройке выхлопных газов': 'свободно текущей выхлопной системе',
            'свободная настройка выхлопных газов': 'свободно текущая выхлопная система',
            
            # Технические термины
            'лошадь': 'лошадиных сил',
            'лошади': 'лошадиных сил',
            'лошадей': 'лошадиных сил',
            'конская сила': 'лошадиная сила',
            'конские силы': 'лошадиные силы',
            'момент кручения': 'крутящий момент',
            
            # Электрокары
            'подзарядка': 'зарядка',
            'подзарядки': 'зарядки',
            'подзарядку': 'зарядку',
            'батарея': 'аккумулятор',
            'батареи': 'аккумуляторы',
            'батарею': 'аккумулятор',
            'батарее': 'аккумуляторе',
            'батарей': 'аккумуляторов',
            
            # Бизнес и рынок
            'дилерство': 'автосалон',
            'дилерства': 'автосалоны',
            'дилерству': 'автосалону',
            'производитель': 'автопроизводитель',
            'производителя': 'автопроизводителя',
            
            # Автоспорт
            'прямых': 'прямых участках трассы',
            'на прямых': 'на прямых участках трассы',
            'с половиной десятых': 'на полдесятых секунды',
            'десятых': 'десятых секунды',
            
            # Идиомы и фразы
            'плывя против прилива': 'идя против тренда',
            'плывет против прилива': 'идёт против тренда',
            'против прилива': 'против тренда',
            
            'обдумывает': 'рассматривает',
            'стремится предложить': 'планирует предложить',
            
            # Цены и рынок
            'стартовая цена': 'начальная цена',
            'базовая цена': 'начальная цена',
            'базовая модель': 'базовая версия',
            'топ-модель': 'топ-версия',
            
            # Юбилеи и версии
            'юбилейной отделке': 'юбилейной версии',
            'юбилейная отделка': 'юбилейная версия',
            'первую серийную': 'первый серийный',
            
            # Частые ошибки MyMemory
            'разоблачил': 'представил',
            'разоблачила': 'представила',
            'разоблачено': 'представлено',
            'раскрыл': 'представил',
            'раскрыла': 'представила',
            'раскрыто': 'представлено',
            'раскрытие': 'премьера',
            'раскрытия': 'премьеры',
            'раскрытию': 'премьере',
            'дебют': 'премьера',
            'открыл': 'представил',
            'открыла': 'представила',
            'открыто': 'представлено',
        }
        
        # Специальные замены для целых фраз
        self.phrase_replacements = [
            ('breaks cover', 'будет представлен'),
            ('broke cover', 'был представлен'),
            ('break cover', 'будет представлен'),
            
            ('ICE population', 'парк бензиновых автомобилей'),
            ('ICE Panda', 'бензиновая Panda'),
            ('ICE vehicles', 'автомобили с ДВС'),
            
            ('family SUV', 'семейный внедорожник'),
            ('family hauler', 'семейный внедорожник'),
            
            ('fatal crash', 'смертельная авария'),
            ('fatal accident', 'смертельная авария'),
            ('fatal collision', 'смертельное столкновение'),
            
            ('miles range', 'миль запаса хода'),
            ('mile range', 'миль запаса хода'),
            ('0-60 mph', '0-100 км/ч'),
            ('0-60mph', '0-100 км/ч'),
            ('horsepower', 'л.с.'),
            ('hp', 'л.с.'),
            ('bhp', 'л.с.'),
            ('lb-ft of torque', 'Нм крутящего момента'),
            ('lb-ft', 'Нм'),
            ('top speed', 'максимальная скорость'),
            
            ('base price', 'начальная цена'),
            ('starting at', 'от'),
            ('starts at', 'от'),
            ('starting price', 'начальная цена'),
            ('goes on sale', 'поступит в продажу'),
            ('on sale now', 'уже в продаже'),
            ('available in', 'доступен в'),
            
            ('unveiled', 'представлен'),
            ('revealed', 'представлен'),
            ('launched', 'запущен в производство'),
            ('introduced', 'представлен'),
            
            ('on the straights', 'на прямых участках трассы'),
            ('on straights', 'на прямых'),
            ('half a tenth', 'полдесятых секунды'),
            ('tenths of a second', 'десятых секунды'),
            
            ('swimming against the tide', 'идя против тренда'),
            ('against the tide', 'против тренда'),
            ('anti-ev tide', 'тренда против электромобилей'),
            
            ('according to', 'по данным'),
            ('spokesperson', 'представитель'),
            ('press release', 'пресс-релиз'),
            ('model year', 'модельный год'),
            ('new generation', 'новое поколение'),
            ('next generation', 'следующее поколение'),
            ('all-electric', 'полностью электрический'),
            ('plug-in hybrid', 'подключаемый гибрид'),
            ('mild hybrid', 'мягкий гибрид'),
            ('free-flowing exhaust', 'свободно текущая выхлопная система'),
        ]
    
    def translate(self, text, source_lang='en', target_lang='ru'):
        """Перевод текста с пост-обработкой"""
        if not text or len(text.strip()) == 0:
            return ""
        
        if self._is_russian(text):
            return text
        
        try:
            if self.daily_chars_used + len(text) > self.daily_limit:
                logger.warning(f"⚠️ Дневной лимит MyMemory исчерпан")
                return text
            
            now = time.time()
            if now - self.last_call_time < self.min_interval:
                time.sleep(self.min_interval - (now - self.last_call_time))
            
            if len(text) > 450:
                parts = self._split_text(text, 450)
                translated_parts = []
                for part in parts:
                    translated_part = self._translate_with_postprocess(part, source_lang, target_lang)
                    translated_parts.append(translated_part)
                    time.sleep(0.5)
                result = ' '.join(translated_parts)
            else:
                result = self._translate_with_postprocess(text, source_lang, target_lang)
            
            return result
            
        except Exception as e:
            logger.warning(f"Ошибка MyMemory: {e}")
            return text
    
    def _translate_with_postprocess(self, text, source_lang, target_lang):
        """Перевод с пост-обработкой"""
        translated = self._translate_chunk(text, source_lang, target_lang)
        translated = self._apply_glossary(translated)
        translated = self._final_cleanup(translated)
        return translated
    
    def _translate_chunk(self, text, source_lang, target_lang):
        """Перевод одного куска через MyMemory API"""
        params = {
            'q': text,
            'langpair': f'{source_lang}|{target_lang}',
            'de': self.email if self.email else 'autoimpulse@mymemory.com'
        }
        
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
        
        if 'matches' in result and result['matches']:
            best_match = max(result['matches'], key=lambda x: x.get('match', 0))
            if best_match.get('match', 0) > 0.5:
                translated = best_match.get('translation', text)
                self.last_call_time = time.time()
                self.daily_chars_used += len(text)
                return translated
        
        return text
    
    def _apply_glossary(self, text):
        """Применение глоссария авто-терминов"""
        result = text
        sorted_terms = sorted(self.auto_glossary.items(), key=lambda x: len(x[0]), reverse=True)
        
        for wrong, correct in sorted_terms:
            pattern = r'\b' + re.escape(wrong) + r'\b'
            result = re.sub(pattern, correct, result, flags=re.IGNORECASE)
        
        return result
    
    def _final_cleanup(self, text):
        """Финальная очистка перевода"""
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\s+([.,!?;:])', r'\1', text)
        text = re.sub(r'\. ([а-я])', lambda m: '. ' + m.group(1).upper(), text)
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        
        return text.strip()
    
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
        """Проверка, что текст на русском"""
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
        
        tests = [
            ("Tesla unveiled new Model S with 500 miles range", "Tesla"),
            ("The car has 450 horsepower and 600 lb-ft of torque", "Мощность"),
            ("BMW recalls 10000 vehicles due to battery issue", "Отзыв"),
            ("Family SUV with 300 miles range breaks cover", "Внедорожник"),
        ]
        
        logger.info(f"✅ MyMemory Translator инициализирован")
        if MYMEMORY_EMAIL:
            logger.info(f"📧 Email: {MYMEMORY_EMAIL} (лимит 50000 символов/день)")
        else:
            logger.info(f"⚠️ Email не указан. Добавьте MYMEMORY_EMAIL для увеличения лимита")
        
        logger.info(f"🧪 Тесты перевода:")
        for en_text, hint in tests:
            ru_text = translator.translate(en_text, "en", "ru")
            logger.info(f"   EN: {en_text}")
            logger.info(f"   RU: {ru_text}")
            logger.info("")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации MyMemory: {e}")
        translator = None
        ENABLE_TRANSLATION = False
else:
    logger.info("Перевод отключён")

# ============================================
# ВСЕ RSS-ИСТОЧНИКИ (РОССИЯ + ЗАРУБЕЖЬЕ)
# ============================================

RSS_FEEDS = [
           # 🇷🇺 РОССИЙСКИЕ ИСТОЧНИКИ (ПРОВЕРЕННЫЕ РАБОЧИЕ)
    {
        'name': 'Дром',
        'url': 'https://www.drom.ru/export/xml/news.rss',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2.0,
        'category': 'russia'
    },
    {
        'name': 'Ведомости Авто',
        'url': 'https://www.vedomosti.ru/rss/rubric/auto',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'high',
        'weight': 2.0,
        'category': 'russia'
    },
    {
        'name': 'Автостат',
        'url': 'https://www.autostat.ru/news/rss/',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'medium',
        'weight': 1.5,
        'category': 'russia'
    },
    {
        'name': 'Коммерсантъ Автопилот',
        'url': 'https://www.kommersant.ru/RSS/auto.xml',
        'lang': 'ru',
        'region': '🇷🇺',
        'priority': 'medium',
        'weight': 1.5,
        'category': 'russia'
    },
    
    # 🇬🇧 БРИТАНСКИЕ ИСТОЧНИКИ
    {
        'name': 'Autocar UK',
        'url': 'https://www.autocar.co.uk/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'high',
        'weight': 1.5
    },
    {
        'name': 'Auto Express',
        'url': 'https://www.autoexpress.co.uk/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'medium',
        'weight': 1
    },
    {
        'name': 'Evo Magazine',
        'url': 'https://www.evo.co.uk/rss',
        'lang': 'en',
        'region': '🇬🇧',
        'priority': 'medium',
        'weight': 1
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
    
    # 🌍 ЭЛЕКТРОМОБИЛИ
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
        'url': 'https://www.planetf1.com/feed/',
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
    
    # 💎 ЛЮКС
    {
        'name': 'Supercar Blondie',
        'url': 'https://supercarblondie.com/feed/',
        'lang': 'en',
        'region': '🌍',
        'priority': 'high',
        'category': 'luxury',
        'weight': 2
    },
    
    # 📰 НОВОСТНЫЕ АГЕНТСТВА
    {
        'name': 'CNBC Autos',
        'url': 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15837362',
        'lang': 'en',
        'region': '🇺🇸',
        'priority': 'high',
        'weight': 1.5
    },
]

# ============================================
# СИСТЕМА РЕЙТИНГА
# ============================================

HOT_KEYWORDS = {
    # 🇷🇺 РОССИЙСКИЕ КЛЮЧЕВЫЕ СЛОВА (высокий приоритет)
    'lada': 4, 'лада': 4, 'ваз': 4, 'уаз': 4, 'камаз': 4,
    'aurus': 4, 'москвич': 4, 'европротокол': 4,
    'россия': 3, 'российский': 3, 'российская': 3,
    'автоваз': 4, 'sollers': 3, 'белджи': 3, 'хавейл': 3,
    'chery': 3, 'haval': 3, 'geely': 3, 'changan': 3,
    'отзыв': 3, 'отзывают': 3, 'отозван': 3,
    'штраф': 3, 'штрафы': 3, 'пдд': 3, 'гаи': 3, 'гибдд': 3,
    'автосалон': 3, 'дилер': 3, 'дилеры': 3,
    'акциз': 3, 'налог': 3, 'утилизационный сбор': 4,
    'китайские авто': 3, 'китайский автомобиль': 3,
    'parallel import': 3, 'параллельный импорт': 3,
    'цена': 2, 'цены': 2, 'стоимость': 2, 'подорожание': 3,
    'продажи': 3, 'статистика': 2, 'рынок': 2,
    'дтп': 3, 'авария': 3, 'катастрофа': 3,
    'премьера': 3, 'презентация': 3, 'новинка': 3,
    'формула 1': 3, 'ф1': 3,
    'миллион': 2, 'миллиард': 2,
    'электрокар': 3, 'электромобиль': 3,
    
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
    'гибрид': 2,
    'тесла': 3, 'феррари': 3, 'ламборгини': 3, 'порше': 2,
}

CATEGORIES = {
    'russia': {
        'keywords': ['lada', 'лада', 'ваз', 'уаз', 'камаз', 'aurus', 'москвич', 
                     'автоваз', 'россия', 'российский', 'chery', 'haval', 'geely',
                     'штраф', 'пдд', 'гибдд', 'отзыв', 'дилер', 'акциз'],
        'emoji': '🇷🇺',
        'name': 'Россия'
    },
    'electric': {
        'keywords': ['tesla', 'electric', 'ev', 'battery', 'charging', 'электро', 'гибрид', 'ion', 'ioniq', 'тесла', 'электрокар', 'электромобиль'],
        'emoji': '⚡',
        'name': 'Электрокары'
    },
    'motorsport': {
        'keywords': ['f1', 'formula', 'race', 'racing', 'wrc', 'гонк', 'чемпионат', 'формула', 'ф1'],
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
    elif feed_info.get('priority') == 'medium':
        score += 0.5  # Было +1 для high, теперь +0.5 для medium
    
    score += feed_info.get('weight', 1) * 0.5
    
    if get_image_url(entry):
        score += 0.5
    
    if 20 < len(entry.get('title', '')) < 100:
        score += 0.5
    
    # 🇺 КОРРЕКТИРОВКА: понижаем рейтинг российских новостей на 30%
    # чтобы они составляли ~15-17% от публикаций (1 из 6-7)
    if feed_info.get('lang') == 'ru':
        score *= 1.7
    
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
    clean_text = clean_text.strip()
    
    # Убираем обрезанные слова в конце (например, "re-", "anti-")
    if clean_text.endswith('-') or clean_text.endswith('...'):
        # Находим последнее полное слово
        words = clean_text.split()
        if len(words) > 1:
            # Убираем последнее слово если оно обрезано
            if words[-1].endswith('-') or len(words[-1]) < 3:
                clean_text = ' '.join(words[:-1])
    
    return clean_text
    
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
        'bugatti': '#bugatti', 'mclaren': '#mclaren', 'rolls-royce': '#rollsroyce',
        'chery': '#chery', 'haval': '#haval', 'geely': '#geely',
        'changan': '#changan', 'uaz': '#уаз', 'kamaz': '#камаз'
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
    """Отправка новости в канал с обработкой ошибок"""
    try:
        if image_url and ENABLE_IMAGES:
            try:
                bot.send_photo(
                    CHANNEL_ID,
                    image_url,
                    caption=message,
                    parse_mode='Markdown'
                )
                return True
            except Exception as img_error:
                # Если изображение не отправилось, отправляем без него
                logger.warning(f"⚠️ Не удалось отправить изображение: {img_error}")
                bot.send_message(
                    CHANNEL_ID,
                    message,
                    parse_mode='Markdown',
                    disable_web_page_preview=False
                )
                return True
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
            f"🌐 Перевод: {'✅ MyMemory + глоссарий' if ENABLE_TRANSLATION else '❌'}\n"
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
