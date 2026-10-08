import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'core/deep_links.dart';
import 'core/firebase_push.dart';
import 'features/business/home_screen.dart';
import 'features/payments/disputes_screen.dart';
import 'features/payments/payment_detail_screen.dart';
import 'features/payments/payments_screen.dart';
import 'features/qr/request_payment_screen.dart';
import 'features/qr/request_qr_screen.dart';
import 'features/qr/static_qr_screen.dart';
import 'features/settlements/add_account_screen.dart';
import 'features/settlements/settlements_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await FirebasePush.init(); // no-op until Firebase is configured

  final tokens = TokenStore();
  final api = ApiClient(tokens);
  final auth = AuthController(api, tokens)..bootstrap();
  final lock = AppLockController(auth, tokens);
  await lock.init(); // locks on cold start if biometric unlock is on
  auth.addListener(() {
    if (auth.signedIn) FirebasePush.registerWith(api);
  });

  // Created once (not in build) so deep links can navigate from outside the widget tree.
  final router = buildRouter(auth);
  final messengerKey = GlobalKey<ScaffoldMessengerState>();
  final links = DeepLinks(
      router: router, auth: auth, api: api, messengerKey: messengerKey, resolve: routeForPush);

  runApp(MultiProvider(
    providers: [
      Provider<TokenStore>.value(value: tokens),
      Provider<ApiClient>.value(value: api),
      ChangeNotifierProvider<AuthController>.value(value: auth),
      ChangeNotifierProvider<AppLockController>.value(value: lock),
    ],
    child: SokoPayBusinessApp(router: router, messengerKey: messengerKey),
  ));

  // After runApp, so a notification that launched the app can navigate.
  await FirebasePush.listen(links);
}

GoRouter buildRouter(AuthController auth) => GoRouter(
      initialLocation: '/',
      refreshListenable: auth,
      redirect: (context, state) {
        final loc = state.matchedLocation;
        final onAuthFlow = const {'/', '/otp', '/pin-login', '/forgot-pin'}.contains(loc);
        if (!auth.signedIn && !onAuthFlow) return '/';
        if (auth.signedIn && loc == '/') return '/home';
        return null;
      },
      routes: [
        GoRoute(
          path: '/',
          builder: (_, __) => const PhoneScreen(tagline: 'Business · take payments, get paid', terms: LegalDoc.merchantTerms),
        ),
        GoRoute(path: '/otp', builder: (_, s) => OtpScreen(phone: s.extra as String? ?? '')),
        GoRoute(path: '/forgot-pin', builder: (_, s) => ForgotPinScreen(phone: s.extra as String?)),
        GoRoute(path: '/change-pin', builder: (_, __) => const ChangePinScreen()),
        GoRoute(path: '/profile', builder: (_, __) => const ProfileScreen()),
        GoRoute(path: '/close-account', builder: (_, __) => const CloseAccountScreen()),
        GoRoute(
          path: '/pin-login',
          builder: (_, s) => PinScreen(mode: PinMode.login, phone: s.extra as String?),
        ),
        GoRoute(path: '/pin-set', builder: (_, __) => const PinScreen(mode: PinMode.set)),
        GoRoute(path: '/security', builder: (_, __) => const SecuritySettingsScreen()),
        GoRoute(path: '/help', builder: (_, __) => const HelpLegalScreen(mainTerms: LegalDoc.merchantTerms)),
        GoRoute(path: '/home', builder: (_, __) => const BusinessHomeScreen()),
        GoRoute(path: '/qr', builder: (_, __) => const StaticQrScreen()),
        GoRoute(path: '/request', builder: (_, __) => const RequestPaymentScreen()),
        GoRoute(
          path: '/request/:token',
          builder: (_, s) => RequestQrScreen(token: s.pathParameters['token']!),
        ),
        GoRoute(path: '/payments', builder: (_, __) => const PaymentsScreen()),
        GoRoute(path: '/disputes', builder: (_, __) => const DisputesScreen()),
        GoRoute(path: '/statement', builder: (_, __) => const StatementScreen(scope: 'merchant')),
        GoRoute(
          path: '/payments/:reference',
          builder: (_, s) => PaymentDetailScreen(reference: s.pathParameters['reference']!),
        ),
        GoRoute(path: '/settlements', builder: (_, __) => const SettlementsScreen()),
        GoRoute(
          path: '/settlements/add-account',
          builder: (_, s) => AddAccountScreen(bankAvailable: s.extra as bool? ?? false),
        ),
      ],
    );

class SokoPayBusinessApp extends StatelessWidget {
  const SokoPayBusinessApp({super.key, required this.router, required this.messengerKey});
  final GoRouter router;
  final GlobalKey<ScaffoldMessengerState> messengerKey;

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'SokoPay Business',
      theme: buildSokoTheme(),
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: messengerKey,   // in-app banners for foreground notifications
      routerConfig: router,
      builder: (context, child) => AppLockGate(child: child ?? const SizedBox()),
    );
  }
}
