# Recipes

Common patterns and usage examples for the LangExtract API.

## Single Document Extraction

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "document_url": "https://example.com/contract.pdf",
    "provider": "gpt-4o",
    "passes": 2
  }'
```

## Raw Text Extraction

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC dated Jan 1 2025 for $50,000.",
    "extraction_config": {
      "temperature": 0.3
    }
  }'
```

## Batch Extraction with Webhook

```bash
curl -X POST http://localhost:8000/api/v1/extract/batch \
  -H "Content-Type: application/json" \
  -d '{
    "batch_id": "invoices-q1",
    "callback_url": "https://yourapp.example.com/webhooks/batch",
    "documents": [
      {"document_url": "https://example.com/inv-001.pdf"},
      {"document_url": "https://example.com/inv-002.pdf"},
      {"raw_text": "Invoice #003 ..."}
    ]
  }'
```

## Poll Task Status

```bash
curl http://localhost:8000/api/v1/tasks/<task-id>
```

## Revoke a Task

```bash
curl -X DELETE http://localhost:8000/api/v1/tasks/<task-id>
```

## Idempotent Submission

Include an `idempotency_key` to prevent duplicate processing:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "Some text",
    "idempotency_key": "upload-abc-123"
  }'
```

Sending the same key again returns the original task ID.

## Custom Prompt and Examples

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "...",
    "extraction_config": {
      "prompt_description": "Extract all parties and dates from this legal agreement.",
      "examples": [
        {
          "text": "Agreement between X and Y dated Jan 1",
          "extractions": [
            {"extraction_class": "party", "extraction_text": "X"},
            {"extraction_class": "party", "extraction_text": "Y"},
            {"extraction_class": "date", "extraction_text": "Jan 1"}
          ]
        }
      ],
      "temperature": 0.2,
      "max_workers": 5
    }
  }'
```
