import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/tokens.dart';
import '../../settings/providers/settings_provider.dart';
import '../models/chat_models.dart';
import '../providers/chat_provider.dart';
import '../widgets/message_bubble.dart';
import '../widgets/starter_prompts.dart';
import '../widgets/voice_input_button.dart';

/// Chat surface — top app bar with locale toggle, message list with
/// timestamp separators, input bar with voice + send, and a
/// scroll-to-bottom FAB that appears when the user has scrolled up.
///
/// Rebuild discipline: uses ``.select()`` to subscribe to narrow slices
/// of [ChatState] so that unrelated state changes (e.g. ``offlineReady``
/// flipping) don't rebuild the whole message list.
class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final _focusNode = FocusNode();

  /// Whether the user has scrolled away from the bottom — drives the
  /// scroll-to-bottom FAB visibility.
  bool _showScrollToBottom = false;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _controller.dispose();
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    final max = _scrollController.position.maxScrollExtent;
    final current = _scrollController.position.pixels;
    // Show FAB when user is more than ~200px above the bottom.
    final shouldShow = (max - current) > 200;
    if (shouldShow != _showScrollToBottom) {
      setState(() => _showScrollToBottom = shouldShow);
    }
  }

  void _send() {
    final text = _controller.text;
    if (text.trim().isEmpty) return;
    HapticFeedback.lightImpact();
    _controller.clear();
    ref.read(chatProvider.notifier).send(text);
    _scrollToBottom();
  }

  void _scrollToBottom({bool animated = true}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      final target = _scrollController.position.maxScrollExtent + 80;
      if (animated) {
        _scrollController.animateTo(
          target,
          duration: AppMotion.medium,
          curve: AppMotion.emphasized,
        );
      } else {
        _scrollController.jumpTo(target);
      }
    });
  }

  /// Tearoff-compatible wrapper for the scroll-to-bottom FAB.
  void _jumpToBottom() => _scrollToBottom();

  void _onVoiceResult(String text) {
    _controller.text = text;
    _send();
  }

  void _clearChat() {
    HapticFeedback.mediumImpact();
    ref.read(chatProvider.notifier).clearChat();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final locale = ref.watch(settingsProvider.select((s) => s.locale));

    // Narrow selects — each rebuilds only when the specific slice changes.
    final messages = ref.watch(chatProvider.select((s) => s.messages));
    final isLoading = ref.watch(chatProvider.select((s) => s.isLoading));

    // Auto-scroll when new messages arrive.
    ref.listen(chatProvider.select((s) => s.messages.length), (prev, next) {
      if ((prev ?? 0) < next) _scrollToBottom();
    });

    // Show error snackbar on error transitions.
    ref.listen(chatProvider.select((s) => s.error), (_, error) {
      if (error == null) return;
      final messenger = ScaffoldMessenger.of(context);
      messenger.hideCurrentSnackBar();
      messenger.showSnackBar(
        SnackBar(
          content: Text(error),
          action: SnackBarAction(
            label: 'Retry',
            onPressed: () => ref.read(chatProvider.notifier).retryLast(),
          ),
        ),
      );
      ref.read(chatProvider.notifier).clearError();
    });

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Icon(Icons.smart_toy, color: cs.primary, size: 28),
            const SizedBox(width: AppSpacing.sm),
            const Text('URA Tax Assistant'),
          ],
        ),
        actions: [
          PopupMenuButton<String>(
            initialValue: locale,
            tooltip: 'Language',
            icon: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.language, size: 20),
                const SizedBox(width: 2),
                Text(
                  locale.toUpperCase(),
                  style: Theme.of(
                    context,
                  ).textTheme.labelSmall?.copyWith(fontWeight: FontWeight.w600),
                ),
              ],
            ),
            onSelected: (v) {
              HapticFeedback.selectionClick();
              ref.read(settingsProvider.notifier).setLocale(v);
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'en', child: Text('English')),
              PopupMenuItem(value: 'lg', child: Text('Luganda')),
              PopupMenuItem(value: 'sw', child: Text('Swahili')),
              PopupMenuItem(value: 'nyn', child: Text('Runyankole')),
              PopupMenuItem(value: 'ach', child: Text('Acholi')),
            ],
          ),
          IconButton(
            icon: const Icon(Icons.add_comment_outlined),
            tooltip: 'New conversation',
            onPressed: _clearChat,
          ),
          const SizedBox(width: AppSpacing.xs),
        ],
      ),
      floatingActionButton: AnimatedOpacity(
        duration: AppMotion.fast,
        opacity: _showScrollToBottom ? 1 : 0,
        child: IgnorePointer(
          ignoring: !_showScrollToBottom,
          child: FloatingActionButton.small(
            onPressed: _jumpToBottom,
            tooltip: 'Jump to latest',
            child: const Icon(Icons.arrow_downward),
          ),
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: messages.isEmpty
                ? StarterPrompts(
                    onTap: (prompt) {
                      _controller.text = prompt;
                      _send();
                    },
                  )
                : _MessageList(
                    controller: _scrollController,
                    messages: messages,
                    isLoading: isLoading,
                    locale: locale,
                  ),
          ),
          _InputBar(
            controller: _controller,
            focusNode: _focusNode,
            isLoading: isLoading,
            onSend: _send,
            onVoiceResult: _onVoiceResult,
            locale: locale,
          ),
        ],
      ),
    );
  }
}

/// Message list with day-separator headers + typing indicator tail.
class _MessageList extends ConsumerWidget {
  const _MessageList({
    required this.controller,
    required this.messages,
    required this.isLoading,
    required this.locale,
  });

  final ScrollController controller;
  final List<ChatMessage> messages;
  final bool isLoading;
  final String locale;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Build a flat item list interleaving day-separators between messages.
    final items = <_ListItem>[];
    DateTime? lastDay;
    for (final msg in messages) {
      final day = DateTime(
        msg.timestamp.year,
        msg.timestamp.month,
        msg.timestamp.day,
      );
      if (lastDay != day) {
        items.add(_ListItem.separator(day));
        lastDay = day;
      }
      items.add(_ListItem.message(msg));
    }
    if (isLoading) items.add(const _ListItem.typing());

    return ListView.builder(
      controller: controller,
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.md,
        AppSpacing.sm,
        AppSpacing.md,
        AppSpacing.sm,
      ),
      itemCount: items.length,
      // Grow the cache extent so bubbles just off-screen are already
      // laid out — eliminates jank when the user slow-scrolls.
      cacheExtent: 500,
      itemBuilder: (context, i) {
        final item = items[i];
        return switch (item.kind) {
          _ItemKind.separator => RepaintBoundary(
            child: _DaySeparator(day: item.day!),
          ),
          _ItemKind.typing => const RepaintBoundary(child: _TypingIndicator()),
          _ItemKind.message => RepaintBoundary(
            child: MessageBubble(
              message: item.message!,
              locale: locale,
              onFeedback: (rating) => _onFeedback(ref, item.message!, rating),
            ),
          ),
        };
      },
    );
  }

  void _onFeedback(WidgetRef ref, ChatMessage msg, String rating) {
    if (msg.isUser || msg.response == null) return;
    HapticFeedback.lightImpact();
    // Find the preceding user message.
    String userQuery = '';
    final idx = messages.indexOf(msg);
    for (int j = idx - 1; j >= 0; j--) {
      if (messages[j].isUser) {
        userQuery = messages[j].content;
        break;
      }
    }
    ref
        .read(chatProvider.notifier)
        .submitFeedback(
          FeedbackPayload(
            messageId: msg.id,
            rating: rating,
            userQuery: userQuery,
            botReply: msg.content,
          ),
        );
  }
}

/// Internal discriminated union for the flat list items.
enum _ItemKind { message, separator, typing }

class _ListItem {
  const _ListItem._(this.kind, {this.message, this.day});
  const _ListItem.message(ChatMessage m)
    : this._(_ItemKind.message, message: m);
  const _ListItem.separator(DateTime d) : this._(_ItemKind.separator, day: d);
  const _ListItem.typing() : this._(_ItemKind.typing);

  final _ItemKind kind;
  final ChatMessage? message;
  final DateTime? day;
}

/// Centered day pill (Today / Yesterday / DD MMM YYYY).
class _DaySeparator extends StatelessWidget {
  const _DaySeparator({required this.day});

  final DateTime day;

  static const _months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];

  String _label() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final yesterday = today.subtract(const Duration(days: 1));
    if (day == today) return 'Today';
    if (day == yesterday) return 'Yesterday';
    return '${day.day} ${_months[day.month - 1]} ${day.year}';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: AppSpacing.md),
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.xs,
        ),
        decoration: BoxDecoration(
          color: cs.surfaceContainerHighest,
          borderRadius: AppRadius.chipRadius,
        ),
        child: Text(
          _label(),
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
            color: cs.onSurfaceVariant,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}

/// Chat input bar with text field, voice button, and send button.
class _InputBar extends StatelessWidget {
  const _InputBar({
    required this.controller,
    required this.focusNode,
    required this.isLoading,
    required this.onSend,
    required this.onVoiceResult,
    required this.locale,
  });

  final TextEditingController controller;
  final FocusNode focusNode;
  final bool isLoading;
  final VoidCallback onSend;
  final ValueChanged<String> onVoiceResult;
  final String locale;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bottom = MediaQuery.paddingOf(context).bottom;

    return Container(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.sm,
        AppSpacing.sm,
        AppSpacing.sm,
        AppSpacing.sm + bottom,
      ),
      decoration: BoxDecoration(
        color: cs.surface,
        boxShadow: [
          BoxShadow(
            color: cs.shadow.withValues(alpha: 0.08),
            blurRadius: 8,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: Row(
        children: [
          VoiceInputButton(onResult: onVoiceResult, locale: locale),
          const SizedBox(width: AppSpacing.xs),
          Expanded(
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              textInputAction: TextInputAction.send,
              maxLength: 2000,
              maxLines: 4,
              minLines: 1,
              onSubmitted: (_) => onSend(),
              decoration: InputDecoration(
                hintText: switch (locale) {
                  'lg' => 'Wandiika ekibuuzo kyo...',
                  'sw' => 'Andika swali lako kuhusu kodi...',
                  'nyn' => 'Andiika ekibuuzo kyaawe...',
                  'ach' => 'Co lok ma imito penyo...',
                  _ => 'Ask about URA taxes...',
                },
                counterText: '',
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.xs),
          IconButton.filled(
            onPressed: isLoading ? null : onSend,
            icon: isLoading
                ? SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: cs.onPrimary,
                    ),
                  )
                : const Icon(Icons.send),
            tooltip: 'Send',
          ),
        ],
      ),
    );
  }
}

/// Animated typing indicator (three dots).
class _TypingIndicator extends StatefulWidget {
  const _TypingIndicator();

  @override
  State<_TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<_TypingIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _anim;

  @override
  void initState() {
    super.initState();
    _anim = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat();
  }

  @override
  void dispose() {
    _anim.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Semantics(
      label: 'Assistant is typing',
      liveRegion: true,
      child: Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.symmetric(
            vertical: AppSpacing.xs,
            horizontal: AppSpacing.sm,
          ),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.md,
          ),
          decoration: BoxDecoration(
            color: cs.surfaceContainerHighest,
            borderRadius: AppRadius.bubbleRadius,
          ),
          child: ListenableBuilder(
            listenable: _anim,
            builder: (context, _) {
              return Row(
                mainAxisSize: MainAxisSize.min,
                children: List.generate(3, (i) {
                  final delay = i * 0.2;
                  final t = ((_anim.value - delay) % 1.0).clamp(0.0, 1.0);
                  final y = -4 * (1 - (2 * t - 1) * (2 * t - 1));
                  return Transform.translate(
                    offset: Offset(0, y),
                    child: Container(
                      margin: const EdgeInsets.symmetric(horizontal: 2),
                      width: 8,
                      height: 8,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: cs.onSurfaceVariant.withValues(alpha: 0.7),
                      ),
                    ),
                  );
                }),
              );
            },
          ),
        ),
      ),
    );
  }
}
