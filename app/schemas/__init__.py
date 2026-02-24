"""
Pydantic models for API requests and responses.

All data contracts live here so that route handlers, workers,
and services can import lightweight schema objects without
circular dependencies.

For convenience every public model is re-exported from this
``__init__`` so that ``from app.schemas import ExtractionRequest``
keeps working.
"""

from app.schemas.enums import TaskState
from app.schemas.health import CeleryHealthResponse, HealthResponse
from app.schemas.plugins import (
    DSPyEvaluateRequest,
    DSPyEvaluateResponse,
    DSPyListResponse,
    DSPyLoadResponse,
    DSPyOptimizationRequest,
    DSPyOptimizationResponse,
    DSPySaveRequest,
    DSPySaveResponse,
    RAGQueryParseRequest,
    RAGQueryParseResponse,
)
from app.schemas.requests import (
    AuditConfig,
    BatchExtractionRequest,
    ExtractionConfig,
    ExtractionRequest,
    GuardrailsConfig,
    Provider,
)
from app.schemas.responses import (
    BatchTaskSubmitResponse,
    TaskRevokeResponse,
    TaskStatusResponse,
    TaskSubmitResponse,
)
from app.schemas.results import (
    ExtractedEntity,
    ExtractionMetadata,
    ExtractionResult,
)

__all__ = [
    "AuditConfig",
    "BatchExtractionRequest",
    "BatchTaskSubmitResponse",
    "CeleryHealthResponse",
    "DSPyEvaluateRequest",
    "DSPyEvaluateResponse",
    "DSPyListResponse",
    "DSPyLoadResponse",
    "DSPyOptimizationRequest",
    "DSPyOptimizationResponse",
    "DSPySaveRequest",
    "DSPySaveResponse",
    "ExtractedEntity",
    "ExtractionConfig",
    "ExtractionMetadata",
    "ExtractionRequest",
    "ExtractionResult",
    "GuardrailsConfig",
    "HealthResponse",
    "Provider",
    "RAGQueryParseRequest",
    "RAGQueryParseResponse",
    "TaskRevokeResponse",
    "TaskState",
    "TaskStatusResponse",
    "TaskSubmitResponse",
]
