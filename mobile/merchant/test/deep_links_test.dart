import 'package:flutter_test/flutter_test.dart';
import 'package:sokopay_merchant/core/deep_links.dart';

/// Run with `flutter test` in mobile/merchant. Keep in step with the backend contract
/// in apps/merchants/notifications.py (and its test_merchant_notifications.py).
void main() {
  group('routeForPush', () {
    test('payment notification opens that payment', () {
      expect(routeForPush({'type': 'merchant_payment', 'reference': 'SP-7KQ2MX9D4T'}),
          '/payments/SP-7KQ2MX9D4T');
    });

    test('malformed or hostile references fall back to the list, never a raw path', () {
      for (final bad in ['', 'SP-short', 'SP-7KQ2MX9D4T/../../settings', 'sp-7kq2mx9d4t',
                         'SP-ILOU000000', '/settlements', 'SP-7KQ2MX9D4T?x=1']) {
        expect(routeForPush({'type': 'merchant_payment', 'reference': bad}), '/payments', reason: bad);
      }
    });

    test('settlement notifications open settlements', () {
      expect(routeForPush({'type': 'settlement', 'settlement': 'x'}), '/settlements');
      expect(routeForPush({'type': 'settlement_accounts'}), '/settlements');
    });

    test('unknown or missing types are ignored', () {
      expect(routeForPush({}), isNull);
      expect(routeForPush({'type': 'payment', 'reference': 'SP-7KQ2MX9D4T'}), isNull); // customer-app type
      expect(routeForPush({'type': '/home'}), isNull);
    });
  });
}
