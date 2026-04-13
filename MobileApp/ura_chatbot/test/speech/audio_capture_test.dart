import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:ura_chatbot/core/speech/audio_capture.dart';
import 'package:ura_chatbot/core/speech/vad.dart';

/// These tests exercise the pure-Dart pipeline helpers without
/// touching the `record` platform channel. They verify that the
/// RMS VAD detects a loud chunk, stays silent on quiet chunks, and
/// emits a SpeechEnd event after the configured silence window.
void main() {
  group('AudioChunk', () {
    test('computes sample count and duration', () {
      // 3200 samples × 2 bytes = 6400 bytes at 16 kHz = 200 ms.
      final chunk = AudioChunk(
        bytes: Uint8List(6400),
        sampleRate: 16000,
        capturedAtMs: 0,
      );
      expect(chunk.sampleCount, equals(3200));
      expect(chunk.durationMs, equals(200));
    });
  });

  group('RmsVad', () {
    late RmsVad vad;

    setUp(() {
      vad = RmsVad(
        config: const VadConfig(
          minSilenceDurationMs: 100,
          maxSegmentDurationMs: 5000,
        ),
        rmsThreshold: 100,
      );
    });

    AudioChunk makeChunk(double amplitude, int atMs) {
      // 160 samples = 10 ms @ 16 kHz.
      final bytes = Uint8List(320);
      final bd = bytes.buffer.asByteData();
      for (var i = 0; i < 160; i++) {
        bd.setInt16(
          i * 2,
          (amplitude * 32767).toInt().clamp(-32768, 32767),
          Endian.little,
        );
      }
      return AudioChunk(bytes: bytes, sampleRate: 16000, capturedAtMs: atMs);
    }

    test('silent chunks yield no events', () {
      final events = vad.feed(makeChunk(0, 0)).toList();
      expect(events, isEmpty);
    });

    test('loud chunk triggers SpeechStart + SpeechChunk', () {
      final events = vad.feed(makeChunk(0.5, 10)).toList();
      expect(events, hasLength(2));
      expect(events.first, isA<SpeechStart>());
      expect(events.last, isA<SpeechChunk>());
    });

    test('silence after speech triggers SpeechEnd', () {
      // Open the segment with a loud chunk, then feed silence until
      // the detector times out.
      vad.feed(makeChunk(0.5, 0)).toList();
      vad.feed(makeChunk(0, 50)).toList();
      final endEvents = vad.feed(makeChunk(0, 200)).toList();
      final ends = endEvents.whereType<SpeechEnd>().toList();
      expect(ends, hasLength(1));
      expect(ends.first.utterance.lengthInBytes, greaterThan(0));
    });

    test('max segment duration forces an end even under sustained speech', () {
      vad.feed(makeChunk(0.5, 0)).toList();
      vad.feed(makeChunk(0.5, 100)).toList();
      final ends = vad
          .feed(makeChunk(0.5, 6000))
          .whereType<SpeechEnd>()
          .toList();
      expect(ends, hasLength(1));
    });
  });
}
