# Mobile App — 2026 Architecture

Design-decision document for `MobileApp/ura_chatbot/`. Read
[MobileApp/ura_chatbot/README.md](../MobileApp/ura_chatbot/README.md)
for setup instructions; this doc is for engineers making architectural
changes.

---

## 1. Layered architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                          UI (widgets)                                 │
│  screens  ◀── RepaintBoundary ── const constructors ── .select()      │
└──────────────────────────────┬────────────────────────────────────────┘
                               │  watches
┌──────────────────────────────▼────────────────────────────────────────┐
│                 State (Riverpod 2.6 Notifier API)                     │
│                                                                        │
│   NotifierProvider<ChatNotifier, ChatState>        ← chat + offline   │
│   NotifierProvider<SettingsNotifier, AppSettings>  ← theme + locale   │
│   FutureProvider<List<TagInfo>>                    ← FAQ tags         │
│   FutureProvider.family<List<FAQItem>, String>     ← FAQ per tag      │
│   FutureProvider<BuildInfo>                        ← package_info     │
│   StreamProvider<bool>                             ← connectivity     │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────────┐
│                     Core infrastructure                                │
│                                                                        │
│   router/       → go_router StatefulShellRoute.indexedStack            │
│   network/      → Dio + session / error interceptors                   │
│   inference/    → MediaPipe LLM Inference via MethodChannel            │
│   storage/      → SharedPreferences facade                             │
│   connectivity/ → connectivity_plus stream                             │
│   errors/       → runZonedGuarded + FlutterError.onError               │
│   build_info/   → package_info_plus                                    │
│   theme/        → Material 3 + design tokens                           │
│   ui/           → EmptyState, SkeletonList, AppErrorView, OfflineBanner│
└──────────────────────────────┬────────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────────┐
│                       Platform (native)                                │
│                                                                        │
│   Android (Kotlin)  → MainActivity.kt → MediaPipe LLM Inference API    │
│   iOS (Swift)       → AppDelegate.swift → MediaPipeTasksGenAI          │
│   Both              → MethodChannel("com.ura_chatbot/llm_inference")   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architectural decisions (ADRs)

### ADR-001 — Riverpod 2.6 `Notifier` (not `StateNotifier`)

**Context.** Riverpod 2.6+ deprecated `StateNotifier` in favour of the
`Notifier` / `AsyncNotifier` base classes. The old pattern required a
separate field class + provider boilerplate and did not support codegen.

**Decision.** Migrate all providers to the new `Notifier` API. Keep an
explicit `ChatState` data class rather than wrapping in `AsyncValue`
because the chat surface holds partial history (many messages)
alongside a single in-flight request — wrapping the whole thing in an
`AsyncValue` would throw away history on every error.

**Consequences.**
- `build()` replaces a constructor + initial state.
- `ref.read` / `ref.watch` work inside `Notifier.build()`, enabling
  clean dependency wiring without constructor plumbing.
- Tests use `container.read(chatProvider.notifier)` without any DI.
- Code-gen (future `riverpod_generator` adoption) is a one-line change.

See: `lib/features/chat/providers/chat_provider.dart`,
`lib/features/settings/providers/settings_provider.dart`.

---

### ADR-002 — go_router with `StatefulShellRoute`

**Context.** The pre-2026 app used imperative `Navigator.push` and a
hand-rolled `IndexedStack` for tab preservation. This works but:
- Is not deep-linkable.
- Does not survive a process restart with intent state.
- Requires custom code to preserve per-tab navigation stacks.

**Decision.** Use `go_router` 14.3+ with `StatefulShellRoute.indexedStack`.
Each branch (Chat / FAQ / Settings) gets its own Navigator, so the
FAQ branch can drill into `FAQDetailScreen` and still remember the
drill-down when the user comes back from the Chat tab.

**Consequences.**
- Deep links like `https://ura-chatbot.example/faq/vat` become one
  line of Android manifest + iOS Info.plist to wire up at launch.
- Route strings are constants in `AppRoutes` for refactor safety.
- `TagInfo` is passed via `GoRouterState.extra` so the detail screen
  renders the title immediately without re-fetching the tag list.

See: `lib/core/router/app_router.dart`.

---

### ADR-003 — Design tokens over magic numbers

**Context.** The pre-2026 codebase hardcoded spacing (`4`, `8`, `12`,
`16`, `20`, `24`), radius (`4`, `8`, `10`, `18`), and durations
(`300ms`, `500ms`) at each call site. Any design refresh required a
massive find-replace.

**Decision.** Add `lib/core/theme/tokens.dart` exporting:
- `AppSpacing` — `xxs, xs, sm, md, lg, xl, xxl, xxxl, huge` on a 4dp grid
- `AppRadius` — `xs, sm, md, lg, xl, xxl, pill` + reusable `BorderRadius` constants
- `AppMotion` — `instant, fast, medium, slow` + `emphasized` curves
- `AppElevation` — Material 3 levels 0-5
- `ColorTokens` extension — `tint(double)`, `subtle`, `muted`, `soft`, `emphasis`

Every widget imports tokens and uses symbolic names. A wholesale
rhythm change is now one `const double md = 12` edit.

**Consequences.** Consistent rhythm across 15+ widgets. Unit tested
for monotonicity in `tests/core/ui_components_test.dart`.

---

### ADR-004 — `withValues(alpha:)` instead of deprecated `withAlpha(int)`

**Context.** Flutter 3.27 deprecated `Color.withAlpha(int)` in favour
of `Color.withValues(alpha: double)` which handles wide-gamut colour
spaces correctly.

**Decision.** Zero call sites of `withAlpha` in the codebase. The
`ColorTokens` extension uses method name `tint(double)` rather than
`alpha(double)` so it doesn't shadow the built-in `Color.alpha` int
getter (which would silently resolve to the wrong thing).

**Consequences.** A dedicated unit test guards against regression:

```dart
test('ColorTokens extension does not shadow Color.alpha int getter', () {
  const c = Color(0xFF1A3A6B);
  expect(c.tint(0.5).a, closeTo(0.5, 0.01));
  expect(c.subtle.a, closeTo(0.08, 0.01));
  // ...
});
```

---

### ADR-005 — Global error boundary via `runZonedGuarded`

**Context.** A framework error inside `build()` gives users the default
red error box in release mode, which looks broken. Unhandled async
errors vanish into stderr and never reach a crash reporter.

**Decision.** `lib/core/errors/error_handler.dart` installs three
funnels:
1. `FlutterError.onError` — framework build/layout errors
2. `PlatformDispatcher.instance.onError` — unhandled async errors
3. `ErrorWidget.builder` — release-mode replacement for the default
   red box (neutral "Something went wrong" card)

Every funnel routes to `AppErrorHandler.report(error, stack)` which
logs via `dart:developer` and optionally forwards to a
pluggable `reporter` function. Teams pick their vendor (Sentry,
Crashlytics, BugSnag) in one line at `main()`:

```dart
AppErrorHandler.reporter = Sentry.captureException;
```

**Consequences.** Zero-config stable release builds; vendor choice
deferred without touching any other file.

---

### ADR-006 — `connectivity_plus` for offline UX

**Context.** The chat provider has an automatic fallback to the
on-device Gemma-2B model when a network error occurs, but the user
never saw a visible indication that they were offline. The status
was only inferable from the response metadata.

**Decision.** Subscribe to `Connectivity.onConnectivityChanged` via a
Riverpod `StreamProvider<bool>`. The `OfflineBanner` widget watches
that provider and slides in from the top when `isOnline == false`,
with differentiated copy depending on whether the on-device model is
available:

- **Offline + no fallback**: red error banner "No internet connection"
- **Offline + on-device ready**: tertiary-container banner "Offline —
  using on-device model"

The banner lives in the `StatefulShellRoute` builder so it's visible
on all three tabs.

**Consequences.** User always knows the app's mode. The on-device
fallback path is now legible rather than silent.

---

### ADR-007 — Stricter, curated lints

**Context.** The default `flutter_lints` package misses a lot of
safety-critical rules (`use_build_context_synchronously`,
`avoid_dynamic_calls`, `prefer_const_constructors`). The strictest
upstream preset (`very_good_analysis`) introduces significant noise
from pure-style rules (`sort_constructors_first`,
`always_put_required_named_parameters_first`) that do not catch bugs.

**Decision.** `analysis_options.yaml` extends `flutter_lints` with a
**curated** set of safety-critical + real-perf rules. Pure-style
rules are explicitly disabled in a `# --- Deliberately OFF ---`
section so future readers understand the rationale.

Safety-critical rules enforced:
- `use_build_context_synchronously` — prevents context use after async gap
- `avoid_slow_async_io` — catches blocking I/O on the UI thread
- `avoid_web_libraries_in_flutter` — cross-platform build safety
- `cancel_subscriptions` / `close_sinks` — resource leaks
- `control_flow_in_finally` / `throw_in_finally` — exception-loss bugs
- `hash_and_equals` — Dart value semantics
- `no_duplicate_case_values` — unreachable code
- `strict-casts` / `strict-inference` / `strict-raw-types` — language-level

Performance rules enforced:
- `prefer_const_constructors` (+ `_in_immutables`, `_literals`)
- `prefer_final_fields` / `prefer_final_locals`
- `sized_box_for_whitespace`
- `use_decorated_box` / `use_colored_box`

**Consequences.** `flutter analyze` returns `No issues found!` on the
entire lib tree. CI gates on it.

---

## 3. Performance patterns

### 3.1 Narrow subscriptions via `.select()`

The chat screen uses `ref.watch(chatProvider.select((s) => s.messages))`
and `ref.watch(chatProvider.select((s) => s.isLoading))` instead of
`ref.watch(chatProvider)`. This means the AppBar doesn't rebuild when
a new message arrives, and the message list doesn't rebuild when the
loading spinner toggles.

### 3.2 `RepaintBoundary` around list items

Every `MessageBubble`, `_DaySeparator`, `_TagCard`, and `_FAQCard` is
wrapped in a `RepaintBoundary`. This lets Flutter cache the bubble's
paint layer; scrolling repaints only the layers of bubbles that are
actually entering/leaving the viewport.

### 3.3 `cacheExtent: 500`

The chat `ListView.builder` sets `cacheExtent: 500` to pre-lay-out
messages just off-screen. Eliminates jank when the user slow-scrolls.

### 3.4 `MediaQuery.sizeOf` / `paddingOf`

Replaces `MediaQuery.of(context).size` / `.padding`. These narrower
getters track only the sub-property, so rotating the device or
opening the keyboard doesn't rebuild unrelated widgets.

### 3.5 `textScaler.clamp(0.85, 1.35)`

The root `MaterialApp.builder` clamps the system text scale to a
sensible range. Prevents the layout from imploding at iOS "Larger
Text" maximum (2.0× default) while still honouring accessibility needs.

---

## 4. Testing strategy

| Layer | Coverage |
|---|---|
| Design tokens | Monotonicity of `AppSpacing` / `AppRadius` / `AppMotion` + `ColorTokens` extension regression guard |
| Shared UI | `EmptyState` (2 tests), `SkeletonList` (1), `AppErrorView` (2), `OfflineBanner` (1) |
| App bootstrap | `widget_test.dart` — navigation bar renders with all providers wired |

Run:

```bash
flutter analyze   # 0 issues
flutter test      # 12 passed
```

---

## 5. Deferred (intentional non-goals)

Items from the original audit that were **not** applied, with
rationale:

| Item | Rationale |
|---|---|
| `freezed` / JSON codegen | Only 2 data models; manual `fromJson` is fine and keeps the build simple. |
| i18n strings (`.arb` files) | Only 2 locales (en / lg) with a handful of user-facing strings. 50-file refactor not worth the cost yet. |
| Adaptive navigation (NavigationRail on tablets) | App is locked to portrait orientation. |
| Hero animations / shared element transitions | Polish, not a production gap. |
| Push notifications / deep links beyond FAQ detail | Out of scope for this pass. |
| Sentry / Crashlytics wiring | Hook is in place (`AppErrorHandler.reporter`); vendor choice is a one-liner when the team picks one. |

---

## 6. File reference

Every file in `lib/` maps to one of the layers above. Use this as a
grep reference:

```bash
# Widgets that talk to providers
grep -rn "ref.watch" lib/features/
# Providers
grep -rn "Provider\|Notifier" lib/features/ | grep -v test
# Router declarations
grep -rn "GoRoute\|StatefulShellRoute" lib/core/router/
# Design-token call sites
grep -rn "AppSpacing\|AppRadius\|AppMotion" lib/
```
