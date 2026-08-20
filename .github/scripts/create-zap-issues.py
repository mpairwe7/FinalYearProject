#!/usr/bin/env python3
"""ZAP & Bandit Security Issue Creator — URA AI Chatbot.

Parses ZAP DAST and Bandit SAST JSON results and creates GitHub issues for
HIGH and CRITICAL security findings with automated deduplication and remediation.

Standards: OWASP WSTG v4.2, OWASP Top 10, CWE/CAPEC mapping
"""

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from github import Github, GithubException

try:
    from github import Github, GithubException
except ImportError:
    print("PyGithub not installed. Run: pip install PyGithub")
    sys.exit(0)

RISK_LEVELS = {
    "3": {"name": "Critical", "emoji": "🔴", "label": "security-critical"},
    "2": {"name": "High", "emoji": "🟠", "label": "security-high"},
    "1": {"name": "Medium", "emoji": "🟡", "label": "security-medium"},
    "0": {"name": "Low", "emoji": "🟢", "label": "security-low"},
}

CREATE_ISSUE_THRESHOLD = ["3", "2"]

CWE_REMEDIATION = {
    "79": {
        "title": "Cross-Site Scripting (XSS)",
        "remediation": """
**Remediation Steps:**
1. Implement output encoding / sanitization using DOMPurify or bleach
2. Enforce strict Content-Security-Policy (CSP) headers
3. Enable HTTPOnly flag on session cookies
4. Use React/Next.js built-in XSS protection (avoid dangerouslySetInnerHTML)
5. Sanitize all user input server-side with InputGuard

**Code Example (Python/FastAPI):**
```python
from markupsafe import escape
sanitized_input = escape(user_input)
```
""",
    },
    "89": {
        "title": "SQL Injection",
        "remediation": """
**Remediation Steps:**
1. Use parameterized queries / prepared statements (SQLModel / SQLAlchemy)
2. Never concatenate raw user input into SQL query strings
3. Use ORM filter expressions with strict type validation
4. Apply the principle of least privilege to database users

**Code Example (Python/SQLModel):**
```python
# CORRECT
statement = select(Conversation).where(Conversation.session_id == session_id)
results = session.exec(statement).all()
```
""",
    },
    "78": {
        "title": "OS Command Injection",
        "remediation": """
**Remediation Steps:**
1. Avoid system shell calls with user-controlled input
2. If subprocess is required, pass argument lists with `shell=False`
3. Implement strict allowlist validation for arguments

**Code Example (Python):**
```python
# CORRECT
import subprocess
subprocess.run(["tesseract", input_path, output_base], shell=False, check=True)
```
""",
    },
    "918": {
        "title": "Server-Side Request Forgery (SSRF)",
        "remediation": """
**Remediation Steps:**
1. Validate and sanitize URLs before issuing HTTP requests
2. Restrict outgoing requests to allowlisted domains (e.g. *.ura.go.ug)
3. Block requests to internal RFC 1918 and cloud metadata IP ranges (169.254.169.254)
4. Disable HTTP redirect following or re-validate redirect targets
""",
    },
    "352": {
        "title": "Cross-Site Request Forgery (CSRF)",
        "remediation": """
**Remediation Steps:**
1. Implement anti-CSRF tokens for state-changing endpoints
2. Set SameSite=Lax or SameSite=Strict on session cookies
3. Validate Origin and Referer headers on incoming requests
""",
    },
    "200": {
        "title": "Information Disclosure",
        "remediation": """
**Remediation Steps:**
1. Ensure debug mode is disabled in production (`DEBUG=false`)
2. Mask stack traces and internal errors in user-facing responses
3. Redact sensitive taxpayer PII using OutputGuard
""",
    },
}


def get_remediation(cwe_id: str) -> str:
    if cwe_id in CWE_REMEDIATION:
        return CWE_REMEDIATION[cwe_id]["remediation"]
    return """
**General Remediation Steps:**
1. Review the vulnerability description and associated CWE entry
2. Consult OWASP Top 10 guidelines for this vulnerability category
3. Implement input validation, least privilege, and defensive coding practices
"""


def create_issue_title(alert: dict) -> str:
    name = alert.get("name", "Security Finding")
    risk = RISK_LEVELS.get(str(alert.get("riskcode", "0")), {}).get("name", "Unknown")
    cwe = alert.get("cweid", "")
    title = f"[{risk}] {name}"
    if cwe and cwe != "-1":
        title += f" (CWE-{cwe})"
    return title[:100]


def create_issue_body(alert: dict, scan_date: str) -> str:
    risk_info = RISK_LEVELS.get(str(alert.get("riskcode", "0")), {})
    cwe_id = str(alert.get("cweid", ""))
    instances = alert.get("instances", [])
    affected_urls = "\n".join([f"- `{i.get('uri', 'N/A')}`" for i in instances[:5]])
    if len(instances) > 5:
        affected_urls += f"\n- ... and {len(instances) - 5} more"

    return f"""## {risk_info.get('emoji', '🔍')} Security Vulnerability Finding

### Summary
- **Vulnerability:** {alert.get('name', 'Unknown')}
- **Risk Level:** {risk_info.get('name', 'Unknown')}
- **Confidence:** {alert.get('confidence', 'Unknown')}
- **CWE ID:** {cwe_id if cwe_id != '-1' else 'Not Mapped'}
- **WASC ID:** {alert.get('wascid', 'N/A')}

### Description
{alert.get('desc', 'No description available.').strip()}

### Affected URLs
{affected_urls or '- N/A'}

### Evidence
```
{instances[0].get('evidence', 'No evidence captured')[:500] if instances else 'N/A'}
```

### Impact
{alert.get('otherinfo', 'This finding could compromise application integrity or data confidentiality.').strip()}

{get_remediation(cwe_id)}

### References
{alert.get('reference', 'https://owasp.org/').strip()}

### Detection Metadata
- **Scanner:** OWASP ZAP (DAST)
- **Scan Date:** {scan_date}
- **Alert ID:** {alert.get('alertRef', 'N/A')}
- **Plugin ID:** {alert.get('pluginid', 'N/A')}

---
*Auto-generated by DevSecOps CI Pipeline.*
"""


def ensure_labels_exist(repo, labels: list[str]):
    label_configs = {
        "security": {"color": "d93f0b", "description": "Security vulnerability"},
        "security-critical": {"color": "b60205", "description": "Critical security issue"},
        "security-high": {"color": "d93f0b", "description": "High severity security issue"},
        "security-medium": {"color": "fbca04", "description": "Medium severity security issue"},
        "security-low": {"color": "0e8a16", "description": "Low severity security issue"},
        "automated": {"color": "1d76db", "description": "Automatically created by CI"},
        "zap-scan": {"color": "5319e7", "description": "From OWASP ZAP scan"},
    }
    existing = {l.name for l in repo.get_labels()}
    for label in labels:
        if label not in existing and label in label_configs:
            try:
                repo.create_label(
                    name=label,
                    color=label_configs[label]["color"],
                    description=label_configs[label]["description"]
                )
            except Exception:
                pass


def main():
    github_token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("REPO_NAME", os.environ.get("GITHUB_REPOSITORY"))

    if not github_token or not repo_name:
        print("GITHUB_TOKEN and REPO_NAME not provided — skipping remote issue creation.")
        return

    results_dir = Path("scan-results")
    zap_results_file = None
    for pattern in ["zap-results.json", "zap*.json", "report_json.json"]:
        matches = list(results_dir.glob(pattern)) or list(Path(".").glob(pattern))
        if matches:
            zap_results_file = matches[0]
            break

    if not zap_results_file or not zap_results_file.exists():
        print("No ZAP results file found. Skipping.")
        return

    with open(zap_results_file) as f:
        data = json.load(f)

    alerts = []
    scan_date = data.get("@generated", "Unknown")
    for site in data.get("site", []):
        for alert in site.get("alerts", []):
            risk_code = str(alert.get("riskcode", "0"))
            if risk_code in CREATE_ISSUE_THRESHOLD:
                alerts.append(alert)

    if not alerts:
        print("No HIGH/CRITICAL vulnerabilities found by ZAP. Zero issues created.")
        return

    print(f"Found {len(alerts)} HIGH/CRITICAL ZAP alerts.")

    g = Github(github_token)
    repo = g.get_repo(repo_name)

    required_labels = ["security", "automated", "zap-scan", "security-critical", "security-high"]
    ensure_labels_exist(repo, required_labels)

    existing_issues = {issue.title for issue in repo.get_issues(state="open", labels=["security", "automated"])}

    created = 0
    for alert in alerts:
        title = create_issue_title(alert)
        if title in existing_issues:
            print(f"Skipping duplicate: {title}")
            continue

        risk_info = RISK_LEVELS.get(str(alert.get("riskcode", "0")), {})
        labels = ["security", "automated", "zap-scan"]
        if risk_info.get("label"):
            labels.append(risk_info["label"])

        try:
            body = create_issue_body(alert, scan_date)
            issue = repo.create_issue(title=title, body=body, labels=labels)
            print(f"Created issue #{issue.number}: {title}")
            existing_issues.add(title)
            created += 1
        except Exception as e:
            print(f"Failed to create issue '{title}': {e}")

    print(f"Summary: {created} issues created out of {len(alerts)} alerts.")


if __name__ == "__main__":
    main()
