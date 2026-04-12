import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../storage/local_storage.dart';

/// Singleton Dio HTTP client with session headers and error interceptor.
final apiClientProvider = Provider<Dio>((ref) {
  final dio = Dio(
    BaseOptions(
      baseUrl: ApiConfig.baseUrl,
      connectTimeout: ApiConfig.connectTimeout,
      receiveTimeout: ApiConfig.receiveTimeout,
      headers: {'Content-Type': 'application/json'},
    ),
  );

  dio.interceptors.add(_SessionInterceptor());
  dio.interceptors.add(_ErrorInterceptor());

  return dio;
});

/// Injects X-Session-ID and X-Request-ID on every request.
class _SessionInterceptor extends Interceptor {
  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    options.headers['X-Session-ID'] = LocalStorage.sessionId;
    // Let the server generate X-Request-ID if we don't have one
    handler.next(options);
  }
}

/// Maps Dio errors to a human-readable [ApiException].
class _ErrorInterceptor extends Interceptor {
  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    final message = switch (err.type) {
      DioExceptionType.connectionTimeout ||
      DioExceptionType.sendTimeout ||
      DioExceptionType.receiveTimeout =>
        'Connection timed out. Check your network.',
      DioExceptionType.connectionError =>
        'Cannot reach the server. Make sure you are online.',
      DioExceptionType.badResponse => _parseServerError(err.response),
      _ => 'Something went wrong. Please try again.',
    };
    handler.next(
      DioException(
        requestOptions: err.requestOptions,
        response: err.response,
        type: err.type,
        error: ApiException(message, err.response?.statusCode),
      ),
    );
  }

  String _parseServerError(Response<dynamic>? res) {
    if (res == null) return 'Server error';
    final data = res.data;
    if (data is Map<String, dynamic>) {
      final detail = data['detail'];
      if (detail != null) return detail.toString();
      final error = data['error'];
      if (error is Map<String, dynamic>) {
        final message = error['message'];
        if (message != null) return message.toString();
      }
      return 'Server error (${res.statusCode})';
    }
    return 'Server error (${res.statusCode})';
  }
}

class ApiException implements Exception {
  final String message;
  final int? statusCode;
  const ApiException(this.message, [this.statusCode]);

  @override
  String toString() => message;
}
