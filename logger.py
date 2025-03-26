import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

LOGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Создаем папку для логов если её нет
os.makedirs(LOGS_PATH, exist_ok=True)

LOG_FILE = os.path.join(LOGS_PATH, "debug.log")

# Настройка форматирования
formatter = logging.Formatter(
    fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Настройка ротации логов: каждый день в полночь
handler = TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",
    interval=1,
    backupCount=7,  # Хранить логи за последнюю неделю
    encoding='utf-8'
)
handler.setFormatter(formatter)
handler.suffix = "%Y%m%d"  # Формат суффикса для файлов логов

# Настройка логгера
logger = logging.getLogger("server-stats-bot")
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Добавляем обработчик для вывода в консоль при разработке
if os.getenv("DEBUG"):
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)