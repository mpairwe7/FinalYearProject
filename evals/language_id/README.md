# Spoken language identification set (receptionist Phase 0A)

Can the receptionist tell English, Luganda and Swahili apart by ear, fast enough
to answer the first question in the caller's own language? This set measures it.

| Label | Source (test splits only) | Utterances |
|---|---|---|
| `en` | AfriSpeech-200 `luganda` accent (English read by Luganda speakers) | 40 |
| `en` | AfriSpeech-200 `swahili` accent | 20 |
| `en` | Sunbird SALT `multispeaker-eng` (Ugandan English) | 40 |
| `lg` | Google FLEURS `lg_ug` | 40 |
| `lg` | Sunbird SALT `multispeaker-lug` | 40 |
| `sw` | Google FLEURS `sw_ke` | 80 |
| `mixed_synthetic` | Orpheus-rendered Luganda with English tax terms (proxy — see below) | 60 |

Each utterance becomes a 1.2 s crop, a 3.0 s crop and the full clip, at 16 kHz and
band-limited to 8 kHz (a phone line): 1,518 clips plus 120 mixed.

```bash
# Build (needs HF_TOKEN / a cached HF login with access to Sunbird/salt)
python evals/language_id/build_dataset.py            # → data/ (git-ignored) + manifest.jsonl
python evals/language_id/build_mixed_synthetic.py --url http://127.0.0.1:18100   # Orpheus sidecar up

# Score, inside app-api:gpu on a free GPU (see docs/runbooks/voice-receptionist-demo.md)
python scripts/eval_language_id.py --methods A,B,C,D,E
python scripts/eval_language_id.py --manifest evals/language_id/manifest_mixed_synthetic.jsonl --suffix _mixed
```

Reports land in `evals/reports/language_id_<date>{,_mixed}.{json,md}`; every clip's
prediction goes beside them in `…_clips.json`, which is git-ignored (~1 MB a run).
In the manifest a clip's reference transcript (`text`) is on its `full` rows only;
crops share the `utterance` id.

**Not redistributed:** the audio. AfriSpeech is CC-BY-NC-SA; the builder downloads
it. The manifests are committed and regenerate identically (seeded selection).

**Still missing — needs people, not code:**

- **Recorded code-switched speech.** `mixed_synthetic` is TTS reading
  code-switched text; Orpheus has not been trained on code-switching and TTS is
  cleaner than a phone. Record ~30 real clips ("Nsaba okumanya ku TIN yange",
  "VAT yange ntya okugisasula") and add them as `mixed`.
- **Clips through the real call path** (browser → `WS /v1/calls/stream`, echo
  cancellation on), ~20 per class.
