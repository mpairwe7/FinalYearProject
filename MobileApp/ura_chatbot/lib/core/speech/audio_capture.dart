/// PCM16 microphone capture.
///
/// Wraps the `record` package to expose a clean `Stream<Uint8List>`
/// of PCM16 samples at a fixed 16 kHz, mono — the canonical input
/// format for Whisper, Omnilingual ASR, and Silero VAD.
///
/// Two design choices worth noting:
///
/// 1. **Never writes to disk.** The `record` package supports file
///    recording but we want voice biometric audio to be ephemeral
///    per our compliance story. Every chunk lives in RAM only.
///
/// 2. **Caller-owned stop.** `start()` returns a stream that runs
///    until the caller explicitly calls `stop()` — there is no
///    built-in endpointing here. Endpointing is the job of the VAD
///    layer on top, so this class stays single-responsibility.
library;

import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:record/record.dart';

/// A chunk of PCM16 little-endian samples captured from the mic.
@immutable
class AudioChunk {
  const AudioChunk({
    required this.bytes,
    required this.sampleRate,
    required this.capturedAtMs,
  });

  final Uint8List bytes;
  final int sampleRate;
  final int capturedAtMs;

  /// Number of 16-bit samples in this chunk.
  int get sampleCount => bytes.lengthInBytes ~/ 2;

  /// Duration of this chunk in milliseconds (at its declared sample rate).
  double get durationMs => (sampleCount * 1000.0) / sampleRate;
}

/// Signals surfaced by [AudioCapture] so callers can show UI state.
enum CaptureState { idle, starting, running, stopping, error }

/// Immutable error description — mirrors `DioException`-style class.
@immutable
class AudioCaptureException implements Exception {
  const AudioCaptureException(this.message, {this.cause});
  final String message;
  final Object? cause;

  @override
  String toString() => 'AudioCaptureException: $message';
}

/// Streaming PCM16 mic capture built on top of the `record` package.
class AudioCapture {
  AudioCapture({@visibleForTesting AudioRecorder? recorder})
    : _recorder = recorder ?? AudioRecorder();

  final AudioRecorder _recorder;
  StreamSubscription<Uint8List>? _rawSub;
  StreamController<AudioChunk>? _chunksController;
  CaptureState _state = CaptureState.idle;

  CaptureState get state => _state;

  /// Start capture and return a broadcast stream of PCM16 chunks.
  /// Throws [AudioCaptureException] if the recorder cannot be opened
  /// (permission denied, device busy, platform error).
  ///
  /// Each chunk is delivered as the underlying package emits it —
  /// on mid-range Android this is typically ~32 ms worth of samples.
  Future<Stream<AudioChunk>> start({
    int sampleRate = 16000,
    int numChannels = 1,
  }) async {
    if (_state == CaptureState.running || _state == CaptureState.starting) {
      throw const AudioCaptureException('already running');
    }
    _state = CaptureState.starting;

    try {
      if (!await _recorder.hasPermission()) {
        _state = CaptureState.error;
        throw const AudioCaptureException('microphone permission not granted');
      }

      final rawStream = await _recorder.startStream(
        RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: sampleRate,
          numChannels: numChannels,
          noiseSuppress: true,
          echoCancel: true,
          autoGain: true,
        ),
      );

      _chunksController = StreamController<AudioChunk>.broadcast(
        onCancel: stop,
      );
      _rawSub = rawStream.listen(
        (bytes) {
          _chunksController?.add(
            AudioChunk(
              bytes: bytes,
              sampleRate: sampleRate,
              capturedAtMs: DateTime.now().millisecondsSinceEpoch,
            ),
          );
        },
        onError: (Object err, StackTrace st) {
          _chunksController?.addError(
            AudioCaptureException('recorder error', cause: err),
            st,
          );
        },
        onDone: () async {
          await _chunksController?.close();
          _chunksController = null;
          _state = CaptureState.idle;
        },
      );

      _state = CaptureState.running;
      return _chunksController!.stream;
    } catch (e, s) {
      _state = CaptureState.error;
      await _cleanup();
      if (e is AudioCaptureException) rethrow;
      Error.throwWithStackTrace(
        AudioCaptureException('failed to start capture', cause: e),
        s,
      );
    }
  }

  /// Stop capture. Safe to call multiple times; a no-op when idle.
  Future<void> stop() async {
    if (_state == CaptureState.idle || _state == CaptureState.stopping) {
      return;
    }
    _state = CaptureState.stopping;
    await _cleanup();
    _state = CaptureState.idle;
  }

  /// Release any native resources. Call from your widget's `dispose`.
  Future<void> dispose() async {
    await stop();
    await _recorder.dispose();
  }

  Future<void> _cleanup() async {
    try {
      await _rawSub?.cancel();
    } catch (_) {}
    _rawSub = null;
    try {
      await _recorder.stop();
    } catch (_) {}
    try {
      await _chunksController?.close();
    } catch (_) {}
    _chunksController = null;
  }
}
