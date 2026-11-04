#!/bin/sh
# The company website as its own service (app/website_service.py): only the pages and the registration form, which it
# passes on to the software. No database and no software code. Used by the second Cloud Run service (cloudbuild.yaml).
exec uvicorn app.website_service:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips='*'
