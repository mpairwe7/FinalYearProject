"""URA Payment System (e-Services > Make a Payment suite) Plugin and Connector."""

from __future__ import annotations

from plugins.base import Plugin, PluginMetadata

from .client import PaymentClient
from .connector import (
    PaymentCheckoutSettleTool,
    PaymentConnector,
    PaymentGeneratePrnTool,
    PaymentReactivatePrnTool,
    PaymentVerifyAdvanceTaxTool,
    PaymentViewStatusTool,
)
from .database import PaymentDatabase
from .models import (
    AdvanceTaxVerifyRequest,
    AdvanceTaxVerifyResponse,
    GeneratePrnRequest,
    GeneratePrnResponse,
    PaymentCategory,
    PaymentChannel,
    PaymentCheckoutRequest,
    PaymentCheckoutResponse,
    PaymentPrnRecord,
    PaymentStatusRequest,
    PaymentStatusResponse,
    PrnStatus,
    ReactivatePrnRequest,
    ReactivatePrnResponse,
)
from .service import PaymentService


class PaymentPlugin(Plugin):
    """Plugin encapsulating the URA Payment suite and connector."""

    def __init__(self, service: PaymentService | None = None) -> None:
        metadata = PluginMetadata(
            name="payment_system",
            display_name="URA e-Services > Make a Payment Suite",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to the official e-Payment portal suite. "
                "Enables generating PRN payment slips (taxes and NTR/MDA fees), reactivating expired PRNs, "
                "real-time bank payment status checking, card/mobile-money checkout, and advance motor vehicle tax verification."
            ),
            author="URA Revenue Accounting & e-Payments",
            system_type="payment_gateway",
            tags=("payments", "prn", "bank_slip", "mobile_money", "visa", "advance_tax"),
            documentation_url="https://ura.go.ug/en/make-a-payment/",
        )
        connector = PaymentConnector(service=service)
        super().__init__(metadata=metadata, connector=connector)


__all__ = [
    "PaymentPlugin",
    "PaymentConnector",
    "PaymentService",
    "PaymentDatabase",
    "PaymentClient",
    "PaymentGeneratePrnTool",
    "PaymentViewStatusTool",
    "PaymentReactivatePrnTool",
    "PaymentCheckoutSettleTool",
    "PaymentVerifyAdvanceTaxTool",
    "PaymentPrnRecord",
    "GeneratePrnRequest",
    "GeneratePrnResponse",
    "ReactivatePrnRequest",
    "ReactivatePrnResponse",
    "PaymentStatusRequest",
    "PaymentStatusResponse",
    "PaymentCheckoutRequest",
    "PaymentCheckoutResponse",
    "AdvanceTaxVerifyRequest",
    "AdvanceTaxVerifyResponse",
    "PaymentCategory",
    "PaymentChannel",
    "PrnStatus",
]
