# Recipes

Common patterns and usage examples for the LangCore API.

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

> **Cache interaction:** The first pass may be served from the LiteLLM Redis
> cache (fast, zero cost). Passes ≥ 2 **always bypass** the LLM response cache
> so that each subsequent pass produces a genuinely independent extraction. This
> is handled automatically by the `langcore-litellm` provider.

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

## Structured Output (response_format)

Force the LLM to return valid JSON matching a schema derived from your examples.
This eliminates malformed-JSON parse failures and improves extraction consistency.

By default (`structured_output: null`), the API auto-detects whether the
provider supports `response_format` and enables it when available.  You can
force it on or off explicitly:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC dated Jan 1 2025 for $50,000.",
    "extraction_config": {
      "structured_output": true,
      "examples": [
        {
          "text": "Contract between X Corp and Y Inc dated Dec 15 2024",
          "extractions": [
            {"extraction_class": "party", "extraction_text": "X Corp"},
            {"extraction_class": "party", "extraction_text": "Y Inc"},
            {"extraction_class": "date", "extraction_text": "Dec 15 2024"}
          ]
        }
      ]
    }
  }'
```

> **Notes:**
>
> - Requires examples in `extraction_config` — the JSON Schema is built from
>   them automatically.
> - `fence_output` is forced to `false` when `response_format` is active (the
>   LLM returns raw JSON, not fenced code blocks).
> - Works with any LiteLLM-supported provider that supports
>   `response_format` (OpenAI, Azure, Gemini, Anthropic, etc.).

## Guardrails: JSON Schema Validation

Validate LLM output against a strict JSON Schema.  Invalid output triggers
automatic retry with a corrective prompt:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC dated Jan 1 2025 for $50,000.",
    "extraction_config": {
      "guardrails": {
        "json_schema": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "extraction_class": {"type": "string", "enum": ["party", "date", "amount"]},
              "extraction_text": {"type": "string"},
              "attributes": {"type": "object"}
            },
            "required": ["extraction_class", "extraction_text"]
          }
        },
        "max_retries": 5
      }
    }
  }'
```

## Guardrails: Confidence Threshold Filtering

Automatically reject extractions below a confidence score.  Pair with
multi-pass extraction for best results:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "...",
    "passes": 3,
    "extraction_config": {
      "guardrails": {
        "confidence_threshold": 0.7,
        "confidence_score_key": "confidence_score",
        "on_fail": "filter"
      }
    }
  }'
```

Entities with `confidence_score < 0.7` will be silently removed from
the response.

## Guardrails: Multiple Validators

Combine several validators in one request.  They run in sequence — all
must pass for the output to be accepted:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "...",
    "passes": 3,
    "extraction_config": {
      "guardrails": {
        "json_schema": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "extraction_class": {"type": "string"},
              "extraction_text": {"type": "string"}
            },
            "required": ["extraction_class", "extraction_text"]
          }
        },
        "required_fields": ["extraction_class", "extraction_text"],
        "confidence_threshold": 0.6,
        "on_fail": "reask",
        "max_retries": 3
      }
    }
  }'
```

## DSPy: Optimize Prompts, Then Extract

Use DSPy to improve your extraction prompt, then feed the optimized
config back into the extraction endpoint:

```bash
# Step 1: Optimize
curl -s -X POST http://localhost:8000/api/v1/dspy/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "prompt_description": "Extract all parties and monetary amounts from legal agreements.",
    "examples": [
      {
        "text": "Agreement between X Corp and Y Inc for $100,000.",
        "extractions": [
          {"extraction_class": "party", "extraction_text": "X Corp"},
          {"extraction_class": "party", "extraction_text": "Y Inc"},
          {"extraction_class": "amount", "extraction_text": "$100,000"}
        ]
      }
    ],
    "train_texts": [
      "Contract between Alpha LLC and Beta Corp for $50,000.",
      "MOU between Gamma Inc and Delta Partners for $200,000."
    ],
    "expected_results": [
      [
        {"extraction_class": "party", "extraction_text": "Alpha LLC"},
        {"extraction_class": "party", "extraction_text": "Beta Corp"},
        {"extraction_class": "amount", "extraction_text": "$50,000"}
      ],
      [
        {"extraction_class": "party", "extraction_text": "Gamma Inc"},
        {"extraction_class": "party", "extraction_text": "Delta Partners"},
        {"extraction_class": "amount", "extraction_text": "$200,000"}
      ]
    ],
    "optimizer": "miprov2",
    "num_candidates": 5
  }' -o optimized.json

# Step 2: Extract using the optimized config
PROMPT=$(jq -r .prompt_description optimized.json)
EXAMPLES=$(jq .examples optimized.json)

curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d "{
    \"raw_text\": \"New contract between Omega Ltd and Sigma Inc for \$75,000.\",
    \"extraction_config\": {
      \"prompt_description\": \"$PROMPT\",
      \"examples\": $EXAMPLES
    }
  }"
```

> **Tip:** Run optimization once, save the result, and reuse the optimized
> prompt across all future extraction requests.

## RAG: Parse a Natural-Language Query

Decompose a user query into semantic search terms and structured metadata
filters for hybrid retrieval:

```bash
curl -X POST http://localhost:8000/api/v1/rag/parse \
  -H "Content-Type: application/json" \
  -d '{
    "query": "invoices over $5000 from Acme Corp due in March 2025",
    "schema_fields": {
      "amount": {"type": "float", "description": "Invoice total in USD"},
      "vendor": {"type": "str", "description": "Vendor name"},
      "due_date": {"type": "date", "description": "Payment due date"},
      "status": {"type": "str", "description": "Invoice status"}
    }
  }'
```

Response:

```json
{
  "semantic_terms": ["invoices", "Acme Corp"],
  "structured_filters": {
    "amount": {"$gte": 5000},
    "vendor": {"$eq": "Acme Corp"},
    "due_date": {"$gte": "2025-03-01", "$lte": "2025-03-31"}
  },
  "confidence": 0.92,
  "explanation": "..."
}
```

## Audit: NDJSON File Logging

Enable audit logging to produce a line-delimited JSON audit trail:

```bash
# .env
AUDIT_ENABLED=true
AUDIT_SINK=jsonfile
AUDIT_LOG_PATH=/var/log/langcore/audit.jsonl
```

Then run any extraction — every LLM call is logged:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "Agreement between Acme Corp and Beta LLC...",
    "extraction_config": {
      "audit": {"enabled": true}
    }
  }'
```

Inspect the audit file:

```bash
tail -1 /var/log/langcore/audit.jsonl | jq .
```

## Disable Plugins Per Request

Override global settings to disable audit/guardrails for a specific call:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "Quick test...",
    "extraction_config": {
      "audit": {"enabled": false},
      "guardrails": {"enabled": false}
    }
  }'
```
