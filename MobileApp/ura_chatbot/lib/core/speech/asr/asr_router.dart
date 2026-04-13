/// Routes a transcription request to the best available engine.
///
/// Order of preference:
///
///   1. [WhisperOnnxEngine]  - primary, supports en/lg/sw offline.
///   2. [NativeAsrEngine]    - English-only emergency fallback.
///
/// When the user has selected `locale=lg` but the Whisper model isn't
/// ready yet, we deliberately do *not* fall back to the native OS
/// path — iOS has no Luganda support at all and Android's experimental
/// `lg_UG` locale is too unreliable to return silent text to users.
/// Instead we surface a clear [AsrRouterException] so the UI can show
/// a "download the Luganda model" prompt.
library;

import 'dart:async';
import 'package:flutter/foundation.dart';

import '../../audit/local_ledger.dart';
import 'asr_engine.dart';

@immutable
class AsrRouterException implements Exception {
  const AsrRouterException(this.code, this.message);
  final String code;
  final String message;

  @override
  String toString() => 'AsrRouterException($code): $message';
}

class AsrRouter {
  AsrRouter({required this.primary, required this.fallback});

  final AsrEngine primary;
  final AsrEngine fallback;

  /// Lazy init: both engines probe their availability the first time
  /// the router is asked to do work, so the cold-start cost of model
  /// loading only happens when the user actually speaks.
  Future<void> _ensureInitialized() async {
    // Run in parallel — the native engine and Whisper don't share state.
    await Future.wait([primary.initialize(), fallback.initialize()]);
  }

  Future<AsrResult> transcribe(
    Uint8List pcm16, {
    required String locale,
    List<String> hotwords = const [],
    String? consentGrantId,
  }) async {
    await _ensureInitialized();

    // 1. Prefer Whisper if it can handle the locale.
    if (primary.isAvailable && primary.supportedLocales.contains(locale)) {
      try {
        final result = await primary.transcribe(
          pcm16,
          locale: locale,
          hotwords: hotwords,
        );
        if (result != null) {
          await _auditSuccess(result, consentGrantId);
          return result;
        }
      } catch (e, s) {
        await _auditError(
          primary.id,
          locale,
          'primary_transcribe_failed',
          consentGrantId,
        );
        assert(() {
          debugPrintStack(label: 'primary asr failed', stackTrace: s);
          return true;
        }());
      }
    }

    // 2. For English, try the native OS recogniser. For Luganda the
    //    native path is useless, so we just fail clearly.
    if (locale == 'en' &&
        fallback.isAvailable &&
        fallback.supportedLocales.contains(locale)) {
      // The native engine can't do one-shot PCM transcription (no API),
      // so this branch is only reachable via [transcribeStream]. Fall
      // through to the error below.
    }

    final reason = locale == 'en'
        ? 'no_engine_available'
        : 'model_not_ready_$locale';
    await _auditError(primary.id, locale, reason, consentGrantId);
    final langName = _localeName(locale);
    throw AsrRouterException(
      reason,
      locale == 'en'
          ? 'No speech engine is ready. Check model downloads in Settings.'
          : 'The $langName speech model is not yet downloaded. '
                'Open Settings → Speech models to install it.',
    );
  }

  Stream<PartialAsrResult> transcribeStream(
    Stream<Uint8List> pcmChunks, {
    required String locale,
    List<String> hotwords = const [],
    String? consentGrantId,
  }) async* {
    await _ensureInitialized();

    // Prefer Whisper streaming (which internally buffers, then runs).
    if (primary.isAvailable && primary.supportedLocales.contains(locale)) {
      yield* primary.transcribeStream(
        pcmChunks,
        locale: locale,
        hotwords: hotwords,
      );
      return;
    }

    // English fallback via native OS streaming.
    if (locale == 'en' &&
        fallback.isAvailable &&
        fallback.supportedLocales.contains(locale)) {
      yield* fallback.transcribeStream(
        pcmChunks,
        locale: locale,
        hotwords: hotwords,
      );
      return;
    }

    throw AsrRouterException(
      'stream_unavailable',
      'No streaming engine available for locale="$locale".',
    );
  }

  static String _localeName(String locale) => switch (locale) {
    'en' => 'English',
    'lg' => 'Luganda',
    'sw' => 'Swahili',
    'nyn' => 'Runyankole',
    'ach' => 'Acholi',
    _ => locale.toUpperCase(),
  };

  Future<void> _auditSuccess(AsrResult result, String? consentGrantId) async {
    try {
      await LocalLedger.append(LedgerEventType.asrResult, {
        'engine_id': result.engineId,
        'locale': result.language,
        'latency_ms': result.latencyMs,
        'confidence': result.confidence,
        'text_len': result.text.length,
        'consent_grant_id': ?consentGrantId,
      });
    } catch (_) {
      // Non-fatal — transcription is already done.
    }
  }

  Future<void> _auditError(
    String engineId,
    String locale,
    String reason,
    String? consentGrantId,
  ) async {
    try {
      await LocalLedger.append(LedgerEventType.asrError, {
        'engine_id': engineId,
        'locale': locale,
        'reason': reason,
        'consent_grant_id': ?consentGrantId,
      });
    } catch (_) {}
  }
}
