import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../auth/auth_controller.dart';
import 'api_client.dart';

/// Push-notification deep links, shared by the SokoPay apps.
///
/// Each app supplies a [PushRouteResolver] that turns a notification's `data` into one
/// of ITS OWN routes (see customer/lib/core/deep_links.dart and
/// merchant/lib/core/deep_links.dart). The payload is outside input, so a resolver
/// must only build routes from a fixed list after validating any ids — never navigate
/// to a path taken from the payload. The server still checks ownership.

typedef PushRouteResolver = String? Function(Map<String, dynamic> data);

/// A public payment/transfer reference as SokoPay issues it: "SP-" + 10 Crockford
/// base32 characters (no I, L, O, U).
final spReference = RegExp(r'^SP-[0-9A-HJKMNP-TV-Z]{10}$');
final _uuid = RegExp(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');

/// Opens deep links from notification taps, waiting for sign-in when needed (a tap
/// that launched the app before the session was restored, or after it expired).
class DeepLinks {
  DeepLinks({
    required this.router,
    required this.auth,
    required this.api,
    required this.messengerKey,
    required this.resolve,
    this.fallbackRoute,
  }) {
    auth.addListener(_onAuthChanged);
  }

  final GoRouter router;
  final AuthController auth;
  final ApiClient api;
  final GlobalKey<ScaffoldMessengerState> messengerKey;
  final PushRouteResolver resolve;

  /// Where a tapped notification goes when [resolve] has no specific screen for it
  /// (e.g. the inbox, where the message is kept). Null = stay where you are.
  final String? fallbackRoute;
  String? _pending;

  /// The user tapped a notification (app in background or launched by it).
  void open(Map<String, dynamic> data) {
    _markRead(data['id']);
    final route = resolve(data) ?? fallbackRoute;
    if (route == null) return;
    if (auth.signedIn) {
      _go(route);
    } else {
      _pending = route; // opened once the user is signed in
    }
  }

  /// A notification arrived while the app is open: phones don't show those as system
  /// notifications, so show an in-app banner with a "View" button instead.
  void showForeground(String? title, String? body, Map<String, dynamic> data) {
    final route = resolve(data);
    final text = [if (title != null && title.isNotEmpty) title, if (body != null && body.isNotEmpty) body]
        .join(' — ');
    if (text.isEmpty) return;
    messengerKey.currentState
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(text),
        duration: const Duration(seconds: 6),
        action: route == null ? null : SnackBarAction(label: 'View', onPressed: () => open(data)),
      ));
  }

  void _onAuthChanged() {
    if (auth.signedIn && _pending != null) {
      final route = _pending!;
      _pending = null;
      _go(route);
    }
  }

  void _go(String route) {
    // After the current frame, so the router has finished any auth redirect. Land on
    // home first so "back" from the linked screen goes somewhere sensible.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      router.go('/home');
      if (route != '/home') router.push(route); // never stack home on home
    });
    WidgetsBinding.instance.scheduleFrame();
  }

  Future<void> _post(String path) async {
    try {
      await api.post(path);
    } catch (_) {/* best effort: never block navigation on it */}
  }

  void _markRead(Object? id) {
    final s = id?.toString() ?? '';
    if (!_uuid.hasMatch(s)) return;
    _post('/notifications/$s/read'); // best effort
  }
}
