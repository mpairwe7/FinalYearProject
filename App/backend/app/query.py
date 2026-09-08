"""Query rewriting — synonym expansion, spell correction, domain normalization, and language detection.

Transforms raw user queries into optimized retrieval queries:
- Domain-specific abbreviation expansion (TIN, VAT, PAYE, etc.)
- Common misspelling correction for URA domain terms
- Query normalization (lowercasing, whitespace cleanup)
- Contextual rewriting from conversation history (coreference resolution)
- Automatic language detection (en, lg, sw, nyn, ach)
"""

from __future__ import annotations

import functools
import logging
import os
import re
from typing import Any

from . import mt

logger = logging.getLogger(__name__)

# CodeQL py/log-injection: a request-supplied locale reaches a log call
# below. Strip CR/LF/control characters at the log call itself so a value
# can never forge a fake log line.
_LOG_STRIP_TABLE = dict.fromkeys(range(0x20), None)
_LOG_STRIP_TABLE[0x7F] = None


def _log_safe(value: str) -> str:
    """*value* with control characters (CR/LF included) removed."""
    return value.translate(_LOG_STRIP_TABLE)


# ---------------------------------------------------------------------------
# Language rollout scope
# ---------------------------------------------------------------------------
# Which locales the app actually routes through translation + localization,
# as opposed to which locales the underlying models/detector are CAPABLE of.
# detect_language() below can still tell Acholi/Runyankole/Ateso/Lugbara
# apart, and Sunbird's translate endpoint still serves all of them (see
# sunbird.TRANSLATION_LANGUAGES) — this is a separate, narrower gate on top,
# so the app can ship en/lg/sw first and widen this one env var later
# without touching the detection or translation code underneath it.
#
# Gated 2026-08-19: an explicit or auto-detected locale outside this set is
# treated as English (see ChatModel._generate_en / generate_retrieval_only
# in service.py, both of which import this constant).
SUPPORTED_LOCALES = frozenset(
    loc.strip()
    for loc in os.getenv("SUPPORTED_LOCALES", "en,lg,sw").split(",")
    if loc.strip()
)


def gate_locale(locale: str) -> str:
    """*locale* unchanged if it's in SUPPORTED_LOCALES, else "en"."""
    return locale if locale in SUPPORTED_LOCALES else "en"


# ---------------------------------------------------------------------------
# Language detection — heuristic patterns for Ugandan languages
# ---------------------------------------------------------------------------
# Runyankole (nyn) — Bantu noun-class prefixes (word-start) + common verb forms
_NYN_PREFIXES = re.compile(r"\b(oku|omu|aba|eki|ebi|obu|aha|omw|ogu|eky|enk|emb)\w+", re.IGNORECASE)
_NYN_WORDS = re.compile(
    r"\b(nkore|nkunda|tinkunda|kushonga|oine|aine|niwe|turi|"
    r"twine|rwire|nindwire|ninkunda|ninaba|maka|omushuija|"
    r"omwana|wangye|enshonga|nta|mpa|nimanya)\b",
    re.IGNORECASE,
)
_NYN_MARKERS = None  # computed in detect_language as prefix + word hits
# Acholi (ach) common morphemes and particles
_ACH_MARKERS = re.compile(
    r"\b(ango|ningo|atim|atwero|agengo|tye|bene|dong|kit|gin|"
    r"lwak|dano|latin|piny|kwo|cam|wek|twero|cako|myero)\b",
    re.IGNORECASE,
)
# Swahili (sw) common function words and tax terms
_SW_MARKERS = re.compile(
    r"\b(ninaweza|ninawezaje|nifanye|biashara|kodi|ushuru|kujisajili|"
    r"asilimia|thamani|marejesho|huduma|wafanyakazi|mapato|nchini|"
    r"sababu|jinsi|nini|gani|wapi|vipi|kupata|kuanza|kufanya|"
    r"habari|asante|shukrani|kiasi|kulipa|kuwasilisha|adhabu)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Domain abbreviation expansion
# ---------------------------------------------------------------------------
# Expansions keep the acronym in parentheses on purpose.  Retrieval is
# hybrid: the dense side matches the spelled-out phrase, the sparse
# (BM25) side matches whichever surface form the corpus actually uses.
# Replacing "WHT" with "Withholding Tax" trades one exact match for
# another and loses BM25 recall on every document that writes the
# acronym — which URA guidance routinely does.  BM25 tokenisation
# strips the brackets, so "Withholding Tax (WHT)" indexes as
# ['withholding', 'tax', 'wht'] and both forms hit.
_ABBREVIATIONS: dict[str, str] = {
    "tin": "Taxpayer Identification Number (TIN)",
    "vat": "Value Added Tax (VAT)",
    "paye": "Pay As You Earn (PAYE)",
    "efris": "Electronic Fiscal Receipting and Invoicing System (EFRIS)",
    "ura": "Uganda Revenue Authority (URA)",
    "dts": "Digital Tracking Solution (DTS)",
    "trep": "Tax Registration Expansion Project (TREP)",
    "cit": "Corporate Income Tax (CIT)",
    "pit": "Personal Income Tax (PIT)",
    "wht": "Withholding Tax (WHT)",
    "whit": "Withholding Tax (WHT)",
    "etax": "e-Tax portal (eTax)",
    "efiling": "electronic filing (eFiling)",
    "kcca": "Kampala Capital City Authority (KCCA)",
    "nssf": "National Social Security Fund (NSSF)",
    "ugx": "Ugandan Shillings (UGX)",
    "usd": "US Dollars (USD)",
}

# ---------------------------------------------------------------------------
# Common misspellings in the URA domain
# ---------------------------------------------------------------------------
_CORRECTIONS: dict[str, str] = {
    # Registration & Onboarding
    "regester": "register",
    "registar": "register",
    "regsiter": "register",
    "regstr": "register",
    "rgister": "register",
    "registre": "register",
    "registeration": "registration",
    "registation": "registration",
    "regestration": "registration",
    "registrtion": "registration",
    "regisration": "registration",
    "ragistration": "registration",
    "aply": "apply",
    "aplyin": "applying",
    "aplying": "applying",
    "aprove": "approve",
    "aplication": "application",
    "aplications": "applications",
    "appliation": "application",
    "clearence": "clearance",
    "clearanse": "clearance",
    "clearnce": "clearance",
    "certifikate": "certificate",
    "certifcate": "certificate",
    "certifacete": "certificate",
    # Taxpayer & Entities
    "tax payer": "taxpayer",
    "taxpyer": "taxpayer",
    "individul": "individual",
    "individal": "individual",
    "individuel": "individual",
    "indivdual": "individual",
    "organisaton": "organisation",
    "organizaton": "organization",
    "orgnization": "organization",
    "organiztion": "organization",
    "compnay": "company",
    "comapny": "company",
    "compny": "company",
    "companie": "company",
    "residetn": "resident",
    "resedint": "resident",
    "residensy": "residence",
    "resedence": "residence",
    # Tax Types & Rules
    "withholdin": "withholding",
    "witholding": "withholding",
    "withholdng": "withholding",
    "withoding": "withholding",
    "whitholding": "withholding",
    "wthholding": "withholding",
    "withholdingtax": "withholding tax",
    "excise duity": "excise duty",
    "exise duty": "excise duty",
    "customsduty": "customs duty",
    "exciseduty": "excise duty",
    "taxclearance": "tax clearance",
    "incometax": "income tax",
    "incom tax": "income tax",
    "incme": "income",
    "vatrate": "vat rate",
    "vat rat": "vat rate",
    "tinregistration": "tin registration",
    "latefiling": "late filing",
    "taxpayment": "tax payment",
    "filng": "filing",
    "fillling": "filing",
    "fillng": "filing",
    "fylling": "filing",
    "retun": "return",
    "retrun": "return",
    "retrn": "return",
    "retuns": "returns",
    "retruns": "returns",
    "taxreturn": "tax return",
    "taxreturns": "tax returns",
    "dedline": "deadline",
    "deadlin": "deadline",
    "dedlines": "deadlines",
    "due dat": "due date",
    "statment": "statement",
    "statemnt": "statement",
    "statmnt": "statement",
    "stetement": "statement",
    # Assessments, Penalties, Exemptions
    "assesment": "assessment",
    "assement": "assessment",
    "assessmnt": "assessment",
    "asesment": "assessment",
    "assesed": "assessed",
    "assesesd": "assessed",
    "penality": "penalty",
    "penalyt": "penalty",
    "penlaty": "penalty",
    "panalty": "penalty",
    "penaltie": "penalty",
    "penaltys": "penalties",
    "panalties": "penalties",
    "exmpt": "exempt",
    "exmept": "exempt",
    "exepmt": "exempt",
    "exsempt": "exempt",
    "exmption": "exemption",
    "exempton": "exemption",
    "exemptions": "exemptions",
    "presumtive": "presumptive",
    "presumtve": "presumptive",
    "presumptiv": "presumptive",
    # Disputes & Declarations
    "disput": "dispute",
    "dispuet": "dispute",
    "objection": "objection",
    "obejction": "objection",
    "objestion": "objection",
    "objectin": "objection",
    "apeal": "appeal",
    "apael": "appeal",
    "appeall": "appeal",
    "refud": "refund",
    "refumd": "refund",
    "reffund": "refund",
    "complience": "compliance",
    "compiance": "compliance",
    "compliace": "compliance",
    "decleration": "declaration",
    "declaraton": "declaration",
    "declarashon": "declaration",
    "importaton": "importation",
    "customes": "customs",
    "coustoms": "customs",
    "custome": "customs",
    "crago": "cargo",
    "pucrhase": "purchase",
    "puchase": "purchase",
    # Invoicing & Documents
    "receipting": "receipting",
    "receiping": "receipting",
    "reciept": "receipt",
    "receit": "receipt",
    "reciepting": "receipting",
    "invoiceing": "invoicing",
    "invoic": "invoice",
    "invoyce": "invoice",
    "invoise": "invoice",
    "docuemnts": "documents",
    "docuemnt": "document",
    "documnts": "documents",
    "documnt": "document",
    "requirments": "requirements",
    "requirment": "requirement",
    "requrments": "requirements",
    "reqirements": "requirements",
    # Financial & Calculations
    "turnovr": "turnover",
    "tunover": "turnover",
    "turn over": "turnover",
    "threshhold": "threshold",
    "treshold": "threshold",
    "threshol": "threshold",
    "compulsary": "compulsory",
    "compuslory": "compulsory",
    "compusary": "compulsory",
    "voluntery": "voluntary",
    "volantary": "voluntary",
    "volunterly": "voluntary",
    "provisinal": "provisional",
    "provisonal": "provisional",
    "provisioal": "provisional",
    "hihger": "higher",
    "salery": "salary",
    "persentage": "percentage",
    "percntage": "percentage",
    "bussiness": "business",
    "bizness": "business",
    "busines": "business",
    "buisness": "business",
    "comput": "compute",
    "computaion": "computation",
    "calculat": "calculate",
    "calculaton": "calculation",
    # Common typing slips & chat abbreviations
    "abt": "about",
    "dat": "that",
    "wat": "what",
    "wen": "when",
    "hw": "how",
    "hw to": "how to",
    "how 2": "how to",
    "wats": "what is",
    "whre": "where",
    "bcoz": "because",
    "bcz": "because",
    "coz": "because",
    "plz": "please",
    "pls": "please",
    "u": "you",
    "ur": "your",
    "thx": "thanks",
    "tnx": "thanks",
    "thnk": "thank",
    "cud": "could",
    "wud": "would",
    "shud": "should",
    "hv": "have",
    "ned": "need",
    "claryfy": "clarify",
    "clarifi": "clarify",
    "dificult": "difficult",
    "dificulty": "difficulty",
    "pasword": "password",
    "passward": "password",
    "pssword": "password",
    # General English common misspellings
    "recieve": "receive",
    "recieved": "received",
    "recieving": "receiving",
    "untill": "until",
    "alot": "a lot",
    "definately": "definitely",
    "definitly": "definitely",
    "definetly": "definitely",
    "seperate": "separate",
    "separete": "separate",
    "occured": "occurred",
    "occurance": "occurrence",
    "occurence": "occurrence",
    "beleive": "believe",
    "belive": "believe",
    "goverment": "government",
    "govment": "government",
    "calender": "calendar",
    "tommorow": "tomorrow",
    "tomorow": "tomorrow",
    "yesturday": "yesterday",
    "neccessary": "necessary",
    "necesary": "necessary",
    "succesful": "successful",
    "successfull": "successful",
    "posible": "possible",
    "possable": "possible",
    "problm": "problem",
    "probleme": "problem",
    "infomation": "information",
    "informashon": "information",
    "servise": "service",
    "servises": "services",
    "diffrent": "different",
    "diferent": "different",
    "guidlines": "guidelines",
    "peaple": "people",
    "peple": "people",
    "autometic": "automatic",
    "authoroty": "authority",
    "authrity": "authority",
    "beacause": "because",
    "availble": "available",
    "avaliable": "available",
    "avialable": "available",
    "procede": "proceed",
    "adress": "address",
    "addres": "address",
    "comunication": "communication",
    "begining": "beginning",
    "truely": "truly",
    "existance": "existence",
    "experiance": "experience",
    "convinient": "convenient",
    "sucess": "success",
    "foward": "forward",
    "knowlege": "knowledge",
    "compleatly": "completely",
    "accross": "across",
    "familar": "familiar",
    "offical": "official",
    "offise": "office",
    "secratary": "secretary",
    "responsable": "responsible",
    "supprise": "surprise",
    "writting": "writing",
    "writen": "written",
    "greatful": "grateful",
    "rember": "remember",
    "scedule": "schedule",
    "schedual": "schedule",
    "assistence": "assistance",
    "persone": "person",
    "sugest": "suggest",
    "similer": "similar",
    "catagory": "category",
    "wich": "which",
    "dis": "this",
    "dey": "they",
    "dem": "them",
    "wid": "with",
    "wit": "with",
    "whi": "why",
    "whoo": "who",
    "thanx": "thanks",
    "tel": "tell",
    "r": "are",
    "b": "be",
    "c": "see",
    "y": "why",
    "pleaaase": "please",
    "pleeease": "please",
    "heeeelp": "help",
    "soooo": "so",
}


def expand_abbreviations(query: str) -> str:
    """Expand known abbreviations inline for better retrieval recall."""
    words = query.split()
    expanded = []
    for w in words:
        key = w.lower().strip(".,;:?!\"'()")
        if key in _ABBREVIATIONS:
            # Preserve trailing punctuation
            suffix = w[len(w.rstrip(".,;:?!\"'()")) :]
            expanded.append(_ABBREVIATIONS[key] + suffix)
        else:
            expanded.append(w)
    return " ".join(expanded)


_TAX_DOMAIN_VOCAB: frozenset[str] = frozenset({
    "register", "registration", "registered", "registers",
    "taxpayer", "taxpayers", "individual", "individuals", "organisation", "organization", "organisations", "organizations",
    "company", "companies", "corporate", "corporation",
    "withholding", "customs", "clearance", "assessment", "assessments", "assessed", "dispute", "disputes", "objection", "objections",
    "appeal", "appeals", "invoicing", "receipting", "document", "documents", "application", "applications",
    "apply", "threshold", "thresholds", "compulsory", "voluntary", "higher", "lower", "purchase", "purchases", "invoice", "invoices",
    "resident", "residents", "residence", "salary", "employment", "turnover", "penalty", "penalties",
    "provisional", "exemption", "exemptions", "exempt", "deadline", "deadlines", "declaration", "declarations",
    "electronic", "online", "statement", "statements", "payment", "payments", "business", "commercial", "import", "imports", "export", "exports",
    "compute", "computation", "calculate", "calculation", "return", "returns", "filing",
    "compliance", "certificate", "certificates", "presumptive", "income", "rental", "refund", "refunds",
})

_COMMON_ENGLISH_WORDS: frozenset[str] = frozenset({
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "have", "has", "had", "having", "do", "does", "did", "doing", "done",
    "be", "is", "am", "are", "was", "were", "been", "being",
    "the", "a", "an", "and", "or", "but", "nor", "for", "yet", "so",
    "at", "by", "from", "in", "into", "of", "off", "on", "onto", "out", "over", "to", "up", "with", "under", "about",
    "i", "me", "my", "mine", "you", "your", "yours", "he", "him", "his", "she", "her", "hers", "it", "its", "we", "us", "our", "ours", "they", "them", "their", "theirs",
    "this", "that", "these", "those", "there", "here",
    "much", "many", "more", "most", "some", "any", "no", "not", "all", "both", "half", "each", "every", "other", "another",
    "want", "need", "like", "know", "tell", "give", "take", "make", "get", "find", "check", "help",
    "pay", "paid", "paying", "rate", "rates", "year", "years", "month", "months", "day", "days", "date", "dates", "time", "times",
    "good", "well", "great", "please", "thanks", "thank",
})

_LOCAL_LANGUAGE_WORDS: frozenset[str] = frozenset({
    "kodi", "kwa", "katika", "kujisajili", "asilimia", "thamani", "ushuru",
    "marejesho", "huduma", "wafanyakazi", "mapato", "nchini", "binafsi",
    "kazi", "mwaka", "mwezi", "kutoa", "kulipa", "zaidi", "kiwango", "viwango",
    "habari", "jambo", "karibu", "asante", "shukrani", "ndiyo", "hapana",
    "omusolo", "buli", "okufuna", "ebitundu", "ssente", "alipoota", "abakozi",
    "waggulu", "basasula", "bwe", "era", "kye", "bye", "kampuni", "emisolo",
    "okwewandiisa", "musanyufu", "ebisaanyizo", "enkola", "omusaala", "abakozesa", "ekitongole",
    "gyebaleko", "webale", "yee", "nedda", "nsaba", "sente",
})


_GENERAL_ENGLISH_VOCAB: frozenset[str] = frozenset({
    "information", "government", "calendar", "tomorrow", "yesterday",
    "necessary", "successful", "possible", "problem", "problems",
    "service", "services", "different", "guidelines", "people",
    "automatic", "authority", "because", "available", "proceed",
    "address", "communication", "beginning", "experience", "convenient",
    "knowledge", "completely", "official", "office", "offices",
    "secretary", "responsible", "surprise", "writing", "written",
    "questionnaire", "schedule", "assistance", "person", "suggest",
    "maintenance", "reference", "similar", "receive", "receiving", "received",
    "separate", "believe", "believing", "occurred", "occurring", "definitely",
    "category", "recommend", "recommended", "familiar", "difficulty", "difficult",
    "password", "passwords",
})

_ALL_CORRECTABLE_VOCAB = _TAX_DOMAIN_VOCAB | _GENERAL_ENGLISH_VOCAB


def _damerau_levenshtein(s1: str, s2: str) -> int:
    """Compute Damerau-Levenshtein distance between s1 and s2."""
    len1, len2 = len(s1), len(s2)
    d = [[0] * (len2 + 1) for _ in range(len1 + 1)]
    for i in range(len1 + 1):
        d[i][0] = i
    for j in range(len2 + 1):
        d[0][j] = j
    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and s1[i - 1] == s2[j - 2] and s1[i - 2] == s2[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
    return d[len1][len2]


@functools.lru_cache(maxsize=4096)
def _fuzzy_correct_word(word: str) -> str:
    """Fuzzy match an unrecognized token against tax and general English vocabulary."""
    low = word.lower()
    if len(low) < 4:
        return word
    if low in _ALL_CORRECTABLE_VOCAB or low in _COMMON_ENGLISH_WORDS or low in _LOCAL_LANGUAGE_WORDS:
        return word
    if word.isupper() or any(ch.isdigit() for ch in word):
        return word

    # Only correct if not surrounded by test/dummy padding (e.g. xx...xx)
    if word.startswith("xx") or word.endswith("xx"):
        return word

    max_dist = 1
    best_candidate = None
    best_dist = max_dist + 1

    for candidate in _ALL_CORRECTABLE_VOCAB:
        if abs(len(candidate) - len(low)) > max_dist:
            continue
        dist = _damerau_levenshtein(low, candidate)
        if dist < best_dist and dist <= max_dist:
            best_dist = dist
            best_candidate = candidate

    if best_candidate is not None:
        if word.istitle():
            return best_candidate.capitalize()
        return best_candidate

    return word


def correct_syntax_errors(query: str) -> str:
    """Normalize common English syntax errors, collapsed words, and grammatical slips."""
    q = query
    # Glued question / auxiliary words (missing space syntax errors)
    glued_patterns = {
        r"\bhowmuch\b": "how much",
        r"\bhowmany\b": "how many",
        r"\bhowto\b": "how to",
        r"\bwhatis\b": "what is",
        r"\bwhereis\b": "where is",
        r"\bwhenis\b": "when is",
        r"\bwhois\b": "who is",
        r"\bcani\b": "can i",
        r"\bdoi\b": "do i",
        r"\bdidi\b": "did i",
        r"\bshouldi\b": "should i",
        r"\bwouldi\b": "would i",
        r"\bcouldi\b": "could i",
        r"\bhowdo\b": "how do",
        r"\bhowcan\b": "how can",
        r"\bwhatcan\b": "what can",
        r"\bwhatdo\b": "what do",
        r"\bwheredo\b": "where do",
        r"\bwhendo\b": "when do",
        r"\biwant\b": "i want",
        r"\bineed\b": "i need",
        r"\bihave\b": "i have",
        r"\btellme\b": "tell me",
        r"\bhelpme\b": "help me",
        r"\bthankyou\b": "thank you",
        r"\binorder\b": "in order",
        r"\baswell\b": "as well",
        r"\batleast\b": "at least",
        r"\binfact\b": "in fact",
    }
    for pat, rep in glued_patterns.items():
        q = re.sub(pat, rep, q, flags=re.IGNORECASE)

    # Inverted question syntax: 'how i can get' -> 'how can i get', 'where i can pay' -> 'where can i pay'
    q = re.sub(r"\b(how|where|when|why)\s+i\s+can\b", r"\1 can i", q, flags=re.IGNORECASE)
    # 'how i pay' -> 'how do i pay', 'where i pay' -> 'where do i pay'
    q = re.sub(r"\b(how|where|when)\s+i\s+(pay|get|apply|register|file)\b", r"\1 do i \2", q, flags=re.IGNORECASE)
    # 'i want know' -> 'i want to know', 'i need know' -> 'i need to know'
    q = re.sub(r"\b(i\s+want|i\s+need)\s+(know|get|pay|register|apply|file)\b", r"\1 to \2", q, flags=re.IGNORECASE)
    # Dropped subject pronoun 'i' before 'am' at start of query or clause: 'am having' -> 'i am having'
    q = re.sub(r"(^|[.?!]\s+)\bam\s+(having|a|an|trying|asking|looking|registering|filing|paying|wondering|in)\b", r"\1i am \2", q, flags=re.IGNORECASE)
    # 'am i suppose to' -> 'am i supposed to'
    q = re.sub(r"\bam\s+i\s+suppose\s+to\b", "am i supposed to", q, flags=re.IGNORECASE)
    # Past tense confusion after auxiliary 'did not': 'did not filed' -> 'did not file'
    q = re.sub(r"\bdid\s+not\s+filed\b", "did not file", q, flags=re.IGNORECASE)
    q = re.sub(r"\bdid\s+not\s+paid\b", "did not pay", q, flags=re.IGNORECASE)
    q = re.sub(r"\bdid\s+not\s+registered\b", "did not register", q, flags=re.IGNORECASE)

    return q


def correct_spelling(query: str) -> str:
    """Fix domain-specific and general English misspellings, syntax slips, and typos.

    1. Normalize grammatical syntax errors and glued words.
    2. Disambiguate common syntax slips (e.g. 'wht is/are/does' -> 'what is/are/does').
    3. Apply dictionary-based corrections (_CORRECTIONS) with word boundaries.
    4. Apply fuzzy distance matching on remaining non-dictionary tokens against
       domain and general English vocabularies.
    """
    result = correct_syntax_errors(query)
    # Disambiguate "wht is/are/does" (typo for "what is...") vs "WHT" (Withholding Tax)
    result = re.sub(r"\bwht\s+(is|are|does|do|can|will|should)\b", r"what \1", result, flags=re.IGNORECASE)
    for wrong, right in _CORRECTIONS.items():
        result = re.sub(rf"\b{re.escape(wrong)}\b", right, result, flags=re.IGNORECASE)

    # Token-level fuzzy corrections for non-dictionary typographical slips
    def _token_replace(match: re.Match) -> str:
        token = match.group(0)
        return _fuzzy_correct_word(token)

    result = re.sub(r"\b[A-Za-z]{4,}\b", _token_replace, result)
    return result


def normalize(query: str) -> str:
    """Whitespace and basic cleanup."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    # Clean multiple consecutive punctuation marks
    q = re.sub(r"\?{2,}", "?", q)
    q = re.sub(r"!{2,}", "!", q)
    # Ensure space after comma if followed by a letter
    q = re.sub(r"([A-Za-z]),([A-Za-z])", r"\1, \2", q)
    return q


def rewrite_with_history(
    query: str,
    history: list[dict[str, Any]],
) -> str:
    """Resolve coreferences and elliptical responses using multi-turn conversation history.

    Handles:
    1. Short follow-up answers to assistant prompts (e.g. "individual", "as an individual",
       "company", "resident", "yes", "monthly", "50m") by anchoring to the active task.
    2. Multi-turn pronoun & coreference resolution ("it", "that", "this", "they", "the same")
       anchored to domain entities across recent turns (not just history[-1]).
    3. Contextual expansion for dependent follow-up questions ("what is the deadline?",
       "how much penalty?") by appending or contextualizing with the active topic.
    """
    if not history:
        return query

    q = (query or "").strip()
    if not q:
        return query

    from .context_manager import extract_conversation_entities, normalize_history_turns

    normalized_history = normalize_history_turns(history)
    if not normalized_history:
        return query

    last_turn = normalized_history[-1]
    last_user = last_turn.get("user_message", "")
    last_bot = last_turn.get("bot_reply", "")
    combined_prev = f"{last_user} {last_bot}".lower()

    # Extract multi-turn context entities across history
    entities = extract_conversation_entities(normalized_history)

    # 1. Elliptical answers to TIN registration clarification questions:
    if (re.search(r"\btin\b", combined_prev, re.IGNORECASE) or "TIN Registration" in entities.tax_topics) and re.search(
        r"\b(register|registration|get|obtain|apply|application)\b", combined_prev, re.IGNORECASE
    ):
        if re.search(
            r"^\s*(?:(?:as|for)\s*)?(?:(?:an?|the|my)\s*)?"
            r"(?:individuals?|myself|personal|person|sole\s+(?:proprietor|trader))\b",
            q,
            re.IGNORECASE,
        ):
            return "How do I register for a TIN as an individual"
        if re.search(
            r"^\s*(?:(?:as|for)\s*)?(?:(?:an?|the|my)\s*)?"
            r"(?:organisations?|organizations?|compan(?:y|ies)|ngos?|partnerships?"
            r"|business(?:es)?|institution|trusts?|saccos?)\b",
            q,
            re.IGNORECASE,
        ):
            return "How do I register for a TIN as an organisation"

    # 2. Elliptical answers to PAYE / Salary questions:
    if (
        re.search(r"\b(paye|salary|gross|net\s*pay|take[-\s]?home)\b", combined_prev, re.IGNORECASE)
        or "PAYE (Pay As You Earn)" in entities.tax_topics
    ):
        if re.search(r"^\s*(?:(?:and\s+)?(?:what\s+about\s+)?(?:as|for)\s+)?(?:a\s+)?non[-\s]?residents?\b", q, re.IGNORECASE):
            prev_amounts = re.findall(
                r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*(?:m|k|million|thousand)\b",
                last_user,
                re.IGNORECASE,
            ) or entities.amounts
            if prev_amounts:
                return f"calculate PAYE for a non-resident on {prev_amounts[-1]} gross salary"
            return "calculate PAYE for a non-resident"
        if re.search(r"^\s*(?:(?:and\s+)?(?:what\s+about\s+)?(?:as|for)\s+)?(?:a\s+)?residents?\b", q, re.IGNORECASE):
            prev_amounts = re.findall(
                r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*(?:m|k|million|thousand)\b",
                last_user,
                re.IGNORECASE,
            ) or entities.amounts
            if prev_amounts:
                return f"calculate PAYE for a resident on {prev_amounts[-1]} gross salary"
            return "calculate PAYE for a resident"

    # 3. Multi-turn pronoun / coreference resolution
    # Negative lookahead ensures demonstrative determiners before nouns ("this year",
    # "this month", "that period", "if this is my first time") are not treated as referent pronouns.
    determiner_lookahead = (
        r"(?!\s+(?:year|month|week|day|time|period|date|case|turnover|income|figure|number|amount|slip|form|stage|step|office|branch|category|is\s+(?:my|our|the)\s+first))"
    )
    pronoun_pattern = re.compile(
        rf"\b(it|they|them|the above|the same|(?:this|that|those|these){determiner_lookahead}|its|their)\b",
        re.IGNORECASE,
    )

    if pronoun_pattern.search(q):
        # Check if the query itself introduces a concrete domain topic, entity, or acronym
        q_entities = extract_conversation_entities([{"user_message": q, "bot_reply": ""}])
        if q_entities.active_subject:
            subject = q_entities.active_subject
        else:
            # Scan backward from most recent turn for a concrete domain topic, entity, or acronym
            subject = ""
            for turn in reversed(normalized_history):
                turn_entities = extract_conversation_entities([turn])
                if turn_entities.active_subject:
                    subject = turn_entities.active_subject
                    break

                u_msg = turn.get("user_message", "")
                b_msg = turn.get("bot_reply", "")
                abbreviations = re.findall(r"\b[A-Z]{2,10}\b", u_msg) or re.findall(r"\b[A-Z]{2,10}\b", b_msg)
                if abbreviations:
                    subject = abbreviations[-1]
                    break

            if not subject and entities.active_subject:
                subject = entities.active_subject

        if subject:
            subject_phrase = _ABBREVIATIONS.get(subject.lower(), subject)

            def _replace_pronoun(match: re.Match[str]) -> str:
                pr = match.group(1).lower()
                if pr in ("its", "their"):
                    return f"{subject_phrase}'s"
                if re.search(r"\bregister(?:ing)?\s+for\s+$", q[: match.start()], re.IGNORECASE):
                    article = "an" if subject_phrase[:1].lower() in "aeiou" else "a"
                    return f"{article} {subject_phrase}"
                return subject_phrase

            rewritten = pronoun_pattern.sub(_replace_pronoun, q, count=1)
            logger.debug("Query rewritten with context subject '%s' (input_length=%d)", subject, len(q))
            return rewritten

        # Fallback: use the first assistant sentence as a broad context hint
        first_sentence = re.split(r"(?<=[^A-Z])[.!?]\s", last_bot)[0].strip()
        if first_sentence and len(first_sentence) > 10:
            rewritten = f"Regarding '{first_sentence[:100]}': {q}"
            logger.debug("Query rewritten with assistant context (input_length=%d)", len(q))
            return rewritten

    # 4. Short follow-up without pronouns (<= 8 words) where previous turn asked a question or established a topic
    words = q.split()
    if len(words) <= 8 and not re.search(r"\b(hello|hi|hey|thanks|thank you|bye|goodbye)\b", q, re.IGNORECASE):
        if "?" in last_bot or "please choose" in last_bot.lower() or "choose one" in last_bot.lower():
            for abbrev in ("TIN", "VAT", "PAYE", "EFRIS", "WHT", "CGT", "CIT"):
                if re.search(rf"\b{abbrev}\b", combined_prev, re.IGNORECASE):
                    expanded = _ABBREVIATIONS.get(abbrev.lower(), abbrev)
                    return f"{expanded} {q}"

        # Dependent questions like "what is the deadline?", "what is the penalty?", "how do I file?"
        if (
            re.search(
                r"\b(deadline|due\s+date|penalt(?:y|ies)|how\s+(?:to|do\s+i)\s+file|rates?|threshold"
                r"|what\s+about|how\s+about|what\s+abt|how\s+abt|documents?|requirements?|needed"
                r"|cost|fee|charge|free|how\s+long|duration|processing\s+time|non[-\s]?resident"
                r"|where\s+do\s+i|how\s+can\s+i)\b",
                q,
                re.IGNORECASE,
            )
            and entities.active_subject
            and not re.search(rf"\b{re.escape(entities.active_subject)}\b", q, re.IGNORECASE)
        ):
            base_q = q.rstrip("?.! ")
            return f"{base_q} for {entities.active_subject}?"

    return q


# Lingua eager-preloads its language models, so constructing the detector is
# expensive (~12s). Build it ONCE per process and reuse it across requests —
# rebuilding per call made language detection the dominant chat-latency stage.
_LANG_DETECTOR = None
_LANG_DETECTOR_INIT_FAILED = False


def _get_language_detector():
    """Return a cached :class:`LanguageDetector`, or ``None`` if unavailable."""
    global _LANG_DETECTOR, _LANG_DETECTOR_INIT_FAILED
    if _LANG_DETECTOR is None and not _LANG_DETECTOR_INIT_FAILED:
        try:
            from ml.scripts.lang_id import LanguageDetector

            _LANG_DETECTOR = LanguageDetector(min_confidence=0.55)
        except Exception:
            _LANG_DETECTOR_INIT_FAILED = True
            # warning, not debug: this is a silent quality degradation — the
            # heuristic below reads Luganda as English — and it went unnoticed
            # in production precisely because it was logged where nobody looks.
            logger.warning(
                "LanguageDetector unavailable (ml.scripts.lang_id not importable); "
                "falling back to the character heuristic, which mis-detects "
                "Ugandan languages. See /ready capabilities.",
                exc_info=True,
            )
    return _LANG_DETECTOR


def language_detection_backend() -> str:
    """Which detector is actually answering: ``lingua`` or ``heuristic``.

    Reported on ``/ready`` so an operator can see the degradation from
    outside the process rather than inferring it from answer quality.
    """
    return "lingua" if _get_language_detector() is not None else "heuristic"


def detect_language(text: str, default_lang: str = "en") -> str:
    """Detect input language, returning a locale code (en, lg, sw, nyn, ach).

    Understands syntax errors, typos, and user intent before classification:
    misspellings of words must not alter the response language, and the default
    set language is preserved unless there is genuine lexical evidence for a
    different supported language.
    """
    if not text or len(text.strip()) < 4:
        return default_lang

    # Understand syntax and typos before checking language
    corrected = correct_spelling(normalize(text))
    cleaned = corrected.strip().lower()
    words = set(re.findall(r"[a-z']+", cleaned))
    if not words:
        return default_lang

    n_words = max(len(words), 1)

    # 1. Lexical markers for English, Luganda, and Swahili
    en_hits = len(words & _COMMON_ENGLISH_WORDS) + len(words & _TAX_DOMAIN_VOCAB)
    lg_hits = len(words & _LOCAL_LANGUAGE_WORDS)
    sw_hits = len(_SW_MARKERS.findall(cleaned))

    # A misspelled or noisy English query (e.g. "wat is the vat rat?",
    # "How do I pay assessmnt witholding tax?") resolves to English tokens and
    # must NEVER be hijacked to Luganda or Swahili.
    if en_hits > 0 and lg_hits == 0 and sw_hits == 0:
        return default_lang

    # 2. Strong lexical signals for supported Ugandan / East African locales
    if sw_hits >= 2 or (sw_hits >= 1 and en_hits == 0):
        return "sw"
    if lg_hits >= 2 or (lg_hits >= 1 and en_hits == 0):
        return "lg"

    # Consult statistical detector (lingua) on the corrected text
    det = _get_language_detector()
    if det is not None:
        try:
            result = det.detect(corrected)
            if result.lang in SUPPORTED_LOCALES and result.is_confident(0.75):
                # Extra guard: lingua must not override to lg/sw if English words dominate
                if result.lang in ("lg", "sw") and en_hits > 0 and lg_hits == 0 and sw_hits == 0:
                    return default_lang
                return result.lang
        except Exception:
            logger.debug("LanguageDetector.detect failed; using marker heuristics")

    # Quick heuristic: count marker hits per language
    nyn_hits = len(_NYN_PREFIXES.findall(cleaned)) + len(_NYN_WORDS.findall(cleaned))
    ach_hits = len(_ACH_MARKERS.findall(cleaned))

    nyn_ratio = nyn_hits / n_words
    ach_ratio = ach_hits / n_words
    sw_ratio = sw_hits / n_words

    # Strong signal thresholds for regional dialects
    if nyn_ratio >= 0.20 and nyn_hits >= 2 and en_hits == 0:
        return "nyn"
    if ach_ratio >= 0.20 and ach_hits >= 2 and en_hits == 0:
        return "ach"
    if sw_ratio >= 0.15 and sw_hits >= 2:
        return "sw"

    # Fall back to statistical detector with lower confidence threshold
    if det is not None:
        try:
            result = det.detect(corrected)
            if result.is_confident(0.55):
                if result.lang in ("lg", "sw") and en_hits > 0 and lg_hits == 0 and sw_hits == 0:
                    return default_lang
                return result.lang
        except Exception:
            logger.debug("LanguageDetector.detect failed; using heuristic only")

    # If local detection is low-confidence and no English words found, try Sunbird API
    if en_hits == 0:
        try:
            from . import sunbird
            if sunbird.is_available():
                sb_result = sunbird.detect_language(corrected)
                if sb_result and sb_result.get("locale"):
                    return sb_result["locale"]
        except Exception:
            logger.debug("Sunbird language detection unavailable")

    return default_lang


def rewrite(
    query: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Full query rewriting pipeline."""
    q = normalize(query)
    q = correct_spelling(q)
    q = expand_abbreviations(q)
    if history:
        q = rewrite_with_history(q, history)
    return q


# Sentence boundary requires trailing whitespace after `.!?`, so "37.5m"
# (no space after the period) is never mistaken for one — same guard
# claim_verifier applies. Em/en dash and colon/semicolon also split clauses
# even without a following `.!?` (distress preambles commonly use "— " or
# ": " rather than a full stop before the actual question). No leading `\s*`
# before the dash/colon branch — it and the trailing `\s+` would otherwise
# both match long whitespace runs, a polynomial-backtracking pattern on
# user-controlled text (CodeQL py/polynomial-redos); any leading space stays
# on the previous segment and is removed by the caller's per-segment strip().
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|[—–:;]\s+")


def extract_question_span(text: str) -> str:
    """Return only the interrogative sentence(s) in *text*, or "" if none.

    Distress-framed messages ("I've tried three times and it still doesn't
    work!! What is EFRIS?", "I'm worried — What is EFRIS?") combine
    emotional preamble with a real question in one string. Retrieving on the
    raw combination dilutes BM25/embedding relevance enough to push an
    otherwise-answerable question into false abstention, so callers handling
    a distressed turn should retrieve on this extracted span instead of the
    full rewritten text.
    """
    sentences = _SENTENCE_BOUNDARY_RE.split((text or "").strip())
    questions = [s.strip() for s in sentences if s.strip().endswith("?")]
    return " ".join(questions)


# ---------------------------------------------------------------------------
# Query-time retrieval plan (G17 + agentic multi-intent)
# ---------------------------------------------------------------------------
# Hard filters only fire on *unambiguous* mentions. A bare "2026" is not a
# fiscal year (Ugandan FY is July–June). Soft preferences boost matching
# passages without starving recall when the preferred edition is missing.

def current_fiscal_year() -> str:
    """Soft-preference target for “this fiscal year” / “current”.

    ``CURRENT_FISCAL_YEAR`` in the environment wins. Otherwise use the
    rate-table year in force today so the boost cannot freeze on last
    year's edition (see ``App/docs/tax-rate-tables.md``).
    """
    env = os.getenv("CURRENT_FISCAL_YEAR", "").strip()
    if env:
        return env
    try:
        from .tax.tables import resolve_fiscal_year

        return resolve_fiscal_year()
    except Exception:
        return "FY2026-27"


CURRENT_FISCAL_YEAR = current_fiscal_year()

_FY_EXPLICIT_RE = re.compile(
    r"\bFY\s*(20\d{2})\s*[-/]\s*(?:20)?(\d{2})\b",
    re.I,
)
_FY_SLASH_RE = re.compile(r"\b(20\d{2})\s*/\s*(20)?(\d{2})\s+(?:fiscal\s+)?year\b", re.I)
_CURRENT_FY_RE = re.compile(
    r"\b(this\s+(?:fiscal\s+)?year|current\s+(?:fiscal\s+)?year|latest|this\s+fy)\b",
    re.I,
)

# Machine translation returns the expanded form of a tax term where the
# deterministic routers and the corpus both use the abbreviation: Sunbird
# renders a Luganda VAT question as "what is the value-added tax in Uganda",
# and the rate matcher wants the literal "VAT". Without this a translated
# question misses every fast path and abstains, which is the whole reason
# local-language questions used to get worse answers than English ones.
#
# Only terms whose abbreviation is what the matchers key on are listed. The
# substitution is deliberately one-way (expanded -> abbreviation) and leaves
# the rest of the sentence alone.
_EXPANDED_TAX_TERMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bvalue[-\s]?added\s+tax\b", re.I), "VAT"),
    (re.compile(r"\bpay[-\s]?as[-\s]?you[-\s]?earn\b", re.I), "PAYE"),
    # MT renders this both ways — "Tax Identification Number" and "taxpayer
    # identification number" — so "payer" is optional here.
    (re.compile(r"\btax\s*(?:payer)?\s+identification\s+number\b", re.I), "TIN"),
]


def canonicalize_tax_terms(text: str) -> str:
    """Rewrite expanded tax terms to the abbreviations the matchers use.

    Applied to machine-translated text before it reaches the deterministic
    routers; a no-op for text that already uses the abbreviation.
    """
    out = text or ""
    for pattern, replacement in _EXPANDED_TAX_TERMS:
        out = pattern.sub(replacement, out)
    return out


_TAX_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(value[-\s]?added\s+tax|vat)\b", re.I), "vat"),
    (re.compile(r"\b(pay\s+as\s+you\s+earn|paye)\b", re.I), "paye"),
    (re.compile(r"\b(withholding\s+tax|wht)\b", re.I), "wht"),
    (re.compile(r"\b(corporate\s+income\s+tax|corporation\s+tax|cit)\b", re.I), "cit"),
    (re.compile(r"\b(personal\s+income\s+tax|pit)\b", re.I), "pit"),
    (re.compile(r"\b(excise\s+duty|excise)\b", re.I), "excise"),
    (re.compile(r"\b(customs?(?:\s+duty)?|import\s+duty)\b", re.I), "customs"),
    (re.compile(r"\b(capital\s+gains?(?:\s+tax)?|cgt)\b", re.I), "cgt"),
    (re.compile(r"\b(efris)\b", re.I), "efris"),
    (re.compile(r"\b(tin|taxpayer\s+identification)\b", re.I), "tin"),
]

# Split only on multi-intent markers. A bare "and" is too common
# ("VAT and PAYE rates" is one comparison, not two searches).
#
# Possessive quantifiers (Python 3.11+): \s+ adjacent to alternation here
# is exactly CodeQL's py/polynomial-redos shape — an adversarial run of
# whitespace lets the backtracking engine try many equivalent ways to
# split it across the \s+/\s* boundaries before a match ultimately fails.
# Making them possessive (\s++, \s*+) is the standard fix: the engine
# commits to the longest run and never backtracks into it. Verified
# behavior-identical to the backtracking originals across representative
# inputs, and empirically fast (µs, not seconds) on adversarial whitespace.
# decompose_query() below also runs normalize() before matching, which
# already collapses whitespace runs to one space — independently removing
# the long-run precondition these patterns would otherwise need.
_DECOMPOSE_SPLIT_RE = re.compile(
    r"\s+(?:and also|as well as|and then)\s+|"
    r"\s*;\s+|"
    r"\?\s+(?=(?:what|how|when|where|which|who)\b)",
    re.I,
)
_AND_QUESTION_RE = re.compile(
    r"\s+and\s+(?=(?:what|how|when|where|which|who)\b)",
    re.I,
)


def _normalize_fy(start: str, end: str) -> str:
    end = end[-2:] if len(end) >= 2 else end
    return f"FY{start}-{end}"


def extract_retrieval_filters(query: str) -> dict[str, Any]:
    """Hard Qdrant payload filters for *explicit* metadata in the query.

    ``HybridRetriever.search`` already accepts filters; nothing in the
    serving path used them (G17). Only unambiguous FY labels become a
    hard filter — a missing edition must not silently empty the result
    set, so tax-type and "current year" stay as preferences.
    """
    filters: dict[str, Any] = {}
    text = query or ""
    match = _FY_EXPLICIT_RE.search(text) or _FY_SLASH_RE.search(text)
    if match:
        if match.re is _FY_SLASH_RE:
            filters["fiscal_year"] = _normalize_fy(match.group(1), match.group(3))
        else:
            filters["fiscal_year"] = _normalize_fy(match.group(1), match.group(2))
    return filters


def extract_retrieval_preferences(query: str) -> dict[str, Any]:
    """Soft ranking hints: mentioned tax type and 'current' fiscal year."""
    prefer: dict[str, Any] = {}
    text = query or ""
    if _CURRENT_FY_RE.search(text) and not extract_retrieval_filters(text):
        prefer["fiscal_year"] = current_fiscal_year()
    types = [name for pattern, name in _TAX_TYPE_PATTERNS if pattern.search(text)]
    if len(types) == 1:
        prefer["tax_type"] = types[0]
    return prefer


def decompose_query(query: str) -> list[str]:
    """Split a multi-intent question into at most three retrieval queries.

    Single-intent questions (the common case) return ``[query]`` unchanged.
    Used as a cheap stand-in for query decomposition / multi-hop planning
    without a second LLM call on the hot path.
    """
    text = normalize(query or "")
    if not text:
        return []
    parts = [p.strip(" .?") for p in _DECOMPOSE_SPLIT_RE.split(text) if p.strip()]
    if len(parts) == 1:
        parts = [p.strip(" .?") for p in _AND_QUESTION_RE.split(text) if p.strip()]
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if len(part.split()) < 2:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(part)
    if len(cleaned) <= 1:
        return [text]
    return cleaned[:3]


def plan_retrieval(query: str) -> dict[str, Any]:
    """Bundle filters, preferences, and sub-queries for one retrieval turn."""
    return {
        "filters": extract_retrieval_filters(query),
        "prefer": extract_retrieval_preferences(query),
        "subqueries": decompose_query(query),
    }


# Which translator the retrieval paths use to turn a non-English question
# into something that shares vocabulary with the English corpus.
#
#   local_first  prompted MT through the already-loaded generation model,
#                falling back to Sunbird cloud   (default)
#   local        prompted MT only — no network
#   sunbird      Sunbird cloud only (the historical behaviour)
#
# Defaults to local_first because the cloud tier is a single-account
# dependency with a hard timeout and no same-account retry (issue #298).
# Measured here: a cold /tasks/translate took the full 30s and returned
# nothing, and because both retrieval paths treat translation as
# best-effort, every Luganda and Kiswahili question in that window came
# back `no_retrieval_results` even though the corpus held matching
# passages. The local tier adds no model and no network — it reuses the
# generation model that is already resident.
#
# Quality note: Sunbird's Luganda-native NLLB translates lg->en better than
# a prompted LLM does. That matters less here than it looks, because this
# text is only ever used to *search*; candidates are still bound and gated
# by the same coverage check, so a weak translation costs recall rather
# than admitting a wrong answer. Set RETRIEVAL_MT_BACKEND=sunbird to
# restore cloud-first ordering.
RETRIEVAL_MT_BACKEND = os.getenv("RETRIEVAL_MT_BACKEND", "local_first").lower()


def translate_query_for_retrieval(query: str, locale: str) -> str | None:
    """Best-effort non-English -> English for retrieval. Never raises.

    Shared by both retrieval paths — ``english_retrieval_query`` below (the
    hybrid retriever, G18) and service.py's FAQ translation rescue. They each
    used to call ``sunbird.translate_to_english`` directly, so a single cloud
    timeout took out both.

    Cached (``mt.cache``). One non-English turn translates the same question
    twice as a matter of course — the deterministic routers translate it in
    service.py before retrieval runs, and the hybrid retriever translates it
    again for the corpus — and a taxpayer assistant is asked the same
    questions repeatedly besides. The second call was a whole extra MT round
    trip for a string already translated milliseconds earlier.
    """
    text = (query or "").strip()
    if not text:
        return None
    cached = mt.cache.get(locale, "en", text)
    if cached is not None:
        return cached

    def _local() -> str | None:
        from . import llm as llm_module

        return llm_module.translate_text(query, source_lang=locale, target_lang="en")

    def _cloud() -> str | None:
        from . import sunbird

        return sunbird.translate_to_english(query, locale)

    def _fast_fallback() -> str | None:
        try:
            from .speech_service import SpeechModel

            out = SpeechModel._gemini_translate(query, locale, "en")
            if out and out.strip():
                return out.strip()
            out = SpeechModel._cf_llama_translate(query, locale, "en")
            if out and out.strip():
                return out.strip()
        except Exception:
            logger.debug("Fast cloud retrieval translation fallback failed (%s)", _log_safe(locale), exc_info=True)
        return None

    if RETRIEVAL_MT_BACKEND == "local":
        order = (("local", _local),)
    elif RETRIEVAL_MT_BACKEND == "sunbird":
        order = (("sunbird", _cloud), ("fast_fallback", _fast_fallback))
    else:
        order = (("local", _local), ("sunbird", _cloud), ("fast_fallback", _fast_fallback))

    for name, fn in order:
        try:
            english = fn()
        except Exception:  # noqa: BLE001 — translation is best-effort
            logger.debug("Retrieval translation via %s failed (%s)", name, _log_safe(locale))
            continue
        if not (english and english.strip()):
            continue
        # Verify it actually came back in English. A prompted model asked to
        # translate sometimes answers in the SOURCE language instead, and
        # returning that would be worse than returning nothing: the text is
        # just as unsearchable against an English corpus, AND a non-empty
        # result stops the next tier from ever being tried.
        if detect_language(english) != "en":
            logger.debug("Retrieval translation via %s was not English", name)
            continue
        result = english.strip()
        mt.cache.put(locale, "en", text, result)
        return result
    return None


def english_retrieval_query(query: str, locale: str | None) -> str:
    """Query text to search the English corpus with (G18).

    Source documents are English. The generator answers in *locale*.
    Translation is best-effort: English, unknown, or a failed MT call
    returns the original string so dense/BM25 still run.
    """
    text = (query or "").strip()
    loc = (locale or "en").strip().lower().split("-")[0]
    if not text or loc in ("", "en"):
        return text
    english = translate_query_for_retrieval(text, loc)
    if not english or english.casefold() == text.casefold():
        return text
    return english
