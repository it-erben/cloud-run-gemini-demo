FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/it-erben/cloud-run-gemini-demo"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home app

COPY main.py cloud_records.py ./
COPY templates/ templates/
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 90 main:app"]
