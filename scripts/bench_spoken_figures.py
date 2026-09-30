"""Are figures heard as spoken? Percentages and amounts, through the voice and back.

For each figure, in Luganda and Swahili, builds the sentence the read-aloud
would speak (``clean_text_for_speech``), voices it through ``/v1/tts``
(Orpheus on the GPU stack), transcribes the audio with ``/v1/asr``
(Whisper-SALT), and checks that the figure survived: as digits, as the words
that were spoken, or as English words. Orpheus said "ebitundu 18" as
"ebitundu e tini" and dropped the ten of "obukadde 10"; this is the check
that caught it.

Run inside the api image, on the stack's network (it imports the app):

    docker run --rm --network app_app-network --entrypoint /opt/venv/bin/python \\
        -e PYTHONPATH=/app -v "$PWD:/repo" app-api:gpu /repo/scripts/bench_spoken_figures.py

(Add ``-v "$PWD/App/backend/app:/app/app:ro"`` to measure the checkout's
normaliser rather than the image's.)

Writes ``evals/reports/spoken_figures_<date>.json`` under the mounted repo.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
from pathlib import Path

import httpx
from app.number_words import en_words
from app.speech_normalization import clean_text_for_speech

PERCENTS = (0, 10, 12, 15, 18, 20, 25, 30, 40)
AMOUNTS = ("5,000", "20,000", "335,000", "410,000", "1,000,000", "10,000,000", "150,000,000")
FRAMES = {
    "lg": ("Omusolo guli {}%.", "Omusaala gwa UGX {}."),
    "sw": ("Kodi ni {}%.", "Mshahara wa UGX {}."),
}
# Words of the sentence around the figure, so what is left is the figure.
FRAME_WORDS = {"omusolo", "guli", "ebitundu", "ku", "buli", "kikumi", "kodi", "ni", "asilimia",
               "omusaala", "gwa", "shilingi", "mshahara", "wa", "za", "uganda"}


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower().replace("-", " ")).split()


def _recovered(heard: str, figure: str, spoken: str) -> bool:
    if re.search(rf"(?<!\d){figure}(?!\d)", heard.replace(",", "")):
        return True
    heard_tokens = _tokens(heard)
    said = [w for w in _tokens(spoken) if w not in FRAME_WORDS]
    for reference in (said, _tokens(en_words(int(figure)))):
        if reference and sum(w in heard_tokens for w in reference) / len(reference) >= 0.75:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default="http://ura-app-api:8000")
    ap.add_argument("--out-dir", default="/repo/evals/reports")
    args = ap.parse_args()

    rows = []
    with httpx.Client(timeout=120) as client:
        for lang, (pct_frame, amount_frame) in FRAMES.items():
            cases = [(pct_frame.format(p), str(p)) for p in PERCENTS]
            cases += [(amount_frame.format(a), a.replace(",", "")) for a in AMOUNTS]
            for raw, figure in cases:
                spoken = clean_text_for_speech(raw, locale=lang)
                # A rate-limited or failed call stops the run: counted as "not
                # heard", it would quietly lower the published result.
                resp = client.post(f"{args.api}/v1/tts", json={"text": spoken, "language": lang})
                resp.raise_for_status()
                tts = resp.json()
                audio = base64.b64decode(tts.get("audio_base64") or "")
                if not audio:
                    raise SystemExit(f"/v1/tts gave no audio for {spoken!r}: {tts.get('error')}")
                resp = client.post(f"{args.api}/v1/asr", params={"language": lang}, content=audio,
                                   headers={"Content-Type": "audio/wav", "X-Voice-Consent": "true"})
                resp.raise_for_status()
                heard = resp.json().get("text", "")
                rows.append({"lang": lang, "raw": raw, "spoken": spoken, "heard": heard,
                             "backend": tts.get("backend"), "tts_error": tts.get("error"),
                             "recovered": _recovered(heard, figure, spoken)})
                print(json.dumps(rows[-1], ensure_ascii=False), flush=True)

    summary = {}
    for lang in FRAMES:
        for kind in ("percent", "amount"):
            sel = [r for r in rows if r["lang"] == lang and ("%" in r["raw"]) == (kind == "percent")]
            summary[f"{lang}/{kind}"] = f"{sum(r['recovered'] for r in sel)}/{len(sel)}"
    report = {"date": dt.date.today().isoformat(), "api": args.api, "summary": summary, "rows": rows}
    out = Path(args.out_dir) / f"spoken_figures_{report['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary), f"wrote {out}")


if __name__ == "__main__":
    main()
