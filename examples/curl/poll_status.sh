#!/usr/bin/env bash
# Poll a task until it completes.
#
# Usage:
#   bash examples/curl/poll_status.sh <task-id>

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"
TASK_ID="${1:?Usage: poll_status.sh <task-id>}"

while true; do
  resp=$(curl -s "${API_BASE}/tasks/${TASK_ID}")
  state=$(echo "$resp" | python -c "import sys,json; print(json.load(sys.stdin)['state'])")

  echo "[$(date +%H:%M:%S)] State: ${state}"

  case "$state" in
    SUCCESS|FAILURE)
      echo "$resp" | python -m json.tool
      break
      ;;
  esac

  sleep 2
done
