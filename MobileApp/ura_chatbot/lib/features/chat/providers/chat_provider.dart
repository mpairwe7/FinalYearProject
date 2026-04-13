import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../../core/config/api_config.dart';
import '../../../core/errors/error_handler.dart';
import '../../../core/inference/on_device_llm.dart';
import '../../../core/network/api_client.dart';
import '../../../core/storage/local_storage.dart';
import '../models/chat_models.dart';

/// Immutable chat state.
///
/// Intentionally *not* wrapped in ``AsyncValue`` because the chat surface
/// can hold partial history (many messages) alongside a single in-flight
/// request. The explicit [isLoading] + [error] fields match the real
/// interaction model: an error on one request should not blow away the
/// existing message history.
class ChatState {
  final List<ChatMessage> messages;
  final bool isLoading;
  final String? conversationId;
  final String? error;
  final bool offlineReady;

  const ChatState({
    this.messages = const [],
    this.isLoading = false,
    this.conversationId,
    this.error,
    this.offlineReady = false,
  });

  ChatState copyWith({
    List<ChatMessage>? messages,
    bool? isLoading,
    String? conversationId,
    Object? error = _sentinel,
    bool? offlineReady,
  }) => ChatState(
    messages: messages ?? this.messages,
    isLoading: isLoading ?? this.isLoading,
    conversationId: conversationId ?? this.conversationId,
    error: identical(error, _sentinel) ? this.error : error as String?,
    offlineReady: offlineReady ?? this.offlineReady,
  );

  // Sentinel so copyWith(error: null) can explicitly clear the field.
  static const Object _sentinel = Object();
}

/// Riverpod 2.6 [Notifier] (replaces deprecated [StateNotifier]).
///
/// Uses ``NotifierProvider`` without ``autoDispose`` because the chat
/// session should outlive tab-switches — the user expects their chat
/// history to still be there when they return from the FAQ tab.
class ChatNotifier extends Notifier<ChatState> {
  late final Dio _dio;
  late final OnDeviceLlm _onDeviceLlm;
  static const _uuid = Uuid();
  static const _maxMessages = 200;

  @override
  ChatState build() {
    _dio = ref.read(apiClientProvider);
    _onDeviceLlm = OnDeviceLlm();

    // Kick off on-device LLM init in the background — state will
    // update to ``offlineReady: true`` when the model is ready.
    // ``ref.onDispose`` isn't needed for a singleton notifier.
    _bootstrapOfflineLlm();

    return const ChatState();
  }

  Future<void> _bootstrapOfflineLlm() async {
    try {
      final ok = await _onDeviceLlm.initialize();
      if (ok) {
        state = state.copyWith(offlineReady: true);
      }
    } catch (e, s) {
      // Non-fatal: offline mode just won't be available.
      AppErrorHandler.report(e, s);
    }
  }

  /// Send a user message and get an AI response.
  Future<void> send(String text, {int topK = ApiConfig.defaultTopK}) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty || state.isLoading) return;

    // Client-side length validation (mirrors backend MAX_INPUT_LENGTH)
    if (trimmed.length > ApiConfig.maxInputLength) {
      state = state.copyWith(
        error: 'Message too long (max ${ApiConfig.maxInputLength} characters)',
      );
      return;
    }

    final userMsg = ChatMessage(
      id: _uuid.v4(),
      role: 'user',
      content: trimmed,
      timestamp: DateTime.now(),
    );

    state = state.copyWith(
      messages: [...state.messages, userMsg],
      isLoading: true,
      error: null,
    );

    _trackEvent('chat_message_sent', {'locale': LocalStorage.locale});

    try {
      final res = await _dio.post<Map<String, dynamic>>(
        ApiConfig.chat,
        data: {
          'message': trimmed,
          if (state.conversationId != null)
            'conversation_id': state.conversationId,
          'top_k': topK.clamp(1, ApiConfig.maxTopK),
          'locale': LocalStorage.locale,
        },
      );

      final chatRes = ChatResponse.fromJson(res.data ?? const {});
      _addAssistantMessage(chatRes);
    } on DioException catch (e, s) {
      // Network / connection error → fall back to on-device inference
      final isNetworkError =
          e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.connectionError ||
          e.type == DioExceptionType.sendTimeout;

      if (isNetworkError && state.offlineReady) {
        await _generateOffline(trimmed);
        return;
      }

      final apiErr = e.error;
      state = state.copyWith(
        isLoading: false,
        error: apiErr is ApiException
            ? apiErr.message
            : 'Failed to get response. Please try again.',
      );
      AppErrorHandler.report(e, s);
    } catch (e, s) {
      state = state.copyWith(
        isLoading: false,
        error: 'Something went wrong. Please try again.',
      );
      AppErrorHandler.report(e, s);
    }
  }

  /// Retry the last user message. If the last message is already a user
  /// turn that failed, re-send its content.
  Future<void> retryLast() async {
    final msgs = state.messages;
    if (msgs.isEmpty) return;
    final last = msgs.last;
    if (!last.isUser) return; // nothing to retry
    // Remove the failed user message so ``send`` re-adds it cleanly.
    state = state.copyWith(messages: msgs.sublist(0, msgs.length - 1));
    await send(last.content);
  }

  /// Add an assistant message from a [ChatResponse] and cap history.
  void _addAssistantMessage(ChatResponse chatRes) {
    final assistantMsg = ChatMessage(
      id: _uuid.v4(),
      role: 'assistant',
      content: chatRes.reply,
      timestamp: DateTime.now(),
      response: chatRes,
    );

    var msgs = [...state.messages, assistantMsg];
    if (msgs.length > _maxMessages) {
      msgs = msgs.sublist(msgs.length - _maxMessages);
    }

    state = state.copyWith(
      messages: msgs,
      isLoading: false,
      conversationId: chatRes.conversationId ?? state.conversationId,
    );
  }

  /// On-device Gemma-2B inference fallback (offline mode).
  Future<void> _generateOffline(String query) async {
    try {
      final result = await _onDeviceLlm.generate(
        query: query,
        locale: LocalStorage.locale,
      );

      if (result == null || result.text.isEmpty) {
        state = state.copyWith(
          isLoading: false,
          error: 'Offline inference failed. Please check your connection.',
        );
        return;
      }

      _trackEvent('offline_inference', {
        'latency_ms': result.latencyMs,
        'tokens': result.tokensGenerated,
      });

      final chatRes = ChatResponse(
        reply: result.text,
        retrievalMode: 'offline',
        model: 'gemma-2-2b-it',
        locale: LocalStorage.locale,
      );
      _addAssistantMessage(chatRes);
    } catch (e, s) {
      AppErrorHandler.report(e, s);
      state = state.copyWith(
        isLoading: false,
        error: 'Offline mode unavailable. Please try again when connected.',
      );
    }
  }

  /// Submit feedback (thumbs up/down) for an assistant message.
  Future<bool> submitFeedback(FeedbackPayload feedback) async {
    try {
      await _dio.post<Map<String, dynamic>>(
        ApiConfig.feedback,
        data: {...feedback.toJson(), 'session_id': LocalStorage.sessionId},
      );
      _trackEvent('feedback_submitted', {'rating': feedback.rating});
      return true;
    } catch (e, s) {
      AppErrorHandler.report(e, s);
      return false;
    }
  }

  /// Add a follow-up comment to existing feedback.
  Future<bool> addFeedbackComment(String messageId, String comment) async {
    try {
      await _dio.patch<Map<String, dynamic>>(
        ApiConfig.feedbackComment(messageId),
        data: {'comment': comment},
      );
      return true;
    } catch (e, s) {
      AppErrorHandler.report(e, s);
      return false;
    }
  }

  /// Clear chat history and start a new conversation.
  void clearChat() {
    state = const ChatState().copyWith(offlineReady: state.offlineReady);
  }

  /// Fire-and-forget analytics event.
  void _trackEvent(String type, [Map<String, dynamic>? data]) {
    _dio
        .post<Map<String, dynamic>>(
          ApiConfig.analyticsEvent,
          data: {
            'event_type': type,
            'event_data': data ?? {},
            'session_id': LocalStorage.sessionId,
          },
        )
        .ignore();
  }

  void clearError() => state = state.copyWith(error: null);

  /// Voice-related analytics events — fire-and-forget via existing
  /// _trackEvent helper. Called from the voice input button and TTS
  /// playback button so the chat provider is the single funnel for
  /// all analytics in the chat surface.
  void trackVoiceEvent(String type, [Map<String, dynamic>? data]) {
    _trackEvent(type, data);
  }

  /// Export the current conversation as a PDF. Returns raw bytes on
  /// success, null on failure.
  Future<List<int>?> exportConversation() async {
    if (state.messages.isEmpty) return null;
    try {
      final msgs = state.messages
          .map(
            (m) => {
              'role': m.role,
              'content': m.content,
              'timestamp': m.timestamp.toIso8601String(),
            },
          )
          .toList();
      final res = await _dio.post<List<int>>(
        ApiConfig.exportConversation,
        data: {'messages': msgs, 'session_id': LocalStorage.sessionId},
        options: Options(responseType: ResponseType.bytes),
      );
      _trackEvent('conversation_exported');
      return res.data;
    } catch (e, s) {
      AppErrorHandler.report(e, s);
      return null;
    }
  }
}

/// Typed provider (replaces deprecated ``StateNotifierProvider``).
final chatProvider = NotifierProvider<ChatNotifier, ChatState>(
  ChatNotifier.new,
);
