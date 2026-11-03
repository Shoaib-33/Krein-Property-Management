#!/bin/sh
# Container start. With LITESTREAM_REPLICA_URL set (Cloud Run) the database is restored from Cloud Storage first and
# every change is streamed back; without it (Hugging Face, Render) the app starts exactly as before.
set -ef   # -f: no filename globbing, so the * in APP stays literal
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips=*"

# Cloud Run (it sets K_SERVICE) wipes the disk on every restart. Without Litestream each deploy would quietly start from an
# empty database, so refuse to start instead: Cloud Run then keeps the previous version running. A demo that may start
# empty every time sets KREIN_ALLOW_EMPTY_DISK=1.
if [ -n "$K_SERVICE" ] && [ -z "$LITESTREAM_REPLICA_URL" ] && [ "$KREIN_ALLOW_EMPTY_DISK" != "1" ]; then
    echo "Refusing to start: on Cloud Run the disk is wiped on every restart, and LITESTREAM_REPLICA_URL is not set," >&2
    echo "so every deploy would start with an empty database. Set LITESTREAM_REPLICA_URL (for example gcs://<bucket>/db)," >&2
    echo "or KREIN_ALLOW_EMPTY_DISK=1 for a demo that may lose its data." >&2
    exit 1
fi

# Uploaded files (contracts, receipts, photos) are not in the database, so Litestream does not keep them. With
# KREIN_UPLOADS_DIR set to a lasting folder (a Cloud Storage volume) they are stored there instead.
if [ -n "$KREIN_UPLOADS_DIR" ]; then
    mkdir -p "$KREIN_DATA_DIR" "$KREIN_UPLOADS_DIR"
    if [ ! -L "$KREIN_DATA_DIR/uploads" ]; then
        if [ -d "$KREIN_DATA_DIR/uploads" ]; then
            cp -a "$KREIN_DATA_DIR/uploads/." "$KREIN_UPLOADS_DIR/"
            rm -rf "$KREIN_DATA_DIR/uploads"
        fi
        ln -s "$KREIN_UPLOADS_DIR" "$KREIN_DATA_DIR/uploads"
    fi
fi

if [ -n "$LITESTREAM_REPLICA_URL" ]; then
    litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$KREIN_DATA_DIR/krein.db"
    python -m scripts.hf_start
    exec litestream replicate -config /etc/litestream.yml -exec "$APP"
fi
python -m scripts.hf_start
exec $APP
