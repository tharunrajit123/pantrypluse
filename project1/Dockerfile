FROM python:3.11-slim

# Install system dependencies (Tesseract OCR + build tools for mysql-connector)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/uploads

ENV PORT=10000
EXPOSE 10000

# eventlet worker needed for flask-socketio to work correctly under gunicorn
CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:${PORT} app:app
