"""URA Digital Tax Stamps (DTS / Kakasa platform) Plugin and Connector."""

from __future__ import annotations

from plugins.base import Plugin, PluginMetadata

from .client import DigitalTaxStampsClient
from .connector import (
    DigitalTaxStampsConnector,
    DtsActivateStampsTool,
    DtsOrderStampsTool,
    DtsTaxpayerStatusTool,
    DtsVerifyStampTool,
)
from .database import DtsDatabase
from .models import (
    DamagedStampDeclarationRequest,
    DamagedStampDeclarationResponse,
    GazettedCategory,
    PackagingType,
    StampActivationRequest,
    StampActivationResponse,
    StampOrderRequest,
    StampOrderResponse,
    StampRecord,
    StampStatus,
    StampVerificationRequest,
    StampVerificationResponse,
    TaxpayerDtsProfile,
)
from .service import DigitalTaxStampsService


class DigitalTaxStampsPlugin(Plugin):
    """Plugin encapsulating the Digital Tax Stamps system and connector."""

    def __init__(self, service: DigitalTaxStampsService | None = None) -> None:
        metadata = PluginMetadata(
            name="digital_tax_stamps",
            display_name="Digital Tax Stamps (DTS) / Kakasa Track & Trace",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to the DTS track-and-trace solution for gazetted excisable goods. "
                "Enables Kakasa stamp authentication, stamp requisition & PRN generation, production line activation, "
                "damaged stamp declaration, and manufacturer compliance inspection."
            ),
            author="URA Domestic Taxes & Customs Modernization",
            system_type="track_and_trace",
            tags=("dts", "digital_tax_stamps", "kakasa", "excise", "track_and_trace"),
            documentation_url="https://ura.go.ug/en/category/domestic-tax/digital-tax-stamps/",
        )
        connector = DigitalTaxStampsConnector(service=service)
        super().__init__(metadata=metadata, connector=connector)


__all__ = [
    "DigitalTaxStampsPlugin",
    "DigitalTaxStampsConnector",
    "DigitalTaxStampsService",
    "DtsDatabase",
    "DigitalTaxStampsClient",
    "DtsVerifyStampTool",
    "DtsOrderStampsTool",
    "DtsActivateStampsTool",
    "DtsTaxpayerStatusTool",
    "StampVerificationRequest",
    "StampVerificationResponse",
    "StampOrderRequest",
    "StampOrderResponse",
    "StampActivationRequest",
    "StampActivationResponse",
    "DamagedStampDeclarationRequest",
    "DamagedStampDeclarationResponse",
    "StampRecord",
    "TaxpayerDtsProfile",
    "GazettedCategory",
    "StampStatus",
    "PackagingType",
]
