"""The monitoring config agrees with the code it monitors.

The alerting rules used to reference a label the latency histogram did not
carry, a job name Prometheus did not scrape and a rules file it never loaded
— every one of them silent, because a query that matches nothing is not an
error. This test builds the catalogue of metrics the API really exports (by
reading the code that records them) and fails when a rule or dashboard panel
names anything outside it, when an alert lacks its severity or runbook, when
a runbook link points at a heading that does not exist, or when the rules
drift from the SLOs in monitoring/slo/.

promtool unit tests for the rule logic live in monitoring/tests/ (CI job
"Monitoring config").
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
APP = ROOT / "App" / "backend" / "app"
REPO_URL = "https://github.com/mpairwe7/FinalYearProject/blob/dev/"

_CALL = re.compile(r"metrics\.(inc|observe|set_gauge|add_gauge)\(\s*\"([a-zA-Z_:][a-zA-Z0-9_:]*)\"")
_MAPPED = re.compile(r"\(\"(counter|gauge|histogram)\",\s*\"([a-zA-Z_][a-zA-Z0-9_]*)\"")
_LIFECYCLE = re.compile(r"\"(ura_qdrant_[a-z_]+)\"")
_URA_SERIES = re.compile(r"\bura_[a-z0-9_]+")
_RECORDING = re.compile(r"\bura:[a-zA-Z0-9_:]+")
_KIND = {"inc": "counter", "observe": "histogram", "set_gauge": "gauge", "add_gauge": "gauge"}


def _exported(name: str) -> str:
    return name if name.startswith("ura_") else f"ura_{name}"


def metric_catalogue() -> set[str]:
    """Every series name the API can export, with histogram/counter suffixes."""
    names: set[str] = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found = [(_KIND[m.group(1)], m.group(2)) for m in _CALL.finditer(text)]
        found += [(m.group(1), m.group(2)) for m in _MAPPED.finditer(text)]
        for kind, raw in found:
            base = _exported(raw)
            if kind == "counter":
                names.add(base if base.endswith("_total") else f"{base}_total")
            elif kind == "histogram":
                names.update({f"{base}_bucket", f"{base}_count", f"{base}_sum"})
            else:
                names.add(base)
        names.update(_LIFECYCLE.findall(text))
    return names


def _rules(name: str) -> list[dict]:
    doc = yaml.safe_load((MONITORING / name).read_text())
    return [rule for group in doc["groups"] for rule in group["rules"]]


def _slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(markdown: Path) -> set[str]:
    return {_slug(line.lstrip("#")) for line in markdown.read_text().splitlines() if line.startswith("#")}


CATALOGUE = metric_catalogue()
RECORDINGS = {rule["record"] for rule in _rules("recording-rules.yml")}
ALERTS = _rules("alerting-rules.yml")


def test_catalogue_is_not_trivially_empty():
    for name in ("ura_http_requests_total", "ura_chat_turns_total", "ura_chat_response_time_ms_bucket",
                 "ura_llm_tokens_total", "ura_audit_append_failed_total", "ura_security_events_total",
                 "ura_web_vitals_ms_bucket", "ura_eval_metric_passed", "ura_qdrant_index_drift"):
        assert name in CATALOGUE, name


@pytest.mark.parametrize("rule", _rules("recording-rules.yml") + ALERTS, ids=lambda r: r.get("record") or r["alert"])
def test_rules_only_name_series_the_api_exports(rule):
    unknown = sorted(set(_URA_SERIES.findall(rule["expr"])) - CATALOGUE)
    assert not unknown, f"{rule.get('record') or rule['alert']} names series the API never exports: {unknown}"
    missing = sorted(set(_RECORDING.findall(rule["expr"])) - RECORDINGS)
    assert not missing, f"undefined recording rules: {missing}"


@pytest.mark.parametrize("alert", ALERTS, ids=lambda a: a["alert"])
def test_alerts_are_routable_and_actionable(alert):
    labels, notes = alert.get("labels", {}), alert.get("annotations", {})
    assert labels.get("severity") in {"critical", "warning"}
    assert labels.get("team")
    for key in ("summary", "description", "runbook_url"):
        assert notes.get(key), f"{alert['alert']} lacks {key}"
    url = notes["runbook_url"]
    assert url.startswith(REPO_URL), url
    path, _, anchor = url[len(REPO_URL):].partition("#")
    doc = ROOT / path
    assert doc.is_file(), f"{alert['alert']}: runbook {path} does not exist"
    if anchor:
        assert anchor in _anchors(doc), f"{alert['alert']}: no heading #{anchor} in {path}"


def test_latency_bucket_used_by_the_slo_exists():
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT / "App" / "backend"))
    from app.analytics import _MS_BUCKETS

    assert 3000 in _MS_BUCKETS
    assert 'le="3000.0"' in (MONITORING / "recording-rules.yml").read_text()
    assert importlib.util.find_spec("prometheus_client") is not None


def test_slo_objectives_match_the_burn_rate_rules():
    docs = [d for d in yaml.safe_load_all((MONITORING / "slo" / "ura-chatbot.openslo.yaml").read_text()) if d]
    targets = {d["metadata"]["name"]: d["spec"]["objectives"][0]["target"] for d in docs if d["kind"] == "SLO"}
    assert targets == {"api-availability": 0.999, "chat-latency": 0.95}
    by_name = {a["alert"]: a["expr"] for a in ALERTS}
    for alert, budget in (("UraAvailabilityBudgetBurnFast", 0.001), ("UraAvailabilityBudgetBurnSlow", 0.001),
                          ("UraChatLatencyBudgetBurnFast", 0.05)):
        assert f"{budget}" in by_name[alert], f"{alert} does not use the {budget} error budget"
    assert round(1 - targets["api-availability"], 6) == 0.001
    assert round(1 - targets["chat-latency"], 6) == 0.05


def test_prometheus_loads_the_rules_scrapes_with_a_token_and_alerts_somewhere():
    config = yaml.safe_load((MONITORING / "prometheus.yml").read_text())
    for rule_file in config["rule_files"]:
        assert (MONITORING / rule_file).is_file(), rule_file
    assert config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    api = next(job for job in config["scrape_configs"] if job["job_name"] == "ura-api")
    assert api["authorization"]["credentials_file"].startswith("/run/secrets/")
    jobs = {job["job_name"] for job in config["scrape_configs"]}
    for alert in ALERTS:
        for job in re.findall(r'job="([^"]+)"', alert["expr"]):
            assert job in jobs, f"{alert['alert']} watches job {job!r}, which is not scraped"


def test_alertmanager_delivers_to_a_receiver():
    config = yaml.safe_load((MONITORING / "alertmanager.yml").read_text())
    receivers = {r["name"]: r for r in config["receivers"]}
    assert config["route"]["receiver"] in receivers
    assert receivers["ops-webhook"]["webhook_configs"][0]["url_file"].startswith("/run/secrets/")


def test_dashboard_queries_name_real_series():
    dashboard = json.loads((MONITORING / "grafana" / "dashboards" / "ura-chatbot-overview.json").read_text())
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            expr = target.get("expr", "")
            if panel.get("datasource", {}).get("type") == "loki":
                continue
            unknown = sorted(set(_URA_SERIES.findall(expr)) - CATALOGUE)
            assert not unknown, f"panel {panel['title']!r} queries unknown series {unknown}"
            missing = sorted(set(_RECORDING.findall(expr)) - RECORDINGS)
            assert not missing, f"panel {panel['title']!r} queries undefined recordings {missing}"


def test_compose_monitoring_files_exist():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for name, service in compose["services"].items():
        if "monitoring" not in service.get("profiles", []):
            continue
        for volume in service.get("volumes", []):
            source = volume.split(":", 1)[0]
            if source.startswith("./"):
                assert (ROOT / source).exists(), f"{name} mounts missing {source}"
    assert set(compose["secrets"]) >= {"ura_metrics_token", "alertmanager_webhook_url"}
    grafana_env = " ".join(compose["services"]["grafana"]["environment"])
    assert "GRAFANA_PASSWORD:-}" in grafana_env, "Grafana must not ship a default admin password"
