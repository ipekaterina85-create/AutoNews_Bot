# 🚗 Auto imPulse News Bot

Автоматический бот для публикации автомобильных новостей из зарубежных источников в Telegram-канал.

## 🚀 Возможности

- ✅ Автоматический парсинг RSS из 11+ зарубежных источников
- ✅ Автоматический перевод на русский язык
- ✅ Публикация с изображениями
- ✅ Генерация хештегов
- ✅ Защита от дубликатов
- ✅ Подробное логирование
- ✅ Готово к деплою на Railway

## 📋 Источники новостей

### 🇬🇧 Великобритания
- Autocar UK
- Auto Express

### 🇺🇸 США
- Car and Driver
- Motor Trend
- Road & Track
- The Drive

### 🇩🇪 Германия
- Auto Motor und Sport

### 🌍 Международные
- InsideEVs (электромобили)
- Electrek (электромобили)
- Autosport (автоспорт)
- Motorsport.com (автоспорт)

## 🛠️ Установка и настройка

### 1. Создайте бота в Telegram

1. Откройте [@BotFather](https://t.me/BotFather)
2. Отправьте `/newbot`
3. Задайте имя и username
4. Скопируйте **BOT_TOKEN**

### 2. Создайте канал в Telegram

1. Создайте новый канал
2. Задайте название (например, "Auto imPulse News")
3. Задайте username (например, `@autoimpulse`)
4. Добавьте бота как администратора с правом публикации

### 3. Деплой на Railway

#### Вариант A: Через веб-интерфейс

1. Зарегистрируйтесь на [railway.com](https://railway.com)
2. Нажмите "New Project" → "Deploy from GitHub repo"
3. Загрузите этот код в GitHub репозиторий
4. Выберите репозиторий
5. Railway автоматически определит Python проект

#### Вариант B: Через Railway CLI

```bash
# Установите CLI
npm i -g @railway/cli

# Авторизуйтесь
railway login

# Создайте проект
railway init

# Установите переменные окружения
railway variables set BOT_TOKEN="ваш_токен"
railway variables set CHANNEL_ID="@ваш_канал"
railway variables set CHECK_INTERVAL="1800"
railway variables set ENABLE_TRANSLATION="true"
railway variables set ENABLE_IMAGES="true"
railway variables set ENABLE_HASHTAGS="true"

# Деплой
railway up
