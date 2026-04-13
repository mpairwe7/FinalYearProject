/// Typed, locally-persisted feature flag store.
///
/// 2026 pattern: mirrors the backend `App/backend/app/flags.py` registry
/// so the mobile app can gate risky features (voice, TTS, model auto-
/// download, dev tooling) without a hot deploy. Flags live in
/// SharedPreferences via [LocalStorage] so they survive app restarts.
///
/// Dev-only flags (like [speechLabDev]) are compile-time gated with
/// `kSpeechLabEnabled` so release builds cannot activate them even if
/// a value is somehow persisted.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Compile-time guard for developer tools. Release builds set
/// `dart.vm.product=true`, which flips this to `false` and makes the
/// SpeechLab eval screen, verbose loggers, and other internal surfaces
/// unreachable regardless of runtime flag state.
const bool kSpeechLabEnabled = !bool.fromEnvironment('dart.vm.product');

/// Canonical flag definitions. Add new flags here (don't read keys
/// directly at the call site) so they stay discoverable and auditable.
class FeatureFlag {
  const FeatureFlag({
    required this.key,
    required this.defaultValue,
    required this.description,
    this.devOnly = false,
  });

  final String key;
  final bool defaultValue;
  final String description;

  /// `true` when the flag must be force-disabled in release builds
  /// (useful for screens that leak internal state).
  final bool devOnly;
}

class FeatureFlags {
  FeatureFlags._();

  /// Master switch for the entire voice subsystem (ASR + TTS + LID).
  /// When `false`, the mic button is hidden and all speech engines
  /// are skipped at bootstrap.
  static const voiceEnabled = FeatureFlag(
    key: 'flag.voice_enabled',
    defaultValue: true,
    description: 'Enable on-device voice input (ASR) and output (TTS).',
  );

  /// Enable text-to-speech playback on assistant turns. Independent of
  /// [voiceEnabled] because a user may want to dictate but read replies.
  static const ttsEnabled = FeatureFlag(
    key: 'flag.tts_enabled',
    defaultValue: true,
    description: 'Render a Listen button on assistant messages.',
  );

  /// Allow the model manager to auto-download missing ASR/TTS bundles
  /// on first use. When `false`, the user must trigger downloads
  /// manually from the Model Manager screen.
  static const asrAutoDownload = FeatureFlag(
    key: 'flag.asr_auto_download',
    defaultValue: true,
    description: 'Fetch Whisper/MMS-TTS bundles on first mic tap.',
  );

  /// Expose the SpeechLab developer screen for on-device WER/CER
  /// evaluation, cache inspection, and ledger dumps. Dev builds only.
  static const speechLabDev = FeatureFlag(
    key: 'flag.speech_lab_dev',
    defaultValue: false,
    description: 'Show the SpeechLab self-test and diagnostics screen.',
    devOnly: true,
  );

  /// All registered flags, for iteration (settings UI, tests, diags).
  static const List<FeatureFlag> all = [
    voiceEnabled,
    ttsEnabled,
    asrAutoDownload,
    speechLabDev,
  ];
}

/// Read / write flag values against a [SharedPreferences] instance.
///
/// Kept outside of [FeatureFlags] so the class stays a pure namespace
/// of flag definitions and the storage side effects live in one place.
class FeatureFlagStore {
  FeatureFlagStore(this._prefs);

  final SharedPreferences _prefs;

  bool get(FeatureFlag flag) {
    // Release builds can never enable devOnly flags.
    if (flag.devOnly && !kSpeechLabEnabled) return false;
    return _prefs.getBool(flag.key) ?? flag.defaultValue;
  }

  Future<void> set(FeatureFlag flag, bool value) async {
    if (flag.devOnly && !kSpeechLabEnabled) return;
    await _prefs.setBool(flag.key, value);
  }

  Map<String, bool> snapshot() => {
    for (final f in FeatureFlags.all) f.key: get(f),
  };
}

/// Riverpod entry point. Eagerly resolves SharedPreferences once so
/// the store is available synchronously from any notifier's `build`.
final featureFlagStoreProvider = Provider<FeatureFlagStore>((ref) {
  throw StateError(
    'featureFlagStoreProvider must be overridden in main() after '
    'SharedPreferences.getInstance() has completed.',
  );
});
