import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'core/audit/local_ledger.dart';
import 'core/config/feature_flags.dart';
import 'core/errors/error_handler.dart';
import 'core/router/app_router.dart';
import 'core/storage/local_storage.dart';
import 'core/theme/app_theme.dart';
import 'features/settings/providers/settings_provider.dart';

/// Entry point — every startup-sensitive side effect (error handler,
/// orientation lock, storage init) runs inside ``runZonedGuarded`` so
/// uncaught async errors are captured and routed to [AppErrorHandler].
Future<void> main() async {
  await runZonedGuarded<Future<void>>(() async {
    WidgetsFlutterBinding.ensureInitialized();

    // Install framework + platform error handlers.
    AppErrorHandler.install();

    // Lock to portrait on phones (tablets inherit both).
    await SystemChrome.setPreferredOrientations([
      DeviceOrientation.portraitUp,
      DeviceOrientation.portraitDown,
    ]);

    // Preload local storage — must complete before any provider reads
    // from SharedPreferences.
    await LocalStorage.init();

    // Resolve SharedPreferences once and build the feature-flag store
    // so every notifier can read flags synchronously from `build()`.
    final prefs = await SharedPreferences.getInstance();
    final flagStore = FeatureFlagStore(prefs);

    // Open the local audit ledger so consent grants and (later)
    // speech events can append without an async bootstrap race.
    try {
      await LocalLedger.init();
    } catch (e, s) {
      // Ledger failure must not block app boot — log and continue.
      AppErrorHandler.report(e, s);
    }

    runApp(
      ProviderScope(
        overrides: [featureFlagStoreProvider.overrideWithValue(flagStore)],
        child: const URAApp(),
      ),
    );
  }, AppErrorHandler.report);
}

/// Root app widget.
///
/// Uses [MaterialApp.router] with a [GoRouter] from [routerProvider]
/// so every navigation is declarative and deep-link compatible. The
/// bottom-nav shell lives inside the router (see [app_router.dart]).
class URAApp extends ConsumerWidget {
  const URAApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsProvider);
    final router = ref.watch(routerProvider);

    return MaterialApp.router(
      title: 'URA Tax Assistant',
      debugShowCheckedModeBanner: false,
      themeMode: settings.themeMode,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      routerConfig: router,
      // Respect system text scaling but clamp so the layout doesn't break
      // at extreme accessibility settings (iOS "Larger Text", Android huge).
      builder: (context, child) {
        final mq = MediaQuery.of(context);
        return MediaQuery(
          data: mq.copyWith(
            textScaler: mq.textScaler.clamp(
              minScaleFactor: 0.85,
              maxScaleFactor: 1.35,
            ),
          ),
          child: child ?? const SizedBox.shrink(),
        );
      },
    );
  }
}
