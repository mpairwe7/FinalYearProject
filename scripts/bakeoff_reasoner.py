"""Reasoner model bake-off harness (Phase B.4).

Evaluates a local vLLM OpenAI-compatible endpoint across:
1. Frozen dev scenario contexts (``evals/complex_scenarios/frozen_contexts_dev.jsonl``)
   in ``nothink`` (``max_tokens=1536``) or ``think`` (``max_tokens=4096``) mode,
   recording TTFT, total latency, ``finish_reason``, token usage, reasoning tokens,
   and deterministic accuracy score (with ``route_ok`` and ``sources_relevant``
   reweighted out per §4.B.4).
2. Tool-calling fidelity on 15 calculator prompts using the live ``ToolRegistry``
   schemas from the ``tax_calculator`` namespace.
3. 8-stream concurrent throughput on a long-form (~1,000-token) tax explanation.
4. Optional 12-question Luganda/Swahili sanity + ``mt.figures_survived`` check.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import json
import re
import statistics
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))
sys.path.insert(0, str(ROOT))

from evals.complex_scenarios.scoring import default_tool_runner, score_turn  # noqa: E402

FROZEN_DEV_PATH = ROOT / "evals" / "complex_scenarios" / "frozen_contexts_dev.jsonl"

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK_RE = re.compile(r"^.*?<think>.*", re.DOTALL | re.IGNORECASE)

# 15 tool-calling prompts covering the calculator tools in ToolRegistry
TOOL_FIDELITY_PROMPTS: tuple[dict[str, Any], ...] = (
    {
        "id": "tf_paye_res_22m",
        "prompt": "Calculate the monthly PAYE for a resident employee in Uganda earning a gross monthly salary of UGX 22,000,000.",
        "expected_tool": "calculate_paye",
        "expected_args": {"monthly_gross": 22_000_000, "residency": "resident"},
    },
    {
        "id": "tf_paye_nonres_5m",
        "prompt": "Calculate the monthly PAYE for a non-resident individual earning UGX 5,000,000 gross per month.",
        "expected_tool": "calculate_paye",
        "expected_args": {"monthly_gross": 5_000_000, "residency": "non_resident"},
    },
    {
        "id": "tf_paye_nssf_3m",
        "prompt": "Calculate monthly PAYE and take-home pay including NSSF for a resident earning UGX 3,000,000 per month.",
        "expected_tool": "calculate_paye",
        "expected_args": {"monthly_gross": 3_000_000, "include_nssf": True},
    },
    {
        "id": "tf_vat_add_40m",
        "prompt": "Calculate 18% VAT to add on a VAT-exclusive net amount of UGX 40,000,000.",
        "expected_tool": "calculate_vat",
        "expected_args": {"amount": 40_000_000, "direction": "add"},
    },
    {
        "id": "tf_vat_extract_59m",
        "prompt": "Extract the 18% VAT and net amount from a VAT-inclusive gross price of UGX 59,000,000.",
        "expected_tool": "calculate_vat",
        "expected_args": {"amount": 59_000_000, "direction": "extract"},
    },
    {
        "id": "tf_vat_reg_240m",
        "prompt": "Check whether a business with annual taxable turnover of UGX 240,000,000 must register for VAT.",
        "expected_tool": "check_vat_registration",
        "expected_args": {"annual_turnover": 240_000_000},
    },
    {
        "id": "tf_corp_120m",
        "prompt": "Calculate corporation income tax on an annual chargeable income of UGX 120,000,000.",
        "expected_tool": "calculate_corporation_tax",
        "expected_args": {"chargeable_income": 120_000_000},
    },
    {
        "id": "tf_cgt_500m_320m",
        "prompt": "Calculate corporate capital gains tax when an asset is sold for UGX 500,000,000 with a cost base of UGX 320,000,000.",
        "expected_tool": "calculate_capital_gains",
        "expected_args": {"sale_price": 500_000_000, "cost_base": 320_000_000},
    },
    {
        "id": "tf_rental_ind_90m",
        "prompt": "Calculate annual rental income tax for an individual landlord with annual gross rent of UGX 90,000,000.",
        "expected_tool": "calculate_rental_tax",
        "expected_args": {"annual_gross_rent": 90_000_000, "landlord_type": "individual"},
    },
    {
        "id": "tf_rental_co_200m_140m",
        "prompt": "Calculate annual rental income tax for a company landlord with annual gross rent of UGX 200,000,000 and allowable expenses of UGX 140,000,000.",
        "expected_tool": "calculate_rental_tax",
        "expected_args": {
            "annual_gross_rent": 200_000_000,
            "landlord_type": "company",
            "allowable_expenses": 140_000_000,
        },
    },
    {
        "id": "tf_wht_mgmt_30m",
        "prompt": "Calculate withholding tax on management fees of UGX 30,000,000.",
        "expected_tool": "calculate_withholding",
        "expected_args": {"payment_type": "management_fees", "amount": 30_000_000},
    },
    {
        "id": "tf_wht_services_45m",
        "prompt": "Calculate withholding tax on a domestic professional services payment of UGX 45,000,000.",
        "expected_tool": "calculate_withholding",
        "expected_args": {"payment_type": "services", "amount": 45_000_000},
    },
    {
        "id": "tf_customs_vehicle_30m",
        "prompt": "Calculate customs duty and landed cost for importing a 10-year-old used vehicle (over 8 years) with a CIF value of UGX 30,000,000.",
        "expected_tool": "calculate_customs_duty",
        "expected_args": {
            "cif_value": 30_000_000,
            "goods_category": "used_vehicle_over_8_years",
        },
    },
    {
        "id": "tf_excise_momo_4m",
        "prompt": "Calculate excise duty on a mobile money cash withdrawal of UGX 4,000,000.",
        "expected_tool": "calculate_excise_duty",
        "expected_args": {"excise_type": "mobile_money_withdrawal", "amount": 4_000_000},
    },
    {
        "id": "tf_excise_petrol_2000l",
        "prompt": "Calculate excise duty on 2,000 litres of petrol.",
        "expected_tool": "calculate_excise_duty",
        "expected_args": {"excise_type": "fuel_petrol", "amount": 2_000},
    },
)

TRILINGUAL_SANITY_PROMPTS: tuple[dict[str, str], ...] = (
    {
        "id": "lg_01",
        "lang": "lg",
        "source_en": "An individual landlord pays 12% rental income tax on annual gross rent above UGX 2,820,000, and monthly PAYE returns are due by the 15th of the following month.",
    },
    {
        "id": "lg_02",
        "lang": "lg",
        "source_en": "For a resident employee earning UGX 22,000,000 per month in FY2026-27, monthly PAYE is UGX 7,688,250 and the top marginal rate is 40% above UGX 10,000,000.",
    },
    {
        "id": "lg_03",
        "lang": "lg",
        "source_en": "The compulsory VAT registration threshold is UGX 300,000,000 per year and the standard VAT rate is 18%.",
    },
    {
        "id": "lg_04",
        "lang": "lg",
        "source_en": "A taxpayer who disagrees with an assessment may lodge a written objection within 45 days and must pay 30% of the disputed tax when appealing to the Tax Appeals Tribunal.",
    },
    {
        "id": "lg_05",
        "lang": "lg",
        "source_en": "Withholding tax is 6% on domestic goods and services, 15% on dividends and management fees, and 0.5% excise duty applies to mobile money withdrawals.",
    },
    {
        "id": "lg_06",
        "lang": "lg",
        "source_en": "Late filing of a tax return attracts a penal tax of UGX 200,000 or 2% of the tax payable per month, whichever is greater.",
    },
    {
        "id": "sw_01",
        "lang": "sw",
        "source_en": "An individual landlord pays 12% rental income tax on annual gross rent above UGX 2,820,000, and monthly PAYE returns are due by the 15th of the following month.",
    },
    {
        "id": "sw_02",
        "lang": "sw",
        "source_en": "For a resident employee earning UGX 22,000,000 per month in FY2026-27, monthly PAYE is UGX 7,688,250 and the top marginal rate is 40% above UGX 10,000,000.",
    },
    {
        "id": "sw_03",
        "lang": "sw",
        "source_en": "The compulsory VAT registration threshold is UGX 300,000,000 per year and the standard VAT rate is 18%.",
    },
    {
        "id": "sw_04",
        "lang": "sw",
        "source_en": "A taxpayer who disagrees with an assessment may lodge a written objection within 45 days and must pay 30% of the disputed tax when appealing to the Tax Appeals Tribunal.",
    },
    {
        "id": "sw_05",
        "lang": "sw",
        "source_en": "Withholding tax is 6% on domestic goods and services, 15% on dividends and management fees, and 0.5% excise duty applies to mobile money withdrawals.",
    },
    {
        "id": "sw_06",
        "lang": "sw",
        "source_en": "Late filing of a tax return attracts a penal tax of UGX 200,000 or 2% of the tax payable per month, whichever is greater.",
    },
)


def strip_thinking(text: str) -> tuple[str, str]:
    """Separate ``<think>...</think>`` blocks from the user-facing answer."""
    if not text:
        return "", ""
    thoughts: list[str] = []
    for m in re.finditer(r"<think>(.*?)</think>", text, flags=re.DOTALL | re.IGNORECASE):
        thoughts.append(m.group(1).strip())
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    lower = cleaned.lower()
    if "</think>" in lower and "<think>" not in lower:
        head, _, tail = re.split(r"(</think>)", cleaned, maxsplit=1, flags=re.IGNORECASE)
        thoughts.append(head.strip())
        cleaned = tail.strip()
    if "<think>" in cleaned.lower() and "</think>" not in cleaned.lower():
        parts = re.split(r"<think>", cleaned, maxsplit=1, flags=re.IGNORECASE)
        cleaned = parts[0].strip()
        thoughts.append(parts[1].strip())
    return cleaned, "\n".join(thoughts)


def _validate_endpoint(endpoint: str) -> str:
    """Validate that ``endpoint`` is an HTTP(S) URL and strip trailing slash."""
    parsed = urllib.parse.urlsplit(endpoint.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"endpoint must be an http(s) URL, got {endpoint!r}")
    return endpoint.strip().rstrip("/")


def call_chat_streaming(
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float = 0.2,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Stream a chat completion from vLLM and record TTFT, total latency, and usage."""
    base = _validate_endpoint(endpoint)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.95,
        "min_p": 0.08,
        "presence_penalty": 0.05,
        "repetition_penalty": 1.1,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking,
            "preserve_thinking": False,
        },
    }

    started = time.perf_counter()
    ttft_s: float | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}

    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", f"{base}/chat/completions", json=body) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                r_tok = delta.get("reasoning_content") or delta.get("reasoning") or ""
                c_tok = delta.get("content") or ""
                if (r_tok or c_tok) and ttft_s is None:
                    ttft_s = round(time.perf_counter() - started, 4)
                if r_tok:
                    reasoning_parts.append(r_tok)
                if c_tok:
                    content_parts.append(c_tok)

    total_s = round(time.perf_counter() - started, 4)
    raw_content = "".join(content_parts)
    clean_reply, inline_thought = strip_thinking(raw_content)
    reasoning_text = "".join(reasoning_parts) or inline_thought
    completion_tokens = int(usage.get("completion_tokens") or 0)
    reasoning_chars = len(reasoning_text)

    return {
        "reply": clean_reply,
        "reasoning_preview": reasoning_text[:300],
        "reasoning_chars": reasoning_chars,
        "finish_reason": finish_reason,
        "usage": usage,
        "completion_tokens": completion_tokens,
        "ttft_s": ttft_s if ttft_s is not None else total_s,
        "latency_s": total_s,
    }


def _percentile(vals: list[float], pct: float) -> float:
    """Compute linear-interpolated percentile ``pct`` over ``vals``."""
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 3)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 3)
    return round(s[f] + (k - f) * (s[c] - s[f]), 3)


def bakeoff_tool_runner(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Evaluate existing calculators plus Phase-F statutory formulas against FY2026-27.json for model-only comparison."""
    res = default_tool_runner(tool_name, params)
    if not res.get("tool_missing"):
        return res

    from app.tax.tables import get_table

    table = get_table("FY2026-27")
    if tool_name == "value_benefit_in_kind":
        kind = params.get("kind")
        if kind == "car":
            mv = float(params.get("market_value") or 0)
            yrs = int(params.get("years_since_first_provided") or 0)
            dep_val = mv * ((1.0 - 0.35) ** yrs)
            days_priv = float(params.get("days_private_use") or 365)
            days_yr = float(params.get("days_in_year") or 365)
            emp_pay = float(params.get("employee_payment") or 0)
            ann = max(0.0, 0.20 * dep_val * (days_priv / days_yr) - emp_pay)
            return {"ok": True, "annual_value": ann, "monthly_value": ann / 12.0}
        if kind == "debt_waiver":
            waived = float(params.get("debt_waived") or 0)
            return {"ok": True, "annual_value": waived, "monthly_value": waived / 12.0}
    elif tool_name == "objection_requirements":
        disputed = float(params.get("disputed_tax_ugx") or 0)
        return {
            "ok": True,
            "objection_window_days": float(table.get("objection_timeline_days") or 45),
            "tat_deadline_days": float(table.get("tax_appeals_tribunal_deadline_days") or 30),
            "deposit_rate": 0.30,
            "deposit_amount": 0.30 * disputed,
        }
    elif tool_name == "penalty_late_filing":
        tax_due = float(params.get("tax_payable_ugx") or 0)
        months = float(params.get("months_late") or 1)
        rate = float(table.get("penal_tax_late_filing_monthly_rate") or 0.02)
        min_ugx = float(table.get("penal_tax_late_filing_minimum_ugx") or 200000)
        return {"ok": True, "penal_tax": max(min_ugx, tax_due * rate * months)}
    elif tool_name == "calculate_stamp_duty":
        itype = params.get("instrument_type")
        val = float(params.get("value_ugx") or 0)
        if itype == "property_transfer":
            return {"ok": True, "stamp_duty": val * float(table.get("stamp_duty_property_transfer") or 0.01)}
        if itype == "motor_vehicle_transfer":
            return {"ok": True, "stamp_duty": float(table.get("stamp_duty_motor_vehicle_transfer_ugx") or 200000)}
        if itype == "motorcycle_transfer":
            return {"ok": True, "stamp_duty": float(table.get("stamp_duty_motorcycle_transfer_ugx") or 30000)}
    elif tool_name == "calculate_digital_services_tax":
        rev = float(params.get("gross_uganda_revenue_ugx") or 0)
        rate = float(table.get("digital_services_tax_non_resident") or 0.05)
        return {"ok": True, "dst_tax": rev * rate, "rate": rate}
    elif tool_name == "calculate_gaming_tax":
        stakes = float(params.get("total_stakes_ugx") or 0)
        payouts = float(params.get("total_payouts_ugx") or 0)
        rate = float(table.get("gaming_and_betting_tax_rate") or 0.30)
        return {"ok": True, "operator_gaming_tax": max(0.0, stakes - payouts) * rate}
    elif tool_name == "calculate_advance_vehicle_tax":
        vtype = params.get("vehicle_type")
        if vtype == "goods":
            tonnes = float(params.get("tonnes") or 0)
            rate = float(table.get("motor_vehicle_advance_tax_goods_per_tonne") or 50000)
            return {"ok": True, "annual_advance_tax": tonnes * rate}
        if vtype == "passenger":
            seats = float(params.get("seats") or 0)
            rate = float(table.get("motor_vehicle_advance_tax_passenger_per_seat") or 20000)
            return {"ok": True, "annual_advance_tax": seats * rate}
    elif tool_name == "calculate_presumptive_or_startup_tax":
        return {
            "ok": True,
            "presumptive_lower_threshold": float(table.get("presumptive_tax_lower_threshold") or 10000000),
            "presumptive_upper_threshold": float(table.get("presumptive_tax_upper_threshold") or 150000000),
            "startup_capital_cap": float(table.get("startup_citizen_exemption_capital_cap_ugx") or 500000000),
        }
    return res


def evaluate_frozen_dev(
    endpoint: str,
    model: str,
    *,
    enable_thinking: bool,
    concurrency: int = 2,
) -> dict[str, Any]:
    """Run model-only evaluation across frozen dev contexts."""
    rows = [
        json.loads(line)
        for line in FROZEN_DEV_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    max_tokens = 4096 if enable_thinking else 1536

    def _run_one(row: dict[str, Any]) -> dict[str, Any]:
        gen = call_chat_streaming(
            endpoint,
            model,
            row["messages"],
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
        )
        # Reweight out route_ok and sources_relevant for model-only generator comparison (§4.B.4)
        expect_model_only = copy.deepcopy(row["expect"])
        expect_model_only.pop("route", None)
        resp_obj = {
            "reply": gen["reply"],
            "latency_s": gen["latency_s"],
            "debug": {"finish_reason": gen["finish_reason"]},
        }
        ts = score_turn(expect_model_only, resp_obj, tool_runner=bakeoff_tool_runner)
        return {
            "scenario_id": row["scenario_id"],
            "tax_head": row["tax_head"],
            "turn_index": row["turn_index"],
            "turn_score": ts.to_dict(),
            "ttft_s": gen["ttft_s"],
            "latency_s": gen["latency_s"],
            "finish_reason": gen["finish_reason"],
            "completion_tokens": gen["completion_tokens"],
            "reasoning_chars": gen["reasoning_chars"],
            "reply": gen["reply"],
            "reply_preview": gen["reply"][:400],
        }

    turn_results: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        fmap = {pool.submit(_run_one, r): i for i, r in enumerate(rows)}
        for fut in concurrent.futures.as_completed(fmap):
            i = fmap[fut]
            res = fut.result()
            turn_results[i] = res
            print(
                f"  [turn {len(turn_results)}/{len(rows)}] {res['scenario_id']}#{res['turn_index']}: "
                f"score={res['turn_score']['score']*100:.1f} lat={res['latency_s']:.2f}s",
                flush=True,
            )

    ordered = [turn_results[i] for i in range(len(rows))]
    by_scen: dict[str, list[float]] = defaultdict(list)
    for r in ordered:
        by_scen[r["scenario_id"]].append(float(r["turn_score"]["score"]) * 100.0)
    scen_scores = [statistics.mean(v) for v in by_scen.values()]

    crit_errors = sum(1 for r in ordered if r["turn_score"]["critical_fail"])
    truncated = sum(1 for r in ordered if not r["turn_score"]["complete"])
    lats = [float(r["latency_s"]) for r in ordered]
    ttfts = [float(r["ttft_s"]) for r in ordered]

    fig_hits = 0
    fig_cov = 0
    premise_hits = 0
    premise_tot = 0
    fact_scores: list[float] = []
    for r in ordered:
        ts = r["turn_score"]
        if ts.get("facts") is not None:
            fact_scores.append(float(ts["facts"]))
        if ts.get("premise_ok") is not None:
            premise_tot += 1
            if ts["premise_ok"]:
                premise_hits += 1
        for fd in ts.get("figure_details") or []:
            if fd["status"] in ("hit", "miss"):
                fig_cov += 1
                if fd["status"] == "hit":
                    fig_hits += 1

    return {
        "mean_scenario_score_100": round(statistics.mean(scen_scores), 2) if scen_scores else 0.0,
        "mean_facts_pct": round(100.0 * statistics.mean(fact_scores), 2) if fact_scores else 0.0,
        "figure_accuracy_covered_pct": round(100.0 * fig_hits / fig_cov, 2) if fig_cov else 0.0,
        "premise_challenge_pct": round(100.0 * premise_hits / premise_tot, 2) if premise_tot else 0.0,
        "critical_errors": crit_errors,
        "truncated_turns": truncated,
        "truncation_rate_pct": round(100.0 * truncated / len(ordered), 2) if ordered else 0.0,
        "ttft_p50_s": _percentile(ttfts, 50.0),
        "ttft_p95_s": _percentile(ttfts, 95.0),
        "latency_p50_s": _percentile(lats, 50.0),
        "latency_p95_s": _percentile(lats, 95.0),
        "turns": ordered,
    }


def _parse_inline_tool_call(
    content: str,
) -> tuple[str | None, dict[str, Any], str]:
    """Parse either JSON <tool_call> or Qwen3.5/3.8 XML <function=...><parameter=...> blocks."""
    # 1. JSON <tool_call>{"name": ..., "arguments": ...}</tool_call>
    m_json = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL)
    if m_json:
        try:
            parsed_tc = json.loads(m_json.group(1))
            return parsed_tc.get("name"), parsed_tc.get("arguments") or {}, "inline_json"
        except Exception:
            pass
    # 2. Qwen3.5/3.8 <tool_call><function=NAME><parameter=KEY>VAL</parameter>...</function></tool_call>
    m_fn = re.search(
        r"<function=([A-Za-z0-9_:-]+)>(.*?)</function>",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if m_fn:
        fn_name = m_fn.group(1).strip()
        fn_body = m_fn.group(2)
        args: dict[str, Any] = {}
        for pm in re.finditer(
            r"<parameter=([A-Za-z0-9_:-]+)>\s*(.*?)\s*</parameter>",
            fn_body,
            re.DOTALL | re.IGNORECASE,
        ):
            k = pm.group(1).strip()
            raw_v = pm.group(2).strip()
            if raw_v.lower() in ("true", "false"):
                args[k] = raw_v.lower() == "true"
            else:
                try:
                    args[k] = int(raw_v) if raw_v.isdigit() else float(raw_v)
                except ValueError:
                    try:
                        args[k] = json.loads(raw_v)
                    except Exception:
                        args[k] = raw_v
        return fn_name, args, "inline_xml"
    return None, {}, "none"


def evaluate_tool_fidelity(
    endpoint: str,
    model: str,
    *,
    enable_thinking: bool,
) -> dict[str, Any]:
    """Measure tool-calling fidelity across 15 calculator prompts."""
    from app.tools import ToolRegistry
    import app.tools.calculators  # noqa: F401

    tools_openai = [
        t.to_openai_spec()
        for t in ToolRegistry.all()
        if t.schema.namespace == "tax_calculator"
    ]
    base = _validate_endpoint(endpoint)
    format_hits = 0
    production_hits = 0
    details: list[dict[str, Any]] = []

    with httpx.Client(timeout=120.0) as client:
        for item in TOOL_FIDELITY_PROMPTS:
            body: dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a Ugandan tax assistant. Call the appropriate tax_calculator "
                            "tool with the exact numeric arguments from the user's request."
                        ),
                    },
                    {"role": "user", "content": item["prompt"]},
                ],
                "tools": tools_openai,
                "tool_choice": "auto",
                "temperature": 0.0,
                "max_tokens": 1024 if enable_thinking else 512,
                "chat_template_kwargs": {
                    "enable_thinking": enable_thinking,
                    "preserve_thinking": False,
                },
            }
            ok = False
            called_name = None
            called_args: dict[str, Any] = {}
            err_str = ""
            parsed_from = "none"
            try:
                resp = client.post(f"{base}/chat/completions", json=body)
                resp.raise_for_status()
                data = resp.json()
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                tcalls = msg.get("tool_calls") or []
                if not tcalls and msg.get("content"):
                    called_name, called_args, parsed_from = _parse_inline_tool_call(
                        msg["content"]
                    )
                elif tcalls:
                    parsed_from = "tool_calls"
                    fn = tcalls[0].get("function") or {}
                    called_name = fn.get("name")
                    raw_args = fn.get("arguments") or "{}"
                    called_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                if called_name == item["expected_tool"] and isinstance(called_args, dict):
                    arg_match = True
                    for k, exp_v in item["expected_args"].items():
                        got_v = called_args.get(k)
                        if isinstance(exp_v, (int, float)) and not isinstance(exp_v, bool):
                            if not isinstance(got_v, (int, float)) or abs(float(got_v) - float(exp_v)) > 1.0:
                                arg_match = False
                                break
                        else:
                            if got_v != exp_v:
                                arg_match = False
                                break
                    ok = arg_match
            except Exception as exc:
                err_str = f"{type(exc).__name__}: {exc}"

            if ok:
                format_hits += 1
                if parsed_from in {"tool_calls", "inline_json"}:
                    production_hits += 1
            details.append(
                {
                    "id": item["id"],
                    "expected_tool": item["expected_tool"],
                    "called_tool": called_name,
                    "called_args": called_args,
                    "ok": ok,
                    "production_ok": ok and parsed_from in {"tool_calls", "inline_json"},
                    "parsed_from": parsed_from,
                    "error": err_str,
                }
            )

    return {
        "total": len(TOOL_FIDELITY_PROMPTS),
        "hits": production_hits,
        "fidelity_pct": round(
            100.0 * production_hits / len(TOOL_FIDELITY_PROMPTS), 2
        ),
        "format_hits": format_hits,
        "details": details,
    }


def evaluate_throughput_8_streams(
    endpoint: str,
    model: str,
    *,
    enable_thinking: bool,
) -> dict[str, Any]:
    """Run 8 concurrent streams of a ~1,000-token answer and measure tokens/s and p95 latency."""
    prompt_messages = [
        {
            "role": "system",
            "content": "You are a senior Uganda Revenue Authority tax advisor. Provide thorough, step-by-step statutory explanations with worked examples and tables.",
        },
        {
            "role": "user",
            "content": (
                "Write a comprehensive guide covering Uganda's FY2026-27 rules on: "
                "(1) Resident vs non-resident individual PAYE bands and the 183-day / 122-day residency tests, "
                "(2) Valuation of motor vehicle and housing benefits in kind, "
                "(3) Individual vs corporate rental income tax including the 50% company expense cap, "
                "(4) VAT registration thresholds, EFRIS e-invoice input tax credit rules, and imported services reverse charge, and "
                "(5) Tax objection timelines (45 days) and the 30% disputed tax deposit before the Tax Appeals Tribunal."
            ),
        },
    ]

    def _stream_worker(_idx: int) -> dict[str, Any]:
        res = call_chat_streaming(
            endpoint,
            model,
            prompt_messages,
            enable_thinking=enable_thinking,
            max_tokens=1024,
            temperature=0.2,
        )
        toks = int(res["completion_tokens"] or max(1, len(res["reply"].split())))
        elapsed = max(0.001, float(res["latency_s"]))
        return {
            "tokens": toks,
            "latency_s": elapsed,
            "ttft_s": res["ttft_s"],
            "tokens_per_s": round(toks / elapsed, 2),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_stream_worker, i) for i in range(8)]
        streams = [f.result() for f in futs]

    lats = [s["latency_s"] for s in streams]
    tps = [s["tokens_per_s"] for s in streams]
    return {
        "concurrent_streams": 8,
        "mean_tokens_per_stream": round(statistics.mean(s["tokens"] for s in streams), 1),
        "mean_tokens_per_s_per_stream": round(statistics.mean(tps), 2),
        "aggregate_tokens_per_s": round(sum(s["tokens"] for s in streams) / max(lats), 2),
        "latency_p50_s": _percentile(lats, 50.0),
        "latency_p95_s": _percentile(lats, 95.0),
        "streams": streams,
    }


def evaluate_trilingual_sanity(
    endpoint: str,
    model: str,
) -> dict[str, Any]:
    """Evaluate 12 Luganda/Swahili translations on figure preservation (mt.figures_survived)."""
    from app.mt import figures_survived

    survived_count = 0
    non_empty = 0
    details: list[dict[str, Any]] = []
    for item in TRILINGUAL_SANITY_PROMPTS:
        lang_name = "Luganda" if item["lang"] == "lg" else "Swahili"
        messages = [
            {
                "role": "system",
                "content": (
                    f"Translate the following Ugandan tax guidance accurately into {lang_name}. "
                    "Preserve every number, percentage, currency code (UGX), and day count verbatim."
                ),
            },
            {"role": "user", "content": item["source_en"]},
        ]
        res = call_chat_streaming(
            endpoint,
            model,
            messages,
            enable_thinking=False,
            max_tokens=512,
            temperature=0.0,
        )
        reply = res["reply"]
        ok_fig = bool(reply.strip()) and figures_survived(item["source_en"], reply)
        if reply.strip():
            non_empty += 1
        if ok_fig:
            survived_count += 1
        details.append(
            {
                "id": item["id"],
                "lang": item["lang"],
                "figures_survived": ok_fig,
                "latency_s": res["latency_s"],
                "translation_preview": reply[:240],
            }
        )

    return {
        "total": len(TRILINGUAL_SANITY_PROMPTS),
        "non_empty": non_empty,
        "figures_survived_count": survived_count,
        "figures_survived_pct": round(100.0 * survived_count / len(TRILINGUAL_SANITY_PROMPTS), 2),
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the model bake-off harness."""
    parser = argparse.ArgumentParser(description="Run Phase B reasoner bake-off against a local vLLM endpoint.")
    parser.add_argument("--endpoint", required=True, help="vLLM OpenAI base URL, e.g. http://127.0.0.1:8021/v1")
    parser.add_argument("--model", required=True, help="Served model name")
    parser.add_argument("--mode", choices=("nothink", "think"), default="nothink")
    parser.add_argument("--label", required=True, help="Contender label, e.g. A-nothink, B-nothink, B-think")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--run-tool-fidelity", action="store_true")
    parser.add_argument("--run-throughput", action="store_true")
    parser.add_argument("--run-trilingual-sanity", action="store_true")
    parser.add_argument("--out", required=True, help="Path to write JSON results")
    args = parser.parse_args(argv)

    enable_thinking = args.mode == "think"
    print(f"=== Running bake-off: {args.label} ({args.model} @ {args.endpoint}, mode={args.mode}) ===", flush=True)

    dev_eval = evaluate_frozen_dev(
        args.endpoint,
        args.model,
        enable_thinking=enable_thinking,
        concurrency=args.concurrency,
    )
    result: dict[str, Any] = {
        "label": args.label,
        "endpoint": args.endpoint,
        "model": args.model,
        "mode": args.mode,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dev_eval": dev_eval,
    }

    if args.run_tool_fidelity:
        print(f"  Running 15-prompt tool-calling fidelity for {args.label}...", flush=True)
        result["tool_fidelity"] = evaluate_tool_fidelity(
            args.endpoint,
            args.model,
            enable_thinking=enable_thinking,
        )

    if args.run_throughput:
        print(f"  Running 8-stream throughput benchmark for {args.label}...", flush=True)
        result["throughput_8_streams"] = evaluate_throughput_8_streams(
            args.endpoint,
            args.model,
            enable_thinking=enable_thinking,
        )

    if args.run_trilingual_sanity:
        print(f"  Running 12-prompt lg/sw sanity check for {args.label}...", flush=True)
        result["trilingual_sanity"] = evaluate_trilingual_sanity(
            args.endpoint,
            args.model,
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"DONE {args.label}: score={dev_eval['mean_scenario_score_100']}/100 "
        f"crit={dev_eval['critical_errors']} p95={dev_eval['latency_p95_s']}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
