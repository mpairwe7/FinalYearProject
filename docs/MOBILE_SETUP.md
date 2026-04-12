# URA Chatbot -- Mobile App Setup Guide

Setup and build instructions for the Flutter mobile app at `MobileApp/ura_chatbot/`.

> For architecture / state management / design tokens / ADRs see
> **[MOBILE_ARCHITECTURE.md](MOBILE_ARCHITECTURE.md)**.

---

## 1. Prerequisites

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| Flutter SDK | 3.41+ | Dart SDK ^3.11.0 (bundled with Flutter) |
| Android Studio | Hedgehog+ | Android SDK, emulator images |
| Xcode | 15+ | macOS only; required for iOS builds |
| JDK | 17 | Required by Gradle 8.14 and the Kotlin toolchain |
| CocoaPods | 1.15+ | `gem install cocoapods` (iOS only) |

Verify your environment:

```bash
flutter doctor -v
java -version   # must show 17
```

---

## 2. Project Setup

```bash
git clone <repo-url>
cd FinalYearProject/MobileApp/ura_chatbot

flutter pub get
```

### Configure the API URL

The backend URL is set via a compile-time constant in `lib/core/config/api_config.dart`. The default points at the Android emulator loopback (`http://10.0.2.2:8000`).

Override it at build time:

```bash
flutter run --dart-define=API_URL=https://api.example.com
```

Or for release builds:

```bash
flutter build apk --dart-define=API_URL=https://api.example.com
```

---

## 3. Android Build

Key configuration lives in `android/app/build.gradle.kts`.

| Setting | Value | File |
|---------|-------|------|
| `minSdk` | 24 (API 24, MediaPipe requirement) | `build.gradle.kts` |
| `targetSdk` | Set by Flutter (`flutter.targetSdkVersion`) | `build.gradle.kts` |
| `compileSdk` | Set by Flutter (`flutter.compileSdkVersion`) | `build.gradle.kts` |
| Java/Kotlin target | 17 | `build.gradle.kts` |
| Gradle | 8.14 | `gradle-wrapper.properties` |
| Namespace | `com.example.ura_chatbot` | `build.gradle.kts` |

GGUF model files are excluded from APK compression (required for memory-mapped loading):

```kotlin
androidResources {
    noCompress += listOf("gguf")
}
```

### Signing Config

The release build currently uses debug signing. For production, create a keystore and add a `signingConfigs` block in `build.gradle.kts`:

See the [Android signing guide](https://developer.android.com/studio/publish/app-signing#sign-apk) for configuring release signing. Store credentials in environment variables or a `key.properties` file (excluded from version control via `.gitignore`).

### Build Commands

```bash
# Debug APK
flutter build apk --debug

# Release APK
flutter build apk --release --dart-define=API_URL=https://api.example.com

# Android App Bundle (Play Store)
flutter build appbundle --release --dart-define=API_URL=https://api.example.com
```

---

## 4. iOS Build

Key configuration lives in `ios/Podfile` and the Xcode project.

| Setting | Value | File |
|---------|-------|------|
| Deployment target | iOS 16.0 | `Podfile`, `project.pbxproj` |
| MediaPipe pod | `MediaPipeTasksGenAI ~> 0.10.22` | `Podfile` |
| Frameworks | `use_frameworks!` required | `Podfile` |

### Pod Install

```bash
cd ios
pod install
cd ..
```

If pods fail, try:

```bash
cd ios
pod deintegrate
pod install --repo-update
cd ..
```

### Code Signing

1. Open `ios/Runner.xcworkspace` in Xcode.
2. Select the **Runner** target > **Signing & Capabilities**.
3. Set your Team and Bundle Identifier.
4. For CI, use `match` or manual provisioning profiles.

### Build and Archive

```bash
# Debug (simulator)
flutter build ios --debug --simulator

# Release (device)
flutter build ios --release --dart-define=API_URL=https://api.example.com

# Archive for App Store (via Xcode)
# Open Runner.xcworkspace > Product > Archive
```

---

## 5. On-Device LLM

The app supports on-device inference using **Gemma-2B** in GGUF format via the **MediaPipe LLM Inference API**.

| Property | Value |
|----------|-------|
| Model | `ura-gemma-2b-q4_k_m.gguf` |
| Quantization | Q4_K_M |
| Size | ~1.5 GB |
| Max tokens | 512 (default) |
| Temperature | 0.2 |
| Top-P | 0.95 |
| Context length | 1024 tokens |

Configuration is defined in `lib/core/inference/on_device_llm.dart` (`OnDeviceLlmConfig`).

### Model Placement

**Android:** Place the GGUF file at:

```
android/app/src/main/assets/models/ura-gemma-2b-q4_k_m.gguf
```

On first launch, `MainActivity.kt` extracts the model from APK assets to the app's files directory (the GGUF is too large for direct asset streaming).

**iOS:** Add the GGUF file to the Xcode project as a bundle resource, or place it in the app's Documents directory. `AppDelegate.swift` checks Documents first, then falls back to the main bundle.

### Without the Model

The model file is **not required** for development. If it is absent, `OnDeviceLlm.initialize()` returns `false` and the app uses the remote API exclusively. No crash, no error dialog.

### Dependencies

- Android: `com.google.mediapipe:tasks-genai:0.10.22` (in `build.gradle.kts`)
- iOS: `MediaPipeTasksGenAI ~> 0.10.22` (in `Podfile`)

---

## 6. Architecture

### State Management

The app uses **Riverpod** (`flutter_riverpod: ^2.6.1`). The root widget is wrapped in a `ProviderScope` in `lib/main.dart`.

Key providers:

| Provider | File | Purpose |
|----------|------|---------|
| `chatProvider` | `lib/features/chat/providers/chat_provider.dart` | Chat messages, send/receive, offline fallback |
| `faqProvider` | `lib/features/faq/providers/faq_provider.dart` | FAQ tag listing and content |
| `settingsProvider` | `lib/features/settings/providers/settings_provider.dart` | Theme, locale, TTS/ASR settings |
| `consentProvider` | `lib/features/consent/providers/consent_provider.dart` | Per-purpose voice consent (encrypted) |
| `asrRouterProvider` | `lib/core/speech/speech_providers.dart` | Whisper + native ASR routing |
| `ttsEngineProvider` | `lib/core/speech/speech_providers.dart` | MMS-VITS TTS engine |
| `modelManagerProvider` | `lib/core/speech/speech_providers.dart` | Speech model download/verify |
| `audioCaptureProvider` | `lib/core/speech/speech_providers.dart` | PCM16 mic streaming |
| `ttsPlaybackProvider` | `lib/core/speech/speech_providers.dart` | just_audio TTS playback |

### Networking

**Dio** (`dio: ^5.7.0`) handles all HTTP communication. The client is configured in `lib/core/network/api_client.dart` with timeouts from `ApiConfig`:

- Connect timeout: 10 seconds
- Receive timeout: 30 seconds

### Feature-Based Structure

```
lib/
  main.dart                              # App entry point, ProviderScope, AppShell
  core/
    config/api_config.dart               # API URL, endpoints, constraints
    inference/on_device_llm.dart         # On-device Gemma-2B platform channel client
    network/api_client.dart              # Dio HTTP client
    storage/local_storage.dart           # SharedPreferences wrapper, session ID
    theme/app_theme.dart                 # Light/dark Material 3 themes
  features/
    chat/
      models/chat_models.dart            # ChatMessage, ChatResponse, FeedbackPayload
      providers/chat_provider.dart       # ChatNotifier (StateNotifier)
      screens/chat_screen.dart           # Main chat UI
      widgets/                           # message_bubble, citation_card, feedback_buttons,
                                         # faithfulness_badge, voice_input_button, etc.
    faq/
      models/faq_models.dart
      providers/faq_provider.dart
      screens/faq_screen.dart
    settings/
      providers/settings_provider.dart
      screens/settings_screen.dart
```

### Additional Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `shared_preferences` | ^2.5.3 | Local key-value storage (session, theme, locale) |
| `sherpa_onnx` | ^1.9.0 | Unified speech runtime: Whisper ASR + VITS TTS + Silero VAD |
| `record` | ^5.1.0 | PCM16 16 kHz streaming mic capture |
| `just_audio` | ^0.9.40 | TTS audio playback with in-memory WAV source |
| `flutter_tts` | ^4.0.2 | Native OS TTS fallback (English only) |
| `speech_to_text` | ^7.0.0 | Native OS ASR fallback (English only) |
| `permission_handler` | ^11.3.0 | Mic + notification permission management |
| `flutter_secure_storage` | ^9.2.0 | Encrypted consent grant storage |
| `crypto` | ^3.0.5 | SHA-256 for model verification + TTS cache keys |
| `sqflite` | ^2.3.0 | Hash-chained audit ledger |
| `path_provider` | ^2.1.0 | App support dir for models + TTS cache |
| `uuid` | ^4.5.1 | Message, session, and consent grant IDs |
| `url_launcher` | ^6.3.1 | Opening external links (URA website) |

---

## 7. Platform Channels

Both platforms expose the same MethodChannel for on-device LLM inference.

**Channel name:** `com.ura_chatbot/llm_inference`

| Method | Arguments | Returns |
|--------|-----------|---------|
| `initialize` | `modelPath`, `maxTokens`, `temperature`, `topP`, `contextLength` | `{"success": bool, "modelSizeMb": int}` or `{"success": false, "reason": string}` |
| `generate` | `prompt`, `maxTokens`, `temperature`, `topP` | `{"text": string, "tokensGenerated": int, "latencyMs": double}` |
| `dispose` | (none) | `null` |

### Android -- `MainActivity.kt`

Path: `android/app/src/main/kotlin/com/example/ura_chatbot/MainActivity.kt`

- Extends `FlutterActivity`.
- Runs inference on a single-thread executor to avoid blocking the UI.
- Posts results back to the main looper via `Handler`.
- Resolves the GGUF model by extracting from APK assets to the files directory on first access.

### iOS -- `AppDelegate.swift`

Path: `ios/Runner/AppDelegate.swift`

- Extends `FlutterAppDelegate`.
- Runs inference on a dedicated `DispatchQueue` (`qos: .userInitiated`).
- Dispatches results back to the main queue.
- Resolves the GGUF model from the Documents directory first, then the app bundle.

The Dart side (`lib/core/inference/on_device_llm.dart`) calls these channels and handles `MissingPluginException` gracefully for builds without native support.

---

## 8. Offline Mode

The app provides an offline fallback when the backend API is unreachable.

### How It Works

1. `ChatNotifier.send()` attempts the remote API call first.
2. On `DioException` with `connectionTimeout`, `connectionError`, or `sendTimeout`, it checks `isOfflineReady`.
3. If the on-device Gemma-2B model is loaded, `_generateOffline()` runs local inference using the same prompt template as the backend (`llm.py` format with Gemma chat-template markers).
4. Offline responses are tagged with `retrievalMode: 'offline'` and `model: 'gemma-2-2b-it'`.

### Local FAQ Cache

FAQ data fetched from the `/tags` and `/faq/{tag}` endpoints is cached locally via `shared_preferences`. When offline, the FAQ screen serves cached content.

### Limitations

- On-device inference has no retrieval context (no vector DB access offline), so answers rely on the model's parametric knowledge.
- Maximum context length is 1024 tokens (vs. longer contexts available server-side).
- First inference after a cold start may take several seconds while the model loads (~1.5 GB).

---

## 9. On-Device Speech (ASR + TTS)

The app includes a fully offline speech pipeline for English and Luganda.

### How It Works

1. **First launch** redirects to a consent screen with three scoped grants
   (mic access, transcript storage, model improvement). The mic button
   is disabled until `voiceRecord` consent is granted.

2. **First mic tap** triggers on-demand download of speech model bundles
   (~240 MB total) via Play Asset Delivery (Android) / HTTPS fallback.
   Progress is shown in Settings > Speech Models.

3. **Recording** uses `AudioCapture` (PCM16 16 kHz mono via the `record`
   package) fed through `RmsVad` for voice activity detection. When the
   user stops speaking (800 ms silence), the utterance is routed to
   `AsrRouter.transcribe()`.

4. **ASR** — primary: Whisper Small INT8 via sherpa_onnx (en/lg/sw);
   fallback: native OS `speech_to_text` (English only). URA domain
   glossary (TIN, PAYE, EFRIS, VAT) applied as Whisper hotwords +
   regex post-fixups.

5. **TTS** — "Listen" button on every assistant message. MMS-TTS VITS
   for English + Luganda. LRU disk cache (50 MB, SHA-256 keyed).
   EU AI Act Article 50 compliant: session-scoped disclosure label +
   inaudible 19 kHz watermark on every playback.

6. **Audit** — every consent event, mic open, ASR result, TTS play is
   logged to a hash-chained SQLite ledger (`LocalLedger`). Tamper-evident
   via SHA-256 chain. Consent grant ID threaded through all events.

### Speech Model Bundles

Models are declared in `assets/speech/manifest.json`:

| Bundle | Size | Tier |
|--------|------|------|
| `whisper-small-int8` | 120 MB | Primary ASR |
| `whisper-tiny-int8` | 40 MB | Fallback ASR (low-RAM devices) |
| `silero-vad-v5` | 2.4 MB | Voice activity detection |
| `mms-tts-eng` | 40 MB | English TTS |
| `mms-tts-lug` | 40 MB | Luganda TTS |

Manage downloads in Settings > Speech Models. Each bundle is SHA-256
verified on download and can be deleted to free space.

### Without Speech Models

Speech models are **not required** for development. If absent, the mic
button shows "Download the Luganda model" when tapped for Luganda, and
falls back to native OS recognition for English. TTS buttons show
"TTS model not available" if VITS bundles are missing.

### Android Permissions

```xml
<uses-permission android:name="android.permission.RECORD_AUDIO"/>
<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
```

### iOS Privacy Descriptions

```xml
<key>NSMicrophoneUsageDescription</key>
<string>Record your voice for on-device speech recognition. Audio is
processed entirely on your phone and is never uploaded.</string>
<key>NSSpeechRecognitionUsageDescription</key>
<string>On-device speech recognition to convert your voice to text.
All processing happens locally on your device.</string>
```

---

## 10. App Store Compliance

### Apple App Store -- Guideline 5.1.2(i)

Apps using AI-generated content must disclose:

- That the app uses AI/ML to generate responses.
- What data (if any) is sent to external servers for processing.
- That on-device processing keeps user queries local when offline.

In the App Store Connect submission:

1. In **App Privacy**, disclose data types sent to the backend (user queries, analytics events).
2. In the **App Review Notes**, explain the dual-mode architecture (cloud API + on-device Gemma-2B).
3. Add an in-app disclosure in the Settings screen describing AI usage.

### Google Play -- AI Policies

- Disclose AI-generated content in the Play Console **Data Safety** section.
- Ensure generated content does not violate Google's Generative AI policy (no impersonation, no harmful content).
- The app's content rating questionnaire should reflect that it provides AI-generated tax guidance.

### Both Platforms

- User data sent to the API: message text, session ID (UUID), locale, analytics events.
- No personal identifiable information (PII) is collected unless the user includes it in a message.
- The on-device model processes queries entirely on the user's device with no data exfiltration.

---

## 11. Testing

### Unit and Widget Tests

```bash
cd MobileApp/ura_chatbot
flutter test
```

31 tests across 6 test files (widget, UI components, ASR router,
audio capture/VAD, TTS cache, consent provider).

### Integration Tests

```bash
flutter test integration_test/
```

### Device Testing

```bash
# Run on a connected device or emulator
flutter run

# Run in release mode
flutter run --release --dart-define=API_URL=https://api.example.com

# Run on a specific device
flutter devices                  # list available devices
flutter run -d <device-id>
```

### Testing the On-Device LLM

1. Place `ura-gemma-2b-q4_k_m.gguf` in the appropriate assets directory (see section 5).
2. Run the app on a physical device (emulators may lack sufficient RAM for the 1.5 GB model).
3. Disable network (airplane mode) and send a chat message -- the app should fall back to on-device inference.

---

## 12. Build Commands -- Quick Reference

| Task | Command |
|------|---------|
| Install dependencies | `flutter pub get` |
| Run (debug) | `flutter run` |
| Run (release) | `flutter run --release --dart-define=API_URL=<url>` |
| Build debug APK | `flutter build apk --debug` |
| Build release APK | `flutter build apk --release --dart-define=API_URL=<url>` |
| Build AAB (Play Store) | `flutter build appbundle --release --dart-define=API_URL=<url>` |
| Build iOS (simulator) | `flutter build ios --debug --simulator` |
| Build iOS (device) | `flutter build ios --release --dart-define=API_URL=<url>` |
| Run tests | `flutter test` |
| Run integration tests | `flutter test integration_test/` |
| Analyze code | `flutter analyze` |
| iOS pod install | `cd ios && pod install && cd ..` |
| List devices | `flutter devices` |
| Clean build cache | `flutter clean && flutter pub get` |

All commands should be run from `MobileApp/ura_chatbot/`.
