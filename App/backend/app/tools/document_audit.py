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
                        "description": "Text content, OCR extract, or line-item breakdown of the document.",
                    },
                    "doc_type": {
                        "type": "string",
                        "description": "Type of document: 'invoice', 'receipt', 'assessment', or 'generic'.",
                        "enum": ["invoice", "receipt", "assessment", "generic"],
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
                    "subtotal_ugx": {"type": ["number", "null"]},
                    "tax_ugx": {"type": ["number", "null"]},
                    "total_ugx": {"type": ["number", "null"]},
                    "effective_rate": {"type": ["number", "null"]},
                    "variance_ugx": {"type": ["number", "null"]},
                    "notes": {"type": "array", "items": {"type": "string"}},
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

    def execute(self, text: str, doc_type: str = "invoice", **kwargs: Any) -> dict[str, Any]:
        from ..documents import reconcile_tax_document
        from ..vision.ocr import (
            extract_dates,
            extract_efris_invoice_numbers,
            extract_prn_numbers,
            extract_reference_numbers,
            extract_tax_heads,
            extract_tin_numbers,
            extract_ugx_amounts,
        )

        fields = {
            "tins": extract_tin_numbers(text),
            "prns": extract_prn_numbers(text),
            "efris_invoices": extract_efris_invoice_numbers(text),
            "amounts": extract_ugx_amounts(text),
            "dates": extract_dates(text),
            "references": extract_reference_numbers(text),
            "tax_heads": extract_tax_heads(text),
        }

        recon = reconcile_tax_document(text=text, doc_type=doc_type, fields=fields, tables=[])
        explanation = "; ".join(recon.get("notes", [])) or "No financial reconciliation notes generated."
        return {
            "ok": True,
            "status": recon["status"],
            "subtotal_ugx": recon.get("subtotal_ugx"),
            "tax_ugx": recon.get("tax_ugx"),
            "total_ugx": recon.get("total_ugx"),
            "effective_rate": recon.get("effective_rate"),
            "variance_ugx": recon.get("variance_ugx"),
            "notes": recon.get("notes", []),
            "explanation": explanation,
            "fields": fields,
        }


ToolRegistry.register(AuditTaxDocumentTool())
