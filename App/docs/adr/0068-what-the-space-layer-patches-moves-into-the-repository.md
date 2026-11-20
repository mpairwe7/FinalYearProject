# ADR-0068: What the Space layer patches moves into the repository

## Status

Accepted, 2026-11-20. Amended as each part lands; the table says which have.

## Context and Problem Statement

The Hugging Face Space builds from the production image and then runs a Python
script that edits files inside it. The script is `HF_CPU_PATCH_B64`, a
base64 string in `App/deploy/hf-space/Dockerfile`. It finds a block of source
text, replaces it, and compiles the file. Seven sections of it change the
retriever, the turn pipeline, the speech service, the PDF export, the provider
gateway and the offline index.

That code runs for every taxpayer and exists nowhere else.

- **No test runs it.** `App/tests/test_hf_space_layer.py` checks that each
  block the script replaces still exists, and loads `speech_service.py` as
  built. Nothing loads the retriever or the pipeline as built.
- **No review reads it.** A diff shows one changed 26,000-character line.
- **A move breaks it.** ADR-0067 moved `speech_service.py`, and the script
  both looked for the old path and inserted imports that no longer resolved.

A review of the decoded script (PR #793) raised eight findings on what it had
done for some time (G698). Four were confirmed against the code. Two are in the
reranker this ADR starts with.

- The script pasted a word-overlap reranker class into `rag/retriever.py` and
  replaced the score loop in `_rerank` so that this reranker's candidates got no
  `score_norm`. `demote_stale_editions` skips a candidate without one, so on
  the Space a repealed rate table was never ranked below the one in force.
- With no dense hits `_blend_cpu_lexical` does not run. The scores stayed
  unset, and `hit_relevance` squashes an unset score of 0 to 0.5. A passage
  sharing no word with the question could pass the relevance gates.

## Considered Options

1. Move each behaviour into the repository, behind a setting where it should
   not apply everywhere, with tests on the real code. Delete it from the script.
2. Fix each finding inside the script, and add a test that loads each patched
   module as built.
3. Leave the script and record the findings.

Option 2 keeps production behaviour in a string that no review reads, and every
later move or rename has to be repeated inside it. Option 3 leaves confirmed
defects in production.

## Decision Outcome

Option 1, one part at a time, each in its own pull request.

| Part | Was in the script | Now | Landed |
| --- | --- | --- | --- |
| 1 | The CPU reranker class, its installation in sparse-only mode, and a replaced score loop | `_CpuLexicalReranker` and `_sparse_only_reranker` in `rag/retriever.py`, on when `RERANK_ENABLED` and `SPARSE_CPU_RERANKER` are both true. `_rerank` scores it like any reranker, and it reads the retriever's current BM25 encoder on every call | yes |
| 2 | On the REST path, a turn with an attachment skipped every fast path | `has_readable_document(...)` and `_maybe_handle_fast_paths(beside_document=...)` in `rag/pipeline.py`, passed at all six call sites on both paths: a fast path may answer but may not ask | yes |
| 3 | The Gemini speech fallback | `_gemini_audio_transcribe` in `speech/speech_service.py`, the chain's last tier, on when `STT_FALLBACK_BACKEND=gemini`. Traced, under the cloud deadline, and a response with no content is not silence | yes |

`SPARSE_CPU_RERANKER` is off unless set, so an image that cannot load a
cross-encoder reranks only where a deployment asks for it. The Space's
Dockerfile already set it to `true`.

The Space receives its Dockerfile only from the roll, which the image build
dispatches after it has pushed the new image, and which rewrites the pin to that
image before it uploads the file. So the Space never runs a Dockerfile that has
lost a section of the script on an image that lacks the code replacing it.

## Consequences

- On the Space a stale edition is demoted by `STALE_EDITION_PENALTY`, as it is
  under the cross-encoder, and a passage with no word in common with the
  question scores 0 at the gates when the dense leg returns nothing. Both
  change which passages reach an answer.
- The reranker is ordinary code. It is reviewed in a diff, moved by a codemod
  and covered by `App/backend/tests/test_cpu_lexical_reranker.py`.
- The script no longer touches `rag/retriever.py`. If the Space is pinned back
  to an image from before this change, that image has no CPU reranker and
  serves fused order without reranking, as any torch-free image did before the
  layer existed.
- The script still patches four files: the pipeline (graph-fusion gating and
  the identifiers read from an attachment), the offline index, the PDF export
  and the gateway. They stay until someone moves them too.
- **Part 2 sets one rule for a turn that carries a document, on both paths: a
  fast path may answer but may not ask.** The owner left the choice open, to be
  made without cost to the experience or to quality.
  - A question that carries its own figures still gets the exact calculator
    answer, and a rate question the rate table's. On the web app, which uses
    the streaming path, nothing is slower or less exact than it was.
  - A question about another country's taxes is always declined. On the REST
    path it was not, because the script skipped the dispatcher that holds the
    decline.
  - The paths that ask are skipped: the calculator's request for a missing
    figure and the guided flow it opens, the individual-or-organisation
    question, and the bare-amount menu. The figure may be in the document, and
    the turn goes on to the answer that reads it. On the web app this is new;
    the REST path already behaved so.
  - The alternatives were to skip every fast path on both routes, which gives
    up an exact instant answer the question fully specifies, or to skip none,
    which brings back the request for a figure the taxpayer has just uploaded.
  - An independent run of both routes, parent against head, passed that rule and
    added three things, which are now part of it. A document with no extracted
    text counts as no document: a scan that could not be read cannot hold the
    missing figure, and skipping the question left the taxpayer with nothing to
    answer. The follow-up to a calculation no longer asks for the next figure
    beside a document. The test file creates its own database, where it had
    passed only after another test had.
  - A third case was tried and dropped. When the message seemed to point at the
    document ("the VAT rate on this invoice"), no fast path answered, so that the
    answer came from the document and not from a table. A second independent run
    measured the cost on the route the web app uses: a question that carries its
    own figures and merely names a document ("the PAYE on UGX 2,000,000, as my
    contract says", "see attached") lost its instant exact answer. Words in a
    message cannot tell those two apart reliably. So a question that points at
    the document is still answered from the table on both routes, as the web app
    always did. On the REST route that is new: the Space's script used to send
    every attachment turn to the answer that reads the document.
- **Part 3 changes three things in the speech fallback** (G698).
  - Each call is traced with `tracing.llm_call` and reports the provider's own
    token counts, as every model client must.
  - The call runs under `SPEECH_CLOUD_DEADLINE_S`. The cloud pool does not
    carry a turn's context, and the helper reads "may this turn use a cloud
    model" from it. Run in the pool bare, it would read "yes" on a call turn
    and send the recording. So the call runs inside a copy of the caller's
    context, and a test holds that.
  - A response with no candidate (a blocked prompt) or a candidate with no
    part is no answer. It was returned as an empty transcript, which the chain
    reports as "no speech detected" even with every other tier down.
  - A transcript cut off at the output limit (`finishReason` `MAX_TOKENS`) is
    no answer either. The request caps output at 1,024 tokens and a clip can
    run for minutes, so the end of what was said, where the question usually
    is, would be missing from text the caller read as complete.
- **One finding is not taken.** A candidate whose text is empty still means the
  model heard no words. That is the tested rule that keeps a mis-tap from being
  reported as a failure, and the review's suggestion would have reversed it.

## Confirmation

- `test_cpu_lexical_reranker.py` runs the real class through `_rerank`: a
  stale edition scores below the current one, a question that names the old
  year keeps that edition, and with no dense hits the gate reads 0 for a
  passage sharing no word. With the script's score loop put back, two of its
  seven tests fail.
- Before the change, the retriever loaded as the Space built it gave a stale
  edition the same score as the current one (0.275 and 0.275) and read 0.5 at
  the gate for an unrelated passage. After it, 0.125 against 0.275, and 0.0.
- `test_hf_space_layer.py` checks that the script no longer edits the
  retriever, that the class is in the repository, and that the Space's
  Dockerfile sets `SPARSE_CPU_RERANKER=true`.
- `App/backend/tests/test_gemini_audio_fallback.py` holds the speech fallback:
  the 18 tests that ran against the source as the script rewrote it now run on
  the module, with five more for part 3. Each of three breaks fails one: the
  pool without the turn's context, the call outside the deadline, and no
  content returned as silence.
- `App/backend/tests/test_attachment_turn_fast_paths.py` holds part 2: the
  decline, the exact answer and the rate answer survive an attachment; a
  missing figure and the TIN question are not asked for; no guided flow is
  opened; and every call to the dispatcher on both paths passes the flag. With
  the calculator's guard removed two tests fail, and with the flag dropped from
  one streaming call, one.
- After the merge the Space is checked on the new image: `/ready` reports the
  reranker, the build log shows the script applied, and a live answer is read.
- Part 2 was run twice by a verifier who did not write it, on both routes at parent
  and head, with each commit's layer script applied. A turn with no attachment was
  byte-identical on 132 route turns, and a document with no text behaved exactly
  as none. The second run failed the third case described above, and it was removed.
