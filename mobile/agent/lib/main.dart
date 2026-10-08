import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'core/deep_links.dart';
import 'core/firebase_push.dart';
import 'features/agent/cash_op_screen.dart';
import 'features/agent/history_screen.dart';
import 'features/agent/home_screen.dart';
import 'features/agent/txn_detail_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await FirebasePush.init();

  final tokens = TokenStore();
  final api = ApiClient(tokens);
  final auth = AuthController(api, tokens)..bootstrap();
  final lock = AppLockController(auth, tokens);
  await lock.init(); // locks on cold start if biometric unlock is on
  auth.addListener(() {
    if (auth.signedIn) FirebasePush.registerWith(api);
  });

  // Created once (not in build) so notification taps can navigate from outside the tree.
  final router = buildRouter(auth);
  final messengerKey = GlobalKey<ScaffoldMessengerState>();
  final links = DeepLinks(
    router: router,
    auth: auth,
    api: api,
    messengerKey: messengerKey,
    resolve: routeForPush,
    fallbackRoute: '/inbox',
  );

  runApp(MultiProvider(
    providers: [
      Provider<TokenStore>.value(value: tokens),
      Provider<ApiClient>.value(value: api),
      ChangeNotifierProvider<AuthController>.value(value: auth),
      ChangeNotifierProvider<AppLockController>.value(value: lock),
    ],
    child: SokoPayAgentApp(router: router, messengerKey: messengerKey),
  ));

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
          builder: (_, __) => const PhoneScreen(tagline: 'Agent · cash in and out', terms: LegalDoc.agentTerms),
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
        GoRoute(path: '/help', builder: (_, __) => const HelpLegalScreen(mainTerms: LegalDoc.agentTerms)),
        GoRoute(path: '/home', builder: (_, __) => const AgentHomeScreen()),
        GoRoute(path: '/cash-in', builder: (_, __) => const CashOpScreen(isCashIn: true)),
        GoRoute(path: '/cash-out', builder: (_, __) => const CashOpScreen(isCashIn: false)),
        GoRoute(path: '/inbox', builder: (_, __) => const InboxScreen(resolve: routeForPush)),
        GoRoute(path: '/history', builder: (_, __) => const HistoryScreen()),
        GoRoute(path: '/history/:id', builder: (_, s) => TxnDetailScreen(id: s.pathParameters['id']!)),
      ],
    );

class SokoPayAgentApp extends StatelessWidget {
  const SokoPayAgentApp({super.key, required this.router, required this.messengerKey});
  final GoRouter router;
  final GlobalKey<ScaffoldMessengerState> messengerKey;

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'SokoPay Agent',
      theme: buildSokoTheme(),
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: messengerKey,
      routerConfig: router,
      builder: (context, child) => AppLockGate(child: child ?? const SizedBox()),
    );
  }
}
