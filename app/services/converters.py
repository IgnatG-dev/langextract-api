"""
Data conversion helpers for LangExtract results.

Converts between LangExtract's internal data structures and
the API's Pydantic-friendly dict format.
"""

from __future__ import annotations

import logging
from typing import Any

import langextract as lx

logger = logging.getLogger(__name__)


def build_examples(
    raw_examples: list[dict[str, Any]],
) -> list[lx.data.ExampleData]:
    """Convert plain-dict examples into ``lx.data.ExampleData``.

    Args:
        raw_examples: List of dicts, each with ``text`` and
            ``extractions`` keys.

    Returns:
        A list of ``ExampleData`` ready for ``lx.extract()``.
    """
    return [
        lx.data.ExampleData(
            text=ex["text"],
            extractions=[
                lx.data.Extraction(
                    extraction_class=e["extraction_class"],
                    extraction_text=e["extraction_text"],
                    attributes=e.get("attributes"),
                )
                for e in ex.get("extractions", [])
            ],
        )
        for ex in raw_examples
    ]


def convert_extractions(
    result: lx.data.AnnotatedDocument,
) -> list[dict[str, Any]]:
    """Flatten ``AnnotatedDocument.extractions`` into dicts.

    Args:
        result: The annotated document from ``lx.extract()``.

    Returns:
        A list of entity dicts matching ``ExtractedEntity``
        schema.
    """
    entities: list[dict[str, Any]] = []
    for ext in result.extractions or []:
        # Defensive coercion: the LLM occasionally returns a
        # dict for extraction_text — stringify it so
        # downstream consumers always receive a string.
        raw_text = ext.extraction_text
        if not isinstance(raw_text, (str, int, float)):
            logger.warning(
                "Coercing non-scalar extraction_text (%s) to str for class '%s'",
                type(raw_text).__name__,
                ext.extraction_class,
            )
            raw_text = str(raw_text)

        entity: dict[str, Any] = {
            "extraction_class": ext.extraction_class,
            "extraction_text": str(raw_text),
            "attributes": ext.attributes or {},
            "char_start": (ext.char_interval.start_pos if ext.char_interval else None),
            "char_end": (ext.char_interval.end_pos if ext.char_interval else None),
        }
        # Include cross-pass confidence score when available
        # (multi-pass extraction with total_passes > 1).
        if getattr(ext, "confidence_score", None) is not None:
            entity["confidence_score"] = ext.confidence_score
        entities.append(entity)
    return entities


def extract_token_usage(
    lx_result: lx.data.AnnotatedDocument,
) -> int | None:
    """Attempt to extract token usage from a LangExtract result.

    Args:
        lx_result: The annotated document from ``lx.extract()``.

    Returns:
        Token count if available, ``None`` otherwise.
    """
    usage = getattr(lx_result, "usage", None)
    if usage and hasattr(usage, "total_tokens"):
        return int(usage.total_tokens)
    if isinstance(usage, dict) and "total_tokens" in usage:
        return int(usage["total_tokens"])
    return None
