"""
RAG query parsing service.

Wraps ``langcore-rag`` to expose query decomposition via the
API.  Dynamically builds a Pydantic schema from the caller's
field definitions so that the ``QueryParser`` can discover
filterable fields.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, create_model

from langcore_rag import ParsedQuery, QueryParser

from app.core.config import get_settings
from app.services.providers import resolve_api_key

logger = logging.getLogger(__name__)

# Mapping from user-supplied type strings to Python types
_TYPE_MAP: dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "date": date,
    "datetime": datetime,
}


def _build_dynamic_schema(
    schema_fields: dict[str, dict[str, str]],
) -> type[BaseModel]:
    """Build a Pydantic model dynamically from field definitions.

    Args:
        schema_fields: Dict where keys are field names and values
            are dicts with ``type`` (required) and ``description``
            (optional).

    Returns:
        A dynamically created Pydantic ``BaseModel`` subclass.

    Raises:
        ValueError: If an unknown type string is encountered.
    """
    field_definitions: dict[str, Any] = {}

    for name, info in schema_fields.items():
        type_str = info.get("type", "str").lower()
        python_type = _TYPE_MAP.get(type_str)
        if python_type is None:
            raise ValueError(
                f"Unknown type '{type_str}' for field '{name}'. "
                f"Supported types: {sorted(_TYPE_MAP.keys())}"
            )

        description = info.get("description", "")
        # Make all fields optional since they're metadata filters
        field_definitions[name] = (
            python_type | None,
            Field(default=None, description=description),
        )

    return create_model("DynamicRAGSchema", **field_definitions)


def parse_query(
    query_text: str,
    schema_fields: dict[str, dict[str, str]],
    *,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Parse a natural-language query synchronously.

    Args:
        query_text: The user's search query.
        schema_fields: Field definitions for filter discovery.
        model_id: LLM model for parsing.
        temperature: Sampling temperature.
        max_tokens: Max tokens for the response.

    Returns:
        Dict with semantic_terms, structured_filters,
        confidence, and explanation.
    """
    settings = get_settings()

    model_id = model_id or settings.RAG_MODEL_ID
    temperature = temperature if temperature is not None else settings.RAG_TEMPERATURE
    max_tokens = max_tokens or settings.RAG_MAX_TOKENS
    max_retries = settings.RAG_MAX_RETRIES

    api_key = resolve_api_key(model_id)

    logger.info(
        "Parsing RAG query (model=%s, fields=%d)",
        model_id,
        len(schema_fields),
    )

    schema = _build_dynamic_schema(schema_fields)

    litellm_kwargs: dict[str, Any] = {}
    if api_key:
        litellm_kwargs["api_key"] = api_key

    parser = QueryParser(
        schema=schema,
        model_id=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        **litellm_kwargs,
    )

    result: ParsedQuery = parser.parse(query_text)

    return {
        "semantic_terms": list(result.semantic_terms),
        "structured_filters": dict(result.structured_filters),
        "confidence": result.confidence,
        "explanation": result.explanation,
    }


async def async_parse_query(
    query_text: str,
    schema_fields: dict[str, dict[str, str]],
    *,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Parse a natural-language query asynchronously.

    Uses the ``QueryParser.async_parse`` method for non-blocking
    LLM calls.

    Args:
        query_text: The user's search query.
        schema_fields: Field definitions for filter discovery.
        model_id: LLM model for parsing.
        temperature: Sampling temperature.
        max_tokens: Max tokens for the response.

    Returns:
        Dict with semantic_terms, structured_filters,
        confidence, and explanation.
    """
    settings = get_settings()

    model_id = model_id or settings.RAG_MODEL_ID
    temperature = temperature if temperature is not None else settings.RAG_TEMPERATURE
    max_tokens = max_tokens or settings.RAG_MAX_TOKENS
    max_retries = settings.RAG_MAX_RETRIES

    api_key = resolve_api_key(model_id)

    logger.info(
        "Parsing RAG query async (model=%s, fields=%d)",
        model_id,
        len(schema_fields),
    )

    schema = _build_dynamic_schema(schema_fields)

    litellm_kwargs: dict[str, Any] = {}
    if api_key:
        litellm_kwargs["api_key"] = api_key

    parser = QueryParser(
        schema=schema,
        model_id=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        **litellm_kwargs,
    )

    result: ParsedQuery = await parser.async_parse(query_text)

    return {
        "semantic_terms": list(result.semantic_terms),
        "structured_filters": dict(result.structured_filters),
        "confidence": result.confidence,
        "explanation": result.explanation,
    }
