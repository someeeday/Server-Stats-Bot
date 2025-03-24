FROM python:3.10-slim

WORKDIR /app

# Установка необходимых пакетов и русской локали
RUN apt-get update && apt-get install -y \
    fontconfig \
    fonts-dejavu \
    locales \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Генерация и установка русской локали
RUN sed -i -e 's/# ru_RU.UTF-8 UTF-8/ru_RU.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen && \
    update-locale LANG=ru_RU.UTF-8

# Настройка переменных окружения для локали
ENV LANG=ru_RU.UTF-8 \
    LANGUAGE=ru_RU:ru \
    LC_ALL=ru_RU.UTF-8

# Создаем директорию для шрифтов
RUN mkdir -p /app/fonts

# Копируем файлы проекта
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Копируем шрифты
COPY fonts/ /app/fonts/

# Устанавливаем шрифты в систему
RUN mkdir -p /usr/local/share/fonts/custom && \
    cp -r /app/fonts/*.ttf /usr/local/share/fonts/custom/ || true && \
    fc-cache -fv

# Выводим список доступных шрифтов для отладки
RUN fc-list | grep -i arial || true

# Создание папок и установка прав
RUN mkdir -p /app-pdfs && chmod -R 777 /app-pdfs
RUN mkdir -p /app/logs && chmod -R 777 /app/logs

# Копирование исходного кода
COPY . /app/

# Команда запуска
CMD ["python", "main.py"]
