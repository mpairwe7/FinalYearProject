"""Development-only dashboard for the local BWIMS simulator.

Features:
- Dedicated SQLite database (data_store/bwims_system.db)
- Demo login screen and sample accounts (not production authentication)
- Fixture cargo, warehouse, and clearance examples
- Illustrative duration calculations, not legal deadlines or customs decisions
- No connection to URA or standards-compliant MCP transport
"""

from __future__ import annotations

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

os.environ.setdefault("BWIMS_DB_PATH", str(_root / "data_store" / "bwims_system.db"))

from plugins.apps.common_ui import (  # noqa: E402
    COMMON_AUTH_JS,
    COMMON_CSS,
    install_simulator_safety_boundary,
    render_logo,
)
from plugins.bwims import BwimsClient, BwimsConnector, BwimsService  # noqa: E402

service = BwimsService()
client = BwimsClient(service=service)
connector = BwimsConnector(service=service)
connector.initialize()

app = FastAPI(title="BWIMS Simulator Demo", version="1.0.0")
install_simulator_safety_boundary(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
def index() -> str:  # noqa: S608
    stats = service.get_stats()
    consignments = service.database.list_recent_consignments(6)
    c_rows = []
    for c in consignments:
        tag_cls = "tag-danger" if c["status"] == "OVERSTAYED_AUCTION_RISK" else "tag-success"
        c_rows.append(
            f"<tr><td><strong style='color:var(--accent);'>{c['entry_number']}</strong></td>"
            f"<td>{c['importer_name']}</td>"
            f"<td>{c['warehouse_code']}</td>"
            f"<td>{c['goods_description']}</td>"
            f"<td>UGX {c['cif_value_ugx']:,.0f}</td>"
            f"<td><span class='{tag_cls}'>{c['status']}</span></td></tr>"
        )
    c_table_html = "\n".join(c_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URA BWIMS Customs System</title>
<style>{COMMON_CSS}</style>
</head>
<body>
<div class="container">

  <!-- AUTH GATE (Shown when not authenticated) -->
  <div id="authGate">
    <div class="auth-wrapper">
      <div style="text-align:center; margin-bottom:1.5rem;">
        <div style="display:flex; justify-content:center; margin-bottom:0.5rem;">{render_logo('bwims', 48)}</div>
        <h2 style="font-size:18px; color:var(--accent); margin-top:0.5rem;">URA BWIMS Customs Gateway</h2>
        <div style="font-size:12px; color:var(--text-muted);">Warehouse Operator Authentication &amp; Security CAPTCHA Verification</div>
      </div>

      <div style="display:flex; justify-content:center; gap:1rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem;">
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('login')">Operator Sign In</button>
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('signup')">Register Bond</button>
      </div>

      <div id="authAlert"></div>

      <div id="loginFormPane">
        <form onsubmit="handleLogin(event)">
          <div class="form-group">
            <label class="form-label">Warehouse Code / Station *</label>
            <input id="loginWhCode" type="text" class="input" required value="WH-KLA-001">
          </div>
          <div class="form-group">
            <label class="form-label">Password *</label>
            <input id="loginPass" type="password" class="input" required value="Warehouse@2026">
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
          <button class="quick-login-btn" onclick="quickBwimsLogin('WH-KLA-001', 'Uganda ICD Nakawa')">🏗️ <strong>Nakawa ICD</strong> (WH-KLA-001)</button>
          <button class="quick-login-btn" onclick="quickBwimsLogin('WH-JJA-002', 'Nile Grain Silos')">🌾 <strong>Jinja Silos</strong> (WH-JJA-002)</button>
          <button class="quick-login-btn" onclick="quickBwimsLogin('WH-EBB-003', 'Entebbe Aviation Shed')">✈️ <strong>Entebbe Airport Bond</strong> (WH-EBB-003)</button>
        </div>
      </div>

      <div id="signupFormPane" style="display:none;">
        <form onsubmit="handleSignup(event)">
          <div class="form-group">
            <label class="form-label">Warehouse Facility Name *</label>
            <input id="signupWhName" type="text" class="input" required placeholder="e.g. Namanve Inland Freight Terminal">
          </div>
          <div class="form-group">
            <label class="form-label">Operator Company TIN *</label>
            <input id="signupTin" type="text" class="input" required placeholder="10-digit number" value="1000000007">
          </div>
          <div class="form-group">
            <label class="form-label">Security CAPTCHA *</label>
            <div class="captcha-container">
              <canvas id="signupCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('signup')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('signup')">↻ Refresh</button>
            </div>
            <input id="signupCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Apply for Bond Licensing</button>
        </form>
      </div>
    </div>
  </div>

  <!-- PORTAL DASHBOARD (Visible only when authenticated) -->
  <div id="portalDashboard" style="display:none;">
    <div class="header">
      <div class="logo-title">
        {render_logo('bwims', 34)}
        <div>
          <h1 style="font-size:18px; color:var(--accent);">URA Bonded Warehouse Information Management System (BWIMS)</h1>
          <div style="font-size:12px; color:var(--text-muted);">Customs IM7 Bonded Cargo &amp; Warehousing Tracking · Independent Store (<code>bwims_system.db</code>)</div>
        </div>
      </div>
      <div id="authStatus"><span class="badge">BWIMS Active</span></div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="openTab('dashboard')">📊 Dashboard</button>
      <button class="tab-btn" onclick="openTab('consignments')">📦 Bonded Cargo ({stats['bonded_consignments_count']})</button>
      <button class="tab-btn" onclick="openTab('track')">🔍 Track IM7 Entry</button>
      <button class="tab-btn" onclick="openTab('release')">📤 Ex-Warehouse Release</button>
    </div>

    <div id="dashboard" class="tab-pane active">
      <div class="grid-metrics">
        <div class="metric"><div class="metric-label">Licensed Warehouses</div><div class="metric-val">{stats['warehouses_count']}</div></div>
        <div class="metric"><div class="metric-label">Total Bond Security</div><div class="metric-val">UGX {stats['total_bond_security_ugx']:,.0f}</div></div>
        <div class="metric"><div class="metric-label">Bonded Consignments</div><div class="metric-val">{stats['bonded_consignments_count']}</div></div>
        <div class="metric"><div class="metric-label">Total Bonded CIF</div><div class="metric-val">UGX {stats['total_cif_value_ugx']:,.0f}</div></div>
        <div class="metric"><div class="metric-label">Overstay Risk Alerts</div><div class="metric-val" style="color:var(--danger);">{stats['overstayed_consignments_count']}</div></div>
        <div class="metric"><div class="metric-label">Database Store</div><div class="metric-val" style="color:var(--success);">ONLINE</div></div>
      </div>

      <div class="card">
        <h3 style="font-size:14px; margin-bottom:0.5rem; color:var(--accent);">Recent Bonded Cargo Consignments in IM7 Ledger</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>IM7 Entry No</th><th>Importer</th><th>Warehouse</th><th>Goods Description</th><th>CIF Value</th><th>Status</th></tr></thead>
            <tbody>{c_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="consignments" class="tab-pane">
      <div class="card">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">All Customs Bonded Consignments</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>IM7 Entry No</th><th>Importer</th><th>Warehouse</th><th>Goods Description</th><th>CIF Value</th><th>Status</th></tr></thead>
            <tbody>{c_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="track" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Track IM7 Consignment &amp; Expiry Countdown</h3>
        <div id="trackAlert"></div>
        <form onsubmit="handleTrackEntry(event)">
          <div class="form-group">
            <label class="form-label">IM7 Customs Entry Number *</label>
            <input id="trackNo" type="text" class="input" required value="2026-ASY-IM7-88912">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Audit Warehousing Duration</button>
        </form>
        <div class="quick-login">
          <div style="font-size:11px; color:var(--text-muted); margin-bottom:0.4rem;">Sample Consignment Tests:</div>
          <button class="quick-login-btn" onclick="testEntry('2026-ASY-IM7-88912')">🚗 <strong>100 Toyota Hilux</strong> (2026-ASY-IM7-88912) - Active</button>
          <button class="quick-login-btn" onclick="testEntry('2026-ASY-IM7-44102')">🍬 <strong>500 MT Raw Sugar</strong> (2026-ASY-IM7-44102) - Active</button>
          <button class="quick-login-btn" onclick="testEntry('2025-ASY-IM7-00912')">⚠️ <strong>Secondhand Clothes</strong> (2025-ASY-IM7-00912) - OVERSTAYED</button>
        </div>
      </div>
    </div>

    <div id="release" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Ex-Warehouse Clearance (IM4 Home Consumption)</h3>
        <div id="relAlert"></div>
        <form onsubmit="handleExWarehouse(event)">
          <div class="form-group">
            <label class="form-label">IM7 Entry Number *</label>
            <input id="relEntry" type="text" class="input" required value="2026-ASY-IM7-88912">
          </div>
          <div class="form-group">
            <label class="form-label">Importer TIN *</label>
            <input id="relTin" type="text" class="input" required value="1000000007">
          </div>
          <div class="form-group">
            <label class="form-label">Quantity to Release *</label>
            <input id="relQty" type="number" class="input" required value="10">
          </div>
          <div class="form-group">
            <label class="form-label">Duty Payment PRN Reference *</label>
            <input id="relPrn" type="text" class="input" required value="226030005005">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Process IM4 Clearance</button>
        </form>
      </div>
    </div>

    <div class="footer">
      <div>URA BWIMS Standalone Node v1.0.0 · Database: <code>bwims_system.db</code></div>
      <div>
        <a href="/docs">Swagger API Docs</a> |
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

function quickBwimsLogin(code, name) {{
  document.getElementById('loginWhCode').value = code;
  document.getElementById('loginPass').value = 'Warehouse@2026';
  document.getElementById('loginCaptchaInput').value = activeCaptcha['login'];
  handleLogin(new Event('submit'));
}}

function handleLogin(e) {{
  e.preventDefault();
  const code = document.getElementById('loginWhCode').value;
  const pass = document.getElementById('loginPass').value;
  const captcha = document.getElementById('loginCaptchaInput').value;

  fetch('/api/v1/auth/login', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      identifier: code,
      password: pass,
      captcha: captcha,
      expected_captcha: activeCaptcha['login']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      localStorage.setItem('bwims_user', JSON.stringify(res.body.user));
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
  const name = document.getElementById('signupWhName').value;
  const tin = document.getElementById('signupTin').value;
  const captcha = document.getElementById('signupCaptchaInput').value;

  fetch('/api/v1/auth/signup', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      warehouse_name: name,
      tin: tin,
      captcha: captcha,
      expected_captcha: activeCaptcha['signup']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-success">✓ Bond licensing application submitted!</div>';
      showAuthMode('login');
    }} else {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">' + (res.body.detail || 'Failed') + '</div>';
      refreshCaptcha('signup');
    }}
  }});
}}

function checkAuth() {{
  const u = localStorage.getItem('bwims_user');
  if (u) {{
    const parsed = JSON.parse(u);
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('portalDashboard').style.display = 'block';
    document.getElementById('authStatus').innerHTML = '<span class="badge" style="background:#10b981; color:#fff;">✓ ' + (parsed.warehouse_name || parsed.warehouse_code) + '</span> <button class="btn btn-secondary" style="padding:2px 6px; font-size:10px; margin-left:6px;" onclick="logout()">Logout</button>';
  }} else {{
    document.getElementById('authGate').style.display = 'block';
    document.getElementById('portalDashboard').style.display = 'none';
    refreshCaptcha('login');
  }}
}}

function logout() {{
  localStorage.removeItem('bwims_user');
  checkAuth();
}}

function testEntry(no) {{
  document.getElementById('trackNo').value = no;
  fetch('/api/v1/consignments/' + no).then(r => r.json()).then(data => {{
    if (data.ok && data.found) {{
      const c = data.consignment;
      const isWarn = data.is_overstayed;
      document.getElementById('trackAlert').innerHTML = '<div class="alert ' + (isWarn ? 'alert-danger' : 'alert-success') + '"><strong>' + (isWarn ? '⚠️ OVERSTAYED CARGO ALERT' : '✓ BONDED IN STORAGE') + '</strong><br>Importer: ' + c.importer_name + '<br>Goods: ' + c.goods_description + '<br>Storage: ' + data.days_in_storage + ' days (Warehouse: ' + c.warehouse_code + ')<br>CIF: UGX ' + Number(c.cif_value_ugx).toLocaleString() + '</div>';
    }} else {{
      document.getElementById('trackAlert').innerHTML = '<div class="alert alert-danger">❌ Consignment not found</div>';
    }}
  }});
}}

function handleTrackEntry(e) {{
  e.preventDefault();
  testEntry(document.getElementById('trackNo').value);
}}

function handleExWarehouse(e) {{
  e.preventDefault();
  const entry = document.getElementById('relEntry').value;
  const tin = document.getElementById('relTin').value;
  const qty = parseFloat(document.getElementById('relQty').value);
  const prn = document.getElementById('relPrn').value;

  fetch('/api/v1/clearance', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ entry_number: entry, importer_tin: tin, cleared_quantity: qty, duty_paid_prn: prn }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('relAlert').innerHTML = '<div class="alert alert-success"><strong>Clearance Approved!</strong><br>Clearance ID: ' + data.clearance_id + '<br>Released: ' + data.cleared_quantity + '<br>Remaining in Bond: ' + data.remaining_quantity + '</div>';
    }} else {{
      document.getElementById('relAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Clearance failed') + '</div>';
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
    return {"status": "healthy", "service": "bwims", "database": service.get_stats()}


@app.post("/api/v1/auth/login")
def login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    code = str(payload.get("identifier") or payload.get("warehouse_code", "")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA security code.")

    return {"ok": True, "token": f"bwims_jwt_{code}", "user": {"warehouse_code": code, "warehouse_name": f"Facility {code}", "role": "OPERATOR"}}


@app.post("/api/v1/auth/signup")
def signup(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()
    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA code.")
    return {"ok": True, "user": payload, "status": "PENDING_URA_INSPECTION"}


@app.get("/api/v1/consignments/{entry_number}")
def consignment(entry_number: str) -> dict[str, Any]:
    return client.get_consignment(entry_number=entry_number)


@app.get("/api/v1/warehouses/{code}/inventory")
def warehouse_inventory(code: str, importer_tin: str | None = Query(None)) -> dict[str, Any]:
    return client.get_inventory(warehouse_code=code, importer_tin=importer_tin)


@app.post("/api/v1/clearance")
def clear_con(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return client.clear_ex_warehouse(**payload)


@app.get("/mcp/manifest", deprecated=True, summary="Retired legacy route (not MCP JSON-RPC)")
def mcp_manifest() -> dict[str, Any]:
    return {
        "schema_version": "2026-07-28",
        "system": "bwims",
        "tools": [t.to_mcp_tool() for t in connector.get_tools()],
    }


@app.post("/mcp/call", deprecated=True, summary="Retired legacy route (not MCP JSON-RPC)")
def mcp_call(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = body.get("name")
    args = body.get("arguments", {})
    tool_map = {t.schema.name: t for t in connector.get_tools()}
    if name not in tool_map:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"ok": True, "result": tool_map[name].execute(**args)}
