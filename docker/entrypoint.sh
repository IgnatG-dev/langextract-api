#!/usr/bin/env bash
# ------------------------------------------------------------------
# Unified entrypoint for the LangCore API container.
#
# The container role is selected via the APP_ROLE environment variable:
#   web     — starts the FastAPI application via Uvicorn
#   worker  — starts a Celery worker
#   flower  — starts the Flower monitoring dashboard
#   beat    — starts the Celery beat scheduler
#
# Defaults to "web" if APP_ROLE is not set.
# ------------------------------------------------------------------

set -euo pipefail

APP_ROLE="${APP_ROLE:-web}"
LOG_LEVEL="${LOG_LEVEL:-info}"

echo "Starting LangCore container in '${APP_ROLE}' mode …"

case "${APP_ROLE}" in

  web)
    exec uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers "${WEB_WORKERS:-4}" \
      --log-level "${LOG_LEVEL}" \
      --proxy-headers \
      --forwarded-allow-ips "*"
    ;;

  worker)
    exec celery -A app.workers.celery_app worker \
      --loglevel="${LOG_LEVEL}" \
      --concurrency="${WORKER_CONCURRENCY:-2}" \
      --max-tasks-per-child="${MAX_TASKS_PER_CHILD:-100}"
    ;;

  flower)
    # Brief pause lets the worker finish its mingle phase so
    # flower's first inspect round succeeds cleanly.
    exec celery -A app.workers.celery_app \
      --broker="${CELERY_BROKER_URL:-redis://redis:6379/0}" \
      flower \
      --port=5555 \
      --loglevel="${LOG_LEVEL}"
    ;;

  beat)
    exec celery -A app.workers.celery_app beat \
      --loglevel="${LOG_LEVEL}"
    ;;

  *)
    echo "ERROR: Unknown APP_ROLE '${APP_ROLE}'."
    echo "       Valid roles: web | worker | flower | beat"
    exit 1
    ;;

esac
