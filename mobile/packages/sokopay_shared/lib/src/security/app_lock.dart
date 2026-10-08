import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';
import 'package:provider/provider.dart';

import '../auth/auth_controller.dart';
import '../core/brand.dart';
import '../core/theme.dart';
import '../core/token_store.dart';

/// Biometric app lock (fingerprint / Face ID), opt-in.
///
/// When on, the app locks:
///   * on a cold start that restores a saved session, and
///   * when it comes back after [timeout] in the background.
/// Unlock with biometrics, or with the PIN (checked by the SERVER via /auth/login, so it
/// shares the sign-in lockout — 5 wrong PINs locks the account for 5 minutes).
///
/// Scope (product decision): biometrics only unlock the app. Money actions still ask for
/// the PIN, which the server verifies. Nothing secret is gated on the biometric itself:
/// the session tokens stay in encrypted storage either way.
///
/// Also: an app-switcher privacy cover ([obscured]) for every user, lock on or off.
///
/// Platform setup (one-off, in each app's android/ios folders after `flutter create`):
///   Android: MainActivity extends FlutterFragmentActivity; USE_BIOMETRIC permission.
///            Optional, stronger privacy: in MainActivity.onCreate add
///              window.setFlags(WindowManager.LayoutParams.FLAG_SECURE,
///                              WindowManager.LayoutParams.FLAG_SECURE)
///            — guarantees a blank recent-apps preview on every Android phone, but
///            ALSO blocks screenshots and screen recording of the app (product decision).
///   iOS:     NSFaceIDUsageDescription in Info.plist. (The cover alone is reliable on
///            iOS: the snapshot is taken after the app goes inactive.)
class AppLockController extends ChangeNotifier with WidgetsBindingObserver {
  AppLockController(this._auth, this._tokens,
      {this.timeout = const Duration(minutes: 2), LocalAuthentication? localAuth})
      : _local = localAuth ?? LocalAuthentication();

  final AuthController _auth;
  final TokenStore _tokens;
  final LocalAuthentication _local;
  final Duration timeout;

  static const _storage = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));
  static const _kEnabled = 'sp_biometric_unlock';
  static const _kPhone = 'sp_last_phone';

  bool enabled = false;
  bool locked = false;

  /// True while the app is not in the foreground: AppLockGate then covers the content
  /// so the phone's app-switcher preview never shows balances or account details.
  bool obscured = false;
  DateTime? _backgroundedAt;

  Future<void> init() async {
    enabled = (await _storage.read(key: _kEnabled)) == '1';
    // Lock only when a saved session is being restored — not right after a fresh sign-in.
    if (enabled && await _tokens.hasSession) locked = true;
    WidgetsBinding.instance.addObserver(this);
    _auth.addListener(_onAuthChanged);
    notifyListeners();
  }

  void _onAuthChanged() {
    final phone = _auth.me?['phone'];
    if (phone is String && phone.isNotEmpty) _storage.write(key: _kPhone, value: phone);
    if (!_auth.signedIn && locked) {
      locked = false; // signed out: nothing to protect, show the sign-in flow
      notifyListeners();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Privacy cover: the moment the app stops being in front (app switcher opened,
    // home pressed, call coming in) hide its content, so the recent-apps preview the
    // phone captures shows the SokoPay cover, not balances. iOS takes that snapshot
    // after `inactive`, so cover there already. Applies to everyone, lock on or off.
    final cover = state != AppLifecycleState.resumed;
    if (cover != obscured) {
      obscured = cover;
      notifyListeners();
    }
    if (state == AppLifecycleState.paused || state == AppLifecycleState.hidden) {
      _backgroundedAt ??= DateTime.now();
    } else if (state == AppLifecycleState.resumed) {
      final since = _backgroundedAt;
      _backgroundedAt = null;
      if (enabled && _auth.signedIn && since != null && DateTime.now().difference(since) >= timeout) {
        locked = true;
        notifyListeners();
      }
    }
  }

  /// Does this phone have a fingerprint / face enrolled?
  Future<bool> biometricsAvailable() async {
    try {
      return await _local.isDeviceSupported() &&
          await _local.canCheckBiometrics &&
          (await _local.getAvailableBiometrics()).isNotEmpty;
    } on PlatformException {
      return false;
    }
  }

  Future<bool> _authenticate(String reason) async {
    try {
      return await _local.authenticate(
        localizedReason: reason,
        options: const AuthenticationOptions(biometricOnly: true, stickyAuth: true),
      );
    } on PlatformException {
      return false;
    }
  }

  /// Turn biometric unlock on (requires a successful scan first) or off.
  /// Returns an error message, or null on success.
  Future<String?> setEnabled(bool value) async {
    if (value) {
      if (!await biometricsAvailable()) {
        return 'Set up a fingerprint or face unlock in your phone settings first.';
      }
      if (!await _authenticate('Confirm to turn on biometric unlock')) {
        return 'Biometric check was cancelled.';
      }
      await _storage.write(key: _kEnabled, value: '1');
    } else {
      await _storage.delete(key: _kEnabled);
    }
    enabled = value;
    notifyListeners();
    return null;
  }

  Future<bool> unlockWithBiometrics() async {
    final ok = await _authenticate('Unlock SokoPay');
    if (ok) {
      locked = false;
      notifyListeners();
    }
    return ok;
  }

  /// PIN fallback, verified by the server (fresh tokens on success). Returns an error or null.
  Future<String?> unlockWithPin(String pin) async {
    final phone = (_auth.me?['phone'] as String?) ?? await _storage.read(key: _kPhone);
    if (phone == null) return 'Please sign in again.';
    final ok = await _auth.loginWithPin(phone, pin);
    if (!ok) return _auth.error ?? 'Wrong PIN.';
    locked = false;
    notifyListeners();
    return null;
  }

  Future<void> signOutFromLock() async {
    await _auth.signOut();
    locked = false;
    notifyListeners();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _auth.removeListener(_onAuthChanged);
    super.dispose();
  }
}

/// Wrap the app (MaterialApp.router `builder:`) so the lock screen covers everything.
class AppLockGate extends StatelessWidget {
  const AppLockGate({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final lock = context.watch<AppLockController>();
    return Stack(children: [
      child,
      // Own Overlay: the gate sits above the app's Navigator, and the PIN field needs one.
      if (lock.locked)
        Positioned.fill(
          child: Overlay(initialEntries: [OverlayEntry(builder: (_) => const _LockScreen())]),
        ),
      // App-switcher privacy cover — above everything, including the lock screen.
      if (lock.obscured) const Positioned.fill(child: _PrivacyCover()),
    ]);
  }
}

/// What the recent-apps preview shows: brand colour and logo, no account data.
class _PrivacyCover extends StatelessWidget {
  const _PrivacyCover();

  @override
  Widget build(BuildContext context) {
    return const ColoredBox(
      color: SokoColors.ink,
      child: Center(child: SokoLogo(height: 40, onDark: true)),
    );
  }
}

class _LockScreen extends StatefulWidget {
  const _LockScreen();
  @override
  State<_LockScreen> createState() => _LockScreenState();
}

class _LockScreenState extends State<_LockScreen> {
  final _pin = TextEditingController();
  bool _usePin = false;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    // Offer the fingerprint prompt straight away.
    WidgetsBinding.instance.addPostFrameCallback((_) => _biometric());
  }

  @override
  void dispose() {
    _pin.dispose();
    super.dispose();
  }

  Future<void> _biometric() async {
    final ok = await context.read<AppLockController>().unlockWithBiometrics();
    if (!ok && mounted) setState(() => _usePin = true);
  }

  Future<void> _submitPin(String pin) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final err = await context.read<AppLockController>().unlockWithPin(pin);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = err;
      if (err != null) _pin.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Material(
      color: SokoColors.ink,
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(children: [
            const Spacer(),
            const SokoLogo(height: 40, onDark: true),
            const SizedBox(height: 16),
            const Text('Locked', style: TextStyle(color: Colors.white70, fontSize: 16)),
            const Spacer(),
            if (!_usePin) ...[
              IconButton(
                iconSize: 72,
                onPressed: _biometric,
                icon: const Icon(Icons.fingerprint, color: SokoColors.orange),
                tooltip: 'Unlock with biometrics',
              ),
              const Text('Touch to unlock', style: TextStyle(color: Colors.white)),
              const SizedBox(height: 24),
              TextButton(
                onPressed: () => setState(() => _usePin = true),
                child: const Text('Use PIN instead', style: TextStyle(color: Colors.white70)),
              ),
            ] else ...[
              const Text('Enter your PIN', style: TextStyle(color: Colors.white, fontSize: 18)),
              const SizedBox(height: 16),
              SizedBox(
                width: 220,
                child: TextField(
                  controller: _pin,
                  autofocus: true,
                  enabled: !_busy,
                  obscureText: true,
                  maxLength: 6,
                  keyboardType: TextInputType.number,
                  textAlign: TextAlign.center,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  style: const TextStyle(fontSize: 24, letterSpacing: 12),
                  decoration: const InputDecoration(counterText: ''),
                  onChanged: (v) {
                    if (v.length == 6) _submitPin(v);
                  },
                ),
              ),
              if (_busy) const Padding(padding: EdgeInsets.all(12), child: CircularProgressIndicator()),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.orangeAccent)),
                ),
              TextButton(
                onPressed: _biometric,
                child: const Text('Use fingerprint / face instead', style: TextStyle(color: Colors.white70)),
              ),
            ],
            const Spacer(),
            TextButton(
              onPressed: () => context.read<AppLockController>().signOutFromLock(),
              child: const Text('Not you? Sign out', style: TextStyle(color: Colors.white54)),
            ),
          ]),
        ),
      ),
    );
  }
}

/// A settings row to turn biometric unlock on/off (used on each app's More/Security page).
class BiometricUnlockTile extends StatelessWidget {
  const BiometricUnlockTile({super.key});

  @override
  Widget build(BuildContext context) {
    final lock = context.watch<AppLockController>();
    return SwitchListTile(
      secondary: const Icon(Icons.fingerprint),
      title: const Text('Unlock with fingerprint / face'),
      subtitle: const Text('Locks after 2 minutes away. Payments still need your PIN.'),
      value: lock.enabled,
      onChanged: (v) async {
        final err = await lock.setEnabled(v);
        if (err != null && context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(err)));
        }
      },
    );
  }
}
