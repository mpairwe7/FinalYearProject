import 'package:flutter/material.dart';
import 'package:shimmer/shimmer.dart';

import '../theme/tokens.dart';

/// Shimmer placeholder for a list of cards (FAQ, messages, etc.).
///
/// 2026 UX expectation: during async loading show the *shape* of the
/// eventual content, not a centered spinner. Reduces perceived latency
/// because the eye locks onto the layout immediately.
class SkeletonList extends StatelessWidget {
  const SkeletonList({super.key, this.itemCount = 6, this.itemHeight = 72});

  final int itemCount;
  final double itemHeight;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final baseColor = cs.surfaceContainerHighest;
    final highlightColor = cs.surfaceContainer;

    return Shimmer.fromColors(
      baseColor: baseColor,
      highlightColor: highlightColor,
      period: const Duration(milliseconds: 1200),
      child: ListView.builder(
        physics: const NeverScrollableScrollPhysics(),
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: itemCount,
        itemBuilder: (context, index) => Container(
          height: itemHeight,
          margin: const EdgeInsets.only(bottom: AppSpacing.sm),
          decoration: const BoxDecoration(
            color: Colors.white, // recoloured by shimmer
            borderRadius: AppRadius.cardRadius,
          ),
        ),
      ),
    );
  }
}

/// Shimmer placeholder for a chat bubble row.
class SkeletonBubble extends StatelessWidget {
  const SkeletonBubble({super.key, this.isUser = false});

  final bool isUser;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Shimmer.fromColors(
      baseColor: cs.surfaceContainerHighest,
      highlightColor: cs.surfaceContainer,
      period: const Duration(milliseconds: 1200),
      child: Align(
        alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          width: 220,
          height: 48,
          margin: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: AppRadius.bubbleRadius,
          ),
        ),
      ),
    );
  }
}
