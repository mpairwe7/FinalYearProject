"""Export the audit ledger as W3C PROV-O, typed with AgentO (ADR-0033, phase 2).

Each ``generate`` row (one answered turn) becomes a ``prov:Activity`` that is
also an AgentO ``ExecutionStep``. It used the kernel release it was pinned to
(an AgentO ``KnowledgeBase``), the model (a ``LanguageModel``) and the tools it
called (``Tool``), and it generated the answer, a ``prov:Entity`` identified by
the digest of the text served. The agent is a ``prov:SoftwareAgent`` and an
AgentO ``Agent``.

Vocabulary is taken from the published ontologies: PROV-O
(``http://www.w3.org/ns/prov#``) and AgentO as published in its repository
(``http://www.w3id.org/agentic-ai/onto#``; ESWC 2026, CC BY 4.0).

The export carries no taxpayer data. Questions and answers were already
digests in the ledger; a user id is replaced by an unsalted SHA-256 prefix, which
groups a person's turns without naming them (it is a pseudonym, not anonymity:
anyone holding the id can recompute it, so the export stays an internal record).

``python -m app.audit.provenance --tenant default --out turns.prov.jsonld``
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROV = "http://www.w3.org/ns/prov#"
AGENTO = "http://www.w3id.org/agentic-ai/onto#"
URA = "urn:ura-kernel:vocab#"

CONTEXT: dict[str, Any] = {
    "prov": PROV,
    "agento": AGENTO,
    "ura": URA,
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "prov:startedAtTime": {"@type": "xsd:dateTime"},
    "prov:used": {"@type": "@id"},
    "prov:wasAssociatedWith": {"@type": "@id"},
    "prov:wasGeneratedBy": {"@type": "@id"},
    "agento:hasAssociatedTask": {"@type": "@id"},
    "agento:usesTool": {"@type": "@id"},
    "agento:hasKnowledge": {"@type": "@id"},
    "agento:useLanguageModel": {"@type": "@id"},
}


def _pseudonym(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16] if user_id else ""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts or 0), UTC).isoformat()


def turn_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The PROV-O / AgentO nodes for one ``generate`` ledger row."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    activity_id = f"urn:ura-turn:{row.get('event_id') or row.get('seq')}"
    agent_id = f"urn:ura-agent:{payload.get('agent_route') or payload.get('channel') or 'chat'}"
    nodes: list[dict[str, Any]] = []
    used: list[str] = []

    pin = payload.get("kernel_pin") if isinstance(payload.get("kernel_pin"), dict) else None
    if pin and pin.get("kernel_version"):
        kernel_id = f"urn:ura-kernel:release:{pin['kernel_version']}"
        used.append(kernel_id)
        nodes.append({"@id": kernel_id, "@type": ["prov:Entity", "agento:KnowledgeBase"],
                      "ura:contentSha256": pin.get("content_sha256", ""),
                      "ura:rulesetVersion": pin.get("ruleset_version", ""), "ura:asOf": pin.get("as_of", "")})
    model = str(payload.get("model") or "")
    model_id = f"urn:ura-model:{model}" if model else ""
    if model_id:
        used.append(model_id)
        nodes.append({"@id": model_id, "@type": ["prov:Entity", "agento:LanguageModel"], "ura:modelName": model})
    tools = []
    for call in payload.get("tool_calls") or []:
        name = str(call.get("name") or "") if isinstance(call, dict) else ""
        if name:
            tool_id = f"urn:ura-tool:{name}"
            tools.append(tool_id)
            nodes.append({"@id": tool_id, "@type": ["prov:Entity", "agento:Tool"], "ura:toolName": name})
    prov_block = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    if prov_block.get("index_corpus_hash"):
        corpus_id = f"urn:ura-corpus:{prov_block['index_corpus_hash'][:16]}"
        used.append(corpus_id)
        nodes.append({"@id": corpus_id, "@type": ["prov:Entity", "agento:KnowledgeBase"],
                      "ura:indexCorpusHash": prov_block["index_corpus_hash"]})

    agent = {"@id": agent_id, "@type": ["prov:SoftwareAgent", "agento:Agent"]}
    if tools:
        agent["agento:usesTool"] = sorted(set(tools))
    if model_id:
        agent["agento:useLanguageModel"] = model_id
    if pin and pin.get("kernel_version"):
        agent["agento:hasKnowledge"] = f"urn:ura-kernel:release:{pin['kernel_version']}"
    nodes.append(agent)

    task_id = f"urn:ura-task:{payload.get('retrieval_mode') or 'answer'}"
    nodes.append({"@id": task_id, "@type": "agento:Task"})
    activity = {
        "@id": activity_id,
        "@type": ["prov:Activity", "agento:ExecutionStep"],
        "prov:startedAtTime": _iso(row.get("ts", 0)),
        "prov:wasAssociatedWith": agent_id,
        "prov:used": sorted(set(used + tools)),
        "agento:hasAssociatedTask": task_id,
        "agento:stepOrder": int(row.get("seq") or 0),
        "ura:channel": payload.get("channel", ""),
        "ura:outcome": payload.get("outcome", ""),
        "ura:subject": _pseudonym(str(row.get("user_id") or "")),
        "ura:rowHash": row.get("row_hash", ""),
    }
    nodes.append(activity)
    if payload.get("reply_sha256"):
        nodes.append({"@id": f"urn:ura-answer:{payload['reply_sha256'][:32]}", "@type": "prov:Entity",
                      "prov:wasGeneratedBy": activity_id, "ura:replySha256": payload["reply_sha256"]})
    return nodes


def export(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A JSON-LD document for the ``generate`` rows among *rows*, deduplicated by ``@id``."""
    graph: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event_type") != "generate":
            continue
        for node in turn_nodes(row):
            graph.setdefault(node["@id"], node)
    return {"@context": CONTEXT, "@graph": [graph[k] for k in sorted(graph)]}


def export_ledger(tenant_id: str = "default", *, page: int = 200) -> dict[str, Any]:
    from .ledger import get_ledger

    ledger = get_ledger()
    rows: list[dict[str, Any]] = []
    since = 0
    while True:
        batch = ledger.read(tenant_id=tenant_id, since_seq=since, limit=page)
        if not batch:
            break
        rows += batch
        since = int(batch[-1]["seq"])
    return export(rows)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(description="Export the audit ledger as PROV-O / AgentO JSON-LD.")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = export_ledger(args.tenant)
    args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"{len(document['@graph'])} nodes -> {args.out}")  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
