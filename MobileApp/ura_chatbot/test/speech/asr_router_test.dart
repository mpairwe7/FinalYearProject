import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:ura_chatbot/core/speech/asr/asr_engine.dart';
import 'package:ura_chatbot/core/speech/asr/asr_router.dart';

/// Fake ASR engine for testing the router's fallback logic.
class _FakeEngine implements AsrEngine {
  _FakeEngine({
    required this.id,
    required this.supportedLocales,
    this.available = true,
    this.result,
    this.shouldThrow = false,
  });

  @override
  final String id;

  @override
  final Set<String> supportedLocales;

  bool available;
  AsrResult? result;
  bool shouldThrow;
  int transcribeCalls = 0;

  @override
  bool get isAvailable => available;

  @override
  Future<bool> initialize() async => available;

  @override
  Future<AsrResult?> transcribe(
    Uint8List pcm16, {
    required String locale,
    List<String> hotwords = const [],
  }) async {
    transcribeCalls++;
    if (shouldThrow) throw const AsrEngineException('fake error');
    return result;
  }

  @override
  Stream<PartialAsrResult> transcribeStream(
    Stream<Uint8List> pcmChunks, {
    required String locale,
    List<String> hotwords = const [],
  }) async* {
    if (shouldThrow) throw const AsrEngineException('fake stream error');
    final buffer = BytesBuilder(copy: false);
    await for (final chunk in pcmChunks) {
      buffer.add(chunk);
    }
    if (result != null) {
      yield PartialAsrResult(
        text: result!.text,
        isFinal: true,
        language: result!.language,
      );
    }
  }

  @override
  Future<void> dispose() async {}
}

final _dummyPcm = Uint8List(3200); // 100ms of silence at 16kHz

AsrResult _makeResult(String text, {String lang = 'en', String engine = 'test'}) {
  return AsrResult(
    text: text,
    language: lang,
    confidence: 0.9,
    latencyMs: 100,
    engineId: engine,
  );
}

void main() {
  group('AsrRouter', () {
    test('routes English to primary when available', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        result: _makeResult('hello world', engine: 'whisper'),
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      final result = await router.transcribe(
        _dummyPcm,
        locale: 'en',
      );

      expect(result.text, 'hello world');
      expect(result.engineId, 'whisper');
      expect(primary.transcribeCalls, 1);
      expect(fallback.transcribeCalls, 0);
    });

    test('routes Luganda to primary', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        result: _makeResult('nkusaba', lang: 'lg', engine: 'whisper'),
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      final result = await router.transcribe(
        _dummyPcm,
        locale: 'lg',
      );

      expect(result.text, 'nkusaba');
      expect(result.language, 'lg');
    });

    test('throws model_not_ready_lg when primary unavailable for lg', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        available: false,
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      expect(
        () => router.transcribe(_dummyPcm, locale: 'lg'),
        throwsA(
          isA<AsrRouterException>()
              .having((e) => e.code, 'code', 'model_not_ready_lg'),
        ),
      );
    });

    test('throws no_engine_available when both engines down for English', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        available: false,
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
        available: false,
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      expect(
        () => router.transcribe(_dummyPcm, locale: 'en'),
        throwsA(
          isA<AsrRouterException>()
              .having((e) => e.code, 'code', 'no_engine_available'),
        ),
      );
    });

    test('falls through to error when primary returns null for English', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        result: null, // returns null
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
        // Native can't do one-shot PCM transcription, so this path
        // is a no-op and falls through to the error.
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      expect(
        () => router.transcribe(_dummyPcm, locale: 'en'),
        throwsA(isA<AsrRouterException>()),
      );
    });

    test('falls through to error when primary throws for English', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        shouldThrow: true,
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      expect(
        () => router.transcribe(_dummyPcm, locale: 'en'),
        throwsA(isA<AsrRouterException>()),
      );
    });

    test('transcribeStream prefers primary for Luganda', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        result: _makeResult('obulamu', lang: 'lg', engine: 'whisper'),
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      final chunks = Stream.fromIterable([_dummyPcm]);
      final results = await router
          .transcribeStream(chunks, locale: 'lg')
          .toList();

      expect(results, hasLength(1));
      expect(results.first.text, 'obulamu');
      expect(results.first.isFinal, isTrue);
    });

    test('transcribeStream throws for unavailable locale', () async {
      final primary = _FakeEngine(
        id: 'whisper',
        supportedLocales: {'en', 'lg'},
        available: false,
      );
      final fallback = _FakeEngine(
        id: 'native',
        supportedLocales: {'en'},
        available: false,
      );
      final router = AsrRouter(primary: primary, fallback: fallback);

      final chunks = Stream.fromIterable([_dummyPcm]);
      expect(
        () => router.transcribeStream(chunks, locale: 'lg').toList(),
        throwsA(isA<AsrRouterException>()),
      );
    });
  });
}
