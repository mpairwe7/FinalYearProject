"""The ``tasks`` MCP namespace — work that outlives one request.

MCP ``2026-07-28`` is stateless: no ``initialize``, no session id,
identity in ``params._meta`` on every request, any request landing on
any replica. Work that takes longer than a request therefore cannot
hold a connection open, and cannot be remembered in a worker's memory
either — the poll that asks about it may reach a different process.

So a long-running call returns a **task id** and the caller polls it.
That is what these three tools are: an addressable, durable record of
state, not a job queue. What actually performs the work — the OCR
batch, the filing submission, the graph rebuild — advances the row
through :func:`app.database.update_task`.

Two properties are load-bearing:

**Idempotency is honoured on creation, not just on retry.** A retried
filing submission must observe the first attempt rather than start a
second. ``create_task`` returns the existing task with ``replayed:
true`` — the same contract the MCP client's replay cache already uses
for short calls.

**Terminal states are final.** A task that succeeded, failed or was
cancelled cannot be moved again, so a late worker cannot overwrite a
cancellation the taxpayer has already been told about.

Gated by ``FLAG_MCP_TASKS``. Closed, the tools return a structured
explanation rather than vanishing, so a caller holding them in a
whitelist gets a reason instead of an unknown-tool error.
"""

from __future__ import annotations

from typing import Any

from ..flags import flags
from . import Tool, ToolRegistry, ToolSchema

TASKS_NAMESPACE = "tasks"

#: Work the agent may start. A closed set: an open ``kind`` would let a
#: model invent work nothing knows how to run, and the row would sit at
#: ``pending`` for ever with no worker and no error.
TASK_KINDS = ("document_ocr", "filing_submission", "graph_rebuild", "bundle_export")

_DISABLED = {
    "ok": False,
    "error": "Long-running tasks are not enabled on this deployment.",
    "hint": "Set FLAG_MCP_TASKS=true to enable the tasks namespace.",
}


def _enabled() -> bool:
    return flags.is_enabled("mcp_tasks")


class TaskCreateTool(Tool):
    """Start a long-running job and return its id."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="task_create",
            description=(
                "Start work that takes longer than one reply — an OCR "
                "batch, a filing submission, a bundle export — and return "
                "a task id to poll with task_get. Use this INSTEAD of "
                "waiting: the connection cannot be held open. Always pass "
                "an idempotency_key so a retry observes the first attempt "
                "rather than starting a second."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(TASK_KINDS),
                        "description": "Which kind of work to start.",
                    },
                    "args": {
                        "type": "object",
                        "description": "Arguments for the work, kind-specific.",
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": (
                            "Caller-chosen key. A repeat with the same key "
                            "returns the original task with replayed: true."
                        ),
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
            risk="medium",
            namespace=TASKS_NAMESPACE,
            read_only=False,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

    def execute(
        self,
        kind: str,
        args: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not _enabled():
            return dict(_DISABLED)
        if kind not in TASK_KINDS:
            return {
                "ok": False,
                "error": f"unknown task kind {kind!r}",
                "known_kinds": list(TASK_KINDS),
            }
        from ..database import create_task

        task_data = create_task(kind, args or {}, idempotency_key=idempotency_key)
        action = "Replayed existing background task" if task_data.get("replayed") else "Created background task"
        explanation = f"{action} '{kind}' (ID: {task_data.get('task_id', '')}) with status '{task_data.get('status', 'pending')}'."
        return {"ok": True, **task_data, "explanation": explanation}


class TaskGetTool(Tool):
    """Poll a task."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="task_get",
            description=(
                "Check a task started with task_create. Returns its "
                "status (pending, running, succeeded, failed, cancelled), "
                "progress from 0 to 1, and the result once it has "
                "finished. Tell the taxpayer what the status means rather "
                "than reading the field name back to them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Id from task_create."}
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            risk="low",
            namespace=TASKS_NAMESPACE,
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, task_id: str) -> dict[str, Any]:
        if not _enabled():
            return dict(_DISABLED)
        from ..database import get_task

        task = get_task(task_id)
        if task is None:
            # Not found and not-yours are deliberately the same answer:
            # distinguishing them would confirm a task id exists on
            # another tenant.
            return {"ok": False, "error": "no such task"}
        progress_pct = int(float(task.get("progress", 0.0)) * 100)
        status = str(task.get("status", ""))
        explanation = f"Task '{task.get('kind', '')}' (ID: {task_id}) is currently {status} with {progress_pct}% progress."
        return {"ok": True, **task, "explanation": explanation}


class TaskCancelTool(Tool):
    """Cancel a task that has not finished."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="task_cancel",
            description=(
                "Cancel a task that is still pending or running. A task "
                "that has already finished cannot be cancelled and is "
                "returned unchanged — say so rather than implying it "
                "stopped."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Id from task_create."}
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            risk="medium",
            namespace=TASKS_NAMESPACE,
            read_only=False,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

    def execute(self, task_id: str) -> dict[str, Any]:
        if not _enabled():
            return dict(_DISABLED)
        from ..database import cancel_task, get_task

        before = get_task(task_id)
        if before is None:
            return {"ok": False, "error": "no such task"}
        after = cancel_task(task_id) or before
        is_cancelled = after["status"] == "cancelled" and before["status"] != after["status"]
        explanation = f"Task '{task_id}' was cancelled." if is_cancelled else f"Task '{task_id}' status is '{after['status']}'."
        return {
            "ok": True,
            **after,
            "cancelled": is_cancelled,
            "explanation": explanation,
        }


ToolRegistry.register(TaskCreateTool())
ToolRegistry.register(TaskGetTool())
ToolRegistry.register(TaskCancelTool())
