"""Decide whether a crawl may be proposed for the corpus (ADR-0023).

The crawler writes files; this reads them and answers one of three ways:

``pass``
    Nothing a reviewer needs to weigh: a person still merges it.
``review``
    Real changes that deserve reading: statutory figures moved, pages were
    withdrawn, new documents arrived, a page was quarantined.
``hold``
    Do not propose this at all. Either the crawl did not really happen (the site
    stopped answering) or the site looks wrong (a mass rewrite, a mass removal,
    instructions planted across several pages, older pages coming back).

Crawled text used to flow from a website to taxpayers with nobody looking: the
job pushed straight to ``dev``, and the next unrelated merge shipped it. A
defaced government page is not a far-fetched event, and PoisonedRAG showed five
planted texts per question are enough. Content checks alone are not an answer
either, so this gate is one layer: provenance comes from the crawler, and the
decision stays with a person (OWASP ASI06: "require human review for high-risk
actions", "detect suspicious updates or frequencies"; NITA-U NISP 2026 s.5.2.4.3:
"protect AI models and training datasets from poisoning attacks").

    python -m app.crawl_gate evaluate --before DIR --after Data/crawl --pdf-dir Data/pdfs --out DIR
    python -m app.crawl_gate slo --crawl-dir Data/crawl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .crawl_corpus import CRAWL_STATE_NAME, CorpusSelection, _clean, _parse_time, load_crawl_state, review_pages

logger = logging.getLogger(__name__)

#: Above these shares a run is held. They trip on what a compromise or a broken
#: extractor looks like, and a one-off rebuild passes ``--baseline``.
MAX_REMOVED_SHARE = float(os.getenv("CRAWL_GATE_MAX_REMOVED_SHARE", "0.10"))
MAX_CHANGED_SHARE = float(os.getenv("CRAWL_GATE_MAX_CHANGED_SHARE", "0.30"))
#: One bad page is a page to read. Several suggest the site itself is compromised.
MAX_QUARANTINED = int(os.getenv("CRAWL_GATE_MAX_QUARANTINED", "2"))
#: Share of page fetches the site must answer for the run to mean anything.
MIN_ANSWERED_SHARE = float(os.getenv("CRAWL_GATE_MIN_ANSWERED_SHARE", "0.5"))
#: Shares say nothing about a tiny corpus.
MIN_PAGES_FOR_SHARES = 20

#: The full pass runs on the last day of each month, so a healthy gap peaks near
#: 31, plus a week for someone to merge. Change checks have no objective here: on
#: a quiet day they rightly publish nothing, so their date on dev says nothing.
CRAWL_SLO_FULL_DAYS = int(os.getenv("CRAWL_SLO_FULL_DAYS", "38"))
#: A few older responses are a stale cache. Many mean the site went backwards,
#: which is what a restored backup or a rollback looks like.
MAX_OLDER_RESPONSES = int(os.getenv("CRAWL_GATE_MAX_OLDER_RESPONSES", "5"))

_ANSWERED_OUTCOMES = frozenset({"ok", "not_modified", "not_found", "gone"})
_UNANSWERED_OUTCOMES = frozenset({"network_error", "server_error", "rate_limited", "blocked", "challenge"})

#: How a statutory figure is written: money with a currency, grouped thousands, a percentage.
_FIGURE = re.compile(
    r"(?:UGX|Ushs?\.?|Shs\.?)\s*\d[\d,]*(?:\.\d+)?|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\s?%",
    re.IGNORECASE,
)
_REPORT_LIMIT = 40


def figures(text: str) -> set[str]:
    """The money amounts and percentages in *text*, in one spelling."""
    return {re.sub(r"\s+", "", match.group(0)).upper() for match in _FIGURE.finditer(text or "")}


@dataclass
class GateReport:
    verdict: str = "pass"
    tier: str = ""
    baseline: bool = False
    #: Whether the corpus would differ, which decides if a notices run is worth a PR.
    has_content_changes: bool = False
    hold_reasons: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    run: dict[str, Any] = field(default_factory=dict)
    selected_before: int = 0
    selected_after: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    figure_changes: list[dict[str, Any]] = field(default_factory=list)
    #: Text changed although the publisher's date did not move: nobody announced it.
    unannounced: list[str] = field(default_factory=list)
    #: Newer capture kept because the site served an older version.
    kept_newer: list[str] = field(default_factory=list)
    quarantined: list[dict[str, str]] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    pdfs_accepted: list[str] = field(default_factory=list)
    pdfs_rejected: list[dict[str, str]] = field(default_factory=list)
    pdf_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """The pull request body: what a reviewer must look at, first."""
        run = self.run
        outcomes = run.get("outcomes") or {}
        lines = [
            f"## Crawl gate: **{self.verdict}**",
            "",
            f"Tier `{self.tier or 'unknown'}`, {run.get('fetches', 0)} requests, stopped by "
            f"`{run.get('stopped_by', 'unknown')}`, complete: `{run.get('complete', False)}`.",
            f"Corpus pages {self.selected_before} → {self.selected_after}: "
            f"{len(self.added)} added, {len(self.changed)} changed, {len(self.removed)} removed.",
            "",
        ]
        if self.baseline:
            lines += ["> **Baseline run.** Mass-change limits were waived by the operator.", ""]
        for title, items in (("Held because", self.hold_reasons), ("Needs a reader because", self.review_reasons)):
            if items:
                lines += [f"### {title}", *(f"- {item}" for item in items), ""]
        if self.figure_changes:
            lines += [
                f"### Statutory figures changed on {len(self.figure_changes)} page(s)",
                "Check these against the Act or the rate table before merging.",
                "",
                "| Page | Removed | Added |",
                "| --- | --- | --- |",
            ]
            for item in self.figure_changes[:_REPORT_LIMIT]:
                gone = ", ".join(item["removed"][:8]) or "none"
                new = ", ".join(item["added"][:8]) or "none"
                lines.append(f"| {item['url']} | {gone} | {new} |")
            lines.append("")
        lines += self._url_section("Kept the newer capture: the site served an older version", self.kept_newer)
        lines += self._url_section("Text changed with no new publisher date", self.unannounced)
        lines += self._url_section("Withdrawn or excluded", [f"{r['url']} ({r['reason']})" for r in self.removed])
        lines += self._url_section("Quarantined", [f"{q['url']} ({q['reason']})" for q in self.quarantined])
        lines += self._url_section("Text cut at the size limit", self.truncated)
        lines += self._url_section("New in the corpus", self.added)
        lines += self._url_section("Text changed, no figures moved", self._quiet_changes())
        if self.pdfs_accepted or self.pdfs_rejected or self.pdf_notes:
            lines += [
                "### Documents",
                *(f"- accepted `{name}`" for name in self.pdfs_accepted),
                *(f"- **rejected** `{item['pdf']}`: {item['reason']}" for item in self.pdfs_rejected),
                *(f"- note: {note}" for note in self.pdf_notes),
                "",
            ]
        if outcomes:
            lines += ["<details><summary>Fetch outcomes</summary>", "", "```json", json.dumps(outcomes, indent=1), "```", "</details>", ""]
        return "\n".join(lines) + "\n"

    def _quiet_changes(self) -> list[str]:
        moved = {item["url"] for item in self.figure_changes}
        return [url for url in self.changed if url not in moved]

    @staticmethod
    def _url_section(title: str, items: list[str]) -> list[str]:
        if not items:
            return []
        lines = [f"### {title} ({len(items)})", *(f"- {item}" for item in items[:_REPORT_LIMIT])]
        if len(items) > _REPORT_LIMIT:
            lines.append(f"- and {len(items) - _REPORT_LIMIT} more, listed in `report.json`")
        return [*lines, ""]


def _selection(crawl_dir: Path) -> tuple[CorpusSelection, dict[str, dict[str, Any]]]:
    pages_dir = crawl_dir / "pages"
    if not pages_dir.is_dir():
        return CorpusSelection(), {}
    review = review_pages(pages_dir)
    return review, {_clean(page.get("url")): page for _path, page in review.selected}


def _exclusion_for(url: str, ledger: dict[str, Any]) -> str:
    entry = ledger.get(url) or {}
    return _clean(entry.get("status")) or "no longer selected"


def _judge_run(run: dict[str, Any], report: GateReport) -> None:
    """Hold a run that says nothing about the site, whatever files it left."""
    if not run:
        report.hold_reasons.append("The ledger records no crawl run.")
        return
    if not run.get("fetches"):
        report.hold_reasons.append("The crawl made no requests.")
    primary = _clean(run.get("primary_host")) or "ura.go.ug"
    unreachable = sorted(host for host, state in (run.get("robots") or {}).items() if state == "unreachable")
    if primary in unreachable:
        report.hold_reasons.append(f"robots.txt was unreachable on {primary}: RFC 9309 forbids crawling it.")
    # A secondary host being down costs its own pages, not the whole run.
    others = [*(host for host in unreachable if host != primary), *(run.get("hosts_down") or [])]
    if sorted(set(others) - {primary}):
        report.review_reasons.append(f"Not read this run: {', '.join(sorted(set(others) - {primary}))}.")
    outcomes = run.get("outcomes") or {}
    answered = sum(count for name, count in outcomes.items() if name in _ANSWERED_OUTCOMES)
    unanswered = sum(count for name, count in outcomes.items() if name in _UNANSWERED_OUTCOMES)
    total = answered + unanswered
    if total and answered / total < MIN_ANSWERED_SHARE:
        report.hold_reasons.append(f"Only {answered} of {total} page requests were answered: the site was not really read.")
    if run.get("complete") is False and not report.hold_reasons:
        report.review_reasons.append(f"The run stopped early (`{run.get('stopped_by')}`): what it found is real, but it is not the whole site.")


def _judge_shares(report: GateReport, *, baseline: bool) -> None:
    base = report.selected_before
    if baseline or base < MIN_PAGES_FOR_SHARES:
        return
    removed, changed = len(report.removed) / base, len(report.changed) / base
    if removed > MAX_REMOVED_SHARE:
        report.hold_reasons.append(
            f"{len(report.removed)} of {base} pages would leave the corpus ({removed:.0%}, limit {MAX_REMOVED_SHARE:.0%}). "
            "Check for a site restructure or an outage before accepting this."
        )
    if changed > MAX_CHANGED_SHARE:
        report.hold_reasons.append(
            f"{len(report.changed)} of {base} pages changed ({changed:.0%}, limit {MAX_CHANGED_SHARE:.0%}). "
            "Check for defacement or an extraction regression."
        )


def _is_unannounced(before_entry: dict[str, Any], after_entry: dict[str, Any]) -> bool:
    """Whether text changed while the sitemap says nothing did since it was last read.

    Ordinary edits move ``lastmod``. A change that does not is either a rendering
    change or an edit made around the CMS, and the second is how tampering looks.
    """
    listed = _parse_time(after_entry.get("sitemap_lastmod"))
    verified = _parse_time(before_entry.get("last_verified_at"))
    return listed is not None and verified is not None and listed <= verified


def vet_new_pdfs(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    pdf_dir: Path,
    report: GateReport,
) -> None:
    """Apply the upload checks to what the crawler brought in.

    ``ml/`` may not import these guards, so a crawled PDF was never inspected or
    scanned on its way to the corpus. A rejected file is deleted from the
    candidate and forgotten by the ledger, so the next run looks at it afresh.
    """
    from .malware_scan import MalwareRejected, scan_bytes
    from .pdf_guards import CORPUS_PDF_LIMITS, PdfRejected, inspect_pdf_bytes, validate_pdf_header

    known = before_state.get("pdf_hashes") or {}
    hashes: dict[str, Any] = after_state.get("pdf_hashes") or {}
    for digest in sorted(set(hashes) - set(known)):
        name = _clean(hashes[digest].get("filename"))
        path = pdf_dir / name
        if not name or Path(name).name != name or not path.is_file():
            hashes.pop(digest)
            report.pdfs_rejected.append({"pdf": name or digest, "reason": "file missing from the candidate"})
            continue
        data = path.read_bytes()
        try:
            validate_pdf_header(data)
            inspection = inspect_pdf_bytes(data, limits=CORPUS_PDF_LIMITS)
            scan = scan_bytes(data, filename=name)
        except (PdfRejected, MalwareRejected) as exc:
            path.unlink()
            hashes.pop(digest)
            report.pdfs_rejected.append({"pdf": name, "reason": str(exc)[:200]})
            continue
        report.pdfs_accepted.append(name)
        report.pdf_notes.extend(f"{name}: {warning}" for warning in inspection.warnings)
        if scan.skipped:
            report.pdf_notes.append(f"{name}: malware scan skipped ({scan.detail})")


def evaluate(
    before_dir: Path,
    after_dir: Path,
    *,
    pdf_dir: Path | None = None,
    baseline: bool = False,
) -> GateReport:
    """Compare the corpus a crawl would produce with the one it starts from."""
    before_review, before_pages = _selection(before_dir)
    after_review, after_pages = _selection(after_dir)
    pages_dir = after_dir / "pages"
    after_state = load_crawl_state(pages_dir)
    before_state = load_crawl_state(before_dir / "pages")
    ledger = after_state.get("visited") or {}
    before_ledger = before_state.get("visited") or {}
    runs = after_state.get("runs") or []
    run = runs[-1] if runs else {}

    report = GateReport(
        tier=_clean(run.get("tier")),
        baseline=baseline,
        run=run,
        selected_before=len(before_pages),
        selected_after=len(after_pages),
        quarantined=after_review.quarantined,
    )
    report.added = sorted(set(after_pages) - set(before_pages))
    report.removed = [
        {"url": url, "reason": _exclusion_for(url, ledger)} for url in sorted(set(before_pages) - set(after_pages))
    ]
    for url in sorted(set(before_pages) & set(after_pages)):
        old, new = _clean(before_pages[url].get("text")), _clean(after_pages[url].get("text"))
        if " ".join(old.split()) == " ".join(new.split()):
            continue
        report.changed.append(url)
        if _is_unannounced(before_ledger.get(url) or {}, ledger.get(url) or {}):
            report.unannounced.append(url)
        old_figures, new_figures = figures(old), figures(new)
        if old_figures != new_figures:
            report.figure_changes.append(
                {"url": url, "removed": sorted(old_figures - new_figures), "added": sorted(new_figures - old_figures)}
            )
    report.truncated = sorted(url for url, page in after_pages.items() if page.get("truncated"))
    started = _clean(run.get("started_at"))
    report.kept_newer = sorted(
        url for url, entry in ledger.items() if started and _clean(entry.get("older_response_at")) >= started
    )

    _judge_run(run, report)
    _judge_shares(report, baseline=baseline)
    if len(report.quarantined) > MAX_QUARANTINED:
        report.hold_reasons.append(
            f"{len(report.quarantined)} pages hold instructions aimed at a model (limit {MAX_QUARANTINED}): the site may be compromised."
        )
    if len(report.kept_newer) > MAX_OLDER_RESPONSES:
        report.hold_reasons.append(
            f"{len(report.kept_newer)} pages came back older than the copy held (limit {MAX_OLDER_RESPONSES}): "
            "the site may have been restored from a backup."
        )
    if pdf_dir is not None:
        vet_new_pdfs(before_state, after_state, pdf_dir, report)

    if report.kept_newer:
        report.review_reasons.append(f"The site served an older version of {len(report.kept_newer)} page(s); the newer copy was kept.")
    if report.unannounced:
        report.review_reasons.append(f"{len(report.unannounced)} page(s) changed with no new publisher date.")
    if report.figure_changes:
        report.review_reasons.append(f"Statutory figures moved on {len(report.figure_changes)} page(s).")
    if report.removed:
        report.review_reasons.append(f"{len(report.removed)} page(s) would leave the corpus.")
    if report.quarantined:
        report.review_reasons.append(f"{len(report.quarantined)} page(s) were quarantined.")
    if report.truncated:
        report.review_reasons.append(f"{len(report.truncated)} page(s) were cut at the size limit.")
    if report.pdfs_accepted or report.pdfs_rejected:
        report.review_reasons.append(f"{len(report.pdfs_accepted) + len(report.pdfs_rejected)} new document(s) to look at.")
    if baseline:
        report.review_reasons.append("Baseline run: compare a sample of pages by eye.")

    report.has_content_changes = bool(
        report.added or report.removed or report.changed or report.pdfs_accepted or report.pdfs_rejected
    )
    report.verdict = "hold" if report.hold_reasons else "review" if report.review_reasons else "pass"
    return report


def check_slo(crawl_dir: Path, *, now: datetime | None = None) -> list[str]:
    """What is wrong with the age of the crawl, as a person would say it.

    Reads the ledger on the checked-out branch, so it measures what can actually
    ship: an unmerged crawl proposal does not count. Replaces a nightly check
    that compared two files absent from every checkout and so could not fail.
    """
    state = load_crawl_state(crawl_dir / "pages")
    moment = now or datetime.now(timezone.utc)
    last = _parse_time(state.get("last_full_run_at"))
    if last is None:
        return ["No complete full crawl is on record."]
    age = moment - last
    if age > timedelta(days=CRAWL_SLO_FULL_DAYS):
        return [f"The last complete full crawl merged here ran {age.days} days ago (limit {CRAWL_SLO_FULL_DAYS})."]
    return []


def write_candidate(after_dir: Path, pdf_dir: Path, out_dir: Path, report: GateReport) -> None:
    """Assemble what the publish job may commit, plus the report.

    The job that parsed untrusted pages holds no write token, and the job with
    the token runs nothing from this directory: it only copies named data files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report.to_dict(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(report.to_markdown(), encoding="utf-8")
    if report.verdict == "hold":
        return
    shutil.copytree(after_dir / "pages", out_dir / "crawl" / "pages", dirs_exist_ok=True)
    shutil.copy2(after_dir / CRAWL_STATE_NAME, out_dir / "crawl" / CRAWL_STATE_NAME)
    if report.pdfs_accepted:
        (out_dir / "pdfs").mkdir(exist_ok=True)
        for name in report.pdfs_accepted:
            shutil.copy2(pdf_dir / name, out_dir / "pdfs" / name)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Judge a crawl before it can reach the corpus")
    commands = parser.add_subparsers(dest="command", required=True)
    judge = commands.add_parser("evaluate", help="Compare a crawl with its starting point")
    judge.add_argument("--before", type=Path, required=True)
    judge.add_argument("--after", type=Path, required=True)
    judge.add_argument("--pdf-dir", type=Path, default=None)
    judge.add_argument("--out", type=Path, required=True)
    judge.add_argument("--baseline", action="store_true", help="Waive mass-change limits for a deliberate rebuild")
    slo = commands.add_parser("slo", help="Fail when the crawl on this branch is too old")
    slo.add_argument("--crawl-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "slo":
        problems = check_slo(args.crawl_dir)
        for problem in problems:
            sys.stderr.write(f"{problem}\n")
        return 1 if problems else 0

    report = evaluate(args.before, args.after, pdf_dir=args.pdf_dir, baseline=args.baseline)
    # A rejected PDF changed the ledger, and the candidate must carry that.
    if args.pdf_dir is not None:
        _rewrite_pdf_hashes(args.after, report)
    write_candidate(args.after, args.pdf_dir or Path(), args.out, report)
    sys.stdout.write(f"{report.verdict}\n")
    return 0


def _rewrite_pdf_hashes(after_dir: Path, report: GateReport) -> None:
    """Forget PDFs the vetting refused, so the next run reconsiders them."""
    if not report.pdfs_rejected:
        return
    path = after_dir / CRAWL_STATE_NAME
    state = json.loads(path.read_text(encoding="utf-8"))
    refused = {item["pdf"] for item in report.pdfs_rejected}
    state["pdf_hashes"] = {
        digest: entry for digest, entry in (state.get("pdf_hashes") or {}).items() if _clean(entry.get("filename")) not in refused
    }
    path.write_text(json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
