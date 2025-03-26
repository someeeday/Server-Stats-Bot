# Этап сборки
FROM python:3.10-slim as builder

WORKDIR /app

# Установка необходимых пакетов для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Копирование и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Основной этап
FROM python:3.10-slim

WORKDIR /app

# Установка минимально необходимых пакетов и русской локали
RUN apt-get update && apt-get install -y --no-install-recommends \
    fontconfig \
    fonts-dejavu \
    locales \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i -e 's/# ru_RU.UTF-8 UTF-8/ru_RU.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && update-locale LANG=ru_RU.UTF-8

# Настройка переменных окружения
ENV LANG=ru_RU.UTF-8 \
    LANGUAGE=ru_RU:ru \
    LC_ALL=ru_RU.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Копирование установленных пакетов из этапа сборки
COPY --from=builder /root/.local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages

# Создание и настройка директорий
RUN mkdir -p /app/fonts /app-pdfs /app/logs \
    && chmod -R 777 /app-pdfs /app/logs

# Копирование шрифтов и обновление кэша
COPY fonts/ /app/fonts/
RUN mkdir -p /usr/local/share/fonts/custom \
    && cp -r /app/fonts/*.ttf /usr/local/share/fonts/custom/ \
    && fc-cache -fv

# Копирование исходного кода
COPY . .

# Проверка наличия необходимых файлов
RUN python -c "import matplotlib; import reportlab"

# Пользователь с ограниченными правами
RUN useradd -m -r -s /bin/bash botuser \
    && chown -R botuser:botuser /app /app-pdfs /app/logs
USER botuser

CMD ["python", "main.py"]
