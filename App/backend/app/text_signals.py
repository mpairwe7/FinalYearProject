"""Shared lexical signals for scoring and tone: courtesy detection & distress.

Faithfulness scoring, claim verification, and the offline eval harness all
need the same answer: "is this sentence conversational courtesy, or a factual
claim that must be grounded in retrieved context?"  Politeness must never be
punished as a hallucination, and a hallucination must never hide behind
politeness — so the classifier is deterministic, anchored on sentence shape,
and refuses to mark anything containing figures (rates, amounts, deadlines)
as courtesy.

Stdlib-only on purpose: this module is imported by ``retriever``,
``claim_verifier``, ``evaluation``, ``service`` and ``llm`` and must never
create an import cycle.
"""

from __future__ import annotations

import re

# Superset of claim_verifier's historical stopword list; used to reduce
# sentences to content tokens so function words cannot dominate overlap.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "about", "after", "all", "also", "am", "an", "and", "any",
        "are", "as", "at", "be", "been", "but", "by", "can", "could",
        "did", "do", "does", "for", "from", "had", "has", "have", "he",
        "her", "his", "how", "i", "if", "in", "into", "is", "it", "its",
        "may", "me", "more", "most", "must", "my", "no", "not", "of",
        "on", "or", "our", "out", "over", "she", "should", "so", "some",
        "such", "than", "that", "the", "their", "them", "then", "there",
        "these", "they", "this", "to", "was", "we", "were", "what",
        "when", "where", "which", "who", "why", "will", "with", "would",
        "you", "your",
    }
)

_WORD_RE = re.compile(r"\w+")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")

# The three official URA hotline numbers are the only digit sequences a
# courtesy sentence may contain (contact footers quote them verbatim).
_HOTLINE_RE = re.compile(r"0800\s?117\s?000|0800\s?217\s?000|0772\s?140\s?000")
_FIGURE_RE = re.compile(r"\d|%|\bpercent\b|\bugx\b|\bshs\b|\bshillings?\b")

_MD_MARKUP_RE = re.compile(r"[*_`#]+")
_CHANNEL_TOKEN_RE = re.compile(
    r"ura\.go\.ug|0800\s?117\s?000|0800\s?217\s?000|0772\s?140\s?000"
    r"|whatsapp|contact cent(re|er)|toll[- ]?free"
)
# Sentence splitting breaks URLs ("ura.go.ug") apart, leaving contact-footer
# fragments without their courtesy lead; strip channel vocabulary to see
# whether anything contentful remains.
_CHANNEL_STRIP_RE = re.compile(
    r"0800\s?117\s?000|0800\s?217\s?000|0772\s?140\s?000|https?://\S+|www\.\S+"
    r"|ura\.go\.ug|\bura\b|\bgo\b|\bug\b|whatsapp|toll[- ]?free"
    r"|contact cent(?:re|er)|\bcall\b|\bvisit\b|\bportal\b|\bweb\b"
)

# Anchored sentence shapes. Grouped for readability; matched against a
# lowercased, markdown-stripped sentence with normalised apostrophes.
_COURTESY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        # -- greetings / thanks / apologies ------------------------------
        r"^(hello|hi there|hi|hey|greetings|good (morning|afternoon|evening|day))\b",
        r"^thank(s| you)\b",
        r"\bthank you for (asking|reaching out|your patience|getting in touch)\b",
        r"^(i am|i'm|we are|we're) (sorry|glad|happy|here)\b",
        r"^(great|good) question\b",
        r"^that('s| is) a (great|good) question\b",
        r"^here('s| is) what you need to know\b",
        r"^you('re| are) welcome\b",
        r"^(no problem|my pleasure|of course)\b",
        # -- empathy acknowledgments (require an emotion word) ------------
        r"^i (fully |completely )?(understand|know|realise|realize|appreciate|hear)\b"
        r".*\b(stress\w*|frustrat\w*|worr\w*|confus\w*|urgent|difficult|overwhelm\w*|concern\w*|tough)\b",
        r"^that (sounds|can be|must be|can feel|must feel) "
        r"(frustrating|stressful|difficult|confusing|overwhelming|tough)\b",
        r"^(no worries|don't worry|do not worry)\b",
        r"^let('s| us) (sort|work|take|figure|go|get)\b",
        # -- offers of help / sign-offs / engagement ----------------------
        # "happy to help" was listed but the models write "happy to assist"
        # just as often, and a closing pleasantry counted as an uncited factual
        # claim — enough on its own to send a fully-cited answer to "revise".
        r"\b(feel free to (ask|reach out)|(happy|glad|pleased) to (help|assist)"
        r"|hope (this|that) helps|don't hesitate to (ask|contact|reach out)"
        r"|is there anything else)\b",
        # "If you have any further questions, …" — a sign-off, and the most
        # common closing sentence in these replies. The figure guard above
        # still keeps anything carrying a number scoreable.
        r"^if you have any (further |other |additional |more )?"
        r"(questions|inquiries|queries|concerns)\b",
        r"^(please )?let me know if\b",
        r"^i can help( you)?( with)?\b",
        r"\bhow (can|may) i (help|assist)\b",
        r"\bwhat would you like to know\b",
        r"^(i am|i'm) the ura digital assistant\b",
        r"^ura is happy to help\b",
        # -- contact footers (courtesy lead required; channel token
        #    checked separately in is_courtesy_sentence) ------------------
        r"^for (help|assistance|more (information|details|support))\b",
        r"^(please )?contact (ura|the ura)\b",
        r"^you can (also )?(contact|call|visit|reach|whatsapp)\b",
        r"^reach out( to ura)?\b",
        r"^if you (get stuck|need (more )?help|have (more|any) questions)\b",
        # -- follow-up suggestions (SYSTEM_PROMPT rule 15) -----------------
        r"^you (might|may) also want to\b",
        r"^would you (also )?like to (know|learn)\b",
        r"^related topics?\b",
        r"^you (might|may|could) also (find|be interested)\b",
        # -- meta preambles / graceful fallbacks ---------------------------
        r"^based on the ura guidance i retrieved\b",
        r"^here('s| is) the most relevant guidance i found\b",
        r"^i could(n't| not) find\b",
        r"^i (don't|do not) have enough (information|details)\b",
        r"^i('d| would) rather connect you\b",
        r"^you deserve a (definitive|clear) answer\b",
        r"^i('ve| have) flagged (this|it)\b",
        r"^(please )?try rephrasing\b",
        r"^could you( please)? (share|tell me|give me|provide|rephrase)\b",
        r"^i('d| would) be happy to help\b",
        r"^let me put that a different way\b",
    )
)

_DISTRESS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "frustration",
        re.compile(
            r"frustrat|angry|annoy|fed up|ridiculous|useless|not working"
            r"|doesn't work|does not work|third time|again and again"
            r"|still (can't|cannot|no|not)"
        ),
    ),
    (
        "anxiety",
        re.compile(
            r"worried|worry|worrying|scared|afraid|anxious|confus|stress"
            r"|help me|don't know what|don't understand|do not understand|lost my"
        ),
    ),
    (
        "urgency",
        re.compile(
            r"urgent|asap|as soon as possible|deadline|due (today|tomorrow)"
            r"|penalt|\bfines?\b|\bfined\b|audit|enforcement|seiz|arrears"
            r"|overdue|late fee"
        ),
    ),
)

#: Financial or personal hardship.  Kept separate from frustration: a
#: frustrated user wants the process fixed, a user in hardship needs
#: options and usually a person.  For a revenue authority this is the
#: signal that most needs to change how the answer is written.
_HARDSHIP_RE = re.compile(
    r"\b(can'?t afford|cannot afford|no money|broke|bankrupt"
    r"|lose (my|the) (business|shop|job|home|land)|losing (my|the) (business|job)"
    r"|shut(ting)? down|closed down|out of business"
    r"|struggling|hardship|desperate|nothing left"
    r"|(?:i am|i'm|we are|my (?:child|son|daughter|wife|husband|mother|father|parent|family))(?:\s+(?:is|are|was|were|fell|got))?\s+(?:very\s+)?(?:sick|ill|in hospital)"
    r"|(?:my|our)\s+(?:mother|father|wife|husband|child|son|daughter|parent)(?:\s+(?:has|have|had|is|was))?\s+(?:died|passed away)"
    r"|bereavement|bereaved"
    r"|(?:account|bank)\s+(?:is\s+)?frozen|frozen\s+(?:my\s+)?(?:account|bank)"
    r"|agency\s+notice|distress\s+warrant"
    r"|sealed\s+(?:my\s+)?(?:shop|premises|business)"
    r"|seized\s+(?:my\s+)?(?:goods|cargo|vehicle|truck|car)"
    r"|lost\s+my\s+job|unemployed)\b"
)

#: Comprehension trouble — the explanation needs rebuilding, not
#: reassurance.  The anxiety pattern above also matches "don't
#: understand", so this is checked against a genuine-worry cue below.
_CONFUSION_RE = re.compile(
    r"\b(confus\w+|don'?t understand|do not understand|makes no sense"
    r"|i'?m lost|unclear|complicated)\b"
)

_WORRY_RE = re.compile(r"\b(worried|worry|worrying|scared|afraid|anxious|stress\w*|panic\w*|nervous)\b")

_EMPATHY_ACKS: dict[str, str] = {
    "frustration": "I'm sorry for the trouble — let's get this sorted out together.",
    "anxiety": "I understand this can feel stressful — let's take it step by step.",
    "urgency": "I know deadlines can be stressful, so here's the quickest way forward.",
    "hardship": "I'm sorry you're going through this — let's look at what options you have.",
    "confusion": "Let me put that a different way.",
}

_TONE_HINTS: dict[str, str] = {
    "frustration": (
        "The user sounds frustrated. Begin with one short empathetic sentence "
        "acknowledging the difficulty, then answer directly."
    ),
    "anxiety": (
        "The user sounds worried. Begin with one short reassuring sentence, "
        "then answer directly."
    ),
    "urgency": (
        "The user is under time pressure. Begin with one short sentence "
        "acknowledging the urgency, then give the fastest path first."
    ),
    "hardship": (
        "The user describes financial or personal hardship. Acknowledge it in "
        "one sentence, answer plainly, and mention that a human officer can "
        "discuss relief or payment arrangements."
    ),
    "confusion": (
        "The user is confused rather than upset. Re-explain in shorter "
        "sentences with one concrete example; do not add reassurance."
    ),
}


def split_sentences(text: str) -> list[str]:
    """Split text into scoreable sentences (same rule the scorer has always used)."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if len(s.strip()) > 5]


def content_tokens(text: str) -> set[str]:
    """Lowercased word tokens minus stopwords; digits and URL fragments stay."""
    return {t for t in _WORD_RE.findall((text or "").lower()) if t not in STOPWORDS}


def _normalise(sentence: str) -> str:
    cleaned = _MD_MARKUP_RE.sub("", sentence or "")
    cleaned = cleaned.replace("’", "'").replace("—", " ").replace("–", " ")
    return " ".join(cleaned.split()).lower().strip(" -\t")


def is_courtesy_sentence(sentence: str) -> bool:
    """True when the sentence is conversational courtesy, not a factual claim.

    Never true for sentences carrying figures (any digit outside the three
    URA hotline numbers, percentages, or currency amounts) — those stay
    scoreable so the grounding gate remains breakable.
    """
    text = _normalise(sentence)
    if not text:
        return False
    if _FIGURE_RE.search(_HOTLINE_RE.sub(" ", text)):
        return False
    for pattern in _COURTESY_PATTERNS:
        if pattern.search(text):
            return True
    # Contact-channel fragment: a sentence (or URL-split remnant) that is
    # nothing but URA channels — hotlines, portal URL, WhatsApp — with at
    # most one contentful word left once those are stripped.
    if _CHANNEL_TOKEN_RE.search(text):
        residue = content_tokens(_CHANNEL_STRIP_RE.sub(" ", text))
        if len(residue) <= 1:
            return True
    return False


def detect_user_distress(message: str) -> str:
    """Classify the message: '' | frustration | anxiety | urgency | hardship | confusion.

    Hardship is checked first and outranks everything: a message can read
    as frustrated *and* describe losing a business, and the second is the
    one that should change how the assistant answers.

    Confusion is separated out of anxiety, whose pattern also matches
    "don't understand". "I don't understand what chargeable income means"
    wants the explanation rebuilt, not reassurance — so it is only read as
    anxiety when a genuine worry cue is present too.
    """
    text = _normalise(message)
    if not text:
        return ""
    if _HARDSHIP_RE.search(text):
        return "hardship"
    if _CONFUSION_RE.search(text) and not _WORRY_RE.search(text):
        return "confusion"
    if (message or "").count("!") >= 2:
        return "frustration"
    for kind, pattern in _DISTRESS_PATTERNS:
        if pattern.search(text):
            return kind
    return ""


# ---------------------------------------------------------------------------
# Jurisdiction
#
# URA administers taxes in Uganda and nowhere else, but the deterministic rate
# and calculator paths read only the tax word and answered anything. Measured on
# 2026-09-02 (GAPS §2.11, G41): "What is the corporate income tax rate in Kenya
# for 2026?" returned "**The corporation tax rate in Uganda is 30%** (FY2026-27)
# … from the official URA FY2026-27 rate table". Reproduced for Rwanda.
#
# The reply is labelled Uganda, so it is not a false statement — which is what
# makes it dangerous. It silently answers a different question, in the
# authoritative register, with a citation, on the path users trust most. A
# taxpayer comparing jurisdictions reads a confident number against the country
# they asked about.
# ---------------------------------------------------------------------------

#: Neighbours and the economies taxpayers most often compare Uganda against.
#: Demonyms are included because "the Kenyan VAT rate" names a jurisdiction just
#: as plainly as "Kenya". Capitals are deliberately excluded: they add little
#: recall over the country name and every extra token is a false-positive risk
#: on a corpus full of place names.
_FOREIGN_JURISDICTIONS: tuple[tuple[str, str], ...] = (
    ("Kenya", r"kenyan?"),
    ("Tanzania", r"tanzanian?"),
    ("Rwanda", r"rwandan?"),
    ("Burundi", r"burundian?"),
    ("South Sudan", r"south\s+sudan(?:ese)?"),
    ("the Democratic Republic of the Congo", r"\bdrc\b|democratic\s+republic\s+of\s+(?:the\s+)?congo"),
    ("Ethiopia", r"ethiopian?"),
    ("Somalia", r"somalian?|somali"),
    ("Nigeria", r"nigerian?"),
    ("South Africa", r"south\s+africans?"),
    ("Ghana", r"ghanaian?|\bghana\b"),
    ("Egypt", r"egyptian?|\begypt\b"),
    ("the United Kingdom", r"\buk\b|united\s+kingdom|britain|british"),
    # "us" is the first-person plural pronoun far more often than it is the
    # country, and this list is consulted on fast path 0, before retrieval — so
    # a bare `\busa?\b` refused "can you help us with VAT registration" as a
    # United States question, naming a country the taxpayer never mentioned.
    #
    # The country reading now needs evidence: an unambiguous spelling, a
    # determiner ("the US"), or a following noun that the pronoun cannot take.
    # `federal`/`irs`/`dollar`/`citizen` qualify; `tax` deliberately does not,
    # because "give us tax advice" is ordinary English.
    #
    # That gives up "do i pay us tax". Accepted knowingly: the two failures are
    # not symmetric. A missed foreign jurisdiction falls through to normal
    # retrieval and is answered or abstained on; a false positive hard-refuses
    # a legitimate taxpayer with a confident, specific untruth. Precision wins.
    (
        "the United States",
        r"\busa\b|u\.s\.a\b|united\s+states|american?"
        r"|the\s+us\b"
        r"|\bus\s+(?:federal|irs|dollars?|citizens?|residents?|nationals?)\b",
    ),
    ("India", r"\bindian?\b"),
    ("China", r"chinese|\bchina\b"),
)

_FOREIGN_JURISDICTION_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE))
    for name, pattern in _FOREIGN_JURISDICTIONS
)

_UGANDA_RE = re.compile(r"\bugandan?\b|\bura\b|\bkampala\b", re.IGNORECASE)


def detect_foreign_jurisdiction(message: str) -> str:
    """Name the non-Ugandan jurisdiction this message is about, or ''.

    Returns '' when the message names Uganda too: "how does Uganda's VAT compare
    with Kenya's" is a Uganda question with a comparison in it, and refusing it
    outright would be worse than answering the half URA can speak to. The caller
    is expected to add the scope caveat in that case rather than stay silent.
    """
    text = message or ""
    if not text.strip() or _UGANDA_RE.search(text):
        return ""
    for name, pattern in _FOREIGN_JURISDICTION_RES:
        if pattern.search(text):
            return name
    return ""


# Naming another country is not the same as asking about its tax system. Most
# messages that mention one are ordinary cross-border URA questions — importing
# from Kenya, paying a Kenyan supplier, declaring foreign rental income — and
# they are fully answerable. Telling those taxpayers "I can't give you Kenya's
# side of that comparison" answers a question they did not ask. So the caveat
# needs a comparison to actually be present, not just a second country.
#
# Comparative adjectives only, never bare "more"/"less": "I import more than
# 100 units from Kenya" is a quantity, not a comparison.
_COMPARISON_MARKER_RE = re.compile(
    r"\bcompar(?:e[sd]?|ing|ison|ative)\b"
    r"|\bversus\b|\bvs\.?\b"
    r"|\bdifference\s+between\b"
    r"|\b(higher|lower|cheaper|greater|bigger|smaller|better|worse)\b",
    re.IGNORECASE,
)


def detect_comparison_jurisdiction(message: str) -> str:
    """Name the foreign jurisdiction a Uganda question also asks about, or ''.

    The mirror of :func:`detect_foreign_jurisdiction`: that one deliberately
    stays silent when Uganda is named too, so a comparison still gets the half
    URA can answer. This is what makes the other half visible — without it the
    reply hands over Uganda's figure and never mentions that the country the
    taxpayer wanted to compare against was not addressed at all, which reads
    as though the comparison had been answered.

    Requires an explicit comparison, not merely a second country. Cross-border
    trade questions name other countries constantly and are answerable in full;
    caveating those would answer a question the taxpayer never asked.
    """
    text = message or ""
    if not text.strip() or not _UGANDA_RE.search(text):
        return ""
    if not _COMPARISON_MARKER_RE.search(text):
        return ""
    for name, pattern in _FOREIGN_JURISDICTION_RES:
        if pattern.search(text):
            return name
    return ""


def jurisdiction_scope_caveat(country: str) -> str:
    """The line to append to a Uganda answer that was asked as a comparison."""
    return (
        f"_This covers Uganda only — URA does not administer {country}'s taxes, "
        f"so I can't give you {country}'s side of that comparison. You'd need "
        f"{country}'s own revenue authority for it._"
    )


def out_of_jurisdiction_reply(country: str) -> str:
    """What to say instead of a Ugandan figure the taxpayer did not ask for."""
    return (
        f"I can only help with taxes administered by the Uganda Revenue Authority. "
        f"I don't hold {country}'s tax rates, and quoting Uganda's figures for "
        f"{country} would be misleading.\n\n"
        f"For {country} you'll need that country's own revenue authority. "
        f"If you meant Uganda, ask again without naming {country} and I'll answer "
        f"from the official URA rate table."
    )


def empathy_ack(kind: str) -> str:
    """One short, translation-friendly empathetic opener for a distress kind.

    Every returned string must satisfy ``is_courtesy_sentence`` so the
    acknowledgment never dilutes faithfulness or claim verification.
    """
    return _EMPATHY_ACKS.get(kind, "")


def tone_hint_for(kind: str) -> str:
    """Per-turn system-prompt hint telling the LLM how to open for this user."""
    return _TONE_HINTS.get(kind, "")


# ---------------------------------------------------------------------------
# Canonical user-facing courtesy copy
# ---------------------------------------------------------------------------
# Reply templates shared by the REST, streaming, and agentic paths. They live
# here — next to is_courtesy_sentence — so the copy and the classifier that
# must recognise it can never drift apart (test_text_signals pins each one).

GREETING_REPLY = (
    "Hello, and welcome! I'm the URA Digital Assistant. I can help you with "
    "tax registration, filing returns, payments, customs, and more — how can "
    "I help you today?"
)

GRATITUDE_REPLY = (
    "You're welcome — I'm glad I could help! Is there anything else you'd "
    "like to know about URA services?"
)

FAREWELL_REPLY = (
    "Thank you for chatting with the URA Digital Assistant — goodbye for "
    "now! Feel free to reach out any time, or visit https://ura.go.ug."
)

CLARIFICATION_PROMPT = (
    "Of course — I'd be happy to help. Could you share a little more detail? "
    "For example, are you asking about VAT, PAYE, customs, registration, or "
    "a specific tax type?"
)

ABSTENTION_REPLY = (
    "I'm sorry — I couldn't find a reliable answer to this in the URA "
    "knowledge base, and I'd rather connect you with the right people than "
    "guess. Please contact URA at https://ura.go.ug or call the toll-free "
    "Contact Centre on 0800 117 000 / 0800 217 000."
)

NO_HITS_REPLY = (
    "I'm sorry — I couldn't find a specific answer in the URA knowledge "
    "base. Try rephrasing your question, or contact URA directly at "
    "https://ura.go.ug — they're glad to help."
)

ESCALATION_REPLY_LEAD = (
    "You deserve a definitive answer on this, so I've flagged it for a URA "
    "officer to review"
)

ESCALATION_REPLY_FOOTER = (
    " — meanwhile, you can also reach URA directly at https://ura.go.ug or "
    "via the Contact Centre."
)

CONTACT_FOOTER = (
    "If you get stuck at any step, URA is happy to help: visit "
    "https://ura.go.ug, call toll-free 0800 117 000 / 0800 217 000, or "
    "WhatsApp 0772 140 000."
)

GROUNDED_REVISION_PREAMBLE = (
    "Here's the most relevant guidance I found in official URA sources:"
)

# What replaces an answer whose figure contradicts the URA passage it cites.
#
# Claim verification already caught these — a reply saying "VAT is 20%" against
# a passage saying 18%, or a threshold that a budget has since moved — and the
# response judge already escalated them. What it did not do was stop showing
# the figure. A taxpayer acts on the number, not on the amber banner above it,
# so on a revenue authority's assistant a detected contradiction that is still
# printed is the same as not having detected it.
#
# The withheld answer says what happened rather than pretending the question
# was never asked: "I could not find anything" is a different, and false,
# statement about a turn where retrieval worked and generation went wrong.
CONTRADICTED_CLAIM_REPLY = (
    "I drafted an answer to this, but the figures in it disagreed with the "
    "URA documents I was reading — so I have not shown it, because a wrong "
    "figure here is worse than no figure. The passages I found are listed "
    "below; a URA officer has been asked to give you a definitive answer. "
    "You can also call URA toll-free on 0800 117 000 / 0800 217 000."
)


# A model asked to "cite passages like [1]" routinely groups its references
# instead — "[1, 3]" — and every consumer in this codebase reads citations with
# a regex shaped like \[(\d{1,3})\]. That regex does not match a grouped marker
# at all, so a properly cited sentence is read as having NO citation.
#
# In production that silently discarded every Gemini answer: claim verification
# marked the sentence uncited AND unsupported (no refs -> no cited context ->
# zero lexical overlap), which decided "revise", which replaced the generated
# prose with verbatim corpus excerpts under GROUNDED_REVISION_PREAMBLE. The
# answer was correct and cited; it was thrown away on a formatting mismatch.
# Measured on identical content: "[1, 3]" -> revise, score 0.5; "[1][3]" ->
# approve, score 1.0.
#
# Normalising once, where generated text enters the system, is what makes that
# right everywhere instead of in six places — the claim verifier, the three
# has-a-citation checks in service.py, and the frontend's marker stripper, which
# would otherwise leave a literal "[1, 3]" sitting in the rendered answer.
_GROUPED_CITATION_RE = re.compile(r"\[\s*(\d{1,3}(?:\s*[,;]\s*\d{1,3})+)\s*\]")


def normalise_citation_markers(text: str) -> str:
    """Expand grouped citation markers: ``[1, 3]`` -> ``[1][3]``.

    Single markers and non-citation brackets are left untouched, so this is
    safe to apply to any reply. Ranges (``[1-3]``) are deliberately NOT
    expanded: the hyphen is far more often a page or section reference than a
    citation range, and inventing refs is worse than missing one.
    """
    if not text or "[" not in text:
        return text

    def expand(match: re.Match[str]) -> str:
        refs = [r.strip() for r in re.split(r"[,;]", match.group(1))]
        return "".join(f"[{r}]" for r in refs if r)

    return _GROUPED_CITATION_RE.sub(expand, text)
