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
        r"\b(feel free to (ask|reach out)|happy to help|glad to help"
        r"|hope (this|that) helps|don't hesitate to (ask|contact|reach out)"
        r"|is there anything else)\b",
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
    r"|sick|ill|hospital|died|passed away|funeral)\b"
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
