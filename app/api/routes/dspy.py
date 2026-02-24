"""DSPy prompt optimization, persistence, and evaluation routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.schemas.plugins import (
    DSPyEvaluateRequest,
    DSPyEvaluateResponse,
    DSPyListResponse,
    DSPyLoadResponse,
    DSPyOptimizationRequest,
    DSPyOptimizationResponse,
    DSPySaveRequest,
    DSPySaveResponse,
)
from app.services.dspy_optimizer import (
    async_load_config,
    async_run_evaluation,
    async_run_optimization,
    async_save_config,
    list_configs,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dspy"])


@router.post(
    "/dspy/optimize",
    response_model=DSPyOptimizationResponse,
    summary="Optimize extraction prompts with DSPy",
    description=(
        "Run DSPy prompt optimization using MIPROv2 or GEPA "
        "optimizers. Accepts training documents with expected "
        "extractions and returns an optimized prompt description "
        "and curated few-shot examples. The optimized config can "
        "be passed directly to the extraction endpoint's "
        "``extraction_config`` for improved accuracy."
    ),
)
async def optimize_prompt(
    request: DSPyOptimizationRequest,
) -> DSPyOptimizationResponse:
    """Run DSPy prompt optimization.

    This endpoint is compute-intensive and makes many LLM
    calls internally.  Expect response times of 30s-5min
    depending on training set size and optimizer strategy.
    """
    _check_dspy_enabled()

    if len(request.train_texts) != len(request.expected_results):
        raise HTTPException(
            status_code=400,
            detail=(
                f"train_texts ({len(request.train_texts)}) and "
                f"expected_results ({len(request.expected_results)}) "
                "must have the same length."
            ),
        )

    try:
        result = await async_run_optimization(
            prompt_description=request.prompt_description,
            examples=request.examples,
            train_texts=request.train_texts,
            expected_results=request.expected_results,
            model_id=request.model_id,
            optimizer=request.optimizer,
            num_candidates=request.num_candidates,
            max_bootstrapped_demos=request.max_bootstrapped_demos,
            max_labeled_demos=request.max_labeled_demos,
            num_threads=request.num_threads,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("DSPy optimization failed")
        raise HTTPException(
            status_code=500,
            detail=f"Optimization failed: {exc}",
        ) from exc

    return DSPyOptimizationResponse(**result)


# -------------------------------------------------------------------
# Config persistence endpoints
# -------------------------------------------------------------------


def _check_dspy_enabled() -> None:
    """Raise 503 if DSPy is disabled."""
    settings = get_settings()
    if not settings.DSPY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "DSPy is disabled. Set DSPY_ENABLED=true to enable."
            ),
        )


@router.post(
    "/dspy/configs/save",
    response_model=DSPySaveResponse,
    summary="Save an optimized DSPy config",
    description=(
        "Persist an optimized prompt description and curated "
        "few-shot examples to disk under the configured "
        "``DSPY_CONFIG_DIR``. The saved config can later be loaded "
        "for extraction or evaluation without re-running "
        "optimization."
    ),
)
async def save_config(request: DSPySaveRequest) -> DSPySaveResponse:
    """Save an optimized DSPy config to disk."""
    _check_dspy_enabled()

    try:
        result = await async_save_config(
            config_name=request.config_name,
            prompt_description=request.prompt_description,
            examples=request.examples,
            metadata=request.metadata,
        )
    except Exception as exc:
        logger.exception("Failed to save DSPy config '%s'", request.config_name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save config: {exc}",
        ) from exc

    return DSPySaveResponse(**result)


@router.get(
    "/dspy/configs",
    response_model=DSPyListResponse,
    summary="List saved DSPy configs",
    description=(
        "Return the names of all saved optimized configs "
        "available under ``DSPY_CONFIG_DIR``."
    ),
)
async def list_saved_configs() -> DSPyListResponse:
    """List all saved DSPy config names."""
    _check_dspy_enabled()
    return DSPyListResponse(configs=list_configs())


@router.get(
    "/dspy/configs/{config_name}",
    response_model=DSPyLoadResponse,
    summary="Load a saved DSPy config",
    description=(
        "Load a previously saved optimized config by name. "
        "Returns the prompt description, examples, and any "
        "stored metadata."
    ),
)
async def load_config(config_name: str) -> DSPyLoadResponse:
    """Load a saved DSPy config from disk."""
    _check_dspy_enabled()

    try:
        result = await async_load_config(config_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to load DSPy config '%s'", config_name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {exc}",
        ) from exc

    return DSPyLoadResponse(**result)


# -------------------------------------------------------------------
# Evaluation endpoint
# -------------------------------------------------------------------


@router.post(
    "/dspy/evaluate",
    response_model=DSPyEvaluateResponse,
    summary="Evaluate an optimized DSPy config",
    description=(
        "Evaluate an optimized config against test documents "
        "with expected extractions. Returns precision, recall, "
        "F1 score, and per-document metrics. Supply either a "
        "``config_name`` (previously saved) or inline "
        "``prompt_description`` + ``examples``."
    ),
)
async def evaluate_config(
    request: DSPyEvaluateRequest,
) -> DSPyEvaluateResponse:
    """Evaluate a DSPy config against test data."""
    _check_dspy_enabled()

    if len(request.test_texts) != len(request.expected_results):
        raise HTTPException(
            status_code=400,
            detail=(
                f"test_texts ({len(request.test_texts)}) and "
                f"expected_results ({len(request.expected_results)}) "
                "must have the same length."
            ),
        )

    # Validate that exactly one source is provided
    has_config = request.config_name is not None
    has_inline = (
        request.prompt_description is not None
        and request.examples is not None
    )
    if not has_config and not has_inline:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide either config_name or both "
                "prompt_description and examples."
            ),
        )

    try:
        result = await async_run_evaluation(
            test_texts=request.test_texts,
            expected_results=request.expected_results,
            config_name=request.config_name,
            prompt_description=request.prompt_description,
            examples=request.examples,
            model_id=request.model_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("DSPy evaluation failed")
        raise HTTPException(
            status_code=500,
            detail=f"Evaluation failed: {exc}",
        ) from exc

    return DSPyEvaluateResponse(**result)
