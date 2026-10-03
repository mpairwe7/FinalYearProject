"""Agentic connector bridging URA TIN Registration System into the URA agentic tool framework."""

from __future__ import annotations

import logging
from typing import Any

from plugins.base import SystemConnector, Tool, ToolSchema

from .client import TinRegistrationClient
from .service import TinRegistrationService

logger = logging.getLogger(__name__)


class TinSearchVerifyTool(Tool):
    """Tool for searching and verifying Tax Identification Numbers (TINs)."""

    def __init__(self, client: TinRegistrationClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="tin_search_verify",
            description=(
                "Search sample taxpayer records in the local TIN simulator. This does not check URA, NIRA, or URSB records."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "10-digit TIN, 14-character NIN, URSB registration number, or business name.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "found": {"type": "boolean"},
                    "taxpayer": {"type": "object"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="tin_registration",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.search_taxpayer(query=query)


class TinApplyIndividualTool(Tool):
    """Tool for issuing an Instant Individual TIN for citizens with National ID (NIN)."""

    def __init__(self, client: TinRegistrationClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="tin_apply_individual",
            description=(
                "Create a sample registration record in the local TIN simulator. This does not validate a NIN with NIRA "
                "or issue a real TIN."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "nin": {
                        "type": "string",
                        "description": "14-character National Identification Number (NIN) from NIRA.",
                    },
                    "full_name": {
                        "type": "string",
                        "description": "Full legal name as appearing on the National ID card.",
                    },
                    "date_of_birth": {
                        "type": "string",
                        "description": "Date of birth in YYYY-MM-DD format.",
                    },
                    "mobile": {
                        "type": "string",
                        "description": "Active Ugandan mobile telephone number.",
                    },
                    "email": {
                        "type": "string",
                        "description": "Email address for receiving the TIN certificate.",
                    },
                    "district": {
                        "type": "string",
                        "description": "District of physical residence.",
                    },
                },
                "required": ["nin", "full_name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "tin": {"type": "string"},
                    "legal_name": {"type": "string"},
                    "status": {"type": "string"},
                    "certificate_reference": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            requires_confirmation=True,
            namespace="tin_registration",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        nin: str,
        full_name: str,
        date_of_birth: str = "1990-01-01",
        mobile: str = "+256700000000",
        email: str = "taxpayer@gmail.com",
        district: str = "Kampala",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.apply_instant_individual_tin(
            nin=nin,
            full_name=full_name,
            date_of_birth=date_of_birth,
            mobile=mobile,
            email=email,
            district=district,
        )


class TinApplyNonIndividualTool(Tool):
    """Tool for registering a Non-Individual company TIN linked to URSB."""

    def __init__(self, client: TinRegistrationClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="tin_apply_non_individual",
            description=(
                "Apply for a Non-Individual corporate Taxpayer Identification Number (TIN). "
                "Cross-validates company incorporation details with the URSB business registry."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "ursb_registration_number": {
                        "type": "string",
                        "description": "Official company registration number or BRN issued by URSB.",
                    },
                    "business_name": {
                        "type": "string",
                        "description": "Registered company or legal entity name.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["NON_INDIVIDUAL_COMPANY", "PARTNERSHIP", "TRUST_NGO"],
                        "description": "Category of legal entity.",
                    },
                    "district": {
                        "type": "string",
                        "description": "District of registered commercial office.",
                    },
                    "mobile": {
                        "type": "string",
                        "description": "Official corporate contact telephone number.",
                    },
                    "email": {
                        "type": "string",
                        "description": "Official corporate contact email address.",
                    },
                },
                "required": ["ursb_registration_number", "business_name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "tin": {"type": "string"},
                    "business_name": {"type": "string"},
                    "status": {"type": "string"},
                    "active_tax_heads": {"type": "array", "items": {"type": "string"}},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            requires_confirmation=True,
            namespace="tin_registration",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        ursb_registration_number: str,
        business_name: str,
        category: str = "NON_INDIVIDUAL_COMPANY",
        district: str = "Kampala",
        mobile: str = "+256700000000",
        email: str = "info@company.co.ug",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.apply_non_individual_tin(
            ursb_registration_number=ursb_registration_number,
            business_name=business_name,
            category=category,
            district=district,
            mobile=mobile,
            email=email,
        )


class TinTaxObligationsTool(Tool):
    """Tool for registering or updating tax heads for an existing TIN."""

    def __init__(self, client: TinRegistrationClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="tin_tax_obligations",
            description=(
                "Register additional tax head obligations for an existing TIN (e.g. VAT_STANDARD when turnover "
                "exceeds UGX 150M threshold, PAYE for employees, or LOCAL_EXCISE)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tin": {
                        "type": "string",
                        "description": "10-digit Tax Identification Number.",
                    },
                    "tax_head": {
                        "type": "string",
                        "enum": [
                            "VAT_STANDARD",
                            "PAYE",
                            "CORPORATION_TAX",
                            "WITHHOLDING_TAX",
                            "LOCAL_EXCISE",
                            "RENTAL_TAX",
                        ],
                        "description": "Statutory tax obligation head.",
                    },
                },
                "required": ["tin", "tax_head"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "tin": {"type": "string"},
                    "legal_name": {"type": "string"},
                    "active_obligations": {"type": "array", "items": {"type": "string"}},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            requires_confirmation=True,
            namespace="tin_registration",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

    def execute(self, tin: str, tax_head: str, **kwargs: Any) -> dict[str, Any]:
        return self._client.add_tax_obligation(tin=tin, tax_head=tax_head)


class TinRegistrationConnector(SystemConnector):
    """System connector for URA TIN Registration platform."""

    def __init__(self, service: TinRegistrationService | None = None) -> None:
        self._service = service or TinRegistrationService()
        self._client = TinRegistrationClient(service=self._service)
        self._tools: list[Tool] = [
            TinSearchVerifyTool(self._client),
            TinApplyIndividualTool(self._client),
            TinApplyNonIndividualTool(self._client),
            TinTaxObligationsTool(self._client),
        ]
        self._healthy = False

    @property
    def system_name(self) -> str:
        return "tin_registration"

    @property
    def client(self) -> TinRegistrationClient:
        return self._client

    @property
    def service(self) -> TinRegistrationService:
        return self._service

    def initialize(self) -> bool:
        self._healthy = self._client.ping()
        logger.info("TinRegistrationConnector initialized (healthy=%s)", self._healthy)
        return self._healthy

    def is_healthy(self) -> bool:
        return self._healthy and self._client.ping()

    def get_tools(self) -> list[Tool]:
        return list(self._tools)

    def get_status(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "display_name": "URA Taxpayer Registration & Instant TIN System",
            "healthy": self.is_healthy(),
            "live_mode": self._client.is_live,
            "tool_count": len(self._tools),
            "tools": [t.schema.name for t in self._tools],
            "database": self._service.get_stats(),
        }

    def shutdown(self) -> None:
        self._healthy = False
        logger.info("TinRegistrationConnector shut down")
