# sherpa-onnx Android Integration (2026)

This directory holds the native glue code for the unified audio runtime
on Android. The real `.so` libraries ship separately — this README is
the integration contract for whoever builds the native side.

## What this does

Bridges Flutter/Dart to the `sherpa-onnx` C++ library via JNI:

```
Dart MethodChannel('app.ura.sherpa_onnx')
     |
     v
Kotlin plugin (app/src/main/kotlin/.../SherpaOnnxPlugin.kt)
     |
     v
JNI glue (this directory)
     |
     v
libsherpa-onnx-jni.so (pre-built upstream)
```

## Integration steps

1. **Add the dependency** to `android/app/build.gradle`:
   ```gradle
   dependencies {
       implementation 'com.k2-fsa:sherpa-onnx:1.11.4'
   }
   ```
   (Upstream: https://github.com/k2-fsa/sherpa-onnx — Apache-2.0)

2. **Register the MethodChannel** in `MainActivity.kt`:
   ```kotlin
   import io.flutter.embedding.android.FlutterActivity
   import io.flutter.embedding.engine.FlutterEngine
   import io.flutter.plugin.common.MethodChannel
   import com.k2fsa.sherpa.onnx.*

   class MainActivity: FlutterActivity() {
       private val CHANNEL = "app.ura.sherpa_onnx"
       override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
           super.configureFlutterEngine(flutterEngine)
           MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
               .setMethodCallHandler { call, result ->
                   // Dispatch to SherpaOnnxBridge (see stub below)
               }
       }
   }
   ```

3. **Load assets at init time**:
   ```kotlin
   val asrEnCfg = OfflineRecognizerConfig(
       featConfig = FeatureConfig(sampleRate = 16000),
       modelConfig = OfflineModelConfig(
           whisper = OfflineWhisperModelConfig(
               encoder = "speech/asr/whisper-small-en/encoder.int8.onnx",
               decoder = "speech/asr/whisper-small-en/decoder.int8.onnx"
           ),
           tokens = "speech/asr/whisper-small-en/tokens.txt",
           numThreads = 2
       )
   )
   val asrEn = OfflineRecognizer(assetManager, asrEnCfg)
   ```

4. **Assets are auto-deployed** by
   `ml/scripts/speech/export_mobile_speech.py` into
   `android/app/src/main/assets/speech/{asr,tts,mt}/<component_id>/`.

## Dart channel contract

See `lib/core/speech/sherpa_channel.dart`. Methods implemented:

| Method | Args | Return |
|---|---|---|
| `init` | asr_en, asr_lg, tts_en, tts_lg, mt, vad, sample_rate, num_threads | `bool` |
| `transcribe` | audio: List\<int\> (int16 PCM), language: str | `{text, confidence, backend}` |
| `push_audio` | audio: List\<int\> | void |
| `end_utterance` | - | void |
| `synthesize` | text, language, voice? | `{pcm: List<int>, sample_rate, backend}` |
| `translate` | text, source_lang, target_lang | `{text, backend}` |
| `detect_language` | text | `{lang, confidence}` |
| `release` | - | void |

EventChannels:

| Channel | Events |
|---|---|
| `app.ura.sherpa_onnx/asr_stream` | `{text, is_final, confidence, backend}` |
| `app.ura.sherpa_onnx/tts_stream` | `{pcm, sample_rate, is_final, backend}` |

## Status

**Scaffolded.** The Dart layer is runnable; the native side ships in a
follow-up task. Until the native plugin is registered, `SherpaChannel`
methods return mock / passthrough values so the rest of the app does
not crash.
