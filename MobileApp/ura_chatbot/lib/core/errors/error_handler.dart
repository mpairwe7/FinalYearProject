import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

/// Install global error handlers.
///
/// Must be called inside ``runZonedGuarded`` from ``main()`` so every
/// Dart unhandled exception, Flutter framework error, and ``ErrorWidget``
/// rebuild goes through one funnel.
///
/// 2026 production checklist:
///   * ``FlutterError.onError`` for framework build / layout errors
///   * ``PlatformDispatcher.instance.onError`` for unhandled async errors
///   * ``ErrorWidget.builder`` for *user-facing* crash screens in release
///   * A single ``reportError`` entry point that third-party crash
///     reporters (Sentry, Crashlytics) can replace in one line.
class AppErrorHandler {
  AppErrorHandler._();

  /// Optional crash-reporter hook. Swap this in from ``main()`` with
  /// e.g. ``Sentry.captureException`` or ``FirebaseCrashlytics.instance.recordError``.
  static Future<void> Function(Object error, StackTrace? stack)? reporter;

  static void install() {
    // Framework errors (build, layout, paint).
    FlutterError.onError = (FlutterErrorDetails details) {
      FlutterError.presentError(details);
      _report(details.exception, details.stack);
    };

    // Unhandled async errors that escape the Flutter framework.
    PlatformDispatcher.instance.onError = (error, stack) {
      _report(error, stack);
      return true;
    };

    // Release-mode error widget: swap the default red error box for a
    // friendly "Something went wrong" screen that doesn't reveal a
    // stack trace to end users. In debug builds, keep the original so
    // developers can see the problem.
    if (kReleaseMode) {
      ErrorWidget.builder = (FlutterErrorDetails details) {
        return const _ReleaseErrorBox();
      };
    }
  }

  /// Funnel an error through the reporter. Also logs via `dart:developer`
  /// so it shows up in `flutter logs` / Logcat / Console.
  static Future<void> report(Object error, [StackTrace? stack]) async {
    await _report(error, stack);
  }

  static Future<void> _report(Object error, StackTrace? stack) async {
    developer.log(
      'URAChatbot error',
      error: error,
      stackTrace: stack,
      name: 'ura_chatbot',
    );
    if (reporter != null) {
      try {
        await reporter!(error, stack);
      } catch (_) {
        // Never let the reporter itself crash the app.
      }
    }
  }
}

/// Neutral release-mode error placeholder — replaces the default red box.
class _ReleaseErrorBox extends StatelessWidget {
  const _ReleaseErrorBox();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ColoredBox(
      color: cs.surface,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.error_outline, size: 40, color: cs.error),
              const SizedBox(height: 12),
              Text(
                'Something went wrong rendering this view.',
                textAlign: TextAlign.center,
                style: Theme.of(
                  context,
                ).textTheme.bodyMedium?.copyWith(color: cs.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
