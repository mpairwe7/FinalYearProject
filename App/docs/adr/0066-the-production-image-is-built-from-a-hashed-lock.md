# ADR-0066: The production image is built from a hashed lock, and CI tests what it ships

## Status

Accepted, 2026-11-19.

## Context and Problem Statement

The image the Hugging Face Space runs (`App/Dockerfile.cranecloud`) installed `App/backend/requirements-cranecloud.txt`: 34 version ranges, with no hashes. Three things followed.

- **Two builds of one commit could differ.** Each build took whatever was newest inside each range that day, for the 34 packages and the 58 they pull in.
- **Nothing checked a downloaded file.** pip verified no hash, so a replaced or corrupted file on the index would have been installed.
- **CI tested other versions.** CI installed `App/requirements.txt` first, which pinned `numpy==1.26.4`, and the image's ranges on top. The image shipped numpy 2.4.6. Four more packages differed between the test environment and the image: `pandas`, `qdrant-client`, `portalocker` and `python-dateutil`.

`App/backend/requirements.lock.txt`, a hashed lock for the other backend list, already existed. Nothing installed from it.

Dependabot had also lost two folders. ADR-0064 moved `ml/` and the root requirements under `App/`, but `.github/dependabot.yml` still watched `/ml` and `/` for pip, and `/` for Docker. Dependabot reports nothing for a folder with no manifest, and the test that guarded the entry asserted the string `/ml`.

## Considered Options

1. Install the image from a hashed lock, and install the same lock in CI.
2. Pin exact versions in `requirements-cranecloud.txt` without hashes.
3. Keep ranges and rebuild often.

## Decision Outcome

Option 1. It is pip's own hash-checking mode, and the repository already uses it for the crawler (`App/ml/requirements-crawl.txt`).

- **The lock.** `App/backend/requirements-cranecloud.lock.txt` is compiled with the command in its header: `uv pip compile` with `--generate-hashes`, for the image's Python (3.11.17) and platform. It pins 92 packages, each with the SHA-256 of every file it may be installed from. The ranges file stays the place a person edits.
- **The image.** The build runs `pip install --require-hashes --only-binary :all: -r requirements-cranecloud.lock.txt`. `--only-binary` means no source distribution is built, so no package's build script runs during the image build. The lock is in the build context and in the build workflow's path filter.
- **CI.** Every job that installs `App/requirements.txt` passes the lock as constraints (`-c`), so the packages the two lists share resolve to the shipped versions. The backend steps then install the lock itself with `--require-hashes`. The root list's `numpy==1.26.4` becomes the image's range, because it was the one pin that excluded a shipped version.
- **Vulnerability audit.** The pip-audit job audits the lock as pinned. It had never audited the image's list at all, and re-resolving the ranges could pass while a pinned version was vulnerable.
- **Dependabot.** It watches `/App/ml` and `/App` for pip, and no longer `/` for Docker.

Not covered: `App/Dockerfile`, the API image the Compose stacks build, still installs ranges with a CUDA index. It is not what production runs. Its lock stays checked by `test_backend_lockfile_satisfies_its_requirements` and is tracked as G690.

## Consequences

- Adopting the lock changed no production version. It was compared with `pip freeze` from the shipped image `sha-9a8e344`: the same 92 packages at the same versions.
- A dependency change is now two files. Edit the range, re-run the header's command, and commit both. A range raised without a new lock fails `test_the_production_lock_satisfies_its_requirements_and_hashes_every_package`.
- A package that publishes no wheel for Python 3.11 on Linux x86-64 fails the image build, by design. The fix is to pick a version with a wheel, not to drop `--only-binary`.
- New releases no longer arrive by themselves. Dependabot's weekly pip run for `/App/backend` proposes them, and a person merges.
- The bootstrap line `pip install --upgrade pip wheel setuptools` is still unpinned.

## Confirmation

- `App/tests/test_ci_supply_chain_policy.py`:
  - the image installs only the lock, hash-checked and binary-only
  - the lock satisfies every range and hashes every package
  - every CI install of the root list is constrained by the lock, and no root pin excludes a shipped version
  - every folder Dependabot watches holds a manifest
  - the pip-audit matrix audits the lock as pinned
- In the image's own base container (`python:3.11-slim-bookworm`, the pinned digest), `pip install --require-hashes --only-binary :all:` from the lock installed and `pip check` passed.
- `uv pip install --dry-run` of both CI commands resolved: 217 packages for the constrained root list, with the five shipped versions, and 92 for the lock.
