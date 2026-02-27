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
            "LLM model ID passed to LiteLLM. Supports cloud providers "
            "(e.g. 'gpt-4o', 'gemini-2.5-flash', 'claude-3.5-sonnet') "
            "and local/self-hosted models "
            "(e.g. 'ollama/llama3.1', 'hosted_vllm/meta-llama/Llama-3.1-8B'). "
            "Must start with an alphanumeric character and "
            "contain only letters, digits, dots, underscores, "
            "slashes, and hyphens."
        ),
    ),
]


# ── Extraction configuration model ─────────────────────────


class GuardrailsConfig(BaseModel):
    """Per-request guardrails configuration.

    Controls LLM output validation, retry, and corrective
    prompting via ``langcore-guardrails``.  When provided
    in ``extraction_config``, overrides the global
    ``GUARDRAILS_*`` settings for this request.
    """

    enabled: bool | None = Field(
        default=None,
        description=(
            "Enable output validation with retry. "
            "When ``None``, falls back to the global "
            "``GUARDRAILS_ENABLED`` setting."
        ),
    )
    json_schema: dict[str, Any] | None = Field(
        default=None,
        description=(
            "JSON Schema dict to validate LLM output against. "
            "When set, a ``JsonSchemaValidator`` is created "
            "automatically."
        ),
    )
    regex_pattern: str | None = Field(
        default=None,
        description=(
            "Regex pattern the LLM output must match. "
            "When set, a ``RegexValidator`` is created."
        ),
    )
    regex_description: str | None = Field(
        default=None,
        description=(
            "Human-readable description of the regex pattern, "
            "used in corrective error messages."
        ),
    )
    confidence_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum confidence score threshold. When set, a "
            "``ConfidenceThresholdValidator`` rejects outputs "
            "below this threshold. Requires the LLM output to "
            "include a confidence score field."
        ),
    )
    confidence_score_key: str | None = Field(
        default=None,
        description=(
            "Name of the field containing the confidence score "
            "in the LLM output. Defaults to 'confidence_score'."
        ),
    )
    required_fields: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "List of field names that must be present in the "
            "LLM output. When set, a "
            "``FieldCompletenessValidator`` is created using "
            "a dynamic schema with these fields as required."
        ),
    )
    on_fail: str | None = Field(
        default=None,
        description=(
            "Action to take when validation fails: "
            "'exception' (raise), 'reask' (retry with correction), "
            "'filter' (return None), 'noop' (ignore). "
            "Default is 'reask'."
        ),
    )
    max_retries: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description=(
            "Maximum retry attempts on validation failure. "
            "Overrides ``GUARDRAILS_MAX_RETRIES``."
        ),
    )
    include_output_in_correction: bool | None = Field(
        default=None,
        description=(
            "Include the invalid output in the correction "
            "prompt.  Set ``False`` for error-only mode to "
            "save tokens."
        ),
    )
    json_schema_strict: bool = Field(
        default=True,
        description=(
            "When ``True``, additional properties not in "
            "the schema cause validation failure."
        ),
    )
    pydantic_schema_fields: dict[str, dict[str, str]] | None = Field(
        default=None,
        description=(
            "Dynamic Pydantic schema for SchemaValidator. "
            "Outer key is the field name, inner dict has "
            "``type`` ('str', 'int', 'float', 'bool') and "
            "optional ``description``. When set, a "
            "``SchemaValidator`` is created automatically."
        ),
    )
    pydantic_strict: bool = Field(
        default=False,
        description=(
            "When ``True``, the SchemaValidator uses strict "
            "Pydantic validation (no type coercion)."
        ),
    )
    consistency_rules: list[dict[str, str]] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Business-rule consistency checks. Each dict has "
            "``field`` (field name to check), ``operator`` "
            "('lt', 'gt', 'eq', 'ne'), and ``other_field`` "
            "(field to compare against). E.g. "
            '``{"field": "start_date", "operator": "lt", '
            '"other_field": "end_date"}`` ensures start_date '
            "is before end_date."
        ),
    )


class AuditConfig(BaseModel):
    """Per-request audit configuration.

    Controls structured audit logging via
    ``langcore-audit``.  When provided in
    ``extraction_config``, overrides the global
    ``AUDIT_*`` settings for this request.
    """

    enabled: bool | None = Field(
        default=None,
        description=(
            "Enable audit logging. When ``None``, falls back "
            "to the global ``AUDIT_ENABLED`` setting."
        ),
    )
    sample_length: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Store truncated prompt/response samples in audit "
            "records for debugging.  Overrides "
            "``AUDIT_SAMPLE_LENGTH``."
        ),
    )


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
    consensus_providers: list[str] | None = Field(
        default=None,
        min_length=2,
        description=(
            "Two or more LLM model IDs to use for consensus extraction. "
            "When set, the response with the highest cross-model agreement "
            "is kept. Costs Nx API calls."
        ),
    )
    consensus_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum Jaccard similarity for two outputs to be considered "
            "in agreement (default 0.6)."
        ),
    )
    structured_output: bool | None = Field(
        default=None,
        description=(
            "Enable LLM-level structured output (``response_format``) "
            "for providers that support JSON Schema constraints. "
            "Supported by cloud APIs (OpenAI, Anthropic, Gemini 2.0+, "
            "Groq, Mistral) and local servers (Ollama >= 0.5, vLLM). "
            "When ``True``, a JSON Schema is auto-generated from "
            "the extraction examples and passed to the LLM so its "
            "output is guaranteed to be valid JSON matching the "
            "extraction format — eliminating parse errors and "
            "improving reliability.  When ``None`` (default), "
            "structured output is enabled automatically for "
            "supported providers.  Set to ``False`` to force "
            "prompt-only extraction."
        ),
    )
    guardrails: GuardrailsConfig | None = Field(
        default=None,
        description=(
            "Output validation and retry configuration via "
            "langcore-guardrails.  When unset, falls back "
            "to the global ``GUARDRAILS_*`` settings."
        ),
    )
    audit: AuditConfig | None = Field(
        default=None,
        description=(
            "Audit logging configuration via "
            "langcore-audit.  When unset, falls back "
            "to the global ``AUDIT_*`` settings."
        ),
    )
    hybrid_rules: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Deterministic extraction rules for langcore-hybrid. "
            "Each dict has ``type`` ('regex' or 'callable'), "
            "``pattern`` (regex string with named groups), and "
            "optional ``description`` and ``confidence``. "
            "When set and HYBRID_ENABLED=true, rules are tried "
            "before the LLM — matching prompts skip the LLM "
            "entirely for zero-latency deterministic extraction."
        ),
    )
    no_cache: bool | None = Field(
        default=None,
        description=(
            "When ``True``, bypass the extraction-result cache "
            "for this request — always run a fresh extraction "
            "and do not store the result in the cache.  Useful "
            "for debugging, prompt iteration, and one-off "
            "re-processing.  Defaults to ``False`` (caching "
            "follows the global ``EXTRACTION_CACHE_ENABLED`` "
            "setting)."
        ),
    )

    def to_flat_dict(self) -> dict[str, Any]:
        """Return a dict with only non-None values.

        Nested models (``guardrails``, ``audit``) are serialized
        to plain dicts so the result is JSON-serializable and
        safe for Celery task arguments.

        Returns:
            Flat dict suitable for ``run_extraction``.
        """
        data: dict[str, Any] = {}
        for k, v in self.model_dump().items():
            if v is not None:
                data[k] = v
        return data


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
            "LLM model ID to use for extraction. "
            "Cloud: 'gpt-4o', 'gemini-2.5-flash', 'claude-3.5-sonnet'. "
            "Local: 'ollama/llama3.1', 'hosted_vllm/<model>'. "
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
            "Optional LangCore configuration overrides "
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
