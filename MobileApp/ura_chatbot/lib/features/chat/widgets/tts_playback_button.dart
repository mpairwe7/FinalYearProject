/// Listen button on assistant messages that plays TTS synthesis.
///
/// Three visual states: idle (speaker icon), loading (spinner),
/// playing (stop icon). Shows the AI-voice disclosure label on first
/// play per session, then a compact badge on subsequent plays.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/audit/local_ledger.dart';
import '../../../core/compliance/ai_act.dart';
import '../../../core/speech/speech_providers.dart';
import '../../../core/speech/tts/playback.dart';
import '../../../core/theme/tokens.dart';

class TtsPlaybackButton extends ConsumerStatefulWidget {
  const TtsPlaybackButton({
    super.key,
    required this.text,
    required this.locale,
    required this.messageId,
  });

  final String text;
  final String locale;
  final String messageId;

  @override
  ConsumerState<TtsPlaybackButton> createState() => _TtsPlaybackButtonState();
}

class _TtsPlaybackButtonState extends ConsumerState<TtsPlaybackButton> {
  PlaybackState _state = PlaybackState.idle;
  SyntheticVoiceLabel? _label;
  bool _showFullLabel = false;
  StreamSubscription<PlaybackState>? _stateSub;

  @override
  void dispose() {
    _stateSub?.cancel();
    super.dispose();
  }

  Future<void> _onTap() async {
    final playback = ref.read(ttsPlaybackProvider);
    final ttsEngine = ref.read(ttsEngineProvider);
    final cache = ref.read(ttsCacheProvider);
    final labeler = ref.read(syntheticVoiceLabelerProvider);

    if (_state == PlaybackState.playing) {
      await playback.stop();
      return;
    }
    if (_state == PlaybackState.paused) {
      await playback.resume();
      return;
    }

    // Always stop any in-flight playback from another button before
    // starting a new one — prevents audio overlap.
    await playback.stop();

    if (!mounted) return;
    setState(() => _state = PlaybackState.loading);

    // Re-subscribe to playback state.
    await _stateSub?.cancel();
    if (!mounted) return;
    _stateSub = playback.stateStream.listen((s) {
      if (mounted) setState(() => _state = s);
    });

    try {
      // Check cache first.
      final modelId = widget.locale == 'lg' ? 'mms-tts-lug' : 'mms-tts-eng';
      var result = await cache.get(widget.text, modelId);
      final wasCached = result != null;

      if (result == null) {
        result = await ttsEngine.synthesize(
          text: widget.text,
          locale: widget.locale,
        );
        if (result != null) {
          await cache.put(widget.text, result);
        }
      }

      if (result == null) {
        if (mounted) {
          ScaffoldMessenger.of(context)
            ..hideCurrentSnackBar()
            ..showSnackBar(
              const SnackBar(
                content: Text('TTS model not available. Check Settings.'),
              ),
            );
          setState(() => _state = PlaybackState.idle);
        }
        return;
      }

      // Stamp watermark for AI Act compliance.
      final label = labeler.labelFor(
        modelId: result.modelId,
        locale: result.locale,
      );
      final needsFull = labeler.needsFullDisclosure;
      final stamped = labeler.stampWatermark(
        result.wavBytes,
        sampleRate: result.sampleRate,
      );

      if (needsFull) labeler.markFullShown();

      if (mounted) {
        setState(() {
          _label = label;
          _showFullLabel = needsFull;
        });
      }

      // Audit.
      await LocalLedger.append(
        wasCached ? LedgerEventType.ttsCacheHit : LedgerEventType.ttsPlayed,
        {
          'locale': widget.locale,
          'model_id': result.modelId,
          'latency_ms': result.latencyMs,
          'cached': wasCached,
          'label_shown': needsFull ? 'full' : 'short',
        },
      );

      await playback.play(stamped, sampleRate: result.sampleRate);
    } catch (e) {
      if (mounted) {
        setState(() => _state = PlaybackState.idle);
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(const SnackBar(content: Text('Could not play audio')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ttsEnabled = ref.watch(ttsEnabledProvider);

    if (!ttsEnabled) return const SizedBox.shrink();

    return Semantics(
      label: _state == PlaybackState.playing
          ? 'Stop listening to message'
          : 'Listen to message aloud',
      button: true,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: AppSpacing.xxxl, // 32dp compact row
            child: TextButton.icon(
              onPressed: _onTap,
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
                visualDensity: VisualDensity.compact,
              ),
              icon: _state == PlaybackState.loading
                  ? SizedBox(
                      width: AppSpacing.md + AppSpacing.xxs, // 14dp
                      height: AppSpacing.md + AppSpacing.xxs,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: cs.primary,
                      ),
                    )
                  : Icon(
                      _state == PlaybackState.playing
                          ? Icons.stop_circle_outlined
                          : Icons.volume_up_outlined,
                      size: 18,
                    ),
              label: Text(
                _state == PlaybackState.playing ? 'Stop' : 'Listen',
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ),
          ),
          if (_label != null)
            Padding(
              padding: const EdgeInsets.only(
                left: AppSpacing.sm,
                top: AppSpacing.xxs,
              ),
              child: Text(
                _showFullLabel ? _label!.fullLabel : _label!.shortLabel,
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: cs.onSurfaceVariant.soft,
                  fontSize: 10,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
