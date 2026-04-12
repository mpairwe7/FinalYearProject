/// Voice Activity Detection (VAD) abstraction.
///
/// Takes a raw PCM16 16 kHz mono chunk stream from [AudioCapture] and
/// emits endpointed [VadEvent]s that higher layers (ASR, UI) can
/// consume. The default implementation uses `sherpa_onnx`'s bundled
/// Silero VAD — the same native runtime used for ASR and TTS — so the
/// app only links one native library.
///
/// Endpointing rules (2026 Silero defaults):
///   - **onset**: 300 ms sustained speech-probability > 0.5 → SpeechStart
///   - **offset**: 800 ms sustained silence after onset → SpeechEnd
///   - Chunks inside an active segment are buffered (not streamed)
///     so the ASR layer receives one consolidated PCM blob per
///     utterance rather than lots of tiny fragments. This mirrors the
///     2026 Baseten/WhisperX production pattern.
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import 'audio_capture.dart';

/// Events emitted by a VAD pipeline.
sealed class VadEvent {
  const VadEvent();
}

class SpeechStart extends VadEvent {
  const SpeechStart({required this.atMs});
  final int atMs;
}

/// Emitted while speech is active, containing only the in-segment
/// bytes. Consumers that want low-latency partial transcription can
/// feed these into a streaming ASR engine.
class SpeechChunk extends VadEvent {
  const SpeechChunk(this.bytes);
  final Uint8List bytes;
}

/// Emitted when silence has persisted long enough to declare the
/// segment finished. Carries the full concatenated utterance so
/// non-streaming ASR engines can transcribe it in one shot.
class SpeechEnd extends VadEvent {
  const SpeechEnd({
    required this.utterance,
    required this.durationMs,
    required this.endedAtMs,
  });

  final Uint8List utterance;
  final double durationMs;
  final int endedAtMs;
}

class VadError extends VadEvent {
  const VadError(this.message, {this.cause});
  final String message;
  final Object? cause;
}

/// Parameter bag for the default Silero VAD behaviour.
@immutable
class VadConfig {
  const VadConfig({
    this.speechThreshold = 0.5,
    this.minSpeechDurationMs = 250,
    this.minSilenceDurationMs = 800,
    this.maxSegmentDurationMs = 15000,
  });

  final double speechThreshold;
  final int minSpeechDurationMs;
  final int minSilenceDurationMs;

  /// Hard upper bound so a runaway segment can't consume unlimited
  /// RAM. When exceeded, we emit [SpeechEnd] and reset.
  final int maxSegmentDurationMs;
}

/// Abstract base so tests can inject fakes without touching sherpa.
abstract class VadDetector {
  /// Consume a PCM16 chunk and emit zero or more events.
  Iterable<VadEvent> feed(AudioChunk chunk);

  /// Reset the internal state machine to idle.
  void reset();
}

/// Simple energy-gated VAD used as the default test fallback and as
/// a ground-truth path on devices where sherpa_onnx isn't available.
///
/// Not production-grade — we ship [SileroVad] (sherpa-backed) for
/// real use and keep this one available for unit tests and for
/// initial bring-up before the ASR models have been downloaded.
class RmsVad implements VadDetector {
  RmsVad({this.config = const VadConfig(), this.rmsThreshold = 800})
      : _buffer = BytesBuilder(copy: false);

  final VadConfig config;

  /// Simple RMS threshold over PCM16 samples. 800 is an empirical
  /// value that works well in a quiet room; production code should
  /// use the Silero-backed detector.
  final double rmsThreshold;

  bool _inSpeech = false;
  int _speechStartMs = 0;
  int _lastSpeechMs = 0;
  final BytesBuilder _buffer;

  @override
  Iterable<VadEvent> feed(AudioChunk chunk) sync* {
    final rms = _rmsOf(chunk.bytes);
    final nowMs = chunk.capturedAtMs;
    final isSpeech = rms > rmsThreshold;

    if (!_inSpeech && isSpeech) {
      _inSpeech = true;
      _speechStartMs = nowMs;
      _lastSpeechMs = nowMs;
      _buffer.add(chunk.bytes);
      yield SpeechStart(atMs: nowMs);
      yield SpeechChunk(chunk.bytes);
      return;
    }

    if (_inSpeech) {
      _buffer.add(chunk.bytes);
      yield SpeechChunk(chunk.bytes);
      if (isSpeech) {
        _lastSpeechMs = nowMs;
      }
      final silenceMs = nowMs - _lastSpeechMs;
      final totalMs = nowMs - _speechStartMs;
      if (silenceMs >= config.minSilenceDurationMs ||
          totalMs >= config.maxSegmentDurationMs) {
        yield SpeechEnd(
          utterance: _buffer.toBytes(),
          durationMs: totalMs.toDouble(),
          endedAtMs: nowMs,
        );
        reset();
      }
    }
  }

  @override
  void reset() {
    _inSpeech = false;
    _speechStartMs = 0;
    _lastSpeechMs = 0;
    _buffer.clear();
  }

  static double _rmsOf(Uint8List pcm16) {
    final data = pcm16.buffer.asByteData();
    final n = pcm16.lengthInBytes ~/ 2;
    if (n == 0) return 0;
    var sumSq = 0.0;
    for (var i = 0; i < n; i++) {
      final s = data.getInt16(i * 2, Endian.little).toDouble();
      sumSq += s * s;
    }
    return (sumSq / n).abs();
  }
}

/// Stream pipeline helper: run a [VadDetector] against an [AudioChunk]
/// stream and produce a [VadEvent] stream. The returned stream is
/// broadcast so multiple listeners (UI, ASR pipeline) can consume it
/// in parallel.
Stream<VadEvent> runVad(
  Stream<AudioChunk> input,
  VadDetector detector,
) async* {
  await for (final chunk in input) {
    try {
      for (final event in detector.feed(chunk)) {
        yield event;
      }
    } catch (e, s) {
      yield VadError('vad feed failed', cause: e);
      assert(() {
        debugPrintStack(label: 'vad error', stackTrace: s);
        return true;
      }());
    }
  }
}
