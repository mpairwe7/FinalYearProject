/// Abstract TTS engine interface.
///
/// Mirrors the [AsrEngine] lifecycle (initialize / isAvailable / dispose)
/// so all offline speech models in the app follow the same shape.
///
/// Concrete engines:
///
///   - [MmsVitsEngine]   — primary on-device VITS (MMS-TTS, English + Luganda)
///   - [NativeTtsEngine] — last-resort `flutter_tts` fallback (English only)
library;

import 'package:flutter/foundation.dart';

/// Immutable result from [TtsEngine.synthesize].
@immutable
class TtsResult {
  const TtsResult({
    required this.wavBytes,
    required this.sampleRate,
    required this.locale,
    required this.modelId,
    required this.latencyMs,
    this.durationMs = 0,
  });

  /// Raw PCM16 little-endian mono samples (no WAV header).
  final Uint8List wavBytes;
  final int sampleRate;
  final String locale;
  final String modelId;
  final double latencyMs;

  /// Approximate playback duration of the synthesised audio in ms.
  final double durationMs;
}

/// Base class every TTS backend implements.
abstract class TtsEngine {
  /// Stable identifier for audit rows and analytics.
  String get id;

  /// Locale codes this engine can synthesise.
  Set<String> get supportedLocales;

  /// `true` after a successful [initialize].
  bool get isAvailable;

  /// Prepare the engine. Idempotent. Never throws.
  Future<bool> initialize();

  /// Synthesise [text] as PCM16 audio. Returns `null` if the engine
  /// is unavailable or the text is empty.
  Future<TtsResult?> synthesize({
    required String text,
    required String locale,
  });

  /// Release native resources.
  Future<void> dispose();
}
