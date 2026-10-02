"""Standalone FastAPI Application for URA Digital Tax Stamps (DTS / Kakasa platform).

Features:
- Dedicated SQLite database (data_store/dts_system.db)
- Mandatory Auth Gate with URA Security CAPTCHA verification
- 1-click Demo Account presets (Nile Breweries, Kakira Sugar, Rwenzori Bottling, Tororo Cement)
- Kakasa mobile stamp authentication with interactive QR / barcode testing
- 9 Gazetted excisable commodities ordering with PRN fee generation
- Line controller activation batching and spoiled stamp reconciliation
- Local Excise Duty (LED) return auto-reconciliation
- MCP 2026 tools gateway (/mcp/manifest, /mcp/call)
"""

from __future__ import annotations

import datetime
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

os.environ.setdefault("DTS_DB_PATH", str(_root / "data_store" / "dts_system.db"))

from plugins.apps.common_ui import COMMON_AUTH_JS, COMMON_CSS, render_logo  # noqa: E402
from plugins.digital_tax_stamps import (  # noqa: E402
    DigitalTaxStampsClient,
    DigitalTaxStampsConnector,
    DigitalTaxStampsService,
)

service = DigitalTaxStampsService()
client = DigitalTaxStampsClient(service=service)
connector = DigitalTaxStampsConnector(service=service)
connector.initialize()

app = FastAPI(title="URA Digital Tax Stamps (DTS / Kakasa) System", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
def index() -> str:  # noqa: S608
    stats = service.get_stats()
    stamps = service.database.list_recent_stamps(6)
    stamp_rows = []
    for st in stamps:
        tag_cls = "tag-success" if st["status"] == "GENUINE" else "tag-danger"
        stamp_rows.append(
            f"<tr><td><strong style='color:var(--accent);'>{html.escape(str(st['stamp_code']))}</strong></td>"
            f"<td>{html.escape(str(st['product_category']))}</td>"
            f"<td>{html.escape(str(st['brand_name']))}</td>"
            f"<td>{html.escape(str(st['manufacturer_name']))}</td>"
            f"<td><span class='{tag_cls}'>{html.escape(str(st['status']))}</span></td></tr>"
        )
    stamp_table_html = "\n".join(stamp_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URA Digital Tax Stamps &amp; Kakasa</title>
<style>{COMMON_CSS}</style>
</head>
<body>
<div class="container">

  <!-- AUTH GATE (Shown when not authenticated) -->
  <div id="authGate">
    <div class="auth-wrapper">
      <div style="text-align:center; margin-bottom:1.5rem;">
        <div style="display:flex; justify-content:center; margin-bottom:0.5rem;">{render_logo('dts', 48)}</div>
        <h2 style="font-size:18px; color:var(--accent); margin-top:0.5rem;">URA DTS &amp; Kakasa Gateway</h2>
        <div style="font-size:12px; color:var(--text-muted);">Manufacturer Authentication &amp; Security CAPTCHA Verification</div>
      </div>

      <div style="display:flex; justify-content:center; gap:1rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem;">
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('login')">Manufacturer Sign In</button>
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('signup')">Register Plant</button>
      </div>

      <div id="authAlert"></div>

      <div id="loginFormPane">
        <form onsubmit="handleLogin(event)">
          <div class="form-group">
            <label class="form-label">Manufacturer 10-Digit TIN *</label>
            <input id="loginTin" type="text" class="input" required value="1000000002">
          </div>
          <div class="form-group">
            <label class="form-label">Password *</label>
            <input id="loginPass" type="password" class="input" required value="Manufacturer@2026">
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
          <button class="quick-login-btn" onclick="quickLogin('1000000002', 'Nile Breweries Limited')">🍺 <strong>Nile Breweries Limited</strong> (Tax ID: 1000000002)</button>
          <button class="quick-login-btn" onclick="quickLogin('1000000001', 'Kakira Sugar Limited')">🍬 <strong>Kakira Sugar Limited</strong> (Tax ID: 1000000001)</button>
          <button class="quick-login-btn" onclick="quickLogin('1000000006', 'Rwenzori Bottling Company')">💧 <strong>Rwenzori Bottling</strong> (Tax ID: 1000000006)</button>
          <button class="quick-login-btn" onclick="quickLogin('1000000003', 'Tororo Cement Limited')">🏗️ <strong>Tororo Cement Ltd</strong> (Tax ID: 1000000003)</button>
        </div>
      </div>

      <div id="signupFormPane" style="display:none;">
        <form onsubmit="handleSignup(event)">
          <div class="form-group">
            <label class="form-label">Manufacturer Name *</label>
            <input id="signupName" type="text" class="input" required placeholder="e.g. Uganda Craft Breweries Ltd">
          </div>
          <div class="form-group">
            <label class="form-label">10-Digit TIN *</label>
            <input id="signupTin" type="text" class="input" required placeholder="10-digit number">
          </div>
          <div class="form-group">
            <label class="form-label">Security CAPTCHA *</label>
            <div class="captcha-container">
              <canvas id="signupCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('signup')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('signup')">↻ Refresh</button>
            </div>
            <input id="signupCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Enroll Facility on DTS</button>
        </form>
      </div>
    </div>
  </div>

  <!-- PORTAL DASHBOARD (Visible only when authenticated) -->
  <div id="portalDashboard" style="display:none;">
    <div class="header">
      <div class="logo-title">
        {render_logo('dts', 34)}
        <div>
          <h1 style="font-size:18px; color:var(--accent);">URA Digital Tax Stamps (DTS) &amp; Kakasa</h1>
          <div style="font-size:12px; color:var(--text-muted);">Track-and-Trace Solution for Excisable Commodities · Independent Store (<code>dts_system.db</code>)</div>
        </div>
      </div>
      <div id="authStatus"><span class="badge">DTS Active</span></div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="openTab('dashboard')">📊 Dashboard</button>
      <button class="tab-btn" onclick="openTab('scanner')">🔍 Kakasa Stamp Scanner</button>
      <button class="tab-btn" onclick="openTab('stamps')">🏷️ Affixed Stamps ({stats['total_stamps_count']})</button>
      <button class="tab-btn" onclick="openTab('order')">🛒 Order Stamps &amp; PRN</button>
    </div>

    <div id="dashboard" class="tab-pane active">
      <div class="grid-metrics">
        <div class="metric"><div class="metric-label">Manufacturers &amp; Importers</div><div class="metric-val">{stats['manufacturers_count']}</div></div>
        <div class="metric"><div class="metric-label">Packaging Lines</div><div class="metric-val">{stats['packaging_lines_count']}</div></div>
        <div class="metric"><div class="metric-label">Total Stamps Tracked</div><div class="metric-val">{stats['total_stamps_count']}</div></div>
        <div class="metric"><div class="metric-label">Kakasa Verified Genuine</div><div class="metric-val" style="color:var(--success);">{stats['genuine_stamps_count']}</div></div>
        <div class="metric"><div class="metric-label">Requisitions Placed</div><div class="metric-val">{stats['orders_count']}</div></div>
        <div class="metric"><div class="metric-label">Database Store</div><div class="metric-val" style="color:var(--success);">ONLINE</div></div>
      </div>

      <div class="card">
        <h3 style="font-size:14px; margin-bottom:0.5rem; color:var(--accent);">Recent Digital Tax Stamps in Registry</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Stamp Serial</th><th>Category</th><th>Product Brand</th><th>Manufacturer</th><th>Status</th></tr></thead>
            <tbody>{stamp_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="scanner" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Kakasa Stamp Verification Scanner</h3>
        <div id="scanAlert"></div>
        <form onsubmit="handleVerifyStamp(event)">
          <div class="form-group">
            <label class="form-label">Digital Tax Stamp Code / Serial *</label>
            <input id="scanCode" type="text" class="input" required value="DTS-UG-BEV-990182746">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Authenticate Stamp via Kakasa</button>
        </form>
        <div class="quick-login" style="margin-top:1rem;">
          <div style="font-size:11px; color:var(--text-muted); margin-bottom:0.4rem;">Try Seeded Stamp Tests:</div>
          <button class="quick-login-btn" onclick="testStamp('DTS-UG-BEV-990182746')">✅ <strong>Nile Special Lager 500ml</strong> (DTS-UG-BEV-990182746)</button>
          <button class="quick-login-btn" onclick="testStamp('DTS-UG-WTR-554433221')">✅ <strong>Rwenzori Mineral Water 500ml</strong> (DTS-UG-WTR-554433221)</button>
          <button class="quick-login-btn" onclick="testStamp('DTS-UG-BEV-EXPIRED-001')">⚠️ <strong>Club Pilsener (Expired)</strong> (DTS-UG-BEV-EXPIRED-001)</button>
          <button class="quick-login-btn" onclick="testStamp('DTS-FAKE-COUNTERFEIT-999')">❌ <strong>Counterfeit Code</strong> (DTS-FAKE-COUNTERFEIT-999)</button>
        </div>
      </div>
    </div>

    <div id="stamps" class="tab-pane">
      <div class="card">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">Digital Tax Stamps Registry</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Stamp Serial</th><th>Category</th><th>Product Brand</th><th>Manufacturer</th><th>Status</th></tr></thead>
            <tbody>{stamp_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="order" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Requisition Digital Tax Stamps</h3>
        <div id="orderAlert"></div>
        <form onsubmit="handleOrderStamps(event)">
          <div class="form-group">
            <label class="form-label">Manufacturer TIN *</label>
            <input id="orderTin" type="text" class="input" required value="1000000002">
          </div>
          <div class="form-group">
            <label class="form-label">Gazetted Category *</label>
            <select id="orderCat" class="input">
              <option value="BEER">Beer (UGX 35/unit)</option>
              <option value="SPIRITS">Spirits (UGX 110/unit)</option>
              <option value="WINE">Wine (UGX 100/unit)</option>
              <option value="BOTTLED_WATER">Bottled Water (UGX 15/unit)</option>
              <option value="CEMENT">Cement (UGX 135/bag)</option>
              <option value="SUGAR">Sugar (UGX 35/bag)</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Order Quantity *</label>
            <input id="orderQty" type="number" class="input" required value="1000">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Place Requisition &amp; Generate PRN</button>
        </form>
      </div>
    </div>

    <div class="footer">
      <div>URA DTS Standalone Node v1.0.0 · Database: <code>dts_system.db</code></div>
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

function quickLogin(tin, name) {{
  document.getElementById('loginTin').value = tin;
  document.getElementById('loginPass').value = 'Manufacturer@2026';
  document.getElementById('loginCaptchaInput').value = activeCaptcha['login'];
  handleLogin(new Event('submit'));
}}

function handleLogin(e) {{
  e.preventDefault();
  const tin = document.getElementById('loginTin').value;
  const pass = document.getElementById('loginPass').value;
  const captcha = document.getElementById('loginCaptchaInput').value;

  fetch('/api/v1/auth/login', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      identifier: tin,
      password: pass,
      captcha: captcha,
      expected_captcha: activeCaptcha['login']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      localStorage.setItem('dts_user', JSON.stringify(res.body.user));
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
  const tin = document.getElementById('signupTin').value;
  const captcha = document.getElementById('signupCaptchaInput').value;

  fetch('/api/v1/auth/signup', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      manufacturer_name: name,
      tin: tin,
      captcha: captcha,
      expected_captcha: activeCaptcha['signup']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-success">✓ Facility registered! You can now sign in.</div>';
      showAuthMode('login');
    }} else {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">' + (res.body.detail || 'Registration failed') + '</div>';
      refreshCaptcha('signup');
    }}
  }});
}}

function checkAuth() {{
  const u = localStorage.getItem('dts_user');
  if (u) {{
    const parsed = JSON.parse(u);
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('portalDashboard').style.display = 'block';
    document.getElementById('authStatus').innerHTML = '<span class="badge" style="background:#10b981; color:#fff;">✓ ' + (parsed.manufacturer_name || parsed.name) + '</span> <button class="btn btn-secondary" style="padding:2px 6px; font-size:10px; margin-left:6px;" onclick="logout()">Logout</button>';
  }} else {{
    document.getElementById('authGate').style.display = 'block';
    document.getElementById('portalDashboard').style.display = 'none';
    refreshCaptcha('login');
  }}
}}

function logout() {{
  localStorage.removeItem('dts_user');
  checkAuth();
}}

function handleVerifyStamp(e) {{
  e.preventDefault();
  const code = document.getElementById('scanCode').value;
  testStamp(code);
}}

function testStamp(code) {{
  document.getElementById('scanCode').value = code;
  fetch('/api/v1/stamps/verify/' + code).then(r => r.json()).then(data => {{
    if (data.ok && data.is_authentic) {{
      document.getElementById('scanAlert').innerHTML = '<div class="alert alert-success"><strong>✓ KAKASA GENUINE TAX STAMP</strong><br>Brand: ' + data.brand_name + '<br>Manufacturer: ' + data.manufacturer_name + '<br>Batch: ' + data.batch_number + '<br>Status: ' + data.status + '</div>';
    }} else {{
      document.getElementById('scanAlert').innerHTML = '<div class="alert alert-danger">❌ ' + (data.error || 'UNVERIFIED / COUNTERFEIT STAMP') + '</div>';
    }}
  }});
}}

function handleOrderStamps(e) {{
  e.preventDefault();
  const tin = document.getElementById('orderTin').value;
  const cat = document.getElementById('orderCat').value;
  const qty = parseInt(document.getElementById('orderQty').value);
  fetch('/api/v1/stamps/order', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ taxpayer_tin: tin, product_category: cat, quantity: qty }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('orderAlert').innerHTML = '<div class="alert alert-success"><strong>Requisition Placed!</strong><br>Order ID: ' + data.order_id + '<br>Payment PRN: <strong>' + data.prn + '</strong><br>Fee Payable: UGX ' + Number(data.total_amount_ugx).toLocaleString() + '<br>Collection: ' + data.collection_point + '</div>';
    }} else {{
      document.getElementById('orderAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Failed') + '</div>';
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
    return {"status": "healthy", "service": "dts", "database": service.get_stats()}


@app.post("/api/v1/auth/login")
def login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tin = str(payload.get("identifier") or payload.get("tin", "")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    if expected and captcha != expected:
        raise HTTPException(
            status_code=400,
            detail="Invalid CAPTCHA security code. Please enter the characters shown in the security box.",
        )

    mf = service.database.get_manufacturer(tin)
    if not mf:
        raise HTTPException(status_code=401, detail=f"Manufacturer with TIN '{tin}' not found in DTS registry.")

    return {
        "ok": True,
        "token": f"dts_jwt_{tin}",
        "user": mf.to_dict(),
        "message": f"Welcome back, {mf.manufacturer_name}!",
    }


@app.post("/api/v1/auth/signup")
def signup(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tin = str(payload.get("tin", "")).strip()
    name = str(payload.get("manufacturer_name", "")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA code.")

    if not tin or not name:
        raise HTTPException(status_code=400, detail="tin and manufacturer_name are required.")

    existing = service.database.get_manufacturer(tin)
    if existing:
        raise HTTPException(status_code=409, detail="Manufacturer TIN is already registered on DTS.")

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    conn = service.database._get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO dts_manufacturers (
                tin, manufacturer_name, taxpayer_type, gazetted_categories,
                dts_registration_date, active_stamps_inventory, is_compliant, created_at
            ) VALUES (?, ?, 'LOCAL_MANUFACTURER', '[\"BEER\"]', ?, 100000, 1, ?)
            """,
            (tin, name, now_utc.strftime("%Y-%m-%d"), now_utc.isoformat()),
        )
    return {"ok": True, "tin": tin, "manufacturer_name": name, "status": "ACTIVE"}


@app.get("/api/v1/auth/me")
def me(tin: str = Query("1000000002")) -> dict[str, Any]:
    mf = service.database.get_manufacturer(tin)
    return {"ok": True, "user": mf.to_dict() if mf else None}


@app.get("/api/v1/stamps/verify/{stamp_code}")
def verify_stamp(stamp_code: str) -> dict[str, Any]:
    return client.verify_stamp(stamp_code=stamp_code)


@app.post("/api/v1/stamps/order")
def order_stamps(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return client.order_stamps(**payload)


@app.get("/api/v1/manufacturers/{tin}")
def manufacturer_profile(tin: str) -> dict[str, Any]:
    return client.get_taxpayer_profile(tin=tin)


@app.get("/api/v1/tariffs")
def tariffs() -> dict[str, Any]:
    return client.list_tariffs()


@app.get("/mcp/manifest")
def mcp_manifest() -> dict[str, Any]:
    return {
        "schema_version": "2026-07-28",
        "system": "digital_tax_stamps",
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
