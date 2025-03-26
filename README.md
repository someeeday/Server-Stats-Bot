# Server Stats Bot

Telegram бот для мониторинга серверов через SSH с оптимизированным потреблением ресурсов.

## Требования

- Python 3.10+
- Docker (опционально)
- SSH-доступ к серверам

## Установка

### С использованием Docker (рекомендуется)

```bash
# Склонировать репозиторий
git clone https://github.com/your-username/Server-Stats-Bot.git
cd Server-Stats-Bot

# Создать файл .env с токеном бота
echo "BOT_TOKEN=your_bot_token" > .env

# Запустить через Docker Compose
docker-compose up -d --build
```

### Без Docker

```bash
# Склонировать репозиторий
git clone https://github.com/your-username/Server-Stats-Bot.git
cd Server-Stats-Bot

# Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # Linux
venv\Scripts\activate     # Windows

# Установить зависимости
pip install -r requirements.txt

# Создать файл .env с токеном бота
echo "BOT_TOKEN=your_bot_token" > .env

# Запустить бота
python main.py
```

## Лицензия

MIT License - см. файл [LICENSE](LICENSE)

