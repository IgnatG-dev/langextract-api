"""
Default LangExtract configuration for contract entity extraction.

These defaults are used when the caller does not supply their own
``prompt_description`` or ``examples`` inside ``extraction_config``.
Override per-request by including the corresponding keys in the
``extraction_config`` dict sent to ``POST /api/v1/extract``.

Example ``extraction_config`` override::

    {
        "prompt_description": "Extract medication names …",
        "examples": [
            {
                "text": "Take Aspirin 81 mg daily.",
                "extractions": [
                    {
                        "extraction_class": "medication",
                        "extraction_text": "Aspirin 81 mg",
                        "attributes": {
                            "dosage": "81 mg",
                            "frequency": "daily"
                        }
                    }
                ]
            }
        ]
    }
"""

# ── Default prompt ──────────────────────────────────────────────────────────

DEFAULT_PROMPT_DESCRIPTION: str = (
    "Extract key contract entities in order of appearance. "
    "Use exact text for extractions. Do not paraphrase or "
    "overlap entities. "
    "Provide meaningful attributes for each entity to add "
    "context."
)

# ── Default few-shot examples ───────────────────────────────────────────────
# Stored as plain dicts so this module has zero runtime
# dependencies.  Converted to ``lx.data.ExampleData`` objects
# inside the extractor service.

DEFAULT_EXAMPLES: list[dict] = [
    {
        "text": (
            "This Agreement ('Agreement') is entered into as "
            "of January 15, 2025, by and between Acme "
            "Corporation, a Delaware corporation ('Seller'), "
            "and Global Industries LLC ('Buyer'). The total "
            "purchase price shall be $2,500,000 payable within "
            "30 days of closing."
        ),
        "extractions": [
            {
                "extraction_class": "party",
                "extraction_text": "Acme Corporation",
                "attributes": {
                    "role": "Seller",
                    "jurisdiction": "Delaware",
                    "entity_type": "corporation",
                },
            },
            {
                "extraction_class": "party",
                "extraction_text": "Global Industries LLC",
                "attributes": {"role": "Buyer"},
            },
            {
                "extraction_class": "date",
                "extraction_text": "January 15, 2025",
                "attributes": {"type": "effective_date"},
            },
            {
                "extraction_class": "monetary_amount",
                "extraction_text": "$2,500,000",
                "attributes": {"type": "purchase_price"},
            },
            {
                "extraction_class": "term",
                "extraction_text": "30 days",
                "attributes": {
                    "type": "payment_term",
                    "reference": "closing",
                },
            },
        ],
    },
]
