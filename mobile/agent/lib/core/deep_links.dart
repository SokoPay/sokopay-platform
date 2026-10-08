/// Where an Agent-app notification goes (push taps and inbox taps). The handling
/// (sign-in wait, banners, mark-read) is shared: sokopay_shared DeepLinks.
///
/// Backend contract (apps/agents/services.py, test_agent_notifications.py):
///   {"type": "float"}        → home (float balance + top-up context)
///   {"type": "agent_status"} → home
///   {"type": "cashout"}      → transactions (a cash-out was approved / declined)
/// Unknown types → null (a push tap then opens the inbox).
String? routeForPush(Map<String, dynamic> data) {
  switch (data['type']) {
    case 'float':
    case 'agent_status':
      return '/home';
    case 'cashout':
      return '/history';
    default:
      return null;
  }
}
