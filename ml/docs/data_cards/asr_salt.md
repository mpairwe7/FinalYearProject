# Data Card: Makerere SALT-ASR (Luganda)

## Provenance

- **Source**: Makerere AI Lab — Speech And Language Technologies (SALT)
- **License**: **CC-BY-4.0** (attribution required — cite in model card)
- **Status**: optional augmentation dataset; not bundled by default.

## Content

- Curated Luganda speech + transcripts from the SALT project.
- Typical split: `train.tsv` / `dev.tsv` / `test.tsv`.
- Sample rate varies; loader resamples to 16 kHz.

## Collection

- Recruited native speakers; read speech from curated prompts.
- Cleaner acoustic conditions than Common Voice (studio / controlled
  environment for most recordings).

## Preprocessing

Handled by `ml/scripts/data_aug/asr_loaders.py::load_salt_asr`:

- Parses `train.tsv` / `dev.tsv` / `test.tsv`.
- Emits `AudioExample` rows with `license=cc_by`.

## In-project usage

- **Training**: optional addition to Common Voice lg.
- **Evaluation**: optional (preserves a held-out SALT subset).
- **Bundled in mobile**: no.

## Privacy / PII

- Speakers consented to the SALT collection protocol.
- Transcripts are vetted for PII by the original dataset authors.

## Attribution

When used, any model card derived from this dataset must include:

> Training data includes the Makerere SALT-ASR Luganda corpus
> (CC-BY-4.0). See https://github.com/SunbirdAI/salt.
