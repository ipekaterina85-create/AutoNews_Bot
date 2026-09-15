import os
import time
import hashlib
import re
import html
import logging
import signal
import sys
import socket
import ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from telebot import TeleBot
from telebot import apihelper
from deep_translator import GoogleTranslator, MyMemoryTranslator

# Устанавливаем timeout для всех запросов
socket.setdefaulttimeout(30)

# Отключаем строгую проверку SSL (компромисс для старых RSS-лент)
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 1800))
NEWS_PER_SOURCE = int(os.environ.get('NEWS_PER_SOURCE', 3))
MAX_DESCRIPTION_LENGTH = int(os.environ.get('MAX_DESCRIPTION_LENGTH', 600))
ENABLE_TRANSLATION = os.environ.get('ENABLE_TRANSLATION', 'true').lower() == 'true'
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
ENABLE_HASHTAGS = os.environ.get('ENABLE_HASHTAGS', 'true').lower() == 'true'
MIN_SCORE = int(os.environ.get('MIN_SCORE', 5))
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

# Если перевод недоступен — НЕ публиковать новость на языке оригинала
PUBLISH_UNTRANSLATED = os.environ.get('PUBLISH_UNTRANSLATED', 'false').lower() == 'true'

# 🎯 ЛИМИТЫ
DAILY_POST_LIMIT = int(os.environ.get('DAILY_POST_LIMIT', 25))
MOTORSPORT_DAILY_LIMIT = int(os.environ.get('MOTORSPORT_DAILY_LIMIT', 2))

# 🌙 НОЧНОЙ РЕЖИМ (по Московскому времени, UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))
PUBLISH_START_HOUR = int(os.environ.get('PUBLISH_START_HOUR', 7))
PUBLISH_END_HOUR = int(os.environ.get('PUBLISH_END_HOUR', 24))

# 🕐 ГИБКИЕ ЧАСЫ ПУБЛИКАЦИИ (пиковые часы)
PEAK_HOURS = [
    (7, 10),    # Утренний пик
    (11, 14),   # Обеденный пик
    (17, 22),   # Вечерний пик
]

# Максимум постов за один цикл
MAX_POSTS_PER_CYCLE = 1

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
# УМНЫЙ ПЕРЕВОДЧИК: DeepL → Google → MyMemory
# ============================================

class SmartTranslator:
    """Цепочка переводчиков с автоматическим фолбэком.
    - DeepL (если задан DEEPL_API_KEY) — основной, стабильный, 500k симв/мес бесплатно.
    - Google — если DeepL нет; при бане (rate limit / 500) уходит на паузу 10 минут.
    - MyMemory — резервный, режет текст на чанки по 480 символов (его лимит).
    После перевода текст полируется авто-глоссарием."""

    def __init__(self):
        self.google = GoogleTranslator(source='auto', target='ru')
        self.min_interval = 1.5          # пауза между запросами к Google (анти-бан)
        self.last_call_time = 0
        self.google_pause_until = 0

        self.deepl = None
        deepl_key = os.environ.get('DEEPL_API_KEY', '')
        if deepl_key:
            try:
                # В разных версиях библиотеки класс зовётся DeeplTranslator или DeepLTranslator
                try:
                    from deep_translator import DeeplTranslator
                except ImportError:
                    from deep_translator import DeepLTranslator as DeeplTranslator
                try:
                    self.deepl = DeeplTranslator(api_key=deepl_key, source='en', target='ru',
                                                 use_free_api=deepl_key.endswith(':fx'))
                except TypeError:
                    self.deepl = DeeplTranslator(api_key=deepl_key, source='en', target='ru')
                logger.info("✅ DeepL подключён основным переводчиком")
            except Exception as e:
                logger.warning(f"DeepL не подключился: {e}")

        # Авто-глоссарий: правит УЖЕ переведённый русский текст
        self.auto_glossary = {
            'лошадиных сил': 'л.с.',
            'лошадиные силы': 'л.с.',
            'лошадиная сила': 'л.с.',
            'диапазон': 'запас хода',
            'диапазона': 'запаса хода',
            'диапазоне': 'запасе хода',
            'диапазоном': 'запасом хода',
            'фунтов-футов': 'Нм',
            'фунт-футов': 'Нм',
            'фут-фунтов': 'Нм',
            'внедорожник': 'кроссовер',
            'внедорожника': 'кроссовера',
            'внедорожнику': 'кроссоверу',
            'внедорожником': 'кроссовером',
            'внедорожнике': 'кроссовере',
            'внедорожники': 'кроссоверы',
            'внедорожников': 'кроссоверов',
            'аккумулятор': 'батарея',
            'аккумуляторы': 'батареи',
            'аккумуляторе': 'батарее',
            'аккумуляторов': 'батарей',
            'автосалон': 'официальный дилер',
            'автосалоны': 'официальные дилеры',
            'автопроизводитель': 'производитель',
            'автопроизводителя': 'производителя',
            'представил': 'рассекретил',
            'представила': 'рассекретила',
            'представлено': 'рассекречено',
            'премьера': 'дебют',
            'премьеры': 'дебюта',
            'премьере': 'дебюту',
            'подключаемый гибрид': 'гибрид PHEV',
            'мягкий гибрид': 'гибрид MHEV',
            'полностью электрический': 'чистый электрокар',
            'японский внутренний рынок': 'JDM',
            'начальная цена': 'стартовая цена',
            'базовая версия': 'базовая комплектация',
            'топ-версия': 'топовая комплектация',
        }

    # ---------- точка входа ----------
    def translate(self, text, source_lang='auto', target_lang='ru'):
        if not text or not text.strip():
            return ""
        if self._is_russian(text):
            return text

        result = None

        # 1) DeepL, если задан ключ
        if self.deepl is not None:
            result = self._try_deepl(text, source_lang)

        # 2) Google, если не на паузе
        if result is None and time.time() >= self.google_pause_until:
            result = self._try_google(text)
            if result is None:
                self.google_pause_until = time.time() + 600
                logger.warning("⏸️ Google заблокирован — пауза 10 минут, переходим на MyMemory")

        # 3) MyMemory
        if result is None:
            result = self._try_mymemory(text, source_lang)

        if result is None:
            logger.error("❌ Все переводчики недоступны")
            return ""   # пустая строка = сигнал «перевода нет»

        return self._final_cleanup(self._apply_glossary(result))

    # ---------- бэкенды ----------
    def _try_deepl(self, text, source_lang):
        try:
            auth_key = os.environ.get('DEEPL_API_KEY', '')
            if not auth_key:
                return None
        
            # Free API использует api-free.deepl.com, Pro — api.deepl.com
            base_url = 'https://api-free.deepl.com' if auth_key.endswith(':fx') else 'https://api.deepl.com'
        
            src_map = {'en': 'EN', 'ja': 'JA', 'ru': 'RU', 'de': 'DE', 
                       'fr': 'FR', 'zh': 'ZH', 'ko': 'KO'}
            source = src_map.get(source_lang, 'EN')
        
            response = requests.post(
                f"{base_url}/v2/translate",
                data={
                    'auth_key': auth_key,
                    'text': text,
                    'source_lang': source,
                    'target_lang': 'RU'
                },
                timeout=10
            )
        
            if response.status_code == 200:
                result = response.json()
                translated = result['translations'][0]['text']
                return translated if translated and self._is_russian(translated) else None
            else:
                logger.warning(f"DeepL HTTP {response.status_code}: {response.text[:100]}")
                return None
        except Exception as e:
            logger.warning(f"DeepL ошибка: {str(e)[:120]}")
            return None

    def _try_google(self, text):
        try:
            now = time.time()
            if now - self.last_call_time < self.min_interval:
                time.sleep(self.min_interval - (now - self.last_call_time))
            res = self.google.translate(text)
            self.last_call_time = time.time()
            return res if res and self._is_russian(res) else None
        except Exception as e:
            logger.warning(f"Google ошибка: {str(e)[:120]}")
            return None

    # MyMemory требует полные региональные коды, а не 'en'/'ru'
    MM_LANGS = {
        'en': 'en-GB', 'ja': 'ja-JP', 'de': 'de-DE', 'fr': 'fr-FR',
        'ru': 'ru-RU', 'zh': 'zh-CN', 'ko': 'ko-KR', 'es': 'es-ES', 'it': 'it-IT',
    }

    def _try_mymemory(self, text, source_lang):
        try:
            src = self.MM_LANGS.get(source_lang, 'en-GB')
            mm = MyMemoryTranslator(source=src, target='ru-RU')
            out = []
            for chunk in self._split_480(text):
                res = mm.translate(chunk)
                if not res:
                    return None
                out.append(res)
                time.sleep(0.5)
            result = ' '.join(out)
            return result if result and self._is_russian(result) else None
        except Exception as e:
            logger.warning(f"MyMemory ошибка: {str(e)[:120]}")
            return None

    # ---------- вспомогательные ----------
    @staticmethod
    def _split_480(text, limit=480):
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks, cur = [], ""
        for s in sentences:
            if len(cur) + len(s) + 1 <= limit:
                cur = (cur + " " + s).strip()
            else:
                if cur:
                    chunks.append(cur)
                while len(s) > limit:
                    chunks.append(s[:limit])
                    s = s[limit:]
                cur = s
        if cur:
            chunks.append(cur)
        return chunks or [text[:limit]]

    def _apply_glossary(self, text):
        result = text
        for wrong, correct in sorted(self.auto_glossary.items(), key=lambda x: len(x[0]), reverse=True):
            result = re.sub(r'\b' + re.escape(wrong) + r'\b', correct, result, flags=re.IGNORECASE)
        return result

    @staticmethod
    def _final_cleanup(text):
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\s+([.,!?;:])', r'\1', text)
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        return text.strip()

    @staticmethod
    def _is_russian(text):
        if not text:
            return False
        cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я')
        let = sum(1 for c in text if c.isalpha())
        return let > 0 and (cyr / let) > 0.5


# ============================================
# СОЗДАНИЕ ЭКЗЕМПЛЯРА ПЕРЕВОДЧИКА
# (имя translator существует ВСЕГДА — иначе NameError в публикациях)
# ============================================

translator = None

if ENABLE_TRANSLATION:
    try:
        translator = SmartTranslator()
        logger.info("✅ SmartTranslator инициализирован (цепочка: DeepL → Google → MyMemory)")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации переводчика: {e}")
        translator = None
        ENABLE_TRANSLATION = False
else:
    logger.info("Перевод отключён")

# ============================================
# RSS-ИСТОЧНИКИ (мёртвые ленты удалены)
# ============================================

RSS_FEEDS = [
    # 🇷🇺 РОССИЯ
    {'name': 'Дром', 'url': 'https://www.drom.ru/export/xml/news.rss', 'lang': 'ru',
     'region': '🇷🇺', 'country': 'russia', 'priority': 'high', 'weight': 2.0, 'category': 'russia'},
    {'name': 'Ведомости Авто', 'url': 'https://www.vedomosti.ru/rss/rubric/auto', 'lang': 'ru',
     'region': '🇷🇺', 'country': 'russia', 'priority': 'high', 'weight': 2.0, 'category': 'russia'},
    {'name': 'Коммерсантъ Автопилот', 'url': 'https://www.kommersant.ru/RSS/auto.xml', 'lang': 'ru',
     'region': '🇷🇺', 'country': 'russia', 'priority': 'medium', 'weight': 1.5, 'category': 'russia'},
    {'name': 'Автостат', 'url': 'https://www.autostat.ru/news/rss/', 'lang': 'ru',
     'region': '🇷🇺', 'country': 'russia', 'priority': 'medium', 'weight': 1.5, 'category': 'russia'},

    # 🇨🇳 КИТАЙ
    {'name': 'CarNewsChina', 'url': 'https://www.carnewschina.com/feed/', 'lang': 'en',
     'region': '🇨🇳', 'country': 'china', 'priority': 'high', 'weight': 2.0, 'category': 'china'},
    {'name': 'CnEVPost', 'url': 'https://cnevpost.com/feed/', 'lang': 'en',
     'region': '🇨🇳', 'country': 'china', 'priority': 'high', 'weight': 2.0, 'category': 'china'},

    # 🇯🇵 ЯПОНИЯ
    {'name': 'Response.jp', 'url': 'https://response.jp/index.rdf', 'lang': 'ja',
     'region': '🇯🇵', 'country': 'japan', 'priority': 'high', 'weight': 1.8, 'category': 'japan'},
    {'name': 'Car Watch', 'url': 'https://car.watch.impress.co.jp/index.rdf', 'lang': 'ja',
     'region': '🇯🇵', 'country': 'japan', 'priority': 'medium', 'weight': 1.5, 'category': 'japan'},

    # 🇰🇷 КОРЕЯ
    {'name': 'Yonhap News Auto', 'url': 'https://en.yna.co.kr/rss/auto.xml', 'lang': 'en',
     'region': '🇰🇷', 'country': 'korea', 'priority': 'high', 'weight': 1.8, 'category': 'korea'},
    {'name': 'Korean Car Blog', 'url': 'https://www.koreancarblog.com/feed/', 'lang': 'en',
     'region': '🇰🇷', 'country': 'korea', 'priority': 'medium', 'weight': 1.5, 'category': 'korea'},

    # 🇧🇾 БЕЛАРУСЬ
    {'name': 'Onliner Авто', 'url': 'https://auto.onliner.by/feed', 'lang': 'ru',
     'region': '🇧🇾', 'country': 'belarus', 'priority': 'high', 'weight': 1.8, 'category': 'cis'},
    {'name': 'ABW.BY', 'url': 'https://www.abw.by/rss', 'lang': 'ru',
     'region': '🇧🇾', 'country': 'belarus', 'priority': 'high', 'weight': 1.8, 'category': 'cis'},

    # 🇰🇿 КАЗАХСТАН
    {'name': 'Tengrinews (Авто/Экономика)', 'url': 'https://tengrinews.kz/rss/', 'lang': 'ru',
     'region': '🇰🇿', 'country': 'kazakhstan', 'priority': 'high', 'weight': 1.8, 'category': 'cis'},

    # 🇦🇲 АРМЕНИЯ
    {'name': '1in.am (Авто/Происшествия)', 'url': 'https://1in.am/rss/', 'lang': 'ru',
     'region': '🇦🇲', 'country': 'armenia', 'priority': 'medium', 'weight': 1.3, 'category': 'cis'},

    # 🇦🇿 АЗЕРБАЙДЖАН
    {'name': 'Trend News (Авто/Экономика)', 'url': 'https://trend.az/rss/', 'lang': 'ru',
     'region': '🇦🇿', 'country': 'azerbaijan', 'priority': 'medium', 'weight': 1.3, 'category': 'cis'},

    # 🇰🇬 КЫРГЫЗСТАН
    {'name': '24.kg (Авто/Общество)', 'url': 'https://24.kg/rss/', 'lang': 'ru',
     'region': '🇰🇬', 'country': 'kyrgyzstan', 'priority': 'medium', 'weight': 1.3, 'category': 'cis'},

    # 🇲🇩 МОЛДОВА
    {'name': 'Auto.MD', 'url': 'https://auto.md/feed/', 'lang': 'ru',
     'region': '🇲🇩', 'country': 'moldova', 'priority': 'medium', 'weight': 1.3, 'category': 'cis'},

    # 🇬🇧 БРИТАНИЯ
    {'name': 'Autocar UK', 'url': 'https://www.autocar.co.uk/rss', 'lang': 'en',
     'region': '🇬🇧', 'country': 'uk', 'priority': 'high', 'weight': 1.5},
    {'name': 'Auto Express', 'url': 'https://www.autoexpress.co.uk/rss', 'lang': 'en',
     'region': '🇬🇧', 'country': 'uk', 'priority': 'medium', 'weight': 1.0},

    # 🇺🇸 США
    {'name': 'Car and Driver', 'url': 'https://www.caranddriver.com/rss/all.xml/', 'lang': 'en',
     'region': '🇺🇸', 'country': 'usa', 'priority': 'high', 'weight': 1.5},
    {'name': 'Motor1', 'url': 'https://www.motor1.com/rss/news/all/', 'lang': 'en',
     'region': '🇺🇸', 'country': 'usa', 'priority': 'high', 'weight': 1.5},
    {'name': 'Road & Track', 'url': 'https://www.roadandtrack.com/rss/all.xml/', 'lang': 'en',
     'region': '🇺🇸', 'country': 'usa', 'priority': 'high', 'weight': 1.5},
    {'name': 'The Drive', 'url': 'https://www.thedrive.com/rss', 'lang': 'en',
     'region': '🇺🇸', 'country': 'usa', 'priority': 'high', 'weight': 1.5},

    # 🌍 ЭЛЕКТРОМОБИЛИ
    {'name': 'Electrek', 'url': 'https://electrek.co/feed/', 'lang': 'en',
     'region': '🌍', 'country': 'world', 'priority': 'high', 'category': 'electric', 'weight': 2.0},
    {'name': 'CleanTechnica', 'url': 'https://cleantechnica.com/feed/', 'lang': 'en',
     'region': '🌍', 'country': 'world', 'priority': 'high', 'category': 'electric', 'weight': 2.0},
    {'name': 'Green Car Reports', 'url': 'https://www.greencarreports.com/rss', 'lang': 'en',
     'region': '🌍', 'country': 'world', 'priority': 'high', 'category': 'electric', 'weight': 2.0},

    # 🏁 АВТОСПОРТ (лимит 2/сутки)
    {'name': 'Autosport', 'url': 'https://www.autosport.com/rss/feed/all', 'lang': 'en',
     'region': '🌍', 'country': 'world', 'priority': 'medium', 'category': 'motorsport',
     'weight': 1.0, 'max_per_cycle': 2},
    {'name': 'Crash.net', 'url': 'https://www.crash.net/rss', 'lang': 'en',
     'region': '🌍', 'country': 'world', 'priority': 'medium', 'category': 'motorsport',
     'weight': 1.0, 'max_per_cycle': 2},

    # 💎 ЛЮКС
    {'name': 'Supercar Blondie', 'url': 'https://supercarblondie.com/feed/', 'lang': 'en',
     'region': '🌍', 'country': 'world', 'priority': 'high', 'category': 'luxury', 'weight': 2.0},

    # 📰 АГЕНТСТВА
    {'name': 'CNBC Autos',
     'url': 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15837362',
     'lang': 'en', 'region': '🇺🇸', 'country': 'usa', 'priority': 'high', 'weight': 1.5},
]

# ============================================
# СИСТЕМА РЕЙТИНГА
# ============================================

HOT_KEYWORDS = {
    'lada': 4, 'лада': 4, 'ваз': 4, 'уаз': 4, 'камаз': 4,
    'aurus': 4, 'москвич': 4, 'европротокол': 4,
    'россия': 3, 'российский': 3, 'российская': 3,
    'автоваз': 4, 'sollers': 3,
    'chery': 3, 'haval': 3, 'geely': 3, 'changan': 3,
    'byd': 3, 'zeekr': 3, 'nio': 3, 'xpeng': 3, 'avatr': 3, 'jac': 3,
    'отзыв': 3, 'отзывают': 3, 'отозван': 3,
    'штраф': 3, 'штрафы': 3, 'пдд': 3, 'гаи': 3, 'гибдд': 3,
    'акциз': 3, 'налог': 3, 'утилизационный сбор': 4,
    'параллельный импорт': 3,
    'цена': 2, 'цены': 2, 'стоимость': 2, 'подорожание': 3,
    'продажи': 3, 'статистика': 2, 'рынок': 2,
    'дтп': 3, 'авария': 3, 'катастрофа': 3,
    'премьера': 3, 'презентация': 3, 'новинка': 3,
    'формула 1': 3, 'ф1': 3,
    'миллион': 2, 'миллиард': 2,
    'электрокар': 3, 'электромобиль': 3,
    'беларусь': 3, 'белоруссия': 3, 'минск': 2,
    'казахстан': 3, 'казахский': 2, 'алматы': 2, 'астана': 2,
    'армения': 3, 'армянский': 2, 'ереван': 2,
    'азербайджан': 3, 'азербайджанский': 2, 'баку': 2,
    'кыргызстан': 3, 'киргизия': 3, 'бишкек': 2,
    'молдова': 3, 'молдавский': 2, 'кишинев': 2,
    'тесла': 3, 'грузия': 2,
    'tesla': 3, 'bugatti': 3, 'ferrari': 3, 'lamborghini': 3, 'porsche': 2,
    'rolls-royce': 3, 'mclaren': 3, 'pagani': 3, 'koenigsegg': 3,
    'electric': 2, 'ev': 2, 'autonomous': 3, 'self-driving': 3, 'autopilot': 3,
    'battery': 2, 'charging': 2, 'hydrogen': 2, 'revolutionary': 3,
    'unveiled': 2, 'revealed': 2, 'launch': 2, 'debut': 2, 'premiere': 2,
    'concept': 2, 'prototype': 2, 'new model': 2,
    'f1': 1, 'formula 1': 1, 'wrc': 1, 'le mans': 1,
    'championship': 1, 'victory': 1, 'race': 0.5, 'racing': 0.5,
    'moto': 0.5, 'motogp': 0.5, 'grand prix': 1, 'podium': 0.5,
    'recall': 2, 'crash': 2, 'accident': 2, 'bankrupt': 3, 'scandal': 3,
    'million': 2, 'billion': 2, 'fastest': 2, 'most expensive': 3,
    'best-selling': 2,
    'гибрид': 2, 'jdm': 2, 'кей-кар': 2,
    'феррари': 3, 'ламборгини': 3, 'порше': 2, 'genesis': 3,
    # мировые бренды (EN + RU)
    'toyota': 2, 'тойота': 2, 'honda': 2, 'хонда': 2, 'nissan': 2, 'ниссан': 2,
    'mazda': 2, 'мазда': 2, 'subaru': 2, 'субару': 2, 'mitsubishi': 2, 'мицубиси': 2,
    'suzuki': 2, 'сузуки': 2, 'lexus': 2, 'лексус': 2, 'bmw': 2, 'бмв': 2,
    'mercedes': 2, 'мерседес': 2, 'audi': 2, 'ауди': 2,
    'volkswagen': 2, 'vw': 2, 'фольксваген': 2,
    'ford': 2, 'форд': 2, 'hyundai': 2, 'хендай': 2, 'хёндэ': 2,
    'kia': 2, 'киа': 2, 'volvo': 2, 'вольво': 2, 'skoda': 2, 'шкода': 2,
    'renault': 2, 'рено': 2, 'peugeot': 2, 'пежо': 2, 'citroen': 2, 'ситроен': 2,
    'chevrolet': 2, 'шевроле': 2, 'jeep': 2, 'джип': 2,
    'land rover': 2, 'ленд ровер': 2, 'range rover': 2, 'рендж ровер': 2,
    'jaguar': 2, 'ягуар': 2, 'дженезис': 2,
    # общие авто-термины
    'кроссовер': 2, 'внедорожник': 2, 'седан': 2, 'пикап': 2,
    'двигатель': 2, 'мотор': 2, 'коробка': 2, 'вариатор': 2, 'робот': 2,
    'подвеск': 2, 'тормоз': 2, 'пробег': 2, 'кузов': 2, 'рестайлинг': 2,
    'комплектаци': 2, 'привод': 2, 'тюнинг': 2, 'engine': 2, 'suv': 2,
    'sedan': 2, 'pickup': 2, 'vehicle': 1, 'mileage': 1,
}

CATEGORIES = {
    'russia': {
        'keywords': ['lada', 'лада', 'ваз', 'уаз', 'камаз', 'aurus', 'москвич',
                     'автоваз', 'россия', 'российский', 'chery', 'haval', 'geely',
                     'штраф', 'пдд', 'гибдд', 'отзыв', 'дилер', 'акциз'],
        'emoji': '🇷', 'name': 'Россия'
    },
    'cis': {
        'keywords': ['беларусь', 'белоруссия', 'казахстан', 'армения',
                     'азербайджан', 'кыргызстан', 'молдова', 'грузия', 'минск',
                     'алматы', 'астана', 'ереван', 'баку', 'бишкек', 'кишинев'],
        'emoji': '🌐', 'name': 'СНГ/ЕАЭС'
    },
    'china': {
        'keywords': ['china', 'chinese', 'китай', 'китайский', 'byd', 'chery',
                     'geely', 'hongqi', 'zeekr', 'li auto', 'nio', 'xpeng', 'avatr', 'jac'],
        'emoji': '🇨', 'name': 'Китай'
    },
    'japan': {
        'keywords': ['japan', 'japanese', 'япония', 'японский', 'toyota', 'honda',
                     'nissan', 'mazda', 'subaru', 'lexus', 'mitsubishi', 'suzuki'],
        'emoji': '🇯', 'name': 'Япония'
    },
    'korea': {
        'keywords': ['korea', 'korean', 'корея', 'корейский', 'hyundai', 'kia',
                     'genesis', 'kg mobility', 'ssangyong'],
        'emoji': '🇰🇷', 'name': 'Корея'
    },
    'electric': {
        'keywords': ['tesla', 'electric', 'ev', 'battery', 'charging', 'электро',
                     'гибрид', 'ion', 'ioniq', 'тесла', 'электрокар', 'электромобиль'],
        'emoji': '⚡', 'name': 'Электрокары'
    },
    'motorsport': {
        'keywords': ['f1', 'formula', 'race', 'racing', 'wrc', 'гонк', 'чемпионат',
                     'формула', 'ф1', 'motogp', 'grand prix', 'podium'],
        'emoji': '🏁', 'name': 'Автоспорт'
    },
    'luxury': {
        'keywords': ['luxury', 'premium', 'rolls-royce', 'bentley', 'ferrari',
                     'lamborghini', 'mclaren', 'феррари', 'ламборгини'],
        'emoji': '💎', 'name': 'Люкс'
    },
    'innovation': {
        'keywords': ['autonomous', 'self-driving', 'ai', 'innovation', 'technology', 'инноваци'],
        'emoji': '🚀', 'name': 'Инновации'
    },
}

FLAGS = {
    'russia': '🇷🇺', 'belarus': '🇧🇾', 'kazakhstan': '🇰🇿', 'armenia': '🇦🇲',
    'azerbaijan': '🇦🇿', 'kyrgyzstan': '🇰🇬', 'moldova': '🇲🇩', 'china': '🇨🇳',
    'japan': '🇯🇵', 'korea': '🇰🇷', 'uk': '🇬🇧', 'usa': '🇺🇸', 'world': '🌍',
}

# Маркеры технических страниц-заглушек, которые нельзя публиковать
GARBAGE_MARKERS = ('error 500', "that's an error", 'server error',
                   "that's all we know", 'error 404', 'just a moment')


def calculate_news_score(entry, feed_info):
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
        score += 0.5

    score += feed_info.get('weight', 1) * 0.5

    if get_image_url(entry):
        score += 0.5

    if 20 < len(entry.get('title', '')) < 100:
        score += 0.5

    if feed_info.get('country') in ['russia', 'belarus', 'kazakhstan', 'armenia',
                                     'azerbaijan', 'kyrgyzstan', 'moldova',
                                     'china', 'japan', 'korea']:
        score *= 1.1

    return round(score, 2), matched_keywords


def get_news_category(entry, feed_info):
    """ИСПРАВЛЕНО: категория берётся из источника/страны, а не по ключевым словам
    в иностранных лентах (иначе статья Autocar про Chery улетала в «Россия»)."""
    # 1. Явная категория источника
    if 'category' in feed_info and feed_info['category'] in CATEGORIES:
        return feed_info['category'], CATEGORIES[feed_info['category']]

    # 2. Маппинг по стране
    country = feed_info.get('country', '')
    if country in ('china', 'japan', 'korea'):
        return country, CATEGORIES[country]
    if country in ('uk', 'usa', 'world'):
        return 'general', {'emoji': '🌍', 'name': 'Мир'}

    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"

    # 3. Тематические категории по ключевым словам (без russia/cis)
    for cat_id, cat_info in CATEGORIES.items():
        if cat_id in ('russia', 'cis'):
            continue
        for keyword in cat_info['keywords']:
            if keyword in text:
                return cat_id, cat_info

    # 4. Россия/СНГ по ключевым словам — только для русскоязычных лент
    if feed_info.get('lang') == 'ru':
        for cat_id in ('russia', 'cis'):
            for keyword in CATEGORIES[cat_id]['keywords']:
                if keyword in text:
                    return cat_id, CATEGORIES[cat_id]

    return 'general', {'emoji': '🚗', 'name': 'Новости'}

# ============================================
# ХРАНИЛИЩЕ ОПУБЛИКОВАННЫХ НОВОСТЕЙ
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


def save_published(news_id, normalized_title=None):
    try:
        with open(PUBLISHED_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{news_id}\n")
        if normalized_title:
            save_published_title(normalized_title)
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
# ХРАНИЛИЩЕ ДЛЯ ДЕДУПЛИКАЦИИ
# ============================================

DUPLICATES_FILE = 'published_titles.txt'
MAX_DUPLICATES_HISTORY = 5000


def load_published_titles():
    if os.path.exists(DUPLICATES_FILE):
        try:
            with open(DUPLICATES_FILE, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            logger.error(f"Ошибка загрузки заголовков: {e}")
            return set()
    return set()


def save_published_title(normalized_title):
    try:
        with open(DUPLICATES_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{normalized_title}\n")
        cleanup_duplicates_file()
    except Exception as e:
        logger.error(f"Ошибка сохранения заголовка: {e}")


def cleanup_duplicates_file():
    try:
        if not os.path.exists(DUPLICATES_FILE):
            return
        with open(DUPLICATES_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) > MAX_DUPLICATES_HISTORY:
            with open(DUPLICATES_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines[-MAX_DUPLICATES_HISTORY:])
    except Exception as e:
        logger.error(f"Ошибка очистки файла дубликатов: {e}")

# ============================================
# СЧЁТЧИКИ (ДНЕВНЫЕ ЛИМИТЫ)
# ============================================

def get_today_str():
    return datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d')


DAILY_POST_COUNT_FILE = 'daily_post_count.txt'


def load_daily_post_count():
    today = get_today_str()
    if os.path.exists(DAILY_POST_COUNT_FILE):
        try:
            with open(DAILY_POST_COUNT_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    parts = content.split(' ')
                    if len(parts) == 2 and parts[0] == today:
                        return int(parts[1])
        except Exception as e:
            logger.error(f"Ошибка загрузки счётчика постов: {e}")
    return 0


def save_daily_post_count(count):
    today = get_today_str()
    try:
        with open(DAILY_POST_COUNT_FILE, 'w', encoding='utf-8') as f:
            f.write(f"{today} {count}")
    except Exception as e:
        logger.error(f"Ошибка сохранения счётчика постов: {e}")


def increment_daily_post_count():
    current = load_daily_post_count()
    new_count = current + 1
    save_daily_post_count(new_count)
    return new_count


def get_daily_post_remaining():
    return max(0, DAILY_POST_LIMIT - load_daily_post_count())


MOTORSPORT_COUNT_FILE = 'motorsport_daily_count.txt'


def load_motorsport_count():
    today = get_today_str()
    if os.path.exists(MOTORSPORT_COUNT_FILE):
        try:
            with open(MOTORSPORT_COUNT_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    parts = content.split(' ')
                    if len(parts) == 2 and parts[0] == today:
                        return int(parts[1])
        except Exception as e:
            logger.error(f"Ошибка загрузки счётчика спорта: {e}")
    return 0


def save_motorsport_count(count):
    today = get_today_str()
    try:
        with open(MOTORSPORT_COUNT_FILE, 'w', encoding='utf-8') as f:
            f.write(f"{today} {count}")
    except Exception as e:
        logger.error(f"Ошибка сохранения счётчика спорта: {e}")


def increment_motorsport_count():
    current = load_motorsport_count()
    new_count = current + 1
    save_motorsport_count(new_count)
    return new_count


def get_motorsport_remaining():
    return max(0, MOTORSPORT_DAILY_LIMIT - load_motorsport_count())

# ============================================
# ПРОВЕРКА ВРЕМЕНИ ПУБЛИКАЦИИ
# ============================================

def is_publishing_time() -> bool:
    now_moscow = datetime.now(MOSCOW_TZ)
    current_hour = now_moscow.hour
    if PUBLISH_END_HOUR == 24:
        return current_hour >= PUBLISH_START_HOUR
    if PUBLISH_END_HOUR < PUBLISH_START_HOUR:
        return current_hour >= PUBLISH_START_HOUR or current_hour < PUBLISH_END_HOUR
    return PUBLISH_START_HOUR <= current_hour < PUBLISH_END_HOUR


def is_peak_hour() -> bool:
    current_hour = datetime.now(MOSCOW_TZ).hour
    for start, end in PEAK_HOURS:
        if start <= current_hour < end:
            return True
    return False


def get_next_publish_time() -> str:
    now_moscow = datetime.now(MOSCOW_TZ)
    if now_moscow.hour < PUBLISH_START_HOUR:
        next_time = now_moscow.replace(hour=PUBLISH_START_HOUR, minute=0, second=0, microsecond=0)
    else:
        next_time = (now_moscow + timedelta(days=1)).replace(hour=PUBLISH_START_HOUR, minute=0, second=0, microsecond=0)
    return next_time.strftime('%d.%m.%Y %H:%M МСК')


def get_next_peak_time() -> str:
    now_moscow = datetime.now(MOSCOW_TZ)
    current_hour = now_moscow.hour
    for start, end in PEAK_HOURS:
        if current_hour < start:
            return now_moscow.replace(hour=start, minute=0, second=0, microsecond=0).strftime('%d.%m.%Y %H:%M МСК')
    next_start = PEAK_HOURS[0][0]
    return (now_moscow + timedelta(days=1)).replace(hour=next_start, minute=0, second=0, microsecond=0).strftime('%d.%m.%Y %H:%M МСК')

# ============================================
# УТИЛИТЫ
# ============================================

def get_news_id(entry):
    unique_str = f"{entry.get('title', '')}{entry.get('link', '')}"
    return hashlib.md5(unique_str.encode('utf-8')).hexdigest()


def translate_text(text, source_lang='en'):
    """Переводит текст. Возвращает "" если перевод недоступен."""
    if not ENABLE_TRANSLATION:
        return text
    if 'translator' not in globals() or translator is None:
        return text
    if not text or len(text.strip()) == 0:
        return ""
    return translator.translate(text, source_lang, 'ru')


def clean_html(text):
    """Убирает теги, HTML-сущности (&#8220; и т.п.) и WordPress-хвосты."""
    if not text:
        return ""
    clean_text = re.sub('<[^<]+?>', '', text)
    clean_text = html.unescape(clean_text)
    clean_text = re.sub(r'The post\s.+?appeared first on\s.+?\.', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'(Keep reading|Read more|Continue reading|continued)\s*$', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'\s+', ' ', clean_text)
    clean_text = clean_text.strip()
    if clean_text.endswith('-') or clean_text.endswith('...'):
        words = clean_text.split()
        if len(words) > 1:
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

# ============================================
# ФИЛЬТР «НЕ АВТО» (отсекает мусор из общих лент СНГ)
# ============================================

AUTO_MANDATORY_KEYWORDS = [
    # бренды
    'toyota', 'honda', 'nissan', 'mazda', 'subaru', 'mitsubishi', 'suzuki', 'lexus',
    'bmw', 'mercedes', 'audi', 'volkswagen', 'vw', 'ford', 'hyundai', 'kia', 'volvo',
    'skoda', 'renault', 'peugeot', 'citroen', 'chevrolet', 'jeep', 'jaguar', 'porsche',
    'ferrari', 'lamborghini', 'tesla', 'byd', 'chery', 'haval', 'geely', 'changan',
    'zeekr', 'nio', 'xpeng', 'li auto', 'lada', 'уаз', 'камаз', 'автоваз', 'москвич',
    'aurus', 'тойота', 'хонда', 'ниссан', 'мазда', 'субару', 'лексус', 'бмв',
    'мерседес', 'ауди', 'фольксваген', 'форд', 'хендай', 'хёндэ', 'киа', 'вольво',
    'шкода', 'рено', 'пежо', 'шевроле', 'джип', 'ягуар', 'порше', 'тесла',
    # общие авто-слова RU
    'авто', 'машин', 'автомобил', 'кроссовер', 'внедорожник', 'седан', 'пикап',
    'двигател', 'мотор', 'коробк', 'вариатор', 'робот', 'подвеск', 'тормоз',
    'пробег', 'кузов', 'салон', 'шин', 'колес', 'бензин', 'дизел', 'топлив',
    'зарядк', 'электрокар', 'электромобил', 'гибрид', 'батаре', 'дтп', 'авари',
    'штраф', 'пдд', 'гибдд', 'гаи', 'гонк', 'формула', 'motogp', 'рестайлинг',
    'премьер', 'дебют', 'комплектаци', 'привод', 'тюнинг', 'дилер', 'автосалон',
    # общие авто-слова EN
    'car', 'auto', 'vehicle', 'ev', 'suv', 'sedan', 'truck', 'pickup', 'engine',
    'motor', 'battery', 'charging', 'mileage', 'recall', 'crash', 'racing', 'formula',
]


def is_auto_related(entry):
    """True, только если новость реально про авто/мото."""
    text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
    return any(kw in text for kw in AUTO_MANDATORY_KEYWORDS)

# ============================================
# ХЕШТЕГИ
# ============================================

def generate_hashtags(entry, feed_info):
    if not ENABLE_HASHTAGS:
        return ""
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    text = f"{title} {summary}"
    tags = ['#автоновости']

    cat_id, cat_info = get_news_category(entry, feed_info)
    if cat_id == 'russia':
        tags.append('#россия')
    elif cat_id == 'cis':
        country_tags = {
            'belarus': '#беларусь', 'kazakhstan': '#казахстан', 'armenia': '#армения',
            'azerbaijan': '#азербайджан', 'kyrgyzstan': '#кыргызстан', 'moldova': '#молдова',
        }
        if feed_info.get('country') in country_tags:
            tags.append(country_tags[feed_info.get('country')])
    elif cat_id in ('china', 'japan', 'korea'):
        tags.append({'china': '#китай', 'japan': '#япония', 'korea': '#корея'}[cat_id])
    elif cat_id != 'general':
        tags.append(f"#{cat_id}")

    brands = {
        'tesla': '#tesla', 'toyota': '#toyota', 'bmw': '#bmw',
        'mercedes': '#mercedes', 'audi': '#audi', 'volkswagen': '#vw',
        'porsche': '#porsche', 'ferrari': '#ferrari', 'lamborghini': '#lamborghini',
        'ford': '#ford', 'honda': '#honda', 'nissan': '#nissan',
        'hyundai': '#hyundai', 'kia': '#kia', 'lada': '#лада',
        'bugatti': '#bugatti', 'mclaren': '#mclaren', 'rolls-royce': '#rollsroyce',
        'chery': '#chery', 'haval': '#haval', 'geely': '#geely',
        'changan': '#changan', 'byd': '#byd', 'zeekr': '#zeekr', 'nio': '#nio'
    }
    for brand, tag in brands.items():
        if brand in text:
            tags.append(tag)
            break

    return ' '.join(tags[:5])

# ============================================
# ФОРМАТИРОВАНИЕ СООБЩЕНИЯ
# ============================================

def format_message(entry, feed_info, score, category):
    """Возвращает (message, title). Если перевод недоступен и англопосты запрещены —
    возвращает (None, None), и новость пропускается."""
    original_title = entry.get('title', 'Без названия')
    link = entry.get('link', '')
    original_summary = entry.get('summary', '')
    source_lang = feed_info.get('lang', 'en')

    if source_lang != 'ru':
        translated_title = translate_text(original_title, source_lang)
        translated_summary = translate_text(original_summary, source_lang)
        if not translated_title:
            if not PUBLISH_UNTRANSLATED:
                return None, None
            translated_title = original_title
            translated_summary = original_summary
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

    # Рейтинг на экране не выше 10.0
    display_score = min(score, 10.0)

    # БЕЗ строки «ГОРЯЧАЯ НОВОСТЬ» — пост начинается сразу с заголовка
    message = f"{cat_emoji} *{translated_title}*\n\n"
    if translated_summary:
        message += f"{translated_summary}\n\n"

    message += "━━━━━━━━━━━━━━━━━━━\n"
    message += f" Рейтинг: {display_score}/10\n"
    message += f"📰 Источник: {source_name} {region}\n"
    message += f"🏷️ Категория: {cat_name}\n"
    message += f"\n🔗 [Читать полностью]({link})\n\n"

    hashtags = generate_hashtags(entry, feed_info)
    if hashtags:
        message += hashtags

    return message, translated_title


def send_news_to_channel(message, image_url=None):
    try:
        if image_url and ENABLE_IMAGES:
            try:
                bot.send_photo(CHANNEL_ID, image_url, caption=message, parse_mode='Markdown')
                return True
            except Exception:
                logger.warning("⚠️ Не удалось отправить изображение")
                bot.send_message(CHANNEL_ID, message, parse_mode='Markdown', disable_web_page_preview=False)
                return True
        else:
            bot.send_message(CHANNEL_ID, message, parse_mode='Markdown', disable_web_page_preview=False)
            return True
    except Exception as e:
        logger.error(f"Ошибка отправки в канал: {e}")
        return False

# ============================================
# ДЕДУПЛИКАЦИЯ
# ============================================

def generate_news_key(entry):
    title = entry.get('title', '').lower().strip()
    link = entry.get('link', '').strip()
    summary = entry.get('summary', '').lower()

    url_key = hashlib.md5(link.encode('utf-8')).hexdigest() if link else ''

    normalized_title = re.sub(r'[^\w\sа-яa-z0-9]', '', title)
    normalized_title = re.sub(r'\s+', ' ', normalized_title).strip()
    title_key = hashlib.md5(normalized_title.encode('utf-8')).hexdigest()

    text = f"{title} {summary}"
    text = re.sub('<[^<]+?>', '', text)

    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'в', 'на', 'с', 'к', 'по', 'под', 'над', 'для', 'от', 'до',
        'и', 'или', 'но', 'а', 'да', 'же', 'ли', 'бы', 'будет', 'будут',
        'был', 'была', 'было', 'были', 'есть', 'быть',
        'новый', 'новая', 'новое', 'новые',
        'фото', 'фотографии', 'обзор', 'представлен', 'представлена'
    }

    words = re.findall(r'\b[a-zA-Zа-яА-Я0-9]{4,}\b', text)
    key_words = [w.lower() for w in words if w.lower() not in stop_words]
    unique_words = list(dict.fromkeys(key_words))[:15]
    words_key = '_'.join(sorted(unique_words))

    brands = ['tesla', 'bmw', 'mercedes', 'audi', 'porsche', 'ferrari',
              'lamborghini', 'bugatti', 'mclaren', 'corvette', 'chevrolet',
              'ford', 'toyota', 'honda', 'nissan', 'mazda', 'lexus',
              'lada', 'уаз', 'камаз', 'автоваз', 'byd', 'nio', 'xpeng',
              'geely', 'chery', 'haval', 'changan', 'exeed', 'zeekr']
    found_brands = [brand for brand in brands if brand in text.lower()]
    brands_key = '_'.join(sorted(found_brands))

    return {
        'url_key': url_key,
        'title_key': title_key,
        'words_key': words_key,
        'brands_key': brands_key,
        'title': title,
        'link': link,
        'normalized_title': normalized_title
    }


def remove_duplicates(news_list, time_window_hours=48):
    if not news_list:
        return news_list

    published_titles = load_published_titles()

    unique_news = []
    seen_urls = set()
    seen_titles = {}
    seen_combinations = {}

    logger.info(f" Начинаю дедупликацию {len(news_list)} новостей...")
    logger.info(f"📚 В базе {len(published_titles)} ранее опубликованных заголовков")

    for news_data in news_list:
        entry = news_data['entry']
        title = entry.get('title', '').strip()
        link = entry.get('link', '').strip()
        news_key = news_data.get('news_key', {})

        is_duplicate = False
        duplicate_reason = None
        matched_with = None

        if news_key.get('normalized_title') and news_key['normalized_title'] in published_titles:
            is_duplicate = True
            duplicate_reason = "ALREADY_PUBLISHED"
            matched_with = "уже опубликовано ранее"

        if not is_duplicate and link and link in seen_urls:
            is_duplicate = True
            duplicate_reason = "URL"
            matched_with = "тот же URL"

        if not is_duplicate and news_key.get('normalized_title'):
            norm_title = news_key['normalized_title']
            if norm_title in seen_titles:
                is_duplicate = True
                duplicate_reason = "TITLE_EXACT"
                matched_with = "тот же заголовок"

        if not is_duplicate and news_key.get('normalized_title'):
            current_words = set(news_key['normalized_title'].split())
            for seen_title in list(seen_titles.keys()):
                seen_words = set(seen_title.split())
                if current_words and seen_words:
                    intersection = len(current_words.intersection(seen_words))
                    union = len(current_words.union(seen_words))
                    similarity = intersection / union if union > 0 else 0
                    if similarity >= 0.7:
                        is_duplicate = True
                        duplicate_reason = f"TITLE_SIMILAR_{int(similarity * 100)}%"
                        matched_with = f"схожесть заголовков {similarity:.0%}"
                        break

        if not is_duplicate and news_key.get('brands_key') and news_key.get('words_key'):
            current_brands = news_key['brands_key']
            current_words = set(news_key['words_key'].split('_'))
            for combo_key in list(seen_combinations.keys()):
                seen_brands, seen_words_str = combo_key
                if current_brands == seen_brands and current_brands:
                    seen_words = set(seen_words_str.split('_'))
                    intersection = len(current_words.intersection(seen_words))
                    union = len(current_words.union(seen_words))
                    similarity = intersection / union if union > 0 else 0
                    if similarity >= 0.6:
                        is_duplicate = True
                        duplicate_reason = f"BRAND_CONTENT_{int(similarity * 100)}%"
                        matched_with = f"тот же бренд + схожесть {similarity:.0%}"
                        break

        if is_duplicate:
            logger.warning(f"⏭️ ДУБЛИКАТ ({duplicate_reason}):")
            logger.warning(f"   Заголовок: {title[:80]}...")
            logger.warning(f"   Причина: {matched_with}")
        else:
            unique_news.append(news_data)
            if link:
                seen_urls.add(link)
            if news_key.get('normalized_title'):
                seen_titles[news_key['normalized_title']] = news_data
            if news_key.get('brands_key') and news_key.get('words_key'):
                seen_combinations[(news_key['brands_key'], news_key['words_key'])] = news_data
            logger.info(f"✅ Уникальная: {title[:60]}...")

    logger.info(f"✨ После дедупликации: {len(unique_news)} новостей (было {len(news_list)}, удалено {len(news_list) - len(unique_news)})")
    return unique_news

# ============================================
# ОСНОВНАЯ ЛОГИКА
# ============================================

def fetch_and_publish():
    published = load_published()
    new_count = 0
    error_count = 0
    skipped_count = 0
    working_sources = 0
    failed_sources = 0

    all_news = []

    if not is_publishing_time():
        logger.info(f"🌙 Ночной режим. Публикация пропущена.")
        logger.info(f"🕐 Следующая публикация: {get_next_publish_time()}")
        return 0, 0

    if not is_peak_hour():
        logger.info(f"⏸️ Неактивный час. Публикация пропущена.")
        logger.info(f"🕐 Следующий пиковый час: {get_next_peak_time()}")
        return 0, 0

    daily_remaining = get_daily_post_remaining()
    motorsport_remaining = get_motorsport_remaining()

    logger.info(f"📊 Осталось слотов на сегодня: {daily_remaining}/{DAILY_POST_LIMIT}")
    logger.info(f" Осталось слотов для спорта: {motorsport_remaining}/{MOTORSPORT_DAILY_LIMIT}")

    if daily_remaining <= 0:
        logger.info(f"⏭️ Дневной лимит постов достигнут ({DAILY_POST_LIMIT}/день). Пропускаем цикл.")
        return 0, 0

    max_per_cycle = MAX_POSTS_PER_CYCLE

    logger.info(f"Начинаем проверку {len(RSS_FEEDS)} источников...")

    for feed_info in RSS_FEEDS:
        try:
            logger.info(f"Проверяем: {feed_info['name']}")

            feed = feedparser.parse(
                feed_info['url'],
                request_headers={'User-Agent': 'AutoImPulseBot/1.0'},
                agent='AutoImPulseBot/1.0'
            )

            if feed.bozo and not feed.entries:
                logger.warning(f"❌ Ошибка RSS {feed_info['name']}: {feed.bozo_exception}")
                failed_sources += 1
                continue

            if not feed.entries:
                logger.warning(f"⚠️ {feed_info['name']}: нет новостей")
                failed_sources += 1
                continue

            working_sources += 1

            source_limit = feed_info.get('max_per_cycle', NEWS_PER_SOURCE)
            logger.info(f"✅ {feed_info['name']}: найдено {len(feed.entries)} новостей (лимит: {source_limit})")

            for entry in feed.entries[:source_limit]:
                try:
                    news_id = get_news_id(entry)

                    # 🚫 Фильтр технических страниц-заглушек (Error 500 и т.п.)
                    title_check = entry.get('title', '').lower()
                    if any(g in title_check for g in GARBAGE_MARKERS):
                        logger.warning(f"⏭️ Пропуск страницы ошибки сервера: {entry.get('title', '')[:60]}")
                        continue

                    if news_id in published:
                        continue

                    score, keywords = calculate_news_score(entry, feed_info)
                    category_id, category_info = get_news_category(entry, feed_info)
                    news_key = generate_news_key(entry)

                    all_news.append({
                        'entry': entry,
                        'feed_info': feed_info,
                        'news_id': news_id,
                        'news_key': news_key,
                        'score': score,
                        'keywords': keywords,
                        'category': category_info
                    })
                except Exception as e:
                    logger.warning(f"️ Ошибка обработки новости: {e}")
                    continue

        except Exception as e:
            logger.error(f"❌ Ошибка {feed_info['name']}: {e}")
            failed_sources += 1
            continue

    all_news.sort(key=lambda x: x['score'], reverse=True)

    logger.info(f"📊 Рабочих источников: {working_sources} | Не рабочих: {failed_sources}")
    logger.info(f"📰 Собрано {len(all_news)} новостей до дедупликации")

    all_news = remove_duplicates(all_news)
    logger.info(f"✨ После дедупликации: {len(all_news)} уникальных новостей")

    # ❗ ВАЖНО: НЕ обрезаем до max_per_cycle здесь.
    # Обрезка происходит ниже, ВНУТРИ цикла балансировки, ПОСЛЕ всех фильтров.

    russian_count = 0
    foreign_count = 0
    cis_count = 0
    motorsport_count = 0
    published_news = []

    for news_data in all_news:
        score = news_data['score']

        # 🚫 Фильтр мусора: новости без авто-контекста не публикуем
        if not is_auto_related(news_data['entry']):
            skipped_count += 1
            logger.info(f"🚫 НЕ АВТО (пропуск): {news_data['entry'].get('title', '')[:50]}")
            continue

        country = news_data['feed_info'].get('country', 'world')
        is_russian = country == 'russia'
        is_cis = country in ['belarus', 'kazakhstan', 'armenia', 'azerbaijan',
                              'kyrgyzstan', 'moldova']
        is_motorsport = news_data['feed_info'].get('category') == 'motorsport'

        if score < MIN_SCORE:
            skipped_count += 1
            continue

        if is_motorsport:
            if motorsport_count >= motorsport_remaining:
                skipped_count += 1
                logger.info(f"⏭️ Пропуск спорта (лимит {MOTORSPORT_DAILY_LIMIT}/сутки): {news_data['entry'].get('title', '')[:50]}")
                continue
            motorsport_count += 1

        if is_russian:
            if foreign_count + cis_count >= russian_count:
                russian_count += 1
            else:
                skipped_count += 1
                continue

        if is_cis:
            cis_count += 1
        else:
            foreign_count += 1

        published_news.append(news_data)

        if len(published_news) >= max_per_cycle:
            break

    logger.info(f" Отобрано {len(published_news)} новостей для публикации в этом цикле (лимит: {max_per_cycle})")
    logger.info(f"🏁 Спортивных новостей: {motorsport_count}")

    posts_published_today = 0

    for news_data in published_news:
        if posts_published_today >= daily_remaining:
            logger.info(f"⏭️ Дневной лимит постов достигнут ({DAILY_POST_LIMIT}/день)")
            break

        try:
            entry = news_data['entry']
            feed_info = news_data['feed_info']
            news_id = news_data['news_id']
            score = news_data['score']
            category = news_data['category']

            message, title = format_message(entry, feed_info, score, category)

            # Перевод недоступен, англопосты запрещены — пропускаем
            if message is None:
                skipped_count += 1
                logger.warning("⏭️ Пропуск: перевод недоступен, публикация на языке оригинала запрещена")
                continue

            image_url = get_image_url(entry)

            if send_news_to_channel(message, image_url):
                news_key = news_data.get('news_key', {})
                save_published(news_id, news_key.get('normalized_title', ''))

                posts_published_today += 1
                new_daily_count = increment_daily_post_count()

                if feed_info.get('category') == 'motorsport':
                    new_motorsport_count = increment_motorsport_count()
                    logger.info(f"🏁 Спортивная новость #{new_motorsport_count}/{MOTORSPORT_DAILY_LIMIT} за сегодня")

                new_count += 1
                country = feed_info.get('country', 'world')
                flag = FLAGS.get(country, '🌍')
                source_name = feed_info.get('name', 'Unknown')
                logger.info(f"✅ [{flag}] {source_name} (рейтинг {score:.2f}): {title[:50]}...")
                logger.info(f"📊 Пост {new_daily_count}/{DAILY_POST_LIMIT} за сегодня")
                time.sleep(3)
            else:
                error_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка публикации: {e}")
            error_count += 1
            continue

    logger.info(f" Итог: ✅{new_count} | 🇷{russian_count} | СНГ{cis_count} | 🌍{foreign_count} | 🏁{motorsport_count} | ⏭️{skipped_count} | ❌{error_count}")
    logger.info(f"📊 Опубликовано в этом цикле: {posts_published_today}")
    logger.info(f"📊 Всего сегодня: {load_daily_post_count()}/{DAILY_POST_LIMIT}")
    return new_count, error_count


def send_startup_message():
    try:
        now_moscow = datetime.now(MOSCOW_TZ).strftime('%H:%M МСК')
        peak_hours_str = ', '.join([f"{start}:00-{end}:00" for start, end in PEAK_HOURS])

        startup_message = (
            "🤖 *Auto imPulse News Bot запущен!*\n\n"
            f"📡 Источников: {len(RSS_FEEDS)} (Россия, Китай, Япония, Корея, СНГ, UK, USA, мир)\n"
            f"⏱️ Интервал: {CHECK_INTERVAL // 60} мин\n"
            f"📈 Мин. рейтинг: {MIN_SCORE}/10\n"
            f"🌐 Перевод: {'✅ цепочка DeepL → Google → MyMemory' if ENABLE_TRANSLATION else '❌'}\n"
            f"🚫 Англопосты при сбое перевода: {'разрешены' if PUBLISH_UNTRANSLATED else 'запрещены'}\n"
            f"🖼️ Изображения: {'✅' if ENABLE_IMAGES else '❌'}\n"
            f"🏷️ Хештеги: {'✅' if ENABLE_HASHTAGS else '❌'}\n"
            f"🏁 Лимит спорта: {MOTORSPORT_DAILY_LIMIT}/сутки\n"
            f"📊 Дневной лимит постов: {DAILY_POST_LIMIT}/сутки\n"
            f"📝 Постов за цикл: {MAX_POSTS_PER_CYCLE}\n"
            f"⏰ Пиковые часы: {peak_hours_str}\n"
            f"🌙 Публикация: {PUBLISH_START_HOUR}:00 - {PUBLISH_END_HOUR}:00 МСК\n"
            f" Текущее время: {now_moscow}\n\n"
            f"🕐 {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S МСК')}"
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
        logger.error("❌ Бот не админ!")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка канала: {e}")
        return False


def main():
    logger.info("=" * 60)
    logger.info("Auto imPulse News Bot запускается...")
    logger.info("=" * 60)

    if not check_channel_access():
        logger.error(" Нет доступа к каналу!")
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
