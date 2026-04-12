/// Voice consent screen.
///
/// Shown at first launch (via router redirect) and reachable from
/// Settings → "Privacy & voice consent". Presents each [ConsentCategory]
/// with its title, description, and a Material 3 switch. The Continue
/// button persists grants and marks the user as bootstrapped.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/router/app_router.dart';
import '../../../core/theme/tokens.dart';
import '../models/consent_models.dart';
import '../providers/consent_provider.dart';

class ConsentScreen extends ConsumerStatefulWidget {
  const ConsentScreen({super.key, this.asInitialFlow = true});

  /// `true` when shown as the first-launch redirect; `false` when
  /// reached from Settings (adds a back button, shortens copy).
  final bool asInitialFlow;

  @override
  ConsumerState<ConsentScreen> createState() => _ConsentScreenState();
}

class _ConsentScreenState extends ConsumerState<ConsentScreen> {
  /// Local draft state — the user toggles switches here and only
  /// commits on Continue. Avoids writing to secure storage on every
  /// tap while the user is still deciding.
  late final Map<ConsentCategory, bool> _draft;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final existing = ref.read(consentProvider).grants;
    _draft = {
      for (final c in ConsentCategory.values)
        c: existing[c]?.granted ?? c.defaultGranted,
    };
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;
    final isInitial = widget.asInitialFlow;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Privacy & voice'),
        automaticallyImplyLeading: !isInitial,
      ),
      body: SafeArea(
        child: ListView(
          padding: AppSpacing.screenPadding,
          children: [
            // --- Hero preamble ---
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.all(AppSpacing.md),
                  decoration: BoxDecoration(
                    color: cs.primaryContainer,
                    borderRadius: AppRadius.cardRadius,
                  ),
                  child: Icon(
                    Icons.mic_none_rounded,
                    color: cs.onPrimaryContainer,
                    size: 32,
                  ),
                ),
                const SizedBox(width: AppSpacing.lg),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        isInitial ? 'Before you speak' : 'Voice permissions',
                        style: tt.titleLarge,
                      ),
                      const SizedBox(height: AppSpacing.xs),
                      Text(
                        'This app processes voice entirely on your phone. '
                        'Audio never leaves the device.',
                        style: tt.bodyMedium?.copyWith(
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xxl),

            // --- Each consent row ---
            for (final category in ConsentCategory.values)
              _ConsentTile(
                category: category,
                value: _draft[category]!,
                onChanged: _saving
                    ? null
                    : (v) {
                        HapticFeedback.selectionClick();
                        setState(() => _draft[category] = v);
                      },
              ),

            const SizedBox(height: AppSpacing.xl),

            // --- Legal footnote ---
            Container(
              padding: const EdgeInsets.all(AppSpacing.lg),
              decoration: BoxDecoration(
                color: cs.surfaceContainerHighest,
                borderRadius: AppRadius.cardRadius,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Your rights',
                    style: tt.titleSmall,
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Text(
                    '• You can change any of these choices at any time '
                    'from Settings.\n'
                    '• You can export or delete all locally-stored data '
                    'from the Model Manager.\n'
                    '• When the assistant speaks to you with an AI '
                    'voice, this app will label it clearly per the EU '
                    'AI Act (Art. 50).',
                    style: tt.bodySmall?.copyWith(
                      color: cs.onSurfaceVariant,
                      height: 1.5,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.xxl),

            // --- Actions ---
            FilledButton.icon(
              onPressed: _saving ? null : _commit,
              icon: _saving
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.check_rounded),
              label: Text(isInitial ? 'Continue to chat' : 'Save'),
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(52),
                shape: const RoundedRectangleBorder(
                  borderRadius: AppRadius.inputRadius,
                ),
              ),
            ),
            if (!isInitial) ...[
              const SizedBox(height: AppSpacing.sm),
              TextButton(
                onPressed: _saving ? null : () => context.pop(),
                child: const Text('Cancel'),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _commit() async {
    setState(() => _saving = true);
    final notifier = ref.read(consentProvider.notifier);
    try {
      for (final entry in _draft.entries) {
        if (entry.value) {
          await notifier.grant(entry.key);
        } else {
          await notifier.withdraw(entry.key);
        }
      }
      await notifier.markBootstrapped();
      if (!mounted) return;
      if (widget.asInitialFlow) {
        context.go(AppRoutes.chat);
      } else {
        context.pop();
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }
}

class _ConsentTile extends StatelessWidget {
  const _ConsentTile({
    required this.category,
    required this.value,
    required this.onChanged,
  });

  final ConsentCategory category;
  final bool value;
  final ValueChanged<bool>? onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.lg),
        decoration: BoxDecoration(
          color: cs.surfaceContainerLow,
          borderRadius: AppRadius.cardRadius,
          border: Border.all(color: cs.outlineVariant),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(category.title, style: tt.titleMedium),
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    category.description,
                    style: tt.bodySmall?.copyWith(
                      color: cs.onSurfaceVariant,
                      height: 1.45,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Switch(
              value: value,
              onChanged: onChanged,
            ),
          ],
        ),
      ),
    );
  }
}
