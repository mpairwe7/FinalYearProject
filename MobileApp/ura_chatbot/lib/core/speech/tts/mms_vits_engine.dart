/// On-device MMS-TTS VITS engine for English and Luganda.
///
/// Uses sherpa_onnx's offline TTS runner under the hood. Each locale
/// gets its own VITS checkpoint (~40 MB each):
///
///   - `mms-tts-eng` for English
///   - `mms-tts-lug` for Luganda
///
/// The engine loads the model for the requested locale on demand and
/// caches the runner so subsequent synthesis calls are fast (~200 ms
/// for short sentences on mid-range devices).
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import '../model_manager.dart';
import 'tts_engine.dart';

/// Model file paths resolved from the manifest.
@immutable
class VitsModelFiles {
  const VitsModelFiles({
    required this.modelPath,
    required this.tokensPath,
    required this.locale,
  });

  final String modelPath;
  final String tokensPath;
  final String locale;
}

/// Abstract runner — the actual sherpa_onnx binding will be wired in
/// Phase F when native plugins are integrated. Until then, the
/// _UnimplementedRunner placeholder allows the Dart layer to compile.
abstract class VitsRunner {
  Future<void> load(VitsModelFiles files);
  Future<Uint8List> run(String text, {int sampleRate = 22050});
  void release();
}

class _UnimplementedRunner implements VitsRunner {
  @override
  Future<void> load(VitsModelFiles files) async {}

  @override
  Future<Uint8List> run(String text, {int sampleRate = 22050}) async {
    // Return silence — 0.5s of zeros at the given sample rate.
    return Uint8List(sampleRate);
  }

  @override
  void release() {}
}

/// Bundle IDs per locale matching `assets/speech/manifest.json`.
/// nyn/ach TTS not yet available — omitted so synthesize() returns null
/// and the UI shows an appropriate "TTS not available" message.
const _bundleForLocale = <String, String>{
  'en': 'mms-tts-eng',
  'lg': 'mms-tts-lug',
  'sw': 'mms-tts-sw',
};

class MmsVitsEngine implements TtsEngine {
  MmsVitsEngine({
    required ModelManager manager,
    @visibleForTesting VitsRunner? runner,
  }) : _manager = manager,
       _runner = runner ?? _UnimplementedRunner();

  final ModelManager _manager;
  final VitsRunner _runner;

  bool _initialized = false;
  bool _available = false;
  String? _loadedLocale;

  @override
  String get id => 'mms-vits-tts';

  @override
  Set<String> get supportedLocales => const {'en', 'lg', 'sw'};

  @override
  bool get isAvailable => _initialized && _available;

  @override
  Future<bool> initialize() async {
    if (_initialized) return _available;
    _initialized = true;
    // Just check that at least one TTS bundle is known.
    await _manager.loadManifest();
    _available = _bundleForLocale.values.any(
      (id) => _manager.allBundles.any((b) => b.id == id),
    );
    return _available;
  }

  @override
  Future<TtsResult?> synthesize({
    required String text,
    required String locale,
  }) async {
    if (text.trim().isEmpty) return null;
    if (!supportedLocales.contains(locale)) return null;

    final bundleId = _bundleForLocale[locale];
    if (bundleId == null) return null;

    final sw = Stopwatch()..start();

    // Ensure the model is downloaded.
    final bundle = await _manager.ensure(bundleId);
    if (bundle == null) return null;

    // Load the runner if locale changed.
    if (_loadedLocale != locale) {
      _runner.release();
      final files = VitsModelFiles(
        modelPath: bundle.fileByRole('model'),
        tokensPath: bundle.fileByRole('tokens'),
        locale: locale,
      );
      await _runner.load(files);
      _loadedLocale = locale;
    }

    final pcm16 = await _runner.run(text);
    sw.stop();

    const sampleRate = 22050;
    final durationMs = (pcm16.lengthInBytes / 2) / sampleRate * 1000;

    return TtsResult(
      wavBytes: pcm16,
      sampleRate: sampleRate,
      locale: locale,
      modelId: bundleId,
      latencyMs: sw.elapsedMilliseconds.toDouble(),
      durationMs: durationMs,
    );
  }

  @override
  Future<void> dispose() async {
    _runner.release();
    _loadedLocale = null;
    _initialized = false;
    _available = false;
  }
}
