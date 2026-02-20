"""Request models for extraction endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

# Maximum raw_text size in characters (~10 MB of text).
_MAX_RAW_TEXT_CHARS: int = 10_000_000

# File extensions that indicate binary (non-text) content.
# These are rejected at the schema layer before a download
# is attempted.
_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".zip",
        ".tar",
        ".gz",
        ".rar",
        ".7z",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".exe",
        ".dll",
        ".bin",
    }
)


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
    callback_headers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional HTTP headers to include in the webhook "
            'request (e.g. ``{"Authorization": '
            '"Bearer <token>"}``).  Merged with the '
            "default Content-Type and signature headers."
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

    @field_validator("document_url")
    @classmethod
    def _reject_binary_url_suffix(
        cls,
        v: HttpUrl | None,
    ) -> HttpUrl | None:
        """Reject document URLs with known binary extensions.

        The API only accepts plain-text or Markdown content.
        Binary formats (PDF, DOCX, images, etc.) must be
        converted to text before submission.
        """
        if v is not None:
            path = str(v).split("?")[0].split("#")[0]
            dot_idx = path.rfind(".")
            if dot_idx != -1:
                ext = path[dot_idx:].lower()
                if ext in _BINARY_EXTENSIONS:
                    raise ValueError(
                        f"Unsupported file type '{ext}'. "
                        "document_url must point to a "
                        "plain-text or Markdown resource. "
                        "Convert binary files to text "
                        "before submitting."
                    )
        return v

    @field_validator("raw_text")
    @classmethod
    def _cap_raw_text_size(
        cls,
        v: str | None,
    ) -> str | None:
        """Reject raw_text that would consume too much memory
        or that contains null bytes (binary indicator)."""
        if v is not None:
            if len(v) > _MAX_RAW_TEXT_CHARS:
                raise ValueError(
                    f"raw_text exceeds maximum of {_MAX_RAW_TEXT_CHARS:,} characters."
                )
            if "\x00" in v:
                raise ValueError(
                    "raw_text contains null bytes — "
                    "only valid text content is accepted."
                )
        return v

    @model_validator(mode="after")
    def _require_at_least_one_input(
        self,
    ) -> ExtractionRequest:
        """Ensure the caller provides a document URL or text."""
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
    callback_headers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional HTTP headers to include in the batch "
            "webhook request (e.g. Authorization)."
        ),
    )
