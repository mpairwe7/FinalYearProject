"""Phase 0B — is Orpheus-3B fast enough (and good enough) to be the Luganda voice?

Streams receptionist sentences through the Orpheus sidecar
(``App/backend/orpheus_sidecar``) and measures, per request:

* **TTFA** — time to the first byte of audio (what the caller waits for),
* **total** — time to the last byte, and the audio's own length (RTF).

It also writes every clip under ``evals/orpheus_tts/samples/`` with a *blind*
id and a rating sheet (``listening_sheet.csv``) for the native-speaker
listening test; the id → speaker key goes to a separate file so raters cannot
see which voice they are scoring. Latency is measurable here; naturalness is
not — that half of the gate needs people.

Stdlib only (the sidecar does the heavy lifting), so it runs from any host:

    python3 scripts/bench_orpheus_tts.py --url http://127.0.0.1:18100

The sentences are drafts written for timing, spread over short / medium /
long. They have not been reviewed by a Luganda speaker — raters should score
the *voice*, and flag wording separately in the sheet's notes column.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import http.client
import json
import random
import statistics
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 24000

SENTENCES: dict[str, list[str]] = {
    "short": [
        "Weebale kukuba ku URA.",
        "Lindako katono.",
        "Nkebera kati.",
        "Oyagala kumanya ki?",
        "Kale, nkutegedde.",
        "Nsonyiwa, ddamu nate.",
        "Mbuulira TIN yo.",
        "Tukwataganya n'omukozi.",
        "Kiki ekirala?",
        "Yee, kituufu.",
    ],
    "medium": [
        "Okwewandiisa ku TIN tekusasulwa, era osobola okukikola ku mukutu gwa URA.",
        "Omusolo gwa VAT guli ebitundu kkumi na munaana ku buli kikumi.",
        "Nsaba ondinde katono nga nkebera amawulire gano.",
        "Alipoota y'omusolo gw'enfuna eweebwayo ng'omwaka gw'ebyensimbi guwedde.",
        "Omukozi wa URA ajja kukukubira essimu mu bbanga ttono.",
        "Nnyinza okukuyamba ku by'okwewandiisa, okusasula, n'eby'oku mwalo.",
        "Namba yo ey'okujuliza eri ku ssimu yo, gikuume bulungi.",
        "Tosobola kusasula ssente mu ngalo; kozesa banka oba essimu.",
        "Kino kikwatagana n'omusolo gwa PAYE ku musaala gw'abakozi.",
        "Nsonyiwa, sikuwulidde bulungi, oyinza okuddamu ekibuuzo kyo?",
    ],
    "long": [
        "Okwewandiisa ku TIN, genda ku mukutu gwa URA, jjuzaamu foomu, olwo ofune namba yo mu nnaku ntono nga tosasudde kintu kyonna.",
        "Bw'oba ng'ofuna enfuna okuva mu nnyumba z'opangisa, olina okuwaayo alipoota y'omusolo gw'ennyumba ezipangisibwa buli mwaka.",
        "Abakozesa bonna balina okuggyako omusolo gwa PAYE ku misaala gy'abakozi baabwe ne bagusasula eri URA buli mwezi.",
        "Singa olemwa okusasula omusolo ku budde, URA eyinza okukusalira ekibonerezo, n'olwekyo kirungi okusasula mangu.",
        "Okukyusa obwannannyini bw'emmotoka, omuguzi n'omutunzi balina okujja ne TIN zaabwe n'ebiwandiiko by'emmotoka.",
        "EFRIS nkola ya URA ey'okufulumya risiiti ku kompyuta, era buli muntu awandiisiddwa ku VAT alina okugikozesa.",
        "Bw'oba toli mumativu n'omusolo gwe bakusalidde, osobola okuwakanya mu bbanga ery'ennaku amakumi asatu.",
        "Nkukwataganya n'omukozi wa URA kati, nsaba olinde ku ssimu, omukozi ajja kufuna ebikwata ku kibuuzo kyo byonna.",
        "Abasuubuzi abatono basobola okusasula omusolo omukalu buli mwaka singa enfuna yaabwe teri waggulu nnyo.",
        "Okufuna ebisingawo, kuba ku nnamba ey'obwereere, oba genda ku mukutu gwa URA ku yintaneeti.",
    ],
}
DEFAULT_SPEAKERS = ("salt_lug_0001", "waxal_lug_0003", "waxal_lug_0005", "waxal_lug_0007")


def _stream(url: str, text: str, voice: str, seed: int) -> tuple[bytes, float, float]:
    """POST one sentence; return (pcm, ttfa_ms, total_ms)."""
    parts = urlparse(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=120)
    body = json.dumps({"input": text, "voice": voice, "response_format": "pcm", "seed": seed})
    t0 = time.perf_counter()
    conn.request("POST", "/v1/audio/speech", body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"{voice}: HTTP {resp.status} {resp.read()[:200]!r}")
    chunks: list[bytes] = []
    ttfa = None
    while True:
        chunk = resp.read1(8192) if hasattr(resp, "read1") else resp.read(8192)
        if not chunk:
            break
        if ttfa is None:
            ttfa = (time.perf_counter() - t0) * 1000
        chunks.append(chunk)
    total = (time.perf_counter() - t0) * 1000
    conn.close()
    return b"".join(chunks), round(ttfa or total, 1), round(total, 1)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * q / 100
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--url", default="http://127.0.0.1:18100")
    ap.add_argument("--speakers", default=",".join(DEFAULT_SPEAKERS))
    ap.add_argument("--concurrency", type=int, default=1, help="parallel requests (1 = one caller)")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "orpheus_tts")
    ap.add_argument("--reports", type=Path, default=ROOT / "evals" / "reports")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    speakers = [s.strip() for s in args.speakers.split(",") if s.strip()]

    jobs = [(length, i, text, spk) for spk in speakers for length, texts in SENTENCES.items()
            for i, text in enumerate(texts)]
    results: list[dict[str, object]] = []
    samples = args.out / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    blind_ids = rng.sample(range(10000, 99999), len(jobs))

    def run(job: tuple[str, int, str, str], blind: int) -> dict[str, object]:
        length, i, text, spk = job
        pcm, ttfa, total = _stream(args.url, text, spk, args.seed)
        audio_s = len(pcm) / 2 / SAMPLE_RATE
        path = samples / f"{blind}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return {"blind_id": blind, "speaker": spk, "length": length, "index": i, "text": text,
                "ttfa_ms": ttfa, "total_ms": total, "audio_s": round(audio_s, 2),
                "rtf": round(total / 1000 / max(audio_s, 0.01), 3)}

    _stream(args.url, "Oli otya.", speakers[0], args.seed)  # warm connection + CUDA graphs
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run, job, blind) for job, blind in zip(jobs, blind_ids)]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
            print(f"{len(results)}/{len(jobs)}", end="\r", flush=True)

    def summary(rows: list[dict[str, object]]) -> dict[str, float]:
        ttfa = [float(r["ttfa_ms"]) for r in rows]
        total = [float(r["total_ms"]) for r in rows]
        rtf = [float(r["rtf"]) for r in rows]
        return {"n": len(rows), "ttfa_ms_p50": _pct(ttfa, 50), "ttfa_ms_p95": _pct(ttfa, 95),
                "total_ms_p50": _pct(total, 50), "total_ms_p95": _pct(total, 95),
                "rtf_p50": round(statistics.median(rtf), 3) if rtf else 0.0}

    by_speaker = {spk: summary([r for r in results if r["speaker"] == spk]) for spk in speakers}
    by_length = {ln: summary([r for r in results if r["length"] == ln]) for ln in SENTENCES}
    overall = summary(results)
    ttfa_p50 = overall["ttfa_ms_p50"]
    latency_verdict = "GO" if ttfa_p50 <= 800 else ("GO_WITH_PRERENDER" if ttfa_p50 <= 2000 else "FALLBACK_SPARK")
    report = {
        "date": dt.date.today().isoformat(),
        "url": args.url,
        "concurrency": args.concurrency,
        "overall": overall,
        "by_speaker": by_speaker,
        "by_length": by_length,
        "latency_verdict": latency_verdict,
        "naturalness": "pending — native-speaker ratings in evals/orpheus_tts/listening_sheet.csv",
        # No blind ids here: this file names the speaker, so carrying the id
        # would unblind anyone who opens it next to the rating sheet.
        "requests": [
            {k: v for k, v in r.items() if k != "blind_id"}
            for r in sorted(results, key=lambda r: (str(r["speaker"]), str(r["length"]), int(r["index"])))
        ],
    }
    args.reports.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.concurrency == 1 else f"_c{args.concurrency}"
    (args.reports / f"orpheus_tts_{report['date']}{suffix}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))

    with (args.out / "listening_sheet.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["blind_id", "file", "text", "naturalness_1_5", "pronunciation_1_5", "rater", "notes"])
        for r in sorted(results, key=lambda r: int(r["blind_id"])):
            w.writerow([r["blind_id"], f"samples/{r['blind_id']}.wav", r["text"], "", "", "", ""])
    (args.out / "speaker_key.json").write_text(json.dumps(
        {str(r["blind_id"]): r["speaker"] for r in results}, indent=1))

    print()
    print(json.dumps({"overall": overall, "by_length": by_length, "by_speaker": by_speaker,
                      "latency_verdict": latency_verdict}, indent=1))


if __name__ == "__main__":
    main()
