"""URA EFRIS (Electronic Fiscal Receipting and Invoicing System) Plugin and Connector."""

from __future__ import annotations

from plugins.base import Plugin, PluginMetadata

from .client import EfrisClient
from .connector import (
    EfrisConnector,
    EfrisCreditNoteTool,
    EfrisFiscalInvoiceTool,
    EfrisStockManagementTool,
    EfrisTaxpayerStatusTool,
)
from .database import EfrisDatabase
from .models import (
    CreditNoteReason,
    CreditNoteRequest,
    CreditNoteResponse,
    DocumentStatus,
    FiscalInvoiceRequest,
    FiscalInvoiceResponse,
    InvoiceItem,
    InvoiceType,
    InvoiceVerificationRequest,
    InvoiceVerificationResponse,
    StockItem,
    TaxpayerEfrisProfile,
    TaxRateCategory,
)
from .service import EfrisService


class EfrisPlugin(Plugin):
    """Plugin encapsulating the EFRIS system and connector."""

    def __init__(self, service: EfrisService | None = None) -> None:
        metadata = PluginMetadata(
            name="efris",
            display_name="Electronic Fiscal Receipting and Invoicing System",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to EFRIS for real-time fiscal invoice generation, "
                "FDN verification, stock inventory tracking, credit notes, and taxpayer terminal readiness."
            ),
            author="URA IT Innovation & Modernization",
            system_type="e-invoicing",
            tags=("efris", "vat", "invoicing", "fiscal", "tax_compliance"),
            documentation_url="https://efris.ura.go.ug",
        )
        connector = EfrisConnector(service=service)
        super().__init__(metadata=metadata, connector=connector)


__all__ = [
    "EfrisPlugin",
    "EfrisConnector",
    "EfrisService",
    "EfrisDatabase",
    "EfrisClient",
    "EfrisFiscalInvoiceTool",
    "EfrisTaxpayerStatusTool",
    "EfrisStockManagementTool",
    "EfrisCreditNoteTool",
    "FiscalInvoiceRequest",
    "FiscalInvoiceResponse",
    "InvoiceVerificationRequest",
    "InvoiceVerificationResponse",
    "CreditNoteRequest",
    "CreditNoteResponse",
    "InvoiceItem",
    "StockItem",
    "TaxpayerEfrisProfile",
    "InvoiceType",
    "DocumentStatus",
    "CreditNoteReason",
    "TaxRateCategory",
]
