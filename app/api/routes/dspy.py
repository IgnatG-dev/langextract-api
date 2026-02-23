"""DSPy prompt optimization route."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.schemas.plugins import (
    DSPyOptimizationRequest,
    DSPyOptimizationResponse,
)
from app.services.dspy_optimizer import async_run_optimization

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
    settings = get_settings()

    if not settings.DSPY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "DSPy optimization is disabled. " "Set DSPY_ENABLED=true to enable."
            ),
        )

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
