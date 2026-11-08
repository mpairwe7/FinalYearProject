"""Staff-only semantic-layer tools (ADR-0033, phase 3).

``semantic_metric_catalogue`` lists certified metrics and
``semantic_metric_query`` runs one. Both are in the ``semantic_layer``
namespace, declared ``high`` risk with ``allowed_roles`` URA staff and admins,
so the dispatch policy refuses every other caller. Both are gated by
``FLAG_SEMANTIC_LAYER`` and do nothing until a database is connected.
"""

from __future__ import annotations

import logging
from typing import Any

from ..flags import flags
from . import Tool, ToolRegistry, ToolSchema

logger = logging.getLogger(__name__)

SEMANTIC_NAMESPACE = "semantic_layer"
STAFF_ROLES = ("ura_staff", "ura_admin")

_DISABLED = {
    "ok": False,
    "error": "The semantic layer is not enabled on this deployment.",
    "hint": "It is staff-only and needs FLAG_SEMANTIC_LAYER, Vault, a data-sharing agreement and a DPIA (ADR-0033).",
}
_NOT_CONNECTED = {"ok": False, "error": "No analytical database is connected to the semantic layer."}


def _layer() -> Any:
    if not flags.is_enabled("semantic_layer"):
        return dict(_DISABLED)
    from ..semantic_layer.adapter import get_layer

    layer = get_layer()
    return layer if layer is not None else dict(_NOT_CONNECTED)


def _schema(name: str, description: str, parameters: dict[str, Any], output: dict[str, Any]) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description,
        parameters=parameters,
        risk="high",
        namespace=SEMANTIC_NAMESPACE,
        allowed_roles=STAFF_ROLES,
        # Declared like every elevated tool; staff act under their own mandate, so the
        # role satisfies it (as with escalation), and no one else may call it at all.
        required_scopes=("analytics",),
        scope_exempt_roles=STAFF_ROLES,
        read_only=True,
        idempotent=True,
        open_world=False,
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"}, **output},
                       "required": ["ok"]},
    )


class SemanticMetricCatalogueTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return _schema(
            "semantic_metric_catalogue",
            "URA staff only: list the certified aggregate metrics the semantic layer can run, with their owners "
            "and parameters.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"metrics": {"type": "array", "items": {"type": "object"}}},
        )

    def execute(self) -> dict[str, Any]:
        layer = _layer()
        if isinstance(layer, dict):
            return layer
        return {"ok": True, "metrics": layer.catalogue()}


class SemanticMetricQueryTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return _schema(
            "semantic_metric_query",
            "URA staff only: run one certified aggregate metric by id with its parameters. Groups below the "
            "metric's minimum size are suppressed; no taxpayer-level rows are returned.",
            {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "A metric id from semantic_metric_catalogue."},
                    "parameters": {"type": "object", "description": "The metric's parameters."},
                },
                "required": ["metric"],
                "additionalProperties": False,
            },
            {"rows": {"type": "array", "items": {"type": "object"}}, "suppressed_rows": {"type": "integer"},
             "truncated": {"type": "boolean"}},
        )

    def execute(self, metric: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        layer = _layer()
        if isinstance(layer, dict):
            return layer
        from ..semantic_layer.catalogue import MetricError

        try:
            return layer.query(metric, parameters or {})
        except MetricError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.warning("semantic metric %s failed", metric, exc_info=True)
            return {"ok": False, "error": "The metric could not be run (time limit or database error)."}


ToolRegistry.register(SemanticMetricCatalogueTool())
ToolRegistry.register(SemanticMetricQueryTool())
