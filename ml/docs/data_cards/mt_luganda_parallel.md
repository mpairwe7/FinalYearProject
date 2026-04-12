# Data Card: URA Luganda Parallel Corpus

## Provenance

- **Source**: Project-internal curation under `Data/TTT/`.
- **License**: **project-internal / proprietary**
- **Files**:
  - `eng.lug.txt` — tab-separated English/Luganda (mojibake-repaired)
  - `Luganda.csv` — CSV with English / Luganda columns
  - `Luganda_Agriculture-specific_dataset-1.csv`
  - `luganda_wiki_corpus.txt` — monolingual Luganda (Wikipedia-derived)
  - `WordProject_ Luganda_English_Corpus - verses.txt`
  - `makerere_luganda_monolingual_corpus.csv`
  - `Makerere_Sentiment_corpus_Luganda_and_Kiswahil_translations.csv`
  - `Multilingual Parallel Corpus.xlsx`

## Content

Parallel English <-> Luganda sentence pairs spanning general domain,
agriculture, and religious text. Monolingual Luganda Wikipedia text is
used for backtranslation augmentation.

## Collection

- Existing project data inherited from the current data augmentation
  pipeline (`ml/scripts/data_aug/loaders.py::load_luganda_data`).
- Original authors / licenses of the source corpora vary; the
  combined set is treated as project-internal until licences are
  individually confirmed.

## Preprocessing

Handled by `ml/scripts/data_aug/mt_loaders.py`:

- Unicode NFKC + ftfy mojibake repair.
- PII redaction via the shared `text_utils.redact_pii` pipeline.
- Deduplication by `content_hash(source_text, target_text)`.
- Stratified train/val/test split by doc_id.

## Known biases

- Religious text (WordProject) introduces archaic register.
- Wikipedia dominates monolingual side; domain mismatch with URA tax
  vocabulary.
- Agriculture-specific dataset introduces domain terms that may not
  generalise.

## In-project usage

- **Training** (MT): `ml/scripts/mt/finetune_mt.py`
- **Training** (LLM legacy): `ml/scripts/data_aug/loaders.py::load_luganda_data`
  (wrapped as instruction data — preserved for backwards compatibility).
- **Backtranslation**: `ml/scripts/mt/backtranslate.py` uses the
  monolingual Luganda text.

## Privacy / PII

- PII redacted at load time (TIN, NIN, phone, email — see
  `text_utils.redact_pii`).
- Any remaining PII in the source files is a bug — report via
  `SECURITY.md`.

## License attribution

**TODO**: Each source file in `Data/TTT/` should be tagged with its
upstream licence. Until that audit is done, treat the combined corpus
as project-internal and use it only for derivative models that will
not be redistributed without a fresh licence review.
