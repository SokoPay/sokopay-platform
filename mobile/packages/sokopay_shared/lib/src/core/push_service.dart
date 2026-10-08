import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';

import 'api_client.dart';

/// Registers this phone for push notifications.
///
/// Token acquisition is injected ([getToken]) so the shared package doesn't depend on
/// Firebase directly: each app passes `FirebaseMessaging.instance.getToken`. If Firebase
/// isn't configured yet (no google-services.json / GoogleService-Info.plist), the call
/// throws, we log it, and the app carries on without push — never a crash.
class PushService {
  PushService(this._api, {required this.app});
  final ApiClient _api;
  final String app; // "customer" | "agent"

  Future<void> register(Future<String?> Function() getToken) async {
    try {
      final token = await getToken();
      if (token == null || token.length < 20) return;
      await _api.post('/devices', data: {
        'platform': Platform.isIOS ? 'ios' : 'android',
        'app': app,
        'token': token,
      });
    } catch (e) {
      debugPrint('Push registration skipped: $e');
    }
  }

  Future<void> unregister(String token) async {
    try {
      await _api.delete('/devices/$token');
    } catch (_) {/* best effort on sign-out */}
  }
}
