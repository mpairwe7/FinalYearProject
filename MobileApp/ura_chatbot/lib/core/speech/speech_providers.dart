/// Riverpod wiring for the speech subsystem.
///
/// Everything that touches the microphone, VAD, ASR, or TTS flows
/// through one of these providers so widgets never instantiate the
/// engines directly and tests can override individual pieces.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'asr/asr_engine.dart';
import 'asr/asr_router.dart';
import 'asr/native_asr_engine.dart';
import 'asr/whisper_onnx_engine.dart';
import 'audio_capture.dart';
import 'model_manager.dart';
import 'permissions.dart';
import 'tts/audio_cache.dart';
import 'tts/mms_vits_engine.dart';
import 'tts/native_tts_engine.dart';
import 'tts/playback.dart';
import 'tts/tts_engine.dart';
import 'vad.dart';
import '../compliance/ai_act.dart';

/// Microphone + notification permission helper.
final speechPermissionsProvider = Provider<SpeechPermissions>((ref) {
  return SpeechPermissions();
});

/// Streaming PCM16 audio capture. Held as a long-lived singleton so
/// the recorder isn't repeatedly opened/closed on every mic press
/// (which causes audible clicks on some devices).
final audioCaptureProvider = Provider<AudioCapture>((ref) {
  final capture = AudioCapture();
  ref.onDispose(() async {
    await capture.dispose();
  });
  return capture;
});

/// Default VAD detector. Starts out as the cheap RMS-based detector
/// so Phase B brings up the UI without waiting for sherpa-onnx model
/// download. Phase C overrides this provider with the Silero VAD
/// implementation once its weights are cached on disk.
final vadDetectorProvider = Provider<VadDetector>((ref) {
  return RmsVad();
});

/// Singleton model manager owning the on-device speech bundles.
final modelManagerProvider = Provider<ModelManager>((ref) {
  final mgr = ModelManager();
  ref.onDispose(mgr.dispose);
  return mgr;
});

/// Primary Whisper-ONNX ASR engine. Held as a long-lived singleton so
/// the encoder/decoder aren't re-loaded on every mic press — first
/// transcription costs ~1s of warm-up, subsequent transcriptions are
/// sub-second.
final whisperEngineProvider = Provider<AsrEngine>((ref) {
  return WhisperOnnxEngine(
    modelBundleId: 'whisper-small-int8',
    manager: ref.watch(modelManagerProvider),
  );
});

/// Native OS recogniser, kept for English-only emergency fallback.
final nativeAsrProvider = Provider<AsrEngine>((ref) {
  final engine = NativeAsrEngine();
  ref.onDispose(engine.dispose);
  return engine;
});

/// The router the UI layer actually talks to.
final asrRouterProvider = Provider<AsrRouter>((ref) {
  return AsrRouter(
    primary: ref.watch(whisperEngineProvider),
    fallback: ref.watch(nativeAsrProvider),
  );
});

// ─── TTS providers ──────────────────────────────────────────────────

/// Primary MMS-VITS TTS engine (English + Luganda).
final mmsVitsEngineProvider = Provider<TtsEngine>((ref) {
  return MmsVitsEngine(manager: ref.watch(modelManagerProvider));
});

/// Native OS TTS fallback (English only).
final nativeTtsEngineProvider = Provider<TtsEngine>((ref) {
  final engine = NativeTtsEngine();
  ref.onDispose(engine.dispose);
  return engine;
});

/// The TTS engine the UI actually talks to. Prefers MMS-VITS; the
/// native engine is used only when VITS models are unavailable and
/// the locale is English.
final ttsEngineProvider = Provider<TtsEngine>((ref) {
  return ref.watch(mmsVitsEngineProvider);
});

/// Shared TTS audio cache.
final ttsCacheProvider = Provider<TtsAudioCache>((ref) {
  return TtsAudioCache();
});

/// Singleton playback controller.
final ttsPlaybackProvider = Provider<TtsPlayback>((ref) {
  final playback = TtsPlayback();
  ref.onDispose(playback.dispose);
  return playback;
});

/// Session-scoped AI Act labeler.
final syntheticVoiceLabelerProvider = Provider<SyntheticVoiceLabeler>((ref) {
  return SyntheticVoiceLabeler();
});

/// Whether TTS is enabled in settings.
final ttsEnabledProvider = Provider<bool>((ref) {
  return true; // Default on; overridden by settingsProvider when wired.
});
