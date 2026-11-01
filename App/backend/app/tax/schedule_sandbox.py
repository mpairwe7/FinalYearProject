"""Capability-restricted deterministic Decimal AST & multi-period schedule sandbox (CS-4).

Implements a zero-I/O, capability-restricted arithmetic and multi-period tax
schedule evaluator aligned with:
- **OWASP Top 10 for Agentic Applications 2026 (`ASI02` Tool Misuse & `ASI05` Unexpected Code Execution)**
- **Program-Aided Language Models (PAL / PoT)** & **Programmatic Tool Calling (CE-MCP, arXiv:2602.15945)**
- **ADR-0007 (Code-owned tax logic)**: zero ``eval()``/``exec()``, zero filesystem/network/env
  authority, bounded AST node count, and exact ``decimal.Decimal`` arithmetic.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MAX_AST_NODES = 256
MAX_EXPRESSION_CHARS = 2048
MAX_SCHEDULE_ROWS = 60
MAX_EXPONENT = 12
MAX_ABS_VALUE = Decimal("1e15")
_TWO_DP = Decimal("0.01")


class SandboxViolationError(ValueError):
    """Raised when an expression or schedule violates sandbox capability or resource bounds."""


def _to_decimal(val: Any) -> Decimal:
    if isinstance(val, bool):
        raise SandboxViolationError("Boolean values are not valid monetary/numeric operands.")
    if isinstance(val, Decimal):
        d = val
    elif isinstance(val, (int, float)):
        d = Decimal(str(val))
    elif isinstance(val, str):
        cleaned = val.replace(",", "").replace("_", "").strip()
        try:
            d = Decimal(cleaned)
        except InvalidOperation as err:
            raise SandboxViolationError(f"Invalid numeric literal: {val!r}") from err
    else:
        raise SandboxViolationError(f"Unsupported operand type: {type(val).__name__}")
    if not d.is_finite() or abs(d) > MAX_ABS_VALUE:
        raise SandboxViolationError("Numeric value exceeds safe sandbox bounds.")
    return d


def evaluate_decimal_expression(
    expression: str,
    bindings: Mapping[str, Any] | None = None,
) -> Decimal:
    """Safely evaluate a pure arithmetic expression using ``Decimal`` under strict AST bounds.

    Permits only numeric literals, bound variable names, ``+``, ``-``, ``*``,
    ``/``, ``//``, ``%``, ``**`` (bounded to ``MAX_EXPONENT``), and the pure
    functions ``sum``, ``min``, ``max``, ``abs``, and ``round``.
    All attribute access, subscripts, imports, lambdas, comprehensions, and
    arbitrary calls are rejected with :class:`SandboxViolationError`.
    """
    expr_str = (expression or "").strip()
    if not expr_str:
        raise SandboxViolationError("Empty expression.")
    if len(expr_str) > MAX_EXPRESSION_CHARS:
        raise SandboxViolationError(f"Expression exceeds {MAX_EXPRESSION_CHARS} characters.")
    try:
        tree = ast.parse(expr_str, mode="eval")
    except SyntaxError as err:
        raise SandboxViolationError(f"Invalid arithmetic syntax: {err.msg}") from err

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise SandboxViolationError(f"AST node count ({len(nodes)}) exceeds limit ({MAX_AST_NODES}).")

    env: dict[str, Decimal | tuple[Decimal, ...]] = {}
    for k, v in (bindings or {}).items():
        if not k or not str(k).isidentifier() or str(k).startswith("_"):
            raise SandboxViolationError(f"Disallowed variable identifier: {k!r}")
        if isinstance(v, (list, tuple)):
            if len(v) > MAX_SCHEDULE_ROWS:
                raise SandboxViolationError(f"Sequence binding {k!r} exceeds {MAX_SCHEDULE_ROWS} items.")
            env[str(k)] = tuple(_to_decimal(item) for item in v)
        else:
            env[str(k)] = _to_decimal(v)

    def _eval(node: ast.AST) -> Decimal | tuple[Decimal, ...]:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return _to_decimal(node.value)
        if isinstance(node, (ast.Tuple, ast.List)):
            if len(node.elts) > MAX_SCHEDULE_ROWS:
                raise SandboxViolationError(f"Sequence literal exceeds {MAX_SCHEDULE_ROWS} items.")
            return tuple(_eval_scalar(elt) for elt in node.elts)
        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id not in env:
                raise SandboxViolationError(f"Unknown or disallowed identifier: {node.id!r}")
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = _eval_scalar(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise SandboxViolationError(f"Disallowed unary operator: {type(node.op).__name__}")
        if isinstance(node, ast.BinOp):
            left = _eval_scalar(node.left)
            right = _eval_scalar(node.right)
            if isinstance(node.op, ast.Add):
                res = left + right
            elif isinstance(node.op, ast.Sub):
                res = left - right
            elif isinstance(node.op, ast.Mult):
                res = left * right
            elif isinstance(node.op, ast.Div):
                if right == 0:
                    raise SandboxViolationError("Division by zero.")
                res = left / right
            elif isinstance(node.op, ast.FloorDiv):
                if right == 0:
                    raise SandboxViolationError("Division by zero.")
                res = left // right
            elif isinstance(node.op, ast.Mod):
                if right == 0:
                    raise SandboxViolationError("Modulo by zero.")
                res = left % right
            elif isinstance(node.op, ast.Pow):
                if abs(right) > MAX_EXPONENT or right != int(right):
                    raise SandboxViolationError(f"Exponent must be an integer within ±{MAX_EXPONENT}.")
                res = left ** int(right)
            else:
                raise SandboxViolationError(f"Disallowed binary operator: {type(node.op).__name__}")
            return _to_decimal(res)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise SandboxViolationError("Only direct allowlisted function calls without keyword args are permitted.")
            fn = node.func.id
            if fn == "abs":
                if len(node.args) != 1:
                    raise SandboxViolationError("abs() takes exactly 1 argument.")
                return abs(_eval_scalar(node.args[0]))
            if fn == "round":
                if not (1 <= len(node.args) <= 2):
                    raise SandboxViolationError("round() takes 1 or 2 arguments.")
                val = _eval_scalar(node.args[0])
                places = int(_eval_scalar(node.args[1])) if len(node.args) == 2 else 0
                if not (0 <= places <= 6):
                    raise SandboxViolationError("round() precision must be between 0 and 6 decimal places.")
                quant = Decimal(1) if places == 0 else Decimal(f"1e-{places}")
                return val.quantize(quant, rounding=ROUND_HALF_UP)
            if fn in ("sum", "min", "max"):
                if not node.args:
                    raise SandboxViolationError(f"{fn}() requires at least 1 argument.")
                items: list[Decimal] = []
                for arg in node.args:
                    evaluated = _eval(arg)
                    if isinstance(evaluated, tuple):
                        items.extend(evaluated)
                    else:
                        items.append(evaluated)
                if not items:
                    raise SandboxViolationError(f"{fn}() received an empty sequence.")
                if fn == "sum":
                    return _to_decimal(sum(items, Decimal(0)))
                if fn == "min":
                    return min(items)
                return max(items)
            raise SandboxViolationError(f"Disallowed function call: {fn!r}")
        raise SandboxViolationError(f"Disallowed AST construct: {type(node).__name__}")

    def _eval_scalar(node: ast.AST) -> Decimal:
        val = _eval(node)
        if isinstance(val, tuple):
            raise SandboxViolationError("Expected scalar operand, got sequence.")
        return val

    result = _eval_scalar(tree)
    return result


_NON_ADDITIVE_KEYS: frozenset[str] = frozenset(
    {
        "ok",
        "rate",
        "rate_percent",
        "effective_rate",
        "effective_rate_percent",
        "tax_rate",
        "marginal_rate",
        "days",
        "days_in_uganda",
        "year",
        "month",
        "period",
        "band_count",
        "units",
    }
)
_NON_ADDITIVE_SUFFIXES: tuple[str, ...] = (
    "_rate",
    "_percent",
    "_pct",
    "_ratio",
    "_count",
    "_days",
    "_id",
    "_year",
)


def evaluate_multi_period_tax_schedule(
    tool_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Execute a verified read-only calculator across multiple periods/items deterministically (CS-4).

    Enforces:
    - ``1 <= len(rows) <= MAX_SCHEDULE_ROWS`` (60 rows max, e.g., 5 years of monthly returns)
    - Target tool must exist in :class:`~app.tools.ToolRegistry` and be ``read_only=True``
    - Aggregates additive monetary output fields using exact ``Decimal`` arithmetic while
      excluding rate/percentage/count metadata fields from summation.
    """
    from ..tools import ToolRegistry

    if not rows:
        raise SandboxViolationError("Schedule rows cannot be empty.")
    if len(rows) > MAX_SCHEDULE_ROWS:
        raise SandboxViolationError(f"Schedule row count ({len(rows)}) exceeds limit ({MAX_SCHEDULE_ROWS}).")

    tool = ToolRegistry.get(tool_name)
    if tool is None:
        raise SandboxViolationError(f"Unknown calculator tool: {tool_name!r}")
    if not getattr(tool.schema, "read_only", False):
        raise SandboxViolationError(f"Tool {tool_name!r} is not read-only and cannot run in a schedule batch.")

    row_results: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise SandboxViolationError(f"Row {idx} must be a mapping of tool parameters.")
        res = ToolRegistry.call(tool_name, dict(row))
        if not isinstance(res, dict) or not res.get("ok", True):
            err = res.get("error", "calculation failed") if isinstance(res, dict) else "invalid output"
            return {
                "ok": False,
                "tool": tool_name,
                "failed_row_index": idx,
                "error": str(err),
                "completed_rows": len(row_results),
            }
        row_results.append(res)
        for key, value in res.items():
            key_lower = str(key).lower()
            if key_lower in _NON_ADDITIVE_KEYS or key_lower.endswith(_NON_ADDITIVE_SUFFIXES):
                continue
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                totals[key] = totals.get(key, Decimal(0)) + _to_decimal(value)

    formatted_totals = {
        k: float(v.quantize(_TWO_DP, rounding=ROUND_HALF_UP)) for k, v in sorted(totals.items())
    }
    return {
        "ok": True,
        "tool": tool_name,
        "row_count": len(row_results),
        "totals": formatted_totals,
        "rows": row_results,
    }
