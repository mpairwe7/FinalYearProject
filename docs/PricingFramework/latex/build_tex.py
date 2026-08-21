#!/usr/bin/env python3
"""Transform the URA pricing document markdown into XeLaTeX, then a PDF.

Emits a single .tex file for tectonic/xelatex. Column widths for the 34 tables
are computed from cell content, because LaTeX will not size them the way a
browser does.
"""
import html as _html
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "URA_ASSISTANT_PRICING_DOCUMENT.md"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "ura-pricing.tex")

FONT_DIR = pathlib.Path(__file__).resolve().parent / "fonts"

# Newsreader and Libre Franklin ship from Google Fonts as VARIABLE fonts only,
# which XeTeX cannot instance itself; static cuts are generated here once.
VARIABLE = [
    ("newsreader",    "Newsreader%5Bopsz,wght%5D.ttf",        "Newsreader",    {"opsz": 16}, [400, 500, 600]),
    ("newsreader",    "Newsreader-Italic%5Bopsz,wght%5D.ttf", "Newsreader-It", {"opsz": 16}, [400]),
    ("librefranklin", "LibreFranklin%5Bwght%5D.ttf",          "LibreFranklin", {},           [500, 600, 700, 800]),
]
STATIC = [("ibmplexmono", f"IBMPlexMono-{s}.ttf") for s in ("Regular", "Medium", "SemiBold")]


def fetch_fonts() -> None:
    """Download and instance the three families. Idempotent; skips what exists."""
    import io
    import urllib.request
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl"
    hdr = {"User-Agent": "curl/8"}
    FONT_DIR.mkdir(exist_ok=True)

    def get(url):
        return urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60).read()

    for fam, fname, out, pins, weights in VARIABLE:
        missing = [w for w in weights if not (FONT_DIR / f"{out}-{w}.ttf").exists()]
        if not missing:
            continue
        raw = get(f"{RAW}/{fam}/{fname}")
        for w in missing:
            f = TTFont(io.BytesIO(raw))
            instancer.instantiateVariableFont(f, {**pins, "wght": w},
                                              inplace=True, updateFontNames=True)
            f.save(FONT_DIR / f"{out}-{w}.ttf")
            print(f"  instanced {out}-{w}.ttf")
    for fam, fname in STATIC:
        p = FONT_DIR / fname
        if not p.exists():
            p.write_bytes(get(f"{RAW}/{fam}/{fname}"))
            print(f"  fetched {fname}")


# --------------------------------------------------------------------- inline
SPECIALS = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
    "#": r"\#", "_": r"\_", "%": r"\%", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    # Four glyphs the text faces do not carry. All appear in formula contexts,
    # so math mode is the correct home for them rather than a font fallback.
    "\u03a3": r"\ensuremath{\Sigma}",
    "\u220f": r"\ensuremath{\prod}",
    "\u2192": r"\ensuremath{\rightarrow}",
    "\u2080": r"\textsubscript{0}",
}


def esc(t: str) -> str:
    return "".join(SPECIALS.get(c, c) for c in t)


def breakable(t: str) -> str:
    """Let long paths wrap.

    A file path is one unbreakable word to TeX, so a cell or line holding
    ``docs/Reports/MCP_CONVERSATIONAL_PAINPOINTS_ACCURACY_REPORT_2026-08-21.md``
    overflows its column by ~25 mm. Permit a break after each separator; the
    argument is already LaTeX-escaped, so match the escaped underscore.
    """
    return re.sub(r"(/|\\_|-|\.)", r"\1\\allowbreak{}", t)


def inline(t: str) -> str:
    """Markdown inline → LaTeX. Code spans are stashed so they escape once."""
    stash: list[str] = []

    def keep(m):
        stash.append(m.group(1))
        return f"\x00{len(stash) - 1}\x00"

    t = re.sub(r"`([^`]+)`", keep, t)
    t = _html.unescape(t)
    t = esc(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"\\emph{\1}", t)
    t = re.sub(r"\x00(\d+)\x00",
               lambda m: r"\code{" + breakable(esc(stash[int(m.group(1))])) + "}", t)

    # Provenance markers become real typographic objects, as on the web page.
    t = re.sub(r"\\textbf\{\[([MEP])\]\}", lambda m: rf"\mk{m.group(1)}{{}}", t)
    t = re.sub(r"(?<=[\s(])\[([MEP])\]", lambda m: rf"\mk{m.group(1)}{{}}", t)

    # SWEBOK references and this document's own section references.
    t = re.sub(r"§(?:15|1|7)\.[\d.]+(?:–[\d.]+)?|§15(?![\d.])",
               lambda m: r"\swref{" + m.group(0) + "}", t)
    t = re.sub(r"§[A-G]\d(?:–§?[A-G]?\d)?",
               lambda m: r"\docref{" + m.group(0) + "}", t)

    # Straight quotes read as typewriter output in a serif face. Convert pairs
    # so the opening one actually opens; a stray unpaired quote closes.
    t = re.sub(r'"([^"]*)"', r"``\1''", t)
    t = t.replace('"', "''")
    return t


# ---------------------------------------------------------------------- tables
_FONTS: dict = {}
PT_MM = 25.4 / 72.27


def text_mm(text: str, font: str, pt: float) -> float:
    """Advance width of *text* in millimetres, from the real font metrics.

    Character counts are a poor proxy: "CONCURRENCY" and "1,000 users" are the
    same length and nowhere near the same width. Measuring is what stops a
    column being sized too narrow for its own header.
    """
    from fontTools.ttLib import TTFont
    f = _FONTS.get(font)
    if f is None:
        ttf = TTFont(str(FONT_DIR / f"{font}.ttf"))
        f = _FONTS[font] = (ttf.getBestCmap(), ttf["hmtx"].metrics,
                            ttf["head"].unitsPerEm, ttf.getGlyphOrder())
    cmap, hmtx, upm, order = f
    units = 0
    for ch in text:
        g = cmap.get(ord(ch))
        units += hmtx[g][0] if g and g in hmtx else upm * 0.5
    return units / upm * pt * PT_MM


def longest_word_mm(text: str, font: str, pt: float) -> float:
    words = re.sub(r"[*`]", "", text).split()
    return max((text_mm(w, font, pt) for w in words), default=0.0)


def col_spec(rows: list[list[str]], align: list[str], total_mm: float,
             body_pt: float) -> str:
    """p{} widths measured from font metrics, then fitted to the text block."""
    HDR_FONT, HDR_PT = "LibreFranklin-700", 6.4
    BODY_FONT = "Newsreader-400"

    ncol = len(rows[0])
    header, body = rows[0], (rows[1:] or rows)

    # tabcolsep is applied on both sides of every column.
    usable = max(total_mm - 2.6 * ncol, 40.0)
    hard_cap = usable * 0.42          # no single column may dominate the table

    weights, floors = [], []
    for j in range(ncol):
        cells = [re.sub(r"[*`]", "", r[j]) for r in body if j < len(r)]
        widths_mm = [text_mm(c, BODY_FONT, body_pt) for c in cells] or [6.0]
        mean = sum(widths_mm) / len(widths_mm)
        longest = max(widths_mm)
        weights.append(max(mean * 0.75 + longest * 0.25, 3.0))

        # A column must be at least as wide as the longest unbreakable run it
        # has to hold — in its header (uppercase, smaller, sans) or its body.
        hdr_word = longest_word_mm(header[j].upper(), HDR_FONT, HDR_PT) if j < len(header) else 0.0
        body_word = longest_word_mm(" ".join(cells), BODY_FONT, body_pt)
        floors.append(min(max(hdr_word, body_word, 4.0) + 0.6, hard_cap))

    if sum(floors) > usable:          # pathologically wide table — scale floors
        floors = [f / sum(floors) * usable for f in floors]

    # Allocate proportionally, then raise any column that is under its floor and
    # freeze it, letting the rest share what remains. Clamping in one pass is
    # what pushed several tables past the text block.
    widths = [w / sum(weights) * usable for w in weights]
    for _ in range(ncol + 1):
        under = [j for j in range(ncol) if widths[j] < floors[j] - 1e-6]
        if not under:
            break
        free = usable - sum(floors[j] for j in under)
        wsum = sum(weights[j] for j in range(ncol) if j not in under) or 1.0
        for j in range(ncol):
            widths[j] = floors[j] if j in under else max(
                weights[j] / wsum * free, floors[j])
    widths = [mm / sum(widths) * usable for mm in widths]

    parts = []
    for j, mm in enumerate(widths):
        a = align[j] if j < len(align) else "l"
        cell = {"r": r"\RaggedLeft\arraybackslash", "c": r"\Centering\arraybackslash"}.get(
            a, r"\RaggedRight\arraybackslash")
        parts.append(f">{{{cell}}}p{{{mm:.2f}mm}}")
    return "".join(parts)


def table(block: list[str]) -> str:
    rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in block]
    align: list[str] = []
    start = 1
    if len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in rows[1]):
        for c in rows[1]:
            align.append("r" if c.endswith(":") and not c.startswith(":")
                         else "c" if c.startswith(":") and c.endswith(":") else "l")
        start = 2

    ncol = len(rows[0])
    wide = ncol >= 9
    width_mm = 247.0 if wide else 150.0
    body_pt = 6.6 if wide or ncol >= 7 else (7.7 if ncol >= 5 else 8.2)
    size = r"\tblwide" if wide or ncol >= 7 else (r"\tblmid" if ncol >= 5 else r"\tbl")
    spec = col_spec([rows[0]] + rows[start:], align, width_mm, body_pt)

    def line(cells, bold=False):
        out = []
        for j in range(ncol):
            raw = cells[j] if j < len(cells) else ""
            # Header case is set here, not with \MakeUppercase: that macro also
            # uppercases the colour-name arguments inside \mkM and friends.
            c = inline(raw.upper() if bold else raw)
            out.append(rf"\tblhead{{{c}}}" if bold else c)
        return " & ".join(out) + r" \\"

    body = "\n".join(line(r) for r in rows[start:] if any(x for x in r))
    head = line(rows[0], bold=True)
    tex = "\n".join([
        r"\needspace{18mm}",
        rf"\begingroup{size}",
        rf"\begin{{longtable}}{{{spec}}}",
        r"\toprule", head, r"\midrule\endfirsthead",
        r"\toprule", head, r"\midrule\endhead",
        r"\bottomrule\endfoot", r"\bottomrule\endlastfoot",
        body,
        r"\end{longtable}\endgroup",
    ])
    if wide:
        tex = "\\begin{widetable}\n" + tex + "\n\\end{widetable}"
    return tex


# ---------------------------------------------------------------------- blocks
def convert(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    while i < n:
        ln = lines[i]

        if ln.strip() == "---":
            # A rule immediately before a Part head is redundant: \PartHead
            # issues its own \clearpage, so the rule only dangles at the foot
            # of the previous page and pushes it shorter. Nine of the ten rules
            # in this document are of that kind.
            nxt = next((x for x in lines[i + 1:i + 4] if x.strip()), "")
            if not nxt.startswith("## "):
                out.append(r"\rail")
            i += 1
            continue

        if ln.startswith("|"):
            block = []
            while i < n and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(table(block))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl, txt = len(m.group(1)), m.group(2).strip()
            i += 1
            if lvl == 1:
                continue                      # the cover page carries the title
            gut = ""
            gm = re.search(r"\s*\((SWEBOK\s+§[^)]+|§[^)]+)\)\s*$", txt)
            if gm:
                gut = gm.group(1).replace("SWEBOK ", "")
                txt = txt[: gm.start()].strip()
            label = ""
            lm = re.match(r"^([A-G]\d|Part\s+[A-G])\.?\s*[—.]?\s*(.*)$", txt)
            if lm and lm.group(2):
                label, txt = lm.group(1), lm.group(2).strip(" —")
            g = inline(gut) if gut else ""
            if lvl == 2:
                out.append(rf"\PartHead{{{esc(label)}}}{{{inline(txt)}}}{{{g}}}")
            elif lvl == 3:
                out.append(rf"\SecHead{{{esc(label)}}}{{{inline(txt)}}}{{{g}}}")
            else:
                out.append(rf"\SubHead{{{inline(txt)}}}")
            continue

        if ln.startswith("> "):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip())
                i += 1
            if any(x.startswith("#") for x in buf):
                k = v = note = ""
                for x in buf:
                    if x.startswith("### "):
                        k = inline(x[4:])
                    elif x.startswith("## "):
                        v = inline(x[3:])
                    elif x:
                        note = inline(x)
                out.append(rf"\pricebox{{{k}}}{{{v}}}{{{note}}}")
            else:
                out.append(rf"\pullquote{{{inline(' '.join(buf))}}}")
            continue

        if re.match(r"^(-|\d+\.)\s+", ln):
            ordered = bool(re.match(r"^\d+\.\s+", ln))
            items: list[str] = []
            while i < n and (re.match(r"^(-|\d+\.)\s+", lines[i])
                             or (lines[i].startswith("  ") and lines[i].strip() and items)):
                if re.match(r"^(-|\d+\.)\s+", lines[i]):
                    items.append(re.sub(r"^(-|\d+\.)\s+", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            env = "enumerate" if ordered else "itemize"
            out.append(rf"\begin{{{env}}}[leftmargin=5mm,itemsep=1.6mm,topsep=1.6mm,parsep=0pt]")
            out += [rf"  \item {inline(x)}" for x in items]
            out.append(rf"\end{{{env}}}")
            continue

        if ln.strip():
            buf = []
            while (i < n and lines[i].strip()
                   and not lines[i].startswith(("|", "#", "-", ">"))
                   and lines[i].strip() != "---"
                   and not re.match(r"^\d+\.\s", lines[i])):
                buf.append(lines[i].strip())
                i += 1
            out.append(inline(" ".join(buf)) + "\n")
            continue
        i += 1

    return "\n".join(out)


fetch_fonts()
body = convert(SRC.read_text(encoding="utf-8"))
preamble = (HERE / "preamble.tex").read_text(encoding="utf-8")
OUT.write_text(preamble.replace("%%BODY%%", body), encoding="utf-8")
print(f"wrote {OUT} — {len(body.splitlines())} body lines, "
      f"{body.count('longtable') // 2} tables, {body.count('begin{widetable}')} landscape")
