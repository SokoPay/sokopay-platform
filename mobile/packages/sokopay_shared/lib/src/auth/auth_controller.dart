import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../core/api_client.dart';
import '../core/token_store.dart';

/// Holds auth state and drives the sign-in flow against the backend:
///   POST /auth/otp/request, /auth/otp/verify, /auth/pin/set, /auth/login, /auth/me
class AuthController extends ChangeNotifier {
  AuthController(this._api, this._tokens);

  final ApiClient _api;
  final TokenStore _tokens;

  bool loading = false;
  String? error;
  bool signedIn = false;
  Map<String, dynamic>? me;

  Future<void> bootstrap() async {
    signedIn = await _tokens.hasSession;
    if (signedIn) {
      await loadMe();
    }
    notifyListeners();
  }

  Future<bool> requestOtp(String phone) async =>
      (await _guard(() async {
        await _api.post('/auth/otp/request', data: {'phone': phone});
        return true;
      })) ?? false;

  /// Verifies the OTP. Returns 'needs_pin' (new customer: create a PIN), 'home',
  /// 'pin_login' (existing customer: the SMS code alone isn't enough — enter your PIN),
  /// or null on failure.
  Future<String?> verifyOtp(String phone, String code) => _guard(() async {
        final r = await _api.post('/auth/otp/verify', data: {'phone': phone, 'code': code});
        if (r.data['next'] == 'pin_login') return 'pin_login';
        final t = r.data['tokens'];
        await _tokens.save(access: t['access'], refresh: t['refresh']);
        me = r.data['user'];
        signedIn = true;
        return r.data['needs_pin'] == true ? 'needs_pin' : 'home';
      });

  Future<bool> setPin(String pin) async =>
      (await _guard(() async {
        final r = await _api.post('/auth/pin/set', data: {'pin': pin});
        // Changing an existing PIN signs every other device out; the server hands
        // this device a fresh token pair so it stays signed in.
        final t = r.data is Map ? r.data['tokens'] : null;
        if (t != null) {
          await _tokens.save(access: t['access'], refresh: t['refresh']);
        }
        await loadMe();
        return true;
      })) ?? false;

  Future<bool> loginWithPin(String phone, String pin) async =>
      (await _guard(() async {
        final r = await _api.post('/auth/login', data: {'phone': phone, 'pin': pin});
        final t = r.data['tokens'];
        await _tokens.save(access: t['access'], refresh: t['refresh']);
        signedIn = true;
        await loadMe();
        return true;
      })) ?? false;

  /// Change PIN (current PIN required). Other devices are signed out; this one gets
  /// fresh tokens. Returns true on success (error message in [error] otherwise).
  Future<bool> changePin(String currentPin, String newPin) async =>
      (await _guard(() async {
        final r = await _api.post('/auth/pin/change', data: {'current_pin': currentPin, 'new_pin': newPin});
        final t = r.data['tokens'];
        await _tokens.save(access: t['access'], refresh: t['refresh']);
        return true;
      })) ?? false;

  /// Forgot PIN, step 1: text a reset code (same answer whether or not the number exists).
  Future<bool> requestPinReset(String phone) async =>
      (await _guard(() async {
        await _api.post('/auth/pin/forgot', data: {'phone': phone});
        return true;
      })) ?? false;

  /// Forgot PIN, step 2: code (+ Ghana Card if verified) + new PIN → signed in.
  Future<bool> resetPin({required String phone, required String code, required String newPin,
          String ghanaCard = ''}) async =>
      (await _guard(() async {
        final r = await _api.post('/auth/pin/reset',
            data: {'phone': phone, 'code': code, 'new_pin': newPin, 'ghana_card': ghanaCard});
        final t = r.data['tokens'];
        await _tokens.save(access: t['access'], refresh: t['refresh']);
        me = Map<String, dynamic>.from(r.data['user']);
        signedIn = true;
        return true;
      })) ?? false;

  Future<Map<String, dynamic>?> loadProfile() => _guard(() async =>
      Map<String, dynamic>.from((await _api.get('/auth/profile')).data));

  Future<bool> saveProfile({String? fullName, String? email}) async =>
      (await _guard(() async {
        await _api.patch('/auth/profile', data: {
          if (fullName != null) 'full_name': fullName,
          if (email != null) 'email': email,
        });
        await loadMe();
        return true;
      })) ?? false;

  /// Close the account (PIN required). On success the session ends on this phone too.
  Future<bool> closeAccount(String pin, String reason) async {
    final ok = (await _guard(() async {
          await _api.post('/auth/close', data: {'pin': pin, 'reason': reason});
          return true;
        })) ??
        false;
    if (ok) {
      await _tokens.clear();
      signedIn = false;
      me = null;
      notifyListeners();
    }
    return ok;
  }

  Future<void> loadMe() async {
    try {
      final r = await _api.get('/auth/me');
      me = Map<String, dynamic>.from(r.data);
    } catch (_) {/* token may be stale; interceptor handles refresh */}
  }

  /// Signs out. Tells the server first so the tokens are revoked (not just
  /// forgotten on this phone); if the network is down we still clear locally.
  Future<void> signOut() async {
    try {
      final refresh = await _tokens.refresh;
      await _api.post('/auth/logout', data: {'refresh': refresh ?? ''});
    } catch (_) {/* best effort — local sign-out always proceeds */}
    await _tokens.clear();
    signedIn = false;
    me = null;
    notifyListeners();
  }

  /// Signs out of every device (e.g. after a lost phone).
  Future<void> signOutEverywhere() async {
    try {
      await _api.post('/auth/logout-all');
    } catch (_) {}
    await _tokens.clear();
    signedIn = false;
    me = null;
    notifyListeners();
  }

  /// Runs an async action with loading/error bookkeeping. Returns the action's
  /// result, or null if it threw (with `error` set to a friendly message).
  Future<T?> _guard<T>(Future<T> Function() action) async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      return await action();
    } on DioException catch (e) {
      error = e.response?.data is Map && (e.response?.data['error'] != null)
          ? e.response!.data['error'].toString()
          : 'Something went wrong. Please try again.';
      return null;
    } finally {
      loading = false;
      notifyListeners();
    }
  }
}
