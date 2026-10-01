"""Agentic connector bridging URSB sample system into the URA agentic tool framework."""

from __future__ import annotations

import logging
from typing import Any

from plugins.base import SystemConnector, Tool, ToolSchema

from .client import UrsbClient
from .service import UrsbService

logger = logging.getLogger(__name__)


class UrsbVerifyBusinessTool(Tool):
    """Tool for querying and verifying registered businesses and companies on URSB."""

    def __init__(self, client: UrsbClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ursb_verify_business",
            description=(
                "Search and verify legal business entity details with the Uganda Registration Services Bureau (URSB). "
                "Retrieves official registration number/BRN, incorporation date, directors list (Form 20), "
                "and active legal status required for URA TIN generation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Business Name, Registration Number, or Business Registration Number (BRN).",
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
                    "entity": {"type": "object"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="ursb",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.search_business(query=query)


class UrsbRegisterBusinessTool(Tool):
    """Tool for reserving or registering business names and corporate entities with URSB."""

    def __init__(self, client: UrsbClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ursb_register_business",
            description=(
                "Register a formal business name or incorporate a limited company with URSB. "
                "Generates an official Registration Number to enable subsequent URA tax registration."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "business_name": {
                        "type": "string",
                        "description": "Proposed business or company name.",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["LIMITED_COMPANY", "BUSINESS_NAME", "PARTNERSHIP"],
                        "description": "Type of legal entity.",
                    },
                    "nature_of_business": {
                        "type": "string",
                        "description": "Brief description of principal commercial activities.",
                    },
                    "district": {
                        "type": "string",
                        "description": "Physical operational district (e.g., Kampala, Wakiso, Mukono).",
                    },
                    "applicant_nin": {
                        "type": "string",
                        "description": "National Identification Number (NIN) of primary applicant/director.",
                    },
                },
                "required": ["business_name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "registration_number": {"type": "string"},
                    "business_name": {"type": "string"},
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            namespace="ursb",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        business_name: str,
        entity_type: str = "LIMITED_COMPANY",
        nature_of_business: str = "General Commercial Services",
        district: str = "Kampala",
        applicant_nin: str = "CM000000000000",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.register_business(
            business_name=business_name,
            entity_type=entity_type,
            nature_of_business=nature_of_business,
            district=district,
            applicant_nin=applicant_nin,
        )


class UrsbComplianceStatusTool(Tool):
    """Tool for checking company annual return filings and compliance with URSB."""

    def __init__(self, client: UrsbClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ursb_compliance_status",
            description=(
                "Inspect company legal compliance with URSB: checks active legal standing, "
                "annual return filing up-to-date status, and Form 20 director records for URA TIN readiness."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "registration_number": {
                        "type": "string",
                        "description": "Official URSB registration number (e.g. URSB-CO-10001).",
                    }
                },
                "required": ["registration_number"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "registration_number": {"type": "string"},
                    "business_name": {"type": "string"},
                    "ready_for_ura_tin": {"type": "boolean"},
                    "status": {"type": "string"},
                    "details": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="ursb",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, registration_number: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.check_compliance(registration_number=registration_number)


class UrsbConnector(SystemConnector):
    """System connector for URSB."""

    def __init__(self, service: UrsbService | None = None) -> None:
        self._service = service or UrsbService()
        self._client = UrsbClient(service=self._service)
        self._tools: list[Tool] = [
            UrsbVerifyBusinessTool(self._client),
            UrsbRegisterBusinessTool(self._client),
            UrsbComplianceStatusTool(self._client),
        ]
        self._healthy = False

    @property
    def system_name(self) -> str:
        return "ursb"

    @property
    def client(self) -> UrsbClient:
        return self._client

    @property
    def service(self) -> UrsbService:
        return self._service

    def initialize(self) -> bool:
        self._healthy = self._client.ping()
        logger.info("UrsbConnector initialized (healthy=%s)", self._healthy)
        return self._healthy

    def is_healthy(self) -> bool:
        return self._healthy and self._client.ping()

    def get_tools(self) -> list[Tool]:
        return list(self._tools)

    def get_status(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "display_name": "Uganda Registration Services Bureau (URSB)",
            "healthy": self.is_healthy(),
            "live_mode": self._client.is_live,
            "tool_count": len(self._tools),
            "tools": [t.schema.name for t in self._tools],
            "database": self._service.get_stats(),
        }

    def shutdown(self) -> None:
        self._healthy = False
        logger.info("UrsbConnector shut down")
