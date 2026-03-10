# URA Chatbot — Flutter Mobile App

AI-powered tax assistant for Uganda Revenue Authority, with on-device Gemma-2B inference and remote API fallback.

## Architecture

```
Flutter App
  ├── Remote API Mode (default)
  │     └── Dio HTTP → FastAPI backend → Qwen2.5-3B-Instruct (server)
  │
  └── On-Device Mode (offline capable)
        └── Platform Channels → MediaPipe LLM Inference → Gemma-2-2B GGUF (device)
```

The app automatically detects whether the on-device GGUF model is bundled and uses it for offline inference. When unavailable, it falls back to the remote API.

## Features

- Chat with RAG-grounded answers (citations, faithfulness scores)
- On-device Gemma-2B inference for offline use
- Voice input (speech-to-text)
- FAQ browsing by category (41 tags)
- Feedback (thumbs up/down + comments)
- Locale support (English, Luganda)
- Dark/light theme
- Escalation banners for low-confidence answers

## Project Structure

```
lib/
├── main.dart                         # App bootstrap
├── core/
│   ├── config/api_config.dart        # API endpoints + constraints
│   ├── network/api_client.dart       # Dio HTTP client
│   ├── inference/on_device_llm.dart  # On-device Gemma-2B (MediaPipe)
│   ├── storage/local_storage.dart    # SharedPreferences
│   └── theme/app_theme.dart          # Material 3 theme
└── features/
    ├── chat/
    │   ├── models/chat_models.dart   # ChatMessage, ChatResponse, Citation
    │   ├── providers/chat_provider.dart  # Riverpod state (API + offline)
    │   ├── screens/chat_screen.dart
    │   └── widgets/                  # MessageBubble, CitationCard, etc.
    ├── faq/                          # FAQ browsing
    └── settings/                     # Theme, locale, model settings
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

### On-Device Inference Setup

To enable offline Gemma-2B inference:

1. **Fine-tune and export** the model:
   ```bash
   # Fine-tune Gemma-2B for mobile
   python ml/scripts/fine_tune_gemma.py --target mobile_gemma_2b

   # Export to GGUF INT4 (~1.5 GB)
   python ml/scripts/export_mobile.py \
     --adapter artifacts/models/ura-gemma-2-2b-it-*/final \
     --quant Q4_K_M
   ```

2. **Bundle the model** in the Android app:
   ```bash
   cp artifacts/mobile/ura-gemma-2b-q4_k_m.gguf \
     android/app/src/main/assets/models/
   ```

3. **Native MediaPipe bridge** (already implemented):
   - **Android**: `MainActivity.kt` — Kotlin MethodChannel handler using `com.google.mediapipe:tasks-genai`
   - **iOS**: `AppDelegate.swift` — Swift MethodChannel handler using `MediaPipeTasksGenAI`
   - **Dart**: `on_device_llm.dart` — Platform channel bridge with automatic fallback
   - **Offline fallback**: `chat_provider.dart` — Falls back to on-device Gemma-2B on network errors

4. **iOS: Install CocoaPods** (Podfile already includes MediaPipe):
   ```bash
   cd ios && pod install && cd ..
   ```

### Device Requirements (On-Device)

| Platform | Min Version | Min RAM | Storage |
|----------|-------------|---------|---------|
| Android | API 24+ (recommended 12+) | 6 GB | ~1.5 GB |
| iOS | 16.0 | iPhone 12+ | ~1.5 GB |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_URL` | Backend API URL | `http://10.0.2.2:8000` (emulator) |

Pass via `--dart-define`:
```bash
flutter run --dart-define=API_URL=https://api.ura-chatbot.com
```

## Dependencies

### Flutter (pubspec.yaml)

| Package | Version | Purpose |
|---------|---------|---------|
| `flutter_riverpod` | ^2.6.1 | State management |
| `dio` | ^5.7.0 | HTTP client |
| `speech_to_text` | ^7.0.0 | Voice input |
| `shared_preferences` | ^2.5.3 | Local storage |
| `uuid` | ^4.5.1 | Message IDs |
| `url_launcher` | ^6.3.1 | External links |

### Native (on-device inference)

| Platform | Dependency | Purpose |
|----------|-----------|---------|
| Android | `com.google.mediapipe:tasks-genai:0.10.22` | On-device Gemma-2B LLM inference |
| iOS | `MediaPipeTasksGenAI` (CocoaPods/SPM) | On-device Gemma-2B LLM inference |

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
