"""PDF report generation for conversation export and tax summaries.

Uses ``fpdf2`` (MIT, no system dependencies) to generate branded PDF
reports from conversation history or tax calculation results.

Endpoints:
    POST /v1/export/conversation  → PDF bytes
    POST /v1/export/tax-summary   → PDF bytes
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF

log = logging.getLogger(__name__)

# URA brand colours (from the official style guide).
_URA_NAVY = (26, 58, 107)
_URA_GOLD = (198, 156, 47)
_WHITE = (255, 255, 255)
_LIGHT_GRAY = (240, 240, 240)
_DARK_GRAY = (60, 60, 60)


class _URAReport(FPDF):
    """URA-branded PDF base with header/footer."""

    def __init__(self, title: str = "URA Tax Assistant Report"):
        super().__init__()
        self._title = title
        self.set_auto_page_break(auto=True, margin=20)
        self._font_name = self._setup_font()

    def _setup_font(self) -> str:
        """Load NotoSans for full Unicode support (African language diacritics).
        Falls back to Helvetica if the TTF is not bundled."""
        font_dir = Path(__file__).parent / "fonts"
        noto_regular = font_dir / "NotoSans-Regular.ttf"
        noto_bold = font_dir / "NotoSans-Bold.ttf"

        if noto_regular.exists():
            self.add_font("NotoSans", "", str(noto_regular), uni=True)
            if noto_bold.exists():
                self.add_font("NotoSans", "B", str(noto_bold), uni=True)
            else:
                self.add_font("NotoSans", "B", str(noto_regular), uni=True)
            self.set_font("NotoSans", size=10)
            return "NotoSans"
        else:
            log.warning(
                "NotoSans font not found at %s — falling back to Helvetica. "
                "African language diacritics may not render correctly. "
                "Download NotoSans-Regular.ttf from Google Fonts.",
                font_dir,
            )
            self.set_font("Helvetica", size=10)
            return "Helvetica"

    def header(self):
        # Navy bar.
        self.set_fill_color(*_URA_NAVY)
        self.rect(0, 0, 210, 16, "F")
        # Title in white.
        self.set_text_color(*_WHITE)
        self.set_font(self._font_name, "B", 12)
        self.set_y(3)
        self.cell(0, 10, self._title, align="C")
        # Gold accent line.
        self.set_draw_color(*_URA_GOLD)
        self.set_line_width(0.8)
        self.line(10, 16, 200, 16)
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font(self._font_name, "I", 7)
        self.set_text_color(*_DARK_GRAY)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.cell(
            0,
            10,
            f"Generated {ts} | URA Tax Assistant | Page {self.page_no()}/{{nb}}",
            align="C",
        )


# ---------------------------------------------------------------------------
# Conversation export
# ---------------------------------------------------------------------------


def generate_conversation_pdf(
    messages: list[dict[str, Any]],
    *,
    title: str = "Conversation Report",
    session_id: str = "",
) -> bytes:
    """Render a conversation as a branded PDF. Returns raw PDF bytes."""
    pdf = _URAReport(title=title)
    pdf.alias_nb_pages()
    pdf.add_page()

    if session_id:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_DARK_GRAY)
        pdf.cell(0, 5, f"Session: {session_id}", ln=True)
        pdf.ln(3)

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")

        is_user = role == "user"

        # Role label.
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_URA_NAVY if is_user else _URA_GOLD)
        label = "You" if is_user else "URA Assistant"
        if timestamp:
            label += f"  ({timestamp})"
        pdf.cell(0, 5, label, ln=True)

        # Message body.
        if is_user:
            pdf.set_fill_color(*_LIGHT_GRAY)
        else:
            pdf.set_fill_color(245, 248, 255)

        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(*_DARK_GRAY)

        # Multi-cell for wrapping.
        x = pdf.get_x()
        pdf.get_y()
        pdf.set_x(x + 5)
        pdf.multi_cell(180, 5, content, fill=True)
        pdf.ln(4)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tax summary export
# ---------------------------------------------------------------------------


def generate_tax_summary_pdf(
    calculation: dict[str, Any],
    *,
    taxpayer_ref: str = "",
) -> bytes:
    """Render a tax calculation summary as a branded PDF."""
    pdf = _URAReport(title="Tax Calculation Summary")
    pdf.alias_nb_pages()
    pdf.add_page()

    # Taxpayer reference.
    if taxpayer_ref:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*_URA_NAVY)
        pdf.cell(0, 6, f"Reference: {taxpayer_ref}", ln=True)
        pdf.ln(3)

    # Summary table.
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*_WHITE)
    pdf.set_fill_color(*_URA_NAVY)
    pdf.cell(95, 8, "Item", border=1, fill=True)
    pdf.cell(95, 8, "Amount (UGX)", border=1, fill=True, align="R")
    pdf.ln()

    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(*_DARK_GRAY)

    items = calculation.get("items", [])
    for i, item in enumerate(items):
        bg = _LIGHT_GRAY if i % 2 == 0 else _WHITE
        pdf.set_fill_color(*bg)
        pdf.cell(95, 7, str(item.get("label", "")), border=1, fill=True)
        pdf.cell(
            95,
            7,
            f"{item.get('amount', 0):,.0f}",
            border=1,
            fill=True,
            align="R",
        )
        pdf.ln()

    # Total row.
    total = calculation.get("total", 0)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(*_URA_GOLD)
    pdf.set_text_color(*_WHITE)
    pdf.cell(95, 9, "TOTAL", border=1, fill=True)
    pdf.cell(95, 9, f"{total:,.0f}", border=1, fill=True, align="R")
    pdf.ln(12)

    # Notes.
    notes = calculation.get("notes", "")
    if notes:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*_DARK_GRAY)
        pdf.multi_cell(0, 5, f"Notes: {notes}")

    # Disclaimer.
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0,
        4,
        "This is an estimate generated by the URA Tax Assistant and does not "
        "constitute a binding tax assessment. For official calculations, "
        "please use the URA e-services portal or contact URA directly.",
    )

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
