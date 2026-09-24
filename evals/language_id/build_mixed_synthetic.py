"""Synthetic stand-in for the ``mixed`` class: Luganda carrying English tax terms.

The plan's ``mixed`` class is **self-recorded** code-switched speech ("Nsaba
okumanya ku TIN yange"), which no public corpus has. Until those recordings
exist, this renders the same kind of sentence with the Orpheus sidecar's
Luganda voices so the mixed-language rule can at least be exercised.

It is a weak proxy and is labelled ``mixed_synthetic`` everywhere: TTS
speech is cleaner than a phone call, and the Orpheus model card warns that
it has not seen code-switched text. A number from this set does not
substitute for the recorded set — see ``evals/language_id/README.md``.

    python evals/language_id/build_mixed_synthetic.py --url http://127.0.0.1:18100

Writes ``data/mixed_synthetic/…`` (16k + 8k copies) and
``manifest_mixed_synthetic.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_dataset import SR, _bucket, _phone_band, _to_mono_16k, _write_wav  # noqa: E402

SENTENCES = (
    "Nsaba okumanya ku TIN yange.",
    "VAT yange ntya okugisasula?",
    "Nnyinza ntya okufuna TIN number?",
    "PAYE ku musaala gwange ebalibwa etya?",
    "Njagala okuwandiisa business yange ku EFRIS.",
    "Return y'omusolo ngiwaayo ddi?",
    "Withholding tax eri ebitundu bimeka?",
    "Nsobola okusasula omusolo ku mobile money?",
    "Rental income tax nsasula mmeka?",
    "Customs duty ku mmotoka enkadde eri mmeka?",
    "Nkyusa ntya ownership y'emmotoka?",
    "Penalty ya late filing eri mmeka?",
    "Nnina okwewandiisa ku VAT singa turnover yange eri waggulu?",
    "E-tax portal ngiyingira ntya?",
    "Nsaba mbuulire ku presumptive tax.",
    "Company yange erina okusasula corporate tax mmeka?",
    "TIN yange nagibuza, nkole ki?",
    "Invoice ya EFRIS ngifulumya ntya?",
    "Ssente z'omusolo nzisasulira ku bank ki?",
    "Objection ku assessment ngiwaayo ntya?",
    "Digital tax stamps zikola zitya?",
    "Stamp duty ku land transfer eri mmeka?",
    "Nnyinza okufuna tax clearance certificate?",
    "Import duty ku simu eri mmeka?",
    "Small business yange esasula omusolo gwa bika ki?",
    "Njagala okwogera n'officer wa URA.",
    "Refund ya VAT ngifuna ntya?",
    "Deadline ya PAYE return eri ddi?",
    "Local excise duty ekola etya?",
    "Nsaba onnyambe ku NSSF ne PAYE.",
)
SPEAKERS = ("salt_lug_0001", "waxal_lug_0005")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--url", default="http://127.0.0.1:18100")
    ap.add_argument("--out", type=Path, default=HERE / "data")
    ap.add_argument("--manifest", type=Path, default=HERE / "manifest_mixed_synthetic.jsonl")
    args = ap.parse_args()

    rows: list[dict[str, object]] = []
    with httpx.Client(timeout=60) as client:
        for spk in SPEAKERS:
            for i, text in enumerate(SENTENCES):
                resp = client.post(f"{args.url}/v1/audio/speech",
                                   json={"input": text, "voice": spk, "response_format": "pcm", "seed": i})
                resp.raise_for_status()
                pcm = np.frombuffer(resp.content, dtype="<i2").astype(np.float32) / 32768.0
                audio = _to_mono_16k(pcm, 24000)
                for channel, clip in (("16k", audio), ("8k", _phone_band(audio))):
                    rel = Path("mixed_synthetic") / spk / f"{i:02d}_{channel}.wav"
                    _write_wav(args.out / rel, clip)
                    duration = round(len(clip) / SR, 3)
                    rows.append({
                        "path": rel.as_posix(), "label": "mixed_synthetic", "duration_s": duration,
                        "bucket": _bucket(duration), "source": f"orpheus_{spk}", "channel": channel,
                        "variant": "full", "utterance": f"{spk}_{i:02d}", "text": text,
                    })
    with args.manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} entries to {args.manifest}")


if __name__ == "__main__":
    main()
