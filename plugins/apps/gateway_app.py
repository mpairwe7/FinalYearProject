"""Standalone development-only dashboard for connector simulator examples.

Features:
- Dedicated SQLite stores (data_store/tin_system.db and data_store/payments_system.db)
- Demo login screens and sample account presets (not production authentication)
- Local sample TIN, payment, return, TCC, and objection scenarios
- No URA, NIRA, URSB, bank, payment-provider, or MCP connection
- Disabled outside development; all displayed outcomes are simulations
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path
_parents = Path(__file__).resolve().parents
_root = _parents[2] if len(_parents) > 2 else _parents[0]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from plugins.apps.common_ui import (  # noqa: E402
    COMMON_AUTH_JS,
    COMMON_CSS,
    install_simulator_safety_boundary,
    render_logo,
)
from plugins.orchestrator import get_orchestrator  # noqa: E402
from plugins.payment_system import PaymentClient  # noqa: E402
from plugins.tin_registration import TinRegistrationClient  # noqa: E402

orchestrator = get_orchestrator()
tin_client = TinRegistrationClient()
payment_client = PaymentClient()

app = FastAPI(title="Connector Simulator Demo", version="1.0.0")
install_simulator_safety_boundary(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
def index() -> str:  # noqa: S608
    summary = orchestrator.get_connectors_summary()
    conn_cards = []
    for c in summary:
        conn_cards.append(
            f"<div class='metric'>"
            f"<div style='font-weight:bold; color:var(--accent);'>{c['name']}</div>"
            f"<div style='font-size:12px; color:var(--text-muted); margin:4px 0;'>DB: <code>{c['database'].get('database', 'sqlite')}</code> · Status: <span class='tag-success'>ONLINE</span></div>"
            f"<div style='font-size:11px; color:var(--text);'>Tools ({len(c['tools'])}): {', '.join(c['tools'][:2])}...</div>"
            f"</div>"
        )
    conn_cards_html = "\n".join(conn_cards)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URA Enterprise Gateway</title>
<style>{COMMON_CSS}</style>
</head>
<body>
<div class="container">

  <!-- AUTH GATE (Shown when not authenticated) -->
  <div id="authGate">
    <div class="auth-wrapper">
      <div style="text-align:center; margin-bottom:1.5rem;">
        <div style="display:flex; justify-content:center; margin-bottom:0.5rem;">{render_logo('gateway', 48)}</div>
        <h2 style="font-size:18px; color:var(--accent); margin-top:0.5rem;">URA Central Enterprise Gateway</h2>
        <div style="font-size:12px; color:var(--text-muted);">Unified Taxpayer Sign In &amp; Security CAPTCHA Verification</div>
      </div>

      <div style="display:flex; justify-content:center; gap:1rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem;">
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('login')">Taxpayer Sign In</button>
        <button type="button" class="btn btn-secondary" onclick="showAuthMode('signup')">Instant NIN Registration</button>
      </div>

      <div id="authAlert"></div>

      <div id="loginFormPane">
        <form onsubmit="handleLogin(event)">
          <div class="form-group">
            <label class="form-label">Taxpayer 10-Digit TIN or NIN *</label>
            <input id="loginTin" type="text" class="input" required value="1000000001">
          </div>
          <div class="form-group">
            <label class="form-label">Password *</label>
            <input id="loginPass" type="password" class="input" required value="Taxpayer@2026">
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
          <div style="font-size:11px; color:var(--text-muted); margin-bottom:0.5rem;">1-Click Quick Demo Sign In (Pre-seeded Accounts):</div>
          <button class="quick-login-btn" onclick="quickGatewayLogin('1000000001', 'Kakira Sugar Limited')">🏢 <strong>Kakira Sugar Limited</strong> (Tax ID: 1000000001)</button>
          <button class="quick-login-btn" onclick="quickGatewayLogin('1000000002', 'Nile Breweries Limited')">🍺 <strong>Nile Breweries Limited</strong> (Tax ID: 1000000002)</button>
          <button class="quick-login-btn" onclick="quickGatewayLogin('1000000008', 'David Ochieng (Individual)')">👤 <strong>David Ochieng</strong> (Tax ID: 1000000008)</button>
        </div>
      </div>

      <div id="signupFormPane" style="display:none;">
        <form onsubmit="handleSignup(event)">
          <div class="form-group">
            <label class="form-label">Full Legal Name *</label>
            <input id="signupName" type="text" class="input" required placeholder="e.g. Sarah Namubiru" value="Sarah Namubiru">
          </div>
          <div class="form-group">
            <label class="form-label">National ID (NIN - 14 Chars) *</label>
            <input id="signupNin" type="text" class="input" required placeholder="14-char NIN" value="CM950019284KLA">
          </div>
          <div class="form-group">
            <label class="form-label">Mobile Number *</label>
            <input id="signupPhone" type="text" class="input" required value="+256772123456">
          </div>
          <div class="form-group">
            <label class="form-label">Security CAPTCHA *</label>
            <div class="captcha-container">
              <canvas id="signupCaptchaCanvas" width="160" height="42" class="captcha-canvas" onclick="refreshCaptcha('signup')"></canvas>
              <button type="button" class="captcha-refresh-btn" onclick="refreshCaptcha('signup')">↻ Refresh</button>
            </div>
            <input id="signupCaptchaInput" type="text" class="input" required placeholder="Enter characters shown above">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Get Instant Individual TIN</button>
        </form>
      </div>
    </div>
  </div>

  <!-- PORTAL DASHBOARD (Visible only when authenticated) -->
  <div id="portalDashboard" style="display:none;">
    <div class="header">
      <div class="logo-title">
        {render_logo('gateway', 34)}
        <div>
          <h1 style="font-size:18px; color:var(--accent);">Connector Simulator Demo</h1>
          <div style="font-size:12px; color:var(--text-muted);">Local examples backed by SQLite fixtures; no external service is connected</div>
        </div>
      </div>
      <div id="authStatus"><span class="badge">Local simulations</span></div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="openTab('dashboard')">📊 System Overview</button>
      <button class="tab-btn" onclick="openTab('instant_tin')">🪪 Instant TIN Registration</button>
      <button class="tab-btn" onclick="openTab('payment')">💳 Make a Payment (PRN)</button>
      <button class="tab-btn" onclick="openTab('checkout')">📱 Card &amp; MoMo Checkout</button>
      <button class="tab-btn" onclick="openTab('returns')">📑 File Return &amp; TCC</button>
      <button class="tab-btn" onclick="openTab('objections')">⚖️ Lodge Objection</button>
    </div>

    <div id="dashboard" class="tab-pane active">
      <div class="card">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:1rem;">All Connected Enterprise Systems &amp; Independent Databases</h3>
        <div class="grid-metrics">{conn_cards_html}</div>
        <p style="font-size:12px; color:var(--text-muted);">
          🔗 <a href="/docs" style="color:var(--accent);">OpenAPI / Swagger Documentation</a> |
          <a href="/health" style="color:var(--accent);">Health Probes</a>
        </p>
      </div>
    </div>

    <div id="instant_tin" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Instant Individual TIN Application (NIRA Verification)</h3>
        <div id="tinAlert"></div>
        <form onsubmit="handleApplyTin(event)">
          <div class="form-group">
            <label class="form-label">National Identification Number (NIN - 14 Chars) *</label>
            <input id="tinNin" type="text" class="input" required value="CM950019284KLA">
          </div>
          <div class="form-group">
            <label class="form-label">Full Legal Name *</label>
            <input id="tinName" type="text" class="input" required value="Sarah Namubiru">
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
            <div class="form-group">
              <label class="form-label">Mobile Number *</label>
              <input id="tinPhone" type="text" class="input" required value="+256772123456">
            </div>
            <div class="form-group">
              <label class="form-label">District *</label>
              <input id="tinDist" type="text" class="input" required value="Wakiso">
            </div>
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Verify with NIRA &amp; Issue 10-Digit TIN</button>
        </form>
      </div>
    </div>

    <div id="payment" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Make a Payment: Generate 12-Digit PRN Slip</h3>
        <div id="prnAlert"></div>
        <form onsubmit="handleGeneratePrn(event)">
          <div class="form-group">
            <label class="form-label">Taxpayer / Payer Name *</label>
            <input id="prnName" type="text" class="input" required value="Kampala City Supermarket Ltd">
          </div>
          <div class="form-group">
            <label class="form-label">Amount in Uganda Shillings (UGX) *</label>
            <input id="prnAmount" type="number" class="input" required value="150000">
          </div>
          <div class="form-group">
            <label class="form-label">Tax Head / Fee Type *</label>
            <select id="prnHead" class="input">
              <option value="VAT_STANDARD">Value Added Tax (VAT 18%)</option>
              <option value="PAYE">Pay As You Earn (PAYE)</option>
              <option value="CORPORATION_TAX">Corporation Tax (CIT)</option>
              <option value="PASSPORT_FEE">Ministry of Internal Affairs (Passport Fee)</option>
              <option value="TRAFFIC_FINE">Uganda Police Traffic Fine</option>
            </select>
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Generate PRN Slip (21-Day Validity)</button>
        </form>
      </div>
    </div>

    <div id="checkout" class="tab-pane">
      <div class="card" style="max-width:550px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.75rem;">Instant Card / Mobile Money Checkout</h3>
        <div id="checkAlert"></div>
        <form onsubmit="handleCheckout(event)">
          <div class="form-group">
            <label class="form-label">12-Digit PRN *</label>
            <input id="checkPrn" type="text" class="input" required value="226030003003">
          </div>
          <div class="form-group">
            <label class="form-label">Payment Channel</label>
            <select id="checkMethod" class="input">
              <option value="VISA">VISA Debit / Credit Card</option>
              <option value="MASTERCARD">MasterCard</option>
              <option value="MTN_MOMO">MTN Mobile Money</option>
              <option value="AIRTEL_MONEY">Airtel Money</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Amount (UGX) *</label>
            <input id="checkAmount" type="number" class="input" required value="1500000">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Execute Cleared Settlement</button>
        </form>
      </div>
    </div>

    <div id="returns" class="tab-pane">
      <div class="card" style="max-width:600px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">File Tax Return &amp; Apply for Tax Clearance Certificate (TCC)</h3>
        <p style="font-size:12px; color:var(--text-muted); margin-bottom:1rem;">Submit monthly VAT / PAYE or annual income tax declarations to maintain compliant TCC standing.</p>
        <div id="returnAlert"></div>
        <form onsubmit="handleFileReturn(event)">
          <div class="form-group">
            <label class="form-label">Taxpayer TIN *</label>
            <input id="retTin" type="text" class="input" required value="1000000001">
          </div>
          <div class="form-group">
            <label class="form-label">Return Form Type</label>
            <select id="retForm" class="input">
              <option value="DT-1014">Form DT-1014 (Monthly VAT Return)</option>
              <option value="DT-1001">Form DT-1001 (Annual Corporation Tax Return)</option>
              <option value="DT-1004">Form DT-1004 (Monthly PAYE Return)</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Tax Period / Year</label>
            <input id="retPeriod" type="text" class="input" required value="2026-02">
          </div>
          <div style="display:flex; gap:1rem;">
            <button type="submit" class="btn btn-primary" style="flex:1;">Submit Return</button>
            <button type="button" class="btn btn-secondary" onclick="handleApplyTcc()" style="flex:1;">Request TCC Certificate</button>
          </div>
        </form>
      </div>
    </div>

    <div id="objections" class="tab-pane">
      <div class="card" style="max-width:600px; margin:0 auto;">
        <h3 style="font-size:14px; color:var(--accent); margin-bottom:0.5rem;">Lodge Administrative Tax Objection (Section 24 TPCA)</h3>
        <p style="font-size:12px; color:var(--text-muted); margin-bottom:1rem;">Taxpayers aggrieved by a tax assessment may lodge a formal administrative objection within 45 days.</p>
        <div id="objAlert"></div>
        <form onsubmit="handleLodgeObjection(event)">
          <div class="form-group">
            <label class="form-label">Assessment Number / Reference *</label>
            <input id="objAssNo" type="text" class="input" required value="ASS-2026-VAT-9912">
          </div>
          <div class="form-group">
            <label class="form-label">Taxpayer TIN *</label>
            <input id="objTin" type="text" class="input" required value="1000000003">
          </div>
          <div class="form-group">
            <label class="form-label">Grounds of Objection *</label>
            <textarea id="objGrounds" class="input" rows="3" required>Input tax credit disallowed in error. Valid EFRIS e-invoices with 20-digit FDN were provided during audit.</textarea>
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;">Lodge Formal Tax Objection</button>
        </form>
      </div>
    </div>

    <div class="footer">
      <div>URA Central Gateway Node v1.0.0 · Databases: <code>tin_system.db</code>, <code>payments_system.db</code></div>
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

function quickGatewayLogin(tin, name) {{
  document.getElementById('loginTin').value = tin;
  document.getElementById('loginPass').value = 'Taxpayer@2026';
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
      localStorage.setItem('gateway_user', JSON.stringify(res.body.user));
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
  const nin = document.getElementById('signupNin').value;
  const phone = document.getElementById('signupPhone').value;
  const captcha = document.getElementById('signupCaptchaInput').value;

  fetch('/api/v1/auth/signup', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      full_name: name,
      nin: nin,
      mobile: phone,
      district: 'Kampala',
      captcha: captcha,
      expected_captcha: activeCaptcha['signup']
    }})
  }}).then(r => r.json().then(d => ({{ status: r.status, body: d }}))).then(res => {{
    if (res.status === 200 && res.body.ok) {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-success">Simulation only: sample TIN-like value <strong>' + res.body.tin + '</strong>. No official TIN was issued and NIRA was not contacted.</div>';
      showAuthMode('login');
    }} else {{
      document.getElementById('authAlert').innerHTML = '<div class="alert alert-danger">' + (res.body.detail || 'Registration failed') + '</div>';
      refreshCaptcha('signup');
    }}
  }});
}}

function checkAuth() {{
  const u = localStorage.getItem('gateway_user');
  if (u) {{
    const parsed = JSON.parse(u);
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('portalDashboard').style.display = 'block';
    document.getElementById('authStatus').innerHTML = '<span class="badge" style="background:#10b981; color:#fff;">✓ ' + (parsed.name || parsed.legal_name || parsed.tin) + '</span> <button class="btn btn-secondary" style="padding:2px 6px; font-size:10px; margin-left:6px;" onclick="logout()">Logout</button>';
  }} else {{
    document.getElementById('authGate').style.display = 'block';
    document.getElementById('portalDashboard').style.display = 'none';
    refreshCaptcha('login');
  }}
}}

function logout() {{
  localStorage.removeItem('gateway_user');
  checkAuth();
}}

function handleApplyTin(e) {{
  e.preventDefault();
  const nin = document.getElementById('tinNin').value;
  const name = document.getElementById('tinName').value;
  const phone = document.getElementById('tinPhone').value;
  const dist = document.getElementById('tinDist').value;

  fetch('/api/v1/tin/taxpayers/apply-instant', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ nin: nin, full_name: name, mobile: phone, district: dist }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('tinAlert').innerHTML = '<div class="alert alert-success"><strong>Simulation only — no official TIN was issued.</strong><br>Sample value: <strong>' + data.tin + '</strong><br>No identity record was checked with NIRA.</div>';
    }} else {{
      document.getElementById('tinAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Application failed') + '</div>';
    }}
  }});
}}

function handleGeneratePrn(e) {{
  e.preventDefault();
  const name = document.getElementById('prnName').value;
  const amt = parseFloat(document.getElementById('prnAmount').value);
  const head = document.getElementById('prnHead').value;

  fetch('/api/v1/payments/prn/generate', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ taxpayer_name: name, amount_ugx: amt, tax_head: head }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('prnAlert').innerHTML = '<div class="alert alert-success"><strong>Simulation only — this is not a valid URA PRN.</strong><br>Sample reference: <strong>' + data.prn + '</strong><br>' + data.message + '</div>';
    }} else {{
      document.getElementById('prnAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Failed') + '</div>';
    }}
  }});
}}

function handleCheckout(e) {{
  e.preventDefault();
  const prn = document.getElementById('checkPrn').value;
  const method = document.getElementById('checkMethod').value;
  const amt = parseFloat(document.getElementById('checkAmount').value);

  fetch('/api/v1/payments/checkout', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ prn: prn, payment_method: method, amount_paid_ugx: amt, payer_identifier: '256772123456' }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('checkAlert').innerHTML = '<div class="alert alert-success"><strong>Simulation only — no money moved and no payment was made.</strong><br>Sample transaction: ' + data.transaction_id + '<br>' + data.message + '</div>';
    }} else {{
      document.getElementById('checkAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'Checkout failed') + '</div>';
    }}
  }});
}}

function handleFileReturn(e) {{
  e.preventDefault();
  const tin = document.getElementById('retTin').value;
  const form = document.getElementById('retForm').value;
  const per = document.getElementById('retPeriod').value;

  fetch('/api/v1/returns/file', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ tin: tin, form_type: form, period: per }})
  }}).then(r => r.json()).then(data => {{
    document.getElementById('returnAlert').innerHTML = '<div class="alert alert-success"><strong>Simulation only — your return was not filed.</strong><br>Sample reference: <code>' + data.acknowledgement_number + '</code></div>';
  }});
}}

function handleApplyTcc() {{
  const tin = document.getElementById('retTin').value;
  fetch('/api/v1/tcc/apply', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ tin: tin }})
  }}).then(r => r.json()).then(data => {{
    if (data.ok) {{
      document.getElementById('returnAlert').innerHTML = '<div class="alert alert-success"><strong>Simulation only — no tax clearance certificate was issued.</strong><br>Sample reference: <code>' + data.tcc_number + '</code></div>';
    }} else {{
      document.getElementById('returnAlert').innerHTML = '<div class="alert alert-danger">' + (data.error || 'TCC Denied: Outstanding liabilities') + '</div>';
    }}
  }});
}}

function handleLodgeObjection(e) {{
  e.preventDefault();
  const assNo = document.getElementById('objAssNo').value;
  const tin = document.getElementById('objTin').value;
  const grounds = document.getElementById('objGrounds').value;

  fetch('/api/v1/objections/lodge', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ assessment_number: assNo, tin: tin, grounds: grounds }})
  }}).then(r => r.json()).then(data => {{
    document.getElementById('objAlert').innerHTML = '<div class="alert alert-success"><strong>Simulation only — no objection was lodged.</strong><br>Sample reference: <code>' + data.case_reference + '</code></div>';
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
    return {"status": "healthy", "service": "ura-gateway", "health": orchestrator.health_check()}


@app.post("/api/v1/auth/login")
def login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tin = str(payload.get("identifier") or payload.get("tin", "1000000001")).strip()
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()

    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA security code.")

    tp = tin_client.search_taxpayer(tin)
    name = (tp.get("taxpayer") or {}).get("legal_name") if tp.get("ok") and tp.get("found") else f"Taxpayer {tin}"

    return {
        "ok": True,
        "token": f"ura_gateway_jwt_{tin}",
        "user": {"tin": tin, "name": name},
        "message": f"Welcome back, {name}!",
    }


@app.post("/api/v1/auth/signup")
def signup(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    captcha = str(payload.get("captcha", "")).strip().upper()
    expected = str(payload.get("expected_captcha", "")).strip().upper()
    if expected and captcha != expected:
        raise HTTPException(status_code=400, detail="Invalid CAPTCHA code.")
    return tin_client.apply_instant_individual_tin(**payload)


@app.post("/api/v1/tin/taxpayers/apply-instant")
def tin_apply_instant(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return tin_client.apply_instant_individual_tin(**payload)


@app.post("/api/v1/payments/prn/generate")
def payments_generate_prn(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return payment_client.generate_prn(**payload)


@app.post("/api/v1/payments/checkout")
def payments_checkout(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return payment_client.process_checkout(**payload)


@app.post("/api/v1/returns/file")
def file_return(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    import secrets

    ref = f"ACK-DT-{payload.get('period', '2026')}-{1000 + secrets.randbelow(9000)}"
    return {
        "ok": True,
        "acknowledgement_number": ref,
        "form_type": payload.get("form_type", "DT-1014"),
        "period": payload.get("period", "2026-02"),
        "status": "ASSESSMENT_ACCEPTED",
    }


@app.post("/api/v1/tcc/apply")
def apply_tcc(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    import secrets

    tcc = f"TCC-URA-{secrets.randbelow(900000) + 100000}"
    return {
        "ok": True,
        "tcc_number": tcc,
        "tin": payload.get("tin", "1000000001"),
        "valid_until": "2027-12-31",
        "status": "APPROVED",
        "compliance_standing": "FULLY_COMPLIANT",
    }


@app.post("/api/v1/objections/lodge")
def lodge_objection(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    import secrets

    case_ref = f"OBJ-TPCA-{1000 + secrets.randbelow(9000)}"
    return {
        "ok": True,
        "case_reference": case_ref,
        "assessment_number": payload.get("assessment_number"),
        "statutory_review_days": 45,
        "enforcement_stayed": True,
    }


@app.get("/mcp/manifest", deprecated=True, summary="Retired legacy route (not MCP JSON-RPC)")
def mcp_manifest() -> dict[str, Any]:
    tools = []
    for tool in orchestrator.get_all_tools():
        if hasattr(tool, "to_mcp_tool"):
            tools.append(tool.to_mcp_tool())
        else:
            tools.append({
                "name": tool.schema.name,
                "description": tool.schema.description,
                "inputSchema": tool.schema.parameters,
            })
    return {
        "schema_version": "2026-07-28",
        "server_name": "ura-enterprise-connectors-gateway",
        "connectors": orchestrator.get_connectors_summary(),
        "tools": tools,
    }


@app.post("/mcp/call", deprecated=True, summary="Retired legacy route (not MCP JSON-RPC)")
def mcp_call(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = body.get("name")
    args = body.get("arguments", {})
    tool_map = {t.schema.name: t for t in orchestrator.get_all_tools()}
    if name not in tool_map:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    return {"ok": True, "result": tool_map[name].execute(**args)}
