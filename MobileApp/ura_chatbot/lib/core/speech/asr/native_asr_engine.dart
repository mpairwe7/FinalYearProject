/// Legacy OS speech-to-text fallback.
///
/// Wraps the existing `speech_to_text` plugin, which routes to
/// Android's `SpeechRecognizer` (Google) and iOS's `SFSpeechRecognizer`.
/// We deliberately scope this engine to **English only** — neither
/// platform has reliable Luganda support, and the old voice button's
/// `lg_UG` locale was silently failing on most devices.
///
/// This engine is used only as the last-resort emergency path when
/// the Whisper ONNX model is unavailable (not downloaded, or the
/// device doesn't support the sherpa runtime).
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

import 'asr_engine.dart';

class NativeAsrEngine implements AsrEngine {
  NativeAsrEngine({@visibleForTesting stt.SpeechToText? speech})
      : _speech = speech ?? stt.SpeechToText();

  final stt.SpeechToText _speech;
  bool _initialized = false;
  bool _available = false;

  @override
  String get id => 'native-speech-to-text';

  @override
  Set<String> get supportedLocales => const {'en'};

  @override
  bool get isAvailable => _initialized && _available;

  @override
  Future<bool> initialize() async {
    if (_initialized) return _available;
    _initialized = true;
    try {
      _available = await _speech.initialize(
        onError: (_) {},
        onStatus: (_) {},
      );
    } catch (_) {
      _available = false;
    }
    return _available;
  }

  /// One-shot transcription from a PCM16 buffer — **not supported**.
  /// The `speech_to_text` plugin only exposes a streaming API backed
  /// by the OS recogniser; it cannot be fed a pre-recorded byte
  /// buffer. Callers that reach this engine must use
  /// [transcribeStream] instead, or drop back to Whisper once the
  /// model is downloaded.
  @override
  Future<AsrResult?> transcribe(
    Uint8List pcm16, {
    required String locale,
    List<String> hotwords = const [],
  }) async {
    // The OS recogniser cannot accept raw PCM buffers, so we can't
    // return a transcription for a completed utterance that was
    // captured by our own `AudioCapture`. Returning null signals the
    // router to try the next engine in line.
    return null;
  }

  @override
  Stream<PartialAsrResult> transcribeStream(
    Stream<Uint8List> pcmChunks, {
    required String locale,
    List<String> hotwords = const [],
  }) async* {
    if (!isAvailable || locale != 'en') return;
    final controller = StreamController<PartialAsrResult>();
    try {
      await _speech.listen(
        localeId: 'en_US',
        listenOptions: stt.SpeechListenOptions(
          listenMode: stt.ListenMode.dictation,
          partialResults: true,
        ),
        onResult: (result) {
          controller.add(
            PartialAsrResult(
              text: result.recognizedWords,
              isFinal: result.finalResult,
              language: 'en',
            ),
          );
          if (result.finalResult) {
            controller.close();
          }
        },
      );
      // Drain the PCM stream so AudioCapture keeps running in
      // lockstep with the OS recogniser. The bytes themselves are
      // discarded — the OS recogniser reads from the mic directly.
      // ignore: unused_local_variable
      await for (final _ in pcmChunks) {
        if (controller.isClosed) break;
      }
    } catch (e) {
      controller.addError(
        AsrEngineException('native asr stream failed', cause: e),
      );
      await controller.close();
    }
    yield* controller.stream;
  }

  @override
  Future<void> dispose() async {
    if (!_initialized) return;
    try {
      await _speech.cancel();
    } catch (_) {}
    _initialized = false;
    _available = false;
  }
}
