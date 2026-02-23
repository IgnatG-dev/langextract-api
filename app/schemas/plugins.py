"""Request and response models for plugin endpoints (DSPy, RAG)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── DSPy optimization ───────────────────────────────────────


class DSPyOptimizationRequest(BaseModel):
    """Request body for DSPy prompt optimization.

    Requires training data: seed examples, training document
    texts, and expected extraction results for each document.
    """

    prompt_description: str = Field(
        ...,
        min_length=10,
        description=(
            "Initial extraction prompt to optimize. "
            "This is the starting point that DSPy will "
            "improve upon."
        ),
    )
    examples: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "Seed few-shot examples. Each dict should have "
            "``text`` and ``extractions`` keys, where "
            "``extractions`` is a list of dicts with "
            "``extraction_class`` and ``extraction_text``."
        ),
    )
    train_texts: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Training document texts to optimize against. "
            "Must have the same length as ``expected_results``."
        ),
    )
    expected_results: list[list[dict[str, str]]] = Field(
        ...,
        min_length=1,
        description=(
            "Expected extractions for each training document. "
            "Each inner list contains dicts with "
            "``extraction_class`` and ``extraction_text``. "
            "Must be parallel with ``train_texts``."
        ),
    )
    model_id: str | None = Field(
        default=None,
        description=(
            "LLM model ID for DSPy optimization. "
            "Defaults to ``DSPY_MODEL_ID`` setting."
        ),
    )
    optimizer: str | None = Field(
        default=None,
        description=(
            "Optimizer strategy: 'miprov2' (fast, general) "
            "or 'gepa' (reflective, feedback-driven). "
            "Defaults to ``DSPY_OPTIMIZER`` setting."
        ),
    )
    num_candidates: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Number of candidates to explore (MIPROv2 only).",
    )
    max_bootstrapped_demos: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description="Max bootstrapped demos for the optimizer.",
    )
    max_labeled_demos: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description="Max labelled demos for the optimizer.",
    )
    num_threads: int | None = Field(
        default=None,
        ge=1,
        le=16,
        description="Thread count for parallel evaluation.",
    )


class DSPyOptimizationResponse(BaseModel):
    """Response from DSPy prompt optimization."""

    prompt_description: str = Field(
        ...,
        description="The optimized extraction prompt.",
    )
    examples: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Curated few-shot examples selected by the "
            "optimizer. Each dict has ``text`` and "
            "``extractions`` keys."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optimization metrics: optimizer name, model, "
            "elapsed time, candidate count, etc."
        ),
    )


# ── RAG query parsing ──────────────────────────────────────


class RAGQueryParseRequest(BaseModel):
    """Request body for RAG query parsing.

    Decomposes a natural-language query into semantic search
    terms and structured metadata filters.
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Natural-language query to parse.",
    )
    schema_fields: dict[str, dict[str, str]] = Field(
        ...,
        description=(
            "Schema field definitions for filter discovery. "
            "Outer key is the field name, inner dict has "
            "``type`` (e.g. 'str', 'int', 'float', 'bool', "
            "'date') and optional ``description``."
        ),
    )
    model_id: str | None = Field(
        default=None,
        description=(
            "LLM model for query parsing. " "Defaults to ``RAG_MODEL_ID`` setting."
        ),
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the parser LLM.",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=100,
        le=4096,
        description="Max tokens for the parser LLM response.",
    )


class RAGQueryParseResponse(BaseModel):
    """Response from RAG query parsing."""

    semantic_terms: list[str] = Field(
        default_factory=list,
        description=("Free-text keywords/phrases for vector " "similarity search."),
    )
    structured_filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Metadata filters using MongoDB-style operators "
            "($eq, $gt, $gte, $lt, $lte, $in, $nin, etc.)."
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=("Parser confidence score (0.0 - 1.0)."),
    )
    explanation: str = Field(
        default="",
        description="Human-readable rationale for the parse.",
    )
