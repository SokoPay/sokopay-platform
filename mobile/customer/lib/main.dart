import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'core/deep_links.dart';
import 'core/firebase_push.dart';
import 'features/activity/activity_screen.dart';
import 'features/bills/biller_list_screen.dart';
import 'features/cashout/allow_cashout_screen.dart';
import 'features/cross_border/cross_border_screen.dart';
import 'features/data/data_screen.dart';
import 'features/home/home_screen.dart';
import 'features/kyc/id_document_screen.dart';
import 'features/kyc/kyc_screen.dart';
import 'features/lifestyle/lifestyle_screen.dart';
import 'features/marketplace/marketplace_screen.dart';
import 'features/merchants/merchants_screen.dart';
import 'features/more/more_screen.dart';
import 'features/offers/offers_screen.dart';
import 'features/payments/receipt_screen.dart';
import 'features/providers/providers_screen.dart';
import 'features/scan/scan_screen.dart';
import 'features/shell/main_shell.dart';
import 'features/transfer/transfer_screen.dart';
import 'features/transfers/transfers_hub_screen.dart';
import 'features/wallet/wallet_forms.dart';
import 'features/wallet/wallet_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await FirebasePush.init(); // no-op until Firebase is configured

  final tokens = TokenStore();
  final api = ApiClient(tokens);
  final auth = AuthController(api, tokens)..bootstrap();
  final lock = AppLockController(auth, tokens);
  await lock.init(); // locks on cold start if biometric unlock is on

  // Register this phone for push whenever a session starts (sign-in or app launch).
  auth.addListener(() {
    if (auth.signedIn) FirebasePush.registerWith(api, app: 'customer');
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
    fallbackRoute: '/inbox', // no specific screen? the message is in the inbox
  );

  runApp(MultiProvider(
    providers: [
      Provider<TokenStore>.value(value: tokens),
      Provider<ApiClient>.value(value: api),
      ChangeNotifierProvider<AuthController>.value(value: auth),
      ChangeNotifierProvider<AppLockController>.value(value: lock),
    ],
    child: SokoPayApp(router: router, messengerKey: messengerKey),
  ));

  // After runApp, so a notification that launched the app can navigate.
  await FirebasePush.listen(links);
}

GoRouter buildRouter(AuthController auth) => GoRouter(
      initialLocation: '/',
      refreshListenable: auth.session, // sign-in/out only (see AuthController.session)
      redirect: (context, state) {
        final signedIn = auth.signedIn;
        final loc = state.matchedLocation;
        final onAuthFlow = const {'/', '/otp', '/pin-login', '/forgot-pin'}.contains(loc);
        // Not signed in → keep to the auth flow.
        if (!signedIn && !onAuthFlow) return '/';
        // Signed in but on the landing page → go home.
        if (signedIn && loc == '/') return '/home';
        return null;
      },
      routes: [
        GoRoute(
          path: '/',
          builder: (_, __) => const PhoneScreen(tagline: 'Pay bills, airtime and shops', terms: LegalDoc.terms),
        ),
        GoRoute(
          path: '/otp',
          builder: (_, s) => OtpScreen(phone: s.extra as String? ?? ''),
        ),
        GoRoute(path: '/forgot-pin', builder: (_, s) => ForgotPinScreen(phone: s.extra as String?)),
        GoRoute(path: '/change-pin', builder: (_, __) => const ChangePinScreen()),
        GoRoute(path: '/profile', builder: (_, __) => const ProfileScreen(documentsRoute: '/id-documents')),
        GoRoute(path: '/id-documents', builder: (_, __) => const IdDocumentScreen()),
        GoRoute(path: '/close-account', builder: (_, __) => const CloseAccountScreen()),
        GoRoute(
          path: '/pin-set',
          builder: (_, __) => const PinScreen(mode: PinMode.set),
        ),
        GoRoute(
          path: '/pin-login',
          builder: (_, s) => PinScreen(mode: PinMode.login, phone: s.extra as String?),
        ),
        // Bottom navigation: Home · Transfers · [Scan QR] · Offers · More.
        StatefulShellRoute.indexedStack(
          builder: (_, __, shell) => MainShell(shell: shell),
          branches: [
            StatefulShellBranch(routes: [
              GoRoute(
                path: '/home',
                builder: (ctx, __) => HomeScreen(onTab: (i) => StatefulNavigationShell.of(ctx).goBranch(i)),
              ),
            ]),
            StatefulShellBranch(routes: [
              GoRoute(path: '/transfers', builder: (_, __) => const TransfersHubScreen()),
            ]),
            StatefulShellBranch(routes: [
              GoRoute(path: '/offers', builder: (_, __) => const OffersScreen()),
            ]),
            StatefulShellBranch(routes: [
              GoRoute(path: '/more', builder: (_, __) => const MoreScreen()),
            ]),
          ],
        ),
        GoRoute(
          path: '/bills',
          builder: (_, s) => BillerListScreen(category: s.uri.queryParameters['category']),
        ),
        GoRoute(path: '/wallet', builder: (_, __) => const WalletScreen()),
        GoRoute(path: '/wallet/fund', builder: (_, __) => const WalletFundScreen()),
        GoRoute(path: '/wallet/send', builder: (_, __) => const WalletSendScreen()),
        GoRoute(path: '/data', builder: (_, __) => const DataScreen()),
        GoRoute(path: '/transfer', builder: (_, s) => TransferScreen(initialType: s.uri.queryParameters['type'])),
        GoRoute(path: '/marketplace', builder: (_, __) => const MarketplaceScreen()),
        GoRoute(path: '/kyc', builder: (_, __) => const KycScreen()),
        GoRoute(path: '/scan', builder: (_, __) => const ScanScreen()),
        GoRoute(path: '/merchants', builder: (_, __) => const MerchantsScreen()),
        GoRoute(path: '/cross-border', builder: (_, __) => const CrossBorderScreen()),
        GoRoute(
          path: '/lifestyle/:category',
          builder: (_, s) => LifestyleScreen(category: s.pathParameters['category']!),
        ),
        GoRoute(path: '/inbox', builder: (_, __) => const InboxScreen(resolve: routeForPush)),
        GoRoute(path: '/activity', builder: (_, s) => ActivityScreen(initialFilter: s.uri.queryParameters['filter'])),
        GoRoute(path: '/statement', builder: (_, __) => const StatementScreen()),
        GoRoute(path: '/cashout', builder: (_, __) => const AllowCashOutScreen()),
        GoRoute(path: '/security', builder: (_, __) => const SecuritySettingsScreen()),
        GoRoute(path: '/help', builder: (_, __) => const HelpLegalScreen(mainTerms: LegalDoc.terms)),
        GoRoute(
          path: '/providers/:category',
          builder: (_, s) => ProvidersScreen(category: s.pathParameters['category']!),
        ),
        GoRoute(
          path: '/payments/:reference',
          builder: (_, s) => ReceiptScreen(reference: s.pathParameters['reference']!),
        ),
      ],
    );

class SokoPayApp extends StatelessWidget {
  const SokoPayApp({super.key, required this.router, required this.messengerKey});
  final GoRouter router;
  final GlobalKey<ScaffoldMessengerState> messengerKey;

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'SokoPay',
      theme: buildSokoTheme(),
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: messengerKey, // in-app banners for foreground notifications
      routerConfig: router,
      builder: (context, child) => AppLockGate(child: child ?? const SizedBox()),
    );
  }
}
