"""Instruction prompt for the vision model that reads uploaded documents."""

from __future__ import annotations

# URA document analysis prompt — tuned for Ugandan tax documents
VLM_PROMPT = (
    "Analyze this Uganda Revenue Authority document carefully.\n"
    "Identify:\n"
    "1) Document type: receipt, TIN certificate, tax assessment notice, "
    "customs declaration, filing form, EFRIS invoice, or other.\n"
    "2) Key fields: amounts (in UGX), dates, TIN numbers, taxpayer names, "
    "tax types, reference numbers.\n"
    "3) Brief summary of what this document shows.\n\n"
    "Format your response as:\n"
    "TYPE: <document type>\n"
    "FIELDS: <key-value pairs>\n"
    "SUMMARY: <1-2 sentence summary>"
)
