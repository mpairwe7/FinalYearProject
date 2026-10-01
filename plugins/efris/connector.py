"""Agentic connector bridging URA EFRIS sample system into the URA agentic tool framework."""

from __future__ import annotations

import logging
from typing import Any

from plugins.base import SystemConnector, Tool, ToolSchema

from .client import EfrisClient
from .service import EfrisService

logger = logging.getLogger(__name__)


class EfrisFiscalInvoiceTool(Tool):
    """Tool for issuing and verifying EFRIS electronic fiscal invoices and receipts."""

    def __init__(self, client: EfrisClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="efris_fiscal_invoice",
            description=(
                "Issue or verify URA EFRIS fiscal documents. Allows generating a 20-digit "
                "Fiscal Document Number (FDN) with QR code or authenticating an existing "
                "invoice FDN and verification code."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["issue", "verify"],
                        "description": "Operation: 'issue' to create a fiscal invoice, or 'verify' to validate an FDN.",
                    },
                    "seller_tin": {
                        "type": "string",
                        "description": "10-digit TIN of the selling business (required for 'issue').",
                    },
                    "buyer_tin": {
                        "type": "string",
                        "description": "10-digit TIN of the buyer (optional for B2C retail).",
                    },
                    "buyer_name": {
                        "type": "string",
                        "description": "Name of the customer or business.",
                    },
                    "invoice_type": {
                        "type": "string",
                        "enum": ["B2B", "B2C", "B2G"],
                        "description": "Transaction category. Defaults to B2B.",
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "commodity_code": {"type": "string"},
                                "description": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit_price": {"type": "number"},
                                "tax_category": {"type": "string"},
                            },
                            "required": ["description", "quantity", "unit_price"],
                            "additionalProperties": False,
                        },
                        "description": "List of supplied goods/services with quantities and prices (for 'issue').",
                    },
                    "fdn": {
                        "type": "string",
                        "description": "20-digit Fiscal Document Number to validate (required for 'verify').",
                    },
                    "verification_code": {
                        "type": "string",
                        "description": "6-character verification code printed below the receipt barcode (for 'verify').",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "action": {"type": "string"},
                    "data": {"type": "object"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            namespace="efris",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin", "taxpayer"),
            read_only=False,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

    def execute(
        self,
        action: str,
        seller_tin: str = "",
        buyer_tin: str | None = None,
        buyer_name: str | None = None,
        invoice_type: str = "B2B",
        items: list[dict[str, Any]] | None = None,
        fdn: str = "",
        verification_code: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        action_clean = str(action or "").lower().strip()
        if action_clean == "issue":
            if not seller_tin:
                return {"ok": False, "error": "seller_tin is required for invoice issuance"}
            if not items:
                return {"ok": False, "error": "items list is required for invoice issuance"}
            result = self._client.issue_invoice(
                seller_tin=seller_tin,
                items=items,
                buyer_tin=buyer_tin,
                buyer_name=buyer_name,
                invoice_type=invoice_type,
            )
            return {"ok": result.get("ok", False), "action": "issue", "data": result}
        elif action_clean == "verify":
            if not fdn:
                return {"ok": False, "error": "fdn is required for verification"}
            result = self._client.verify_invoice(fdn=fdn, verification_code=verification_code)
            return {"ok": result.get("ok", False), "action": "verify", "data": result}
        else:
            return {"ok": False, "error": f"Unknown action '{action}'. Use 'issue' or 'verify'."}


class EfrisTaxpayerStatusTool(Tool):
    """Tool for checking taxpayer EFRIS registration and device readiness."""

    def __init__(self, client: EfrisClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="efris_taxpayer_status",
            description=(
                "Query taxpayer EFRIS compliance status, integration mode (EFD vs system-to-system), "
                "active terminal serial numbers, and VAT status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tin": {
                        "type": "string",
                        "description": "10-digit Tax Identification Number (TIN).",
                    }
                },
                "required": ["tin"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "tin": {"type": "string"},
                    "registered": {"type": "boolean"},
                    "profile": {"type": "object"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="efris",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, tin: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.get_taxpayer_profile(tin=tin)


class EfrisStockManagementTool(Tool):
    """Tool for inspecting stock balances and recording stock-in on EFRIS."""

    def __init__(self, client: EfrisClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="efris_stock_management",
            description=(
                "Manage taxpayer stock inventory on EFRIS. Query current stock quantities on hand "
                "or record stock additions from local purchases or customs imports."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["query", "stock_in"],
                        "description": "'query' to inspect stock balances, 'stock_in' to add new inventory.",
                    },
                    "tin": {
                        "type": "string",
                        "description": "10-digit Taxpayer TIN.",
                    },
                    "commodity_code": {
                        "type": "string",
                        "description": "URA commodity classification code (e.g., '50202301' for bottled water).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Item description (for stock_in).",
                    },
                    "quantity": {
                        "type": "number",
                        "description": "Quantity to add (for stock_in).",
                    },
                    "unit_cost": {
                        "type": "number",
                        "description": "Unit purchase cost in UGX (for stock_in).",
                    },
                },
                "required": ["action", "tin"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "tin": {"type": "string"},
                    "data": {"type": "object"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            namespace="efris",
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        action: str,
        tin: str = "",
        commodity_code: str | None = None,
        description: str = "",
        quantity: float = 0.0,
        unit_cost: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        action_clean = str(action or "").lower().strip()
        if action_clean == "query":
            return self._client.get_stock(tin=tin, commodity_code=commodity_code)
        elif action_clean == "stock_in":
            if not commodity_code:
                return {"ok": False, "error": "commodity_code is required for stock_in"}
            if quantity <= 0:
                return {"ok": False, "error": "quantity must be greater than zero"}
            return self._client.record_stock_in(
                tin=tin,
                commodity_code=commodity_code,
                description=description or "Stock Item",
                quantity=quantity,
                unit_cost=unit_cost,
            )
        else:
            return {"ok": False, "error": f"Unknown action '{action}'. Use 'query' or 'stock_in'."}


class EfrisCreditNoteTool(Tool):
    """Tool for applying credit notes to adjust already-issued fiscal documents."""

    def __init__(self, client: EfrisClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="efris_credit_note",
            description=(
                "Create a credit note on URA EFRIS against an issued fiscal document (FDN). "
                "Required when goods are returned, price discounts are applied, or invoicing errors occur. "
                "Requires elevated consent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "original_fdn": {
                        "type": "string",
                        "description": "20-digit Fiscal Document Number of the invoice being adjusted.",
                    },
                    "seller_tin": {
                        "type": "string",
                        "description": "10-digit TIN of the selling business.",
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["GOODS_RETURNED", "PRICE_DISCOUNT", "INVOICING_ERROR"],
                        "description": "Statutory reason for the credit note.",
                    },
                    "adjusted_amount": {
                        "type": "number",
                        "description": "Total gross amount in UGX to adjust.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed explanation of the adjustment reason.",
                    },
                },
                "required": ["original_fdn", "seller_tin", "reason", "adjusted_amount"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "credit_note_number": {"type": "string"},
                    "adjusted_gross": {"type": "number"},
                    "adjusted_vat": {"type": "number"},
                    "status": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="high",
            requires_confirmation=True,
            namespace="efris",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            scope_exempt_roles=("ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

    def execute(
        self,
        original_fdn: str,
        seller_tin: str,
        reason: str,
        adjusted_amount: float,
        description: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.create_credit_note(
            original_fdn=original_fdn,
            seller_tin=seller_tin,
            reason=reason,
            adjusted_amount=adjusted_amount,
            description=description,
        )


class EfrisConnector(SystemConnector):
    """System connector for URA EFRIS."""

    def __init__(self, service: EfrisService | None = None) -> None:
        self._service = service or EfrisService()
        self._client = EfrisClient(service=self._service)
        self._tools: list[Tool] = [
            EfrisFiscalInvoiceTool(self._client),
            EfrisTaxpayerStatusTool(self._client),
            EfrisStockManagementTool(self._client),
            EfrisCreditNoteTool(self._client),
        ]
        self._healthy = False

    @property
    def system_name(self) -> str:
        return "efris"

    @property
    def client(self) -> EfrisClient:
        return self._client

    @property
    def service(self) -> EfrisService:
        return self._service

    def initialize(self) -> bool:
        self._healthy = self._client.ping()
        logger.info("EfrisConnector initialized (healthy=%s)", self._healthy)
        return self._healthy

    def is_healthy(self) -> bool:
        return self._healthy and self._client.ping()

    def get_tools(self) -> list[Tool]:
        return list(self._tools)

    def get_status(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "display_name": "Electronic Fiscal Receipting & Invoicing System (EFRIS)",
            "healthy": self.is_healthy(),
            "live_mode": self._client.is_live,
            "tool_count": len(self._tools),
            "tools": [t.schema.name for t in self._tools],
            "database": self._service.get_stats(),
        }

    def shutdown(self) -> None:
        self._healthy = False
        logger.info("EfrisConnector shut down")
