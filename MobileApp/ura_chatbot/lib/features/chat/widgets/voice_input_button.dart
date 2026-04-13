/// Microphone button that captures audio, runs VAD endpointing, and
/// routes the resulting utterance through the on-device ASR pipeline.
///
/// 2026 UX: when the user taps and recording is active, the button
/// pulses to make the "I'm listening" state visually unambiguous. The
/// pulse uses a halo behind the icon so it does not interfere with the
/// tap target or icon legibility.
///
/// Accessibility: exposes the recording state to screen readers via
/// [Semantics.liveRegion] so assistive tech announces the transition.
library;

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/audit/local_ledger.dart';
import '../../../core/speech/asr/asr_router.dart';
import '../../../core/speech/audio_capture.dart';
import '../../../core/speech/permissions.dart';
import '../../../core/speech/speech_providers.dart';
import '../../../core/speech/vad.dart';
import '../../../core/theme/tokens.dart';
import '../../consent/models/consent_models.dart';
import '../../consent/providers/consent_provider.dart';

/// Microphone button with VAD-based endpointing and on-device ASR.
class VoiceInputButton extends ConsumerStatefulWidget {
  const VoiceInputButton({
    super.key,
    required this.onResult,
    required this.locale,
  });

  final ValueChanged<String> onResult;
  final String locale;

  @override
  ConsumerState<VoiceInputButton> createState() => _VoiceInputButtonState();
}

class _VoiceInputButtonState extends ConsumerState<VoiceInputButton>
    with SingleTickerProviderStateMixin, WidgetsBindingObserver {
  late final AnimationController _pulse;

  bool _listening = false;
  bool _processing = false;
  bool _toggling = false; // double-tap guard
  StreamSubscription<VadEvent>? _vadSub;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _pulse = AnimationController(
      vsync: this,
      duration: AppMotion.slow * 2.4, // 1200ms pulse
    );
  }

  /// Stop recording when app is backgrounded to release the mic and
  /// prevent battery drain.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused && _listening) {
      _stopListening();
    }
  }

  bool get _consentGranted => ref.read(voiceRecordGrantedProvider);

  String? get _consentGrantId {
    final state = ref.read(consentProvider);
    return state.grants[ConsentCategory.voiceRecord]?.grantId;
  }

  Future<void> _toggle() async {
    // Double-tap guard: reject rapid taps while a transition is in flight.
    if (_toggling || _processing) return;
    _toggling = true;

    try {
      HapticFeedback.selectionClick();

      if (_listening) {
        await _stopListening();
        return;
      }

      // Check consent first.
      if (!_consentGranted) {
        if (mounted) {
          ScaffoldMessenger.of(context)
            ..hideCurrentSnackBar()
            ..showSnackBar(
              const SnackBar(
                content: Text(
                  'Voice recording requires consent. Go to Settings → '
                  'Privacy & voice consent.',
                ),
              ),
            );
        }
        return;
      }

      // Check microphone permission.
      final perms = ref.read(speechPermissionsProvider);
      final status = await perms.requestMicrophone();
      if (status != SpeechPermissionStatus.granted) {
        if (mounted) {
          ScaffoldMessenger.of(context)
            ..hideCurrentSnackBar()
            ..showSnackBar(
              SnackBar(
                content: const Text('Microphone permission required'),
                action: SnackBarAction(
                  label: 'Settings',
                  onPressed: perms.openSettings,
                ),
              ),
            );
        }
        return;
      }

      await _startListening();
    } finally {
      _toggling = false;
    }
  }

  Future<void> _startListening() async {
    final capture = ref.read(audioCaptureProvider);
    final detector = ref.read(vadDetectorProvider);
    final router = ref.read(asrRouterProvider);

    try {
      // Log mic open.
      await LocalLedger.append(LedgerEventType.micOpened, {
        'locale': widget.locale,
        if (_consentGrantId != null) 'consent_grant_id': _consentGrantId,
      });

      final audioStream = await capture.start();
      if (!mounted) return;

      setState(() => _listening = true);
      _pulse.repeat(reverse: true);

      detector.reset();
      final vadStream = runVad(audioStream, detector);

      _vadSub = vadStream.listen(
        (event) async {
          // Re-check consent on every VAD event — if the user withdrew
          // consent while recording, stop immediately.
          if (!_consentGranted) {
            await _stopListening();
            return;
          }

          switch (event) {
            case SpeechStart():
              break;

            case SpeechChunk():
              break;

            case SpeechEnd(:final utterance, :final durationMs):
              await _onUtteranceComplete(utterance, durationMs, router);

            case VadError(:final message):
              if (mounted) {
                ScaffoldMessenger.of(context)
                  ..hideCurrentSnackBar()
                  ..showSnackBar(
                    SnackBar(content: Text('Voice error: $message')),
                  );
              }
              await _stopListening();
          }
        },
        onError: (Object e) async {
          await _stopListening();
        },
      );
    } on AudioCaptureException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text('Mic error: ${e.message}')));
      }
      _resetState();
    }
  }

  Future<void> _onUtteranceComplete(
    Uint8List utterance,
    double durationMs,
    AsrRouter router,
  ) async {
    await _stopCapture();
    if (!mounted) return;
    setState(() => _processing = true);

    try {
      final result = await router.transcribe(
        utterance,
        locale: widget.locale,
        consentGrantId: _consentGrantId,
      );
      if (mounted && result.text.isNotEmpty) {
        widget.onResult(result.text);
      }
    } on AsrRouterException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(e.message)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(const SnackBar(content: Text('Transcription failed')));
      }
    } finally {
      _resetState();
    }
  }

  Future<void> _stopListening() async {
    await _stopCapture();
    _resetState();
  }

  Future<void> _stopCapture() async {
    await _vadSub?.cancel();
    _vadSub = null;
    final capture = ref.read(audioCaptureProvider);
    await capture.stop();
  }

  void _resetState() {
    if (!mounted) return;
    setState(() {
      _listening = false;
      _processing = false;
    });
    _pulse.stop();
    _pulse.reset();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _vadSub?.cancel();
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final hasConsent = ref.watch(voiceRecordGrantedProvider);
    final cs = Theme.of(context).colorScheme;
    final isActive = _listening || _processing;

    return Semantics(
      label: _listening
          ? 'Recording voice input'
          : _processing
          ? 'Processing speech'
          : 'Start voice input',
      button: true,
      liveRegion: isActive,
      child: SizedBox(
        width: AppSpacing.huge, // 48dp Material 3 touch target
        height: AppSpacing.huge,
        child: Stack(
          alignment: Alignment.center,
          children: [
            if (_listening)
              AnimatedBuilder(
                animation: _pulse,
                builder: (context, _) {
                  final scale = 1.0 + (_pulse.value * 0.6);
                  final alpha = (1.0 - _pulse.value) * 0.35;
                  return Transform.scale(
                    scale: scale,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: cs.error.withValues(alpha: alpha),
                      ),
                      child: const SizedBox(
                        width: AppSpacing.xxxl, // 32dp halo
                        height: AppSpacing.xxxl,
                      ),
                    ),
                  );
                },
              ),
            IconButton(
              onPressed: hasConsent ? _toggle : null,
              icon: AnimatedSwitcher(
                duration: AppMotion.fast,
                transitionBuilder: (child, animation) =>
                    ScaleTransition(scale: animation, child: child),
                child: _processing
                    ? SizedBox(
                        width: AppSpacing.xl, // 20dp spinner
                        height: AppSpacing.xl,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: cs.primary,
                        ),
                      )
                    : Icon(
                        _listening ? Icons.mic : Icons.mic_none,
                        key: ValueKey(_listening),
                        color: _listening ? cs.error : cs.onSurfaceVariant,
                      ),
              ),
              tooltip: _listening
                  ? 'Stop listening'
                  : _processing
                  ? 'Processing…'
                  : 'Voice input',
            ),
          ],
        ),
      ),
    );
  }
}
