"""
LLM provider resolution helpers.

Centralises API-key selection and provider detection so that
the main extraction orchestrator stays focused on business
logic.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ── Provider detection helpers ──────────────────────────────

# Each tuple: (match substrings in lower-cased model ID, settings attribute)
_PROVIDER_KEY_MAP: list[tuple[tuple[str, ...], str]] = [
    (("gpt", "openai", "o1-", "o3-", "o4-"), "OPENAI_API_KEY"),
    (("claude", "anthropic"), "ANTHROPIC_API_KEY"),
    (("mistral", "mixtral", "codestral", "pixtral"), "MISTRAL_API_KEY"),
    (("gemini", "gemma"), "GEMINI_API_KEY"),
]

# Patterns that identify an OpenAI model (for fence_output, etc.)
_OPENAI_PATTERNS: tuple[str, ...] = ("gpt", "openai", "o1-", "o3-", "o4-")

# Patterns that identify an Anthropic model
_ANTHROPIC_PATTERNS: tuple[str, ...] = ("claude", "anthropic")

# Patterns that identify a Mistral model
_MISTRAL_PATTERNS: tuple[str, ...] = ("mistral", "mixtral", "codestral", "pixtral")

# Patterns that identify a Gemini/Google model
_GEMINI_PATTERNS: tuple[str, ...] = ("gemini", "gemma")


def resolve_api_key(provider: str) -> str | None:
    """Pick the correct API key for *provider* from settings.

    Resolution order:
    1. Match against known provider patterns (OpenAI, Anthropic,
       Mistral, Gemini) and return the corresponding key.
    2. Fall back to ``LANGCORE_API_KEY`` (generic / proxy key).
    3. Return ``None`` if nothing is configured.

    Args:
        provider: Model ID string (e.g. ``gpt-4o``,
            ``claude-3.5-sonnet``, ``mistral-large``).

    Returns:
        An API key string, or ``None`` if nothing is configured.
    """
    settings = get_settings()
    lower = provider.lower()

    for patterns, attr in _PROVIDER_KEY_MAP:
        if any(p in lower for p in patterns):
            key = getattr(settings, attr, "") or None
            if key:
                logger.debug(
                    "Resolved API key for %s via %s",
                    provider,
                    attr,
                )
                return key
            # Pattern matched but key is empty — fall through

    # Fallback: generic LangCore API key
    fallback = settings.LANGCORE_API_KEY or None
    if fallback:
        logger.debug(
            "Using fallback LANGCORE_API_KEY for %s",
            provider,
        )
    return fallback


def is_openai_model(provider: str) -> bool:
    """Return ``True`` if *provider* is an OpenAI model.

    Args:
        provider: Model ID string.

    Returns:
        Boolean indicating whether OpenAI-specific flags apply.
    """
    lower = provider.lower()
    return any(p in lower for p in _OPENAI_PATTERNS)


def is_anthropic_model(provider: str) -> bool:
    """Return ``True`` if *provider* is an Anthropic model.

    Args:
        provider: Model ID string.

    Returns:
        Boolean indicating whether Anthropic-specific flags apply.
    """
    lower = provider.lower()
    return any(p in lower for p in _ANTHROPIC_PATTERNS)


def is_mistral_model(provider: str) -> bool:
    """Return ``True`` if *provider* is a Mistral model.

    Args:
        provider: Model ID string.

    Returns:
        Boolean indicating whether Mistral-specific flags apply.
    """
    lower = provider.lower()
    return any(p in lower for p in _MISTRAL_PATTERNS)


def is_gemini_model(provider: str) -> bool:
    """Return ``True`` if *provider* is a Google Gemini model.

    Args:
        provider: Model ID string.

    Returns:
        Boolean indicating whether Gemini-specific flags apply.
    """
    lower = provider.lower()
    return any(p in lower for p in _GEMINI_PATTERNS)
