FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends apcupsd \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin upsdash

WORKDIR /app
COPY app.py /app/app.py
RUN pip install --no-cache-dir Flask==3.1.2 gunicorn==23.0.0

USER 10001:10001

CMD ["gunicorn", "--bind", "127.0.0.1:8011", "--workers", "1", "--threads", "4", "--timeout", "30", "app:app"]
