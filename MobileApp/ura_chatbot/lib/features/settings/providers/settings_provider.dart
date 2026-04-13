import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/storage/local_storage.dart';

/// Persistent UI preferences (locale, theme).
///
/// Immutable state class — updated by [SettingsNotifier] via
/// ``copyWith``. Both fields are persisted to SharedPreferences
/// via [LocalStorage] so they survive app restarts.
/// ASR model tier preference.
enum AsrModelTier { small, tiny }

class AppSettings {
  final String locale;
  final ThemeMode themeMode;
  final bool ttsEnabled;
  final AsrModelTier asrModelTier;
  final bool asrAutoDownload;

  const AppSettings({
    this.locale = 'en',
    this.themeMode = ThemeMode.system,
    this.ttsEnabled = true,
    this.asrModelTier = AsrModelTier.small,
    this.asrAutoDownload = true,
  });

  AppSettings copyWith({
    String? locale,
    ThemeMode? themeMode,
    bool? ttsEnabled,
    AsrModelTier? asrModelTier,
    bool? asrAutoDownload,
  }) => AppSettings(
    locale: locale ?? this.locale,
    themeMode: themeMode ?? this.themeMode,
    ttsEnabled: ttsEnabled ?? this.ttsEnabled,
    asrModelTier: asrModelTier ?? this.asrModelTier,
    asrAutoDownload: asrAutoDownload ?? this.asrAutoDownload,
  );
}

/// Riverpod 2.6 [Notifier] (replaces deprecated [StateNotifier]).
class SettingsNotifier extends Notifier<AppSettings> {
  // Matches backend pattern: ^[a-z]{2}(-[A-Z]{2})?$
  // Accept 2-3 letter ISO 639 codes (en, lg, sw, nyn, ach).
  static final _localeRe = RegExp(r'^[a-z]{2,3}(-[A-Z]{2})?$');

  @override
  AppSettings build() {
    return AppSettings(
      locale: LocalStorage.locale,
      themeMode: _parseThemeMode(LocalStorage.themeMode),
    );
  }

  Future<void> setLocale(String locale) async {
    if (!_localeRe.hasMatch(locale)) return; // reject invalid locales
    if (locale == state.locale) return; // no-op
    await LocalStorage.setLocale(locale);
    state = state.copyWith(locale: locale);
  }

  Future<void> setThemeMode(ThemeMode mode) async {
    if (mode == state.themeMode) return;
    await LocalStorage.setThemeMode(mode.name);
    state = state.copyWith(themeMode: mode);
  }

  Future<void> setTtsEnabled(bool enabled) async {
    if (enabled == state.ttsEnabled) return;
    await LocalStorage.setTtsEnabled(enabled);
    state = state.copyWith(ttsEnabled: enabled);
  }

  Future<void> setAsrModelTier(AsrModelTier tier) async {
    if (tier == state.asrModelTier) return;
    await LocalStorage.setAsrModelTier(tier.name);
    state = state.copyWith(asrModelTier: tier);
  }

  Future<void> setAsrAutoDownload(bool auto) async {
    if (auto == state.asrAutoDownload) return;
    await LocalStorage.setAsrAutoDownload(auto);
    state = state.copyWith(asrAutoDownload: auto);
  }

  static ThemeMode _parseThemeMode(String s) => switch (s) {
    'light' => ThemeMode.light,
    'dark' => ThemeMode.dark,
    _ => ThemeMode.system,
  };
}

final settingsProvider = NotifierProvider<SettingsNotifier, AppSettings>(
  SettingsNotifier.new,
);
