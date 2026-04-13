/// Last-resort English-only TTS via the `flutter_tts` plugin.
///
/// Falls back to the OS text-to-speech engine (Google TTS on Android,
/// AVSpeechSynthesizer on iOS). Neither platform has a native Luganda
/// voice, so this engine is strictly English-only.
///
/// Unlike [MmsVitsEngine], this engine plays audio directly through the
/// system speaker (the plugin doesn't expose raw PCM), so [synthesize]
/// returns a zero-length buffer with metadata only — the caller should
/// use [speak] instead.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_tts/flutter_tts.dart';

import 'tts_engine.dart';

class NativeTtsEngine implements TtsEngine {
  NativeTtsEngine({@visibleForTesting FlutterTts? tts})
    : _tts = tts ?? FlutterTts();

  final FlutterTts _tts;
  bool _initialized = false;
  bool _available = false;

  @override
  String get id => 'native-flutter-tts';

  @override
  Set<String> get supportedLocales => const {'en'};

  @override
  bool get isAvailable => _initialized && _available;

  @override
  Future<bool> initialize() async {
    if (_initialized) return _available;
    _initialized = true;
    try {
      await _tts.setLanguage('en-US');
      await _tts.setSpeechRate(0.5);
      await _tts.setVolume(1.0);
      await _tts.setPitch(1.0);
      _available = true;
    } catch (_) {
      _available = false;
    }
    return _available;
  }

  /// Synthesize is not supported for raw PCM — the OS TTS plays
  /// directly. Returns null; callers should use [speak] instead.
  @override
  Future<TtsResult?> synthesize({
    required String text,
    required String locale,
  }) async {
    return null;
  }

  /// Play text through the system speaker. Returns a future that
  /// completes when playback is done.
  Future<void> speak(String text) async {
    if (!isAvailable || text.trim().isEmpty) return;
    final completer = Completer<void>();
    _tts.setCompletionHandler(() {
      if (!completer.isCompleted) completer.complete();
    });
    await _tts.speak(text);
    await completer.future;
  }

  Future<void> stop() async {
    await _tts.stop();
  }

  @override
  Future<void> dispose() async {
    await _tts.stop();
    _initialized = false;
    _available = false;
  }
}
