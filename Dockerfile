FROM python:3.10-slim

# Определение ОС через переменную (по умолчанию Linux)
ARG TARGETPLATFORM="linux/amd64"

# Установка зависимостей и шрифтов для всех платформ
WORKDIR /app
RUN apt-get update && apt-get install -y \
    fontconfig \
    fonts-dejavu \
    procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Установка PowerShell только для Windows
RUN if [ "$TARGETPLATFORM" = "windows/amd64" ]; then \
        curl https://packages.microsoft.com/config/debian/11/packages-microsoft-prod.deb -o packages-microsoft-prod.deb \
        && dpkg -i packages-microsoft-prod.deb \
        && apt-get update \
        && apt-get install -y powershell \
        && rm packages-microsoft-prod.deb; \
    fi

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Создание папок и установка прав
RUN mkdir -p /app-pdfs && chmod -R 777 /app-pdfs
RUN mkdir -p /app/logs && chmod -R 777 /app/logs

# Копирование исходного кода
COPY . /app/

# Команда запуска
CMD ["python", "main.py"]
