#!/bin/sh
# Container start. With LITESTREAM_REPLICA_URL set (Cloud Run) the database is restored from Cloud Storage first and
# every change is streamed back; without it (Hugging Face, Render) the app starts exactly as before.
set -ef   # -f: no filename globbing, so the * in APP stays literal
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips=*"
if [ -n "$LITESTREAM_REPLICA_URL" ]; then
    litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$KREIN_DATA_DIR/krein.db"
    python -m scripts.hf_start
    exec litestream replicate -config /etc/litestream.yml -exec "$APP"
fi
python -m scripts.hf_start
exec $APP
