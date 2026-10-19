FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HAVEN_DATA_DIR=/data \
    HAVEN_SECURE_COOKIES=1     HAVEN_TRUSTED_PROXY_HOPS=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY manage.py ./manage.py
COPY scripts ./scripts
# the sample data for a public demo (HAVEN_SAMPLE_DATA=1); no real personal data
COPY sample-data ./sample-data

RUN mkdir -p /data
EXPOSE 7860

# Hugging Face uses 7860; Render and most hosts pass the port in $PORT.
# --proxy-headers only makes the app see https (for secure cookies and the same-origin check); the visitor's IP for the
# login limit is read by app/security.client_ip with HAVEN_TRUSTED_PROXY_HOPS. Set it to 0 if no proxy is in front.
CMD ["sh", "-c", "python -m scripts.hf_start && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
