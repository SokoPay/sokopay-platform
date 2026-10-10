import 'dart:math';

import 'package:dio/dio.dart';

/// One idempotency key per payment attempt.
///
/// If the network drops before the server answers, the customer may tap Pay again: the
/// same key is sent, so the server moves the money at most once. Once the server has
/// answered (success, or an error such as a wrong PIN or low balance), the attempt is
/// settled and the next tap is a new payment with a new key.
class PaymentAttempt {
  PaymentAttempt(this.prefix);

  final String prefix;
  String? _key;
  static final _random = Random.secure();

  String get key => _key ??= '$prefix-${DateTime.now().microsecondsSinceEpoch}-${_random.nextInt(1 << 32)}';

  /// Call when the request finished. Keeps the key only if the outcome is unknown
  /// (no response from the server), so a retry can't pay twice.
  void settle([Object? error]) {
    if (error is DioException && error.response == null) return;
    _key = null;
  }
}
