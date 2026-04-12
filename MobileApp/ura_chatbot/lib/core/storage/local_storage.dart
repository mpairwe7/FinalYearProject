import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

/// Thin wrapper around SharedPreferences with session management.
class LocalStorage {
  LocalStorage._();

  static late final SharedPreferences _prefs;
  static late final String _sessionId;

  static const _keySessionId = 'session_id';
  static const _keyLocale = 'locale';
  static const _keyThemeMode = 'theme_mode';
  /// Must be called once before runApp().
  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();

    // Persistent session ID — survives app restarts.
    var sid = _prefs.getString(_keySessionId);
    if (sid == null) {
      sid = const Uuid().v4();
      await _prefs.setString(_keySessionId, sid);
    }
    _sessionId = sid;
  }

  static String get sessionId => _sessionId;

  // --- Locale ---
  static String get locale => _prefs.getString(_keyLocale) ?? 'en';
  static Future<void> setLocale(String v) => _prefs.setString(_keyLocale, v);

  // --- Theme ---
  static String get themeMode => _prefs.getString(_keyThemeMode) ?? 'system';
  static Future<void> setThemeMode(String v) =>
      _prefs.setString(_keyThemeMode, v);

  // --- Voice / Speech ---
  static const _keyTtsEnabled = 'tts_enabled';
  static const _keyAsrModelTier = 'asr_model_tier';
  static const _keyAsrAutoDownload = 'asr_auto_download';

  static bool get ttsEnabled => _prefs.getBool(_keyTtsEnabled) ?? true;
  static Future<void> setTtsEnabled(bool v) =>
      _prefs.setBool(_keyTtsEnabled, v);

  static String get asrModelTier =>
      _prefs.getString(_keyAsrModelTier) ?? 'small';
  static Future<void> setAsrModelTier(String v) =>
      _prefs.setString(_keyAsrModelTier, v);

  static bool get asrAutoDownload =>
      _prefs.getBool(_keyAsrAutoDownload) ?? true;
  static Future<void> setAsrAutoDownload(bool v) =>
      _prefs.setBool(_keyAsrAutoDownload, v);
}
