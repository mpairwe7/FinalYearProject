"""URA Tax Identification Number (TIN) Registration Plugin and Connector."""

from __future__ import annotations

from plugins.base import Plugin, PluginMetadata

from .client import TinRegistrationClient
from .connector import (
    TinApplyIndividualTool,
    TinApplyNonIndividualTool,
    TinRegistrationConnector,
    TinSearchVerifyTool,
    TinTaxObligationsTool,
)
from .database import TinDatabase
from .models import (
    InstantTinRequest,
    InstantTinResponse,
    NonIndividualTinRequest,
    NonIndividualTinResponse,
    TaxHeadType,
    TaxObligation,
    TaxObligationUpdateRequest,
    TaxObligationUpdateResponse,
    TaxpayerCategory,
    TaxpayerRecord,
    TinSearchRequest,
    TinSearchResponse,
)
from .service import TinRegistrationService


class TinRegistrationPlugin(Plugin):
    """Plugin encapsulating the URA TIN Registration system and connector."""

    def __init__(self, service: TinRegistrationService | None = None) -> None:
        metadata = PluginMetadata(
            name="tin_registration",
            display_name="URA Taxpayer Registration & Instant TIN System",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to the Tax Identification Number (TIN) registration engine. "
                "Enables Instant Individual TIN issuance via citizen NIN, Non-Individual company TIN registration "
                "linked to URSB, tax heads enrollment (VAT, PAYE, CIT), and active taxpayer verification."
            ),
            author="URA Domestic Taxes & Registration Modernization",
            system_type="taxpayer_registration",
            tags=("tin", "instant_tin", "nin", "registration", "tax_obligations", "ursb_link"),
            documentation_url="https://ura.go.ug/en/domestic-taxes/get-a-tin/",
        )
        connector = TinRegistrationConnector(service=service)
        super().__init__(metadata=metadata, connector=connector)


__all__ = [
    "TinRegistrationPlugin",
    "TinRegistrationConnector",
    "TinRegistrationService",
    "TinRegistrationDatabase",
    "TinRegistrationClient",
    "TinSearchVerifyTool",
    "TinApplyIndividualTool",
    "TinApplyNonIndividualTool",
    "TinTaxObligationsTool",
    "TaxpayerRecord",
    "TaxObligation",
    "InstantTinRequest",
    "InstantTinResponse",
    "NonIndividualTinRequest",
    "NonIndividualTinResponse",
    "TinSearchRequest",
    "TinSearchResponse",
    "TaxObligationUpdateRequest",
    "TaxObligationUpdateResponse",
    "TaxpayerCategory",
    "TaxHeadType",
]
