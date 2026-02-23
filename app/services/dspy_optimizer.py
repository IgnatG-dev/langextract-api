"""
DSPy prompt optimization service.

Wraps ``langcore-dspy`` to expose prompt optimization via the
API.  Runs optimization asynchronously in a thread pool since
DSPy makes many synchronous LLM calls internally.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langcore.core.data import ExampleData, Extraction
from langcore_dspy import DSPyOptimizer, OptimizedConfig

from app.core.config import get_settings
from app.services.providers import resolve_api_key

logger = logging.getLogger(__name__)


def _build_example_data(
    raw_examples: list[dict[str, Any]],
) -> list[ExampleData]:
    """Convert raw dicts to ``ExampleData`` instances.

    Each dict should have ``text`` and ``extractions`` keys,
    where ``extractions`` is a list of dicts with
    ``extraction_class`` and ``extraction_text``.

    Args:
        raw_examples: List of raw example dicts.

    Returns:
        List of ``ExampleData`` instances.
    """
    results: list[ExampleData] = []
    for ex in raw_examples:
        extractions = [
            Extraction(
                extraction_class=e.get("extraction_class", ""),
                extraction_text=e.get("extraction_text", ""),
            )
            for e in ex.get("extractions", [])
        ]
        results.append(ExampleData(text=ex.get("text", ""), extractions=extractions))
    return results


def _build_expected_results(
    raw_expected: list[list[dict[str, str]]],
) -> list[list[Extraction]]:
    """Convert raw expected results to ``Extraction`` lists.

    Args:
        raw_expected: List of lists of extraction dicts.

    Returns:
        List of lists of ``Extraction`` instances.
    """
    return [
        [
            Extraction(
                extraction_class=e.get("extraction_class", ""),
                extraction_text=e.get("extraction_text", ""),
            )
            for e in doc_expected
        ]
        for doc_expected in raw_expected
    ]


def run_optimization(
    prompt_description: str,
    examples: list[dict[str, Any]],
    train_texts: list[str],
    expected_results: list[list[dict[str, str]]],
    *,
    model_id: str | None = None,
    optimizer: str | None = None,
    num_candidates: int | None = None,
    max_bootstrapped_demos: int | None = None,
    max_labeled_demos: int | None = None,
    num_threads: int | None = None,
) -> dict[str, Any]:
    """Run DSPy prompt optimization synchronously.

    Args:
        prompt_description: Initial extraction prompt.
        examples: Seed few-shot examples.
        train_texts: Training document texts.
        expected_results: Expected extractions per document.
        model_id: LLM to use for optimization.
        optimizer: Optimizer strategy (miprov2 | gepa).
        num_candidates: Number of candidates to explore.
        max_bootstrapped_demos: Max bootstrapped demos.
        max_labeled_demos: Max labelled demos.
        num_threads: Thread count.

    Returns:
        Dict with optimized prompt, examples, and metadata.
    """
    settings = get_settings()

    model_id = model_id or settings.DSPY_MODEL_ID
    optimizer = optimizer or settings.DSPY_OPTIMIZER
    num_candidates = num_candidates or settings.DSPY_NUM_CANDIDATES
    max_bootstrapped_demos = (
        max_bootstrapped_demos or settings.DSPY_MAX_BOOTSTRAPPED_DEMOS
    )
    max_labeled_demos = max_labeled_demos or settings.DSPY_MAX_LABELED_DEMOS
    num_threads = num_threads or settings.DSPY_NUM_THREADS

    api_key = resolve_api_key(model_id)

    logger.info(
        "Starting DSPy optimization (model=%s, optimizer=%s, "
        "train_docs=%d, seed_examples=%d)",
        model_id,
        optimizer,
        len(train_texts),
        len(examples),
    )

    dspy_optimizer = DSPyOptimizer(
        model_id=model_id,
        api_key=api_key,
    )

    example_data = _build_example_data(examples)
    expected = _build_expected_results(expected_results)

    config: OptimizedConfig = dspy_optimizer.optimize(
        prompt_description=prompt_description,
        examples=example_data,
        train_texts=train_texts,
        expected_results=expected,
        optimizer=optimizer,  # type: ignore[arg-type]
        num_candidates=num_candidates,
        max_bootstrapped_demos=max_bootstrapped_demos,
        max_labeled_demos=max_labeled_demos,
        num_threads=num_threads,
    )

    # Serialize examples to plain dicts
    serialized_examples = [
        {
            "text": ex.text,
            "extractions": [
                {
                    "extraction_class": e.extraction_class,
                    "extraction_text": e.extraction_text,
                }
                for e in ex.extractions
            ],
        }
        for ex in config.examples
    ]

    return {
        "prompt_description": config.prompt_description,
        "examples": serialized_examples,
        "metadata": config.metadata,
    }


async def async_run_optimization(
    prompt_description: str,
    examples: list[dict[str, Any]],
    train_texts: list[str],
    expected_results: list[list[dict[str, str]]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Run DSPy optimization asynchronously via thread pool.

    DSPy's optimization is CPU and I/O intensive with many
    synchronous LLM calls, so we offload it to a thread.

    Args:
        prompt_description: Initial extraction prompt.
        examples: Seed few-shot examples.
        train_texts: Training document texts.
        expected_results: Expected extractions per document.
        **kwargs: Forwarded to ``run_optimization``.

    Returns:
        Dict with optimized prompt, examples, and metadata.
    """
    return await asyncio.to_thread(
        run_optimization,
        prompt_description,
        examples,
        train_texts,
        expected_results,
        **kwargs,
    )
