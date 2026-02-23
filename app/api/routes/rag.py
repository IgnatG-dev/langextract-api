"""RAG query parsing route."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.schemas.plugins import (
    RAGQueryParseRequest,
    RAGQueryParseResponse,
)
from app.services.rag_parser import async_parse_query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rag"])


@router.post(
    "/rag/parse",
    response_model=RAGQueryParseResponse,
    summary="Parse a natural-language query for RAG retrieval",
    description=(
        "Decomposes a natural-language query into semantic search "
        "terms and structured metadata filters using an LLM. "
        "Provide a schema describing your filterable metadata "
        "fields and the parser will split the query into "
        "vector-search terms and precise filters suitable for "
        "hybrid RAG retrieval."
    ),
)
async def parse_rag_query(
    request: RAGQueryParseRequest,
) -> RAGQueryParseResponse:
    """Parse a query for hybrid RAG retrieval.

    Requires ``RAG_ENABLED=true`` in settings.  The response
    includes semantic terms for vector search, structured
    filters for metadata matching, a confidence score, and
    a human-readable explanation.
    """
    settings = get_settings()

    if not settings.RAG_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "RAG query parsing is disabled. " "Set RAG_ENABLED=true to enable."
            ),
        )

    if not request.schema_fields:
        raise HTTPException(
            status_code=400,
            detail="schema_fields must contain at least one field.",
        )

    try:
        result = await async_parse_query(
            query_text=request.query,
            schema_fields=request.schema_fields,
            model_id=request.model_id,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("RAG query parsing failed")
        raise HTTPException(
            status_code=500,
            detail=f"Query parsing failed: {exc}",
        ) from exc

    return RAGQueryParseResponse(**result)
