"""Tests for the default extraction configuration.

Validates:
- ``DEFAULT_PROMPT_DESCRIPTION`` is a non-empty string
- ``DEFAULT_EXAMPLES`` has the expected structure
- Examples can be converted via ``_build_examples()``
"""

from __future__ import annotations

from app.core.defaults import (
    DEFAULT_EXAMPLES,
    DEFAULT_PROMPT_DESCRIPTION,
)


class TestDefaultPromptDescription:
    """Tests for ``DEFAULT_PROMPT_DESCRIPTION``."""

    def test_is_non_empty_string(self):
        """The default prompt is a non-empty string."""
        assert isinstance(DEFAULT_PROMPT_DESCRIPTION, str)
        assert len(DEFAULT_PROMPT_DESCRIPTION) > 10

    def test_mentions_extraction(self):
        """The prompt should reference extraction."""
        lower = DEFAULT_PROMPT_DESCRIPTION.lower()
        assert "extract" in lower


class TestDefaultExamples:
    """Tests for ``DEFAULT_EXAMPLES``."""

    def test_is_non_empty_list(self):
        """At least one example is provided."""
        assert isinstance(DEFAULT_EXAMPLES, list)
        assert len(DEFAULT_EXAMPLES) >= 1

    def test_example_structure(self):
        """Each example has 'text' and 'extractions' keys."""
        for ex in DEFAULT_EXAMPLES:
            assert "text" in ex, "Example missing 'text' key"
            assert "extractions" in ex, "Example missing 'extractions' key"
            assert isinstance(ex["text"], str)
            assert isinstance(ex["extractions"], list)

    def test_extraction_structure(self):
        """Each extraction has the required keys."""
        for ex in DEFAULT_EXAMPLES:
            for ext in ex["extractions"]:
                assert "extraction_class" in ext
                assert "extraction_text" in ext
                assert isinstance(ext["extraction_class"], str)
                assert isinstance(ext["extraction_text"], str)

    def test_examples_have_entity_variety(self):
        """Default examples cover multiple entity classes."""
        classes = set()
        for ex in DEFAULT_EXAMPLES:
            for ext in ex["extractions"]:
                classes.add(ext["extraction_class"])
        # Should have at least party, date, and monetary_amount
        assert "party" in classes
        assert "date" in classes
        assert "monetary_amount" in classes

    def test_examples_are_buildable(self):
        """Default examples can be converted via _build_examples."""
        from app.services.extractor import _build_examples

        result = _build_examples(DEFAULT_EXAMPLES)
        assert len(result) == len(DEFAULT_EXAMPLES)
        # Each result should have extractions
        for built, raw in zip(
            result,
            DEFAULT_EXAMPLES,
            strict=True,
        ):
            assert len(built.extractions) == len(
                raw["extractions"],
            )
