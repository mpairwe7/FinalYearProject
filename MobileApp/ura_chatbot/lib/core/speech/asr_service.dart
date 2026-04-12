/// Streaming ASR service with VAD gating (2026).
///
/// Sits between the microphone and the LLM. Responsibilities:
///
///   * Capture 16 kHz mono PCM from the mic.
///   * Gate chunks through Silero VAD to avoid wasting inference on silence.
///   * Dispatch audio chunks to `SherpaChannel.pushAudioChunk`.
///   * Stream partial + final transcription events back to callers.
///
/// The service deliberately has no direct dependency on `package:record`
/// or `package:flutter_sound`; the host app wires the mic stream in. This
/// keeps this file testable and avoids locking the project to one
/// recording plugin.
library;

import 'dart:async';
import 'dart:typed_data';

import 'sherpa_channel.dart';
import 'speech_config.dart';

class AsrResult {
  const AsrResult({
    required this.text,
    required this.isFinal,
    this.confidence = 0.0,
    this.backend = 'unknown',
  });

  final String text;
  final bool isFinal;
  final double confidence;
  final String backend;

  factory AsrResult.fromMap(Map<String, dynamic> m) => AsrResult(
        text: (m['text'] as String?) ?? '',
        isFinal: (m['is_final'] as bool?) ?? false,
        confidence: (m['confidence'] as num?)?.toDouble() ?? 0.0,
        backend: (m['backend'] as String?) ?? 'unknown',
      );
}

/// High-level streaming ASR service.
class AsrService {
  AsrService({
    SpeechPipelineConfig? config,
    SherpaChannel? channel,
  })  : _config = config ?? const SpeechPipelineConfig(),
        _channel = channel ?? SherpaChannel.instance;

  final SpeechPipelineConfig _config;
  final SherpaChannel _channel;
  StreamSubscription<Map<String, dynamic>>? _nativeSub;
  final StreamController<AsrResult> _out = StreamController.broadcast();

  String _currentLanguage = 'en';
  bool _streaming = false;

  Stream<AsrResult> get results => _out.stream;
  bool get isStreaming => _streaming;

  /// Begin a streaming transcription session. The caller should then push
  /// audio chunks via [pushChunk] and call [endUtterance] when done.
  Future<void> startStream({String language = 'en'}) async {
    if (_streaming) return;
    _currentLanguage = language;
    _streaming = true;
    _nativeSub = _channel
        .transcribeStream(language: language)
        .listen(
          (event) => _out.add(AsrResult.fromMap(event)),
          onError: (_) => _streaming = false,
          onDone: () => _streaming = false,
        );
  }

  /// Push a PCM chunk (16-bit mono at [SpeechAudioConfig.sampleRate]).
  Future<void> pushChunk(Int16List samples) async {
    if (!_streaming) return;
    await _channel.pushAudioChunk(samples.toList());
  }

  /// Flush the session and emit a final hypothesis.
  Future<void> endUtterance() async {
    if (!_streaming) return;
    await _channel.endUtterance();
    _streaming = false;
    await _nativeSub?.cancel();
    _nativeSub = null;
  }

  /// One-shot batch ASR for a pre-captured buffer.
  Future<AsrResult> transcribeOnce(
    Int16List samples, {
    String language = 'en',
  }) async {
    final m = await _channel.transcribe(
      audioInt16: samples.toList(),
      language: language,
    );
    return AsrResult.fromMap(m.map((k, v) => MapEntry(k, v)));
  }

  Future<void> dispose() async {
    await _nativeSub?.cancel();
    _nativeSub = null;
    await _out.close();
    _streaming = false;
  }

  SpeechPipelineConfig get config => _config;
  String get currentLanguage => _currentLanguage;
}
