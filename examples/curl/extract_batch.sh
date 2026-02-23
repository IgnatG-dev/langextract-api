#!/usr/bin/env bash
# Submit a batch of documents and poll the returned task IDs.
#
# Usage:
#   bash examples/curl/extract_batch.sh

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

echo "==> Submitting batch..."
resp=$(curl -s -X POST "${API_BASE}/extract/batch" \
  -H "Content-Type: application/json" \
  -d '{
    "batch_id": "demo-batch-001",
    "documents": [
      {
        "raw_text": "CONTRACT 1 - Acme Corp agrees to supply 500 units to Beta LLC at $25 each. Total: $12,500. Effective date: March 1, 2025.",
        "provider": "gpt-4o"
      },
      {
        "raw_text": "CONTRACT 2 - Charlie Enterprises leases warehouse space from Delta Holdings at $3,200/month for 24 months.",
        "provider": "gpt-4o"
      },
      {
        "raw_text": "CONTRACT 3 - Echo Inc purchases software licences from Foxtrot SaaS Ltd for $9,000/year. Auto-renews annually.",
        "provider": "gpt-4o"
      }
    ]
  }')

echo "$resp" | python -m json.tool

# Extract the batch task ID and poll it
BATCH_TASK_ID=$(echo "$resp" | python -c "import sys,json; print(json.load(sys.stdin).get('batch_task_id',''))" 2>/dev/null)

if [[ -z "$BATCH_TASK_ID" ]]; then
  echo "No batch_task_id in response — poll individual task IDs instead."
  exit 0
fi

echo ""
echo "==> Polling batch task ${BATCH_TASK_ID} ..."
bash "$(dirname "$0")/poll_status.sh" "$BATCH_TASK_ID"
