# Runbook: Releasing the Statutory Knowledge Kernel

How to change a rate, a deadline, a penalty or a rule and get it to production (ADR-0033). The
reference is [../KNOWLEDGE_KERNEL.md](../KNOWLEDGE_KERNEL.md).

## When you need a release

You need one whenever any of these change:

- the rate tables (`App/backend/app/tax/data/FY*.json`);
- the graph build (`app/graph/build.py`);
- the kernel model (`app/kernel/model.py`);
- the rules (`app/kernel/rules.py`).

CI tells you. `test_the_running_kernel_is_a_recorded_release` fails, and so does:

```bash
cd App/backend && PYTHONPATH=. python -m app.kernel.release check
```

## Steps

1. **Make the change and validate it.**

   ```bash
   cd App/backend
   PYTHONPATH=. python -m pytest ../../tests/agents/test_statutory_kernel_2026_11.py -q
   PYTHONPATH=. python -c "from app.graph.build import build_graph; from app.kernel.validate import check; print(check(build_graph()))"
   ```

   Every rate-table key must still be classified. A new key with an unfamiliar shape needs a prefix in
   `_KERNEL_TAX_PREFIXES` and, if it is not a rate, a rule in `_KERNEL_KIND_RULES`. Give every figure a
   `legal_basis`. One without a basis is served as unverified.

2. **Choose a version.**
   - A new fiscal year, a new node kind or a rule change is a minor version.
   - A corrected figure or citation is a patch version.
   - A change to the model's meaning is a major version.

3. **Record the release.** An engineer records an `engineering_baseline`:

   ```bash
   PYTHONPATH=. python -m app.kernel.release record --version 1.1.0 --notes "FY2027-28 tables (Finance Acts 2027)"
   ```

   A release becomes `approved` only when the URA tax-policy reviewer is named:

   ```bash
   PYTHONPATH=. python -m app.kernel.release record --version 1.1.1 --notes "Reviewed" \
     --reviewer "Name Surname" --role "Commissioner, Tax Policy"
   ```

4. **Commit `app/kernel/releases.json` with the change**, in the same pull request.

## What a release changes at runtime

- New turns pin to the new version. Each audit row's `kernel_pin` shows which release answered it.
- A graph approval suspended under the old release is refused on resume ("the kernel release changed
  since the approval was asked"), and the taxpayer is asked again. That is deliberate. Expect a short
  rise in re-asked approvals right after a deploy.

## Investigating an evidence conflict

The discrepancy queue receives items of type `evidence_conflict` when a document, FAQ or web
passage states a different figure from the kernel's. The kernel's figure was served. Decide which is
right:

- **The passage is stale:** correct or tombstone it at its source.
- **The kernel is wrong:** fix the rate table, then release (above).
