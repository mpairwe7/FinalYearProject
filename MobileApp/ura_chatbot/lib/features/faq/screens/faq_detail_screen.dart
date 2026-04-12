import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/tokens.dart';
import '../../../core/ui/app_error_view.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/loading_skeleton.dart';
import '../models/faq_models.dart';
import '../providers/faq_provider.dart';

/// Detail screen for a single FAQ category.
///
/// Separated from [FAQScreen] so the go_router setup can reference it
/// directly without importing half the faq_screen file.
class FAQDetailScreen extends ConsumerWidget {
  const FAQDetailScreen({
    super.key,
    required this.tagId,
    required this.tagName,
  });

  final String tagId;
  final String tagName;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final faqAsync = ref.watch(faqItemsProvider(tagId));

    return Scaffold(
      appBar: AppBar(title: Text(tagName)),
      body: faqAsync.when(
        loading: () => const SkeletonList(itemHeight: 84),
        error: (e, _) => AppErrorView(
          message: e.toString(),
          onRetry: () => ref.invalidate(faqItemsProvider(tagId)),
        ),
        data: (faqs) {
          if (faqs.isEmpty) {
            return const EmptyState(
              icon: Icons.help_outline,
              title: 'No FAQs yet',
              description: 'This topic has no questions published yet.',
            );
          }
          return RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(faqItemsProvider(tagId));
              await ref.read(faqItemsProvider(tagId).future);
            },
            child: ListView.builder(
              padding: AppSpacing.listPadding,
              physics: const AlwaysScrollableScrollPhysics(),
              itemCount: faqs.length,
              itemBuilder: (context, i) => RepaintBoundary(
                child: _FAQCard(faq: faqs[i]),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _FAQCard extends StatelessWidget {
  const _FAQCard({required this.faq});

  final FAQItem faq;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        shape: const RoundedRectangleBorder(borderRadius: AppRadius.cardRadius),
        collapsedShape:
            const RoundedRectangleBorder(borderRadius: AppRadius.cardRadius),
        tilePadding: const EdgeInsets.symmetric(horizontal: AppSpacing.lg),
        childrenPadding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          0,
          AppSpacing.lg,
          AppSpacing.lg,
        ),
        leading: Icon(Icons.help_outline, color: cs.primary, size: 20),
        title: Text(
          faq.question,
          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
        ),
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: SelectableText(
              faq.answer,
              style: TextStyle(
                fontSize: 14,
                color: cs.onSurfaceVariant,
                height: 1.5,
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
              'Source: ${faq.source}',
              style: TextStyle(
                fontSize: 11,
                color: cs.onSurfaceVariant.withValues(alpha: 0.56),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
