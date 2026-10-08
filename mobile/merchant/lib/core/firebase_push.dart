import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

/// Firebase Cloud Messaging glue for the merchant app ("payment received",
/// "settlement paid"). Without Firebase config the app runs normally, minus push.
class FirebasePush {
  static bool _ready = false;

  static Future<void> init() async {
    try {
      await Firebase.initializeApp();
      await FirebaseMessaging.instance.requestPermission();
      _ready = true;
    } catch (e) {
      debugPrint('Firebase not configured; push disabled: $e');
    }
  }

  static Future<void> registerWith(ApiClient api) async {
    if (!_ready) return;
    final push = PushService(api, app: 'merchant');
    await push.register(FirebaseMessaging.instance.getToken);
    FirebaseMessaging.instance.onTokenRefresh.listen((t) => push.register(() async => t));
  }

  /// Wire notification taps and foreground messages to [links]. Covers all three cases:
  ///   * app closed, launched by a tap   → getInitialMessage
  ///   * app in background, tap          → onMessageOpenedApp
  ///   * app open (no system banner)     → onMessage → in-app banner with "View"
  static Future<void> listen(DeepLinks links) async {
    if (!_ready) return;
    final initial = await FirebaseMessaging.instance.getInitialMessage();
    if (initial != null) links.open(initial.data);
    FirebaseMessaging.onMessageOpenedApp.listen((m) => links.open(m.data));
    FirebaseMessaging.onMessage.listen(
        (m) => links.showForeground(m.notification?.title, m.notification?.body, m.data));
  }
}
