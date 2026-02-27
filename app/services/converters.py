"""
Data conversion helpers for LangCore results.

Converts between LangCore's internal data structures and
the API's Pydantic-friendly dict format.
"""

from __future__ import annotations

import logging
from typing import Any

import langcore as lx

logger = logging.getLogger(__name__)

# Map common string labels to numeric confidence values.
_CONFIDENCE_LABEL_MAP: dict[str, float] = {
    "very high": 0.95,
    "high": 0.95,
    "medium": 0.7,
    "moderate": 0.7,
    "low": 0.4,
    "very low": 0.2,
}

# Default confidence when the value cannot be interpreted.
_DEFAULT_CONFIDENCE: float = 0.9


def _coerce_confidence(raw: Any) -> float:
    """Coerce an LLM-provided confidence value to a float in 0.0-1.0.

    LLMs sometimes return descriptive labels (``"high"``) or
    percentage integers (``85``) instead of the requested 0-1
    float.  This function normalises all variants so downstream
    code and numeric DB columns always receive a valid float.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        if val != val:  # NaN check
            return _DEFAULT_CONFIDENCE
        return val / 100.0 if val > 1.0 else val

    if isinstance(raw, str):
        # Try numeric parse first ("0.85", "85")
        try:
            val = float(raw)
            return val / 100.0 if val > 1.0 else val
        except ValueError:
            pass
        label = raw.strip().lower()
        if label in _CONFIDENCE_LABEL_MAP:
            return _CONFIDENCE_LABEL_MAP[label]
        logger.warning(
            "Unrecognized confidence label '%s', defaulting to %s",
            raw,
            _DEFAULT_CONFIDENCE,
        )

    return _DEFAULT_CONFIDENCE


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

        attrs = dict(ext.attributes) if ext.attributes else {}

        # Defensive coercion: the LLM may return a string label
        # (e.g. "high", "medium") instead of a numeric confidence
        # value.  Coerce to float so downstream consumers always
        # receive a number compatible with numeric DB columns.
        if "confidence" in attrs:
            attrs["confidence"] = _coerce_confidence(attrs["confidence"])

        entity: dict[str, Any] = {
            "extraction_class": ext.extraction_class,
            "extraction_text": str(raw_text),
            "attributes": attrs,
            "char_start": (ext.char_interval.start_pos if ext.char_interval else None),
            "char_end": (ext.char_interval.end_pos if ext.char_interval else None),
        }
        # Include cross-pass confidence score when available
        # (multi-pass extraction with total_passes > 1).
        # Round to 2 decimal places when below 1.0 for cleaner output.
        if getattr(ext, "confidence_score", None) is not None:
            score = ext.confidence_score
            entity["confidence_score"] = score if score >= 1.0 else round(score, 2)
        entities.append(entity)
    return entities


def extract_token_usage(
    lx_result: lx.data.AnnotatedDocument,
) -> int | None:
    """Attempt to extract token usage from a LangCore result.

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
