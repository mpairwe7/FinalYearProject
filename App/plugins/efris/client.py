"""Client SDK for interacting with URA EFRIS system."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    CreditNoteReason,
    CreditNoteRequest,
    FiscalInvoiceRequest,
    InvoiceItem,
    InvoiceType,
    InvoiceVerificationRequest,
    TaxRateCategory,
)
from .service import EfrisService

logger = logging.getLogger(__name__)


class EfrisClient:
    """Client for the local EFRIS simulator; live URA calls are not implemented."""

    def __init__(
        self,
        service: EfrisService | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        del api_base, api_key  # retained for compatibility; remote calls are not implemented
        self._service = service or EfrisService()

    @property
    def is_live(self) -> bool:
        return False

    def ping(self) -> bool:
        """Report local simulator readiness, not external EFRIS connectivity."""
        return True

    def get_taxpayer_profile(self, tin: str) -> dict[str, Any]:
        """Query taxpayer registration and terminal readiness on EFRIS."""
        tin = str(tin or "").strip()
        if not tin:
            return {"ok": False, "error": "Tax Identification Number (TIN) is required"}

        profile = self._service.get_taxpayer_profile(tin)
        if not profile:
            return {
                "ok": False,
                "tin": tin,
                "registered": False,
                "message": f"Taxpayer with TIN '{tin}' is not registered on EFRIS.",
            }
        return {
            "ok": True,
            "tin": tin,
            "registered": True,
            "profile": profile.to_dict(),
        }

    def issue_invoice(
        self,
        seller_tin: str,
        items: list[dict[str, Any]],
        buyer_tin: str | None = None,
        buyer_name: str | None = None,
        invoice_type: str = "B2B",
        currency: str = "UGX",
        branch_id: str = "MAIN_HQ",
        cashier_id: str = "CASHIER_01",
        offline_reference: str | None = None,
    ) -> dict[str, Any]:
        """Create a sample invoice with an FDN-shaped reference in the local simulator."""
        seller_tin = str(seller_tin or "").strip()
        if not seller_tin:
            return {"ok": False, "error": "seller_tin is required"}
        if not items:
            return {"ok": False, "error": "Invoice items cannot be empty"}

        typed_items: list[InvoiceItem] = []
        for idx, item in enumerate(items, 1):
            try:
                tax_cat_str = item.get("tax_category", "STANDARD_18")
                tax_cat = TaxRateCategory(tax_cat_str) if tax_cat_str in TaxRateCategory._value2member_map_ else TaxRateCategory.STANDARD
                qty = float(item.get("quantity", 1.0))
                price = float(item.get("unit_price", 0.0))
                if qty <= 0:
                    return {"ok": False, "error": f"Item {idx}: quantity must be greater than zero"}
                if price < 0:
                    return {"ok": False, "error": f"Item {idx}: unit_price must not be negative"}
                tax_rate = 0.18 if tax_cat == TaxRateCategory.STANDARD else 0.0
                typed_items.append(
                    InvoiceItem(
                        commodity_code=str(item.get("commodity_code", f"COMM-{idx:03d}")),
                        description=str(item.get("description", "Standard Supply")),
                        quantity=qty,
                        unit_price=price,
                        tax_rate=tax_rate,
                        tax_category=tax_cat,
                    )
                )
            except Exception:  # noqa: BLE001
                return {"ok": False, "error": f"Invalid item format at index {idx}"}

        inv_type = InvoiceType.B2B
        if invoice_type.upper() in ("B2C", "RETAIL"):
            inv_type = InvoiceType.B2C
        elif invoice_type.upper() in ("B2G", "GOVERNMENT"):
            inv_type = InvoiceType.B2G

        req = FiscalInvoiceRequest(
            seller_tin=seller_tin,
            buyer_tin=str(buyer_tin).strip() if buyer_tin else None,
            buyer_name=str(buyer_name).strip() if buyer_name else None,
            invoice_type=inv_type,
            currency=currency,
            branch_id=branch_id,
            cashier_id=cashier_id,
            items=typed_items,
            offline_reference=offline_reference,
        )
        resp = self._service.issue_fiscal_invoice(req)
        return resp.to_dict()

    def verify_invoice(self, fdn: str, verification_code: str | None = None) -> dict[str, Any]:
        """Verify an FDN against the central EFRIS registry."""
        fdn = str(fdn or "").strip()
        if not fdn:
            return {"ok": False, "error": "Fiscal Document Number (FDN) is required"}

        req = InvoiceVerificationRequest(
            fdn=fdn,
            verification_code=str(verification_code).strip() if verification_code else None,
        )
        resp = self._service.verify_invoice(req)
        return resp.to_dict()

    def create_credit_note(
        self,
        original_fdn: str,
        seller_tin: str,
        reason: str,
        adjusted_amount: float,
        description: str = "",
        buyer_tin: str | None = None,
    ) -> dict[str, Any]:
        """Create a credit note against an already-issued fiscal document."""
        original_fdn = str(original_fdn or "").strip()
        seller_tin = str(seller_tin or "").strip()
        if not original_fdn or not seller_tin:
            return {"ok": False, "error": "original_fdn and seller_tin are required"}

        try:
            cn_reason = CreditNoteReason(reason)
        except ValueError:
            valid_reasons = [r.value for r in CreditNoteReason]
            return {
                "ok": False,
                "error": f"Invalid reason '{reason}'. Allowed reasons: {valid_reasons}",
            }

        req = CreditNoteRequest(
            original_fdn=original_fdn,
            seller_tin=seller_tin,
            reason=cn_reason,
            adjusted_amount=float(adjusted_amount),
            description=description,
            buyer_tin=str(buyer_tin).strip() if buyer_tin else None,
        )
        resp = self._service.apply_credit_note(req)
        return resp.to_dict()

    def get_stock(self, tin: str, commodity_code: str | None = None) -> dict[str, Any]:
        """Retrieve current inventory stock balance for taxpayer."""
        tin = str(tin or "").strip()
        if not tin:
            return {"ok": False, "error": "TIN is required"}

        balances = self._service.get_stock_balance(tin, commodity_code)
        return {
            "ok": True,
            "tin": tin,
            "count": len(balances),
            "stock_items": balances,
        }

    def record_stock_in(
        self,
        tin: str,
        commodity_code: str,
        description: str,
        quantity: float,
        unit_cost: float,
        unit_of_measure: str = "PIECES",
        category: str = "General",
    ) -> dict[str, Any]:
        """Add inventory stock items from purchases or imports."""
        return self._service.record_stock_in(
            tin=tin,
            commodity_code=commodity_code,
            description=description,
            quantity=float(quantity),
            unit_cost=float(unit_cost),
            unit_of_measure=unit_of_measure,
            category=category,
        )
