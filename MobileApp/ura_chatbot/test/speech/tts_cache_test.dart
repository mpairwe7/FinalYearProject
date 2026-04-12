import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:ura_chatbot/core/speech/tts/tts_engine.dart';

void main() {
  test('TtsResult round-trips correctly', () {
    final result = TtsResult(
      wavBytes: Uint8List(4400),
      sampleRate: 22050,
      locale: 'en',
      modelId: 'mms-tts-eng',
      latencyMs: 150,
      durationMs: 100,
    );
    expect(result.sampleRate, 22050);
    expect(result.locale, 'en');
    expect(result.durationMs, 100);
  });

  test('TtsResult carries model metadata', () {
    final result = TtsResult(
      wavBytes: Uint8List(4400),
      sampleRate: 22050,
      locale: 'lg',
      modelId: 'mms-tts-lug',
      latencyMs: 150,
      durationMs: 100,
    );
    expect(result.locale, 'lg');
    expect(result.modelId, 'mms-tts-lug');
  });

  test('TtsResult default durationMs is zero', () {
    final result = TtsResult(
      wavBytes: Uint8List(0),
      sampleRate: 22050,
      locale: 'en',
      modelId: 'mms-tts-eng',
      latencyMs: 0,
    );
    expect(result.durationMs, 0);
  });
}
