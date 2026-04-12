/// Platform channel bindings for sherpa-onnx (2026 production).
///
/// Mirrors the pattern of [OnDeviceLlmConfig] (see `core/inference/on_device_llm.dart`)
/// but targets the unified audio runtime:
///
///     Flutter -> MethodChannel -> JNI/Swift -> sherpa-onnx -> ONNX graphs
///
/// The sherpa-onnx library exposes one runtime for ASR, TTS, VAD and
/// language-ID. Keeping every audio-side call behind this one channel
/// keeps the Dart layer simple and makes the native integration easy to
/// audit (only one FFI surface).
///
/// Asset layout expected on the device (written by
/// `ml/scripts/speech/export_mobile_speech.py`):
///
///   Android:
///     android/app/src/main/assets/speech/asr/<component_id>/
///     android/app/src/main/assets/speech/tts/<component_id>/
///     android/app/src/main/assets/speech/mt/<component_id>/
///
///   iOS:
///     ios/Runner/speech/asr/<component_id>/
///     ios/Runner/speech/tts/<component_id>/
///     ios/Runner/speech/mt/<component_id>/
///
/// Each component is a sherpa-onnx directory containing `model.onnx` +
/// `tokens.txt` + `config.json`.
library;

import 'dart:async';
import 'package:flutter/services.dart';

/// Singleton platform-channel wrapper.
class SherpaChannel {
  SherpaChannel._();
  static final SherpaChannel instance = SherpaChannel._();

  static const MethodChannel _channel = MethodChannel('app.ura.sherpa_onnx');
  static const EventChannel _asrEvents = EventChannel('app.ura.sherpa_onnx/asr_stream');
  static const EventChannel _ttsEvents = EventChannel('app.ura.sherpa_onnx/tts_stream');

  bool _initialised = false;

  bool get initialised => _initialised;

  /// Initialise the sherpa-onnx runtime with the asset paths.
  ///
  /// Returns `true` on success. The native side reads the asset layout
  /// from the app bundle; Dart only passes the component ids.
  Future<bool> init({
    required String asrEnComponent,
    required String asrLgComponent,
    required String ttsEnComponent,
    required String ttsLgComponent,
    required String mtComponent,
    String vadComponent = 'silero-vad',
    int sampleRate = 16000,
    int numThreads = 2,
  }) async {
    try {
      final ok = await _channel.invokeMethod<bool>('init', {
        'asr_en': asrEnComponent,
        'asr_lg': asrLgComponent,
        'tts_en': ttsEnComponent,
        'tts_lg': ttsLgComponent,
        'mt': mtComponent,
        'vad': vadComponent,
        'sample_rate': sampleRate,
        'num_threads': numThreads,
      });
      _initialised = ok ?? false;
      return _initialised;
    } on PlatformException {
      _initialised = false;
      return false;
    } on MissingPluginException {
      // Native plugin not yet registered — the speech pipeline is
      // scaffolded but the native bindings ship separately.
      _initialised = false;
      return false;
    }
  }

  /// One-shot offline ASR. Accepts 16kHz mono int16 PCM.
  Future<Map<String, dynamic>> transcribe({
    required List<int> audioInt16,
    required String language,
  }) async {
    try {
      final result = await _channel.invokeMapMethod<String, dynamic>(
        'transcribe',
        {
          'audio': audioInt16,
          'language': language,
        },
      );
      return result?.cast<String, dynamic>() ??
          const <String, dynamic>{'text': '', 'backend': 'empty'};
    } on PlatformException catch (e) {
      return <String, dynamic>{'text': '', 'error': e.message, 'backend': 'error'};
    } on MissingPluginException {
      return const <String, dynamic>{
        'text': '',
        'backend': 'mock',
        'error': 'sherpa-onnx native plugin not registered',
      };
    }
  }

  /// Streaming ASR: emit PCM chunks, receive partial + final hypotheses.
  ///
  /// Each event on the returned stream is a Map with keys:
  ///   `text`, `is_final`, `confidence`, `backend`.
  Stream<Map<String, dynamic>> transcribeStream({required String language}) {
    return _asrEvents
        .receiveBroadcastStream(<String, Object?>{'language': language})
        .map((event) => Map<String, dynamic>.from(event as Map));
  }

  /// Push a PCM chunk into the active streaming ASR session.
  Future<void> pushAudioChunk(List<int> audioInt16) async {
    try {
      await _channel.invokeMethod<void>('push_audio', {'audio': audioInt16});
    } on MissingPluginException {
      // Ignored in scaffolded builds.
    }
  }

  /// End the active streaming ASR session and flush the final hypothesis.
  Future<void> endUtterance() async {
    try {
      await _channel.invokeMethod<void>('end_utterance');
    } on MissingPluginException {
      // Ignored in scaffolded builds.
    }
  }

  /// Offline TTS. Returns raw 16-bit PCM samples + sample rate.
  Future<Map<String, dynamic>> synthesize({
    required String text,
    required String language,
    String? voice,
  }) async {
    try {
      final result = await _channel.invokeMapMethod<String, dynamic>(
        'synthesize',
        {
          'text': text,
          'language': language,
          if (voice != null) 'voice': voice,
        },
      );
      return result?.cast<String, dynamic>() ??
          const <String, dynamic>{'pcm': <int>[], 'sample_rate': 0, 'backend': 'empty'};
    } on PlatformException catch (e) {
      return <String, dynamic>{'pcm': <int>[], 'error': e.message, 'backend': 'error'};
    } on MissingPluginException {
      return const <String, dynamic>{
        'pcm': <int>[],
        'sample_rate': 0,
        'backend': 'mock',
        'error': 'sherpa-onnx native plugin not registered',
      };
    }
  }

  /// Streaming TTS: yields audio chunks as sentences are synthesised.
  Stream<Map<String, dynamic>> synthesizeStream({
    required String text,
    required String language,
    String? voice,
  }) {
    return _ttsEvents.receiveBroadcastStream(<String, Object?>{
      'text': text,
      'language': language,
      if (voice != null) 'voice': voice,
    }).map((event) => Map<String, dynamic>.from(event as Map));
  }

  /// On-device translation via the MADLAD student ONNX.
  Future<Map<String, dynamic>> translate({
    required String text,
    required String sourceLang,
    required String targetLang,
  }) async {
    try {
      final result = await _channel.invokeMapMethod<String, dynamic>(
        'translate',
        {
          'text': text,
          'source_lang': sourceLang,
          'target_lang': targetLang,
        },
      );
      return result?.cast<String, dynamic>() ??
          const <String, dynamic>{'text': '', 'backend': 'empty'};
    } on PlatformException catch (e) {
      return <String, dynamic>{'text': text, 'error': e.message, 'backend': 'error'};
    } on MissingPluginException {
      return <String, dynamic>{
        'text': text,
        'backend': 'mock',
        'error': 'sherpa-onnx native plugin not registered',
      };
    }
  }

  /// Heuristic language detection for code-switching routing.
  Future<Map<String, dynamic>> detectLanguage(String text) async {
    try {
      final result = await _channel.invokeMapMethod<String, dynamic>(
        'detect_language',
        {'text': text},
      );
      return result?.cast<String, dynamic>() ??
          const <String, dynamic>{'lang': 'en', 'confidence': 0.0};
    } on PlatformException {
      return <String, dynamic>{'lang': 'en', 'confidence': 0.0};
    } on MissingPluginException {
      return <String, dynamic>{'lang': 'en', 'confidence': 0.0};
    }
  }

  Future<void> release() async {
    try {
      await _channel.invokeMethod<void>('release');
    } on MissingPluginException {
      // Ignored.
    }
    _initialised = false;
  }
}
