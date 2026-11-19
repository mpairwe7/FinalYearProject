# TTS Voice Recording Protocol

## Purpose

Record 3-4 hours of clean speech per language to train VITS text-to-speech
models for the URA Tax Assistant. Target languages: Luganda, Runyankole, Acholi.

## Speaker Requirements

- Native speaker of the target language
- Clear, neutral accent (no strong regional dialect)
- Comfortable reading tax/financial terminology
- Available for 2-3 recording sessions (1.5 hours each)
- Age 20-50 (voice maturity, no age-related tremor)

## Equipment

| Item | Minimum | Recommended |
|------|---------|-------------|
| Microphone | USB condenser (e.g. Blue Yeti) | Large-diaphragm condenser (e.g. AT2020) |
| Room | Quiet room, closed windows | Treated booth or closet with blankets |
| Interface | Direct USB or 3.5mm | XLR audio interface (e.g. Focusrite Scarlett) |
| Software | Audacity | Audacity with noise profile |
| Headphones | Any closed-back | Studio monitor headphones |

## Recording Settings

| Parameter | Value |
|-----------|-------|
| Sample rate | 22050 Hz (VITS native) or 44100 Hz (downsample later) |
| Bit depth | 16-bit PCM |
| Channels | Mono |
| Format | WAV (uncompressed) |
| Noise floor | < -50 dBFS |
| Peak level | -3 to -6 dBFS (no clipping) |

## Session Structure

1. **Setup** (10 min): Mic check, noise profile, test recording
2. **Warm-up** (5 min): Read 10 familiar sentences to settle voice
3. **Recording** (70 min): Read prompts from `{lang}_prompts.txt`
   - Pause 1-2 seconds between sentences
   - Re-read any sentence with stumbles or background noise
   - Break every 20 minutes (drink water, rest voice)
4. **Wrap-up** (5 min): Review recordings, note any issues

## Prompts

Generate prompts using:
```bash
python -m ml.scripts.tts.generate_tts_prompts \
    --languages nyn ach lg \
    --output-dir Data/speech/tts/prompts
```

Each language gets 500 sentences covering:
- URA tax terminology
- Numbers, currency amounts, dates
- Ugandan place names
- Phoneme-balanced general sentences

## Output Directory Structure

```
Data/speech/tts/
├── {lang}_voice/
│   └── speaker_001/
│       ├── CONSENT.yaml          # Signed consent (see template below)
│       ├── metadata.csv          # audio_file|text|normalised_text
│       └── wavs/
│           ├── 0001.wav
│           ├── 0002.wav
│           └── ...
├── prompts/
│   ├── lg_prompts.txt
│   ├── nyn_prompts.txt
│   └── ach_prompts.txt
└── RECORDING_PROTOCOL.md         # This file
```

## CONSENT.yaml Template

```yaml
version: 1
date: "2026-MM-DD"
language: "nyn"  # or "ach", "lg"
speaker_name: "Full Name"
speaker_id: "speaker_001"
consent_type: "tts_training"
consent_text: |
  I agree that my recorded voice will be used to train a text-to-speech
  model for the URA Tax Assistant application. I understand that:
  1. My voice will be synthesised to read tax information aloud.
  2. The model will be deployed on mobile devices.
  3. My identity will not be disclosed; the model is identified by
     language only (e.g. "Runyankole voice").
  4. I may withdraw consent by contacting the project team.
signed: true
witness: "Witness Name"
```

## Quality Checks

After recording, run:
```bash
python -m ml.scripts.tts.validate_recordings \
    --data-dir Data/speech/tts/nyn_voice/speaker_001
```

This checks:
- All WAV files are mono, 16-bit, correct sample rate
- No clipping (peak < -1 dBFS)
- Noise floor < -50 dBFS
- Duration 2-15 seconds per file
- metadata.csv matches WAV file count
- CONSENT.yaml exists and is valid

## Contact for Recording Sessions

- **Luganda**: Makerere University, Department of African Languages
- **Runyankole**: Mbarara University of Science and Technology
- **Acholi**: Gulu University, Faculty of Education and Humanities
