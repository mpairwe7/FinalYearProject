"""Common CSS, HTML templates, and UI components for URA enterprise standalone systems."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

COMMON_CSS = """
:root {
  --bg: #0b132b;
  --surface: #1c2541;
  --surface-alt: #162039;
  --border: #3a506b;
  --accent: #00adb5;
  --accent-hover: #08d9d6;
  --text: #f8fafc;
  --text-muted: #94a3b8;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: radial-gradient(ellipse at top, #1e293b 0%, #0f172a 50%, #020617 100%); color: var(--text); min-height: 100vh; line-height: 1.5; padding: 1.5rem; }
.container { max-width: 1050px; margin: 0 auto; }
.header { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem; }
.logo-title { display: flex; align-items: center; gap: 0.75rem; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 9999px; background: rgba(0, 173, 181, 0.15); color: var(--accent); border: 1px solid rgba(0, 173, 181, 0.3); font-weight: 600; text-transform: uppercase; }
.card { background: rgba(28, 37, 65, 0.85); backdrop-filter: blur(12px); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }
.grid-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
.metric { background: var(--surface-alt); padding: 1rem; border-radius: 8px; border: 1px solid var(--border); }
.metric-label { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.metric-val { font-size: 20px; font-weight: 700; color: var(--accent); margin-top: 4px; }
.tabs { display: flex; gap: 0.5rem; border-bottom: 1px solid var(--border); margin-bottom: 1.5rem; overflow-x: auto; padding-bottom: 2px; }
.tab-btn { padding: 0.5rem 1rem; font-size: 13px; font-weight: 600; color: var(--text-muted); background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer; transition: all 0.2s; white-space: nowrap; }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-btn:hover { color: var(--text); }
.tab-pane { display: none; }
.tab-pane.active { display: block; }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0.5rem 1rem; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: 0.15s; }
.btn-primary { background: var(--accent); color: #0b132b; font-weight: 700; }
.btn-primary:hover { background: var(--accent-hover); box-shadow: 0 0 12px rgba(0, 173, 181, 0.4); }
.btn-secondary { background: var(--surface-alt); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { background: #233355; }
.form-group { margin-bottom: 1rem; }
.form-label { display: block; font-size: 12px; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; }
.input { width: 100%; padding: 0.5rem 0.75rem; background: var(--surface-alt); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 13px; font-family: monospace; outline: none; transition: border-color 0.2s; }
.input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(0, 173, 181, 0.2); }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; margin-top: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; }
th { background: #131d38; padding: 0.6rem 0.75rem; color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-family: monospace; border-bottom: 1px solid var(--border); }
td { padding: 0.6rem 0.75rem; border-bottom: 1px solid rgba(58, 80, 107, 0.4); font-family: monospace; }
tr:hover { background: rgba(0, 173, 181, 0.05); }
.tag-success { background: rgba(16, 185, 129, 0.2); color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.tag-warning { background: rgba(245, 158, 11, 0.2); color: #fbbf24; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.tag-danger { background: rgba(239, 68, 68, 0.2); color: #f87171; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.auth-wrapper { max-width: 480px; margin: 3rem auto; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 2rem; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
.quick-login { margin-top: 1.25rem; padding-top: 1rem; border-top: 1px dashed var(--border); }
.quick-login-btn { display: block; width: 100%; text-align: left; padding: 0.5rem 0.75rem; margin-bottom: 0.4rem; background: var(--surface-alt); border: 1px solid var(--border); border-radius: 6px; font-size: 11px; color: var(--text-muted); cursor: pointer; transition: 0.15s; }
.quick-login-btn strong { color: var(--accent); }
.quick-login-btn:hover { background: #233355; color: var(--text); border-color: var(--accent); }
.alert { padding: 0.75rem; border-radius: 8px; font-size: 12px; margin-bottom: 1rem; line-height: 1.4; }
.alert-success { background: rgba(16, 185, 129, 0.15); border: 1px solid var(--success); color: #34d399; }
.alert-danger { background: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger); color: #f87171; }
.captcha-container { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
.captcha-canvas { background: #162039; border: 1px solid var(--border); border-radius: 8px; cursor: pointer; }
.captcha-refresh-btn { background: var(--surface-alt); border: 1px solid var(--border); color: var(--text-muted); padding: 0.4rem 0.6rem; border-radius: 6px; font-size: 12px; cursor: pointer; }
.captcha-refresh-btn:hover { color: var(--accent); border-color: var(--accent); }
.footer { margin-top: 2rem; border-top: 1px solid var(--border); padding-top: 1rem; display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--text-muted); flex-wrap: wrap; gap: 0.5rem; }
.footer a { color: var(--accent); text-decoration: none; }
.footer a:hover { text-decoration: underline; }
"""

COMMON_AUTH_JS = """
function generateCaptchaCode() {
  const chars = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';  // pragma: allowlist secret
  let code = '';
  for (let i = 0; i < 5; i++) {
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return code;
}

function drawCaptcha(canvasId, code) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const grad = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  grad.addColorStop(0, '#162039');
  grad.addColorStop(1, '#0b132b');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (let i = 0; i < 4; i++) {
    ctx.strokeStyle = 'rgba(0, 173, 181, 0.3)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(Math.random() * canvas.width, Math.random() * canvas.height);
    ctx.bezierCurveTo(
      Math.random() * canvas.width, Math.random() * canvas.height,
      Math.random() * canvas.width, Math.random() * canvas.height,
      Math.random() * canvas.width, Math.random() * canvas.height
    );
    ctx.stroke();
  }

  ctx.font = 'bold 22px monospace';
  ctx.textBaseline = 'middle';
  const startX = 18;
  const spacing = (canvas.width - 36) / code.length;

  for (let i = 0; i < code.length; i++) {
    ctx.save();
    const x = startX + i * spacing;
    const y = canvas.height / 2 + (Math.random() - 0.5) * 4;
    const angle = (Math.random() - 0.5) * 0.35;
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.fillStyle = i % 2 === 0 ? '#38bdf8' : '#f59e0b';
    ctx.fillText(code[i], 0, 0);
    ctx.restore();
  }

  for (let i = 0; i < 25; i++) {
    ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
    ctx.beginPath();
    ctx.arc(Math.random() * canvas.width, Math.random() * canvas.height, 1, 0, Math.PI * 2);
    ctx.fill();
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const simulatorNotice = document.createElement('div');
  simulatorNotice.setAttribute('role', 'note');
  simulatorNotice.style.cssText = 'background:#422006;color:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;margin:0 auto 16px;max-width:1050px;font-size:13px;font-weight:600;';
  simulatorNotice.textContent = 'SIMULATION ONLY — This demo uses local sample data. It does not connect to URA, NIRA, URSB, customs, banks, or payment providers, and does not create official records.';
  document.body.insertBefore(simulatorNotice, document.body.firstChild);
});
"""


def install_simulator_safety_boundary(app: FastAPI) -> None:
    """Mark legacy demo responses and prevent their use as production services."""

    @app.middleware("http")
    async def simulator_safety_boundary(request: Request, call_next):  # noqa: ANN001
        if os.getenv("APP_ENV", "development").strip().lower() == "production" and request.url.path != "/health":
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Standalone connector demos are disabled in production; use official URA services.",
                    "mode": "simulation",
                    "live": False,
                },
            )
        if request.url.path.startswith("/mcp/"):
            return JSONResponse(
                status_code=410,
                content={
                    "detail": "This legacy endpoint is not an MCP JSON-RPC transport.",
                    "mode": "simulation",
                    "live": False,
                },
            )
        response = await call_next(request)
        response.headers["X-Connector-Mode"] = "simulation"
        response.headers["X-Connector-Live"] = "false"
        return response

EFRIS_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#0369a1" />
  <path d="M7 6h18v20l-3-2-3 2-3-2-3 2-3-2-3 2V6z" fill="#0284c7" />
  <path d="M10 11h12M10 15h12M10 19h7" stroke="#ffffff" stroke-width="2" stroke-linecap="round" />
  <circle cx="21" cy="20" r="3" fill="#38bdf8" />
  <path d="m20 20 1 1 2-2" stroke="#0f172a" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
</svg>"""

DTS_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#b45309" />
  <path d="M6 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V9z" fill="#d97706" />
  <path d="M10 11h4v4h-4zM18 11h4v4h-4zM10 17h4v4h-4z" fill="#ffffff" />
  <circle cx="20" cy="19" r="2.5" fill="#fef08a" />
  <path d="M8 7l16 18" stroke="#f59e0b" stroke-width="1" stroke-dasharray="2 2" />
</svg>"""

URSB_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#6b21a8" />
  <path d="M16 6l10 5H6l10-5z" fill="#c084fc" />
  <path d="M9 13v9M14 13v9M18 13v9M23 13v9" stroke="#ffffff" stroke-width="2" stroke-linecap="round" />
  <path d="M6 24h20v2H6v-2z" fill="#e9d5ff" />
</svg>"""

BWIMS_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#047857" />
  <path d="M6 20h20l-3 6H9l-3-6z" fill="#10b981" />
  <path d="M8 12h5v6H8zM15 10h5v8h-5zM21 14h4v4h-4z" fill="#34d399" />
  <path d="M5 24c4 1 7-1 11 0s7-1 11 0" stroke="#a7f3d0" stroke-width="1.5" stroke-linecap="round" />
</svg>"""

TIN_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#0e7490" />
  <rect x="6" y="8" width="20" height="16" rx="3" fill="#0891b2" stroke="#67e8f9" stroke-width="1.5" />
  <rect x="9" y="11" width="5" height="5" rx="1" fill="#cffafe" />
  <path d="M17 12h6M17 15h4M9 19h14" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round" />
</svg>"""

PAYMENT_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#be123c" />
  <rect x="5" y="9" width="22" height="14" rx="2.5" fill="#e11d48" stroke="#fecdd3" stroke-width="1.2" />
  <path d="M5 13h22" stroke="#881337" stroke-width="2" />
  <circle cx="10" cy="18" r="1.5" fill="#ffffff" />
  <path d="M14 18h8" stroke="#ffe4e6" stroke-width="1.5" stroke-linecap="round" />
</svg>"""

GATEWAY_LOGO_SVG = """<svg width="{size}" height="{size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="32" height="32" rx="8" fill="#0f172a" stroke="#00adb5" stroke-width="1.5" />
  <circle cx="16" cy="16" r="9" stroke="#38bdf8" stroke-width="2" stroke-dasharray="3 3" />
  <circle cx="16" cy="16" r="4" fill="#00adb5" />
  <path d="M16 4v4M16 24v4M4 16h4M24 16h4" stroke="#00adb5" stroke-width="2" stroke-linecap="round" />
</svg>"""


def render_logo(logo_type: str, size: int = 32) -> str:
    logos = {
        "efris": EFRIS_LOGO_SVG,
        "dts": DTS_LOGO_SVG,
        "ursb": URSB_LOGO_SVG,
        "bwims": BWIMS_LOGO_SVG,
        "tin": TIN_LOGO_SVG,
        "payment": PAYMENT_LOGO_SVG,
        "gateway": GATEWAY_LOGO_SVG,
    }
    template = logos.get(logo_type.lower(), GATEWAY_LOGO_SVG)
    return template.format(size=size)
