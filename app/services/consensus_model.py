"""
Consensus language model wrapper for cross-provider agreement.

Dispatches the same prompt to multiple LLM providers and
returns only the output that achieves majority agreement,
improving extraction determinism at the cost of N× API calls
(where N is the number of consensus providers).

This is an opt-in "high-confidence" mode — it should NOT be
the default extraction path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterator, Sequence
from typing import Any

from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput

logger = logging.getLogger(__name__)

# Minimum word-level Jaccard similarity to consider two outputs
# as "in agreement".  Tuned for structured YAML/JSON extraction
# output where minor formatting differences are expected.
_DEFAULT_SIMILARITY_THRESHOLD: float = 0.6

# Regex for normalising extraction output before comparison.
_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation noise.

    Args:
        text: Raw LLM output string.

    Returns:
        Normalised string suitable for word-level comparison.
    """
    return _WHITESPACE_RE.sub(" ", text.lower().strip())


def _word_set(text: str) -> set[str]:
    """Split normalised text into a bag-of-words set.

    Args:
        text: Normalised text.

    Returns:
        Set of unique word tokens.
    """
    return set(_normalise_text(text).split())


def _jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings.

    Uses bag-of-words tokenisation after normalisation.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Float in ``[0.0, 1.0]``.  Returns ``1.0`` when both
        strings are empty.
    """
    words_a = _word_set(a)
    words_b = _word_set(b)
    if not words_a and not words_b:
        return 1.0
    union = words_a | words_b
    if not union:
        return 1.0
    return len(words_a & words_b) / len(union)


def _select_consensus_output(
    outputs: list[str],
    threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> tuple[str, float]:
    """Select the output with highest cross-provider agreement.

    For each candidate output, compute how many other outputs
    exceed the similarity ``threshold``.  The candidate with the
    highest agreement count wins.  Ties are broken by position
    (first model wins).

    Args:
        outputs: List of raw LLM output strings, one per model.
        threshold: Minimum Jaccard similarity to count as
            "in agreement".

    Returns:
        A ``(chosen_output, agreement_score)`` tuple where
        ``agreement_score = agreement_count / len(outputs)``
        ranges from ``1/N`` (no agreement) to ``1.0``
        (unanimous).
    """
    if not outputs:
        return "", 0.0

    n = len(outputs)
    if n == 1:
        return outputs[0], 1.0

    best_idx = 0
    best_agreement = 0

    for i, candidate in enumerate(outputs):
        agreement = 1  # counts itself
        for j, other in enumerate(outputs):
            if i == j:
                continue
            if _jaccard_similarity(candidate, other) >= threshold:
                agreement += 1
        if agreement > best_agreement:
            best_agreement = agreement
            best_idx = i

    return outputs[best_idx], best_agreement / n


class ConsensusLanguageModel(BaseLanguageModel):
    """Language model wrapper that dispatches to multiple providers.

    For each inference prompt, all wrapped models are queried and
    the output with the highest cross-model agreement is returned.
    This improves extraction determinism when LLM outputs are
    non-deterministic.

    The ``score`` of each returned ``ScoredOutput`` reflects the
    consensus agreement ratio (``agreement_count / n_models``).

    Usage::

        model_a = manager.get_or_create_model("gpt-4o", ...)
        model_b = manager.get_or_create_model("claude-3-opus", ...)
        consensus = ConsensusLanguageModel(
            models=[model_a, model_b],
        )
        # Use like any other BaseLanguageModel:
        results = consensus.infer(["Extract entities from..."])

    Args:
        models: Two or more ``BaseLanguageModel`` instances to
            query in parallel.
        similarity_threshold: Minimum Jaccard similarity for
            two outputs to be considered "in agreement".

    Raises:
        ValueError: If fewer than two models are provided.
    """

    def __init__(
        self,
        models: list[BaseLanguageModel],
        *,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
        **kwargs: Any,
    ) -> None:
        """Initialise the consensus wrapper.

        Args:
            models: List of provider model instances.
            similarity_threshold: Jaccard threshold for agreement.
            **kwargs: Forwarded to ``BaseLanguageModel.__init__``.
        """
        super().__init__(**kwargs)
        if len(models) < 2:
            raise ValueError(
                "ConsensusLanguageModel requires at least 2 models, "
                f"got {len(models)}."
            )
        self._models = models
        self._threshold = similarity_threshold

    # ── Sync inference ──────────────────────────────────────

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,
    ) -> Iterator[Sequence[ScoredOutput]]:
        """Run inference on all models and return consensus output.

        Each prompt is sent to all wrapped models.  For each
        prompt position, the response with the highest
        cross-model agreement is selected.

        Args:
            batch_prompts: List of prompts to process.
            **kwargs: Additional inference parameters.

        Yields:
            Lists of ``ScoredOutput``, one per prompt.
        """
        # Collect results from all models.  Each entry is a list
        # of per-prompt results (list of list of Sequence[ScoredOutput]).
        all_model_results: list[list[Sequence[ScoredOutput]]] = []
        for model in self._models:
            try:
                all_model_results.append(list(model.infer(batch_prompts, **kwargs)))
            except Exception:
                logger.warning(
                    "Consensus model %s failed during infer; "
                    "skipping this provider.",
                    getattr(model, "model_id", "unknown"),
                    exc_info=True,
                )

        if not all_model_results:
            # All models failed — yield empty results.
            for _ in batch_prompts:
                yield [ScoredOutput(score=0.0, output="")]
            return

        n_prompts = len(batch_prompts)
        for prompt_idx in range(n_prompts):
            # Gather outputs for this prompt across models.
            candidate_outputs: list[str] = []
            for model_results in all_model_results:
                if prompt_idx < len(model_results):
                    scored_seq = model_results[prompt_idx]
                    if scored_seq:
                        candidate_outputs.append(scored_seq[0].output)

            chosen, agreement = _select_consensus_output(
                candidate_outputs,
                threshold=self._threshold,
            )
            yield [ScoredOutput(score=agreement, output=chosen)]

    # ── Async inference ─────────────────────────────────────

    async def async_infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,
    ) -> list[Sequence[ScoredOutput]]:
        """Async consensus inference with concurrent provider dispatch.

        All wrapped models are queried concurrently via
        ``asyncio.gather``, then per-prompt consensus selection
        is applied to the collected results.

        Args:
            batch_prompts: List of prompts to process.
            **kwargs: Additional inference parameters.

        Returns:
            List of ``ScoredOutput`` sequences, one per prompt.
        """

        async def _safe_infer(
            model: BaseLanguageModel,
        ) -> list[Sequence[ScoredOutput]] | None:
            """Call ``async_infer`` with error isolation."""
            try:
                return await model.async_infer(batch_prompts, **kwargs)
            except Exception:
                logger.warning(
                    "Consensus model %s failed during async_infer; "
                    "skipping this provider.",
                    getattr(model, "model_id", "unknown"),
                    exc_info=True,
                )
                return None

        raw_results = await asyncio.gather(*[_safe_infer(m) for m in self._models])
        all_model_results = [r for r in raw_results if r is not None]

        if not all_model_results:
            return [[ScoredOutput(score=0.0, output="")] for _ in batch_prompts]

        consensus_results: list[Sequence[ScoredOutput]] = []
        n_prompts = len(batch_prompts)

        for prompt_idx in range(n_prompts):
            candidate_outputs: list[str] = []
            for model_results in all_model_results:
                if prompt_idx < len(model_results):
                    scored_seq = model_results[prompt_idx]
                    if scored_seq:
                        candidate_outputs.append(scored_seq[0].output)

            chosen, agreement = _select_consensus_output(
                candidate_outputs,
                threshold=self._threshold,
            )
            consensus_results.append([ScoredOutput(score=agreement, output=chosen)])

        return consensus_results
