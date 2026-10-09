import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_customer/core/deep_links.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

/// The Profile screen used to crash on open: its load notified AuthController while the
/// page was being built, and the router (listening to the controller) re-ran mid-build.
class _FakeAuth extends AuthController {
  _FakeAuth() : super(ApiClient(TokenStore()), TokenStore());

  @override
  Future<Map<String, dynamic>> fetchProfile() async {
    notifyListeners(); // what used to break the page
    return {
      'phone': '+233244000201',
      'full_name': 'Kofi Asante',
      'email': '',
      'address': '12 Liberation Rd, Osu',
      'gps_address': 'GA-183-8164',
      'name_locked': true,
      'verification': {
        'ghana_card_verified': true,
        'ghana_card_hint': 'GHA-•••••789-0',
        'documents': [
          {'type': 'passport', 'type_label': 'Passport', 'number_hint': '••••4567', 'status': 'pending',
           'status_label': 'Waiting for review', 'expiry_date': '2030-01-01', 'note': ''},
        ],
      },
    };
  }
}

void main() {
  testWidgets('Profile opens from a router without crashing and shows every field', (tester) async {
    final auth = _FakeAuth()..signedIn = true;
    final router = GoRouter(
      initialLocation: '/home',
      refreshListenable: auth.session,
      routes: [
        GoRoute(path: '/home', builder: (c, __) => Scaffold(
            body: Center(child: TextButton(onPressed: () => c.push('/profile'), child: const Text('Profile'))))),
        GoRoute(path: '/profile', builder: (_, __) => const ProfileScreen(documentsRoute: '/id-documents')),
        GoRoute(path: '/id-documents', builder: (_, __) => const Scaffold(body: Text('ID documents'))),
      ],
    );
    await tester.pumpWidget(ChangeNotifierProvider<AuthController>.value(
        value: auth, child: MaterialApp.router(routerConfig: router)));
    await tester.tap(find.text('Profile'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('Personal details'), findsOneWidget);
    expect(find.text('Phone number'), findsOneWidget);
    expect(find.text('+233244000201'), findsWidgets);
    expect(find.text('Ghana Post GPS address'), findsOneWidget);
    expect(find.text('GA-183-8164'), findsWidgets); // the value (the field's hint has the same text)
    expect(find.text('Ghana Card verified'), findsOneWidget);
    await tester.scrollUntilVisible(find.text("Add passport or driver's licence"), 200,
        scrollable: find.descendant(of: find.byType(ListView), matching: find.byType(Scrollable)).first);
    expect(find.textContaining('Waiting for review'), findsOneWidget);
  });

  test('Ghana Post GPS format check', () {
    for (final ok in ['', 'GA-183-8164', 'ga1838164', 'AK-0123-4567', 'GA 183 8164']) {
      expect(gpsProblem(ok), isNull, reason: ok);
    }
    for (final bad in ['12345', 'GA-1-2', 'GAX-183-8164', 'Accra']) {
      expect(gpsProblem(bad), isNotNull, reason: bad);
    }
  });

  test('a document review notification opens the profile', () {
    expect(routeForPush({'type': 'profile'}), '/profile');
  });
}
