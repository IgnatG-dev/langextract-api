# Recipes

Common patterns and usage examples for the LangExtract API.

## Single Document Extraction

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "document_url": "https://storage.example.com/contracts/agreement-2025.txt",
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
      {"document_url": "https://storage.example.com/invoices/inv-001.txt"},
      {"document_url": "https://storage.example.com/invoices/inv-002.txt"},
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

## Multi-Pass with Confidence Scoring

Run multiple extraction passes to get a `confidence_score` (0.0–1.0) on every entity.  Higher values mean the entity was found consistently across passes.
Early stopping kicks in automatically when consecutive passes yield identical results, so extra passes cost nothing when the model is already stable.

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC dated Jan 1 2025 for $50,000.",
    "passes": 3,
    "provider": "gpt-4o"
  }'
```

Entities in the response will include:

```json
{
  "extraction_class": "party",
  "extraction_text": "Acme Corp",
  "attributes": {},
  "char_start": 18,
  "char_end": 27,
  "confidence_score": 1.0
}
```

> **Tip:** A `confidence_score` of `0.33` (1 out of 3 passes) may indicate a hallucinated entity.  Use this field to filter low-confidence results client-side.

## Consensus Mode (Cross-Provider Agreement)

Consensus mode sends the same extraction to **multiple LLM providers** and keeps
only the entities they agree on.  This drastically reduces hallucinations and
improves determinism compared to single-provider extraction.

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC dated Jan 1 2025 for $50,000.",
    "extraction_config": {
      "consensus_providers": ["gpt-4o", "gemini-2.5-pro"],
      "consensus_threshold": 0.7
    }
  }'
```

The response `metadata.provider` will read `"consensus(gpt-4o, gemini-2.5-pro)"`.

### Combining Consensus with Multi-Pass

For maximum accuracy, combine both features — each consensus provider runs multiple passes, entities get cross-pass confidence scores, **and** only provider-agreed entities are returned:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "...",
    "passes": 2,
    "extraction_config": {
      "consensus_providers": ["gpt-4o", "gemini-2.5-pro"],
      "consensus_threshold": 0.6,
      "temperature": 0.3
    }
  }'
```
