import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Stores the JWT access and refresh tokens in the platform's encrypted storage
/// (Keychain on iOS, EncryptedSharedPreferences on Android). Tokens never touch
/// plain shared-preferences or disk in the clear.
class TokenStore {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  static const _kAccess = 'sp_access';
  static const _kRefresh = 'sp_refresh';

  Future<void> save({required String access, required String refresh}) async {
    await _storage.write(key: _kAccess, value: access);
    await _storage.write(key: _kRefresh, value: refresh);
  }

  Future<String?> get access => _storage.read(key: _kAccess);
  Future<String?> get refresh => _storage.read(key: _kRefresh);

  Future<void> clear() async {
    await _storage.delete(key: _kAccess);
    await _storage.delete(key: _kRefresh);
  }

  Future<bool> get hasSession async => (await access) != null;
}
