"""Automated tax document audit & reconciliation tool (MCP).

Exposes the financial arithmetic reconciliation and statutory tax compliance
auditor from ``app.documents`` to the MCP client layer and specialist agents.
"""

from __future__ import annotations

from typing import Any

from . import Tool, ToolRegistry, ToolSchema


class AuditTaxDocumentTool(Tool):
    """Audit financial document text, reconciling VAT, totals, and URA tax identifiers."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="audit_tax_document",
            description=(
                "Audit a financial tax document (invoice, receipt, or assessment): "
                "extract and reconcile taxable subtotal, VAT at the 18% standard rate, "
                "grand total, and verify 10-digit Uganda TINs and PRN payment numbers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "maxLength": 200000,
                        "description": "Text content, OCR extract, or line-item breakdown of the document.",
                    },
                    "doc_type": {
                        "type": "string",
                        "description": "Type of document: 'invoice', 'receipt', 'assessment', or 'generic'.",
                        "enum": ["invoice", "receipt", "assessment", "generic"],
                    },
                    "document_id": {
                        "type": "string",
                        "description": "Optional uploaded document ID to include extracted table summaries in reconciliation.",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "status": {"type": "string"},
                    "currency": {"type": "string"},
                    "subtotal_ugx": {"type": ["number", "null"]},
                    "tax_ugx": {"type": ["number", "null"]},
                    "wht_ugx": {"type": ["number", "null"]},
                    "total_ugx": {"type": ["number", "null"]},
                    "effective_rate": {"type": ["number", "null"]},
                    "variance_ugx": {"type": ["number", "null"]},
                    "notes": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "object"},
                    "explanation": {"type": "string"},
                },
                "required": ["ok", "status", "notes"],
            },
            namespace="tax_calculator",
            risk="low",
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
            title="Audit tax document",
        )

    def execute(
        self,
        text: str,
        doc_type: str = "invoice",
        document_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        from ..documents import get_document, reconcile_tax_document
        from ..guardrails import scan_retrieved_text
        from ..vision.ocr import (
            extract_dates,
            extract_efris_invoice_numbers,
            extract_nin_numbers,
            extract_prn_numbers,
            extract_reference_numbers,
            extract_tax_heads,
            extract_tin_numbers,
            extract_ugx_amounts,
            strip_hidden_control_and_bidi,
        )

        clean_text, _ = scan_retrieved_text(strip_hidden_control_and_bidi(str(text or "")[:200_000]))
        doc_tables: list[Any] = []
        if document_id:
            try:
                rec = get_document(str(document_id).strip())
                if rec is not None and getattr(rec, "tables", None):
                    doc_tables = list(rec.tables)
            except Exception:
                doc_tables = []

        fields = {
            "tins": extract_tin_numbers(clean_text),
            "nins": extract_nin_numbers(clean_text),
            "prns": extract_prn_numbers(clean_text),
            "efris_invoices": extract_efris_invoice_numbers(clean_text),
            "amounts": extract_ugx_amounts(clean_text),
            "dates": extract_dates(clean_text),
            "references": extract_reference_numbers(clean_text),
            "tax_heads": extract_tax_heads(clean_text),
        }

        recon = reconcile_tax_document(text=clean_text, doc_type=doc_type, fields=fields, tables=doc_tables)
        notes = list(recon.get("notes", []) or [])
        if len(notes) > 1:
            explanation = "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1))
        else:
            explanation = notes[0] if notes else "No financial reconciliation notes generated."
        return {
            "ok": True,
            "status": recon["status"],
            "currency": recon.get("currency", "UGX"),
            "subtotal_ugx": recon.get("subtotal_ugx"),
            "tax_ugx": recon.get("tax_ugx"),
            "wht_ugx": recon.get("wht_ugx"),
            "total_ugx": recon.get("total_ugx"),
            "effective_rate": recon.get("effective_rate"),
            "variance_ugx": recon.get("variance_ugx"),
            "notes": recon.get("notes", []),
            "explanation": explanation,
            "fields": fields,
        }


ToolRegistry.register(AuditTaxDocumentTool())
