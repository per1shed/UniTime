# Расписание РязГМУ

Telegram-бот расписания занятий [РязГМУ](https://www.rzgmu.ru).

## Возможности

- Выбор курса → факультета → группы через inline-кнопки
- Просмотр расписания на неделю с переключением недель
- Автоматические уведомления:
  - утром — расписание на текущий день
  - вечером — расписание на завтра
  - за 20 минут до каждой пары
  - в начале перерыва между парами
- Парсинг расписания с сайта РязГМУ (HTML + PDF)
- PostgreSQL для хранения пользователей и кэша расписаний

## Быстрый старт (Docker)

1. Убедитесь, что в `.env` указан `BOT_TOKEN` (уже создан).
2. При необходимости добавьте в `.env`:

```env
POSTGRES_DB=unitime
POSTGRES_USER=unitime
POSTGRES_PASSWORD=unitime_secret
TZ=Europe/Moscow
MORNING_HOUR=7
MORNING_MINUTE=0
LESSON_REMINDER_MINUTES=20
SCHEDULE_SYNC_HOURS=12
```

3. Запуск:

```bash
docker compose up -d --build
```

4. Логи:

```bash
docker compose logs -f bot
```

## Локальный запуск (без Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# PostgreSQL должен быть доступен, задайте DATABASE_URL в .env
export DATABASE_URL=postgresql+asyncpg://unitime:unitime_secret@localhost:5432/unitime
python -m bot.main
```

## Структура

```
bot/           — Telegram-бот (aiogram 3)
parsers/       — парсеры сайта РязГМУ
docker-compose.yml
Dockerfile
```

## Примечания

- Группы в расписании РязГМУ — это номера столбцов в PDF (1, 2, 3, …).
- Расписание синхронизируется с сайта каждые 12 часов и при первом запуске.
- Часовой пояс по умолчанию: `Europe/Moscow`.
