"""Document classification route.

Provides a synchronous endpoint for document type detection
and metadata extraction.  Unlike the ``/extract`` endpoint
which dispatches work to Celery workers, ``/classify`` runs
the LLM call inline and returns the result immediately.

This is designed for quick, single-turn classification tasks
(e.g. document type, language, industry detection) where the
caller needs the result before proceeding.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import litellm
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.provider_manager import ProviderManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["classification"])


# ── Request / Response models ───────────────────────────────


class ClassifyRequest(BaseModel):
    """Request body for document classification."""

    text: str = Field(
        ...,
        min_length=10,
        max_length=500_000,
        description=(
            "Document text to classify. Typically OCR-extracted "
            "text from a contract PDF."
        ),
    )
    provider: str = Field(
        default="gpt-4o",
        min_length=2,
        max_length=128,
        description="LLM model ID passed to LiteLLM.",
    )
    prompt: str = Field(
        ...,
        min_length=10,
        max_length=50_000,
        description=(
            "System prompt instructing the LLM what to classify "
            "and the expected JSON output structure."
        ),
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="LLM temperature (0 = deterministic).",
    )
    max_tokens: int = Field(
        default=4096,
        ge=256,
        le=16384,
        description="Maximum tokens in the LLM response.",
    )


class ClassifyResponse(BaseModel):
    """Response from document classification."""

    result: dict[str, Any] = Field(
        ...,
        description="Parsed JSON classification result.",
    )
    provider: str = Field(
        ...,
        description="Model used for classification.",
    )
    tokens_used: int | None = Field(
        default=None,
        description="Total tokens consumed.",
    )
    processing_time_ms: int = Field(
        ...,
        description="Wall-clock processing time in milliseconds.",
    )


# ── Endpoint ────────────────────────────────────────────────


@router.post(
    "/classify",
    response_model=ClassifyResponse,
)
async def classify_document(
    request: ClassifyRequest,
) -> ClassifyResponse:
    """Classify a document using an LLM with JSON output.

    Sends the document text to the specified model with a
    system prompt and returns the structured JSON result.
    Uses ``litellm`` for provider-agnostic model routing.

    This endpoint runs synchronously (no Celery queue) because
    classification is typically a single, fast LLM call that
    the caller needs before proceeding with further processing.
    """
    start_ms = int(time.time() * 1000)
    settings = get_settings()

    logger.info(
        "Classifying document: provider=%s, text_length=%d",
        request.provider,
        len(request.text),
    )

    try:
        # Ensure LiteLLM cache is configured
        manager = ProviderManager.instance()
        manager.ensure_cache()

        # Strip the "litellm/" or "litellm-" routing prefix that
        # ai-analysis-api adds for langcore plugin resolution.
        # The classify route calls litellm directly, so the bare
        # model ID is needed (e.g. "mistral/mistral-large-latest").
        model_id = request.provider
        if model_id.startswith("litellm/"):
            model_id = model_id[len("litellm/") :]
        elif model_id.startswith("litellm-"):
            model_id = model_id[len("litellm-") :]

        # Truncate text to first ~50k chars for classification
        # (document type can be determined from the first few pages)
        max_text_chars = 50_000
        text = request.text[:max_text_chars]
        if len(request.text) > max_text_chars:
            logger.info(
                "Truncated text from %d to %d chars for classification",
                len(request.text),
                max_text_chars,
            )

        # Call LiteLLM with JSON mode
        response = await litellm.acompletion(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": request.prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze the following document and return your "
                        "classification as a JSON object:\n\n"
                        f"{text}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        # Parse the JSON response
        content = response.choices[0].message.content
        if not content:
            raise HTTPException(
                status_code=502,
                detail="LLM returned empty response",
            )

        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error(
                "Failed to parse LLM JSON response: %s",
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail=f"LLM returned invalid JSON: {exc}",
            ) from exc

        elapsed_ms = int(time.time() * 1000) - start_ms
        tokens = None
        if response.usage:
            tokens = response.usage.total_tokens

        logger.info(
            "Classification complete: provider=%s, tokens=%s, time=%dms",
            request.provider,
            tokens,
            elapsed_ms,
        )

        return ClassifyResponse(
            result=result,
            provider=request.provider,
            tokens_used=tokens,
            processing_time_ms=elapsed_ms,
        )

    except HTTPException:
        raise
    except litellm.RateLimitError as exc:
        elapsed_ms = int(time.time() * 1000) - start_ms
        logger.warning(
            "Rate limited during classification: %s (%dms)",
            exc,
            elapsed_ms,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limited by LLM provider: {exc}",
        ) from exc
    except litellm.AuthenticationError as exc:
        logger.error("Authentication error: %s", exc)
        raise HTTPException(
            status_code=401,
            detail=f"LLM authentication failed: {exc}",
        ) from exc
    except Exception as exc:
        elapsed_ms = int(time.time() * 1000) - start_ms
        logger.error(
            "Classification failed: %s (%dms)",
            exc,
            elapsed_ms,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Classification failed: {exc}",
        ) from exc
