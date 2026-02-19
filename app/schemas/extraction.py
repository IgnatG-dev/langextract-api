"""
Pydantic models for extraction requests and responses.

All data contracts live here so that route handlers, workers,
and services can import lightweight schema objects without
circular dependencies.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

# Maximum raw_text size in characters (~10 MB of text).
_MAX_RAW_TEXT_CHARS: int = 10_000_000

# ── Task state enum ─────────────────────────────────────────


class TaskState(StrEnum):
    """Possible states of a Celery task."""

    PENDING = "PENDING"
    STARTED = "STARTED"
    PROGRESS = "PROGRESS"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"
    RETRY = "RETRY"


# ── Provider validation ─────────────────────────────────────

Provider = Annotated[
    str,
    Field(
        min_length=2,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./-]*$",
        description=(
            "LLM model ID (e.g. 'gpt-4o', 'gemini-2.5-flash'). "
            "Must start with an alphanumeric character and "
            "contain only letters, digits, dots, underscores, "
            "slashes, and hyphens."
        ),
    ),
]


# ── Extraction configuration model ─────────────────────────


class ExtractionConfig(BaseModel):
    """Typed extraction configuration overrides.

    Replaces the previous ``dict[str, Any]`` so that OpenAPI
    docs are explicit and users receive proper validation errors.
    """

    prompt_description: str | None = Field(
        default=None,
        description=("Custom prompt for the extraction pipeline."),
    )
    examples: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Few-shot examples. Each dict should have "
            "``text`` and ``extractions`` keys."
        ),
    )
    max_workers: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum parallel extraction workers.",
    )
    max_char_buffer: int | None = Field(
        default=None,
        ge=100,
        description="Character buffer size for chunking.",
    )
    additional_context: str | None = Field(
        default=None,
        description=("Extra context string appended to the prompt."),
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="LLM sampling temperature.",
    )
    context_window_chars: int | None = Field(
        default=None,
        ge=1000,
        description="Context window size in characters.",
    )

    def to_flat_dict(self) -> dict[str, Any]:
        """Return a dict with only non-None values.

        Returns:
            Flat dict suitable for ``run_extraction``.
        """
        return {k: v for k, v in self.model_dump().items() if v is not None}


# ── Request models ──────────────────────────────────────────


class ExtractionRequest(BaseModel):
    """Request body for submitting an extraction task.

    At least one of ``document_url`` or ``raw_text`` must be
    provided.  A ``callback_url`` can be supplied so the worker
    POSTs the result back (webhook) instead of requiring the
    caller to poll.
    """

    document_url: HttpUrl | None = Field(
        default=None,
        description="URL to the document to extract from",
    )
    raw_text: str | None = Field(
        default=None,
        description="Raw text blob to process directly",
    )
    provider: Provider = Field(
        default="gpt-4o",
        description=(
            "LLM model ID to use for extraction "
            "(e.g. 'gpt-4o', 'gemini-2.5-flash'). "
            "Can override the DEFAULT_PROVIDER env var "
            "per-request."
        ),
    )
    passes: int = Field(
        default=1,
        ge=1,
        le=5,
        description=("Number of extraction passes for higher accuracy"),
    )
    callback_url: HttpUrl | None = Field(
        default=None,
        description=(
            "Webhook URL — if provided, the worker will POST "
            "the completed result to this URL instead of only "
            "storing it in Redis."
        ),
    )
    extraction_config: ExtractionConfig = Field(
        default_factory=ExtractionConfig,
        description=(
            "Optional LangExtract configuration overrides "
            "(prompt, examples, temperature, etc.)."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=256,
        pattern=r"^[\x21-\x7E]+$",
        description=(
            "Optional client-supplied idempotency key. When "
            "provided, repeat submissions with the same key "
            "return the original task ID instead of creating "
            "a new task.  Must contain only printable ASCII "
            "characters (no whitespace or control chars)."
        ),
    )

    @field_validator("raw_text")
    @classmethod
    def _cap_raw_text_size(
        cls,
        v: str | None,
    ) -> str | None:
        """Reject raw_text that would consume too much memory."""
        if v is not None and len(v) > _MAX_RAW_TEXT_CHARS:
            raise ValueError(
                f"raw_text exceeds maximum of {_MAX_RAW_TEXT_CHARS:,} characters."
            )
        return v

    @model_validator(mode="after")
    def _require_at_least_one_input(
        self,
    ) -> ExtractionRequest:
        """Ensure the caller provides a document URL or raw text."""
        if not self.document_url and not self.raw_text:
            raise ValueError(
                "At least one of 'document_url' or 'raw_text' must be provided."
            )
        return self


class BatchExtractionRequest(BaseModel):
    """Request body for submitting a batch of extractions."""

    batch_id: str = Field(
        ...,
        description="Unique identifier for this batch",
    )
    documents: list[ExtractionRequest] = Field(
        ...,
        description="List of documents to extract",
        min_length=1,
    )
    callback_url: HttpUrl | None = Field(
        default=None,
        description=(
            "Webhook URL for the aggregated batch result. "
            "Overrides per-document callback_url values."
        ),
    )


# ── Response models ─────────────────────────────────────────


class TaskSubmitResponse(BaseModel):
    """Returned immediately when a task is submitted."""

    task_id: str = Field(
        ...,
        description="Unique Celery task identifier",
    )
    status: str = Field(
        default="submitted",
        description="Initial task status",
    )
    message: str = Field(
        default="Task submitted successfully",
        description="Human-readable status message",
    )


class BatchTaskSubmitResponse(BaseModel):
    """Returned when a batch is submitted."""

    batch_task_id: str = Field(
        ...,
        description=("Celery task ID for the batch orchestrator"),
    )
    document_task_ids: list[str] = Field(
        default_factory=list,
        description=("Per-document Celery task IDs (parallel mode)"),
    )
    status: str = Field(
        default="submitted",
        description="Initial batch status",
    )
    message: str = Field(
        default="Batch submitted successfully",
        description="Human-readable status message",
    )


class TaskStatusResponse(BaseModel):
    """Returned when polling for task status."""

    task_id: str = Field(
        ...,
        description="Unique Celery task identifier",
    )
    state: TaskState = Field(
        ...,
        description="Current state of the task",
    )
    progress: dict[str, Any] | None = Field(
        default=None,
        description=("Progress metadata (available in PROGRESS state)"),
    )
    result: Any | None = Field(
        default=None,
        description=("Task result (available in SUCCESS state)"),
    )
    error: str | None = Field(
        default=None,
        description=("Error message (available in FAILURE state)"),
    )


class TaskRevokeResponse(BaseModel):
    """Returned when a task revocation is requested."""

    task_id: str = Field(
        ...,
        description="Identifier of the revoked task",
    )
    status: str = Field(
        default="revoked",
        description="Revocation status",
    )
    message: str = Field(
        default="Task revocation signal sent",
        description="Human-readable status message",
    )


# ── Extraction result models (returned by workers) ─────────


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


# ── Health models ───────────────────────────────────────────


class HealthResponse(BaseModel):
    """Returned by the health-check endpoint."""

    status: str = Field(
        ...,
        description="Service health status",
    )
    version: str = Field(
        ...,
        description="Application version",
    )


class CeleryHealthResponse(BaseModel):
    """Returned by the Celery health-check endpoint."""

    status: str = Field(
        ...,
        description="Overall Celery health status",
    )
    message: str = Field(
        ...,
        description="Human-readable summary",
    )
    workers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-worker status details",
    )
