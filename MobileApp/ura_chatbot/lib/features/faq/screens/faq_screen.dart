import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/router/app_router.dart';
import '../../../core/theme/tokens.dart';
import '../../../core/ui/app_error_view.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/loading_skeleton.dart';
import '../models/faq_models.dart';
import '../providers/faq_provider.dart';

/// FAQ topics screen — list of tag cards.
///
/// Navigation: tapping a tag uses [GoRouter.go] with
/// ``AppRoutes.faqDetailFor(tag.id)`` and passes the [TagInfo] via
/// ``extra`` so the detail screen renders the title immediately
/// without re-fetching the tag list.
///
/// Uses shimmer skeletons during the first load, an [EmptyState] when
/// the backend returns zero tags, [AppErrorView] on failure with a
/// retry affordance, and a [RefreshIndicator] for pull-to-refresh.
class FAQScreen extends ConsumerWidget {
  const FAQScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tagsAsync = ref.watch(tagsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('FAQ Topics')),
      body: tagsAsync.when(
        loading: () => const SkeletonList(itemCount: 8, itemHeight: 76),
        error: (e, _) => AppErrorView(
          message: e.toString(),
          onRetry: () => ref.invalidate(tagsProvider),
        ),
        data: (tags) {
          if (tags.isEmpty) {
            return EmptyState(
              icon: Icons.inbox_outlined,
              title: 'No FAQ topics yet',
              description:
                  'The server has no FAQ categories published at the moment. Pull down to refresh.',
              action: FilledButton.tonalIcon(
                onPressed: () => ref.invalidate(tagsProvider),
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh'),
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: () async {
              HapticFeedback.selectionClick();
              ref.invalidate(tagsProvider);
              await ref.read(tagsProvider.future);
            },
            child: ListView.builder(
              padding: AppSpacing.listPadding,
              physics: const AlwaysScrollableScrollPhysics(),
              itemCount: tags.length,
              itemBuilder: (context, i) => RepaintBoundary(
                child: _TagCard(tag: tags[i]),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _TagCard extends StatelessWidget {
  const _TagCard({required this.tag});

  final TagInfo tag;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: cs.primaryContainer,
          child: Text(
            tag.name.isNotEmpty ? tag.name[0].toUpperCase() : '?',
            style: TextStyle(
              color: cs.onPrimaryContainer,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
        title: Text(tag.name),
        subtitle: tag.description.isNotEmpty
            ? Text(
                tag.description,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              )
            : null,
        trailing: const Icon(Icons.chevron_right),
        onTap: () {
          HapticFeedback.selectionClick();
          context.go(AppRoutes.faqDetailFor(tag.id), extra: tag);
        },
      ),
    );
  }
}
