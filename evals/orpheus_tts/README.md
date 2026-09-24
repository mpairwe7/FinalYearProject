# Orpheus-3B Luganda voice — latency benchmark and listening test (Phase 0B)

`scripts/bench_orpheus_tts.py` streams 30 Luganda receptionist sentences (short,
medium, long) × 4 Luganda speakers through the Orpheus sidecar
(`App/backend/orpheus_sidecar`) and records time to first audio (TTFA), total time
and real-time factor. Reports: `evals/reports/orpheus_tts_<date>_*.json`.

| Run | TTFA p50 | TTFA p95 | RTF p50 |
|---|---|---|---|
| bf16, one caller | 355 ms | 364 ms | 0.96 |
| **FP8, one caller** | **234 ms** | **239 ms** | **0.63** |
| FP8, two callers at once | 265 ms | 273 ms | 0.69 |

One RTX A6000, 2026-09-24. RTF under 1 is what lets audio stream without gaps;
bf16 leaves no margin, so the compose default is `ORPHEUS_QUANTIZATION=fp8`.

## Listening test — for people

`samples/` (git-ignored; regenerate with the bench script) holds every clip under a
blind five-digit id, and `listening_sheet.csv` lists them with the sentence. Give
3–5 Luganda speakers the folder and the sheet — **not** `speaker_key.json`, which
maps ids to voices and is git-ignored for that reason. Each rates
`naturalness_1_5` and `pronunciation_1_5` and notes wording problems separately
(the sentences are drafts). Gate: mean naturalness ≥ 3.5, then pick the speaker
with the best mean for `ORPHEUS_TTS_SPEAKER_LG`.
