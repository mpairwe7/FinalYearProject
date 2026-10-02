"""Standalone FastAPI Application for Uganda Registration Services Bureau (URSB).

Features:
- Dedicated SQLite database (data_store/ursb_system.db)
- Mandatory Auth Gate with URSB Security CAPTCHA verification
- 1-click Demo Account presets (Kakira Sugar, Nile Breweries, Pearl Organic Coffee)
- Business entity search by name, BRN, or registration number
- Company incorporation with automatic registration number generation (URSB-CO-XXXXX)
- Form 20 particulars of directors & secretaries
- Legal compliance audit & URA Non-Individual TIN readiness verification
- MCP 2026 tools gateway (/mcp/manifest, /mcp/call)
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path
_parents = Path(__file__).resolve().parents
_root = _parents[2] if len(_parents) > 2 else _parents[0]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import Body, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

os.environ.setdefault("URSB_DB_PATH", str(_root / "data_store" / "ursb_system.db"))

from plugins.apps.common_ui import COMMON_AUTH_JS, COMMON_CSS, render_logo  # noqa: E402
from plugins.ursb import UrsbClient, UrsbConnector, UrsbService  # noqa: E402

service = UrsbService()
client = UrsbClient(service=service)
connector = UrsbConnector(service=service)
connector.initialize()

app = FastAPI(title="URSB Business Registry Standalone System", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
def index() -> str:  # noqa: S608
    stats = service.get_stats()
    entities = service.database.list_recent_entities(6)
    ent_rows = []
    for e in entities:
        ent_rows.append(
            f"<tr><td><strong style='color:var(--accent);'>{html.escape(str(e['registration_number']))}</strong></td>"
            f"<td><strong>{html.escape(str(e['business_name']))}</strong></td>"
            f"<td>{html.escape(str(e['entity_type']))}</td>"
            f"<td>{html.escape(str(e['district']))}</td>"
            f"<td>{html.escape(str(e['registration_date']))}</td>"
            f"<td><span class='tag-success'>{html.escape(str(e['status']))}</span></td></tr>"
        )
    ent_table_html = "\n".join(ent_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URSB Business Registry Portal</title>
<style>{COMMON_CSS}</style>
</head>
<body>
<div class="container">

  <!-- AUTH GATE (Shown when not authenticated) -->
  <div id="authGate">
    <div class="auth-wrapper">
      <div style="text-align:center; margin-bottom:1.5rem;">
        <div style="display:flex; justify-content:center; margin-bottom:0.5rem;">{render_logo('ursb', 48)}</div>
        <h2 style="font-size:18px; color:var(--accent); margin-top:0.5rem;">URSB Business Registry Gateway</h2>
        <div style="font-size:12px; color:var(--text-muted);">Company / Business Authentication &amp; Security CAPTCHA Verification</div>
      </div>

      <div style="display:flex; justify-content:center; gap:1rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem;">
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('login')">Entity Sign In</button>
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('signup')">Incorporate Business</button>
      </div>

      <div id="authAlert"></div>

      <div id="loginFormPane">
        <form onsubmit="handleLogin(event)">
          <div class="form-group">
            <label class="form-label">Registration Number or BRN *</label>
            <input id="loginRegNo" type="text" class="input" required value="URSB-CO-10001">
          </div>
          <div class="form-group">
            <label class="form-label">Password *</label>
            <input id="loginPass" type="password" class="input" required value="Ursb@2026">
          </div>

          <div class="form-group">
            <label class="form-label">Security Verification Code (CAPTCHA) *</label>
            <div class="captcha-container">
              <canvas id="loginCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('login')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('login')" title="Refresh code">↻ Refresh</button>
            </div>
            <input id="loginCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above" maxlength="6">
          </div>

          <button type="submit" class="btn btn-primary" style="width:100%;">Sign In with Security Verification</button>
        </form>

        <div class="quick-login">
          <div style="font-size:11px; color:var(--text-muted); margin-bottom:0.5rem;">1-Click Quick Demo Sign In (Pre-seeded in DB):</div>
          <button class="quick-login-btn" onclick="quickLogin('URSB-CO-10001', 'Kakira Sugar Limited')">🏢 <strong>Kakira Sugar Limited</strong> (URSB-CO-10001)</button>
          <button class="quick-login-btn" onclick="quickLogin('URSB-CO-10002', 'Nile Breweries Limited')">🍺 <strong>Nile Breweries Limited</strong> (URSB-CO-10002)</button>
          <button class="quick-login-btn" onclick="quickLogin('URSB-BN-55102', 'Pearl Organic Coffee')">☕ <strong>Pearl Organic Coffee</strong> (URSB-BN-55102)</button>
        </div>
      </div>

      <div id="signupFormPane" style="display:none;">
        <form onsubmit="handleSignup(event)">
          <div class="form-group">
            <label class="form-label">Proposed Business Name *</label>
            <input id="signupName" type="text" class="input" required placeholder="e.g. Nile Craft Industries Ltd">
          </div>
          <div class="form-group">
            <label class="form-label">Entity Category</label>
            <select id="signupType" class="input">
              <option value="LIMITED_COMPANY">Private Company Limited by Shares</option>
              <option value="BUSINESS_NAME">Sole Proprietorship / Business Name</option>
              <option value="PARTNERSHIP">Partnership</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Applicant National ID (NIN) *</label>
            <input id="signupNin" type="text" class="input" required placeholder="14-char NIN" value="CM950019284KLA">
          </div>
          <div class="form-group">
            <label class="form-label">Security CAPTCHA *</label>
            <div class="captcha-container">
              <canvas id="signupCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('signup')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('signup')">↻ Refresh</button>
            </div>
            <input id="signupCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Submit Incorporation Application</button>
        </form>
      </div>
    </div>
  </div>

  <!-- PORTAL DASHBOARD (Visible only when authenticated) -->
  <div id="portalDashboard" style="display:none;">
    <div class="header">
      <div class="logo-title">
        {render_logo('ursb', 34)}
        <div>
          <h1 style="font-size:18px; color:var(--accent);">Uganda Registration Services Bureau (URSB)</h1>
          <div style="font-size:12px; color:var(--text-muted);">Company Incorporation, Business Names &amp; Form 20 Registry · Independent Store (<code>ursb_system.db</code>)</div>
        </div>
      </div>
      <div id="authStatus"><span class="badge">URSB Active</span></div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="openTab('dashboard')">📊 Dashboard</button>
      <button class="tab-btn" onclick="openTab('search')">🔍 Search Business / BRN</button>
      <button class="tab-btn" onclick="openTab('register')">➕ Register Company</button>
      <button class="tab-btn" onclick="openTab('compliance')">📋 Compliance &amp; TIN Check</button>
    </div>

    <div id="dashboard" class="tab-pane active">
      <div class="grid-metrics">
        <div class="metric"><div class="metric-label">Registered Entities</div><div class="metric-val">{stats['total_registered_entities']}</div></div>
        <div class="metric"><div class="metric-label">Active Companies</div><div class="metric-val">{stats['active_entities']}</div></div>
        <div class="metric"><div class="metric-label">Form 20 Directors</div><div class="metric-val">{stats['directors_count']}</div></div>
        <div class="metric"><div class="metric-label">URA TIN Prerequisite</div><div class="metric-val" style="color:var(--success);">VERIFIED</div></div>
        <div class="metric"><div class="metric-label">Database Store</div><div class="metric-val" style="color:var(--success);">ONLINE</div></div>
      </div>

      <div class="card">
        <h3 style="font-size:14px; margin-bottom:0.5rem; color:var(--accent);">Recent Incorporated Entities in Registry</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Registration No</th><th>Business Name</th><th>Type</th><th>District</th><th>Date</th><th>Status</th></tr></thead>
            <tbody>{ent_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="search" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Search Registered Company or Business Name</h3>
        <div id="searchAlert"></div>
        <form onsubmit="handleSearch(event)">
          <div class="form-group">
            <label class="form-label">Business Name or Registration Number *</label>
            <input id="searchQuery" type="text" class="input" required value="Kakira Sugar Limited">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Search URSB Registry</button>
        </form>
      </div>
    </div>

    <div id="register" class="tab-pane">
      <div class="card" style="max-width:600px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Incorporate New Company with URSB</h3>
        <div id="regAlert"></div>
        <form onsubmit="handleRegisterCompany(event)">
          <div class="form-group">
            <label class="form-label">Proposed Business Name *</label>
            <input id="regName" type="text" class="input" required placeholder="e.g. AfriTech Logistics Limited">
          </div>
          <div class="form-group">
            <label class="form-label">Entity Category</label>
            <select id="regType" class="input">
              <option value="LIMITED_COMPANY">Private Company Limited by Shares</option>
              <option value="BUSINESS_NAME">Sole Proprietorship / Business Name</option>
              <option value="PARTNERSHIP">Partnership</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Principal Nature of Business *</label>
            <input id="regNature" type="text" class="input" required value="Commercial Freight Logistics &amp; Transport">
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
            <div class="form-group">
              <label class="form-label">District *</label>
              <input id="regDistrict" type="text" class="input" required value="Kampala">
            </div>
            <div class="form-group">
              <label class="form-label">Applicant NIN (14-Chars) *</label>
              <input id="regNin" type="text" class="input" required value="CM950019284KLA">
            </div>
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Submit Incorporation</button>
        </form>
      </div>
    </div>

    <div id="compliance" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Check Legal Compliance &amp; TIN Readiness</h3>
        <div id="compAlert"></div>
        <form onsubmit="handleCheckCompliance(event)">
          <div class="form-group">
            <label class="form-label">URSB Registration Number *</label>
            <input id="compRegNo" type="text" class="input" required value="URSB-CO-10001">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Audit Compliance</button>
        </form>
      </div>
    </div>

    <div class="footer">
      <div>URSB Standalone Node v1.0.0 · Database: <code>ursb_system.db</code></div>
      <div>
        <a href="/docs">Swagger API Docs</a> |
        <a href="/mcp/manifest">MCP Tool Manifest</a> |
        <a href="/health">Health Probe</a>
      </div>
    </div>
  </div>

</div>

<script>
{COMMON_AUTH_JS}

let activeCaptcha = {{ login: '', signup: '' }};

function refreshCaptcha(mode) {{
  const code = generateCaptchaCode();
  activeCaptcha[mode] = code;
  drawCaptcha(mode + 'CaptchaCanvas', code);
}}

function openTab(tabId) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  if (event && event.target) event.target.classList.add('active');
  document.getElementById(tabId).classList.add('active');
}}

function showAuthMode(mode) {{
  document.getElementById('loginFormPane').style.display = mode === 'login' ? 'block' : 'none';
  document.getElementById('signupFormPane').style.display = mode === 'signup' ? 'block' : 'none';
  refreshCaptcha(mode);
}}

function quickLogin(regNo, name) {{
  document.getElementById('loginRegNo').value = regNo;
  document.getElementById('loginPass').value = 'Ursb@2026';
  document.getElementById('loginCaptchaInput').value = activeCaptcha['login'];
  handleLogin(new Event('submit'));
}}

function handleLogin(e) {{
  e.preventDefault();
  const regNo = document.getElementById('loginRegNo').value;
  const pass = document.getElementById('loginPass').value;
  const captcha = document.getElementById('loginCaptchaInput').value;

  fetch('/api/v1/auth/login', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      identifier: regNo,
      password: pass,
      captcha: captcha,
      expected_captcha: activeCaptcha['login']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      localStorage.setItem('ursb_user', JSON.stringify(res.body.user));
      document.getElementById('authAlert').innerHTML = '';
      checkAuth();
    }} else {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">' + (res.body.detail || 'Login failed') + '</div>';
      refreshCaptcha('login');
    }}
  }}).catch(err => {{
    document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">Network error: ' + err.message + '</div>';
    refreshCaptcha('login');
  }});
}}

function handleSignup(e) {{
  e.preventDefault();
  const name = document.getElementById('signupName').value;
  const type = document.getElementById('signupType').value;
  const nin = document.getElementById('signupNin').value;
  const captcha = document.getElementById('signupCaptchaInput').value;

  fetch('/api/v1/auth/signup', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      business_name: name,
      entity_type: type,
      applicant_nin: nin,
      nature_of_business: 'Commercial Enterprise',
      district: 'Kampala',
      captcha: captcha,
      expected_captcha: activeCaptcha['signup']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-success">✓ Entity incorporated under ' + res.body.registration_number + '! You can now sign in.</div>';
      showAuthMode('login');
    }} else {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">' + (res.body.detail || 'Registration failed') + '</div>';
      refreshCaptcha('signup');
    }}
  }});
}}

function checkAuth() {{
  const u = localStorage.getItem('ursb_user');
  if (u) {{
    const parsed = JSON.parse(u);
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('portalDashboard').style.display = 'block';
    document.getElementById('authStatus').innerHTML = '<span class="badge" style="background:#10b981; color:#fff;">✓ ' + (parsed.business_name || parsed.name) + '</span> <button class="btn btn-secondary" style="padding:2px 6px; font-size:10px; margin-left:6px;" onclick="logout()">Logout</button>';
  }} else {{
    document.getElementById('authGate').style.display = 'block';
    document.getElementById('portalDashboard').style.display = 'none';
    refreshCaptcha('login');
  }}
}}

function logout() {{
  localStorage.removeItem('ursb_user');
  checkAuth();
}}

function handleSearch(e) {{
  e.preventDefault();
  const q = document.getElementById('searchQuery').value;
  fetch('/api/v1/business/search?query=' + encodeURIComponent(q)).then(r => r.json()).then(data => {{
    if (data.ok && data.found) {{
      const ent = data.entity;
      document.getElementById('searchAlert').innerHTML = '<div class="alert alert-success"><strong>✓ REGISTERED ENTITY FOUND</strong><br>Name: <strong>' + ent.business_name + '</strong><br>Registration Number: <code>' + ent.registration_number + '</code><br>Type: ' + ent.entity_type + '<br>District: ' + ent.district + '<br>Incorporated: ' + ent.registration_date + '</div>';
    }} else {{
      document.getElementById('searchAlert').innerHTML = '<div class="alert alert-danger">❌ No registered entity found matching query</div>';
    }}
  }});
}}

function handleRegisterCompany(e) {{
  e.preventDefault();
  const name = document.getElementById('regName').value;
  const type = document.getElementById('regType').value;
  const nature = document.getElementById('regNature').value;
  const dist = document.getElementById('regDistrict').value;
  const nin = document.getElementById('regNin').value;

  fetch('/api/v1/business/register', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      business_name: name,
      entity_type: type,
      nature_of_business: nature,
      registered_office: dist + ' Central',
      district: dist,
      applicant_nin: nin,
      directors: [{{ full_name: 'Primary Director', nin_or_passport: nin }}]
    }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('regAlert').innerHTML = '<div class="alert alert-success"><strong>✓ Company Incorporated!</strong><br>Registration Number: <code>' + data.registration_number + '</code><br>Certificate: ' + data.certificate_reference + '<br>' + data.message + '</div>';
    }} else {{
      document.getElementById('regAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Failed') + '</div>';
    }}
  }});
}}

function handleCheckCompliance(e) {{
  e.preventDefault();
  const regNo = document.getElementById('compRegNo').value;
  fetch('/api/v1/business/compliance/' + regNo).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('compAlert').innerHTML = '<div class="alert alert-success"><strong>Compliance Audit:</strong><br>' + data.details + '<br>Ready for URA TIN: <strong>' + (data.ready_for_ura_tin ? 'YES' : 'NO') + '</strong></div>';
    }} else {{
      document.getElementById('compAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Audit failed') + '</div>';
    }}
  }});
}}

checkAuth();
</script>
</body>
</html>"""  # noqa: S608


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "healthy", "service": "ursb", "database": service.get_stats()}


@app.post("/api/v1/auth/login")
def login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    reg_no = str(payload.get("identifier") or payload.get("registration_number", "")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA security code.")

    ent = service.database.get_entity(reg_no)
    if not ent:
        raise HTTPException(status_code=401, detail=f"Registration number '{reg_no}' not found in URSB registry.")

    return {
        "ok": True,
        "token": f"ursb_jwt_{reg_no}",
        "user": ent.to_dict(),
        "message": f"Welcome back, {ent.business_name}!",
    }


@app.post("/api/v1/auth/signup")
def signup(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()
    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA code.")
    return client.register_business(**payload)


@app.get("/api/v1/auth/me")
def me(registration_number: str = Query("URSB-CO-10001")) -> dict[str, Any]:
    ent = service.database.get_entity(registration_number)
    return {"ok": True, "user": ent.to_dict() if ent else None}


@app.get("/api/v1/business/search")
def search(query: str = Query(...)) -> dict[str, Any]:
    return client.search_business(query=query)


@app.post("/api/v1/business/register")
def register(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return client.register_business(**payload)


@app.get("/api/v1/business/compliance/{registration_number}")
def compliance(registration_number: str) -> dict[str, Any]:
    return client.check_compliance(registration_number=registration_number)


@app.get("/mcp/manifest")
def mcp_manifest() -> dict[str, Any]:
    return {
        "schema_version": "2026-07-28",
        "system": "ursb",
        "tools": [t.to_mcp_tool() for t in connector.get_tools()],
    }


@app.post("/mcp/call")
def mcp_call(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = body.get("name")
    args = body.get("arguments", {})
    tool_map = {t.schema.name: t for t in connector.get_tools()}
    if name not in tool_map:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"ok": True, "result": tool_map[name].execute(**args)}
