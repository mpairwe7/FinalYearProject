/// End-to-end speech orchestrator (2026).
///
/// Wires the mobile speech pipeline together:
///
///     mic PCM stream
///        -> AsrService (streaming + VAD gated)
///        -> lang-ID
///        -> [MtService: source -> en]         if needed
///        -> OnDeviceLlm                        (existing text LLM)
///        -> [MtService: en -> target]         if needed
///        -> TtsService (streaming synthesis)
///        -> native audio player
///
/// Barge-in handled at the TtsService level: when a fresh utterance
/// begins mid-reply, in-flight synthesis is cancelled.
///
/// This class is deliberately UI-free. Hook it into a provider /
/// bloc / riverpod (whatever the app uses for state) from the widget
/// layer. It only provides:
///
///   * a high-level `respondTo(Stream<Int16List>)` entry point, and
///   * `SpeechTurn` events that downstream consumers can render.
library;

import 'dart:async';
import 'dart:typed_data';

import '../inference/on_device_llm.dart';
import 'asr_service.dart';
import 'mt_service.dart';
import 'speech_config.dart';
import 'tts_service.dart';

enum SpeechStage {
  idle,
  listening,
  asr,
  translating,
  llm,
  synthesising,
  playing,
  error,
}

class SpeechTurn {
  const SpeechTurn({
    required this.stage,
    this.transcript = '',
    this.translatedIn = '',
    this.reply = '',
    this.translatedOut = '',
    this.error,
  });

  final SpeechStage stage;
  final String transcript;
  final String translatedIn;
  final String reply;
  final String translatedOut;
  final String? error;

  SpeechTurn copyWith({
    SpeechStage? stage,
    String? transcript,
    String? translatedIn,
    String? reply,
    String? translatedOut,
    String? error,
  }) => SpeechTurn(
    stage: stage ?? this.stage,
    transcript: transcript ?? this.transcript,
    translatedIn: translatedIn ?? this.translatedIn,
    reply: reply ?? this.reply,
    translatedOut: translatedOut ?? this.translatedOut,
    error: error ?? this.error,
  );
}

class SpeechOrchestrator {
  SpeechOrchestrator({
    SpeechPipelineConfig? config,
    AsrService? asr,
    TtsService? tts,
    MtService? mt,
    OnDeviceLlm? llm,
  }) : _config = config ?? const SpeechPipelineConfig(),
       _asr = asr ?? AsrService(),
       _tts = tts ?? TtsService(),
       _mt = mt ?? MtService(),
       _llm = llm ?? OnDeviceLlm();

  final SpeechPipelineConfig _config;
  final AsrService _asr;
  final TtsService _tts;
  final MtService _mt;
  final OnDeviceLlm _llm;

  final StreamController<SpeechTurn> _events = StreamController.broadcast();

  Stream<SpeechTurn> get events => _events.stream;
  AsrService get asr => _asr;
  TtsService get tts => _tts;

  /// Full-duplex turn: consume a stream of mic PCM and produce a reply.
  ///
  /// [userLanguage] is the language the user is speaking. The reply is
  /// synthesised in the same language (the LLM runs in English and MT
  /// handles the bridge).
  Future<void> respondTo(
    Stream<Int16List> micFrames, {
    String userLanguage = 'en',
  }) async {
    var turn = const SpeechTurn(stage: SpeechStage.listening);
    _events.add(turn);

    // 1. Streaming ASR
    await _asr.startStream(language: userLanguage);
    String finalTranscript = '';
    final asrSub = _asr.results.listen((event) {
      turn = turn.copyWith(
        stage: event.isFinal ? SpeechStage.asr : SpeechStage.listening,
        transcript: event.text,
      );
      _events.add(turn);
      if (event.isFinal) {
        finalTranscript = event.text;
      }
    });

    await for (final frame in micFrames) {
      await _asr.pushChunk(frame);
    }
    await _asr.endUtterance();
    await asrSub.cancel();

    if (finalTranscript.isEmpty) {
      turn = turn.copyWith(
        stage: SpeechStage.error,
        error: 'empty transcription',
      );
      _events.add(turn);
      return;
    }

    // 2. MT source -> English (if needed)
    String llmInput = finalTranscript;
    if (_config.enableTranslationRouting && userLanguage != 'en') {
      turn = turn.copyWith(stage: SpeechStage.translating);
      _events.add(turn);
      final mtResult = await _mt.translate(
        text: finalTranscript,
        sourceLang: userLanguage,
        targetLang: 'en',
      );
      llmInput = mtResult.text;
      turn = turn.copyWith(translatedIn: llmInput);
      _events.add(turn);
    }

    // 3. LLM
    turn = turn.copyWith(stage: SpeechStage.llm);
    _events.add(turn);
    final llmResult = await _llm.generate(query: llmInput);
    final llmText = llmResult?.text.trim() ?? '';
    turn = turn.copyWith(reply: llmText);
    _events.add(turn);

    // 4. MT English -> target (if needed)
    String ttsText = llmText;
    if (_config.enableTranslationRouting && userLanguage != 'en') {
      turn = turn.copyWith(stage: SpeechStage.translating);
      _events.add(turn);
      final mtBack = await _mt.translate(
        text: llmText,
        sourceLang: 'en',
        targetLang: userLanguage,
      );
      ttsText = mtBack.text;
      turn = turn.copyWith(translatedOut: ttsText);
      _events.add(turn);
    }

    // 5. TTS (streaming)
    turn = turn.copyWith(stage: SpeechStage.synthesising);
    _events.add(turn);
    final frames = _tts.synthesise(text: ttsText, language: userLanguage);
    turn = turn.copyWith(stage: SpeechStage.playing);
    _events.add(turn);
    await for (final _ in frames) {
      // Host app hooks into this stream separately to pipe into the
      // native audio player — the orchestrator doesn't own playback.
    }
    turn = turn.copyWith(stage: SpeechStage.idle);
    _events.add(turn);
  }

  Future<void> dispose() async {
    await _asr.dispose();
    await _tts.dispose();
    await _events.close();
  }
}
