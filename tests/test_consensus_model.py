"""Tests for ConsensusLanguageModel.

Covers both sync ``infer()`` and async ``async_infer()`` paths,
including error isolation, majority voting, and edge cases.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from typing import Any
from unittest import mock

import pytest

from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput

from app.services.consensus_model import (
    ConsensusLanguageModel,
    _jaccard_similarity,
    _select_consensus_output,
)


# ── Helpers ─────────────────────────────────────────────────


class _StubModel(BaseLanguageModel):
    """Deterministic stub that returns a fixed output per prompt."""

    def __init__(self, outputs: list[str]) -> None:
        super().__init__()
        self._outputs = outputs
        self.model_id = "stub"

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,
    ) -> Iterator[Sequence[ScoredOutput]]:
        for i, _ in enumerate(batch_prompts):
            text = self._outputs[i] if i < len(self._outputs) else ""
            yield [ScoredOutput(score=1.0, output=text)]

    async def async_infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,
    ) -> list[Sequence[ScoredOutput]]:
        results = []
        for i, _ in enumerate(batch_prompts):
            text = self._outputs[i] if i < len(self._outputs) else ""
            results.append([ScoredOutput(score=1.0, output=text)])
        return results


class _FailingModel(BaseLanguageModel):
    """Stub that always raises during inference."""

    def __init__(self) -> None:
        super().__init__()
        self.model_id = "failing"

    def infer(self, batch_prompts, **kwargs):
        raise RuntimeError("Provider unavailable")

    async def async_infer(self, batch_prompts, **kwargs):
        raise RuntimeError("Provider unavailable")


# ── Unit tests for similarity helpers ───────────────────────


class TestJaccardSimilarity:
    def test_identical(self):
        assert _jaccard_similarity("hello world", "hello world") == 1.0

    def test_empty_both(self):
        assert _jaccard_similarity("", "") == 1.0

    def test_disjoint(self):
        assert _jaccard_similarity("hello", "world") == 0.0

    def test_partial(self):
        sim = _jaccard_similarity("hello world foo", "hello world bar")
        # intersection={hello, world}, union={hello, world, foo, bar}
        assert sim == pytest.approx(2.0 / 4.0)


class TestSelectConsensusOutput:
    def test_single_output(self):
        chosen, score = _select_consensus_output(["hello"])
        assert chosen == "hello"
        assert score == 1.0

    def test_empty_outputs(self):
        chosen, score = _select_consensus_output([])
        assert chosen == ""
        assert score == 0.0

    def test_unanimous_agreement(self):
        chosen, score = _select_consensus_output(
            ["hello world", "hello world", "hello world"]
        )
        assert score == 1.0

    def test_majority_wins(self):
        chosen, score = _select_consensus_output(
            ["entity: foo bar", "entity: foo bar", "totally different"]
        )
        # First two are identical → agreement=2/3 for them
        assert "foo bar" in chosen
        assert score == pytest.approx(2.0 / 3)


# ── ConsensusLanguageModel ──────────────────────────────────


class TestConsensusLanguageModelInit:
    def test_minimum_two_models_required(self):
        with pytest.raises(ValueError, match="at least 2 models"):
            ConsensusLanguageModel(models=[_StubModel(["a"])])

    def test_accepts_two_models(self):
        m = ConsensusLanguageModel(models=[_StubModel(["a"]), _StubModel(["a"])])
        assert len(m._models) == 2


class TestConsensusSync:
    def test_unanimous_agreement(self):
        m1 = _StubModel(["entity: name John"])
        m2 = _StubModel(["entity: name John"])
        consensus = ConsensusLanguageModel(models=[m1, m2])

        results = list(consensus.infer(["Extract entities"]))
        assert len(results) == 1
        assert results[0][0].output == "entity: name John"
        assert results[0][0].score == pytest.approx(1.0)

    def test_majority_two_of_three(self):
        m1 = _StubModel(["entity: name John"])
        m2 = _StubModel(["entity: name John"])
        m3 = _StubModel(["completely different output"])
        consensus = ConsensusLanguageModel(models=[m1, m2, m3])

        results = list(consensus.infer(["Extract"]))
        assert len(results) == 1
        assert "John" in results[0][0].output
        assert results[0][0].score == pytest.approx(2.0 / 3)

    def test_one_model_fails_gracefully(self):
        m1 = _StubModel(["entity: name John"])
        m2 = _FailingModel()
        consensus = ConsensusLanguageModel(models=[m1, m2])

        results = list(consensus.infer(["Extract"]))
        assert len(results) == 1
        assert results[0][0].output == "entity: name John"

    def test_all_models_fail(self):
        consensus = ConsensusLanguageModel(models=[_FailingModel(), _FailingModel()])
        results = list(consensus.infer(["Extract"]))
        assert len(results) == 1
        assert results[0][0].score == 0.0

    def test_multi_prompt_batch(self):
        m1 = _StubModel(["out1", "out2"])
        m2 = _StubModel(["out1", "out2"])
        consensus = ConsensusLanguageModel(models=[m1, m2])

        results = list(consensus.infer(["p1", "p2"]))
        assert len(results) == 2
        assert results[0][0].output == "out1"
        assert results[1][0].output == "out2"


class TestConsensusAsync:
    def test_unanimous_agreement(self):
        m1 = _StubModel(["entity: name John"])
        m2 = _StubModel(["entity: name John"])
        consensus = ConsensusLanguageModel(models=[m1, m2])

        results = asyncio.run(consensus.async_infer(["Extract entities"]))
        assert len(results) == 1
        assert results[0][0].output == "entity: name John"
        assert results[0][0].score == pytest.approx(1.0)

    def test_one_model_fails_gracefully(self):
        m1 = _StubModel(["entity: name John"])
        m2 = _FailingModel()
        consensus = ConsensusLanguageModel(models=[m1, m2])

        results = asyncio.run(consensus.async_infer(["Extract"]))
        assert len(results) == 1
        assert results[0][0].output == "entity: name John"

    def test_all_models_fail(self):
        consensus = ConsensusLanguageModel(models=[_FailingModel(), _FailingModel()])
        results = asyncio.run(consensus.async_infer(["Extract"]))
        assert len(results) == 1
        assert results[0][0].score == 0.0
