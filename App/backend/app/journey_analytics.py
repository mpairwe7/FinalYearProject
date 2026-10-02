"""Per-journey funnel for the staff analytics dashboard.

Turns the grouped counts from ``database.get_journey_funnel`` into one row per
guided journey: how many started, finished, were cancelled or abandoned, where
the unfinished ones stopped, and how taxpayers rated the replies at each step.
The CX team reads the ``stopped`` column as the drop-off to fix first, and the
``not_helpful`` column as the reply to reword first.

Pure: the workflow definitions come in as an argument, so the logic is tested
without a database or the registry singleton.

One approximation, stated where it is shown: a session records the index it is
parked at, not the step it is waiting on after conditional steps are skipped,
so ``stopped`` is attributed to the step at that index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .workflows.loader import default_step_title

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .workflows.loader import WorkflowDefinition


def _empty_row(workflow_id: str, wf: WorkflowDefinition | None) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "name": wf.name if wf else workflow_id,
        "started": 0,
        "completed": 0,
        "cancelled": 0,
        "abandoned": 0,
        "in_progress": 0,
        "completion_pct": 0.0,
        "steps": [
            {
                "step_id": step.id,
                "title": step.title or default_step_title(step.id),
                "stopped": 0,
                "helpful": 0,
                "not_helpful": 0,
            }
            for step in (wf.steps if wf else [])
        ],
    }


def build_journey_funnel(
    raw: Mapping[str, Any],
    workflows: Iterable[WorkflowDefinition],
    *,
    days: int,
    abandon_after_hours: int,
) -> dict[str, Any]:
    """The per-journey view of *raw*, shaped as ``JourneyFunnelResponse``.

    Every journey a taxpayer can start by name (it has trigger phrases) is
    listed even at zero, because a journey nobody reaches is itself a finding;
    calculators, which only the calculator router starts, appear once used.
    """
    by_id = {wf.id: wf for wf in workflows}
    rows: dict[str, dict[str, Any]] = {
        wf.id: _empty_row(wf.id, wf) for wf in by_id.values() if wf.trigger_phrases
    }

    def row(workflow_id: str) -> dict[str, Any]:
        if workflow_id not in rows:
            rows[workflow_id] = _empty_row(workflow_id, by_id.get(workflow_id))
        return rows[workflow_id]

    for item in raw.get("sessions", []):
        entry = row(str(item.get("workflow_id") or ""))
        count = int(item.get("n") or 0)
        status = str(item.get("status") or "")
        stale = bool(int(item.get("stale") or 0))
        entry["started"] += count
        if status == "completed":
            entry["completed"] += count
        elif status == "cancelled":
            entry["cancelled"] += count
        elif stale:
            entry["abandoned"] += count
        else:
            entry["in_progress"] += count
        if status == "cancelled" or (status == "active" and stale):
            index = int(item.get("step_idx") or 0)
            if 0 <= index < len(entry["steps"]):
                entry["steps"][index]["stopped"] += count

    for item in raw.get("feedback", []):
        entry = row(str(item.get("workflow_id") or ""))
        step = next((s for s in entry["steps"] if s["step_id"] == item.get("step_id")), None)
        if step is None:
            continue
        step["helpful" if item.get("rating") == "up" else "not_helpful"] += int(item.get("n") or 0)

    journeys = list(rows.values())
    for entry in journeys:
        if entry["started"]:
            entry["completion_pct"] = round(100.0 * entry["completed"] / entry["started"], 1)
    journeys.sort(key=lambda e: (-e["started"], e["workflow_id"]))
    return {"period_days": days, "abandon_after_hours": abandon_after_hours, "journeys": journeys}
