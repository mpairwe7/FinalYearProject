/// API configuration constants.
///
/// **Production:** override [baseUrl] at build time via
/// ```bash
/// flutter build apk --release --dart-define=API_URL=https://api.example.com
/// ```
///
/// **Development (emulator):** defaults to ``http://10.0.2.2:8000`` which
/// is the Android emulator's loopback to the host machine.
///
/// **Development (real device):** pass your host machine's LAN IP via
/// ``--dart-define=DEV_API_URL=http://192.168.1.42:8000``. On first launch,
/// the app uses this if [API_URL] is unset and the platform is Android
/// physical / iOS physical.
class ApiConfig {
  const ApiConfig._();

  /// Primary API URL — override at build time with
  /// ``--dart-define=API_URL=https://...``.
  static const String baseUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: String.fromEnvironment(
      'DEV_API_URL',
      // Android emulator → host machine loopback.
      defaultValue: 'http://10.0.2.2:8000',
    ),
  );

  /// Whether the app was built with a user-supplied API URL.
  ///
  /// When ``false`` the app falls back to the emulator default, which
  /// is useful for quick dev builds on the emulator but will NOT work
  /// on a real device unless the developer exports ``DEV_API_URL``.
  /// Surface this in an error view so users (and devs) know why the
  /// server is unreachable.
  static const bool hasUserBaseUrl =
      bool.hasEnvironment('API_URL') || bool.hasEnvironment('DEV_API_URL');

  static const Duration connectTimeout = Duration(seconds: 10);
  static const Duration receiveTimeout = Duration(seconds: 30);

  // Endpoints (only those used by the mobile app).
  static const String ready = '/ready';
  static const String chat = '/v1/chat';
  static const String tags = '/tags';
  static String faq(String tag) => '/faq/$tag';
  static const String feedback = '/v1/feedback';
  static String feedbackComment(String id) => '/v1/feedback/$id/comment';
  static const String analyticsEvent = '/v1/analytics/event';

  // Export endpoints.
  static const String exportConversation = '/v1/export/conversation';
  static const String exportTaxSummary = '/v1/export/tax-summary';

  // Constraints (mirror backend validation).
  static const int maxInputLength = 2000;
  static const int maxTopK = 10;
  static const int defaultTopK = 4;
}
