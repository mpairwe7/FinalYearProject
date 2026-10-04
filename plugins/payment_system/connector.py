"""Agentic connector bridging URA Payment System into the URA agentic tool framework."""

from __future__ import annotations

import logging
from typing import Any

from plugins.base import SystemConnector, Tool, ToolSchema

from .client import PaymentClient
from .service import PaymentService

logger = logging.getLogger(__name__)


class PaymentGeneratePrnTool(Tool):
    """Tool for generating a sample PRN-shaped reference in the local simulator."""

    def __init__(self, client: PaymentClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="payment_generate_prn",
            description=(
                "Generate a sample PRN-shaped reference in the local payment simulator. It is not a valid URA PRN "
                "and cannot be used to pay tax, fees, a bank, or a mobile-money provider."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "taxpayer_name": {
                        "type": "string",
                        "description": "Full legal name of the paying individual or business.",
                    },
                    "amount_ugx": {
                        "type": "number",
                        "description": "Exact assessment liability amount in Uganda Shillings (UGX).",
                    },
                    "taxpayer_tin": {
                        "type": "string",
                        "description": "10-digit TIN (optional for Non-TIN government fees).",
                    },
                    "tax_head": {
                        "type": "string",
                        "description": "Tax head or fee category (e.g., VAT_STANDARD, PAYE, CORPORATION_TAX, PASSPORT_FEE).",
                    },
                    "payment_channel": {
                        "type": "string",
                        "enum": ["COMMERCIAL_BANK", "MOBILE_MONEY", "VISA_MASTERCARD"],
                        "description": "Intended settlement channel.",
                    },
                },
                "required": ["taxpayer_name", "amount_ugx"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "prn": {"type": "string"},
                    "taxpayer_name": {"type": "string"},
                    "amount_ugx": {"type": "number"},
                    "expiry_date": {"type": "string"},
                    "status": {"type": "string"},
                    "payment_slip_url": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            requires_confirmation=True,
            namespace="payment_system",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        taxpayer_name: str,
        amount_ugx: float,
        taxpayer_tin: str | None = None,
        tax_head: str = "VAT_STANDARD",
        payment_channel: str = "COMMERCIAL_BANK",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.generate_prn(
            taxpayer_name=taxpayer_name,
            amount_ugx=amount_ugx,
            taxpayer_tin=taxpayer_tin,
            tax_head=tax_head,
            payment_channel=payment_channel,
        )


class PaymentViewStatusTool(Tool):
    """Tool for reading a sample payment status from the local simulator."""

    def __init__(self, client: PaymentClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="payment_view_status",
            description=(
                "Read status stored for a sample reference in the local payment simulator. This does not check "
                "bank settlement, a payment network, or the URA tax ledger."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prn": {
                        "type": "string",
                        "description": "10 or 12-digit Payment Registration Number.",
                    }
                },
                "required": ["prn"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "prn": {"type": "string"},
                    "taxpayer_name": {"type": "string"},
                    "amount_ugx": {"type": "number"},
                    "status": {"type": "string"},
                    "is_cleared": {"type": "boolean"},
                    "cleared_at": {"type": ["string", "null"]},
                    "bank_reference": {"type": ["string", "null"]},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="payment_system",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, prn: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.view_status(prn=prn)


class PaymentReactivatePrnTool(Tool):
    """Tool for renewing validity on an expired PRN."""

    def __init__(self, client: PaymentClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="payment_reactivate_prn",
            description=(
                "Change expiry data for a sample reference in the local payment simulator. This does not reactivate "
                "a real URA PRN."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prn": {
                        "type": "string",
                        "description": "Expired 10 or 12-digit PRN.",
                    }
                },
                "required": ["prn"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "prn": {"type": "string"},
                    "status": {"type": "string"},
                    "new_expiry_date": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            requires_confirmation=True,
            namespace="payment_system",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

    def execute(self, prn: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.reactivate_prn(prn=prn)


class PaymentCheckoutSettleTool(Tool):
    """Tool for executing electronic card (VISA/MasterCard) or Mobile Money payment checkout."""

    def __init__(self, client: PaymentClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="payment_checkout_settle",
            description=(
                "Simulate a payment outcome in the local database. No card, bank, or mobile-money provider is contacted, "
                "no money moves, and no official receipt is issued."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prn": {
                        "type": "string",
                        "description": "12-digit PRN to settle.",
                    },
                    "payment_method": {
                        "type": "string",
                        "enum": ["VISA", "MASTERCARD", "MTN_MOMO", "AIRTEL_MONEY"],
                        "description": "Payment instrument.",
                    },
                    "payer_identifier": {
                        "type": "string",
                        "description": "Card ending (last 4 digits) or mobile telephone number (e.g. +256772111222).",
                    },
                    "amount_paid_ugx": {
                        "type": "number",
                        "description": "Amount to pay in UGX.",
                    },
                },
                "required": ["prn", "payment_method", "amount_paid_ugx"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "prn": {"type": "string"},
                    "transaction_id": {"type": "string"},
                    "receipt_number": {"type": "string"},
                    "status": {"type": "string"},
                    "cleared_at": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="high",
            requires_confirmation=True,
            namespace="payment_system",
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
        prn: str,
        payment_method: str = "VISA",
        payer_identifier: str = "1234",
        amount_paid_ugx: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.process_checkout(
            prn=prn,
            payment_method=payment_method,
            payer_identifier=payer_identifier,
            amount_paid_ugx=amount_paid_ugx,
        )


class PaymentVerifyAdvanceTaxTool(Tool):
    """Tool for verifying commercial passenger and goods vehicle advance income tax payments."""

    def __init__(self, client: PaymentClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="payment_verify_advance_tax",
            description=(
                "Verify advance income tax compliance on commercial passenger service vehicles (PSVs) "
                "(UGX 20,000 per seat) and goods cargo carriers (UGX 50,000 per tonne) before route license renewal."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "vehicle_registration_number": {
                        "type": "string",
                        "description": "Commercial vehicle plate number (e.g., UBK 412A).",
                    }
                },
                "required": ["vehicle_registration_number"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "vehicle_registration_number": {"type": "string"},
                    "is_compliant": {"type": "boolean"},
                    "prn": {"type": "string"},
                    "valid_until": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="payment_system",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, vehicle_registration_number: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.verify_advance_tax(vehicle_registration_number=vehicle_registration_number)


class PaymentConnector(SystemConnector):
    """System connector for URA Payment System."""

    def __init__(self, service: PaymentService | None = None) -> None:
        self._service = service or PaymentService()
        self._client = PaymentClient(service=self._service)
        self._tools: list[Tool] = [
            PaymentGeneratePrnTool(self._client),
            PaymentViewStatusTool(self._client),
            PaymentReactivatePrnTool(self._client),
            PaymentCheckoutSettleTool(self._client),
            PaymentVerifyAdvanceTaxTool(self._client),
        ]
        self._healthy = False

    @property
    def system_name(self) -> str:
        return "payment_system"

    @property
    def client(self) -> PaymentClient:
        return self._client

    @property
    def service(self) -> PaymentService:
        return self._service

    def initialize(self) -> bool:
        self._healthy = self._client.ping()
        logger.info("PaymentConnector initialized (healthy=%s)", self._healthy)
        return self._healthy

    def is_healthy(self) -> bool:
        return self._healthy and self._client.ping()

    def get_tools(self) -> list[Tool]:
        return list(self._tools)

    def get_status(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "display_name": "URA e-Services > Make a Payment Suite",
            "healthy": self.is_healthy(),
            "live_mode": self._client.is_live,
            "tool_count": len(self._tools),
            "tools": [t.schema.name for t in self._tools],
            "database": self._service.get_stats(),
        }

    def shutdown(self) -> None:
        self._healthy = False
        logger.info("PaymentConnector shut down")
