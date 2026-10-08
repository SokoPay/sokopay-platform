import 'dart:math';

import 'package:dio/dio.dart';

import 'config.dart';
import 'token_store.dart';

/// Thin wrapper over Dio that:
///  * points at the SokoPay API,
///  * attaches the JWT access token to every request,
///  * transparently refreshes the access token on a 401 and retries once.
///
/// All feature code calls through this, so auth handling lives in one place.
class ApiClient {
  ApiClient(this._tokens) {
    _dio = Dio(BaseOptions(
      baseUrl: AppConfig.apiBaseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ));
    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: _onRequest,
      onError: _onError,
    ));
  }

  final TokenStore _tokens;
  late final Dio _dio;
  bool _refreshing = false;

  Future<void> _onRequest(
      RequestOptions options, RequestInterceptorHandler handler) async {
    // Every call carries an id the server logs and stores on audit events, so a support
    // case can be traced end to end. Retries of the same request keep the same id.
    options.headers.putIfAbsent('X-Request-ID', _newRequestId);
    // Don't attach a token to the auth endpoints that issue them.
    if (!options.path.contains('/auth/')) {
      final access = await _tokens.access;
      if (access != null) options.headers['Authorization'] = 'Bearer $access';
    }
    handler.next(options);
  }

  Future<void> _onError(DioException e, ErrorInterceptorHandler handler) async {
    final is401 = e.response?.statusCode == 401;
    final isRetry = e.requestOptions.extra['__retried'] == true;
    if (!is401 || isRetry || _refreshing) return handler.next(e);

    final refreshed = await _tryRefresh();
    if (!refreshed) return handler.next(e);

    // Retry the original request once with the new token.
    final access = await _tokens.access;
    final opts = e.requestOptions
      ..headers['Authorization'] = 'Bearer $access'
      ..extra['__retried'] = true;
    try {
      final clone = await _dio.fetch(opts);
      return handler.resolve(clone);
    } on DioException catch (err) {
      return handler.next(err);
    }
  }

  Future<bool> _tryRefresh() async {
    final refresh = await _tokens.refresh;
    if (refresh == null) return false;
    _refreshing = true;
    try {
      final r = await Dio(BaseOptions(baseUrl: AppConfig.apiBaseUrl))
          .post('/auth/refresh', data: {'refresh': refresh});
      final t = r.data['tokens'];
      await _tokens.save(access: t['access'], refresh: t['refresh']);
      return true;
    } catch (_) {
      await _tokens.clear();
      return false;
    } finally {
      _refreshing = false;
    }
  }

  Future<Response<T>> get<T>(String path, {Map<String, dynamic>? query}) =>
      _dio.get<T>(path, queryParameters: query);

  Future<Response<T>> post<T>(String path,
          {Object? data, Map<String, String>? headers}) =>
      _dio.post<T>(path, data: data, options: Options(headers: headers));

  Future<Response<T>> patch<T>(String path, {Object? data}) => _dio.patch<T>(path, data: data);

  Future<Response<T>> delete<T>(String path) => _dio.delete<T>(path);
}

final _rng = Random.secure();

/// 32 hex characters, like the server's own ids.
String _newRequestId() => List.generate(16, (_) => _rng.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
