import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/chat_provider.dart';

/// Thumbs up / down feedback with optional comment follow-up.
class FeedbackButtons extends ConsumerStatefulWidget {
  final String messageId;
  final ValueChanged<String>? onFeedback;

  const FeedbackButtons({super.key, required this.messageId, this.onFeedback});

  @override
  ConsumerState<FeedbackButtons> createState() => _FeedbackButtonsState();
}

class _FeedbackButtonsState extends ConsumerState<FeedbackButtons> {
  String? _selected;

  void _onTap(String rating) {
    if (_selected != null) return; // already rated
    HapticFeedback.selectionClick();
    setState(() => _selected = rating);
    widget.onFeedback?.call(rating);

    if (rating == 'down') {
      _showCommentSheet();
    }
  }

  Future<void> _showCommentSheet() async {
    final comment = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => _CommentSheet(),
    );

    if (comment != null && comment.isNotEmpty && mounted) {
      ref
          .read(chatProvider.notifier)
          .addFeedbackComment(widget.messageId, comment);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _FeedbackIcon(
          icon: Icons.thumb_up_outlined,
          selectedIcon: Icons.thumb_up,
          isSelected: _selected == 'up',
          isDisabled: _selected != null && _selected != 'up',
          color: cs.primary,
          onTap: () => _onTap('up'),
          tooltip: 'Helpful',
        ),
        const SizedBox(width: 4),
        _FeedbackIcon(
          icon: Icons.thumb_down_outlined,
          selectedIcon: Icons.thumb_down,
          isSelected: _selected == 'down',
          isDisabled: _selected != null && _selected != 'down',
          color: cs.error,
          onTap: () => _onTap('down'),
          tooltip: 'Not helpful',
        ),
      ],
    );
  }
}

/// Bottom sheet for feedback comment — disposes its own controller.
class _CommentSheet extends StatefulWidget {
  @override
  State<_CommentSheet> createState() => _CommentSheetState();
}

class _CommentSheetState extends State<_CommentSheet> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        20,
        20,
        20 + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'What went wrong?',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _controller,
            maxLength: 1000,
            maxLines: 3,
            autofocus: true,
            decoration: const InputDecoration(hintText: 'Optional feedback...'),
          ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Skip'),
              ),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: () =>
                    Navigator.pop(context, _controller.text.trim()),
                child: const Text('Submit'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Feedback icon with 48dp minimum touch target (Material a11y).
class _FeedbackIcon extends StatelessWidget {
  final IconData icon;
  final IconData selectedIcon;
  final bool isSelected;
  final bool isDisabled;
  final Color color;
  final VoidCallback onTap;
  final String tooltip;

  const _FeedbackIcon({
    required this.icon,
    required this.selectedIcon,
    required this.isSelected,
    required this.isDisabled,
    required this.color,
    required this.onTap,
    required this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: tooltip,
      button: true,
      child: IconButton(
        constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
        padding: const EdgeInsets.all(8),
        iconSize: 20,
        onPressed: isDisabled ? null : onTap,
        icon: Icon(
          isSelected ? selectedIcon : icon,
          color: isSelected
              ? color
              : isDisabled
              ? color.withValues(alpha: 0.24)
              : color.withValues(alpha: 0.56),
        ),
        tooltip: tooltip,
      ),
    );
  }
}
