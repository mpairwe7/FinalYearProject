// Data models mirroring the FastAPI Pydantic v2 schemas.

class Citation {
  final String ref;
  final String source;
  final String page;
  final String section;
  final String passage;

  const Citation({
    required this.ref,
    required this.source,
    this.page = '',
    this.section = '',
    this.passage = '',
  });

  factory Citation.fromJson(Map<String, dynamic> j) => Citation(
        ref: j['ref'] as String? ?? '',
        source: j['source'] as String? ?? '',
        page: j['page'] as String? ?? '',
        section: j['section'] as String? ?? '',
        passage: j['passage'] as String? ?? '',
      );
}

class ChatResponse {
  final String reply;
  final List<String> sources;
  final List<Citation> citations;
  final double? faithfulnessScore;
  final String retrievalMode;
  final String model;
  final String? conversationId;
  final String locale;
  final bool escalationRequired;
  final String escalationReason;

  const ChatResponse({
    required this.reply,
    this.sources = const [],
    this.citations = const [],
    this.faithfulnessScore,
    this.retrievalMode = 'keyword',
    this.model = 'ura-qwen2.5-3b-instruct',
    this.conversationId,
    this.locale = 'en',
    this.escalationRequired = false,
    this.escalationReason = '',
  });

  factory ChatResponse.fromJson(Map<String, dynamic> j) => ChatResponse(
        reply: j['reply'] as String? ?? '',
        sources: (j['sources'] as List?)?.cast<String>() ?? [],
        citations: (j['citations'] as List?)
                ?.map((c) => Citation.fromJson(c as Map<String, dynamic>))
                .toList() ??
            [],
        faithfulnessScore: (j['faithfulness_score'] as num?)?.toDouble(),
        retrievalMode: j['retrieval_mode'] as String? ?? 'keyword',
        model: j['model'] as String? ?? 'ura-qwen2.5-3b-instruct',
        conversationId: j['conversation_id'] as String?,
        locale: j['locale'] as String? ?? 'en',
        escalationRequired: j['escalation_required'] as bool? ?? false,
        escalationReason: j['escalation_reason'] as String? ?? '',
      );
}

/// A single chat turn (user or assistant).
class ChatMessage {
  final String id;
  final String role; // 'user' | 'assistant'
  final String content;
  final DateTime timestamp;
  final ChatResponse? response; // non-null for assistant messages

  const ChatMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.timestamp,
    this.response,
  });

  bool get isUser => role == 'user';
}

/// Feedback on an assistant message.
class FeedbackPayload {
  final String messageId;
  final String rating; // 'up' | 'down'
  final String comment;
  final String userQuery;
  final String botReply;

  const FeedbackPayload({
    required this.messageId,
    required this.rating,
    this.comment = '',
    this.userQuery = '',
    this.botReply = '',
  });

  Map<String, dynamic> toJson() => {
        'message_id': messageId,
        'rating': rating,
        'comment': comment,
        'user_query': userQuery,
        'bot_reply': botReply,
      };
}
