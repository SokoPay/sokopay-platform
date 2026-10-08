import 'package:sokopay_shared/sokopay_shared.dart';

/// Where a customer-app notification goes — used for push taps and inbox taps alike.
/// The handling (sign-in wait, banners, mark-read) is shared: sokopay_shared DeepLinks.
///
/// Backend contract (apps/notifications/tests/test_customer_deep_links.py):
///   {"type": "payment",  "reference": "SP-…"} → /payments/<ref>  (receipt)
///   {"type": "wallet"}                        → /wallet
///   {"type": "transfer", "reference": "SP-…"} → /transfer
///   {"type": "marketplace"}                   → /marketplace
///   {"type": "cashout_request", "request": …} → /cashout  (approve an agent's cash-out)
/// Unknown types → null (a push tap then opens the inbox, where the message is kept).
/// References are validated; a path from the payload is never used.
String? routeForPush(Map<String, dynamic> data) {
  switch (data['type']) {
    case 'payment':
      final ref = (data['reference'] ?? '').toString();
      return spReference.hasMatch(ref) ? '/payments/$ref' : null;
    case 'wallet':
      return '/wallet';
    case 'transfer':
      return '/transfer';
    case 'marketplace':
      return '/marketplace';
    case 'cashout_request':
      return '/cashout';
    default:
      return null;
  }
}
