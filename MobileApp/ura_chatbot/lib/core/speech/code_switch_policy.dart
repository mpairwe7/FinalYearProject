/// Code-switch routing policy.
///
/// Given an ASR transcript and a [LidResult], decides:
///   - What locale to use for the reply request.
///   - Whether to surface a "Switched to Luganda" toast.
///   - Whether to re-run ASR with a different decoder adapter (future).
library;

import 'package:flutter/foundation.dart';

import 'lid.dart';

/// Decision emitted by [CodeSwitchPolicy].
@immutable
class CodeSwitchDecision {
  const CodeSwitchDecision({
    required this.replyLocale,
    required this.shouldNotifyUser,
    this.toastMessage,
  });

  /// Locale to use for the backend chat request.
  final String replyLocale;

  /// Whether the UI should show a toast about the language switch.
  final bool shouldNotifyUser;

  /// Human-readable message for the toast (null when no notification).
  final String? toastMessage;
}

class CodeSwitchPolicy {
  CodeSwitchPolicy({this.currentLocale = 'en'});

  /// The locale the app currently believes the user is speaking.
  String currentLocale;

  /// Evaluate a transcript + LID result and return a routing decision.
  CodeSwitchDecision evaluate(String transcript, LidResult lid) {
    // If the LID detects a different primary language with reasonable
    // confidence, switch the reply locale and notify the user.
    if (lid.language != currentLocale && lid.confidence > 0.3) {
      currentLocale = lid.language;
      final langName = _langName(lid.language);
      return CodeSwitchDecision(
        replyLocale: lid.language,
        shouldNotifyUser: true,
        toastMessage: 'Switched to $langName',
      );
    }

    // Code-switched text with the same primary language — keep locale,
    // but if it's truly mixed, mention it subtly.
    if (lid.isCodeSwitched) {
      return CodeSwitchDecision(
        replyLocale: currentLocale,
        shouldNotifyUser: false,
      );
    }

    // No switch needed.
    return CodeSwitchDecision(
      replyLocale: currentLocale,
      shouldNotifyUser: false,
    );
  }

  static String _langName(String locale) => switch (locale) {
    'en' => 'English',
    'lg' => 'Luganda',
    'sw' => 'Swahili',
    'nyn' => 'Runyankole',
    'ach' => 'Acholi',
    _ => locale.toUpperCase(),
  };
}
