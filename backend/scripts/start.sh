#!/bin/sh
set -eu

if ! python -c 'from app.config import get_settings; get_settings()'; then
  echo "ERROR: invalid API configuration; check DATABASE_URL and HANDOFF_SIGNING_KEY" >&2
  exit 64
fi

attempt=0
until python -c 'from app.database import engine; from sqlalchemy import text; c=engine.connect(); c.execute(text("SELECT 1")); c.close()' 2>/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "ERROR: database unavailable after 30 attempts; check DATABASE_URL and db logs" >&2
    exit 70
  fi
  echo "Database is not ready (attempt $attempt/30); retrying..." >&2
  sleep 2
done

if ! alembic upgrade head; then
  echo "ERROR: migration failed; inspect API logs, repair the migration, then restart the api service" >&2
  exit 71
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
