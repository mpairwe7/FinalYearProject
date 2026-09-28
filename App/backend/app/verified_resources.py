"""Official URA links offered beside an answer or a guided workflow step.

Every entry below is a real page on an official URA host, confirmed live
(HTTP 200, page title matching the entry) on :data:`VERIFIED_ON`. Entries point
at the official *page* for a task — the place URA publishes the current form —
rather than at a guessed file path: URA re-uploads forms under dated
``/storage/YYYY/MM/`` paths, so a page link stays right when a form is revised
and a file link silently breaks. Run ``python -m app.verified_resources --check``
before moving :data:`VERIFIED_ON` forward.

Three rules hold for every link this module emits:

* it is ``https`` on a URA host (:func:`is_authoritative_ura_url`), checked when
  the registry is built, so a bad entry fails the test suite rather than
  reaching a taxpayer;
* it carries no query string built from the conversation — a TIN, NIN or PRN in
  a URL ends up in browser history and server logs, and no URA page reads such
  parameters anyway;
* it is attached only to a substantive answer (:func:`resources_for_turn`) —
  never to a refusal, an abstention, a greeting or a clarifying question.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .topics import classify_topic

logger = logging.getLogger(__name__)

#: The date every URL in :data:`_RESOURCES` was last confirmed live.
VERIFIED_ON = "2026-09-28"
CURRENT_FISCAL_YEAR = "FY2026-27"

#: Resources shown beside one answer. More than three is a reading list.
MAX_RESOURCES = 3

ResourceKind = Literal["online_form", "downloadable_form", "statutory_source", "guide"]

#: Hosts outside ``*.ura.go.ug`` that URA itself links to as official.
_EXTRA_OFFICIAL_HOSTS = frozenset({"singlewindow.go.ug"})

#: Answer modes that must not carry links: the assistant declined, found
#: nothing, is asking a question back, or is making small talk. A refusal with
#: "official forms" under it reads as an answer.
_NO_RESOURCE_MODES = frozenset(
    {
        "abstained",
        "blocked",
        "clarification",
        "contact_channels",
        "conversational",
        "escalated",
        "false_premise_rejected",
        "greeting",
        "officer_reply",
        "out_of_jurisdiction",
        "out_of_scope",
    }
)


def is_authoritative_ura_url(url: str) -> bool:
    """True only for an ``https`` URL whose host is URA's own.

    Reads the parsed *hostname*, never a prefix of the netloc: in
    ``https://portal.ura.go.ug:443@evil.example/`` everything before the ``@``
    is userinfo and the browser goes to ``evil.example``. Credentials and
    non-default ports are refused outright.
    """
    if not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return False
    if port not in (None, 443):
        return False
    host = (parts.hostname or "").rstrip(".")
    return host == "ura.go.ug" or host.endswith(".ura.go.ug") or host in _EXTRA_OFFICIAL_HOSTS


def _normalise(text: str) -> str:
    """Lower-case, with hyphens and underscores read as spaces."""
    return re.sub(r"[\s\-_]+", " ", (text or "").lower()).strip()


@dataclass(frozen=True)
class OfficialResource:
    """One official URA page, and the whole-word phrases that call for it."""

    id: str
    title: str
    kind: ResourceKind
    url: str
    description: str
    phrases: tuple[str, ...] = ()
    format: Literal["web", "pdf"] = "web"
    citation: str = ""
    checklist: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not is_authoritative_ura_url(self.url):
            raise ValueError(f"{self.id}: {self.url!r} is not an https URA URL")
        if urllib.parse.urlsplit(self.url).query:
            raise ValueError(f"{self.id}: official links carry no query string")
        # Whole words only, with an optional plural: "tin" must not fire on
        # "getting", nor "vat" on "private".
        compiled = tuple(re.compile(rf"\b{re.escape(_normalise(p))}s?\b") for p in self.phrases)
        object.__setattr__(self, "patterns", compiled)

    def to_payload(self, entities: dict[str, Any] | None = None) -> dict[str, Any]:
        prefilled: dict[str, str] = {}
        if entities:
            for k in ("tin", "prn", "nin", "amount", "period"):
                if entities.get(f"{k}s"):
                    prefilled[k] = str(entities[f"{k}s"][0])
                elif k in entities:
                    prefilled[k] = str(entities[k])

        return {
            "id": self.id,
            "title": self.title,
            "type": self.kind,
            "format": self.format,
            "url": self.url,
            "description": self.description,
            "citation": self.citation,
            "checklist": list(self.checklist),
            "source_domain": urllib.parse.urlsplit(self.url).hostname or "",
            "verified_on": VERIFIED_ON,
            "is_verified": True,
            "verification_badge": "Official URA Verified",
            "effective_year": CURRENT_FISCAL_YEAR,
            "last_verified_at": VERIFIED_ON,
            "prefilled_params": prefilled,
        }


_RESOURCES: tuple[OfficialResource, ...] = (
    # Registration ------------------------------------------------------
    OfficialResource(
        id="tin_application",
        title="Get a TIN",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/tin-application/",
        description="URA's starting point for every kind of TIN registration.",
        phrases=("get a tin", "apply for a tin", "tin application"),
    ),
    OfficialResource(
        id="tin_individual",
        title="TIN registration — individuals",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/tin-application/tin-registration-individual/individual-tin-application/",
        description="URA's online portal form for registering an individual TIN.",
        phrases=("individual tin", "tin for an individual", "personal tin"),
    ),
    OfficialResource(
        id="tin_non_individual",
        title="TIN registration — companies and organisations",
        kind="online_form",
        url="https://ura.go.ug/en/tin-registration-non-individual/non-individual-tin-application/",
        description="URA's online portal form for registering a company, partnership, NGO or other entity.",
        phrases=(
            "company tin",
            "business tin",
            "organisation tin",
            "organization tin",
            "ngo tin",
            "non individual",
        ),
    ),
    OfficialResource(
        id="tin_instant",
        title="Instant TIN application",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/tin-application/instant-tin-application/instant-tin-registration/",
        description="Apply for a TIN instantly online using your National ID (NIN) details.",
        phrases=("instant tin",),
    ),
    OfficialResource(
        id="tin_search",
        title="Search TIN",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/tin-application/search-tin/",
        description="Look up a TIN you have forgotten or want to confirm.",
        phrases=(
            "search tin",
            "forgot my tin",
            "forgotten my tin",
            "lost my tin",
            "find my tin",
            "confirm my tin",
            "verify a tin",
        ),
    ),
    OfficialResource(
        id="tin_forms",
        title="E-registration forms",
        kind="downloadable_form",
        url="https://ura.go.ug/en/domestic-taxes/tin-application/download-online-forms/",
        description="Official registration forms, published by URA in their current version.",
        phrases=("registration form", "tin form", "e registration form"),
    ),
    OfficialResource(
        id="starter_pack",
        title="Taxpayer registration starter pack",
        kind="guide",
        url="https://ura.go.ug/download-category/taxpayer-registration-starter-pack/",
        description="URA's onboarding guides for newly registered taxpayers.",
        phrases=("starter pack", "new taxpayer", "newly registered"),
    ),
    # Returns ------------------------------------------------------------
    OfficialResource(
        id="file_return",
        title="File a return",
        kind="online_form",
        url="https://ura.go.ug/en/etax-login/",
        description="Log in to the eTax portal to file returns (VAT, PAYE, Income Tax, Nil returns) and manage your tax account.",
        phrases=(
            "file a return",
            "file my return",
            "filing a return",
            "file returns",
            "tax return",
            "nil return",
            "vat return",
            "paye return",
            "income tax return",
            "submit a return",
            "submit my return",
            "return filing",
        ),
    ),
    OfficialResource(
        id="return_forms",
        title="Online return forms",
        kind="downloadable_form",
        url="https://ura.go.ug/en/domestic-taxes/returns/download-online-return-forms/",
        description="The return templates you complete offline and upload through eTax.",
        phrases=("return form", "return template", "excel template", "offline return"),
    ),
    OfficialResource(
        id="etax_login",
        title="URA eTax portal",
        kind="online_form",
        url="https://ura.go.ug/en/etax-login/",
        description="Sign in to eTax to file returns, register tax types and manage your account.",
        phrases=("etax", "e tax", "portal login", "log in to the portal", "sign in to the portal"),
    ),
    # Payments -----------------------------------------------------------
    OfficialResource(
        id="make_payment",
        title="Make a payment (Generate PRN)",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/make-a-payment/generate-a-payment-slip/",
        description="Register a tax payment online to generate a PRN, then pay by bank, mobile money or card.",
        phrases=(
            "prn",
            "payment registration number",
            "make a payment",
            "payment slip",
            "generate a prn",
            "mobile money",
            "how do i pay",
        ),
    ),
    OfficialResource(
        id="payment_status",
        title="View payment status",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/make-a-payment/view-payment-status/",
        description="Check whether a PRN payment has reached URA.",
        phrases=(
            "payment status",
            "check my payment",
            "confirm my payment",
            "payment not reflecting",
            "verify payment",
            "verify my payment",
        ),
    ),
    OfficialResource(
        id="reactivate_prn",
        title="Reactivate an expired PRN",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/make-a-payment/reactivate-expired-prn/",
        description="Bring an expired PRN back into use instead of registering the payment again.",
        phrases=("expired prn", "reactivate prn", "reactivate a prn", "prn expired", "prn has expired"),
    ),
    # Objections --------------------------------------------------------
    OfficialResource(
        id="object_to_assessment",
        title="Object to a tax assessment",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/objection-appeals/object-to-tax-assessment/",
        description="Lodge a written objection to an assessment you disagree with.",
        citation="Tax Procedures Code Act, section 24",
        # Worded after URA's own page and the objections FAQ corpus.
        checklist=(
            "Lodge within 45 days of receiving the notice of assessment",
            "State the grounds of your objection",
            "Attach the documents that support your position",
        ),
        phrases=(
            "objection",
            "object to",
            "dispute an assessment",
            "dispute my assessment",
            "disagree with the assessment",
            "disagree with my assessment",
            "contest the assessment",
        ),
    ),
    OfficialResource(
        id="adr_form",
        title="Application for alternative dispute resolution",
        kind="downloadable_form",
        format="pdf",
        url="https://ura.go.ug/storage/2023/12/Application-Form-for-Alternative-Dispute-Resolution.pdf",
        description="URA's application form to settle a tax dispute through ADR.",
        phrases=("alternative dispute resolution", "adr"),
    ),
    # EFRIS ---------------------------------------------------------------
    OfficialResource(
        id="efris_portal",
        title="EFRIS portal",
        kind="online_form",
        url="https://ura.go.ug/en/efris-login/",
        description="Sign in to the official EFRIS portal to issue fiscal invoices and receipts and manage your account.",
        phrases=("efris", "electronic fiscal", "e invoice", "e invoicing", "fiscal invoice", "fiscal receipt", "efris login", "efris portal"),
    ),
    OfficialResource(
        id="efris_registration",
        title="EFRIS registration",
        kind="online_form",
        url="https://ura.go.ug/en/efris/efris-registration/",
        description="Register as a VAT or designated business on the EFRIS electronic invoicing system.",
        phrases=("efris registration", "register for efris", "register efris"),
    ),
    OfficialResource(
        id="efris_fdn_validation",
        title="Check an invoice or receipt (FDN validation)",
        kind="online_form",
        url="https://ura.go.ug/en/efris/fdn-validation/",
        description="Confirm an EFRIS invoice or receipt is genuine using its Fiscal Document Number.",
        phrases=(
            "fdn",
            "fiscal document number",
            "verify an invoice",
            "verify invoice",
            "verify a receipt",
            "verify receipt",
            "fake invoice",
            "fake receipt",
            "genuine invoice",
            "genuine receipt",
            "kakasa",
        ),
    ),
    OfficialResource(
        id="efris_invoicing_guide",
        title="EFRIS: issuing invoices and receipts",
        kind="guide",
        url="https://ura.go.ug/en/efris/invoice-receipt-issuance/",
        description="URA's walkthrough for issuing fiscal invoices and receipts in EFRIS.",
        phrases=("issue an invoice", "issue a receipt", "issue invoice", "issue receipt"),
    ),
    OfficialResource(
        id="efris_guides",
        title="EFRIS guides",
        kind="guide",
        url="https://ura.go.ug/en/efris/",
        description="URA's EFRIS help pages: registration, invoicing, stock and reports.",
        phrases=("efris registration", "register for efris"),
    ),
    OfficialResource(
        id="efris_handbook",
        title="The EFRIS handbook (2024)",
        kind="guide",
        format="pdf",
        url="https://ura.go.ug/storage/2024/07/THE-EFRIS-HANDBOOK-2024.pdf",
        description="URA's guide to registering for and using EFRIS.",
        phrases=("efris handbook", "efris guide", "efris manual"),
    ),
    # Customs -------------------------------------------------------------
    OfficialResource(
        id="single_window",
        title="Uganda Electronic Single Window",
        kind="online_form",
        url="https://singlewindow.go.ug/",
        description="The government portal for import and export declarations, permits and clearance.",
        phrases=(
            "single window",
            "customs clearance",
            "clear goods",
            "clearing goods",
            "customs declaration",
            "import declaration",
            "bill of entry",
        ),
    ),
    OfficialResource(
        id="customs_systems",
        title="Customs systems overview",
        kind="guide",
        url="https://ura.go.ug/en/customs-systems/",
        description="The systems URA uses for customs, including ASYCUDA World.",
        phrases=("asycuda", "customs system"),
    ),
    OfficialResource(
        id="bwims_portal",
        title="Bonded Warehouse Information Management System (BWIMS)",
        kind="online_form",
        url="https://ura.go.ug/en/bwims/",
        description="URA's system for bonded warehouse management, cargo tracking and delivery orders.",
        phrases=("bwims", "bwim", "bonded warehouse", "bonded cargo", "warehouse management", "customs warehouse"),
    ),
    # Motor vehicles -------------------------------------------------------
    OfficialResource(
        id="motor_vehicle",
        title="Motor vehicle services",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/motor-vehicle/",
        description="Vehicle registration, transfer of ownership and logbook services.",
        phrases=(
            "motor vehicle",
            "logbook",
            "log book",
            "number plate",
            "vehicle transfer",
            "transfer a vehicle",
            "transfer my car",
            "car transfer",
            "vehicle registration",
        ),
    ),
    OfficialResource(
        id="motor_vehicle_forms",
        title="Motor vehicle forms",
        kind="downloadable_form",
        url="https://ura.go.ug/download-category/manual-motor-vehicle-forms/",
        description="URA's current motor vehicle application and transfer forms.",
        phrases=("vehicle transfer form", "motor vehicle form", "logbook form"),
    ),
    OfficialResource(
        id="motor_vehicle_search",
        title="Search motor vehicle details",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/motor-vehicle/search-vehicle-details-application/",
        description="Look up and verify vehicle registration, ownership, and logbook details online.",
        phrases=("search vehicle", "verify vehicle", "vehicle search", "check motor vehicle", "check logbook"),
    ),
    # Support, Education & Portals -----------------------------------------
    OfficialResource(
        id="touchpoint_portal",
        title="URA Touchpoint (Client Support)",
        kind="online_form",
        url="https://touchpoint.ura.go.ug/",
        description="URA's client support portal for logging inquiries, tracking support tickets, and service requests.",
        phrases=("touchpoint", "support ticket", "client support", "customer care portal", "contact centre portal", "ura touchpoint"),
    ),
    OfficialResource(
        id="elearning_portal",
        title="URA eLearning Platform",
        kind="guide",
        url="https://elearning.ura.go.ug/",
        description="Official URA eLearning platform for online tax courses, webinars and taxpayer training modules.",
        phrases=("elearning", "e-learning", "tax academy", "tax course", "tax training", "learn tax", "tax education portal"),
    ),
    OfficialResource(
        id="procurement_portal",
        title="URA Procurement Management System",
        kind="online_form",
        url="https://ura.go.ug/en/opportunities/tenders/procurement-management-system/",
        description="Official portal for URA procurement opportunities, tender documents, and supplier bidding.",
        phrases=("procurement", "tenders", "tender", "bid", "bidding", "ura tender"),
    ),
    # Other domestic-tax services ------------------------------------------
    OfficialResource(
        id="get_refund",
        title="Get a refund",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/get-a-refund/",
        description="Claim back tax that was overpaid or paid in error.",
        phrases=("refund", "overpaid", "overpayment"),
    ),
    OfficialResource(
        id="tax_clearance",
        title="Tax clearance certificate",
        kind="online_form",
        url="https://ura.go.ug/en/tax-clearance/",
        description="Apply for a tax clearance certificate (TCC).",
        phrases=("tax clearance", "tcc", "clearance certificate"),
    ),
    OfficialResource(
        id="stamp_duty",
        title="Stamp duty",
        kind="online_form",
        url="https://ura.go.ug/en/domestic-taxes/stamp-duty/",
        description="Assess, pay for and print stamp duty certificates.",
        phrases=("stamp duty",),
    ),
    OfficialResource(
        id="digital_tax_stamps",
        title="Digital tax stamps (DTS)",
        kind="online_form",
        url="https://ura.go.ug/en/dts-login/",
        description="Sign in to the DTS portal to manage, affix and activate digital tax stamps.",
        phrases=("digital tax stamp", "dts", "tax stamp", "dts login", "dts portal"),
    ),
    # Law and reference ------------------------------------------------------
    OfficialResource(
        id="laws_and_acts",
        title="Laws, acts and regulations",
        kind="statutory_source",
        url="https://ura.go.ug/download-category/laws-and-acts/",
        description="The tax laws URA administers, as published by URA.",
        phrases=(
            "income tax act",
            "vat act",
            "value added tax act",
            "tax procedures code",
            "excise duty act",
            "stamp duty act",
            "customs management act",
            "tax appeals tribunal act",
            "legislation",
            "statute",
        ),
    ),
    OfficialResource(
        id="vat_amendment_2023",
        title="Value Added Tax (Amendment) Act, 2023",
        kind="statutory_source",
        url="https://ura.go.ug/en/download/value-added-tax-amendment-act-2023/",
        description="The 2023 amendments to the Value Added Tax Act.",
        phrases=("vat amendment", "value added tax amendment"),
    ),
    OfficialResource(
        id="taxation_handbook",
        title="Taxation handbook",
        kind="guide",
        url="https://ura.go.ug/download-category/taxation-handbook/",
        description="URA's handbook on Uganda's domestic and customs taxes.",
        phrases=("taxation handbook", "tax handbook"),
    ),
)

_BY_ID: dict[str, OfficialResource] = {r.id: r for r in _RESOURCES}
_ORDER: dict[str, int] = {r.id: i for i, r in enumerate(_RESOURCES)}

#: Conversation topics (``app.topics``) and workflow ids, each mapped to the
#: pages that serve it, best first.
_TOPIC_RESOURCES: dict[str, tuple[str, ...]] = {
    "tin_registration": ("tin_individual", "tin_non_individual", "tin_instant", "tin_forms"),
    "tin_procedure_help": ("tin_individual", "tin_non_individual", "tin_instant", "tin_forms"),
    "return_filing": ("file_return", "return_forms", "etax_login"),
    "vat_filing": ("file_return", "return_forms", "etax_login"),
    "paye": ("file_return", "return_forms", "etax_login"),
    "cit": ("file_return", "return_forms", "etax_login"),
    "wht": ("file_return", "return_forms", "etax_login"),
    "rental_tax": ("file_return", "return_forms", "etax_login"),
    "payment": ("make_payment", "payment_status", "reactivate_prn"),
    "payment_assistance": ("make_payment", "payment_status", "reactivate_prn"),
    "objection": ("object_to_assessment", "adr_form"),
    "objection_or_dispute": ("object_to_assessment", "adr_form"),
    "efris": ("efris_portal", "efris_registration", "efris_fdn_validation", "efris_handbook"),
    "audit_invoice_compliance": ("efris_portal", "efris_fdn_validation", "efris_handbook"),
    "import_goods": ("single_window", "customs_systems", "bwims_portal"),
    "import_vehicle": ("single_window", "motor_vehicle", "motor_vehicle_search"),
    "customs_clearance": ("single_window", "customs_systems", "bwims_portal"),
    "warehousing": ("bwims_portal", "customs_systems"),
    "motor_vehicle": ("motor_vehicle_search", "motor_vehicle", "motor_vehicle_forms"),
    "touchpoint": ("touchpoint_portal",),
    "education": ("elearning_portal", "starter_pack", "taxation_handbook"),
    "procurement": ("procurement_portal",),
    "refund": ("get_refund",),
    "tcc": ("tax_clearance",),
    "stamp_duty": ("stamp_duty",),
    "excise_duty": ("digital_tax_stamps",),
}


def resource_ids() -> frozenset[str]:
    """Every id a workflow step may name under ``resources`` or ``portal_action``."""
    return frozenset(_BY_ID)


def get_resource(resource_id: str) -> dict[str, Any] | None:
    """The payload for one registry entry, or ``None`` for an unknown id."""
    res = _BY_ID.get(resource_id)
    return res.to_payload() if res else None


def resolve_resource_ids(ids: Iterable[object], *, context: str = "") -> list[dict[str, Any]]:
    """Payloads for the ids a workflow step names; unknown ids are logged and dropped."""
    resolved: list[dict[str, Any]] = []
    for raw in ids:
        payload = get_resource(raw) if isinstance(raw, str) else None
        if payload is None:
            logger.warning("%s: unknown official resource %r ignored", context or "resources", raw)
            continue
        resolved.append(payload)
    return resolved


def portal_action_for(resource_id: str, *, context: str = "") -> dict[str, str]:
    """The ``{label, url}`` button a workflow step offers, from a registry id."""
    res = _BY_ID.get(resource_id)
    if res is None:
        logger.warning("%s: unknown portal_action %r ignored", context or "workflow", resource_id)
        return {}
    return {"label": res.title, "url": res.url}


def get_verified_resources(
    query: str,
    *,
    topic: str = "",
    tax_type: str = "",
    intent: str = "",
    entities: dict[str, Any] | None = None,
    max_items: int = MAX_RESOURCES,
) -> list[dict[str, Any]]:
    """Official pages for *query*, best first.

    A whole-word phrase in the query counts most; *topic* (a conversation topic
    or workflow id) adds its mapped pages behind them. With neither, nothing is
    returned — no link is better than a wrong one.
    """
    effective_topic = topic or tax_type or intent
    text = _normalise(query)
    scores: dict[str, float] = {}
    for rank, rid in enumerate(_TOPIC_RESOURCES.get(effective_topic, ())):
        scores[rid] = scores.get(rid, 0.0) + 2.0 - 0.1 * rank
    if text:
        for res in _RESOURCES:
            hits = sum(1 for pattern in res.patterns if pattern.search(text))
            if hits:
                scores[res.id] = scores.get(res.id, 0.0) + 3.0 * hits
    ordered = sorted(scores, key=lambda rid: (-scores[rid], _ORDER[rid]))
    return [_BY_ID[rid].to_payload(entities) for rid in ordered[: max(0, max_items)]]


def resources_for_turn(
    message: str,
    result: Mapping[str, Any],
    *,
    rewritten: str = "",
) -> list[dict[str, Any]]:
    """The links to show under one assistant turn.

    An active workflow shows only what its current step declares, so the forms
    appear at the step that needs them rather than under every question. A
    finished workflow falls back to the pages for its task. Anything else is
    matched on the taxpayer's own words (plus the English *rewritten* query, so
    a Luganda or Kiswahili question can match too) — never on the persisted
    conversation topic, which outlives the question that set it.
    """
    if str(result.get("retrieval_mode") or "") in _NO_RESOURCE_MODES:
        return []
    workflow = result.get("workflow")
    if isinstance(workflow, Mapping):
        declared = workflow.get("resources") or []
        if declared:
            return list(declared)[:MAX_RESOURCES]
        if workflow.get("status") == "completed":
            return get_verified_resources("", topic=str(workflow.get("id") or ""))
        return []
    text = f"{message}\n{rewritten}" if rewritten and rewritten != message else message
    detected = classify_topic(text)
    return get_verified_resources(text, topic=detected.topic_id if detected else "")


def check_links(timeout: float = 30.0) -> list[tuple[str, str, str]]:
    """Fetch every registry URL; return ``(id, url, problem)`` for each failure."""
    failures: list[tuple[str, str, str]] = []
    for res in _RESOURCES:
        if not res.url.startswith("https://"):
            failures.append((res.id, res.url, "Scheme must be https://"))
            continue
        request = urllib.request.Request(res.url, headers={"User-Agent": "ura-chatbot-link-check/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 # noqa: S310 — audited https URA hosts only
                if response.status != 200:
                    failures.append((res.id, res.url, f"HTTP {response.status}"))
        except Exception as exc:  # noqa: BLE001 — every failure is reported, none is fatal
            failures.append((res.id, res.url, f"{type(exc).__name__}: {exc}"))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fetch every registry URL and report failures")
    args = parser.parse_args(argv)
    if not args.check:
        for res in _RESOURCES:
            print(f"{res.id:24} {res.url}")
        return 0
    failures = check_links()
    for rid, url, problem in failures:
        print(f"FAIL {rid:24} {url}  {problem}")
    print(f"{len(_RESOURCES) - len(failures)}/{len(_RESOURCES)} links live (registry verified {VERIFIED_ON})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
