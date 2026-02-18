"""Task management routes (status polling & revocation)."""

from celery.result import AsyncResult
from fastapi import APIRouter

from app.schemas import (
    TaskRevokeResponse,
    TaskState,
    TaskStatusResponse,
)
from app.worker import celery_app

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task_status(task_id: str) -> TaskStatusResponse:
    """Poll the current status and result of a previously submitted task."""
    result = AsyncResult(task_id, app=celery_app)

    response = TaskStatusResponse(
        task_id=task_id,
        state=TaskState(result.state),
    )

    if result.state == "PENDING":
        response.progress = {
            "status": "Task is waiting to be processed",
        }
    elif result.state == "PROGRESS":
        response.progress = result.info
    elif result.state == "SUCCESS":
        response.result = result.result
    elif result.state == "FAILURE":
        response.error = str(result.info)

    return response


@router.delete("/tasks/{task_id}", response_model=TaskRevokeResponse)
def revoke_task(
    task_id: str,
    terminate: bool = False,
) -> TaskRevokeResponse:
    """Revoke a pending or running task.

    Set ``terminate=true`` to send SIGTERM to a running worker process.
    """
    celery_app.control.revoke(task_id, terminate=terminate)
    return TaskRevokeResponse(
        task_id=task_id,
        status="revoked",
        message=f"Task revocation signal sent (terminate={terminate})",
    )
