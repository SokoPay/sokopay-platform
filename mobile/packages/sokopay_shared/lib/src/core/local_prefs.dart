import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Small on-device preferences (e.g. "hide my balance"). Kept in the same encrypted
/// storage as the tokens — nothing here is secret, but it avoids a second storage
/// dependency and keeps all app state in one place.
class LocalPrefs {
  static const _storage = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));

  static Future<bool> getBool(String key, {bool fallback = false}) async {
    final v = await _storage.read(key: 'pref_$key');
    return v == null ? fallback : v == '1';
  }

  static Future<void> setBool(String key, bool value) =>
      _storage.write(key: 'pref_$key', value: value ? '1' : '0');
}
