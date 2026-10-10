import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

void main() {
  test('a dropped connection keeps the key, so a retry pays once', () {
    final a = PaymentAttempt('p2p');
    final first = a.key;
    a.settle(DioException(requestOptions: RequestOptions(path: '/wallet/send'), type: DioExceptionType.connectionTimeout));
    expect(a.key, first);
  });

  test('an answer from the server starts a new attempt next time', () {
    final a = PaymentAttempt('p2p');
    final first = a.key;
    final req = RequestOptions(path: '/wallet/send');
    a.settle(DioException(requestOptions: req, response: Response(requestOptions: req, statusCode: 403)));
    expect(a.key, isNot(first));
    final second = a.key;
    a.settle();
    expect(a.key, isNot(second));
    expect(a.key.startsWith('p2p-'), isTrue);
  });
}
