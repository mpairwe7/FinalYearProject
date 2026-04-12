import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../connectivity/connectivity_provider.dart';
import '../theme/tokens.dart';

/// Animated offline banner shown at the top of the app shell.
///
/// Watches [connectivityStatusProvider] and slides in when the device
/// loses network. When the on-device LLM is available, the banner
/// reassures the user that chat still works offline.
///
/// Drop into a Column at the top of the body:
///
/// ```dart
/// Column(children: [const OfflineBanner(), Expanded(child: content)])
/// ```
class OfflineBanner extends ConsumerWidget {
  const OfflineBanner({
    super.key,
    this.offlineMessage = 'No internet connection',
    this.offlineCapableMessage = 'Offline — using on-device model',
    this.hasOfflineFallback = false,
  });

  /// Message to show when the app cannot fall back to on-device inference.
  final String offlineMessage;

  /// Message to show when the on-device Gemma-2B model is bundled and
  /// available, so the app *can* still answer offline.
  final String offlineCapableMessage;

  /// Whether the on-device model is ready (passed from the chat provider).
  final bool hasOfflineFallback;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isOnline = ref.watch(isOnlineProvider);
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;

    final text = hasOfflineFallback ? offlineCapableMessage : offlineMessage;
    final icon = hasOfflineFallback ? Icons.offline_bolt : Icons.cloud_off;
    final bg = hasOfflineFallback
        ? cs.tertiaryContainer
        : cs.errorContainer;
    final fg = hasOfflineFallback
        ? cs.onTertiaryContainer
        : cs.onErrorContainer;

    return AnimatedSize(
      duration: AppMotion.medium,
      curve: AppMotion.emphasized,
      alignment: Alignment.topCenter,
      child: isOnline
          ? const SizedBox(width: double.infinity, height: 0)
          : Material(
              color: bg,
              child: SafeArea(
                bottom: false,
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.lg,
                    vertical: AppSpacing.sm,
                  ),
                  child: Row(
                    children: [
                      Icon(icon, size: 18, color: fg),
                      const SizedBox(width: AppSpacing.sm),
                      Expanded(
                        child: Text(
                          text,
                          style: tt.labelMedium?.copyWith(color: fg),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
    );
  }
}
