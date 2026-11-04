FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KREIN_DATA_DIR=/data \
    KREIN_SECURE_COOKIES=1     KREIN_TRUSTED_PROXY_HOPS=1

WORKDIR /app

# Litestream: keeps the SQLite database in Cloud Storage on hosts without a lasting disk (only used when
# LITESTREAM_REPLICA_URL is set, see scripts/start.sh)
ADD https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz /tmp/litestream.tar.gz
RUN tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream && rm /tmp/litestream.tar.gz
COPY deploy/litestream.yml /etc/litestream.yml

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY website ./website
COPY manage.py ./manage.py
COPY scripts ./scripts
# the sample data for a public demo (KREIN_SAMPLE_DATA=1); no real personal data
COPY sample-data ./sample-data

RUN mkdir -p /data
EXPOSE 7860

# Hugging Face uses 7860; Render and most hosts pass the port in $PORT.
# --proxy-headers only makes the app see https (for secure cookies and the same-origin check); the visitor's IP for the
# login limit is read by app/security.client_ip with KREIN_TRUSTED_PROXY_HOPS. Set it to 0 if no proxy is in front.
CMD ["sh", "scripts/start.sh"]
