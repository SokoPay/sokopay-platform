import 'package:dio/dio.dart';

/// Turns any API failure into one short, customer-friendly sentence.
///
/// Handles the three shapes the SokoPay backend returns:
///  * `{"error": "not_licensed"}` (403) — a feature built but not yet licensed by BoG
///  * `{"error": "<message>"}`            — a domain error, already human-readable
///  * `{"field": ["<message>"]}`         — DRF validation errors
String apiErrorMessage(Object error,
    {String fallback = 'Something went wrong. Please try again.'}) {
  if (error is! DioException) return fallback;

  switch (error.type) {
    case DioExceptionType.connectionTimeout:
    case DioExceptionType.receiveTimeout:
    case DioExceptionType.sendTimeout:
    case DioExceptionType.connectionError:
      return 'Network problem. Check your connection and try again.';
    default:
      break;
  }

  final data = error.response?.data;
  if (data is Map) {
    if (data['error'] == 'not_licensed') {
      return 'This service will be available once SokoPay receives the required '
          'Bank of Ghana licence.';
    }
    final message = data['error'] ?? data['detail'];
    if (message is String && message.isNotEmpty) return message;
    for (final value in data.values) {
      if (value is List && value.isNotEmpty) return value.first.toString();
      if (value is String && value.isNotEmpty) return value;
    }
  }
  return fallback;
}
