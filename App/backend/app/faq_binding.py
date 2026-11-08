"""Whether a retrieved FAQ answers this question, and which equivalent FAQ to promote (G486).

Moved out of ``service.py`` so retrieval-side code and the agentic graph share one
definition without importing the orchestrator.
"""

from __future__ import annotations

import functools
import os
import re
from typing import Any

from .query import extract_question_span

_STOP_WORDS = frozenset(
    "a an the is are was were be been am do does did will would shall should "
    "can could may might must have has had of in on at to for with by from "
    "and or not no nor but so if then than that this these those it its i me "
    "my we our you your he she they them their what which who whom how when "
    "where why all each every any some out up down off into over much many more "
    "allowed able happens happen instead without".split()
)


# Keyword retrieval is deliberately conservative.  A FAQ answer can contain
# common words such as "tax", "return", or "registration" while answering a
# completely different question.  Those accidental matches used to be passed
# to the LLM as authoritative context, which made an unrelated question look
# grounded because the generated answer matched the wrong passage.
_FAQ_QUERY_STOP_WORDS = _STOP_WORDS | frozenset(
    {
        "about",
        "could",
        "current",
        "details",
        "information",
        "know",
        "like",
        "please",
        "tell",
        "want",
        "work",
    }
)


_FAQ_TERM_ALIASES = {
    "applications": "application",
    "deadlines": "deadline",
    "documents": "document",
    "filing": "file",
    "filings": "file",
    "imports": "import",
    "penalties": "penalty",
    "payments": "payment",
    "rates": "rate",
    "returns": "return",
    "taxes": "tax",
    "thresholds": "threshold",
    "vehicles": "vehicle",
    "translation": "translate",
    # Vehicles & Transport domain synonyms
    "lorry": "vehicle",
    "lorries": "vehicle",
    "truck": "vehicle",
    "trucks": "vehicle",
    "car": "vehicle",
    "cars": "vehicle",
    "bus": "vehicle",
    "buses": "vehicle",
    "motorcycle": "vehicle",
    "motorcycles": "vehicle",
    "bodaboda": "vehicle",
    "boda": "vehicle",
    "capacity": "capacity",
    "loading": "capacity",
    "carrying": "capacity",
    "carry": "capacity",
    # Documents, Papers & Records
    "papers": "document",
    "paperwork": "document",
    "forms": "form",
    "records": "record",
    "books": "record",
    "bookkeeping": "record",
    "accounts": "record",
    # Business & Entities
    "enterprise": "company",
    "enterprises": "company",
    "firm": "company",
    "firms": "company",
    "corporation": "company",
    # Operations
    "operating": "operate",
    "operation": "operate",
    # Free Zones, EPZ & Customs Offences
    "freeport": "epz",
    "freeports": "epz",
    "freezone": "epz",
    "freezones": "epz",
    "permission": "authority",
    "permit": "authority",
    "authorization": "authority",
    "move": "removing",
    "moving": "removing",
    "remove": "removing",
    "taking": "removing",
    "offense": "offence",
    # Language references
    "french": "language",
    "german": "language",
    "chinese": "language",
    "arabic": "language",
    # Multilingual Tax Lemmas (Luganda)
    "omusolo": "tax",
    "emisolo": "tax",
    "ebitundu": "rate",
    "kikumi": "percent",
    "obupangisa": "rental",
    "mayumba": "rental",
    "nnyumba": "rental",
    "ebiwandiiko": "document",
    "okwewandiisa": "registration",
    "ssekinnoomu": "individual",
    "bannansi": "resident",
    "obusuubuzi": "business",
    "abasuubuzi": "business",
    "magoba": "income",
    "bizinensi": "business",
    "ssente": "amount",
    "akawumbi": "million",
    "obukadde": "million",
    "kikuubo": "trader",
    "emmotoka": "vehicle",
    "pikipiki": "motorcycle",
    "eppikipiki": "motorcycle",
    "obwannannyini": "ownership",
    "okukyusa": "transfer",
    "obutonde": "environmental",
    "ekibonerezo": "penalty",
    "ebibonerezo": "penalties",
    "okwemulugunya": "objection",
    "ebyamaguzi": "goods",
    "ebisale": "fees",
    "ebizibiti": "documents",
    "ensawo": "baggage",
    "okuyingiza": "import",
    "okufulumya": "export",
    "omusaala": "salary",
    "omwezi": "month",
    "olunaku": "deadline",
    # Multilingual Tax Lemmas (Swahili)
    "kodi": "tax",
    "ushuru": "tax",
    "ongezeko": "vat",
    "thamani": "vat",
    "majengo": "rental",
    "kupangisha": "rental",
    "upangishaji": "rental",
    "makampuni": "company",
    "asilimia": "rate",
    "kiwango": "rate",
    "viwango": "rate",
    "usajili": "registration",
    "kujisajili": "registration",
    "hati": "document",
    "pingamizi": "objection",
    "adhabu": "penalty",
    "marejesho": "return",
    "wakaazi": "resident",
    "mshahara": "paye",
    "mapato": "income",
    "bidhaa": "goods",
    "huduma": "service",
    "gari": "vehicle",
    "magari": "vehicles",
    "umiliki": "ownership",
    "uhamisho": "transfer",
    "kuhamisha": "transfer",
    "mazingira": "environmental",
    "ankara": "invoice",
    "risiti": "receipt",
    "forodha": "customs",
    "mizigo": "cargo",
    "mzigo": "cargo",
    "msamaha": "exemption",
    "kuagiza": "import",
    "kusafirisha": "export",
    "mfumo": "system",
    "hifadhi": "warehouse",
    "muda": "deadline",
    "tarehe": "deadline",
    "siku": "days",
    "nambari maalum": "personalized",
    "nambari": "number plate",
    "gharama": "fee",
    "kadi": "logbook",
    "ubao": "plate",
    "spea": "spare parts",
    "vifaa": "parts",
}


def _resolve_alias_chains(aliases: dict[str, str]) -> None:
    """Point every alias at its final term, in place (CodeRabbit on #705).

    ``magari`` named ``vehicles``, itself an alias, so a Luganda or Swahili
    query and its English FAQ ended on different terms and the row scored zero.
    """
    for key in list(aliases):
        seen = {key}
        target = aliases[key]
        while target in aliases and aliases[target] != target and target not in seen:
            seen.add(target)
            target = aliases[target]
        aliases[key] = target


_resolve_alias_chains(_FAQ_TERM_ALIASES)


#: expansion phrase -> acronym, mined from the loaded corpus by
#: :func:`mine_faq_acronyms` and installed by :func:`install_faq_acronyms`.
#: Empty until a FAQ index is loaded, which leaves :func:`faq_subject_terms`
#: behaving exactly as it did before acronym folding existed.
_FAQ_ACRONYM_EXPANSIONS: dict[str, str] = {}


_FAQ_ACRONYM_RE: re.Pattern[str] | None = None


def fold_acronyms(text: str) -> str:
    """Rewrite known expansions to their acronym so both spellings compare equal.

    Applied to the query and to the FAQ row alike, so this cannot introduce an
    asymmetry: "what is pay as you earn" and "What is PAYE?" reduce to the same
    single term, and "What is PAYE (Pay As You Earn)?" reduces to it twice.
    """
    if _FAQ_ACRONYM_RE is None:
        return text
    return _FAQ_ACRONYM_RE.sub(lambda m: _FAQ_ACRONYM_EXPANSIONS[m.group(0).lower()], text)


# Minimum share of the query an FAQ must cover before it may answer.
#
# 0.58 looks high, and the translated-retrieval path makes it look higher still:
# machine translation is paraphrastic, so a translated question covers the
# corpus less well than a native one, and 7 of the 12 Luganda golden questions
# land at 0.33-0.57 and are refused. "What is EFRIS and how does it work?"
# scores 0.57 and is refused even though the corpus has EFRIS entries.
#
# It was measured rather than guessed, and it should not be lowered. Against
# off-domain questions that borrow money/government vocabulary — the ones a
# coverage metric actually confuses — the score distributions overlap:
#
#   coverage   in-domain 0.33-0.91      off-domain 0.00-0.59
#   bm25       in-domain 7.78-15.27     off-domain 3.03-10.40
#
# Neither signal separates them, together or apart. End to end, lowering the
# floor took wrong answers from 1 of 6 off-domain questions to 5 of 6:
# "Who is the president of Uganda?" answered from the AEO scheme FAQ, "How do
# I pay my rent to my landlord?" answered with URA payment instructions. The
# downstream abstention guard does not catch them. Six extra correct answers
# are not worth four extra confident wrong ones from a tax authority.
#
# Rewriting the translated query instead was also measured: it lifts exactly
# one question over the line and pushes "What is withholding tax?" from 0.91
# to 0.50, under it.
#
# test_faq_match_gate.py pins this. If you raise recall here, raise it with a
# signal that separates — reranking or a judge over the candidates — not by
# moving the floor.
_FAQ_MATCH_MIN = float(os.getenv("FAQ_MATCH_MIN", "0.58"))


_FAQ_MATCH_RELATIVE = float(os.getenv("FAQ_MATCH_RELATIVE", "0.82"))


# Minimum share of an FAQ question's own terms that the query must account for
# before a fully-contained query counts as being about that FAQ's subject.
_FAQ_SUBJECT_FOCUS_MIN = float(os.getenv("FAQ_SUBJECT_FOCUS_MIN", "0.34"))


# Canonical FAQ rows reach the retriever as ``csv`` from the in-memory loader
# and as ``faq_jsonl`` from the indexed corpus.  Both must pass the same
# intent-binding gate; listing only one silently exempts the other.
_FAQ_DOC_TYPES = {"csv", "faq_jsonl"}


def faq_terms(text: str) -> set[str]:
    """Return normalized, query-bearing terms for FAQ binding.

    This is intentionally smaller and stricter than BM25's vocabulary.  BM25
    remains responsible for ranking, while these terms answer a separate
    question: does this FAQ actually contain the subject and intent the user
    asked about?

    Acronyms are *not* folded here — see :func:`faq_subject_terms`, which is
    the narrower view used to decide what a question is about.
    """
    terms: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if raw in _FAQ_QUERY_STOP_WORDS:
            continue
        terms.add(_FAQ_TERM_ALIASES.get(raw, raw))
    return terms


@functools.lru_cache(maxsize=4096)
def faq_subject_terms(text: str) -> frozenset[str]:
    """:func:`faq_terms` with acronyms and their expansions folded together.

    Two questions are asked of an FAQ row and they want different views of the
    text.  *Coverage* — "does this row talk about what I asked?" — is served by
    redundancy: "Authorized Economic Operator (AEO)" offering four ways to
    match is a feature, and collapsing it makes every unmatched term cost
    proportionally more (measured: folding coverage too pushed "What is the
    Authorized Economic Operator programme?" under the floor over the
    programme/program spelling alone).  *Subject focus* — "is this row about
    what I asked?" — is the opposite: an acronym spelled twice is one subject,
    and counting it as four is what made the gate reject a definition asked by
    its own acronym.  So the folding lives here and nowhere else.

    Cached because ``faq_match_score`` calls this once per FAQ row per query —
    516 regex substitutions a turn, which measured at +6 ms on ``_simple_search``
    uncached.  The corpus questions are a fixed set, so the cache is warm after
    one turn; :func:`install_faq_acronyms` clears it when the mapping changes.
    Returns a ``frozenset`` so a caller cannot mutate the cached value.
    """
    return frozenset(faq_terms(fold_acronyms(text or "")))


def faq_match_score(query: str, entry: dict[str, Any], locale: str | None = None) -> float:
    """Score whether an FAQ row is bound to *query*, independently of BM25.

    The score combines coverage in the full Q&A with how well the FAQ's own
    question balances against the query.  Requiring both prevents a generic
    answer paragraph from making an unrelated row look relevant.  Deadline
    questions also need temporal evidence; otherwise a topic-only hit such as
    "capital gains" can be mistaken for a filing deadline answer.

    The question term is an F1, not plain coverage, because coverage alone
    cannot separate rows once a short query is fully covered.  "What is VAT?"
    is wholly contained in "Is EFRIS optional for non-VAT taxpayers?", so both
    that row and the real definition scored 1.0 and the relative cutoff had
    nothing to work with.  Weighing precision alongside recall demotes a row
    whose question is far broader than what was asked, and is a no-op when the
    two agree.
    """
    query_terms = faq_terms(query)
    if not query_terms:
        return 0.0

    # G35: coverage is judged against the terms that *constitute the question*,
    # not every term the taxpayer supplied.
    asked_terms = faq_terms(extract_question_span(query)) or query_terms

    loc = locale or entry.get("_matched_locale")
    q_loc = str(entry.get(f"question_{loc}") or "") if loc and loc != "en" else ""
    a_loc = str(entry.get(f"answer_{loc}") or "") if loc and loc != "en" else ""
    if q_loc and a_loc:
        q_raw = q_loc
        a_raw = a_loc
    else:
        q_raw = str(entry.get("question") or "")
        a_raw = str(entry.get("answer") or "")
        text_raw = str(entry.get("text") or "")
        if (not q_raw or not a_raw) and text_raw.startswith("Question: ") and "\nAnswer: " in text_raw:
            parts = text_raw[len("Question: ") :].split("\nAnswer: ", 1)
            if not q_raw:
                q_raw = parts[0].strip()
                entry["question"] = q_raw
            if not a_raw:
                a_raw = parts[1].strip()
                entry["answer"] = a_raw

    question_terms = faq_terms(q_raw)
    answer_terms = faq_terms(a_raw)
    body_terms = question_terms | answer_terms

    # Timing is an intent, not merely a topic.  Do not answer a deadline/due
    # date question from a passage that never states a timing rule.
    timing_terms = {
        "deadline", "due", "date", "period",
        "obudde", "olunaku", "ekkomo", "mwezi",
        "tarehe", "muda", "kipindi",
    }
    timing_evidence = {
        "deadline", "due", "date", "period", "monthly", "annual",
        "obudde", "olunaku", "ekkomo", "mwezi",
        "tarehe", "muda", "kipindi",
    }
    if query_terms & timing_terms and not (
        timing_evidence & body_terms or _TIMING_EXPRESSION_RE.search(f"{q_raw} {a_raw}")
    ):
        return 0.0

    body_coverage = len(asked_terms & body_terms) / len(asked_terms)

    # Focus is judged on subjects, not spellings: "What is PAYE?" against "What
    # is PAYE (Pay As You Earn)?" is one subject asked once, not one term out
    # of four.  See :func:`faq_subject_terms` for why coverage above keeps the
    # unfolded view.
    # Deliberately the *whole* query, unlike the coverage denominator above.
    # Narrowing this too was tried and reverted: it made recall trivially 1.0
    # for any row containing the question's one remaining subject, which trips
    # the focus gate below and hard-zeroes the row. Five FAQ rows stopped
    # retrieving their own question — "Bona fide changing residence – what is
    # exempt?" narrows to "what is exempt?", and the subject the taxpayer asked
    # about is the half that gets dropped. Focus is about how well the row's
    # question matches everything asked; coverage is about what the row can
    # fairly be charged for. They want different views of the query.
    asked_subjects = faq_subject_terms(query)
    question_subjects = faq_subject_terms(q_raw)
    matched = len(asked_subjects & question_subjects)
    question_recall = matched / len(asked_subjects) if asked_subjects else 0.0
    question_precision = matched / len(question_subjects) if question_subjects else 0.0

    # Subject focus is an intent too.  A query wholly contained in a much
    # broader question is topic-adjacent, not answered by it: "What is VAT?"
    # sits inside "Is EFRIS optional for non-VAT taxpayers?", which is about
    # EFRIS.  Containment alone maxes out every coverage signal, so this is
    # gated rather than merely down-weighted.  The bar stays low so that a
    # short query against a legitimately more specific question — "VAT
    # registration" against "When is VAT registration compulsory for a
    # manufacturer?" — is still answered.
    if question_recall == 1.0 and question_precision < _FAQ_SUBJECT_FOCUS_MIN:
        return 0.0
    if question_recall + question_precision:
        question_fit = (
            2 * question_recall * question_precision / (question_recall + question_precision)
        )
    else:
        question_fit = 0.0
    score = (0.55 * body_coverage) + (0.45 * question_fit)
    return round(score, 4)


#: An explicit date or period states a timing rule as surely as the word
#: "deadline" does: "by the 15th of the following month" (CodeRabbit on #705).
#: "May" is left out of the month names: it is usually the modal verb.
_TIMING_EXPRESSION_RE = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)\b"
    r"|\b(?:january|february|march|april|june|july|august|september|october|november|december)\b"
    r"|\b(?:following|next|each|every|end\s+of\s+the)\s+(?:month|year|quarter|week)\b"
    r"|\bwithin\s+\d+\s+(?:working\s+)?(?:days|weeks|months)\b"
    r"|\bon\s+or\s+before\b",
    re.IGNORECASE,
)

#: What a question asks for, from its interrogative. Stop words drop "when" and
#: "how", so "When are capital gains taxed?" and "How are capital gains taxed?"
#: had the same terms and were both promoted (CodeRabbit on #705). At the same
#: position the longer form wins: "how much" is an amount, not a procedure.
_QUESTION_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("amount", re.compile(r"\bhow\s+(?:much|many)\b", re.IGNORECASE)),
    ("timing", re.compile(r"\b(?:when|how\s+long|how\s+soon)\b", re.IGNORECASE)),
    ("procedure", re.compile(r"\bhow\b", re.IGNORECASE)),
    ("party", re.compile(r"\bwho(?:m|se)?\b", re.IGNORECASE)),
    ("place", re.compile(r"\bwhere\b", re.IGNORECASE)),
    ("reason", re.compile(r"\bwhy\b", re.IGNORECASE)),
)


def question_intent(text: str) -> str:
    """The intent of the first interrogative in *text*, or ``""`` when it has none."""
    found = [
        (match.start(), rank, intent)
        for rank, (intent, pattern) in enumerate(_QUESTION_INTENTS)
        if (match := pattern.search(text or ""))
    ]
    return min(found)[2] if found else ""


def faq_question_equivalence(query: str, entry: dict[str, Any]) -> float:
    """F1 over content terms between *query* and an FAQ row's own question.

    1.0 means the user asked *this* FAQ's question — same content terms, modulo
    stopwords, the alias table and acronym folding (asking "What is PAYE?" is
    asking "What is PAYE (Pay As You Earn)?"). Unlike ``faq_match_score`` this is symmetric,
    so an FAQ whose question carries an extra subject term cannot reach 1.0:
    "What is withholding tax exemption?" scores 0.800 against "What is
    withholding tax?", because "exemption" is a term the query never supplied.
    """
    asked = faq_subject_terms(query)
    faq = faq_subject_terms(str(entry.get("question") or ""))
    if not asked or not faq:
        return 0.0
    # Same subject, different question: "when" is not answered by "how".
    asked_intent, faq_intent = question_intent(query), question_intent(str(entry.get("question") or ""))
    if asked_intent and faq_intent and asked_intent != faq_intent:
        return 0.0
    overlap = len(asked & faq)
    if not overlap:
        return 0.0
    precision, recall = overlap / len(faq), overlap / len(asked)
    return 2 * precision * recall / (precision + recall)


def promote_equivalent_faq_hits(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move FAQ rows whose question the user asked verbatim to the front.

    Ranking is RRF over 7,000+ document chunks, and a chunk can outrank the
    curated FAQ row that answers the question word for word. Measured on the
    deployed Space: "What are business records?" was answered from an
    agriculture-sector PDF while the identically-worded FAQ row sat second, and
    "When are capital gains taxed?" returned *how* they are taxed rather than
    *when*. Both have an exact FAQ counterpart.

    Deliberately narrow. Promotion requires term-*equivalence* (1.0), not a high
    score: at 0.8 this would also fire for "What is withholding tax exemption?"
    against "What is withholding tax?", where it currently answers correctly from
    the withholding-tax guide. Relative order is preserved within both groups, so
    where several rows are equivalent — the same question appearing in two files —
    retrieval's own ranking still decides between them.
    """
    if not hits:
        return hits
    promoted: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for hit in hits:
        q_raw = str(hit.get("question") or "").strip()
        text_raw = str(hit.get("text") or "")
        if not q_raw and text_raw.startswith("Question: ") and "\nAnswer: " in text_raw:
            parts = text_raw[len("Question: ") :].split("\nAnswer: ", 1)
            q_raw = parts[0].strip()
            hit["question"] = q_raw
            if not hit.get("answer"):
                hit["answer"] = parts[1].strip()
        is_faq = (
            str(hit.get("doc_type", "")).lower() in _FAQ_DOC_TYPES
            and q_raw != ""
        )
        if is_faq and faq_question_equivalence(query, hit) >= 1.0:
            promoted.append(hit)
        else:
            rest.append(hit)
    return promoted + rest if promoted else hits


def filter_unbound_faq_hits(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove canonical FAQ passages that do not answer the requested intent.

    Keyword hits already carry ``faq_match_score``.  This second pass also
    protects hybrid/indexed FAQ rows, whose stored metadata may not have been
    produced by the in-memory loader.  Non-FAQ PDF and attachment passages are
    left untouched and continue through the normal semantic/grounding gates.
    """
    faq_rows = [
        h
        for h in hits
        if str(h.get("doc_type", "")).lower() in _FAQ_DOC_TYPES
        and str(h.get("source", "")).lower().startswith("ura_")
        and str(h.get("source", "")).lower().endswith("_faqs.csv")
    ]
    if not faq_rows:
        return hits

    scores: dict[int, float] = {}
    for idx, hit in enumerate(hits):
        if hit not in faq_rows:
            continue
        q_raw = str(hit.get("question") or "").strip()
        a_raw = str(hit.get("answer") or "").strip()
        text_raw = str(hit.get("text") or "")
        if (not q_raw or not a_raw) and text_raw.startswith("Question: ") and "\nAnswer: " in text_raw:
            parts = text_raw[len("Question: ") :].split("\nAnswer: ", 1)
            if not q_raw:
                hit["question"] = parts[0].strip()
            if not a_raw:
                hit["answer"] = parts[1].strip()
        # Rows injected by _priority_faq_hits are exempt. They are reached only
        # when an intent regex matches the question, so they are already bound
        # to it more precisely than this gate can measure — and this gate scores
        # term COVERAGE, which is biased toward whichever row has the wordiest
        # answer regardless of whether it answers the question asked.
        #
        # That is not hypothetical. For "How do I file my annual tax returns?"
        # the procedural row "How do I file a return?" scores 0.575 against a
        # 0.584 cutoff and was dropped, while the definition "What is a return
        # filing?" scored 0.713 and survived on the strength of a long answer
        # listing PAYE, VAT, WHT and so on. A "how do I" question was answered
        # with "a return of income is a declaration…", losing by 0.009 to a row
        # that does not answer it at all.
        if hit.get("faq_priority"):
            continue
        # Reranking & semantic separation: when the cross-encoder or multilingual dense model
        # scores the passage with strong confidence (score_rerank >= 0.35 or score_norm >= 0.60),
        # preserve the candidate rather than discarding it via monolingual token overlap.
        rerank_score = float(hit.get("score_rerank") or 0.0)
        norm_score = float(hit.get("score_norm") or 0.0)
        if rerank_score >= 0.35 or norm_score >= 0.60:
            continue
        score = float(hit.get("faq_match_score") or faq_match_score(query, hit))
        scores[idx] = score

    best = max(scores.values(), default=0.0)
    cutoff = max(_FAQ_MATCH_MIN, best * _FAQ_MATCH_RELATIVE)
    filtered: list[dict[str, Any]] = []
    for idx, hit in enumerate(hits):
        if idx not in scores or scores[idx] >= cutoff:
            if idx in scores:
                hit["faq_match_score"] = scores[idx]
            filtered.append(hit)
    return filtered


def install_faq_acronyms(pairs: dict[str, str]) -> None:
    """Make *pairs* the acronym equivalences :func:`faq_subject_terms` folds on."""
    global _FAQ_ACRONYM_RE
    faq_subject_terms.cache_clear()
    _FAQ_ACRONYM_EXPANSIONS.clear()
    _FAQ_ACRONYM_EXPANSIONS.update(pairs)
    if not pairs:
        _FAQ_ACRONYM_RE = None
        return
    # Longest first so "automatic exchange of information" is not consumed by a
    # shorter overlapping expansion.
    alternation = "|".join(re.escape(p) for p in sorted(pairs, key=len, reverse=True))
    _FAQ_ACRONYM_RE = re.compile(rf"\b({alternation})\b", re.IGNORECASE)
