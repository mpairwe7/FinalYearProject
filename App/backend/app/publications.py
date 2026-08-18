"""URA publication ingest — hash-diff a configured https URL (G15 slice).

Never auto-reindexes. On change it writes a crawl JSONL stub and calls
``freshness.enqueue_reindex_request`` so ops still run
``python -m app.indexer --recreate``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ._root import APP_DATA_ROOT, PROJECT_ROOT
from .freshness import FreshnessReport, enqueue_reindex_request

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(
    os.getenv(
        "PUBLICATIONS_SNAPSHOT_PATH",
        str(APP_DATA_ROOT.parent / "Model" / "publications_snapshot.json"),
    )
)
CRAWL_DIR = Path(os.getenv("CRAWL_JSONL_DIR", str(APP_DATA_ROOT / "crawl_jsonl")))


def publications_url() -> str:
    url = (os.getenv("URA_PUBLICATIONS_URL") or "").strip()
    if url in {"fixture", "fixture://ura-publications"}:
        return "fixture://ura-publications"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return url


def fixture_path() -> Path:
    configured = (os.getenv("PUBLICATIONS_FIXTURE_PATH") or "").strip()
    if configured:
        return Path(configured)
    for candidate in (
        PROJECT_ROOT / "Data" / "eval" / "publications_fixture.txt",
        APP_DATA_ROOT / "eval" / "publications_fixture.txt",
    ):
        if candidate.is_file():
            return candidate
    return PROJECT_ROOT / "Data" / "eval" / "publications_fixture.txt"


def load_fixture_body() -> bytes:
    path = fixture_path()
    if path.is_file():
        return path.read_bytes()
    return b"URA sandbox publications fixture. Not a live ura.go.ug crawl.\n"


def _use_fixture(source: str) -> bool:
    if (os.getenv("APP_ENV") or "development").lower() == "production":
        return False
    if source.startswith("fixture:"):
        return True
    if source:
        return False
    return True


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _load_snapshot(path: Path | None = None) -> dict[str, Any]:
    target = path or SNAPSHOT_PATH
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_snapshot(payload: dict[str, Any], path: Path | None = None) -> Path:
    target = path or SNAPSHOT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(target)
    return target


def fetch_publications(url: str | None = None) -> tuple[bytes, str]:
    target = url if url is not None else publications_url()
    if not target:
        raise ValueError("URA_PUBLICATIONS_URL must be an https URL")
    req = Request(target, headers={"User-Agent": "ura-chatbot-freshness/1.0"})
    with urlopen(req, timeout=20) as resp:  # noqa: S310 — https-only above
        body = resp.read()
    return body, _digest(body)


def ingest_publications(
    *,
    url: str | None = None,
    snapshot_path: Path | None = None,
    crawl_dir: Path | None = None,
    body: bytes | None = None,
) -> dict[str, Any]:
    """Fetch (or accept) publication bytes, persist if the hash changed."""
    source = url if url is not None else publications_url()
    if body is None:
        if _use_fixture(source or ""):
            body = load_fixture_body()
            source = source or "fixture://ura-publications"
            digest = _digest(body)
        elif not source:
            return {"ok": False, "error": "URA_PUBLICATIONS_URL unset or not https"}
        else:
            try:
                body, digest = fetch_publications(source)
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                logger.warning("publication fetch failed: %s", type(exc).__name__)
                return {"ok": False, "error": type(exc).__name__}
    else:
        digest = _digest(body)
        source = source or "inline"

    previous = _load_snapshot(snapshot_path)
    changed = previous.get("sha256") != digest
    now = datetime.now(timezone.utc).isoformat()
    written = None
    enqueued = False
    if changed:
        dest = crawl_dir or CRAWL_DIR
        dest.mkdir(parents=True, exist_ok=True)
        written = dest / "ura_publications_latest.jsonl"
        record = {
            "url": source,
            "crawled_at": now,
            "sha256": digest,
            "text": body[:20_000].decode("utf-8", errors="replace"),
            "title": "URA publications snapshot",
        }
        written.write_text(json.dumps(record, ensure_ascii=False) + "\n")
        report = FreshnessReport(
            ok=False,
            corpus_hash=digest,
            previous_hash=str(previous.get("sha256") or ""),
            added=[str(written)],
            removed=[],
            changed=[],
        )
        enqueue_reindex_request(report)
        enqueued = True

    _write_snapshot(
        {"url": source, "sha256": digest, "checked_at": now, "changed": changed},
        snapshot_path,
    )
    return {
        "ok": True,
        "changed": changed,
        "sha256": digest,
        "written": str(written) if written else None,
        "reindex_requested": enqueued,
        "reindex_hint": "python -m app.indexer --recreate",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="", help="Override URA_PUBLICATIONS_URL")
    args = parser.parse_args(argv)
    result = ingest_publications(url=args.url or None)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
