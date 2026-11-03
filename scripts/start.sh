#!/bin/sh
# Container start. With DATABASE_URL set (Cloud Run with Cloud SQL) the database is PostgreSQL and nothing is kept on the
# container's disk. Otherwise the database is a SQLite file: with LITESTREAM_REPLICA_URL set it is restored from Cloud
# Storage first and every change is streamed back; without it (Hugging Face, Render) the app starts exactly as before.
set -ef   # -f: no filename globbing, so the * in APP stays literal
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips=*"

# Cloud Run (it sets K_SERVICE) wipes the disk on every restart. A SQLite file without Litestream would quietly start
# empty on each deploy, so refuse to start instead: Cloud Run then keeps the previous version running. A demo that may
# start empty every time sets KREIN_ALLOW_EMPTY_DISK=1.
if [ -n "$K_SERVICE" ] && [ -z "$DATABASE_URL" ] && [ -z "$LITESTREAM_REPLICA_URL" ] && [ "$KREIN_ALLOW_EMPTY_DISK" != "1" ]; then
    echo "Refusing to start: on Cloud Run the disk is wiped on every restart, and neither DATABASE_URL (Cloud SQL)" >&2
    echo "nor LITESTREAM_REPLICA_URL is set, so every deploy would start with an empty database." >&2
    echo "Set DATABASE_URL, or KREIN_ALLOW_EMPTY_DISK=1 for a demo that may lose its data." >&2
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

if [ -n "$LITESTREAM_REPLICA_URL" ] && [ -z "$DATABASE_URL" ]; then
    litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$KREIN_DATA_DIR/krein.db"
    python -m scripts.hf_start
    exec litestream replicate -config /etc/litestream.yml -exec "$APP"
fi
python -m scripts.hf_start
exec $APP
