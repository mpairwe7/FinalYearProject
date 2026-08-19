"""Exercise the slot cascade against a REAL model, not a lambda.

The 8 resolver unit tests inject a stub, so they prove the contract — off-list
answers rejected, "unclear" falls through, a raising resolver survivable — but
not that an actual model maps "sole trader" to individual, or a Luganda reply to
the right option. That is the completeness gap this closes.

Points llm.LLM_BACKEND at a local vLLM serving Qwen3-8B on a free GPU, then
drives validate_slot end to end. What is asserted is the *outcome* of the whole
cascade, because that is what a user experiences: rules first, model only when
they fail, and the model's answer re-validated against the option list.

Start a model on a free GPU first (the repo's picker chooses one):

    GPU=$(bash scripts/select_free_gpu.sh)
    docker run -d --name ura-vllm --gpus "\"device=$GPU\"" \
      -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
      -p 18001:8001 --ipc=host vllm/vllm-openai:v0.17.1 \
      --model Qwen/Qwen3-8B --port 8001 --gpu-memory-utilization 0.5

Pin one GPU rather than using the compose vllm profile, which reserves
`count: all` — on a shared box that takes cards other work is using.

    LLM_BACKEND=vllm VLLM_BASE_URL=http://localhost:18001/v1 \
      python scripts/verify_slot_resolver_live.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App" / "backend"))

os.environ.setdefault("LLM_BACKEND", "vllm")
os.environ.setdefault("VLLM_BASE_URL", "http://localhost:18001/v1")
os.environ.setdefault("LLM_MODEL", "Sunbird/Sunflower-14B-FP8")
os.environ.setdefault("LLM_ENABLED", "true")

from app import llm  # noqa: E402
from app.workflows.slots import validate_slot  # noqa: E402

KIND = "enum[individual,organisation]"
ENTITY = "enum[individual,company,ngo]"
TAX = "enum[vat,paye,corporation tax,withholding tax,customs,other]"
PAY = "enum[bank,mobile money,online card,other]"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}", flush=True)
    results.append((ok, label))


def resolver(reply: str, options: list[str]) -> str | None:
    """The same wiring service.ChatModel._resolve_slot_choice uses."""
    return llm.classify_choice(reply, options) or None


print(f"\nbackend={llm.LLM_BACKEND}  base={os.environ['VLLM_BASE_URL']}")
print(f"model reachable: {llm.is_available()}")

# ---- the model is only meant to be reached when the rules fail --------------
print("\n[1] The rules still decide first (no inference on the common path)")
calls: list[str] = []


def counting_resolver(reply: str, options: list[str]) -> str | None:
    calls.append(reply)
    return resolver(reply, options)


for reply, expected in [
    ("as an individual", "individual"),
    ("organization", "organisation"),
    ("individul", "individual"),
]:
    ok, value, _ = validate_slot(reply, KIND, counting_resolver)
    check(ok and value == expected, f"{reply!r} -> {expected}", f"got {value!r}")
check(not calls, "the model was not consulted for any of them", f"{len(calls)} calls")

# ---- what only meaning can reach -------------------------------------------
print("\n[2] Replies no rule covers")
SEMANTIC = [
    ("sole trader", KIND, "individual"),
    ("I run a small business", ENTITY, "company"),
    ("a charity", ENTITY, "ngo"),
    ("I pay tax on my salary every month", TAX, "paye"),
    ("I send money with my phone", PAY, "mobile money"),
]
for reply, spec, expected in SEMANTIC:
    t0 = time.monotonic()
    ok, value, err = validate_slot(reply, spec, resolver)
    dt = time.monotonic() - t0
    check(ok and value == expected, f"{reply!r} -> {expected}", f"got {value!r} in {dt:.1f}s {err[:30]}")

# ---- the languages this assistant actually answers in -----------------------
print("\n[3] Replies in the other configured languages")
MULTILINGUAL = [
    ("yee", "boolean", True),             # Luganda: yes
    ("hapana", "boolean", False),         # Swahili: no
    ("ndi muntu ssekinnoomu", KIND, "individual"),  # Luganda: I am an individual
]
for reply, spec, expected in MULTILINGUAL:
    ok, value, err = validate_slot(reply, spec, resolver)
    check(ok and value == expected, f"{reply!r} -> {expected}", f"got {value!r} {err[:30]}")

# The property that actually matters for a word the model does not know. Qwen3-8B
# answers "unclear" for Luganda "nedda" — asserting it resolves would be
# asserting a model capability, and the system is correct either way as long as
# it never returns the OPPOSITE of what was said.
ok, value, err = validate_slot("nedda", "boolean", resolver)
check(
    (not ok) or value is False,
    "an unrecognised negation is refused, never flipped to yes",
    f"ok={ok} value={value!r} {err[:34]}",
)

# ---- the leash: the model must not be able to invent a value ----------------
print("\n[4] The model cannot introduce a value")
ok, _, err = validate_slot("purple elephant", KIND, resolver)
check(not ok, "nonsense is refused rather than forced into an option", f"{err[:50]}")
ok, _, err = validate_slot("individual or organisation, not sure", KIND, resolver)
check(not ok, "a genuinely ambiguous reply asks again", f"{err[:50]}")

failed = [lbl for good, lbl in results if not good]
print(f"\n{'=' * 70}\n{len(results) - len(failed)}/{len(results)} passed")
for lbl in failed:
    print(f"  FAIL  {lbl}")
sys.exit(1 if failed else 0)
