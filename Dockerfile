# Backend image: the API (gunicorn), migrations, the Celery worker and beat
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput \
    && useradd --uid 1000 --create-home cloudhop \
    && mkdir -p credentials \
    && chown cloudhop credentials

USER cloudhop

EXPOSE 8000
# Syncs answer only once the provider has, so allow long requests
CMD ["gunicorn", "cloudhop.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "300"]
