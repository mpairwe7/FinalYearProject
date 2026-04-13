/// Consent data model.
///
/// Separate grants per purpose so withdrawal is surgical — withdrawing
/// "improve model" does not cancel microphone access. This matches the
/// backend `consent_purpose` enum at `App/backend/app/database.py:154`
/// while staying offline-first (grants are stored in
/// `flutter_secure_storage`, never synced).
library;

import 'package:flutter/foundation.dart';

/// Each category maps to one specific, concrete data use. Keep the
/// list short and non-overlapping — the legal standard is
/// "specific, informed, freely given".
enum ConsentCategory {
  /// Permission to capture audio via the device microphone.
  /// Without this, the mic button is disabled.
  voiceRecord,

  /// Permission to keep the text transcript (never the audio) in the
  /// local ledger for auditing and history. Audio is always ephemeral.
  voiceStoreTranscript,

  /// Permission to contribute anonymised error samples to future
  /// model improvements. Default-off; only takes effect when the
  /// backend offers an opt-in campaign.
  voiceImproveModel,
}

extension ConsentCategoryCopy on ConsentCategory {
  String get title => switch (this) {
    ConsentCategory.voiceRecord => 'Microphone access',
    ConsentCategory.voiceStoreTranscript => 'Save transcripts',
    ConsentCategory.voiceImproveModel => 'Improve the model',
  };

  String get description => switch (this) {
    ConsentCategory.voiceRecord =>
      'Let the app capture audio from your microphone while you '
          'hold or tap the voice button. Recording stops as soon '
          'as you release or pause speaking. Audio is processed '
          'entirely on your phone — nothing is uploaded.',
    ConsentCategory.voiceStoreTranscript =>
      'Keep the text version of what you said in the local chat '
          'history on this device. Transcripts never leave the '
          'phone. Turning this off clears the in-app history too.',
    ConsentCategory.voiceImproveModel =>
      'Occasionally let the app submit anonymised error samples '
          '(short text snippets, no audio, no identifiers) so the '
          'Luganda + English models can be improved over time. '
          'Off by default. You can turn this on later in Settings.',
  };

  bool get defaultGranted => switch (this) {
    // Mic and transcript default to granted *after* the consent
    // screen explicitly asks — they are baseline-necessary for the
    // voice feature to work. Model improvement is always off by
    // default.
    ConsentCategory.voiceRecord => true,
    ConsentCategory.voiceStoreTranscript => true,
    ConsentCategory.voiceImproveModel => false,
  };

  /// Storage key for the grant record (in `flutter_secure_storage`).
  String get storageKey => 'consent.$name';
}

/// An active consent record. Immutable — a new object is created on
/// every grant/withdraw so the ledger stamps a distinct seq number.
@immutable
class ConsentGrant {
  const ConsentGrant({
    required this.category,
    required this.granted,
    required this.grantId,
    required this.grantedAtMs,
    required this.version,
    this.withdrawnAtMs,
  });

  /// Which purpose this record covers.
  final ConsentCategory category;

  /// `true` when the user has actively granted this consent. When
  /// `false`, either the user withdrew it or the grant has never been
  /// given. Callers should treat the two identically.
  final bool granted;

  /// Opaque UUID — attached to ledger rows so later audits can tie an
  /// ASR event back to the specific grant that authorised it.
  final String grantId;

  /// When the grant was recorded. `grantedAtMs` is set once and never
  /// rewritten even after a withdraw/regrant cycle, so the ledger
  /// reflects the first time the user opted in for this grant ID.
  final int grantedAtMs;

  /// Present when the grant has been withdrawn.
  final int? withdrawnAtMs;

  /// Schema version of the consent copy. Bump when the wording
  /// changes so Settings can surface a "please re-consent" banner.
  final int version;

  Map<String, Object?> toJson() => {
    'category': category.name,
    'granted': granted,
    'grant_id': grantId,
    'granted_at_ms': grantedAtMs,
    'withdrawn_at_ms': withdrawnAtMs,
    'version': version,
  };

  static ConsentGrant fromJson(Map<String, Object?> json) => ConsentGrant(
    category: ConsentCategory.values.byName(json['category'] as String),
    granted: json['granted'] as bool,
    grantId: json['grant_id'] as String,
    grantedAtMs: json['granted_at_ms'] as int,
    version: json['version'] as int,
    withdrawnAtMs: json['withdrawn_at_ms'] as int?,
  );

  ConsentGrant copyWith({bool? granted, int? withdrawnAtMs}) => ConsentGrant(
    category: category,
    granted: granted ?? this.granted,
    grantId: grantId,
    grantedAtMs: grantedAtMs,
    version: version,
    withdrawnAtMs: withdrawnAtMs ?? this.withdrawnAtMs,
  );
}

/// Immutable state owned by [ConsentNotifier]. Contains one grant per
/// category (absent category → never granted).
@immutable
class ConsentState {
  const ConsentState({
    this.grants = const {},
    this.loaded = false,
    this.bootstrapped = false,
  });

  final Map<ConsentCategory, ConsentGrant> grants;

  /// `true` after the initial async load from secure storage has
  /// resolved. Widgets should render a skeleton while this is `false`.
  final bool loaded;

  /// `true` once the user has seen and acknowledged the consent
  /// screen at least once. The router uses this flag to decide
  /// whether to redirect to `/consent` on launch.
  final bool bootstrapped;

  bool isGranted(ConsentCategory category) =>
      grants[category]?.granted ?? false;

  ConsentState copyWith({
    Map<ConsentCategory, ConsentGrant>? grants,
    bool? loaded,
    bool? bootstrapped,
  }) => ConsentState(
    grants: grants ?? this.grants,
    loaded: loaded ?? this.loaded,
    bootstrapped: bootstrapped ?? this.bootstrapped,
  );
}

/// Current consent copy version. Bump when any [ConsentCategoryCopy]
/// text changes so historical grants can be re-confirmed.
const int kConsentVersion = 1;
