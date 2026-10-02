"""Compile all Markdown Benchmark Reports in docs/Reports/ to professional PDFs using WeasyPrint."""

import glob
import os
import re
from pathlib import Path
import markdown
import weasyprint

CSS_STYLE = """
@page {
    size: A4;
    margin: 20mm 16mm 20mm 16mm;
    @top-left {
        content: "UGANDA REVENUE AUTHORITY • AI TAXPAYER ASSISTANT";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 8pt;
        font-weight: 600;
        color: #7C2E26;
    }
    @top-right {
        content: "EMPIRICAL BENCHMARK REPORT";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 8pt;
        color: #64748b;
    }
    @bottom-left {
        content: "CONFIDENTIAL / OFFICIAL BENCHMARK ARTIFACT";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 8pt;
        color: #64748b;
    }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 8pt;
        font-weight: 600;
        color: #1e293b;
    }
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 9.5pt;
    line-height: 1.5;
    color: #1e293b;
    background-color: #ffffff;
}

h1 {
    font-size: 18pt;
    font-weight: 800;
    color: #7C2E26;
    border-bottom: 2.5pt solid #B7791F;
    padding-bottom: 4px;
    margin-top: 0;
    margin-bottom: 8px;
    letter-spacing: -0.02em;
}

h2 {
    font-size: 13pt;
    font-weight: 700;
    color: #1A365D;
    border-bottom: 1pt solid #e2e8f0;
    padding-bottom: 3px;
    margin-top: 16px;
    margin-bottom: 8px;
}

h3 {
    font-size: 11pt;
    font-weight: 700;
    color: #2D3748;
    margin-top: 12px;
    margin-bottom: 6px;
}

p {
    margin-top: 0;
    margin-bottom: 8px;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
    margin-bottom: 14px;
    font-size: 8.5pt;
    page-break-inside: auto;
}

tr {
    page-break-inside: avoid;
    page-break-after: auto;
}

thead {
    display: table-header-group;
}

th {
    background-color: #7C2E26;
    color: #ffffff;
    font-weight: 700;
    text-align: left;
    padding: 6px 8px;
    border: 1pt solid #7C2E26;
}

th:nth-child(2), th:nth-child(3), th:nth-child(4), th:nth-child(5), th:nth-child(6), th:nth-child(7) {
    text-align: center;
}

td {
    padding: 5px 8px;
    border: 1pt solid #cbd5e1;
    vertical-align: middle;
}

td:nth-child(2), td:nth-child(3), td:nth-child(4), td:nth-child(5), td:nth-child(6), td:nth-child(7) {
    text-align: center;
}

tbody tr:nth-child(even) {
    background-color: #f8fafc;
}

tbody tr:hover {
    background-color: #f1f5f9;
}

code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 8.5pt;
    background-color: #f1f5f9;
    color: #0f172a;
    padding: 1.5px 4px;
    border-radius: 3px;
    border: 0.5pt solid #e2e8f0;
}

pre {
    background-color: #0f172a;
    color: #f8fafc;
    padding: 10px 14px;
    border-radius: 5px;
    font-size: 8pt;
    overflow-x: hidden;
    white-space: pre-wrap;
    word-break: break-all;
    margin-bottom: 10px;
}

pre code {
    background-color: transparent;
    color: #f8fafc;
    padding: 0;
    border: none;
}

blockquote {
    border-left: 3.5pt solid #7C2E26;
    background-color: #fdf8f6;
    margin: 8px 0;
    padding: 6px 12px;
    color: #334155;
}

ul, ol {
    margin-top: 4px;
    margin-bottom: 8px;
    padding-left: 20px;
}

li {
    margin-bottom: 3px;
}

hr {
    border: none;
    border-top: 1pt solid #cbd5e1;
    margin: 14px 0;
}

.kpi-container {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 12px;
}

.kpi-card {
    background: #f8fafc;
    border: 1pt solid #cbd5e1;
    border-radius: 4px;
    padding: 8px;
    text-align: center;
    flex: 1;
}

.kpi-val {
    font-size: 14pt;
    font-weight: 800;
    color: #7C2E26;
}

.kpi-lbl {
    font-size: 7.5pt;
    text-transform: uppercase;
    color: #64748b;
    font-weight: 600;
}
"""

def convert_md_to_pdf(md_path: Path) -> Path:
    pdf_path = md_path.with_suffix(".pdf")
    print(f"Converting: {md_path.name} -> {pdf_path.name}...", flush=True)
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Convert markdown to HTML
    extensions = ["tables", "fenced_code", "attr_list", "def_list"]
    html_body = markdown.markdown(md_text, extensions=extensions)

    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{md_path.stem}</title>
<style>{CSS_STYLE}</style>
</head>
<body>
{html_body}
</body>
</html>
"""
    doc = weasyprint.HTML(string=full_html)
    doc.write_pdf(target=str(pdf_path))
    return pdf_path


def main():
    reports_dir = Path("docs/Reports")
    md_files = sorted(reports_dir.glob("*.md"))
    print(f"Found {len(md_files)} markdown reports in {reports_dir}")
    
    generated = []
    for md_file in md_files:
        try:
            pdf_path = convert_md_to_pdf(md_file)
            size_kb = pdf_path.stat().st_size / 1024
            generated.append((pdf_path, size_kb))
        except Exception as e:
            print(f"ERROR converting {md_file.name}: {e}")

    print("\n" + "=" * 70)
    print("PDF GENERATION SUMMARY")
    print("=" * 70)
    for pdf, size in generated:
        print(f"  ✓ {pdf.name:<55} ({size:.1f} KB)")
    print(f"\nSuccessfully generated {len(generated)} PDF benchmark reports.")


if __name__ == "__main__":
    main()
