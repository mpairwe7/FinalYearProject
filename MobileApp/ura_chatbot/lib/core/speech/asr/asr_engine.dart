/// Abstract ASR engine interface.
///
/// Mirrors the [OnDeviceLlm] shape at `lib/core/inference/on_device_llm.dart`
/// so consumers see the same `initialize` / `isAvailable` / `dispose`
/// lifecycle for every offline model in the app. Concrete engines:
///
///   - [WhisperOnnxEngine] — primary on-device path (sherpa_onnx + Whisper)
///   - [NativeAsrEngine]  — legacy OS speech_to_text fallback (English)
///
/// The [AsrRouter] picks between them based on {locale, availability,
/// model download state}.
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

/// Immutable result from [AsrEngine.transcribe].
@immutable
class AsrResult {
  const AsrResult({
    required this.text,
    required this.language,
    required this.confidence,
    required this.latencyMs,
    required this.engineId,
    this.words = const [],
  });

  final String text;
  final String language;

  /// Engine-reported confidence in [0, 1]. `null` for engines that
  /// don't surface one (we substitute 0.5 so the router treats them
  /// as "neutral").
  final double confidence;
  final double latencyMs;
  final String engineId;

  /// Optional per-word timestamps. Only populated by engines that
  /// return them (Whisper with `word_timestamps=true`).
  final List<AsrWord> words;
}

@immutable
class AsrWord {
  const AsrWord({
    required this.text,
    required this.startMs,
    required this.endMs,
  });

  final String text;
  final double startMs;
  final double endMs;
}

/// Streaming partial produced during [AsrEngine.transcribeStream].
/// `isFinal=true` means "no more updates, treat this as the final
/// transcript for the current utterance".
@immutable
class PartialAsrResult {
  const PartialAsrResult({
    required this.text,
    required this.isFinal,
    required this.language,
  });

  final String text;
  final bool isFinal;
  final String language;
}

@immutable
class AsrEngineException implements Exception {
  const AsrEngineException(this.message, {this.cause});
  final String message;
  final Object? cause;

  @override
  String toString() => 'AsrEngineException: $message';
}

/// Base class every ASR backend implements.
abstract class AsrEngine {
  /// Stable identifier used in audit ledger rows and analytics events.
  String get id;

  /// Locale codes (`en`, `lg`, `sw`, …) that this engine can handle.
  /// An empty set means "no locales" — the router will skip it.
  Set<String> get supportedLocales;

  /// Cheap boolean: `true` after a successful [initialize]. Safe to
  /// read from any thread / isolate.
  bool get isAvailable;

  /// Prepare the engine for use. Idempotent — subsequent calls return
  /// the cached availability status. Never throws; failures flip
  /// [isAvailable] to `false` so callers can gracefully fall back.
  Future<bool> initialize();

  /// Transcribe a one-shot PCM16 little-endian mono 16 kHz buffer.
  /// Returns `null` if the engine is unavailable or the audio is
  /// empty / too short.
  Future<AsrResult?> transcribe(
    Uint8List pcm16, {
    required String locale,
    List<String> hotwords = const [],
  });

  /// Stream partial transcripts as audio arrives. The default
  /// implementation collects the stream into a single buffer and
  /// calls [transcribe] at the end — subclasses override for real
  /// streaming where the underlying backend supports it.
  Stream<PartialAsrResult> transcribeStream(
    Stream<Uint8List> pcmChunks, {
    required String locale,
    List<String> hotwords = const [],
  }) async* {
    final buffer = BytesBuilder(copy: false);
    await for (final chunk in pcmChunks) {
      buffer.add(chunk);
    }
    final result = await transcribe(
      buffer.toBytes(),
      locale: locale,
      hotwords: hotwords,
    );
    if (result != null) {
      yield PartialAsrResult(
        text: result.text,
        isFinal: true,
        language: result.language,
      );
    }
  }

  /// Release native resources. Safe to call even if [initialize] was
  /// never invoked.
  Future<void> dispose();
}
