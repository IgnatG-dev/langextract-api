"""Extraction result models returned by workers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractedEntity(BaseModel):
    """A single entity extracted from the document."""

    extraction_class: str = Field(
        ...,
        description=("Entity class (e.g. party, date, monetary_amount)"),
    )
    extraction_text: str = Field(
        ...,
        description=("Exact text extracted from the source document"),
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=("Key-value attributes providing context"),
    )
    char_start: int | None = Field(
        default=None,
        description=("Start character offset in the source text"),
    )
    char_end: int | None = Field(
        default=None,
        description=("End character offset in the source text"),
    )
    confidence_score: float | None = Field(
        default=None,
        description=(
            "Cross-pass confidence score (0.0-1.0). Present when "
            "the extraction uses multiple passes (``passes > 1``)."
        ),
    )


class ExtractionMetadata(BaseModel):
    """Metadata about how the extraction was performed."""

    provider: str = Field(
        ...,
        description=("AI provider / model that ran the extraction"),
    )
    tokens_used: int | None = Field(
        default=None,
        description=(
            "Total tokens consumed (when reported by the "
            "provider). ``None`` if unavailable."
        ),
    )
    processing_time_ms: int = Field(
        default=0,
        description=("Wall-clock processing time in milliseconds"),
    )


class ExtractionResult(BaseModel):
    """Standardised extraction output returned by every provider."""

    entities: list[ExtractedEntity] = Field(
        default_factory=list,
        description="Extracted entities",
    )
    metadata: ExtractionMetadata = Field(
        ...,
        description="Extraction run metadata",
    )
