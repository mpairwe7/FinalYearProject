# Accessibility statement and WCAG 2.2 AA conformance status

**Service:** URA Tax Assistant (taxpayer chat and staff operations workbench)
**Conformance target:** [WCAG 2.2 Level AA](https://www.w3.org/TR/WCAG22/)
**Published:** 2026-08-19
**Owner:** URA Tax Assistant engineering team

## Current status

The product is engineered and automatically tested against WCAG 2.2 AA. This
is **not yet a formal claim of full conformance**: WCAG conformance covers full
pages and complete processes, and needs human evaluation in addition to
automation. A manual pass by an auditor who did not make the change remains
open in [issue #310](https://github.com/mpairwe7/FinalYearProject/issues/310).

This statement deliberately distinguishes implementation evidence from an
independent conformance finding. It will be updated with the audit date,
auditor, assistive-technology combinations, results, and remediation issue
links before a full conformance claim is made.

## Scope

The release gate exercises these public and authenticated staff surfaces in
both light and dark themes:

| Surface | Route or entry point |
| --- | --- |
| Taxpayer chat and composer | `/` |
| Account settings | header **More options** → **Settings** |
| Sign in | `/signin` |
| Operations overview | `/admin` |
| Agent work queue | `/agent` |
| Escalation queue and reply composer | `/admin/tickets` |
| Feature flag console | `/admin/flags` |

The audit does not represent external identity-provider pages, a browser's
permission prompt for microphone/camera access, or third-party links. Those
are outside this application's control and must be assessed separately in a
deployment review.

## Evidence and release controls

- `App/frontend/e2e/a11y.spec.ts` scans every scoped surface with axe-core's
  WCAG 2.0, 2.1, and 2.2 A/AA tags. Serious and critical results fail the test;
  there are no selector exclusions.
- The same suite checks keyboard menu navigation, dialog focus restoration,
  composer voice controls, and roving focus in the staff queue tabs.
- The chat transcript is not an ARIA live region. A separate atomic status
  region announces that a response has started and completed, preventing a
  stream from being re-announced token by token.
- CI runs the accessibility suite at desktop and mobile viewports. Lighthouse
  retains an accessibility score gate of at least 0.90.

Run the automated evidence locally:

```bash
cd App/frontend
bun run test:a11y
```

## Required independent manual audit

An auditor other than the implementer must record the following for every
scoped surface before a full WCAG 2.2 AA claim is published. Findings are
tracked in issue #310 and in linked remediation issues; an unresolved finding
blocks the claim.

| Check | Minimum evidence |
| --- | --- |
| Keyboard | Tab / Shift+Tab order, Enter/Space activation, Escape dismissal, arrow-key menus, tabs, filters, composer, file attachment, dictation and staff reply flows. Focus is never hidden, trapped, or left on `body`. |
| Screen reader | NVDA + Firefox and VoiceOver + Safari (or equivalent supported combinations). Verify headings, landmarks, names, state changes, error messages, staff queue selection, and one start/one completion announcement for a streamed answer. |
| Visual | Light and dark contrast, focus indicator visibility, 200% zoom/reflow, text spacing, and the warning/accent states in the operations dashboard. |
| Touch/mobile | 320 CSS px and a current Android/iOS browser. Check target size, zoom, portrait/landscape reflow, and microphone/camera fallback messages. |
| Authentication | Keyboard-only OIDC entry and the configured identity-provider flow, including any MFA or passkey screen owned by the provider. |

Record the audit as a comment on issue #310 with: auditor, date, build/commit,
browser and assistive technology versions, each route/flow tested, outcome,
and links to any new findings. Do not replace a failed item with an axe result:
automated scans cannot prove the keyboard, spoken-output, or visual criteria.

## Feedback and help

Report an accessibility barrier with the page or task, browser/device,
assistive technology if used, and a short description of the barrier. Include
the issue number `#310` when reporting against this prototype so the finding
is triaged with the audit record.
