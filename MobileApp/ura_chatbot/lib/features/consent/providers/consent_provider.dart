/// Riverpod provider for per-purpose voice consent.
///
/// Persists grants to `flutter_secure_storage` (encrypted at rest on
/// both Android KeyStore and iOS Keychain) and writes every grant
/// mutation to the local hash-chained ledger for audit.
library;

import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:uuid/uuid.dart';

import '../../../core/audit/local_ledger.dart';
import '../../../core/errors/error_handler.dart';
import '../models/consent_models.dart';

/// Shared secure storage used by the consent subsystem. Exposed as a
/// provider so tests can override it with an in-memory mock.
final consentStorageProvider = Provider<FlutterSecureStorage>((ref) {
  return const FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(accessibility: KeychainAccessibility.unlocked),
  );
});

class ConsentNotifier extends Notifier<ConsentState> {
  static const _uuid = Uuid();
  static const _bootstrappedKey = 'consent.bootstrapped';

  late final FlutterSecureStorage _storage;

  @override
  ConsentState build() {
    _storage = ref.read(consentStorageProvider);
    _load();
    return const ConsentState();
  }

  /// Hydrate state from secure storage. Called once from [build].
  Future<void> _load() async {
    try {
      final grants = <ConsentCategory, ConsentGrant>{};
      for (final category in ConsentCategory.values) {
        final raw = await _storage.read(key: category.storageKey);
        if (raw == null) continue;
        try {
          final json = jsonDecode(raw) as Map<String, Object?>;
          grants[category] = ConsentGrant.fromJson(json);
        } catch (e, s) {
          // Corrupted grant — skip it, will re-prompt on next screen open.
          AppErrorHandler.report(e, s);
        }
      }
      final bootstrapped =
          (await _storage.read(key: _bootstrappedKey)) == 'true';
      state = state.copyWith(
        grants: grants,
        loaded: true,
        bootstrapped: bootstrapped,
      );
    } catch (e, s) {
      AppErrorHandler.report(e, s);
      state = state.copyWith(loaded: true);
    }
  }

  /// Grant (or re-grant) a consent category. Returns the new grant ID
  /// so callers can tie downstream events to this specific grant.
  Future<String> grant(ConsentCategory category) async {
    final existing = state.grants[category];
    final now = DateTime.now().millisecondsSinceEpoch;

    // Re-granting after withdrawal bumps the grant ID so the ledger
    // shows a clean break between the old and new consent periods.
    final grant = ConsentGrant(
      category: category,
      granted: true,
      grantId: existing != null && !existing.granted
          ? _uuid.v4()
          : existing?.grantId ?? _uuid.v4(),
      grantedAtMs: existing?.granted == true ? existing!.grantedAtMs : now,
      version: kConsentVersion,
    );

    await _persist(grant);
    _updateStateWith(grant);
    await _logToLedger(LedgerEventType.consentGrant, grant);
    return grant.grantId;
  }

  /// Withdraw a previously granted consent. Idempotent.
  Future<void> withdraw(ConsentCategory category) async {
    final existing = state.grants[category];
    if (existing == null || !existing.granted) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    final withdrawn = existing.copyWith(
      granted: false,
      withdrawnAtMs: now,
    );
    await _persist(withdrawn);
    _updateStateWith(withdrawn);
    await _logToLedger(LedgerEventType.consentWithdraw, withdrawn);
  }

  /// Mark the initial consent flow complete. Called once from the
  /// consent screen's "Continue" button regardless of which individual
  /// toggles were flipped.
  Future<void> markBootstrapped() async {
    await _storage.write(key: _bootstrappedKey, value: 'true');
    state = state.copyWith(bootstrapped: true);
  }

  /// Test-only helper to clear every grant.
  Future<void> debugReset() async {
    for (final category in ConsentCategory.values) {
      await _storage.delete(key: category.storageKey);
    }
    await _storage.delete(key: _bootstrappedKey);
    state = const ConsentState(loaded: true);
  }

  Future<void> _persist(ConsentGrant grant) async {
    await _storage.write(
      key: grant.category.storageKey,
      value: jsonEncode(grant.toJson()),
    );
  }

  void _updateStateWith(ConsentGrant grant) {
    final next = Map<ConsentCategory, ConsentGrant>.from(state.grants);
    next[grant.category] = grant;
    state = state.copyWith(grants: next);
  }

  Future<void> _logToLedger(String type, ConsentGrant grant) async {
    try {
      await LocalLedger.append(type, {
        'category': grant.category.name,
        'grant_id': grant.grantId,
        'version': grant.version,
      });
    } catch (e, s) {
      // Ledger failure is non-fatal to the consent flow itself — the
      // user's choice is still persisted. Surface for later triage.
      AppErrorHandler.report(e, s);
    }
  }
}

final consentProvider =
    NotifierProvider<ConsentNotifier, ConsentState>(ConsentNotifier.new);

/// Convenience selector: any voice-related grant revoked?
final voiceRecordGrantedProvider = Provider<bool>((ref) {
  return ref.watch(
    consentProvider.select((s) => s.isGranted(ConsentCategory.voiceRecord)),
  );
});
