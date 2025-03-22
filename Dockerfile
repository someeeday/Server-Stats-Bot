FROM python:3.10-slim

# Установка зависимостей
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . /app/

# Команда запуска
CMD ["python", "main.py"]
