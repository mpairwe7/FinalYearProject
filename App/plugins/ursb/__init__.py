"""Uganda Registration Services Bureau (URSB) Plugin and Connector."""

from __future__ import annotations

from plugins.base import Plugin, PluginMetadata

from .client import UrsbClient
from .connector import (
    UrsbComplianceStatusTool,
    UrsbConnector,
    UrsbRegisterBusinessTool,
    UrsbVerifyBusinessTool,
)
from .database import UrsbDatabase
from .models import (
    BusinessEntity,
    BusinessRegistrationRequest,
    BusinessRegistrationResponse,
    BusinessSearchRequest,
    BusinessSearchResponse,
    ComplianceCheckResponse,
    DirectorInfo,
    EntityStatus,
    EntityType,
)
from .service import UrsbService


class UrsbPlugin(Plugin):
    """Plugin encapsulating the URSB business registry system and connector."""

    def __init__(self, service: UrsbService | None = None) -> None:
        metadata = PluginMetadata(
            name="ursb",
            display_name="Uganda Registration Services Bureau (URSB)",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to URSB for verifying registered companies, "
                "business names, Form 20 directors, annual return compliance, and non-individual TIN prerequisites."
            ),
            author="URSB & URA Inter-Agency Automation",
            system_type="business_registry",
            tags=("ursb", "business_registration", "incorporation", "tin_prerequisite", "form_20"),
            documentation_url="https://ursb.go.ug",
        )
        connector = UrsbConnector(service=service)
        super().__init__(metadata=metadata, connector=connector)


__all__ = [
    "UrsbPlugin",
    "UrsbConnector",
    "UrsbService",
    "UrsbDatabase",
    "UrsbClient",
    "UrsbVerifyBusinessTool",
    "UrsbRegisterBusinessTool",
    "UrsbComplianceStatusTool",
    "BusinessEntity",
    "DirectorInfo",
    "BusinessSearchRequest",
    "BusinessSearchResponse",
    "BusinessRegistrationRequest",
    "BusinessRegistrationResponse",
    "ComplianceCheckResponse",
    "EntityType",
    "EntityStatus",
]
