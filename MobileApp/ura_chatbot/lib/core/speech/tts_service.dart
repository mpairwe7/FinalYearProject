/// Streaming TTS service (2026).
///
/// Sits between the LLM output stream and the native audio player.
/// Responsibilities:
///
///   * Chunk incoming LLM text at sentence boundaries.
///   * Dispatch each chunk through `SherpaChannel.synthesizeStream`.
///   * Emit audio frames to callers as soon as they are produced.
///   * Barge-in: cancel pending synthesis when the user starts speaking.
library;

import 'dart:async';
import 'dart:typed_data';

import 'sherpa_channel.dart';
import 'speech_config.dart';

class TtsFrame {
  const TtsFrame({
    required this.pcm,
    required this.sampleRate,
    required this.isFinal,
    this.backend = 'unknown',
  });

  final Int16List pcm;
  final int sampleRate;
  final bool isFinal;
  final String backend;
}

class TtsService {
  TtsService({SpeechPipelineConfig? config, SherpaChannel? channel})
    : _config = config ?? const SpeechPipelineConfig(),
      _channel = channel ?? SherpaChannel.instance;

  final SpeechPipelineConfig _config;
  final SherpaChannel _channel;
  StreamController<TtsFrame>? _currentController;
  StreamSubscription<Map<String, dynamic>>? _currentSub;

  /// Synthesise [text] as a stream of PCM frames, chunked at sentence
  /// boundaries. A new call to [synthesise] cancels the previous stream
  /// (barge-in behaviour) when [SpeechPipelineConfig.bargeInEnabled] is true.
  Stream<TtsFrame> synthesise({
    required String text,
    String language = 'en',
    String? voice,
  }) {
    if (_config.bargeInEnabled) {
      _cancelCurrent();
    }

    final controller = StreamController<TtsFrame>();
    _currentController = controller;
    final component = voice ?? _config.tts.componentFor(language);

    _currentSub = _channel
        .synthesizeStream(text: text, language: language, voice: component)
        .listen(
          (event) {
            final rawList = (event['pcm'] as List?) ?? const [];
            final pcm = Int16List.fromList(rawList.cast<int>());
            controller.add(
              TtsFrame(
                pcm: pcm,
                sampleRate: (event['sample_rate'] as int?) ?? 22050,
                isFinal: (event['is_final'] as bool?) ?? false,
                backend: (event['backend'] as String?) ?? 'unknown',
              ),
            );
          },
          onError: controller.addError,
          onDone: controller.close,
        );

    return controller.stream;
  }

  /// One-shot non-streaming synthesis — returns the full PCM buffer.
  Future<TtsFrame> synthesiseOnce({
    required String text,
    String language = 'en',
    String? voice,
  }) async {
    final component = voice ?? _config.tts.componentFor(language);
    final m = await _channel.synthesize(
      text: text,
      language: language,
      voice: component,
    );
    final rawList = (m['pcm'] as List?) ?? const [];
    return TtsFrame(
      pcm: Int16List.fromList(rawList.cast<int>()),
      sampleRate: (m['sample_rate'] as int?) ?? 22050,
      isFinal: true,
      backend: (m['backend'] as String?) ?? 'unknown',
    );
  }

  /// Cancel any active streaming synthesis — used for barge-in.
  void cancel() {
    _cancelCurrent();
  }

  void _cancelCurrent() {
    _currentSub?.cancel();
    _currentSub = null;
    final c = _currentController;
    if (c != null && !c.isClosed) {
      c.close();
    }
    _currentController = null;
  }

  Future<void> dispose() async {
    _cancelCurrent();
  }
}
