"""
Model wrapper utilities for hybrid rules, guardrails, and audit.

Provides factory functions that decorate a ``BaseLanguageModel``
with ``langcore-hybrid``, ``langcore-guardrails``, and/or
``langcore-audit`` providers based on application settings and
per-request configuration.

Wrapping order (inside → out):
    base model → hybrid → guardrails → audit

Hybrid is innermost so deterministic rules are tried before any
LLM call.  Guardrails validates and retries LLM output.  Audit
is outermost so it logs the final post-validation output.
"""

from __future__ import annotations

import logging
from typing import Any

from langcore.core.base_model import BaseLanguageModel
from langcore_audit import (
    AuditLanguageModel,
    AuditSink,
    JsonFileSink,
    LoggingSink,
)
from langcore_guardrails import (
    ConfidenceThresholdValidator,
    ConsistencyValidator,
    FieldCompletenessValidator,
    GuardrailLanguageModel,
    GuardrailValidator,
    JsonSchemaValidator,
    OnFailAction,
    RegexValidator,
    SchemaValidator,
    ValidatorChain,
    ValidatorEntry,
)
from langcore_hybrid import (
    HybridLanguageModel,
    RegexRule,
    RuleConfig,
)

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


# ── Sink factory ────────────────────────────────────────────


def _build_audit_sinks(settings: Settings) -> list[AuditSink]:
    """Build audit sinks from application settings.

    Supports ``logging``, ``jsonfile``, and ``otel`` sink types.
    Falls back to ``LoggingSink`` on unknown values.

    Args:
        settings: The application ``Settings`` instance.

    Returns:
        A list containing a single ``AuditSink``.
    """
    sink_type = settings.AUDIT_SINK.lower()

    if sink_type == "jsonfile":
        logger.info(
            "Audit sink: JsonFileSink (path=%s)",
            settings.AUDIT_LOG_PATH,
        )
        return [JsonFileSink(path=settings.AUDIT_LOG_PATH)]

    if sink_type == "otel":
        try:
            from langcore_audit.sinks import OtelSpanSink

            logger.info("Audit sink: OtelSpanSink")
            return [OtelSpanSink()]
        except ImportError:
            logger.warning(
                "OpenTelemetry packages not installed — falling back to LoggingSink"
            )
            return [LoggingSink()]

    # Default: stdlib logging
    logger.info("Audit sink: LoggingSink")
    return [LoggingSink()]


# ── Validator factory ───────────────────────────────────────


def _build_validators(
    guardrails_config: dict[str, Any],
) -> list[GuardrailValidator]:
    """Build guardrail validators from per-request config.

    Supports the following validator types based on config keys:
    - ``json_schema``: ``JsonSchemaValidator`` for JSON Schema
      validation.
    - ``regex_pattern``: ``RegexValidator`` for regex matching.
    - ``confidence_threshold``: ``ConfidenceThresholdValidator``
      for minimum confidence scores.
    - ``required_fields``: ``FieldCompletenessValidator`` for
      mandatory field presence checks (builds a dynamic Pydantic
      schema).

    When no explicit validators are configured, a permissive
    ``JsonSchemaValidator`` (syntax-only) is returned so that
    at minimum the LLM output is valid JSON.

    Args:
        guardrails_config: Guardrails configuration dict from
            the request's ``extraction_config``.

    Returns:
        A list of ``GuardrailValidator`` instances.
    """
    validators: list[GuardrailValidator] = []

    # Resolve on_fail action from config (used per-validator)
    on_fail_str: str | None = guardrails_config.get("on_fail")
    if on_fail_str is not None:
        try:
            on_fail = OnFailAction(on_fail_str)
        except ValueError:
            logger.warning(
                "Unknown on_fail action '%s', defaulting to 'reask'",
                on_fail_str,
            )
            on_fail = OnFailAction.REASK
    else:
        on_fail = OnFailAction.REASK

    # ── JSON Schema validator ───────────────────────────────
    json_schema: dict[str, Any] | None = guardrails_config.get(
        "json_schema",
    )
    strict: bool = guardrails_config.get("json_schema_strict", True)
    if json_schema is not None:
        validators.append(
            JsonSchemaValidator(schema=json_schema, strict=strict),
        )

    # ── Regex validator ─────────────────────────────────────
    regex_pattern: str | None = guardrails_config.get("regex_pattern")
    if regex_pattern is not None:
        description = guardrails_config.get(
            "regex_description",
            "output format",
        )
        validators.append(
            RegexValidator(pattern=regex_pattern, description=description),
        )

    # ── Confidence threshold validator ──────────────────────
    confidence_threshold: float | None = guardrails_config.get(
        "confidence_threshold",
    )
    if confidence_threshold is not None:
        score_key = guardrails_config.get(
            "confidence_score_key",
            "confidence_score",
        )
        validators.append(
            ConfidenceThresholdValidator(
                min_confidence=confidence_threshold,
                score_key=score_key,
                on_fail=OnFailAction.FILTER,
            ),
        )

    # ── Field completeness validator ────────────────────────
    required_fields: list[str] | None = guardrails_config.get(
        "required_fields",
    )
    if required_fields:
        # Build a dynamic Pydantic model with the required fields
        # so FieldCompletenessValidator can check for their
        # presence in the LLM output.
        from pydantic import BaseModel as _BaseModel, create_model

        field_definitions = {name: (str, ...) for name in required_fields}
        dynamic_schema = create_model(
            "DynamicFieldSchema",
            **field_definitions,
        )
        validators.append(
            FieldCompletenessValidator(
                schema=dynamic_schema,
                on_fail=on_fail,
            ),
        )

    # ── Pydantic SchemaValidator ────────────────────────────
    pydantic_fields: dict[str, dict[str, str]] | None = (
        guardrails_config.get("pydantic_schema_fields")
    )
    if pydantic_fields:
        from pydantic import BaseModel as _BaseModel, Field, create_model

        _type_map: dict[str, type] = {
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
        }
        field_defs: dict[str, Any] = {}
        for name, meta in pydantic_fields.items():
            py_type = _type_map.get(meta.get("type", "str"), str)
            desc = meta.get("description", "")
            field_defs[name] = (
                py_type,
                Field(description=desc) if desc else ...,
            )
        pydantic_schema = create_model(
            "DynamicPydanticSchema",
            **field_defs,
        )
        strict = guardrails_config.get("pydantic_strict", False)
        validators.append(
            SchemaValidator(
                schema=pydantic_schema,
                on_fail=on_fail,
                strict=strict,
            ),
        )

    # ── Consistency validator ───────────────────────────────
    consistency_rules: list[dict[str, str]] | None = (
        guardrails_config.get("consistency_rules")
    )
    if consistency_rules:
        rule_fns = _build_consistency_rule_fns(consistency_rules)
        validators.append(
            ConsistencyValidator(
                rules=rule_fns,
                on_fail=on_fail,
            ),
        )

    # If no explicit validators, use syntax-only JSON check
    if not validators:
        validators.append(JsonSchemaValidator(schema=None, strict=False))

    # ── Wrap in ValidatorChain when multiple validators ─────
    if len(validators) > 1:
        entries = [ValidatorEntry(validator=v, on_fail=on_fail) for v in validators]
        chain = ValidatorChain(entries=entries)
        logger.info(
            "Built ValidatorChain with %d validators (on_fail=%s)",
            len(validators),
            on_fail.value,
        )
        return [chain]

    return validators


# ── Consistency rule builder ────────────────────────────────


_OPERATORS: dict[str, str] = {
    "lt": "<",
    "gt": ">",
    "le": "<=",
    "ge": ">=",
    "eq": "==",
    "ne": "!=",
}


def _build_consistency_rule_fns(
    rules: list[dict[str, str]],
) -> list:
    """Build callables for ``ConsistencyValidator`` from config.

    Each rule dict has ``field``, ``operator`` (lt/gt/eq/ne/le/ge),
    and ``other_field``.  Returns a list of callables
    ``(dict) -> str | None``.

    Args:
        rules: List of comparison rule dicts.

    Returns:
        List of callables suitable for ``ConsistencyValidator``.
    """
    fns: list = []

    for rule in rules:
        field_name = rule["field"]
        op = rule["operator"]
        other = rule["other_field"]
        op_sym = _OPERATORS.get(op, op)

        def _check(
            data: dict[str, Any],
            *,
            _f: str = field_name,
            _o: str = other,
            _op: str = op,
            _sym: str = op_sym,
        ) -> str | None:
            a = data.get(_f)
            b = data.get(_o)
            if a is None or b is None:
                return None  # missing fields — skip
            try:
                if _op == "lt" and not (a < b):
                    return f"{_f} ({a}) must be {_sym} {_o} ({b})"
                if _op == "gt" and not (a > b):
                    return f"{_f} ({a}) must be {_sym} {_o} ({b})"
                if _op == "le" and not (a <= b):
                    return f"{_f} ({a}) must be {_sym} {_o} ({b})"
                if _op == "ge" and not (a >= b):
                    return f"{_f} ({a}) must be {_sym} {_o} ({b})"
                if _op == "eq" and not (a == b):
                    return f"{_f} ({a}) must be {_sym} {_o} ({b})"
                if _op == "ne" and not (a != b):
                    return f"{_f} ({a}) must be {_sym} {_o} ({b})"
            except TypeError:
                return f"Cannot compare {_f} and {_o}: incompatible types"
            return None

        fns.append(_check)

    return fns


# ── Hybrid rule builder ────────────────────────────────────


def _build_hybrid_rules(
    rule_dicts: list[dict[str, Any]],
) -> list[RegexRule]:
    """Build ``RegexRule`` instances from per-request config.

    Each dict should have at minimum ``pattern`` (regex string
    with named capture groups).  Optional keys: ``description``
    and ``confidence``.

    Args:
        rule_dicts: List of rule definition dicts.

    Returns:
        List of ``RegexRule`` instances.
    """
    rules: list[RegexRule] = []
    for rd in rule_dicts:
        pattern = rd.get("pattern")
        if not pattern:
            logger.warning("Skipping hybrid rule with no pattern")
            continue
        rules.append(
            RegexRule(
                pattern=pattern,
                description=rd.get("description", "regex rule"),
                confidence=float(rd.get("confidence", 1.0)),
            ),
        )
    return rules


# ── Public wrapping API ─────────────────────────────────────


def wrap_with_hybrid(
    model: BaseLanguageModel,
    model_id: str,
    hybrid_rules: list[dict[str, Any]] | None,
) -> BaseLanguageModel:
    """Wrap a model with the hybrid rule-based provider.

    When rules are provided and ``HYBRID_ENABLED`` is ``True``,
    deterministic regex rules are tried before falling back to
    the LLM.  Matching prompts skip the LLM entirely for
    zero-latency deterministic extraction.

    Args:
        model: The base ``BaseLanguageModel`` to wrap.
        model_id: The model identifier string.
        hybrid_rules: Per-request rule definitions (list of dicts
            with ``pattern``, ``description``, ``confidence``).

    Returns:
        A ``HybridLanguageModel`` wrapping the base model, or
        the original model if hybrid is disabled or no rules
        are provided.
    """
    settings = get_settings()

    if not settings.HYBRID_ENABLED:
        return model
    if not hybrid_rules:
        return model

    rules = _build_hybrid_rules(hybrid_rules)
    if not rules:
        return model

    rule_config = RuleConfig(
        rules=rules,
        fallback_on_low_confidence=True,
        min_confidence=settings.HYBRID_MIN_CONFIDENCE,
    )

    wrapped = HybridLanguageModel(
        model_id=f"hybrid/{model_id}",
        inner=model,
        rule_config=rule_config,
    )

    logger.info(
        "Wrapped model %s with hybrid rules (%d rules, min_confidence=%.2f)",
        model_id,
        len(rules),
        settings.HYBRID_MIN_CONFIDENCE,
    )
    return wrapped


def wrap_with_guardrails(
    model: BaseLanguageModel,
    model_id: str,
    guardrails_config: dict[str, Any],
) -> BaseLanguageModel:
    """Wrap a model with the guardrails provider.

    Args:
        model: The base ``BaseLanguageModel`` to wrap.
        model_id: The model identifier string.
        guardrails_config: Per-request guardrails configuration
            (from ``ExtractionConfig.guardrails``).

    Returns:
        A ``GuardrailLanguageModel`` wrapping the base model,
        or the original model if guardrails should not be
        applied.
    """
    settings = get_settings()

    # Resolve enabled flag: per-request > global setting
    enabled = guardrails_config.get("enabled")
    if enabled is None:
        enabled = settings.GUARDRAILS_ENABLED
    if not enabled:
        return model

    validators = _build_validators(guardrails_config)

    max_retries = guardrails_config.get(
        "max_retries",
        settings.GUARDRAILS_MAX_RETRIES,
    )
    include_output = guardrails_config.get(
        "include_output_in_correction",
        settings.GUARDRAILS_INCLUDE_OUTPUT_IN_CORRECTION,
    )
    max_concurrency = settings.GUARDRAILS_MAX_CONCURRENCY
    max_prompt_len = (
        guardrails_config.get(
            "max_correction_prompt_length",
        )
        or settings.GUARDRAILS_MAX_CORRECTION_PROMPT_LENGTH
    )
    max_output_len = (
        guardrails_config.get(
            "max_correction_output_length",
        )
        or settings.GUARDRAILS_MAX_CORRECTION_OUTPUT_LENGTH
    )

    wrapped = GuardrailLanguageModel(
        model_id=f"guardrails/{model_id}",
        inner=model,
        validators=validators,
        max_retries=max_retries,
        max_concurrency=max_concurrency,
        max_correction_prompt_length=max_prompt_len,
        max_correction_output_length=max_output_len,
        include_output_in_correction=include_output,
    )

    validator_names = [type(v).__name__ for v in validators]
    logger.info(
        "Wrapped model %s with guardrails (validators=%s, max_retries=%d)",
        model_id,
        validator_names,
        max_retries,
    )
    return wrapped


def wrap_with_audit(
    model: BaseLanguageModel,
    model_id: str,
    audit_config: dict[str, Any] | None = None,
) -> BaseLanguageModel:
    """Wrap a model with the audit logging provider.

    Args:
        model: The ``BaseLanguageModel`` to wrap (may already
            be wrapped with guardrails).
        model_id: The model identifier string.
        audit_config: Optional per-request audit overrides
            (from ``ExtractionConfig.audit``).

    Returns:
        An ``AuditLanguageModel`` wrapping the model, or the
        original model if audit is disabled.
    """
    settings = get_settings()
    audit_config = audit_config or {}

    # Resolve enabled flag: per-request > global setting
    enabled = audit_config.get("enabled")
    if enabled is None:
        enabled = settings.AUDIT_ENABLED
    if not enabled:
        return model

    sinks = _build_audit_sinks(settings)

    sample_length = audit_config.get(
        "sample_length",
        settings.AUDIT_SAMPLE_LENGTH,
    )

    wrapped = AuditLanguageModel(
        model_id=f"audit/{model_id}",
        inner=model,
        sinks=sinks,
        sample_length=sample_length,
    )

    logger.info(
        "Wrapped model %s with audit logging (sink=%s, sample_length=%s)",
        model_id,
        settings.AUDIT_SINK,
        sample_length,
    )
    return wrapped


def apply_model_wrappers(
    model: BaseLanguageModel,
    model_id: str,
    extraction_config: dict[str, Any],
) -> BaseLanguageModel:
    """Apply hybrid, guardrails, and audit wrappers to a model.

    Wrapping order: base → hybrid → guardrails → audit.

    This is the single entry point called from the extraction
    orchestrator.  Configuration is resolved from both the
    per-request ``extraction_config`` and global application
    settings.

    Args:
        model: The base ``BaseLanguageModel`` instance.
        model_id: The model identifier string.
        extraction_config: The flat extraction configuration
            dict that may contain ``hybrid_rules``,
            ``guardrails``, and ``audit`` sub-dicts.

    Returns:
        The (possibly wrapped) model instance.
    """
    hybrid_rules = extraction_config.get("hybrid_rules")
    guardrails_config = extraction_config.get("guardrails") or {}
    audit_config = extraction_config.get("audit") or {}

    # Step 1: Hybrid rules (innermost wrapper)
    model = wrap_with_hybrid(model, model_id, hybrid_rules)

    # Step 2: Guardrails (validates LLM output)
    model = wrap_with_guardrails(model, model_id, guardrails_config)

    # Step 3: Audit (outermost wrapper)
    model = wrap_with_audit(model, model_id, audit_config)

    return model
