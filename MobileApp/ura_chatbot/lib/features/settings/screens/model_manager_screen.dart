/// Model Manager screen — manage on-device speech model bundles.
///
/// Shows each bundle from the manifest with:
///   - Name, locale, tier, size
///   - Download / Delete / Re-download actions
///   - Live download progress bar
///
/// Accessible from Settings → "Speech models".
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/speech/model_manager.dart';
import '../../../core/speech/speech_providers.dart';
import '../../../core/theme/tokens.dart';

class ModelManagerScreen extends ConsumerStatefulWidget {
  const ModelManagerScreen({super.key});

  @override
  ConsumerState<ModelManagerScreen> createState() => _ModelManagerScreenState();
}

class _ModelManagerScreenState extends ConsumerState<ModelManagerScreen> {
  final Map<String, double> _downloadProgress = {};
  final Map<String, bool> _installed = {};
  StreamSubscription<DownloadProgress>? _progressSub;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _init();
  }

  @override
  void dispose() {
    _progressSub?.cancel();
    super.dispose();
  }

  Future<void> _init() async {
    final mgr = ref.read(modelManagerProvider);
    await mgr.loadManifest();

    _progressSub = mgr.progress.listen((p) {
      if (mounted) {
        setState(() => _downloadProgress[p.bundleId] = p.fraction);
      }
    });

    // Check installation status for each bundle.
    for (final bundle in mgr.allBundles) {
      _installed[bundle.id] = await mgr.isInstalled(bundle.id);
    }

    if (mounted) setState(() => _loading = false);
  }

  Future<void> _download(String bundleId) async {
    final mgr = ref.read(modelManagerProvider);
    setState(() => _downloadProgress[bundleId] = 0);
    final result = await mgr.ensure(bundleId);
    if (mounted) {
      setState(() {
        _downloadProgress.remove(bundleId);
        _installed[bundleId] = result != null;
      });
    }
  }

  Future<void> _delete(String bundleId) async {
    final mgr = ref.read(modelManagerProvider);
    await mgr.delete(bundleId);
    if (mounted) {
      setState(() => _installed[bundleId] = false);
    }
  }

  String _formatSize(int bytes) {
    if (bytes >= 1024 * 1024) {
      return '${(bytes / (1024 * 1024)).toStringAsFixed(0)} MB';
    }
    return '${(bytes / 1024).toStringAsFixed(0)} KB';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final mgr = ref.watch(modelManagerProvider);
    final bundles = mgr.allBundles;

    return Scaffold(
      appBar: AppBar(title: const Text('Speech Models')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView.separated(
              padding: const EdgeInsets.all(AppSpacing.md),
              itemCount: bundles.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
              itemBuilder: (context, i) {
                final bundle = bundles[i];
                final installed = _installed[bundle.id] ?? false;
                final progress = _downloadProgress[bundle.id];
                final isDownloading = progress != null;

                return Card(
                  child: Padding(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                bundle.title,
                                style: Theme.of(context).textTheme.titleSmall,
                              ),
                            ),
                            Chip(
                              label: Text(
                                bundle.tier.toUpperCase(),
                                style: Theme.of(
                                  context,
                                ).textTheme.labelSmall?.copyWith(fontSize: 10),
                              ),
                              visualDensity: VisualDensity.compact,
                            ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          bundle.description,
                          style: Theme.of(context).textTheme.bodySmall
                              ?.copyWith(color: cs.onSurfaceVariant),
                        ),
                        const SizedBox(height: 8),
                        Row(
                          children: [
                            Icon(
                              Icons.language,
                              size: 14,
                              color: cs.onSurfaceVariant,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              bundle.locales.join(', '),
                              style: Theme.of(context).textTheme.labelSmall,
                            ),
                            const SizedBox(width: AppSpacing.md),
                            Icon(
                              Icons.storage,
                              size: 14,
                              color: cs.onSurfaceVariant,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              _formatSize(bundle.sizeBytes),
                              style: Theme.of(context).textTheme.labelSmall,
                            ),
                            const Spacer(),
                            if (installed)
                              Icon(
                                Icons.check_circle,
                                size: 18,
                                color: cs.primary,
                              ),
                          ],
                        ),
                        if (isDownloading) ...[
                          const SizedBox(height: 8),
                          LinearProgressIndicator(value: progress),
                        ],
                        const SizedBox(height: 8),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.end,
                          children: [
                            if (!installed && !isDownloading)
                              FilledButton.tonal(
                                onPressed: () => _download(bundle.id),
                                child: const Text('Download'),
                              ),
                            if (installed) ...[
                              OutlinedButton(
                                onPressed: () => _delete(bundle.id),
                                child: const Text('Delete'),
                              ),
                              const SizedBox(width: 8),
                              FilledButton.tonal(
                                onPressed: () => _download(bundle.id),
                                child: const Text('Re-download'),
                              ),
                            ],
                            if (isDownloading) const Text('Downloading…'),
                          ],
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
    );
  }
}
