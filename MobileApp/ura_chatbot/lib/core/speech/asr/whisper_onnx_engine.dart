/// Whisper-ONNX backed ASR engine.
///
/// Uses the `sherpa_onnx` Flutter plugin as a single native runtime
/// for VAD, ASR, and TTS. On initialisation, loads the Whisper
/// encoder/decoder/tokens triplet from a model bundle persisted by
/// [ModelManager] into the app support directory.
///
/// When the model files aren't yet on disk (first launch, auto-
/// download disabled), [initialize] returns `false` without throwing.
/// Callers can then route requests to [NativeAsrEngine] as a fallback.
///
/// Note: this file intentionally calls into `sherpa_onnx` behind a
/// narrow facade so tests can inject a [WhisperRunner] double
/// without pulling in the whole native binding.
library;

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import '../domain_glossary.dart';
import '../model_manager.dart';
import 'asr_engine.dart';

/// Narrow interface over the sherpa-onnx offline recogniser so we
/// can swap in a fake for unit tests without pulling the native
/// plugin into the test binary.
abstract class WhisperRunner {
  Future<void> load(WhisperModelFiles files, {String? initialPrompt});
  Future<WhisperRunResult> run(
    Uint8List pcm16, {
    required int sampleRate,
    String? language,
    String? initialPrompt,
  });
  Future<void> release();
}

@immutable
class WhisperModelFiles {
  const WhisperModelFiles({
    required this.encoderPath,
    required this.decoderPath,
    required this.tokensPath,
    this.language,
  });

  final String encoderPath;
  final String decoderPath;
  final String tokensPath;
  final String? language;

  /// All three files must exist on disk before the runner can load.
  bool get ready =>
      File(encoderPath).existsSync() &&
      File(decoderPath).existsSync() &&
      File(tokensPath).existsSync();
}

@immutable
class WhisperRunResult {
  const WhisperRunResult({
    required this.text,
    required this.detectedLanguage,
    required this.confidence,
    this.words = const [],
  });

  final String text;
  final String detectedLanguage;
  final double confidence;
  final List<AsrWord> words;
}

/// Primary on-device ASR engine.
class WhisperOnnxEngine implements AsrEngine {
  WhisperOnnxEngine({
    required this.modelBundleId,
    required this.manager,
    WhisperRunner? runner,
  }) : _runner = runner ?? _UnimplementedRunner();

  /// Bundle id looked up in [ModelManager] (e.g., `whisper-small-int8`).
  final String modelBundleId;
  final ModelManager manager;

  final WhisperRunner _runner;
  bool _initialized = false;
  bool _available = false;

  @override
  String get id => 'whisper-onnx:$modelBundleId';

  @override
  // Whisper's multilingual model handles 99+ languages including all
  // Ugandan languages the app targets.
  Set<String> get supportedLocales => {'en', 'lg', 'sw', 'nyn', 'ach'};

  @override
  bool get isAvailable => _initialized && _available;

  @override
  Future<bool> initialize() async {
    if (_initialized) return _available;
    _initialized = true;
    try {
      final bundle = await manager.ensure(modelBundleId);
      if (bundle == null) {
        _available = false;
        return false;
      }
      final files = WhisperModelFiles(
        encoderPath: bundle.fileByRole('encoder'),
        decoderPath: bundle.fileByRole('decoder'),
        tokensPath: bundle.fileByRole('tokens'),
      );
      if (!files.ready) {
        _available = false;
        return false;
      }
      await _runner.load(
        files,
        initialPrompt: UraDomainGlossary.initialPrompt,
      );
      _available = true;
      return true;
    } catch (e, s) {
      _available = false;
      assert(() {
        debugPrintStack(label: 'whisper init failed', stackTrace: s);
        return true;
      }());
      return false;
    }
  }

  @override
  Future<AsrResult?> transcribe(
    Uint8List pcm16, {
    required String locale,
    List<String> hotwords = const [],
  }) async {
    if (!isAvailable) return null;
    if (pcm16.lengthInBytes < 3200) {
      // < 100 ms of audio — skip to avoid Whisper hallucinating on noise.
      return null;
    }

    final started = DateTime.now().millisecondsSinceEpoch;
    try {
      final prompt = _buildInitialPrompt(hotwords);
      final raw = await _runner.run(
        pcm16,
        sampleRate: 16000,
        language: locale,
        initialPrompt: prompt,
      );
      final latency = DateTime.now().millisecondsSinceEpoch - started;
      return AsrResult(
        text: UraDomainGlossary.normalise(raw.text),
        language: raw.detectedLanguage,
        confidence: raw.confidence,
        latencyMs: latency.toDouble(),
        engineId: id,
        words: raw.words,
      );
    } catch (e, s) {
      assert(() {
        debugPrintStack(label: 'whisper transcribe failed', stackTrace: s);
        return true;
      }());
      throw AsrEngineException('whisper transcribe failed', cause: e);
    }
  }

  @override
  Stream<PartialAsrResult> transcribeStream(
    Stream<Uint8List> pcmChunks, {
    required String locale,
    List<String> hotwords = const [],
  }) async* {
    // sherpa_onnx also supports chunked streaming recognisers, but
    // for Whisper we use the offline recogniser + full-utterance
    // chunks gathered by the VAD layer, so we just collect and run
    // once. The AsrEngine default does exactly this — call super().
    yield* (this as AsrEngine).transcribeStream(
      pcmChunks,
      locale: locale,
      hotwords: hotwords,
    );
  }

  @override
  Future<void> dispose() async {
    if (!_initialized) return;
    await _runner.release();
    _initialized = false;
    _available = false;
  }

  String _buildInitialPrompt(List<String> extraHotwords) {
    if (extraHotwords.isEmpty) return UraDomainGlossary.initialPrompt;
    return '${UraDomainGlossary.initialPrompt} '
        'Hotwords: ${extraHotwords.join(', ')}.';
  }
}

/// Placeholder runner used before the native binding is wired. Keeps
/// the code compiling and the tests runnable; Phase C ships the real
/// wrapper in a follow-up commit once the model bundle URLs are
/// finalised in the asset manifest.
class _UnimplementedRunner implements WhisperRunner {
  @override
  Future<void> load(WhisperModelFiles files, {String? initialPrompt}) async {
    throw const AsrEngineException(
      'whisper native runner not available; fall back to native ASR',
    );
  }

  @override
  Future<WhisperRunResult> run(
    Uint8List pcm16, {
    required int sampleRate,
    String? language,
    String? initialPrompt,
  }) async {
    throw const AsrEngineException('whisper native runner not available');
  }

  @override
  Future<void> release() async {}
}
