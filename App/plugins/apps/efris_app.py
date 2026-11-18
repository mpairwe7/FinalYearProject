"""Development-only dashboard for the local EFRIS simulator.

Features:
- Dedicated SQLite database (data_store/efris_system.db)
- Demo login screen and sample accounts (not production authentication)
- Sample FDN-shaped invoice and verification records
- Local credit-note and inventory examples
- VAT reconciliation demonstration using fixtures
- No connection to URA or standards-compliant MCP transport
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

os.environ.setdefault("EFRIS_DB_PATH", str(_root / "data_store" / "efris_system.db"))

from plugins.apps.common_ui import (  # noqa: E402
    COMMON_AUTH_JS,
    COMMON_CSS,
    install_simulator_safety_boundary,
    render_logo,
)
from plugins.efris import EfrisClient, EfrisConnector, EfrisService  # noqa: E402

service = EfrisService()
client = EfrisClient(service=service)
connector = EfrisConnector(service=service)
connector.initialize()

app = FastAPI(title="EFRIS Simulator Demo", version="1.0.0")
install_simulator_safety_boundary(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
def index() -> str:  # noqa: S608
    stats = service.get_stats()
    invoices = service.database.list_recent_invoices(6)
    taxpayers = [service.database.get_taxpayer(t) for t in ["1000000001", "1000000002", "1000000005"]]
    stock = service.database.get_stock_items("1000000005")

    inv_rows = []
    for inv in invoices:
        inv_rows.append(
            f"<tr><td><strong style='color:var(--accent);'>{html.escape(str(inv['fdn']))}</strong></td>"
            f"<td>{html.escape(str(inv['seller_name']))}</td>"
            f"<td>{html.escape(str(inv.get('buyer_name') or 'Walk-in'))}</td>"
            f"<td>UGX {inv['gross_amount']:,.0f}</td>"
            f"<td>UGX {inv['tax_amount']:,.0f}</td>"
            f"<td><span class='tag-success'>{html.escape(str(inv['status']))}</span></td></tr>"
        )
    inv_table_html = "\n".join(inv_rows)

    stock_rows = []
    for s in stock:
        stock_rows.append(
            f"<tr><td>{html.escape(str(s['commodity_code']))}</td>"
            f"<td><strong>{html.escape(str(s['description']))}</strong></td>"
            f"<td>{s['quantity_on_hand']:,.0f} {html.escape(str(s['unit_of_measure']))}</td>"
            f"<td>UGX {s['unit_cost']:,.0f}</td>"
            f"<td>{html.escape(str(s['category']))}</td></tr>"
        )
    stock_table_html = "\n".join(stock_rows)

    tp_opts = []
    for tp in taxpayers:
        if tp:
            tp_opts.append(f"<option value='{html.escape(str(tp.tin))}'>{html.escape(str(tp.business_name))} (Tax ID: {html.escape(str(tp.tin))})</option>")
    tp_options_html = "\n".join(tp_opts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URA EFRIS Portal</title>
<style>{COMMON_CSS}</style>
</head>
<body>
<div class="container">

  <!-- AUTH GATE (Shown when not authenticated) -->
  <div id="authGate">
    <div class="auth-wrapper">
      <div style="text-align:center; margin-bottom:1.5rem;">
        <div style="display:flex; justify-content:center; margin-bottom:0.5rem;">{render_logo('efris', 48)}</div>
        <h2 style="font-size:18px; color:var(--accent); margin-top:0.5rem;">URA EFRIS Access Gateway</h2>
        <div style="font-size:12px; color:var(--text-muted);">Authentication &amp; Security CAPTCHA Verification Required</div>
      </div>

      <div style="display:flex; justify-content:center; gap:1rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem;">
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('login')">Sign In</button>
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('signup')">Register on EFRIS</button>
      </div>

      <div id="authAlert"></div>

      <div id="loginFormPane">
        <form onsubmit="handleLogin(event)">
          <div class="form-group">
            <label class="form-label">Taxpayer 10-Digit TIN *</label>
            <input id="loginTin" type="text" class="input" required placeholder="e.g. 1000000001" value="1000000001">
          </div>
          <div class="form-group">
            <label class="form-label">Password *</label>
            <input id="loginPass" type="password" class="input" required value="Efris@2026">
          </div>

          <!-- URA Security CAPTCHA Box -->
          <div class="form-group">
            <label class="form-label">Security Verification Code (CAPTCHA) *</label>
            <div class="captcha-container">
              <canvas id="loginCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('login')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('login')" title="Refresh security code">↻ Refresh</button>
            </div>
            <input id="loginCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above" maxlength="6">
          </div>

          <button type="submit" class="btn btn-primary" style="width:100%;">Sign In with Security Verification</button>
        </form>

        <div class="quick-login">
          <div style="font-size:11px; color:var(--text-muted); margin-bottom:0.5rem;">1-Click Quick Demo Sign In (Pre-seeded Accounts):</div>
          <button class="quick-login-btn" onclick="quickLogin('1000000001', 'Kakira Sugar Limited')">🏢 <strong>Kakira Sugar Limited</strong> (Tax ID: 1000000001)</button>
          <button class="quick-login-btn" onclick="quickLogin('1000000002', 'Nile Breweries Limited')">🍺 <strong>Nile Breweries Limited</strong> (Tax ID: 1000000002)</button>
          <button class="quick-login-btn" onclick="quickLogin('1000000005', 'Kampala City Supermarket')">🛒 <strong>Kampala City Supermarket</strong> (Tax ID: 1000000005)</button>
        </div>
      </div>

      <div id="signupFormPane" style="display:none;">
        <form onsubmit="handleSignup(event)">
          <div class="form-group">
            <label class="form-label">Company / Business Name *</label>
            <input id="signupName" type="text" class="input" required placeholder="e.g. Nile Agro Exporters Ltd">
          </div>
          <div class="form-group">
            <label class="form-label">10-Digit TIN *</label>
            <input id="signupTin" type="text" class="input" required placeholder="10-digit number">
          </div>
          <div class="form-group">
            <label class="form-label">Integration Mode</label>
            <select id="signupMode" class="input">
              <option value="SYSTEM_TO_SYSTEM">System-to-System ERP (REST API)</option>
              <option value="EFD">Electronic Fiscal Device (EFD)</option>
              <option value="E_INVOICING">Web Portal Invoicing</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Security CAPTCHA *</label>
            <div class="captcha-container">
              <canvas id="signupCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('signup')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('signup')">↻ Refresh</button>
            </div>
            <input id="signupCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Enroll on EFRIS</button>
        </form>
      </div>
    </div>
  </div>

  <!-- PORTAL DASHBOARD (Visible only when authenticated) -->
  <div id="portalDashboard" style="display:none;">
    <div class="header">
      <div class="logo-title">
        {render_logo('efris', 34)}
        <div>
          <h1 style="font-size:18px; color:var(--accent);">URA EFRIS e-Invoicing Portal</h1>
          <div style="font-size:12px; color:var(--text-muted);">Electronic Fiscal Receipting &amp; Invoicing System · Independent Store (<code>efris_system.db</code>)</div>
        </div>
      </div>
      <div id="authStatus" style="font-size:12px; text-align:right;"></div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="openTab('dashboard')">📊 Dashboard</button>
      <button class="tab-btn" onclick="openTab('invoices')">📑 Fiscal Invoices ({stats['invoices_count']})</button>
      <button class="tab-btn" onclick="openTab('issue')">➕ Issue e-Invoice</button>
      <button class="tab-btn" onclick="openTab('verify')">🔍 Verify FDN</button>
      <button class="tab-btn" onclick="openTab('stock')">📦 Stock Inventory</button>
      <button class="tab-btn" onclick="openTab('vat_return')">📑 VAT Return (DT-1014)</button>
    </div>

    <div id="dashboard" class="tab-pane active">
      <div class="grid-metrics">
        <div class="metric"><div class="metric-label">Enrolled Taxpayers</div><div class="metric-val">{stats['taxpayers_count']}</div></div>
        <div class="metric"><div class="metric-label">Fiscal Invoices Issued</div><div class="metric-val">{stats['invoices_count']}</div></div>
        <div class="metric"><div class="metric-label">Total Fiscalized Gross</div><div class="metric-val">UGX {stats['total_fiscalized_ugx']:,.0f}</div></div>
        <div class="metric"><div class="metric-label">18% VAT Collected</div><div class="metric-val">UGX {stats['total_vat_collected_ugx']:,.0f}</div></div>
        <div class="metric"><div class="metric-label">Tracked Stock Commodities</div><div class="metric-val">{stats['stock_items_count']}</div></div>
        <div class="metric"><div class="metric-label">Database Store</div><div class="metric-val" style="color:var(--success);">ONLINE</div></div>
      </div>

      <div class="card">
        <h3 style="font-size:14px; margin-bottom:0.5rem; color:var(--accent);">Recent Fiscal Documents in Database</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>FDN (20-Digit)</th><th>Seller</th><th>Buyer</th><th>Gross</th><th>18% VAT</th><th>Status</th></tr></thead>
            <tbody>{inv_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="invoices" class="tab-pane">
      <div class="card">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">All Issued Fiscal Documents</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>FDN</th><th>Seller</th><th>Buyer</th><th>Gross</th><th>18% VAT</th><th>Status</th></tr></thead>
            <tbody>{inv_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="issue" class="tab-pane">
      <div class="card" style="max-width:600px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Issue Real-time EFRIS Fiscal Invoice</h3>
        <div id="issueAlert"></div>
        <form id="issueForm" onsubmit="handleIssueInvoice(event)">
          <div class="form-group">
            <label class="form-label">Select Registered Seller TIN *</label>
            <select id="sellerTin" class="input">{tp_options_html}</select>
          </div>
          <div class="form-group">
            <label class="form-label">Buyer TIN (Optional for B2C Retail)</label>
            <input id="buyerTin" type="text" class="input" placeholder="e.g. 1000000001" value="1000000001">
          </div>
          <div class="form-group">
            <label class="form-label">Item Description *</label>
            <input id="itemDesc" type="text" class="input" required value="Mineral Drinking Water 500ml">
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
            <div class="form-group">
              <label class="form-label">Quantity *</label>
              <input id="itemQty" type="number" class="input" required value="50">
            </div>
            <div class="form-group">
              <label class="form-label">Unit Price (UGX) *</label>
              <input id="itemPrice" type="number" class="input" required value="1000">
            </div>
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Generate 20-Digit FDN &amp; Fiscalize</button>
        </form>
      </div>
    </div>

    <div id="verify" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Verify EFRIS Fiscal Document Number (FDN)</h3>
        <div id="verifyAlert"></div>
        <form onsubmit="handleVerifyFDN(event)">
          <div class="form-group">
            <label class="form-label">Enter 20-Digit FDN *</label>
            <input id="verifyFdn" type="text" class="input" required value="01240000000000001001">
          </div>
          <div class="form-group">
            <label class="form-label">Verification Code (Optional)</label>
            <input id="verifyCode" type="text" class="input" value="A9F23B">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Verify Authenticity</button>
        </form>
      </div>
    </div>

    <div id="stock" class="tab-pane">
      <div class="card">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">Live Stock Balances (Kampala City Supermarket)</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Commodity Code</th><th>Description</th><th>Stock on Hand</th><th>Unit Cost</th><th>Category</th></tr></thead>
            <tbody>{stock_table_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="vat_return" class="tab-pane">
      <div class="card" style="max-width:600px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">EFRIS Auto-Reconciled Monthly VAT Return (Form DT-1014)</h3>
        <p style="font-size:12px; color:var(--text-muted); margin-bottom:1rem;">Under Section 31 of the VAT Act, output tax is reconciled in real-time from issued fiscal invoices.</p>
        <div id="vatAlert"></div>
        <form onsubmit="handleReconcileVat(event)">
          <div class="form-group">
            <label class="form-label">Select Registered Taxpayer *</label>
            <select id="vatTin" class="input">{tp_options_html}</select>
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Auto-Reconcile Output VAT with EFRIS</button>
        </form>
      </div>
    </div>

    <div class="footer">
      <div>URA EFRIS Standalone Node v1.0.0 · Database: <code>efris_system.db</code></div>
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

function quickLogin(tin, name) {{
  document.getElementById('loginTin').value = tin;
  document.getElementById('loginPass').value = 'Efris@2026';
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
      localStorage.setItem('efris_user', JSON.stringify(res.body.user));
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
  const mode = document.getElementById('signupMode').value;
  const captcha = document.getElementById('signupCaptchaInput').value;

  fetch('/api/v1/auth/signup', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      business_name: name,
      tin: tin,
      integration_mode: mode,
      captcha: captcha,
      expected_captcha: activeCaptcha['signup']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-success">✓ Enrolled ' + name + ' on EFRIS! You can now sign in.</div>';
      showAuthMode('login');
    }} else {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">' + (res.body.detail || 'Signup failed') + '</div>';
      refreshCaptcha('signup');
    }}
  }});
}}

function checkAuth() {{
  const u = localStorage.getItem('efris_user');
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
  localStorage.removeItem('efris_user');
  checkAuth();
}}

function handleIssueInvoice(e) {{
  e.preventDefault();
  const seller = document.getElementById('sellerTin').value;
  const buyer = document.getElementById('buyerTin').value;
  const desc = document.getElementById('itemDesc').value;
  const qty = parseFloat(document.getElementById('itemQty').value);
  const price = parseFloat(document.getElementById('itemPrice').value);

  fetch('/api/v1/invoices/issue', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      seller_tin: seller,
      buyer_tin: buyer,
      items: [{{ commodity_code: '50202301', description: desc, quantity: qty, unit_price: price }}]
    }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('issueAlert').innerHTML = '<div class="alert alert-success"><strong>✓ Invoice Fiscalized!</strong><br>FDN: <code>' + data.fdn + '</code><br>Verification Code: <code>' + data.verification_code + '</code><br>Gross: UGX ' + Number(data.gross_amount).toLocaleString() + ' (VAT: UGX ' + Number(data.tax_amount).toLocaleString() + ')</div>';
    }} else {{
      document.getElementById('issueAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Failed') + '</div>';
    }}
  }});
}}

function handleVerifyFDN(e) {{
  e.preventDefault();
  const fdn = document.getElementById('verifyFdn').value;
  const code = document.getElementById('verifyCode').value;
  fetch('/api/v1/invoices/verify/' + fdn + (code ? '?verification_code=' + code : '')).then(r => r.json()).then(data => {{
    if (data.ok && data.is_authentic) {{
      document.getElementById('verifyAlert').innerHTML = '<div class="alert alert-success"><strong>✓ GENUINE FISCAL INVOICE</strong><br>Seller: ' + data.seller_name + ' (TIN: ' + data.seller_tin + ')<br>Gross: UGX ' + Number(data.gross_amount).toLocaleString() + '<br>Issued: ' + data.issue_date + '</div>';
    }} else {{
      document.getElementById('verifyAlert').innerHTML = '<div class="alert alert-danger">❌ ' + (data.error || 'Invoice not authentic') + '</div>';
    }}
  }});
}}

function handleReconcileVat(e) {{
  e.preventDefault();
  const tin = document.getElementById('vatTin').value;
  fetch('/api/v1/returns/vat-reconciliation', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ tin: tin }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('vatAlert').innerHTML = '<div class="alert alert-success"><strong>✓ Form DT-1014 Auto-Reconciliation Complete:</strong><br>Total EFRIS Taxable Sales: <strong>UGX ' + Number(data.total_taxable_sales_ugx).toLocaleString() + '</strong><br>Calculated Output VAT (18%): <strong>UGX ' + Number(data.output_vat_ugx).toLocaleString() + '</strong><br>Matched Fiscal Document Invoices: <strong>' + data.matched_invoices_count + '</strong></div>';
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
    return {"status": "healthy", "service": "efris", "database": service.get_stats()}


@app.post("/api/v1/auth/login")
def login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tin = str(payload.get("identifier") or payload.get("tin", "")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    # Enforce CAPTCHA check if expected is passed
    if expected and captcha != expected:
        raise HTTPException(
            status_code=400,
            detail="Invalid CAPTCHA security code. Please enter the characters shown in the security box.",
        )

    tp = service.database.get_taxpayer(tin)
    if not tp:
        raise HTTPException(status_code=401, detail=f"Taxpayer with TIN '{tin}' not found in EFRIS registry.")

    return {
        "ok": True,
        "token": f"efris_jwt_{tin}",
        "user": tp.to_dict(),
        "message": f"Welcome back, {tp.business_name}!",
    }


@app.post("/api/v1/auth/signup")
def signup(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tin = str(payload.get("tin", "")).strip()
    name = str(payload.get("business_name", "")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA code.")

    if not tin or not name:
        raise HTTPException(status_code=400, detail="TIN and business_name are required.")

    existing = service.database.get_taxpayer(tin)
    if existing:
        raise HTTPException(status_code=409, detail="Taxpayer TIN is already registered on EFRIS.")

    now_utc = datetime.datetime.now(datetime.UTC)
    conn = service.database._get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO efris_taxpayers (
                tin, business_name, is_vat_registered, efris_status, mandated_sector,
                registration_date, integration_mode, active_terminals, sdc_enabled, created_at
            ) VALUES (?, ?, 1, 'ACTIVE', 'General Trade', ?, ?, '[]', 1, ?)
            """,
            (tin, name, now_utc.strftime("%Y-%m-%d"), payload.get("integration_mode", "SYSTEM_TO_SYSTEM"), now_utc.isoformat()),
        )
    return {"ok": True, "tin": tin, "business_name": name, "status": "ACTIVE"}


@app.get("/api/v1/auth/me")
def me(tin: str = Query("1000000001")) -> dict[str, Any]:
    tp = service.database.get_taxpayer(tin)
    return {"ok": True, "user": tp.to_dict() if tp else None}


@app.post("/api/v1/invoices/issue")
def issue_invoice(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return client.issue_invoice(**payload)


@app.get("/api/v1/invoices/verify/{fdn}")
def verify_invoice(fdn: str, verification_code: str | None = Query(None)) -> dict[str, Any]:
    return client.verify_invoice(fdn=fdn, verification_code=verification_code)


@app.get("/api/v1/taxpayers/{tin}")
def taxpayer_profile(tin: str) -> dict[str, Any]:
    return client.get_taxpayer_profile(tin=tin)


@app.get("/api/v1/stock")
def get_stock(tin: str = Query("1000000005")) -> dict[str, Any]:
    return client.get_stock(tin=tin)


@app.post("/api/v1/returns/vat-reconciliation")
def vat_reconciliation(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tin = str(payload.get("tin", "1000000005")).strip()
    invoices = service.database.list_recent_invoices(20)
    matched = [inv for inv in invoices if inv.get("seller_tin") == tin]
    total_gross = sum(inv["gross_amount"] for inv in matched)
    total_tax = sum(inv["tax_amount"] for inv in matched)
    return {
        "ok": True,
        "tin": tin,
        "matched_invoices_count": len(matched),
        "total_taxable_sales_ugx": round(total_gross - total_tax, 2),
        "output_vat_ugx": round(total_tax, 2),
        "reconciliation_status": "RECONCILED_WITH_EFRIS",
    }


@app.get("/mcp/manifest", deprecated=True, summary="Retired legacy route (not MCP JSON-RPC)")
def mcp_manifest() -> dict[str, Any]:
    return {
        "schema_version": "2026-07-28",
        "system": "efris",
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
