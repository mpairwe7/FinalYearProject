"""Safely rebuild a local or embedded Qdrant corpus when its sources change.

The serving name is a Qdrant *alias*, not a mutable collection. A rebuild
creates a versioned candidate collection, validates its source-hash sentinel,
then changes the alias in one Qdrant operation. If export, embedding, or
upsert fails, the alias is untouched and the previous index continues serving.

Use this for local Compose and the CPU image build. Managed Vectorize remains a
separate deployment integration because it is not reachable from these local
or embedded topologies.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import zlib
from shutil import copyfile
from typing import TYPE_CHECKING, Any

from .freshness import (
    compare,
    compare_index_hash,
    snapshot_sources,
    write_lifecycle_status,
    write_snapshot,
    write_status,
)
from .indexer import (
    BM25_STATE_PATH,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
    build_index,
    load_documents,
)
from .retriever import BM25SparseEncoder, bm25_binding_sentinel_id, deterministic_point_id

logger = logging.getLogger(__name__)

INDEX_CANARY_COUNT = max(1, int(os.getenv("INDEX_CANARY_COUNT", "3")))
INDEX_CANARY_TOP_K = max(1, int(os.getenv("INDEX_CANARY_TOP_K", "5")))
INDEX_CANARY_MIN_HIT_RATE = min(
    1.0, max(0.0, float(os.getenv("INDEX_CANARY_MIN_HIT_RATE", "1.0")))
)

if TYPE_CHECKING:
    from pathlib import Path


class IndexLifecycleError(RuntimeError):
    """A safe rebuild could not be staged or promoted."""


def candidate_collection_name(alias: str, source_corpus_hash: str) -> str:
    """Return the deterministic physical collection for a source revision."""
    if not source_corpus_hash:
        raise IndexLifecycleError("cannot stage an index without a source corpus hash")
    return f"{alias}__build_{source_corpus_hash[:16]}"


def _collection_names(client: Any) -> set[str]:
    return {item.name for item in client.get_collections().collections}


def alias_target(client: Any, alias: str) -> str:
    """Resolve a Qdrant alias to its physical collection, if it exists."""
    for item in client.get_aliases().aliases:
        if item.alias_name == alias:
            return item.collection_name
    return ""


def binding_payload(client: Any, collection: str, alias: str) -> dict[str, Any]:
    """Read the immutable index metadata written after the final upsert."""
    points = client.retrieve(
        collection_name=collection,
        ids=[bm25_binding_sentinel_id(alias)],
        with_payload=True,
        with_vectors=False,
    )
    return dict(points[0].payload or {}) if points else {}


def _canary_sample(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose deterministic, corpus-spanning documents for sparse self-retrieval."""
    usable = [doc for doc in documents if str(doc.get("embed_text") or doc.get("text") or "").strip()]
    if not usable:
        raise IndexLifecycleError("candidate quality gate has no searchable documents")
    count = min(INDEX_CANARY_COUNT, len(usable))
    if count == 1:
        return [usable[0]]
    return [usable[index * (len(usable) - 1) // (count - 1)] for index in range(count)]


def validate_candidate_retrieval(
    client: Any,
    collection: str,
    documents: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Require a staged collection to retrieve deterministic corpus canaries.

    Hash and schema validation prove that a build completed; this gate also
    proves that its sparse vector space can retrieve representative documents.
    It runs before the alias update and therefore turns a relevance regression
    into a harmless failed candidate rather than a live deployment.
    """
    from qdrant_client import models

    packed = payload.get("bm25_state_zlib")
    if not isinstance(packed, str) or not packed:
        raise IndexLifecycleError("candidate quality gate has no embedded BM25 state")
    try:
        state = json.loads(zlib.decompress(base64.b64decode(packed.encode("ascii"))))
        encoder = BM25SparseEncoder.from_dict(state)
    except Exception as exc:
        raise IndexLifecycleError("candidate quality gate could not decode BM25 state") from exc

    canaries = _canary_sample(documents)
    passed = 0
    misses: list[str] = []
    for document in canaries:
        query = str(document.get("embed_text") or document.get("text") or "")
        indices, values = encoder.encode(query)
        if not indices:
            misses.append(str(document.get("source") or "unknown-source"))
            continue
        response = client.query_points(
            collection_name=collection,
            query=models.SparseVector(indices=indices, values=values),
            using="sparse",
            limit=INDEX_CANARY_TOP_K,
            with_payload=False,
            with_vectors=False,
        )
        expected_id = deterministic_point_id(document)
        if expected_id in {str(point.id) for point in response.points}:
            passed += 1
        else:
            misses.append(str(document.get("source") or "unknown-source"))

    hit_rate = passed / len(canaries)
    result = {
        "canaries": len(canaries),
        "passed": passed,
        "hit_rate": hit_rate,
        "minimum_hit_rate": INDEX_CANARY_MIN_HIT_RATE,
        "misses": misses,
    }
    if hit_rate < INDEX_CANARY_MIN_HIT_RATE:
        raise IndexLifecycleError(
            "candidate retrieval quality gate failed "
            f"({passed}/{len(canaries)} canaries; misses={misses[:3]})"
        )
    return result


def promote_alias(client: Any, *, alias: str, collection: str, previous: str = "") -> None:
    """Atomically point the serving alias at a validated candidate collection."""
    try:
        from qdrant_client import models
    except ImportError:
        from types import SimpleNamespace

        models = SimpleNamespace(  # type: ignore[assignment]
            DeleteAliasOperation=lambda **kw: SimpleNamespace(**kw),
            DeleteAlias=lambda **kw: SimpleNamespace(**kw),
            CreateAliasOperation=lambda **kw: SimpleNamespace(**kw),
            CreateAlias=lambda **kw: SimpleNamespace(**kw),
        )

    operations: list[Any] = []
    if previous:
        operations.append(
            models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias))
        )
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=collection, alias_name=alias)
        )
    )
    client.update_collection_aliases(change_aliases_operations=operations)


def _candidate_state_path(source_corpus_hash: str) -> Path:
    return BM25_STATE_PATH.with_name(
        f"{BM25_STATE_PATH.stem}.{source_corpus_hash[:16]}{BM25_STATE_PATH.suffix}"
    )


def _promote_bm25_state(candidate: Path) -> None:
    """Keep the legacy file current too; serving retrievers use the sentinel."""
    target = BM25_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    copyfile(candidate, tmp)
    tmp.replace(target)


def _write_fresh_status(snapshot: dict[str, Any]) -> None:
    """Persist the source/index binding that `/v1/index/freshness` serves."""
    write_snapshot(snapshot)
    report = compare(snapshot, snapshot)
    compare_index_hash(report, str(snapshot["corpus_hash"]))
    write_status(report)


def rebuild_if_needed(*, force: bool = False) -> dict[str, Any]:
    """Stage and atomically promote a Qdrant rebuild when source hashes differ."""
    from qdrant_client import QdrantClient

    snapshot = snapshot_sources()
    source_corpus_hash = str(snapshot["corpus_hash"])
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
    collections = _collection_names(client)
    active = alias_target(client, QDRANT_COLLECTION)

    # A collection and alias cannot share a name. Refuse to convert a legacy
    # mutable collection in place because deleting it first would violate the
    # availability guarantee. Local/CPU deployments use a new `*_active` alias.
    if not active and QDRANT_COLLECTION in collections:
        raise IndexLifecycleError(
            f"{QDRANT_COLLECTION!r} is a physical legacy collection, not an alias. "
            "Set QDRANT_COLLECTION to a new stable alias (for example "
            f"{QDRANT_COLLECTION}_active) and rerun the staged rebuild."
        )

    active_payload = binding_payload(client, QDRANT_COLLECTION, QDRANT_COLLECTION) if active else {}
    active_hash = str(active_payload.get("source_corpus_hash") or "")
    if active and active_hash == source_corpus_hash and not force:
        _write_fresh_status(snapshot)
        logger.info("Qdrant index already matches source hash %s", source_corpus_hash[:12])
        result = {
            "reindexed": False,
            "collection": active,
            "alias": QDRANT_COLLECTION,
            "source_corpus_hash": source_corpus_hash,
        }
        write_lifecycle_status(
            ok=True,
            reindexed=False,
            collection=active,
            source_corpus_hash=source_corpus_hash,
        )
        return result

    candidate = candidate_collection_name(QDRANT_COLLECTION, source_corpus_hash)
    if candidate == active:
        # Never overwrite a serving collection, even if its sentinel is broken.
        candidate = f"{candidate}__retry"
    if candidate in collections:
        client.delete_collection(candidate)
        logger.info("Deleted incomplete candidate collection '%s'", candidate)

    state_path = _candidate_state_path(source_corpus_hash)
    try:
        documents = load_documents()
        stats = build_index(
            documents,
            collection_name=candidate,
            binding_collection_name=QDRANT_COLLECTION,
            bm25_state_path=state_path,
            source_corpus_hash=source_corpus_hash,
        )
        # Do not promote a candidate built while a source file changed. The
        # candidate is harmless because the serving alias still points at old.
        after_build = snapshot_sources()
        if after_build["corpus_hash"] != source_corpus_hash:
            raise IndexLifecycleError("corpus changed during rebuild; candidate was not promoted")
        payload = binding_payload(client, candidate, QDRANT_COLLECTION)
        if payload.get("source_corpus_hash") != source_corpus_hash:
            raise IndexLifecycleError("candidate has no matching source-hash sentinel")
        if not payload.get("bm25_state_zlib"):
            raise IndexLifecycleError("candidate has no embedded BM25 state")
        quality = validate_candidate_retrieval(client, candidate, documents, payload)
    except Exception:
        logger.exception(
            "Qdrant candidate '%s' failed before alias promotion; serving alias remains '%s'",
            candidate,
            active or "<none>",
        )
        raise

    try:
        promote_alias(client, alias=QDRANT_COLLECTION, collection=candidate, previous=active)
    except Exception:
        logger.exception(
            "Qdrant alias promotion from '%s' to '%s' failed; inspect the server before retrying",
            active or "<none>",
            candidate,
        )
        raise

    try:
        _promote_bm25_state(state_path)
        _write_fresh_status(after_build)
    except Exception:
        # Serving continues from the alias and the BM25 state embedded in the
        # candidate sentinel. A later idempotent rebuild repairs this local
        # bookkeeping; never claim that this already-promoted alias is old.
        logger.exception(
            "Qdrant alias '%s' is promoted to '%s', but post-promotion bookkeeping failed; "
            "rerun the safe rebuild to repair status files",
            QDRANT_COLLECTION,
            candidate,
        )
        raise

    logger.info(
        "Promoted Qdrant alias '%s' from '%s' to '%s' source_hash=%s",
        QDRANT_COLLECTION,
        active or "<none>",
        candidate,
        source_corpus_hash[:12],
    )
    result = {
        **stats,
        "quality_gate": quality,
        "reindexed": True,
        "collection": candidate,
        "previous_collection": active,
        "alias": QDRANT_COLLECTION,
        "source_corpus_hash": source_corpus_hash,
    }
    write_lifecycle_status(
        ok=True,
        reindexed=True,
        collection=candidate,
        source_corpus_hash=source_corpus_hash,
    )
    return result


def main() -> int:  # pragma: no cover - command wiring
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Safely stage and promote a Qdrant rebuild")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild when source hashes drift")
    parser.add_argument("--bootstrap", action="store_true", help="Build the first alias-backed index")
    parser.add_argument("--force", action="store_true", help="Rebuild even when hashes match")
    args = parser.parse_args()
    if not args.rebuild and not args.bootstrap:
        parser.error("specify --rebuild or --bootstrap")
    try:
        sys.stdout.write(json.dumps(rebuild_if_needed(force=args.force), sort_keys=True) + "\n")
        return 0
    except Exception as exc:
        write_lifecycle_status(ok=False, error=str(exc))
        logger.error("Safe Qdrant rebuild failed: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
