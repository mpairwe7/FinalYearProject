"""Epistemic false-premise validation guard for fictitious statutory instruments (G43).

Detects queries that presuppose the existence of a non-existent tax, levy,
duty, or statutory fee (e.g. "URA Digital Nomad Levy", "Moon Tax", "Uganda
Crypto Luxury Duty") where neither URA statutory rate tables nor the retrieved
corpus mention the concept. Rejects them affirmatively rather than permitting
the LLM to synthesize authoritative-looking hallucinated rules and figures.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FalsePremiseResult:
    is_false_premise: bool
    concept: str = ""
    reply: str = ""


# Legitimate Ugandan tax categories and common modifier terms that should NOT
# be considered false premises.
_LEGITIMATE_TAX_MODIFIERS = frozenset({
    "income",
    "individual income",
    "employment income",
    "business income",
    "paye",
    "payroll",
    "payroll tax",
    "pay as you earn",
    "vat",
    "value added",
    "value-added",
    "corporation",
    "corporate",
    "corporate income",
    "company",
    "company income",
    "cit",
    "rental",
    "rental income",
    "capital gains",
    "cgt",
    "withholding",
    "wht",
    "withholding vat",
    "withholding tax",
    "customs",
    "customs duty",
    "import",
    "import duty",
    "export",
    "export levy",
    "common external tariff",
    "cet",
    "excise",
    "excise duty",
    "local excise",
    "local excise duty",
    "stamp",
    "stamp duty",
    "dispute",
    "disputes",
    "disputed",
    "non resident",
    "non-resident",
    "input",
    "input vat",
    "input tax",
    "environmental",
    "environmental levy",
    "infrastructure",
    "infrastructure levy",
    "presumptive",
    "presumptive tax",
    "small business",
    "gaming",
    "lottery",
    "betting",
    "casino",
    "local service",
    "lst",
    "hotel",
    "local government",
    "property",
    "property rates",
    "land",
    "land rates",
    "motor vehicle",
    "vehicle",
    "advance",
    "advance tax",
    "digital services",
    "dst",
    "over the top",
    "ott",
    "social media",
    "road",
    "road user",
    "toll",
    "petroleum",
    "fuel",
    "mining",
    "mineral",
    "direct",
    "indirect",
    "turnover",
    "turnover tax",
    "dividend",
    "interest",
    "royalty",
    "penalty",
    "penalties",
    "fines",
    "fine",
    "interest surcharge",
    "carbon",
    "commercial",
    "residential",
    "additional",
    "additional tax",
    "undisputed",
    "undisputed tax",
    "unpaid",
    "unpaid tax",
    "unpaid duty",
    "unpaid duties",
    "disputed",
    "disputed tax",
    "disputed duty",
    "disputed duties",
    "ongoing",
    "ongoing tax",
    "full ongoing",
    "assessed",
    "estimated",
    "provisional",
    "provisional tax",
    "final",
    "final tax",
    "agro processing",
    "agro-processing",
    "agricultural",
    "digital service",
    "digital services",
    "electronic service",
    "electronic services",
    "mixed supply",
    "mixed supplies",
    "exempt supply",
    "exempt supplies",
    "secondary employment",
})

# Words that indicate actions, grammar, prepositions, and conversational intent
# rather than an asserted specific tax modifier.
_STOP_AND_ACTION_WORDS = frozenset({
    # Actions & verbs
    "pay", "paying", "paid", "file", "filing", "filed", "register", "registering", "registered",
    "declare", "declaring", "declared", "submit", "submitting", "submitted", "calculate",
    "calculating", "calculated", "compute", "computing", "computed", "deduct", "deducting",
    "deducted", "charge", "charging", "charged", "apply", "applying", "applied", "collect",
    "collecting", "collected", "remit", "remitting", "remitted", "avoid", "evade", "evading",
    "learn", "know", "see", "show", "tell", "check", "verify", "get", "got", "help", "want",
    "need", "wish", "like", "prefer", "ask", "start", "stop", "resume",
    "order", "ordering", "ordered", "acquire", "acquiring", "acquired", "buy", "buying", "bought",
    "purchase", "purchasing", "purchased", "obtain", "obtaining", "obtained",
    "use", "using", "used", "utilize", "utilizing", "utilized", "require", "required", "requiring",
    "mandate", "mandated", "mandating", "adopt", "adopting", "adopted", "enforce", "enforcing",
    "enforced", "operate", "operating", "operated", "impose", "imposing", "imposed", "exempt",
    "exempting", "exempted", "issue", "issuing", "issued",
    # Modals and auxiliaries
    "can", "could", "would", "should", "may", "might", "must", "shall", "will",
    "do", "does", "did", "have", "has", "had", "am", "is", "are", "was", "were", "be", "been", "being",
    # Pronouns
    "i", "me", "my", "mine", "we", "us", "our", "ours", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them", "their", "theirs",
    "one", "someone", "anyone", "everyone", "who", "whom", "whose", "what", "which",
    # Prepositions, adverbs, ordinals and determiners
    "how", "why", "when", "where", "to", "for", "about", "on", "in", "at", "by", "from", "of",
    "with", "into", "through", "during", "before", "after", "above", "below", "under", "up",
    "down", "off", "over", "again", "further", "then", "once", "here", "there", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "and", "or", "but", "if",
    "own", "same", "so", "than", "too", "very", "s", "t", "just", "don", "now", "as", "per",
    "first", "second", "third", "initial", "primary", "main", "major", "general", "direct", "indirect", "next", "prior",
    # Generic context nouns & qualifiers
    "online", "offline", "portal", "ura", "uganda", "ugandan", "government", "official",
    "law", "laws", "act", "acts", "policy", "rate", "rates", "table", "tables", "system",
    "code", "due", "payable", "applicable", "pending", "outstanding", "total", "standard",
    "general", "statutory", "new", "current", "annual", "monthly", "year", "years", "month",
    "the", "a", "an", "this", "that", "these", "those",
    "additional", "undisputed", "disputed", "unpaid", "ongoing", "full", "assessed", "estimated",
    "provisional", "final", "delayed", "overdue", "excess", "refund", "balance", "claim",
    "credits", "input", "output", "payroll", "escape", "threshold", "thresholds", "avoid", "evade", "contract", "contracts", "limit", "limits", "attract",
    "legal", "status", "voluntary", "disclose", "undisclosed", "carrier", "importing", "manufacturing",
    # Luganda & Swahili question/functional particles
    "omuwendo", "gwa", "eri", "ku", "bimeka", "biki", "ani", "diba", "okusasula",
    "okumenya", "okusaba", "kya", "bya", "kiwango", "cha", "kodi", "ushuru",
    "wa", "ya", "za", "ni", "asilimia", "ngapi", "kulipa", "kujisajili", "katika",
    "nchini", "jinsi", "mu", "yangu", "yako", "yetu", "zao", "inauza", "kuuza",
    "bidhaa", "zenye", "zote", "kila", "wetu", "wangu", "hawawezi", "wanaweza",
    "kampuni", "ambayo", "hapa", "gwange", "gyange", "bizinensi", "waffe", "lwawo",
    "ebbaluwa", "ebaluwa", "omulimu", "ogusooka",
})

_CANDIDATE_PATTERNS = (
    re.compile(
        r"\b(?P<modifier>[a-zA-Z0-9\s\-]{2,40}?)\s+"
        r"(?P<kind>tax|taxes|levy|levies|duty|duties|tariff|tariffs|surcharge|surcharges)\b",
        re.IGNORECASE,
    ),
)


_INTERROGATIVE_PREFIX = re.compile(
    r"^(?:is\s+there(?:\s+an?|\s+any)?|are\s+there(?:\s+any)?|was\s+there|were\s+there|"
    r"what\s+is(?:\s+an?|\s+the)?|what\s+are(?:\s+the)?|tell\s+me\s+about(?:\s+the)?)\s+",
    re.IGNORECASE,
)

_NON_TAX_NOUN_FOLLOWERS = re.compile(
    r"^(table|tables|bracket|brackets|rate|rates|band|bands|return|returns|"
    r"law|laws|act|acts|code|codes|assessment|assessments|credit|credits|"
    r"invoice|invoices|receipt|receipts|officer|officers|portal|head|heads)\b",
    re.IGNORECASE,
)


def _extract_candidate_tax_concepts(query: str) -> list[tuple[str, str]]:
    """Extract candidate (modifier, kind) pairs from *query*."""
    text = (query or "").strip()
    # Consume recognized interrogative prefixes so they never bleed into modifiers
    normalized_text = _INTERROGATIVE_PREFIX.sub("", text)
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for target in (normalized_text, text):
        for pattern in _CANDIDATE_PATTERNS:
            for match in pattern.finditer(target):
                following_text = target[match.end():].lstrip()
                if _NON_TAX_NOUN_FOLLOWERS.match(following_text):
                    continue
                raw_mod = match.group("modifier").strip()
                kind = match.group("kind").strip().lower()
                # Normalize kind to singular for comparison
                if kind.endswith("ies"):
                    base_kind = kind[:-3] + "y"
                elif kind.endswith("es") and kind not in ("taxes",):
                    base_kind = kind[:-2]
                elif kind.endswith("s"):
                    base_kind = kind[:-1]
                else:
                    base_kind = kind

                words = [w.lower() for w in re.split(r"[\s\-]+", raw_mod) if w]
                # Scan backwards from the noun to isolate the contiguous modifier words
                # that qualify the tax name (stopping at action verbs, modals, or pronouns)
                modifier_words = []
                for w in reversed(words):
                    if w in _STOP_AND_ACTION_WORDS:
                        break
                    modifier_words.insert(0, w)

                if not modifier_words:
                    continue

                cleaned_mod = " ".join(modifier_words)
                if cleaned_mod in seen or len(cleaned_mod) < 2:
                    continue
                seen.add(cleaned_mod)
                candidates.append((cleaned_mod, base_kind))
    return candidates


def check_false_premise(
    query: str,
    hits: list[dict[str, Any]] | None = None,
) -> FalsePremiseResult:
    """Validate whether *query* presupposes a non-existent statutory tax/levy.

    Returns a FalsePremiseResult with is_false_premise=True if an asserted tax
    or levy is not known in Ugandan tax law and completely absent from the
    retrieved knowledge base.
    """
    candidates = _extract_candidate_tax_concepts(query)
    if not candidates:
        return FalsePremiseResult(is_false_premise=False)

    corpus_text = ""
    if hits:
        corpus_parts = []
        for h in hits:
            corpus_parts.append(str(h.get("text") or h.get("answer") or ""))
            corpus_parts.append(str(h.get("question") or ""))
            corpus_parts.append(str(h.get("source") or ""))
        corpus_text = " ".join(corpus_parts).lower()

    for clean_mod, kind in candidates:
        # Check against legitimate taxes
        if clean_mod in _LEGITIMATE_TAX_MODIFIERS:
            continue

        # If the candidate ends with a known legitimate statutory base
        # (e.g. "agro-processing corporate income" ends with "corporate income"),
        # it is a sector qualification of a legitimate tax, not an invented tax.
        if any(
            clean_mod.endswith(legit)
            for legit in (
                "corporate income",
                "company income",
                "individual income",
                "employment income",
                "business income",
                "rental income",
                "income",
                "vat",
                "value added",
                "paye",
                "rental",
                "withholding",
                "excise",
                "customs",
                "stamp duty",
            )
        ):
            continue

        # Check whether the exact concept phrase appears
        # in the retrieved corpus or official rate tables
        found_in_corpus = (
            clean_mod in corpus_text
            or f"{clean_mod} {kind}" in corpus_text
        )
        if found_in_corpus:
            continue

        # Concept is not a known Ugandan tax and absent from retrieved passages
        words_cap = " ".join(w.capitalize() for w in clean_mod.split())
        concept_display = f"{words_cap} {kind.capitalize()}"

        # Tailor the guidance depending on whether the query asks about remote/digital work
        is_digital_remote = any(
            term in clean_mod for term in ("digital nomad", "nomad", "remote", "freelanc")
        ) or any(term in (query or "").lower() for term in ("digital nomad", "nomad"))

        if is_digital_remote:
            reply = (
                f"Under current Ugandan tax laws and Uganda Revenue Authority (URA) regulations, "
                f"there is no official \"{concept_display}\". The URA does not administer or impose a specific tax by this name.\n\n"
                f"If you are earning income in Uganda (including remote employment, digital freelancing, or foreign-sourced income), "
                f"standard income tax provisions apply based on your tax residency status. "
                f"Please consult an official URA office or tax advisor for guidance on your specific obligations."
            )
        else:
            reply = (
                f"Under current Ugandan tax laws and Uganda Revenue Authority (URA) regulations, "
                f"there is no official \"{concept_display}\". The URA does not administer or impose such a tax or levy.\n\n"
                f"Legitimate statutory tax heads administered by the URA include Income Tax (PAYE, Corporate Income Tax, Presumptive Tax), "
                f"Value Added Tax (VAT), Withholding Tax (WHT), Rental Tax, Stamp Duty, and Customs and Excise Duties. "
                f"Please consult official URA guidelines or an authorized tax officer for statutory requirements."
            )

        safe_query = query[:120].replace("\r", " ").replace("\n", " ").strip()
        safe_concept = concept_display.replace("\r", " ").replace("\n", " ").strip()
        logger.warning(
            "epistemic false-premise guard triggered for query '%s' (concept: %s)",
            safe_query,
            safe_concept,
        )
        return FalsePremiseResult(
            is_false_premise=True,
            concept=concept_display,
            reply=reply,
        )

    return FalsePremiseResult(is_false_premise=False)


# ---------------------------------------------------------------------------
# Phase F.5: Data-driven premise and fact contradiction checks
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Contradiction:
    """A statutory contradiction between a taxpayer's stated premise and tool-verified law."""

    code: str
    user_premise: str
    statutory_truth: str
    basis: str

    def format_bullet(self) -> str:
        return f"Your premise: {self.user_premise}. Under {self.basis}, {self.statutory_truth}"


def _contradiction_residency(facts: Any, tool_results: dict[str, Any]) -> list[Contradiction]:
    res = tool_results.get("determine_residency")
    if not isinstance(res, dict) or not res.get("ok"):
        return []
    claim = getattr(facts, "residency_claim", None)
    status = res.get("status")
    thresh = res.get("residency_days_threshold", 183)
    days = getattr(facts, "days_in_uganda", None)
    contract_months = getattr(facts, "contract_months", None)
    basis = "Income Tax Act (Cap 338), s.9 and URA Taxation Handbook FY2025-26 (resident individual definition)"

    out: list[Contradiction] = []
    if claim == "non_resident" and status == "resident":
        out.append(
            Contradiction(
                code="residency_claim_non_resident_but_resident",
                user_premise="the individual is a non-resident for tax purposes",
                statutory_truth=(
                    f"they are a Ugandan tax RESIDENT because physical presence of {days} days "
                    f"meets or exceeds the {thresh}-day threshold in any 12-month period commencing "
                    "or ending during the year of income (or meets permanent-home / 122-day average / "
                    "Government posting criteria)."
                ),
                basis=basis,
            )
        )
    elif claim == "non_resident" and status == "undetermined" and res.get("likely_resident"):
        out.append(
            Contradiction(
                code="residency_claim_non_resident_but_likely_resident",
                user_premise=f"the individual is a non-resident on an {contract_months}-month contract",
                statutory_truth=(
                    f"an {contract_months}-month contract (~{(contract_months or 0) * 30} days) means "
                    f"they are LIKELY RESIDENT if physically present in Uganda for {thresh} days or "
                    "more in any 12-month period commencing or ending in the year of income; confirm "
                    "the exact number of days they will be physically present in Uganda."
                ),
                basis=basis,
            )
        )
    elif claim == "resident" and (status == "non_resident" or res.get("likely_non_resident")):
        out.append(
            Contradiction(
                code="residency_claim_resident_but_non_resident",
                user_premise="the individual is a Ugandan tax resident",
                statutory_truth=(
                    f"physical presence of {days} days is below the {thresh}-day threshold, so they "
                    "are a NON-RESIDENT unless they have a permanent home in Uganda, meet the 122-day "
                    "3-year average test, or are a Uganda Government employee posted abroad."
                ),
                basis=basis,
            )
        )
    return out


def _contradiction_vat_threshold(facts: Any, tool_results: dict[str, Any]) -> list[Contradiction]:
    vat_reg = tool_results.get("check_vat_registration")
    if not isinstance(vat_reg, dict) or not vat_reg.get("ok"):
        return []
    req = vat_reg.get("registration_required")
    turnover = vat_reg.get("annual_turnover", 0)
    threshold = vat_reg.get("threshold", 300_000_000)
    fy = vat_reg.get("fiscal_year", "FY2026-27")
    basis = f"Value Added Tax Act (Cap 349), s.7 ({fy} threshold UGX {threshold:,.0f})"

    out: list[Contradiction] = []
    if getattr(facts, "vat_registered", None) is False and req is True:
        out.append(
            Contradiction(
                code="vat_claimed_below_threshold_but_above",
                user_premise="annual turnover is below the compulsory VAT registration threshold",
                statutory_truth=(
                    f"annual taxable turnover of UGX {turnover:,.0f} meets or exceeds the {fy} "
                    f"compulsory VAT registration threshold of UGX {threshold:,.0f}, so VAT "
                    "registration is compulsory."
                ),
                basis=basis,
            )
        )
    elif getattr(facts, "vat_registered", None) is True and req is False:
        out.append(
            Contradiction(
                code="vat_claimed_compulsory_but_below_threshold",
                user_premise="compulsory VAT registration is required at this turnover",
                statutory_truth=(
                    f"annual taxable turnover of UGX {turnover:,.0f} is below the {fy} compulsory "
                    f"VAT registration threshold of UGX {threshold:,.0f} (headroom UGX "
                    f"{vat_reg.get('headroom', 0):,.0f}); registration is not compulsory, though "
                    "voluntary registration is available."
                ),
                basis=basis,
            )
        )
    return out


def _contradiction_individual_rental_expenses(facts: Any, tool_results: dict[str, Any]) -> list[Contradiction]:
    if not getattr(facts, "claims_rental_expense_deduction", False):
        return []
    rental_res = tool_results.get("calculate_rental_tax")
    landlord_type = (
        (rental_res or {}).get("landlord_type")
        if isinstance(rental_res, dict)
        else getattr(facts, "entity_type", None)
    )
    if landlord_type == "company":
        return []
    return [
        Contradiction(
            code="individual_rental_expense_deduction_disallowed",
            user_premise="an individual landlord can deduct repair, maintenance, or interest expenses from rental income",
            statutory_truth=(
                "individual landlords CANNOT deduct any expenses from rental income; individual "
                "rental tax is 12% on gross annual rental income above the UGX 2,820,000 threshold "
                "(only corporate landlords may deduct allowable expenses capped at 50% of gross rent)."
            ),
            basis="Income Tax Act (Cap 338), s.5 and s.22(1)(c)",
        )
    ]


def _contradiction_wht_employment_contract(facts: Any, tool_results: dict[str, Any]) -> list[Contradiction]:
    wht_srv = tool_results.get("wht_non_resident_service")
    has_emp = bool(getattr(facts, "employment_contract", False))
    if not has_emp and not (isinstance(wht_srv, dict) and wht_srv.get("is_employment_contract")):
        return []
    if "withholding_tax" not in (getattr(facts, "tax_heads", None) or []) and wht_srv is None:
        return []
    return [
        Contradiction(
            code="wht_service_excludes_employment_contract",
            user_premise="15% non-resident service withholding tax applies to an employment contract",
            statutory_truth=(
                "a Ugandan-sourced service contract does NOT include an employment contract; "
                "remuneration under an employment contract is taxed under PAYE, not 15% WHT."
            ),
            basis="Income Tax Act (Cap 338), s.85 and URA Withholding Tax Guide (Withholding-Tax-FY-2024-25-1.pdf)",
        )
    ]


def _contradiction_vat_input_without_efris(facts: Any, tool_results: dict[str, Any]) -> list[Contradiction]:
    if getattr(facts, "has_efris_einvoice", None) is not False:
        return []
    elig = tool_results.get("vat_input_claim_eligibility")
    if isinstance(elig, dict) and elig.get("eligible") is True:
        return []
    return [
        Contradiction(
            code="vat_input_requires_efris_einvoice",
            user_premise="claiming input VAT credit using a manual paper invoice/receipt",
            statutory_truth=(
                "a VAT-registered buyer CANNOT claim input VAT credit on a manual paper invoice; "
                "a valid EFRIS e-invoice or e-receipt with a Fiscal Document Number (FDN) is required."
            ),
            basis="Value Added Tax Act (Cap 349), s.28 and URA EFRIS Handbook FY2026-27",
        )
    ]


_CONTRADICTION_RULES = (
    _contradiction_residency,
    _contradiction_vat_threshold,
    _contradiction_individual_rental_expenses,
    _contradiction_wht_employment_contract,
    _contradiction_vat_input_without_efris,
)


def check_fact_contradictions(
    facts: Any,
    tool_results: dict[str, Any] | None = None,
) -> list[Contradiction]:
    """Run all declarative contradiction rules over *facts* and *tool_results*."""
    if facts is None:
        return []
    results = tool_results or {}
    contradictions: list[Contradiction] = []
    seen_codes: set[str] = set()
    for rule_fn in _CONTRADICTION_RULES:
        for item in rule_fn(facts, results):
            if item.code not in seen_codes:
                seen_codes.add(item.code)
                contradictions.append(item)
    return contradictions
