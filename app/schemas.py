"""
Pydantic models for API requests and responses.

All data contracts live here so that ``main.py`` and ``tasks.py``
can import lightweight schema objects without circular dependencies.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator


# ── Task state enum ─────────────────────────────────────────────────────────


class TaskState(str, Enum):
    """Possible states of a Celery task."""

    PENDING = "PENDING"
    STARTED = "STARTED"
    PROGRESS = "PROGRESS"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"
    RETRY = "RETRY"


# ── Request models ──────────────────────────────────────────────────────────


class ExtractionRequest(BaseModel):
    """Request body for submitting an extraction task.

    At least one of ``document_url`` or ``raw_text`` must be provided.
    A ``callback_url`` can be supplied so the worker POSTs the result
    back (webhook) instead of requiring the caller to poll.
    """

    document_url: HttpUrl | None = Field(
        default=None,
        description="URL to the document to extract from",
    )
    raw_text: str | None = Field(
        default=None,
        description="Raw text blob to process directly",
    )
    provider: str = Field(
        default="gemini-1.5-pro",
        description=(
            "AI provider / model to use for extraction. "
            "Can override the DEFAULT_PROVIDER env var per-request."
        ),
    )
    passes: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Number of extraction passes for higher accuracy",
    )
    callback_url: HttpUrl | None = Field(
        default=None,
        description=(
            "Webhook URL — if provided, the worker will POST the "
            "completed result to this URL instead of only storing "
            "it in Redis."
        ),
    )
    extraction_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional extraction configuration overrides",
    )

    @model_validator(mode="after")
    def _require_at_least_one_input(self) -> "ExtractionRequest":
        """Ensure the caller provides a document URL or raw text."""
        if not self.document_url and not self.raw_text:
            raise ValueError(
                "At least one of 'document_url' or 'raw_text' " "must be provided."
            )
        return self


class BatchExtractionRequest(BaseModel):
    """Request body for submitting a batch of extraction tasks."""

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


# ── Response models ─────────────────────────────────────────────────────────


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
        description="Progress metadata (available in PROGRESS state)",
    )
    result: Any | None = Field(
        default=None,
        description="Task result (available in SUCCESS state)",
    )
    error: str | None = Field(
        default=None,
        description="Error message (available in FAILURE state)",
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


# ── Extraction result models (returned by workers) ─────────────────────────


class ExtractedEntity(BaseModel):
    """A single entity extracted from the document."""

    type: str = Field(
        ...,
        description="Entity type (e.g. Company, Date, Amount)",
    )
    value: str = Field(
        ...,
        description="Extracted value",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence score (0-1)",
    )


class ExtractionMetadata(BaseModel):
    """Metadata about how the extraction was performed."""

    provider: str = Field(
        ...,
        description="AI provider / model that ran the extraction",
    )
    tokens_used: int = Field(
        default=0,
        description="Total tokens consumed",
    )
    processing_time_ms: int = Field(
        default=0,
        description="Wall-clock processing time in milliseconds",
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


# ── Health models ───────────────────────────────────────────────────────────


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
