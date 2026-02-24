"""
DSPy prompt optimization service.

Wraps ``langcore-dspy`` to expose prompt optimization via the
API.  Runs optimization asynchronously in a thread pool since
DSPy makes many synchronous LLM calls internally.

Also provides persistence (save/load) and evaluation
endpoints for optimized configs.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
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

    return _serialize_config(config)


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


# -------------------------------------------------------------------
# Config persistence helpers
# -------------------------------------------------------------------


def _config_dir(config_name: str) -> Path:
    """Resolve the directory for a named config.

    Args:
        config_name: Alphanumeric config identifier.

    Returns:
        Absolute ``Path`` to the config directory.
    """
    settings = get_settings()
    return Path(settings.DSPY_CONFIG_DIR).resolve() / config_name


def _config_to_optimized(
    prompt_description: str,
    examples: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> OptimizedConfig:
    """Build an ``OptimizedConfig`` from plain dicts.

    Args:
        prompt_description: The optimized prompt.
        examples: List of example dicts with ``text``
            and ``extractions``.
        metadata: Optional metadata dict.

    Returns:
        An ``OptimizedConfig`` instance.
    """
    example_data = _build_example_data(examples)
    return OptimizedConfig(
        prompt_description=prompt_description,
        examples=example_data,
        metadata=metadata or {},
    )


def _serialize_config(config: OptimizedConfig) -> dict[str, Any]:
    """Serialize an ``OptimizedConfig`` to a plain dict.

    Args:
        config: The config to serialize.

    Returns:
        Dict with ``prompt_description``, ``examples``,
        and ``metadata``.
    """
    return {
        "prompt_description": config.prompt_description,
        "examples": [
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
        ],
        "metadata": config.metadata,
    }


def save_config(
    config_name: str,
    prompt_description: str,
    examples: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an optimized config to disk.

    Args:
        config_name: Identifier for the config.
        prompt_description: The optimized prompt text.
        examples: Few-shot examples.
        metadata: Optional optimization metadata.

    Returns:
        Dict with ``config_name``, ``path``, and ``message``.
    """
    config = _config_to_optimized(prompt_description, examples, metadata)
    directory = _config_dir(config_name)
    directory.mkdir(parents=True, exist_ok=True)

    config.save(str(directory))
    logger.info("Saved DSPy config '%s' to %s", config_name, directory)

    return {
        "config_name": config_name,
        "path": str(directory),
        "message": "Config saved successfully.",
    }


async def async_save_config(
    config_name: str,
    prompt_description: str,
    examples: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save config asynchronously via thread pool."""
    return await asyncio.to_thread(
        save_config,
        config_name,
        prompt_description,
        examples,
        metadata,
    )


def load_config(config_name: str) -> dict[str, Any]:
    """Load a previously saved optimized config from disk.

    Args:
        config_name: Identifier of the config to load.

    Returns:
        Dict with ``config_name``, ``prompt_description``,
        ``examples``, and ``metadata``.

    Raises:
        FileNotFoundError: If the config directory does not exist.
    """
    directory = _config_dir(config_name)
    if not directory.exists():
        raise FileNotFoundError(
            f"Config '{config_name}' not found at {directory}"
        )

    config = OptimizedConfig.load(str(directory))
    logger.info("Loaded DSPy config '%s' from %s", config_name, directory)

    serialized = _serialize_config(config)
    return {
        "config_name": config_name,
        **serialized,
    }


async def async_load_config(config_name: str) -> dict[str, Any]:
    """Load config asynchronously via thread pool."""
    return await asyncio.to_thread(load_config, config_name)


def list_configs() -> list[str]:
    """List all saved config names.

    Returns:
        Sorted list of config directory names.
    """
    settings = get_settings()
    base = Path(settings.DSPY_CONFIG_DIR).resolve()
    if not base.exists():
        return []
    return sorted(
        d.name for d in base.iterdir() if d.is_dir()
    )


def run_evaluation(
    test_texts: list[str],
    expected_results: list[list[dict[str, str]]],
    *,
    config_name: str | None = None,
    prompt_description: str | None = None,
    examples: list[dict[str, Any]] | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate an optimized config against test data.

    Either ``config_name`` or ``prompt_description`` +
    ``examples`` must be provided.

    Args:
        test_texts: Test document texts.
        expected_results: Expected extractions per document.
        config_name: Name of saved config to evaluate.
        prompt_description: Inline prompt (alternative to
            ``config_name``).
        examples: Inline examples (used with
            ``prompt_description``).
        model_id: LLM for evaluation.

    Returns:
        Dict with precision, recall, f1, num_documents,
        and per_document metrics.
    """
    settings = get_settings()
    model_id = model_id or settings.DSPY_MODEL_ID
    api_key = resolve_api_key(model_id)

    # Resolve config
    if config_name:
        loaded = load_config(config_name)
        config = _config_to_optimized(
            loaded["prompt_description"],
            loaded["examples"],
            loaded.get("metadata"),
        )
    elif prompt_description and examples:
        config = _config_to_optimized(
            prompt_description, examples
        )
    else:
        raise ValueError(
            "Provide either config_name or "
            "prompt_description + examples."
        )

    expected = _build_expected_results(expected_results)

    # Build a simple extraction function for evaluate()
    optimizer = DSPyOptimizer(model_id=model_id, api_key=api_key)

    def _extract_fn(text: str) -> list[Extraction]:
        """Extract using the optimized config."""
        return optimizer.extract(
            text=text,
            prompt_description=config.prompt_description,
            examples=config.examples,
        )

    logger.info(
        "Running DSPy evaluation (model=%s, docs=%d)",
        model_id,
        len(test_texts),
    )

    metrics = config.evaluate(
        test_texts=test_texts,
        expected_results=expected,
        extract_fn=_extract_fn,
        model_id=model_id,
    )

    return metrics


async def async_run_evaluation(
    test_texts: list[str],
    expected_results: list[list[dict[str, str]]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Run evaluation asynchronously via thread pool."""
    return await asyncio.to_thread(
        run_evaluation,
        test_texts,
        expected_results,
        **kwargs,
    )
