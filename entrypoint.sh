#!/bin/sh
set -e

echo "[ENTRYPOINT] Ejecutando migraciones de Alembic..."
alembic upgrade head

echo "[ENTRYPOINT] Iniciando Uvicorn en 0.0.0.0:${PORT:-8005}..."
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8005}"

