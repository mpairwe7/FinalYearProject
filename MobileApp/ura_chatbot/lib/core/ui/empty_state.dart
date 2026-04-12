import 'package:flutter/material.dart';

import '../theme/tokens.dart'; // AppSpacing

/// Material 3 empty-state component.
///
/// Use whenever a content surface has legitimately zero items (empty
/// FAQ list, fresh chat, no feedback, etc.). Gives users a clear
/// reason-why + optional recovery action instead of a blank screen.
///
/// ```dart
/// const EmptyState(
///   icon: Icons.inbox_outlined,
///   title: 'No questions yet',
///   description: 'Ask me anything about URA taxes to get started.',
/// )
/// ```
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.description,
    this.action,
  });

  /// Leading icon — use an ``Icons.*_outlined`` variant to match M3.
  final IconData icon;

  /// Short heading — one line, title case.
  final String title;

  /// Optional body copy — one or two sentences.
  final String? description;

  /// Optional recovery action (e.g. a "Retry" button).
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                color: cs.primaryContainer.withValues(alpha: 0.4),
                shape: BoxShape.circle,
              ),
              child: Icon(icon, size: 36, color: cs.onPrimaryContainer),
            ),
            const SizedBox(height: AppSpacing.lg),
            Text(
              title,
              style: tt.titleMedium?.copyWith(
                fontWeight: FontWeight.w600,
                color: cs.onSurface,
              ),
              textAlign: TextAlign.center,
            ),
            if (description != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                description!,
                style: tt.bodyMedium?.copyWith(color: cs.onSurfaceVariant),
                textAlign: TextAlign.center,
              ),
            ],
            if (action != null) ...[
              const SizedBox(height: AppSpacing.xl),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}
