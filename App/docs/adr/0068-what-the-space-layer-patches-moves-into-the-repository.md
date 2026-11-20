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
| 2 | Attachment turns skip the calculator fast paths | the pipeline, on both the REST and the streaming path, with the foreign-jurisdiction decline kept | no |
| 3 | The Gemini speech fallback | `speech/speech_service.py`, behind `STT_FALLBACK_BACKEND=gemini` | no |

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
- The script still patches five files. Parts 2 and 3 remove two more sections;
  the offline index, the PDF export and the gateway sections stay until someone
  moves them too.

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
- After the merge the Space is checked on the new image: `/ready` reports the
  reranker, the build log shows the script applied, and a live answer is read.
