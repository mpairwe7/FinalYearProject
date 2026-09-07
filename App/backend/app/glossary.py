"""Curated URA Statutory Tax Terminology Glossary for Cross-Lingual Translation.

Provides authoritative translations between English, Luganda, and Swahili for
Ugandan tax administration, customs, and civic formalisation.
"""

from __future__ import annotations

import re
from typing import Final

#: Canonical statutory tax terminology mappings for URA
URA_STATUTORY_GLOSSARY: Final[dict[str, dict[str, str]]] = {
    "value added tax": {
        "lg": "Omusolo gw'Okwongera ku Muwendo (VAT)",
        "sw": "Kodi ya Ongezeko la Thamani (VAT)",
    },
    "vat": {
        "lg": "VAT",
        "sw": "VAT",
    },
    "pay as you earn": {
        "lg": "Omusolo gwa PAYE ku musaala",
        "sw": "Kodi ya PAYE ya mshahara",
    },
    "paye": {
        "lg": "PAYE",
        "sw": "PAYE",
    },
    "taxpayer identification number": {
        "lg": "Namba y'Omusasuzi w'Omusolo (TIN)",
        "sw": "Nambari ya Utambulisho wa Mlipakodi (TIN)",
    },
    "tin": {
        "lg": "TIN",
        "sw": "TIN",
    },
    "efris": {
        "lg": "EFRIS",
        "sw": "EFRIS",
    },
    "digital tax stamps": {
        "lg": "Sitiampu z'Omusolo eza Digito (DTS)",
        "sw": "Stempu za Kodi za Kidijitali (DTS)",
    },
    "dts": {
        "lg": "DTS",
        "sw": "DTS",
    },
    "withholding tax": {
        "lg": "Omusolo ogukwatibwaako ku nsasula (WHT)",
        "sw": "Kodi ya Zuio (WHT)",
    },
    "wht": {
        "lg": "WHT",
        "sw": "WHT",
    },
    "rental income tax": {
        "lg": "Omusolo gw'Ennyumba ezipangisibwa",
        "sw": "Kodi ya Upangishaji",
    },
    "rental tax": {
        "lg": "Omusolo gw'Ennyumba ezipangisibwa",
        "sw": "Kodi ya Upangishaji",
    },
    "corporation tax": {
        "lg": "Omusolo gw'Amakampuni (Corporation Tax)",
        "sw": "Kodi ya Kampuni (Corporation Tax)",
    },
    "customs duty": {
        "lg": "Omusolo gw'oku mwalo n'ensalo (Customs Duty)",
        "sw": "Ushuru wa Forodha (Customs Duty)",
    },
    "customs valuation": {
        "lg": "Okupima omuwendo gw'ebintu mu customs",
        "sw": "Uthamini wa forodha (Customs Valuation)",
    },
    "tax clearance certificate": {
        "lg": "Satifikeeti y'Okumalaayo Omusolo (TCC)",
        "sw": "Cheti cha Kuondolewa Kodi (TCC)",
    },
    "tcc": {
        "lg": "TCC",
        "sw": "TCC",
    },
    "tax return": {
        "lg": "Alipoota y'omusolo (Tax Return)",
        "sw": "Marejesho ya kodi (Tax Return)",
    },
    "objection": {
        "lg": "Okwemulugunya ku musolo (Objection)",
        "sw": "Pingamizi la kodi (Objection)",
    },
    "exemption": {
        "lg": "Okusonyiyibwa omusolo",
        "sw": "Msamaha wa kodi",
    },
    "presumptive tax": {
        "lg": "Omusolo oguteeberezebwa ku busuubuzi obutono",
        "sw": "Kodi ya makadirio ya biashara ndogo",
    },
}

_GLOSSARY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE), term)
    for term in sorted(URA_STATUTORY_GLOSSARY.keys(), key=len, reverse=True)
]


def get_translation_glossary_hints(text: str, target_lang: str) -> str:
    """Return concise glossary constraints for translation into *target_lang*."""
    if target_lang not in ("lg", "sw") or not text:
        return ""

    matched_terms: list[str] = []
    seen: set[str] = set()
    for pattern, term in _GLOSSARY_PATTERNS:
        if pattern.search(text):
            trans = URA_STATUTORY_GLOSSARY[term].get(target_lang)
            if trans and term not in seen:
                seen.add(term)
                matched_terms.append(f"{term.title()} -> {trans}")
            if len(matched_terms) >= 4:
                break

    if not matched_terms:
        return ""
    return f" Use official terminology: {'; '.join(matched_terms)}."
