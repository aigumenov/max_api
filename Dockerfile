# Базовый образ Python
FROM python:3.12

# Устанавливаем пакет для работы с сертификатами (если его нет)
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Копируем сертификаты из папки ca-certificates в контейнер
# Убедитесь, что папка ca-certificates находится в корне вашего репозитория (рядом с Dockerfile)
COPY ca-certificates/*.crt /usr/local/share/ca-certificates/

# Обновляем хранилище доверенных сертификатов в системе контейнера
RUN update-ca-certificates

# Указываем Python-библиотекам, где искать сертификаты
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Рабочая директория внутри контейнера
WORKDIR /app

# Копируем зависимости отдельно (для кэширования слоёв)
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта
COPY . .

# Открываем порт (замените на свой, если не 8000)
EXPOSE 8000

# Команда запуска (замените на свою точку входа!)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
