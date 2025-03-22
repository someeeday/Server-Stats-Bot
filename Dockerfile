FROM python:3.10-slim

WORKDIR /app

# Установка необходимых пакетов и русской локали
RUN apt-get update && apt-get install -y \
    fontconfig \
    fonts-dejavu \
    procps \
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

# Копируем шрифт для использования в matplotlib и reportlab
RUN mkdir -p /app/fonts
COPY fonts/ /app/fonts/

# Устанавливаем шрифты в систему
RUN mkdir -p /usr/local/share/fonts && \
    cp -r /app/fonts/*.ttf /usr/local/share/fonts/ || true && \
    fc-cache -fv

# Настройка matplotlib для работы с кириллицей
RUN mkdir -p /root/.config/matplotlib && \
    echo "backend : Agg" > /root/.config/matplotlib/matplotlibrc && \
    echo "font.family : sans-serif" >> /root/.config/matplotlib/matplotlibrc && \
    echo "font.sans-serif : DejaVu Sans, Arial, Verdana" >> /root/.config/matplotlib/matplotlibrc

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Создание папок и установка прав
RUN mkdir -p /app-pdfs && chmod -R 777 /app-pdfs
RUN mkdir -p /app/logs && chmod -R 777 /app/logs

# Копирование исходного кода
COPY . /app/

# Команда запуска
CMD ["python", "main.py"]
