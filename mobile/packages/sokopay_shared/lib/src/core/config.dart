/// App-wide configuration.
class AppConfig {
  /// Base URL of the SokoPay API.
  ///
  /// - Android emulator reaches the host machine at 10.0.2.2.
  /// - iOS simulator can use http://localhost:8000.
  /// Override at build time with: --dart-define=API_BASE_URL=https://...
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000/api/v1',
  );

  /// Web pages (legal documents, payment pages): the API host without /api/v1.
  static String get webBaseUrl => apiBaseUrl.replaceFirst(RegExp(r'/api/v\d+/?$'), '');

  /// Shown on Help & legal. [VERIFY] the real support channels before launch.
  static const String supportContact = String.fromEnvironment(
    'SUPPORT_CONTACT',
    defaultValue: 'support@sokopay.com.gh',
  );
}
