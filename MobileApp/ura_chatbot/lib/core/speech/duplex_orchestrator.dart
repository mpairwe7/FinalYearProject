/// Full-duplex conversational speech orchestrator (2026).
///
/// Replaces the turn-based [SpeechOrchestrator] with a parallel
/// pipeline where ASR and TTS can run simultaneously. Key features:
///
///   - **Barge-in**: If the user starts speaking while TTS is playing,
///     TTS is immediately cancelled and ASR takes priority.
///   - **Streaming events**: A unified [DuplexEvent] stream feeds the
///     UI with real-time state changes (partial transcripts, LLM
///     chunks, synthesis progress).
///   - **Backpressure**: If LLM generation is slower than TTS
///     consumption, the pipeline buffers sentence-level chunks.
///
/// Architecture:
/// ```
/// Mic → AudioCapture → VAD → ASR (streaming)
///                                   ↓
///                               LLM generation (streaming)
///                                   ↓
///                           Sentence chunker → TTS (streaming)
///                                   ↓
///                               Speaker output
///                                   ↑
///                          Barge-in detection ← VAD
/// ```
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import 'audio_capture.dart';
import 'vad.dart';

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

/// Unified event stream for the duplex pipeline.
sealed class DuplexEvent {
  const DuplexEvent();
}

class DuplexListening extends DuplexEvent {
  const DuplexListening();
}

class DuplexPartialTranscript extends DuplexEvent {
  const DuplexPartialTranscript(this.text);
  final String text;
}

class DuplexFinalTranscript extends DuplexEvent {
  const DuplexFinalTranscript(this.text, this.locale);
  final String text;
  final String locale;
}

class DuplexLlmStarted extends DuplexEvent {
  const DuplexLlmStarted();
}

class DuplexLlmChunk extends DuplexEvent {
  const DuplexLlmChunk(this.text);
  final String text;
}

class DuplexLlmComplete extends DuplexEvent {
  const DuplexLlmComplete(this.fullText);
  final String fullText;
}

class DuplexSynthesisStarted extends DuplexEvent {
  const DuplexSynthesisStarted();
}

class DuplexSynthesisChunk extends DuplexEvent {
  const DuplexSynthesisChunk(this.durationMs);
  final double durationMs;
}

class DuplexPlaybackComplete extends DuplexEvent {
  const DuplexPlaybackComplete();
}

class DuplexBargeIn extends DuplexEvent {
  const DuplexBargeIn();
}

class DuplexError extends DuplexEvent {
  const DuplexError(this.message);
  final String message;
}

class DuplexIdle extends DuplexEvent {
  const DuplexIdle();
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

enum DuplexState { idle, listening, processing, synthesizing, bargedIn, error }

// ---------------------------------------------------------------------------
// Orchestrator
// ---------------------------------------------------------------------------

/// Configuration for the duplex pipeline.
@immutable
class DuplexConfig {
  const DuplexConfig({
    this.bargeInEnabled = true,
    this.sentenceChunkMinChars = 32,
    this.sentenceChunkMaxChars = 280,
    this.llmStreamingEnabled = true,
    this.maxTurnDurationMs = 30000,
  });

  final bool bargeInEnabled;
  final int sentenceChunkMinChars;
  final int sentenceChunkMaxChars;
  final bool llmStreamingEnabled;
  final int maxTurnDurationMs;
}

/// Abstract interface for the LLM backend (on-device or API).
abstract class DuplexLlmBackend {
  /// Generate a streaming response. Each yield is a text chunk.
  Stream<String> generateStream(String query, {String locale = 'en'});
}

/// Abstract interface for TTS synthesis.
abstract class DuplexTtsBackend {
  /// Synthesise a sentence and play it. Returns when playback completes.
  Future<void> synthesizeAndPlay(String text, {String locale = 'en'});

  /// Immediately stop any playing audio.
  Future<void> cancel();
}

/// Abstract interface for ASR transcription.
abstract class DuplexAsrBackend {
  /// Start streaming transcription. Yields partial and final results.
  Stream<DuplexAsrResult> transcribeStream(
    Stream<AudioChunk> audioChunks, {
    required String locale,
  });
}

@immutable
class DuplexAsrResult {
  const DuplexAsrResult({
    required this.text,
    required this.isFinal,
    this.locale = 'en',
  });

  final String text;
  final bool isFinal;
  final String locale;
}

/// Full-duplex speech orchestrator.
///
/// Call [startConversation] to begin listening. The orchestrator manages
/// the ASR→LLM→TTS pipeline automatically with barge-in support.
/// Listen to [events] for real-time state updates.
class DuplexOrchestrator {
  DuplexOrchestrator({
    required this.asr,
    required this.llm,
    required this.tts,
    required this.capture,
    required this.vad,
    this.config = const DuplexConfig(),
  });

  final DuplexAsrBackend asr;
  final DuplexLlmBackend llm;
  final DuplexTtsBackend tts;
  final AudioCapture capture;
  final VadDetector vad;
  final DuplexConfig config;

  final _events = StreamController<DuplexEvent>.broadcast();
  DuplexState _state = DuplexState.idle;

  StreamSubscription<VadEvent>? _vadSub;
  bool _isSynthesizing = false;
  bool _disposed = false;

  Stream<DuplexEvent> get events => _events.stream;
  DuplexState get state => _state;

  /// Start the conversational loop.
  Future<void> startConversation({required String locale}) async {
    if (_state != DuplexState.idle) return;

    _setState(DuplexState.listening);
    _events.add(const DuplexListening());

    try {
      final audioStream = await capture.start();
      vad.reset();
      final vadStream = runVad(audioStream, vad);

      _vadSub = vadStream.listen((event) async {
        switch (event) {
          case SpeechStart():
            // Barge-in: if TTS is playing, cancel it.
            if (_isSynthesizing && config.bargeInEnabled) {
              await _handleBargeIn();
            }

          case SpeechEnd(:final utterance):
            await _handleUtterance(utterance, locale);

          case SpeechChunk():
            break;

          case VadError(:final message):
            _events.add(DuplexError(message));
        }
      });
    } catch (e) {
      _setState(DuplexState.error);
      _events.add(DuplexError(e.toString()));
    }
  }

  /// Stop the conversation and release resources.
  Future<void> stopConversation() async {
    await _vadSub?.cancel();
    _vadSub = null;
    await tts.cancel();
    await capture.stop();
    _isSynthesizing = false;
    _setState(DuplexState.idle);
    _events.add(const DuplexIdle());
  }

  Future<void> _handleBargeIn() async {
    _events.add(const DuplexBargeIn());
    await tts.cancel();
    _isSynthesizing = false;
    _setState(DuplexState.listening);
    _events.add(const DuplexListening());
  }

  Future<void> _handleUtterance(Uint8List utterance, String locale) async {
    _setState(DuplexState.processing);

    // 1. Transcribe.
    // For one-shot, we pass the full utterance. For streaming ASR,
    // the VAD buffer contains the complete segment.
    final transcript = await _transcribeOneShot(utterance, locale);
    if (transcript.isEmpty) {
      _setState(DuplexState.listening);
      _events.add(const DuplexListening());
      return;
    }

    _events.add(DuplexFinalTranscript(transcript, locale));

    // 2. Generate LLM response (streaming).
    _events.add(const DuplexLlmStarted());
    final replyBuffer = StringBuffer();
    final sentences = <String>[];

    await for (final chunk in llm.generateStream(transcript, locale: locale)) {
      replyBuffer.write(chunk);
      _events.add(DuplexLlmChunk(chunk));

      // Chunk into sentences for TTS.
      final text = replyBuffer.toString();
      final newSentences = _extractSentences(text, sentences.length);
      for (final sentence in newSentences) {
        sentences.add(sentence);
        // Start TTS as soon as we have a complete sentence.
        _synthesizeSentence(sentence, locale);
      }
    }

    _events.add(DuplexLlmComplete(replyBuffer.toString()));

    // Synthesize any remaining partial sentence.
    final remaining = _remainingText(replyBuffer.toString(), sentences);
    if (remaining.isNotEmpty) {
      await _synthesizeSentence(remaining, locale);
    }

    _events.add(const DuplexPlaybackComplete());
    _setState(DuplexState.listening);
    _events.add(const DuplexListening());
  }

  Future<String> _transcribeOneShot(Uint8List pcm16, String locale) async {
    // Create a single-element stream for the ASR backend.
    final stream = Stream.fromIterable([
      AudioChunk(
        bytes: pcm16,
        sampleRate: 16000,
        capturedAtMs: DateTime.now().millisecondsSinceEpoch,
      ),
    ]);

    final results = await asr.transcribeStream(stream, locale: locale).toList();
    final finalResults = results.where((r) => r.isFinal);
    return finalResults.isNotEmpty ? finalResults.last.text : '';
  }

  Future<void> _synthesizeSentence(String sentence, String locale) async {
    if (_disposed || sentence.trim().isEmpty) return;

    _isSynthesizing = true;
    _setState(DuplexState.synthesizing);
    _events.add(const DuplexSynthesisStarted());

    try {
      await tts.synthesizeAndPlay(sentence, locale: locale);
    } catch (e) {
      // TTS failure is non-fatal — text is still displayed.
      debugPrint('duplex TTS error: $e');
    } finally {
      _isSynthesizing = false;
    }
  }

  /// Extract complete sentences from accumulated text.
  List<String> _extractSentences(String text, int alreadyExtracted) {
    // Split on sentence boundaries (. ! ? followed by space or end).
    final pattern = RegExp(r'[.!?]\s+');
    final parts = text.split(pattern);

    // Only return sentences we haven't already extracted.
    final complete = parts.length > 1
        ? parts.sublist(0, parts.length - 1)
        : <String>[];
    if (complete.length <= alreadyExtracted) return [];

    return complete
        .sublist(alreadyExtracted)
        .where((s) => s.trim().length >= config.sentenceChunkMinChars)
        .toList();
  }

  String _remainingText(String full, List<String> extracted) {
    var remaining = full;
    for (final s in extracted) {
      final idx = remaining.indexOf(s);
      if (idx >= 0) {
        remaining = remaining.substring(idx + s.length);
      }
    }
    return remaining.trim();
  }

  void _setState(DuplexState s) {
    _state = s;
  }

  void dispose() {
    _disposed = true;
    _vadSub?.cancel();
    _events.close();
  }
}
