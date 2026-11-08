"""Kernel releases: versioned, content-addressed, reviewed (ADR-0033).

A release names the exact knowledge an answer rests on. Its identity is the
SHA-256 of the canonical graph, of the ruleset source and of the model
version. Changing a rate table, the build or a rule produces a hash no release
records, so CI fails (``python -m app.kernel.release check``) until a new entry
is added. The first entry for a hash is an ``engineering_baseline``; it becomes
``approved`` only when a named reviewer records it.

Commands::

    python -m app.kernel.release check                     # CI gate
    python -m app.kernel.release propose --version 1.1.0   # print the entry to add
    python -m app.kernel.release record --version 1.1.0 --notes "..." [--reviewer NAME --role ROLE]
"""

from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .model import MODEL_VERSION

REGISTRY = Path(__file__).with_name("releases.json")
RULES_SOURCE = Path(__file__).with_name("rules.py")
#: One gzipped canonical graph per released content hash, so a past turn can be
#: replayed against exactly the knowledge it answered under.
SNAPSHOT_DIR = Path(__file__).with_name("snapshots")

REVIEW_BASELINE = "engineering_baseline"
REVIEW_APPROVED = "approved"

_lock = threading.Lock()
_current: KernelVersion | None = None


@dataclass(frozen=True)
class KernelVersion:
    version: str
    released: bool
    content_sha256: str
    rules_sha256: str
    model_version: str
    ruleset_version: str
    review_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_json(store: Any) -> str:
    return json.dumps(store.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha256(store: Any) -> str:
    return hashlib.sha256(canonical_json(store).encode("utf-8")).hexdigest()


def rules_sha256() -> str:
    """Hash of the ruleset source, or ``""`` when no ruleset exists yet."""
    if not RULES_SOURCE.exists():
        return ""
    return hashlib.sha256(RULES_SOURCE.read_bytes()).hexdigest()


def ruleset_version() -> str:
    if not RULES_SOURCE.exists():
        return "none"
    from . import rules

    return str(rules.RULESET_VERSION)


def load_registry() -> list[dict[str, Any]]:
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return list(raw.get("releases") or [])


def _matches(entry: dict[str, Any], content: str, rules: str) -> bool:
    return (
        entry.get("content_sha256") == content
        and entry.get("rules_sha256", "") == rules
        and entry.get("model_version") == MODEL_VERSION
    )


def compute() -> KernelVersion:
    """The release the running code and tables correspond to."""
    from .functions import kernel_store

    content = content_sha256(kernel_store())
    rules = rules_sha256()
    releases = load_registry()
    for entry in reversed(releases):
        if _matches(entry, content, rules):
            return KernelVersion(
                version=str(entry["version"]),
                released=True,
                content_sha256=content,
                rules_sha256=rules,
                model_version=MODEL_VERSION,
                ruleset_version=ruleset_version(),
                review_status=str((entry.get("review") or {}).get("status") or REVIEW_BASELINE),
            )
    latest = str(releases[-1]["version"]) if releases else "0.0.0"
    return KernelVersion(
        version=f"{latest}+unreleased.{content[:12]}",
        released=False,
        content_sha256=content,
        rules_sha256=rules,
        model_version=MODEL_VERSION,
        ruleset_version=ruleset_version(),
        review_status="unreleased",
    )


def current() -> KernelVersion:
    """:func:`compute`, cached for the process."""
    global _current
    with _lock:
        if _current is None:
            _current = compute()
        return _current


def clear_cache() -> None:
    global _current
    with _lock:
        _current = None


def snapshot_path(content: str) -> Path:
    return SNAPSHOT_DIR / f"{content[:16]}.json.gz"


def write_snapshot(store: Any) -> Path:
    """Write the canonical graph for *store*'s content hash; byte-stable (mtime 0)."""
    content = content_sha256(store)
    path = snapshot_path(content)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    data = gzip.compress(canonical_json(store).encode("utf-8"), compresslevel=9, mtime=0)
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return path


def load_snapshot(version: str) -> Any:
    """The graph of a recorded release, verified against its content hash."""
    from ..graph.store import InMemoryGraphStore

    entry = next((e for e in load_registry() if e.get("version") == version), None)
    if entry is None:
        raise KeyError(f"no kernel release {version!r}")
    path = snapshot_path(str(entry["content_sha256"]))
    raw = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    store = InMemoryGraphStore.from_dict(raw)
    if content_sha256(store) != entry["content_sha256"]:
        raise ValueError(f"snapshot for {version} does not match its recorded content hash")
    return store


def proposed_entry(version: str, notes: str, *, reviewer: str = "", role: str = "") -> dict[str, Any]:
    """The registry entry for the current content, ready to append."""
    from ..graph.schema import NodeKind
    from .functions import kernel_store

    store = kernel_store()
    approved = bool(reviewer and role)
    return {
        "version": version,
        "released": _dt.date.today().isoformat(),
        "content_sha256": content_sha256(store),
        "rules_sha256": rules_sha256(),
        "model_version": MODEL_VERSION,
        "ruleset_version": ruleset_version(),
        "fiscal_years": sorted(n.label for n in store.by_kind(NodeKind.FISCAL_YEAR)),
        "stats": store.stats(),
        "review": {
            "status": REVIEW_APPROVED if approved else REVIEW_BASELINE,
            "reviewer": reviewer,
            "role": role,
            "approved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds") if approved else "",  # noqa: UP017 - Python 3.10 dev container
        },
        "notes": notes,
    }


def _semver(text: str) -> tuple[int, int, int]:
    parts = text.split("+", 1)[0].split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a semantic version: {text!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def record(version: str, notes: str, *, reviewer: str = "", role: str = "") -> dict[str, Any]:
    """Append a release for the current content. Refuses a duplicate or an older version."""
    releases = load_registry()
    if releases and _semver(version) <= _semver(str(releases[-1]["version"])):
        raise ValueError(f"{version} is not newer than {releases[-1]['version']}")
    entry = proposed_entry(version, notes, reviewer=reviewer, role=role)
    if releases and _matches(releases[-1], entry["content_sha256"], entry["rules_sha256"]):
        # The same content may be recorded again only to approve it: the documented
        # step that turns an engineering baseline into a reviewed release.
        promotes = (entry["review"]["status"] == REVIEW_APPROVED
                    and (releases[-1].get("review") or {}).get("status") != REVIEW_APPROVED)
        if not promotes:
            raise ValueError("the current content is already released as " + str(releases[-1]["version"]))
    from .functions import kernel_store

    write_snapshot(kernel_store())
    releases.append(entry)
    REGISTRY.write_text(json.dumps({"releases": releases}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    clear_cache()
    return entry


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(description="Kernel releases (ADR-0033).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("snapshot")
    p_prop = sub.add_parser("propose")
    p_prop.add_argument("--version", required=True)
    p_prop.add_argument("--notes", default="")
    p_rec = sub.add_parser("record")
    p_rec.add_argument("--version", required=True)
    p_rec.add_argument("--notes", required=True)
    p_rec.add_argument("--reviewer", default="")
    p_rec.add_argument("--role", default="")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        version = compute()
        print(json.dumps(version.to_dict(), indent=2))  # noqa: T201 - CLI output
        if not version.released:
            print(  # noqa: T201 - CLI output
                "UNRELEASED: the tables, the build or the rules changed. Record a release "
                "(python -m app.kernel.release record ...)."
            )
            return 1
        return 0
    if args.cmd == "snapshot":
        from .functions import kernel_store

        print(write_snapshot(kernel_store()))  # noqa: T201 - CLI output
        return 0
    if args.cmd == "propose":
        print(json.dumps(proposed_entry(args.version, args.notes), indent=2))  # noqa: T201 - CLI output
        return 0
    print(json.dumps(record(args.version, args.notes, reviewer=args.reviewer, role=args.role), indent=2))  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
