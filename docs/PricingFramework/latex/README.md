# LaTeX build for the pricing document

Transforms `../URA_ASSISTANT_PRICING_DOCUMENT.md` into a print-native PDF.
The markdown is the single source of truth — edit that, never the generated
`.tex`.

```bash
python3 build_tex.py URA_ASSISTANT_PRICING_DOCUMENT.tex
tectonic -X compile URA_ASSISTANT_PRICING_DOCUMENT.tex --outfmt pdf
mv URA_ASSISTANT_PRICING_DOCUMENT.pdf ../
```

`build_tex.py` fetches and instances its own fonts on first run (Newsreader and
Libre Franklin ship as variable fonts, which XeTeX cannot instance itself), so
the only prerequisites are `fonttools` and `tectonic`. Tectonic downloads the
LaTeX packages it needs, so no full TeX Live install is required.

## What the build does that a generic converter does not

- **Column widths are measured, not guessed.** `col_spec` computes each column's
  width from the real font metrics of its content and its header, then fits the
  set to the text block. Sizing by character count left 95 overfull boxes; this
  leaves one, at 1.5 pt.
- **The left margin carries the SWEBOK topic reference** for each section,
  pulled out of the heading text, the way a statute carries marginal notes.
- **Nine-column tables move to landscape pages** with a page style of their own,
  because a rotated page rotates the running head into the sheet edge.
- **Long file paths are made breakable**, so a cell holding
  `docs/Reports/..._REPORT_2026-08-21.md` wraps instead of overflowing by 25 mm.
- **Four glyphs the text faces lack** (Σ ∏ → ₀) are routed to math mode rather
  than silently dropped.
