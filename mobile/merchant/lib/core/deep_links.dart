import 'package:sokopay_shared/sokopay_shared.dart';

/// Where a Business-app notification goes. The handling (sign-in wait, banners,
/// mark-read) is shared — see sokopay_shared DeepLinks; this is only the whitelist.
///
/// The backend's contract (apps/merchants/notifications.py) — all values strings:
///   {"type": "merchant_payment", "reference": "SP-XXXXXXXXXX"} → /payments/<ref>
///   {"type": "settlement", "settlement": "<uuid>"}             → /settlements
///   {"type": "settlement_accounts"}                             → /settlements
/// Anything else is ignored. References are validated; a payload path is never used.
String? routeForPush(Map<String, dynamic> data) {
  switch (data['type']) {
    case 'merchant_payment':
      final ref = (data['reference'] ?? '').toString();
      return spReference.hasMatch(ref) ? '/payments/$ref' : '/payments';
    case 'settlement':
    case 'settlement_accounts':
      return '/settlements';
    default:
      return null;
  }
}
