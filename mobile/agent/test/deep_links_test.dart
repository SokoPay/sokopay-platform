import 'package:flutter_test/flutter_test.dart';
import 'package:sokopay_agent/core/deep_links.dart';

/// Run with `flutter test` in mobile/agent. Keep in step with
/// apps/agents/tests/test_agent_notifications.py.
void main() {
  test('float and status notifications open home', () {
    expect(routeForPush({'type': 'float'}), '/home');
    expect(routeForPush({'type': 'agent_status'}), '/home');
    expect(routeForPush({'type': 'cashout'}), '/history');
  });

  test('other apps\' and unknown types are not opened here', () {
    for (final t in ['payment', 'wallet', 'merchant_payment', 'settlement', '/cash-out', '']) {
      expect(routeForPush({'type': t}), isNull, reason: t);
    }
    expect(routeForPush({}), isNull);
  });
}
