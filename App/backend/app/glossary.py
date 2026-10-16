"""Curated URA Statutory Tax Terminology Glossary for Cross-Lingual Translation.

Provides authoritative translations between English, Luganda, Swahili,
Runyankole, and Acholi for Ugandan tax administration, customs, and civic
formalisation.
"""

from __future__ import annotations

import re
from typing import Final

#: Canonical statutory tax terminology mappings for URA
URA_STATUTORY_GLOSSARY: Final[dict[str, dict[str, str]]] = {
    "value added tax": {
        "lg": "Omusolo gw'Okwongera ku Muwendo (VAT)",
        "sw": "Kodi ya Ongezeko la Thamani (VAT)",
        "nyn": "Omushoro gw'Okwongyera aha Muhendo (VAT)",
        "ach": "Mucoro me Medo Wel (VAT)",
    },
    "vat": {
        "lg": "VAT",
        "sw": "VAT",
        "nyn": "VAT",
        "ach": "VAT",
    },
    "pay as you earn": {
        "lg": "Omusolo gwa PAYE ku musaala",
        "sw": "Kodi ya PAYE ya mshahara",
        "nyn": "Omushoro gwa PAYE aha mushaara",
        "ach": "Mucoro me PAYE i kom mucara",
    },
    "paye": {
        "lg": "PAYE",
        "sw": "PAYE",
        "nyn": "PAYE",
        "ach": "PAYE",
    },
    "taxpayer identification number": {
        "lg": "Namba y'Omusasuzi w'Omusolo (TIN)",
        "sw": "Nambari ya Utambulisho wa Mlipakodi (TIN)",
        "nyn": "Enamba y'Omushasuzi w'Omushoro (TIN)",
        "ach": "Namba me Ngec pa Lacul Mucoro (TIN)",
    },
    "tin": {
        "lg": "TIN",
        "sw": "TIN",
        "nyn": "TIN",
        "ach": "TIN",
    },
    "efris": {
        "lg": "EFRIS",
        "sw": "EFRIS",
        "nyn": "EFRIS",
        "ach": "EFRIS",
    },
    "digital tax stamps": {
        "lg": "Sitiampu z'Omusolo eza Digito (DTS)",
        "sw": "Stempu za Kodi za Kidijitali (DTS)",
        "nyn": "Sitaampu z'Omushoro eza Digito (DTS)",
        "ach": "Citam me Mucoro me Dijitol (DTS)",
    },
    "dts": {
        "lg": "DTS",
        "sw": "DTS",
        "nyn": "DTS",
        "ach": "DTS",
    },
    "withholding tax": {
        "lg": "Omusolo ogukwatibwaako ku nsasula (WHT)",
        "sw": "Kodi ya Zuio (WHT)",
        "nyn": "Omushoro ogurikukwatirwaho aha nshashura (WHT)",
        "ach": "Mucoro ma Kigengo i kom Culu (WHT)",
    },
    "wht": {
        "lg": "WHT",
        "sw": "WHT",
        "nyn": "WHT",
        "ach": "WHT",
    },
    "rental income tax": {
        "lg": "Omusolo gw'Ennyumba ezipangisibwa",
        "sw": "Kodi ya Upangishaji",
        "nyn": "Omushoro gw'Amaju agarikupangisibwa",
        "ach": "Mucoro me Ot ma Kipango",
    },
    "rental tax": {
        "lg": "Omusolo gw'Ennyumba ezipangisibwa",
        "sw": "Kodi ya Upangishaji",
        "nyn": "Omushoro gw'Amaju agarikupangisibwa",
        "ach": "Mucoro me Ot ma Kipango",
    },
    "corporation tax": {
        "lg": "Omusolo gw'Amakampuni (Corporation Tax)",
        "sw": "Kodi ya Kampuni (Corporation Tax)",
        "nyn": "Omushoro gw'Amakampuni (Corporation Tax)",
        "ach": "Mucoro me Kampuni (Corporation Tax)",
    },
    "customs duty": {
        "lg": "Omusolo gw'oku mwalo n'ensalo (Customs Duty)",
        "sw": "Ushuru wa Forodha (Customs Duty)",
        "nyn": "Omushoro gw'eby'ensharo (Customs Duty)",
        "ach": "Mucoro me Wang Lobo (Customs Duty)",
    },
    "customs valuation": {
        "lg": "Okupima omuwendo gw'ebintu mu customs",
        "sw": "Uthamini wa forodha (Customs Valuation)",
        "nyn": "Okupima omuhendo gw'ebintu omu customs",
        "ach": "Pimo wel jami i customs",
    },
    "tax clearance certificate": {
        "lg": "Satifikeeti y'Okumalaayo Omusolo (TCC)",
        "sw": "Cheti cha Kuondolewa Kodi (TCC)",
        "nyn": "Satifikeeti y'Okuhikiiriza Omushoro (TCC)",
        "ach": "Waraga ma Nyuto Culu Mucoro Maber (TCC)",
    },
    "tcc": {
        "lg": "TCC",
        "sw": "TCC",
        "nyn": "TCC",
        "ach": "TCC",
    },
    "tax return": {
        "lg": "Alipoota y'omusolo (Tax Return)",
        "sw": "Marejesho ya kodi (Tax Return)",
        "nyn": "Ekihandiiko ky'omushoro (Tax Return)",
        "ach": "Ripot me mucoro (Tax Return)",
    },
    "objection": {
        "lg": "Okwemulugunya ku musolo (Objection)",
        "sw": "Pingamizi la kodi (Objection)",
        "nyn": "Okujurira aha mushoro (Objection)",
        "ach": "Kwero pimo mucoro (Objection)",
    },
    "exemption": {
        "lg": "Okusonyiyibwa omusolo",
        "sw": "Msamaha wa kodi",
        "nyn": "Okusaasirwa omushoro",
        "ach": "Weko culu mucoro",
    },
    "presumptive tax": {
        "lg": "Omusolo oguteeberezebwa ku busuubuzi obutono",
        "sw": "Kodi ya makadirio ya biashara ndogo",
        "nyn": "Omushoro gw'obushuubuzi bukye (Presumptive Tax)",
        "ach": "Mucoro me biacara matidi (Presumptive Tax)",
    },
    "duty-free allowance": {
        "lg": "Eby'amaguzi ebitasasulwako musolo ku baggage",
        "sw": "Kiwango cha mizigo isiyotozwa ushuru",
        "nyn": "Ebintu ebitarikushashurirwa mushoro",
        "ach": "Jami ma pe kiculo iye mucoro",
    },
    "tax assessment": {
        "lg": "Okubalirira omusolo (Tax Assessment)",
        "sw": "Tathmini ya kodi (Tax Assessment)",
        "nyn": "Okubaririra omushoro (Tax Assessment)",
        "ach": "Pimo wel mucoro (Tax Assessment)",
    },
    "assessment": {
        "lg": "Okubalirira omusolo",
        "sw": "Tathmini ya kodi",
        "nyn": "Okubaririra omushoro",
        "ach": "Pimo wel mucoro",
    },
    "penal tax": {
        "lg": "Omusolo gw'ekibonerezo (Penal Tax)",
        "sw": "Adhabu ya kodi (Penal Tax)",
        "nyn": "Omushoro gw'ekibonerezo (Penal Tax)",
        "ach": "Mucoro me pwod (Penal Tax)",
    },
    "penalty": {
        "lg": "Ekibonerezo",
        "sw": "Faini au adhabu",
        "nyn": "Ekibonerezo",
        "ach": "Pwod onyo fain",
    },
    "tax appeals tribunal": {
        "lg": "Kkooti Ewulira Okwemulugunya ku Misolo (TAT)",
        "sw": "Mahakama ya Rufaa ya Kodi (TAT)",
        "nyn": "Kooti Erikuhurira Okujurira kw'Emishoro (TAT)",
        "ach": "Gang Ngol Kop me Mucoro (TAT)",
    },
    "tat": {
        "lg": "TAT",
        "sw": "TAT",
        "nyn": "TAT",
        "ach": "TAT",
    },
    "capital gains tax": {
        "lg": "Omusolo ku magoba g'ebintu ebitundiddwa (Capital Gains Tax)",
        "sw": "Kodi ya faida ya mtaji (Capital Gains Tax)",
        "nyn": "Omushoro aha magoba g'ebintu ebiguziibwe (Capital Gains Tax)",
        "ach": "Mucoro i kom magoba me cato jami (Capital Gains Tax)",
    },
    "stamp duty": {
        "lg": "Omusolo gwa sitaampu ku biwandiiko (Stamp Duty)",
        "sw": "Ushuru wa stempu (Stamp Duty)",
        "nyn": "Omushoro gwa sitaampu aha bihandiiko (Stamp Duty)",
        "ach": "Mucoro me citam i kom waraga (Stamp Duty)",
    },
    "eaccma": {
        "lg": "EACCMA",
        "sw": "EACCMA",
        "nyn": "EACCMA",
        "ach": "EACCMA",
    },
    "prn": {
        "lg": "PRN",
        "sw": "PRN",
        "nyn": "PRN",
        "ach": "PRN",
    },
    "payment registration number": {
        "lg": "Namba y'okusasulirako omusolo (PRN)",
        "sw": "Nambari ya Usajili wa Malipo (PRN)",
        "nyn": "Enamba y'okushashuriraho omushoro (PRN)",
        "ach": "Namba me Culu Mucoro (PRN)",
    },
    "turnover": {
        "lg": "Ennyingiza y'eby'obusuubuzi (Turnover)",
        "sw": "Mauzo ya biashara (Turnover)",
        "nyn": "Entaasya y'obushuubuzi (Turnover)",
        "ach": "Lim ma odonyo i biacara (Turnover)",
    },
    "threshold": {
        "lg": "Ekkomo ery'omusolo (Threshold)",
        "sw": "Kiwango cha chini cha kodi (Threshold)",
        "nyn": "Obugarukiro bw'omushoro (Threshold)",
        "ach": "Rwom me cako culu mucoro (Threshold)",
    },
    "registration": {
        "lg": "Okuwandiisa (Registration)",
        "sw": "Usajili wa kodi (Registration)",
        "nyn": "Okwehandiikisa (Registration)",
        "ach": "Coyo nying (Registration)",
    },
    "withholding": {
        "lg": "Okukendeeza ku musaala (Withholding)",
        "sw": "Kodi ya zuio (Withholding)",
        "nyn": "Okukwatiraho omushoro (Withholding)",
        "ach": "Gengo mucoro (Withholding)",
    },
}

_GLOSSARY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE), term)
    for term in sorted(URA_STATUTORY_GLOSSARY.keys(), key=len, reverse=True)
]


def get_translation_glossary_hints(text: str, target_lang: str) -> str:
    """Return concise glossary constraints for translation into *target_lang*."""
    norm_lang = target_lang
    if norm_lang not in ("lg", "sw", "nyn", "ach") or not text:
        return ""

    matched_terms: list[str] = []
    seen: set[str] = set()
    for pattern, term in _GLOSSARY_PATTERNS:
        if pattern.search(text):
            trans = URA_STATUTORY_GLOSSARY[term].get(norm_lang)
            if trans and term not in seen:
                seen.add(term)
                matched_terms.append(f"{term.title()} -> {trans}")
            if len(matched_terms) >= 4:
                break

    if not matched_terms:
        return ""
    return f" Use official terminology: {'; '.join(matched_terms)}."
