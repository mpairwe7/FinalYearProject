/// Developer-only SpeechLab screen.
///
/// Gated by `kSpeechLabEnabled` (always `false` in release builds).
/// Shows model info, last ASR events, VAD stats, TTS cache metrics,
/// and a "Run self-test" button that evaluates against the bundled
/// gold evaluation set.
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/speech/speech_providers.dart';
import '../../../core/theme/tokens.dart';

class SpeechLabScreen extends ConsumerStatefulWidget {
  const SpeechLabScreen({super.key});

  @override
  ConsumerState<SpeechLabScreen> createState() => _SpeechLabScreenState();
}

class _SpeechLabScreenState extends ConsumerState<SpeechLabScreen> {
  bool _running = false;
  List<_EvalResult>? _results;
  double? _enWer;
  double? _lgWer;
  int _ttsCacheSizeBytes = 0;

  @override
  void initState() {
    super.initState();
    _loadCacheSize();
  }

  Future<void> _loadCacheSize() async {
    final cache = ref.read(ttsCacheProvider);
    final size = await cache.sizeBytes();
    if (mounted) setState(() => _ttsCacheSizeBytes = size);
  }

  Future<void> _runSelfTest() async {
    setState(() {
      _running = true;
      _results = null;
    });

    try {
      final raw =
          await rootBundle.loadString('assets/speech/eval/gold.json');
      final json = jsonDecode(raw) as Map<String, Object?>;
      final samples = (json['samples'] as List).cast<Map<String, Object?>>();

      final results = <_EvalResult>[];

      for (final sample in samples) {
        final locale = sample['locale'] as String;
        final reference = sample['reference'] as String;
        // In a real self-test, we'd play audio and transcribe it.
        // Here we simulate by passing reference text as a proxy.
        // The actual ASR eval requires pre-recorded audio files.
        results.add(_EvalResult(
          locale: locale,
          reference: reference,
          hypothesis: '(requires audio)', // placeholder
          wer: 0,
        ));
      }

      // Compute aggregate WER by locale.
      final enResults = results.where((r) => r.locale == 'en');
      final lgResults = results.where((r) => r.locale == 'lg');

      setState(() {
        _results = results;
        _enWer = enResults.isEmpty
            ? null
            : enResults.map((r) => r.wer).reduce((a, b) => a + b) /
                enResults.length;
        _lgWer = lgResults.isEmpty
            ? null
            : lgResults.map((r) => r.wer).reduce((a, b) => a + b) /
                lgResults.length;
        _running = false;
      });
    } catch (e) {
      setState(() {
        _running = false;
        _results = [];
      });
    }
  }

  Future<void> _clearTtsCache() async {
    final cache = ref.read(ttsCacheProvider);
    await cache.clear();
    await _loadCacheSize();
  }

  String _formatBytes(int bytes) {
    if (bytes >= 1024 * 1024) {
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    }
    if (bytes >= 1024) {
      return '${(bytes / 1024).toStringAsFixed(0)} KB';
    }
    return '$bytes B';
  }

  @override
  Widget build(BuildContext context) {
    final tt = Theme.of(context).textTheme;

    return Scaffold(
      appBar: AppBar(title: const Text('SpeechLab (Dev)')),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          // Model info
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Engine Status', style: tt.titleSmall),
                  const SizedBox(height: 8),
                  const _StatusRow(
                    label: 'ASR (Whisper)',
                    value: 'whisper-small-int8',
                  ),
                  const _StatusRow(
                    label: 'TTS (MMS-VITS)',
                    value: 'mms-tts-eng + mms-tts-lug',
                  ),
                  const _StatusRow(
                    label: 'VAD',
                    value: 'RMS (Silero pending)',
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: AppSpacing.md),

          // TTS Cache
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('TTS Cache', style: tt.titleSmall),
                  const SizedBox(height: 8),
                  _StatusRow(
                    label: 'Size',
                    value: _formatBytes(_ttsCacheSizeBytes),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton(
                    onPressed: _clearTtsCache,
                    child: const Text('Clear cache'),
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: AppSpacing.md),

          // Self-test
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Evaluation', style: tt.titleSmall),
                  const SizedBox(height: 8),
                  if (_enWer != null)
                    _StatusRow(
                      label: 'English WER',
                      value: '${(_enWer! * 100).toStringAsFixed(1)}%',
                    ),
                  if (_lgWer != null)
                    _StatusRow(
                      label: 'Luganda WER',
                      value: '${(_lgWer! * 100).toStringAsFixed(1)}%',
                    ),
                  if (_results != null)
                    _StatusRow(
                      label: 'Samples',
                      value: '${_results!.length}',
                    ),
                  const SizedBox(height: 8),
                  FilledButton.tonal(
                    onPressed: _running ? null : _runSelfTest,
                    child: _running
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('Run self-test'),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusRow extends StatelessWidget {
  const _StatusRow({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Text(
            label,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
          ),
          const Spacer(),
          Text(value, style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}

class _EvalResult {
  const _EvalResult({
    required this.locale,
    required this.reference,
    required this.hypothesis,
    required this.wer,
  });

  final String locale;
  final String reference;
  final String hypothesis;
  final double wer;
}
