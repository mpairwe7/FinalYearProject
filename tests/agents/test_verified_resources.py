"""Tests for the verified URA resources, downloadable forms, and domain whitelisting (2026)."""

from __future__ import annotations

import pytest

from app.verified_resources import (
    CURRENT_FISCAL_YEAR,
    get_verified_resources,
    is_authoritative_ura_url,
)


def test_authoritative_ura_domains_whitelist():
    # Valid government domains
    assert is_authoritative_ura_url("https://ura.go.ug/en/download/vat-act") is True
    assert is_authoritative_ura_url("https://portal.ura.go.ug/payment?tin=123") is True
    assert is_authoritative_ura_url("https://efris.ura.go.ug/verify") is True
    assert is_authoritative_ura_url("https://customs.ura.go.ug/declaration") is True
    assert is_authoritative_ura_url("https://singlewindow.go.ug/auction") is True
    assert is_authoritative_ura_url("https://touchpoint.ura.go.ug/ticket") is True
    assert is_authoritative_ura_url("https://sub.portal.ura.go.ug/test") is True

    # Untrusted / External domains must fail
    assert is_authoritative_ura_url("https://evil-ura.com/download") is False
    assert is_authoritative_ura_url("http://ura.go.ug.attacker.io/fake") is False
    assert is_authoritative_ura_url("https://google.com") is False
    assert is_authoritative_ura_url("javascript:alert(1)") is False
    assert is_authoritative_ura_url("") is False


def test_verified_resources_retrieval_and_freshness_metadata():
    resources = get_verified_resources("how do I file my monthly VAT return", tax_type="vat")
    assert len(resources) > 0

    first = resources[0]
    assert first["is_verified"] is True
    assert first["verification_badge"] == "Official URA Verified"
    assert first["effective_year"] == CURRENT_FISCAL_YEAR
    assert "last_verified_at" in first
    assert is_authoritative_ura_url(first["url"]) is True

    # Expect both downloadable template and online form
    types = [r["type"] for r in resources]
    assert "downloadable_form" in types or "online_form" in types


def test_contextual_query_param_prefilling():
    entities = {
        "tins": ["1009876543"],
        "dates": ["March 2026"],
        "amounts": ["UGX 2,500,000"],
    }
    resources = get_verified_resources(
        "file vat return",
        tax_type="vat",
        intent="file_return",
        entities=entities,
    )
    assert len(resources) > 0

    online_form = next((r for r in resources if r["type"] == "online_form"), None)
    assert online_form is not None
    assert online_form.get("prefilled_params", {}).get("tin") == "1009876543" or "tin=" in online_form["url"]


def test_statutory_sources_and_checklists():
    resources = get_verified_resources("notice of objection assessment dispute section 24", intent="disputes")
    assert len(resources) > 0

    objection_form = next((r for r in resources if "objection" in r["id"]), None)
    if objection_form:
        assert len(objection_form.get("checklist", [])) > 0
