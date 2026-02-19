#!/usr/bin/env bash
# Extract structured data from inline text.
#
# Usage:
#   bash examples/curl/extract_text.sh

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

curl -s -X POST "${API_BASE}/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC dated January 1, 2025 for the sum of $50,000.",
    "provider": "gpt-4o"
  }' | python -m json.tool
