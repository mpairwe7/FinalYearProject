# URA Chatbot — Flutter Mobile App (2026)

Production-ready AI tax assistant for Uganda Revenue Authority, with
on-device Gemma-2B inference and remote API fallback.

## Architecture

```
Flutter App (Material 3)
  │
  ├── Routing             go_router (declarative, deep-link ready)
  ├── State               Riverpod 2.6 Notifier + AsyncNotifier
  ├── Theme               Material 3 + ColorScheme.fromSeed(URA navy)
  ├── Design tokens       AppSpacing / AppRadius / AppMotion / AppElevation
  ├── Error handling      runZonedGuarded + FlutterError + ErrorWidget.builder
  ├── Connectivity        connectivity_plus → live offline banner
  ├── Build info          package_info_plus → real version in Settings
  │
  ├── Remote mode (default)
  │     └── Dio HTTP → FastAPI → Qwen2.5-3B-Instruct (server-side)
  │
  └── On-device mode (offline capable)
        └── Platform channels → MediaPipe LLM Inference → Gemma-2-2B Q4_K_M
```

The app automatically detects whether the on-device GGUF model is
bundled and uses it for offline inference. When unavailable or when a
network error occurs, it falls back transparently to the remote API —
and the [OfflineBanner] announces the mode to the user.

## Features

- **Chat** with RAG-grounded answers — citations, faithfulness score,
  escalation banner for low-confidence replies, long-press to copy,
  per-message timestamps, day separators
- **On-device Gemma-2B** inference for offline use (1.6 GB Q4_K_M GGUF)
- **Voice input** (speech-to-text) with pulsing indicator when recording
- **FAQ browsing** by category with shimmer skeleton during load,
  pull-to-refresh, empty state when no data, selectable answer text
- **Feedback** (thumbs up/down) with optional comment sheet
- **Settings** with Material 3 segmented buttons for theme + language,
  live server health, build version, privacy summary
- **Offline banner** — auto-shows when `connectivity_plus` reports no
  network, differentiates when the on-device model is available
- **Accessibility** — text-scale clamping, semantic labels, haptic
  feedback on every interactive action, 48dp minimum touch targets
- **Deep linking ready** — ``/faq/:tagId`` already a registered route

## Project Structure

```
lib/
├── main.dart                            # runZonedGuarded bootstrap
├── core/
│   ├── build_info/
│   │   └── build_info_provider.dart     # package_info_plus → BuildInfo
│   ├── config/
│   │   └── api_config.dart              # API_URL / DEV_API_URL env vars
│   ├── connectivity/
│   │   └── connectivity_provider.dart   # connectivityStatusProvider, isOnlineProvider
│   ├── errors/
│   │   └── error_handler.dart           # AppErrorHandler.install() + reporter hook
│   ├── inference/
│   │   └── on_device_llm.dart           # MediaPipe LLM Inference bridge
│   ├── network/
│   │   └── api_client.dart              # Dio + session + error interceptors
│   ├── router/
│   │   └── app_router.dart              # go_router with StatefulShellRoute
│   ├── storage/
│   │   └── local_storage.dart           # SharedPreferences facade
│   ├── theme/
│   │   ├── app_theme.dart               # Material 3 theme from seed colour
│   │   └── tokens.dart                  # AppSpacing, AppRadius, AppMotion, AppElevation
│   └── ui/
│       ├── app_error_view.dart          # Retryable error surface
│       ├── empty_state.dart             # M3 empty-screen pattern
│       ├── loading_skeleton.dart        # Shimmer list + bubble placeholders
│       └── offline_banner.dart          # Live-connectivity banner
│
└── features/
    ├── chat/
    │   ├── models/chat_models.dart      # ChatMessage, ChatResponse, Citation
    │   ├── providers/chat_provider.dart # Riverpod Notifier<ChatState>
    │   ├── screens/chat_screen.dart     # Day-separator ListView + FAB + input
    │   └── widgets/                     # Bubble, citation, feedback, voice, etc.
    ├── faq/
    │   ├── models/faq_models.dart       # TagInfo, FAQItem
    │   ├── providers/faq_provider.dart  # FutureProvider + family
    │   └── screens/
    │       ├── faq_screen.dart          # Tag list
    │       └── faq_detail_screen.dart   # Per-tag FAQ expansion
    └── settings/
        ├── providers/settings_provider.dart  # Notifier<AppSettings>
        └── screens/settings_screen.dart
```

## Setup

### Prerequisites

- Flutter 3.41+ / Dart 3.11+
- Android SDK 34+ / Xcode 15+
- Backend API running (for remote mode)

### Install & Run

```bash
cd MobileApp/ura_chatbot

# Install dependencies
flutter pub get

# Run on connected device/emulator
flutter run

# Build release APK
flutter build apk --release --dart-define=API_URL=https://api.example.com
```

### On-Device Inference Setup (2026 pipeline)

To enable offline Gemma-2B inference:

1. **Fine-tune** the model (one-time, GPU required):
   ```bash
   # From the project root
   python ml/scripts/fine_tune_gemma.py --target mobile_gemma_2b
   # Outputs: artifacts/ura-gemma-2-2b-it-<timestamp>/final/
   ```

2. **Export and auto-deploy** to this Flutter app:
   ```bash
   # Auto-discovers the latest fine-tune output, quantises to Q4_K_M
   # by default, atomically copies into android/app/src/main/assets/models/
   # AND ios/Runner/models/ with post-copy SHA-256 verification
   python ml/scripts/export_mobile.py
   ```

   Other options:
   ```bash
   # Smaller mobile build (~1.2 GB) with imatrix calibration
   python ml/scripts/export_mobile.py --quant IQ3_M --imatrix

   # Skip auto-deploy (CI artifact upload only)
   python ml/scripts/export_mobile.py --no-deploy

   # Dry run — validate adapter + tools
   python ml/scripts/export_mobile.py --dry-run
   ```

   The export pipeline writes:
   - `artifacts/mobile/ura-gemma-2b-q4_k_m.gguf` — quantised model
   - `artifacts/mobile/mobile_manifest.json` — pipeline version, sha256, lineage, deployed paths
   - `artifacts/mobile/MODEL_CARD.md` — full lineage card from `training_config.json`
   - `android/app/src/main/assets/models/ura-gemma-2b-q4_k_m.gguf` — Android asset (atomic + verified)
   - `ios/Runner/models/ura-gemma-2b-q4_k_m.gguf` — iOS staging file

3. **iOS one-time setup** — open `Runner.xcworkspace`, drag the GGUF file
   from `Runner/models/` into the project navigator (target: Runner).
   Subsequent re-exports replace the file in place — no Xcode action needed.

4. **Native MediaPipe bridge** (already implemented):
   - **Android**: `MainActivity.kt` — Kotlin MethodChannel using `com.google.mediapipe:tasks-genai:0.10.22`
   - **iOS**: `AppDelegate.swift` — Swift MethodChannel using `MediaPipeTasksGenAI ~> 0.10.22`
   - **Dart**: `lib/core/inference/on_device_llm.dart` — Platform channel bridge with automatic fallback
   - **Offline fallback**: `chat_provider.dart` — Falls back to on-device Gemma-2B on network errors

5. **iOS: Install CocoaPods** (Podfile already includes MediaPipe):
   ```bash
   cd ios && pod install && cd ..
   ```

> **Android note:** `android/app/build.gradle.kts` already declares
> `androidResources { noCompress += listOf("gguf") }`. This is mandatory —
> without it, the GGUF file is APK-compressed and the MediaPipe LLM
> Inference engine cannot `mmap` the model at runtime.

### Device Requirements (On-Device)

| Platform | Min Version | Min RAM | Storage |
|----------|-------------|---------|---------|
| Android | API 24+ (recommended 12+) | 6 GB | ~1.5 GB |
| iOS | 16.0 | iPhone 12+ | ~1.5 GB |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_URL` | Backend API URL (production builds) | unset |
| `DEV_API_URL` | Dev fallback — LAN IP of dev machine for real-device testing | unset |

If **neither** is set, the app falls back to `http://10.0.2.2:8000`
which is the Android emulator's loopback to the host machine.

```bash
# Production release
flutter build apk --release \
    --dart-define=API_URL=https://api.ura-chatbot.com

# Real-device dev against a LAN backend
flutter run \
    --dart-define=DEV_API_URL=http://192.168.1.42:8000
```

## Dependencies

### Flutter (pubspec.yaml)

| Package | Version | Purpose |
|---------|---------|---------|
| `flutter_riverpod` | ^2.6.1 | State management (Notifier API) |
| `go_router` | ^14.3.0 | Declarative routing + deep linking |
| `dio` | ^5.7.0 | HTTP client |
| `speech_to_text` | ^7.0.0 | Voice input |
| `shared_preferences` | ^2.5.3 | Local storage |
| `uuid` | ^4.5.1 | Message IDs |
| `url_launcher` | ^6.3.1 | External links |
| `package_info_plus` | ^8.0.0 | App version / build number |
| `connectivity_plus` | ^6.0.5 | Live network status |
| `intl` | ^0.19.0 | Date formatting |
| `shimmer` | ^3.0.0 | Loading skeleton placeholders |

### Native (on-device inference)

| Platform | Dependency | Purpose |
|----------|-----------|---------|
| Android | `com.google.mediapipe:tasks-genai:0.10.22` | On-device Gemma-2B inference |
| iOS | `MediaPipeTasksGenAI ~> 0.10.22` (CocoaPods) | On-device Gemma-2B inference |

## Testing

```bash
# Fast static analysis (tuned lints in analysis_options.yaml)
flutter analyze                # → No issues found

# Full test suite (12 tests, <15s)
flutter test                   # → All tests passed!
```

Test coverage:

- `test/widget_test.dart` — app bootstrap + navigation bar
- `test/core/ui_components_test.dart` — EmptyState, SkeletonList,
  AppErrorView, OfflineBanner, design token invariants, ColorTokens
  extension regression guard

See [../../docs/MOBILE_ARCHITECTURE.md](../../docs/MOBILE_ARCHITECTURE.md)
for the full design-decision doc.

## On-Device Inference Flow

```
User query → chat_provider.send()
  ├── Try: Dio HTTP → FastAPI backend (Qwen2.5-3B)
  │     └── Success → Display response with citations
  │
  └── Catch (network error) + isOfflineReady?
        ├── Yes → _generateOffline()
        │     └── OnDeviceLlm.generate() → Platform Channel
        │           ├── Android: MainActivity.kt → MediaPipe → GGUF
        │           └── iOS: AppDelegate.swift → MediaPipe → GGUF
        │
        └── No → Show error message
```

## Gemma-2B Fine-Tuning & Quantization

### Model Spec

| Parameter | Value |
|-----------|-------|
| Base model | `google/gemma-2-2b-it` |
| Fine-tuning | QLoRA (4-bit NF4, double quantization) |
| LoRA rank | 8 (mobile-optimized, reduced from default 16) |
| LoRA alpha | 16 |
| LoRA targets | q/k/v/o/gate/up/down projections |
| Max sequence length | 1024 tokens |
| Training epochs | 5 |
| Learning rate | 1e-4 |
| Export format | GGUF Q4_K_M (~1.5 GB) |
| Inference engine | MediaPipe LLM Inference API |

### Prompt Template (Gemma-2 chat format)

```
<start_of_turn>user
{SYSTEM_PROMPT — 8 rules matching backend llm.py}

## Retrieved passages
[1] Source: knowledge_base
<passage>{text, truncated to 1500 chars}</passage>

## User question
{query}
<end_of_turn>
<start_of_turn>model
```

### On-Device Inference Config

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `maxTokens` | 512 | Matches backend Qwen2.5-3B setting |
| `temperature` | 0.2 | Low for factual tax answers |
| `topP` | 0.95 | Conservative nucleus sampling |
| `contextLength` | 1024 | Matches fine-tuning max_seq_length |
