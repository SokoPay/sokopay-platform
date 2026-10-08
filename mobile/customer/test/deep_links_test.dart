import 'package:flutter_test/flutter_test.dart';
import 'package:sokopay_customer/core/deep_links.dart';

/// Run with `flutter test` in mobile/customer. Keep in step with the backend contract
/// in apps/notifications/tests/test_customer_deep_links.py.
void main() {
  group('routeForPush (customer)', () {
    test('payment opens its receipt', () {
      expect(routeForPush({'type': 'payment', 'reference': 'SP-7KQ2MX9D4T', 'status': 'succeeded'}),
          '/payments/SP-7KQ2MX9D4T');
    });

    test('bad payment references never become a path', () {
      for (final bad in ['', 'SP-7KQ2MX9D4T/../kyc', 'SP-ILOU000000', '/wallet/send', 'SP-7KQ2MX9D4T#x']) {
        expect(routeForPush({'type': 'payment', 'reference': bad}), isNull, reason: bad);
      }
    });

    test('wallet, transfer and marketplace open their screens', () {
      expect(routeForPush({'type': 'wallet'}), '/wallet');
      expect(routeForPush({'type': 'transfer', 'reference': 'SP-7KQ2MX9D4T'}), '/transfer');
      expect(routeForPush({'type': 'marketplace', 'reference': 'x'}), '/marketplace');
      expect(routeForPush({'type': 'cashout_request', 'request': 'x'}), '/cashout');
    });

    test('merchant-app and unknown types are not opened here', () {
      expect(routeForPush({'type': 'merchant_payment', 'reference': 'SP-7KQ2MX9D4T'}), isNull);
      expect(routeForPush({'type': 'settlement'}), isNull);
      expect(routeForPush({}), isNull);
    });
  });
}
