#!/usr/bin/env bash
# Extract structured data from a document URL.
#
# Usage:
#   bash examples/curl/extract_url.sh

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

curl -s -X POST "${API_BASE}/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "document_url": "https://example.com/contract.pdf",
    "provider": "gpt-4o",
    "passes": 2,
    "extraction_config": {
      "temperature": 0.3
    }
  }' | python -m json.tool
