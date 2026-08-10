# PDF Fonts

Place NotoSans TTF files here for full Unicode support in PDF exports.

Required for correct rendering of Luganda (ŋ, ɛ, ɔ), Swahili,
Runyankole, and Acholi text in conversation and tax summary PDFs.

## Download

```bash
# From Google Fonts (OFL license, free for commercial use):
curl -L -o NotoSans-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans%5Bwdth%2Cwght%5D.ttf"

# Or download the full family from:
# https://fonts.google.com/noto/specimen/Noto+Sans
```

If these files are absent, the PDF generator falls back to Helvetica
(Latin-only) and logs a warning.
