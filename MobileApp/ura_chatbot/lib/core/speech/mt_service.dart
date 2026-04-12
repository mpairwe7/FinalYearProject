/// On-device translation service (2026).
///
/// Wraps `SherpaChannel.translate` which dispatches to the bundled
/// MADLAD-400 student ONNX (Apache-2.0). When the native runtime is
/// unavailable this service returns a passthrough so the pipeline never
/// hard-fails.
library;

import 'sherpa_channel.dart';

class MtResult {
  const MtResult({
    required this.text,
    required this.sourceLang,
    required this.targetLang,
    this.latencyMs = 0.0,
    this.backend = 'unknown',
    this.error,
  });

  final String text;
  final String sourceLang;
  final String targetLang;
  final double latencyMs;
  final String backend;
  final String? error;
}

class MtService {
  MtService({SherpaChannel? channel}) : _channel = channel ?? SherpaChannel.instance;

  final SherpaChannel _channel;

  Future<MtResult> translate({
    required String text,
    required String sourceLang,
    required String targetLang,
  }) async {
    if (sourceLang == targetLang || text.isEmpty) {
      return MtResult(
        text: text,
        sourceLang: sourceLang,
        targetLang: targetLang,
        backend: 'passthrough',
      );
    }
    final m = await _channel.translate(
      text: text,
      sourceLang: sourceLang,
      targetLang: targetLang,
    );
    return MtResult(
      text: (m['text'] as String?) ?? text,
      sourceLang: sourceLang,
      targetLang: targetLang,
      latencyMs: (m['latency_ms'] as num?)?.toDouble() ?? 0.0,
      backend: (m['backend'] as String?) ?? 'unknown',
      error: m['error'] as String?,
    );
  }
}
