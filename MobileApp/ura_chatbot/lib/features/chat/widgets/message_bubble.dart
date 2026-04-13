import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../core/theme/tokens.dart';
import '../models/chat_models.dart';
import 'citation_card.dart';
import 'escalation_banner.dart';
import 'faithfulness_badge.dart';
import 'feedback_buttons.dart';
import 'tts_playback_button.dart';

/// A single chat bubble — user (right-aligned primary) or assistant
/// (left-aligned surface variant with assistant extras: faithfulness
/// badge, citations, escalation banner, feedback buttons).
///
/// Long-press copies the bubble text and fires a light haptic.
class MessageBubble extends StatelessWidget {
  const MessageBubble({
    super.key,
    required this.message,
    this.onFeedback,
    this.locale = 'en',
  });

  final ChatMessage message;
  final ValueChanged<String>? onFeedback;
  final String locale;

  String _formatTime(DateTime t) {
    final h = t.hour.toString().padLeft(2, '0');
    final m = t.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }

  void _copyToClipboard(BuildContext context) {
    HapticFeedback.lightImpact();
    Clipboard.setData(ClipboardData(text: message.content));
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        const SnackBar(
          content: Text('Copied to clipboard'),
          duration: Duration(seconds: 1),
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;
    final isUser = message.isUser;
    final maxBubbleWidth = MediaQuery.sizeOf(context).width * 0.82;

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: BoxConstraints(maxWidth: maxBubbleWidth),
        margin: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        child: Column(
          crossAxisAlignment: isUser
              ? CrossAxisAlignment.end
              : CrossAxisAlignment.start,
          children: [
            // Main bubble
            Material(
              color: isUser ? cs.primaryContainer : cs.surfaceContainerHighest,
              borderRadius: BorderRadius.only(
                topLeft: const Radius.circular(AppRadius.lg + 2),
                topRight: const Radius.circular(AppRadius.lg + 2),
                bottomLeft: Radius.circular(
                  isUser ? AppRadius.lg + 2 : AppRadius.xs,
                ),
                bottomRight: Radius.circular(
                  isUser ? AppRadius.xs : AppRadius.lg + 2,
                ),
              ),
              child: InkWell(
                borderRadius: AppRadius.bubbleRadius,
                onLongPress: () => _copyToClipboard(context),
                child: Padding(
                  padding: AppSpacing.bubbleInner,
                  child: SelectableText(
                    message.content,
                    style: tt.bodyMedium?.copyWith(
                      color: isUser
                          ? cs.onPrimaryContainer
                          : cs.onSurfaceVariant,
                      height: 1.4,
                    ),
                  ),
                ),
              ),
            ),

            // Timestamp — aligned to the trailing edge of the bubble.
            Padding(
              padding: const EdgeInsets.only(
                top: AppSpacing.xxs,
                left: AppSpacing.sm,
                right: AppSpacing.sm,
              ),
              child: Text(
                _formatTime(message.timestamp),
                style: tt.labelSmall?.copyWith(
                  color: cs.onSurfaceVariant.soft,
                  fontSize: 10,
                ),
              ),
            ),

            // Assistant extras
            if (!isUser && message.response != null) ...[
              const SizedBox(height: AppSpacing.xs),

              // Escalation banner
              if (message.response!.escalationRequired)
                EscalationBanner(reason: message.response!.escalationReason),

              // Faithfulness badge + retrieval mode
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  FaithfulnessBadge(
                    score: message.response!.faithfulnessScore,
                    retrievalMode: message.response!.retrievalMode,
                  ),
                ],
              ),

              // Citations
              if (message.response!.citations.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xs),
                CitationList(citations: message.response!.citations),
              ],

              // TTS playback button
              TtsPlaybackButton(
                text: message.content,
                locale: locale,
                messageId: message.id,
              ),

              // Feedback buttons
              const SizedBox(height: AppSpacing.xs),
              FeedbackButtons(messageId: message.id, onFeedback: onFeedback),
            ],
          ],
        ),
      ),
    );
  }
}
