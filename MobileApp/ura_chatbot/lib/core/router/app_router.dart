import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../features/chat/screens/chat_screen.dart';
import '../../features/consent/providers/consent_provider.dart';
import '../../features/consent/screens/consent_screen.dart';
import '../../features/faq/models/faq_models.dart';
import '../../features/faq/screens/faq_detail_screen.dart';
import '../../features/faq/screens/faq_screen.dart';
import '../../features/settings/screens/settings_screen.dart';
import '../ui/offline_banner.dart';
import '../../features/chat/providers/chat_provider.dart';

/// Top-level route names — use constants at call sites instead of raw
/// path strings to keep route names refactor-friendly.
class AppRoutes {
  const AppRoutes._();

  static const String chat = '/';
  static const String faq = '/faq';
  static const String faqDetail = '/faq/:tagId';
  static const String settings = '/settings';
  static const String consent = '/consent';

  static String faqDetailFor(String tagId) => '/faq/$tagId';
}

/// The app-wide router.
///
/// Uses [StatefulShellRoute.indexedStack] so each tab preserves its own
/// [Navigator] stack — users can drill into an FAQ detail, switch to
/// Chat, come back, and find their scroll + drill-down state intact.
/// This is the 2026 replacement for the old [IndexedStack] hand-rolled
/// in ``AppShell``.
///
/// Deep linking: ``/faq/:tagId`` is already a full path, so universal
/// links like ``https://ura-chatbot.example/faq/vat`` land the user on
/// the exact FAQ detail screen when the scheme handler is registered.
final routerProvider = Provider<GoRouter>((ref) {
  // Consent bootstrap gate: on first launch the user must see the
  // consent screen before touching any app surface. We redirect from
  // anywhere → `/consent` until `markBootstrapped` fires. After that,
  // the consent screen is reachable normally from Settings.
  return GoRouter(
    initialLocation: AppRoutes.chat,
    redirect: (context, state) {
      final consent = ref.read(consentProvider);
      if (!consent.loaded) return null; // wait for hydrate
      if (!consent.bootstrapped && state.matchedLocation != AppRoutes.consent) {
        return AppRoutes.consent;
      }
      return null;
    },
    refreshListenable: _ConsentRefreshListenable(ref),
    routes: [
      GoRoute(
        path: AppRoutes.consent,
        pageBuilder: (context, state) {
          // `extra` is `true` when routed as the first-launch flow
          // (from the redirect below), and `false` when the user
          // reaches the screen via Settings → Privacy & voice consent.
          final isInitial = state.extra is! bool || state.extra == true;
          return NoTransitionPage(
            child: ConsentScreen(asInitialFlow: isInitial),
          );
        },
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) {
          return _AppScaffold(navigationShell: navigationShell);
        },
        branches: [
          // Chat branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.chat,
                pageBuilder: (context, state) =>
                    const NoTransitionPage(child: ChatScreen()),
              ),
            ],
          ),

          // FAQ branch — supports a nested detail route so drill-down
          // preserves the parent list's scroll position.
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.faq,
                pageBuilder: (context, state) =>
                    const NoTransitionPage(child: FAQScreen()),
                routes: [
                  GoRoute(
                    path: ':tagId',
                    builder: (context, state) {
                      // The tag is passed via GoRouterState.extra so we
                      // don't have to re-fetch a TagInfo from the id.
                      final tag = state.extra as TagInfo?;
                      final tagId = state.pathParameters['tagId'] ?? '';
                      return FAQDetailScreen(
                        tagId: tagId,
                        tagName: tag?.name ?? tagId,
                      );
                    },
                  ),
                ],
              ),
            ],
          ),

          // Settings branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.settings,
                pageBuilder: (context, state) =>
                    const NoTransitionPage(child: SettingsScreen()),
              ),
            ],
          ),
        ],
      ),
    ],
  );
});

/// Bottom-nav shell — replaces the old hand-rolled ``AppShell``.
///
/// The offline banner lives above the shell so network status is
/// visible regardless of which tab is active.
class _AppScaffold extends ConsumerWidget {
  const _AppScaffold({required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final offlineReady = ref.watch(chatProvider.select((s) => s.offlineReady));

    return Scaffold(
      body: Column(
        children: [
          OfflineBanner(hasOfflineFallback: offlineReady),
          Expanded(child: navigationShell),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: navigationShell.currentIndex,
        onDestinationSelected: (i) {
          navigationShell.goBranch(
            i,
            // Tap the already-selected tab = scroll to root (matches
            // the idiomatic Material 3 behaviour).
            initialLocation: i == navigationShell.currentIndex,
          );
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.chat_outlined),
            selectedIcon: Icon(Icons.chat),
            label: 'Chat',
          ),
          NavigationDestination(
            icon: Icon(Icons.help_outline),
            selectedIcon: Icon(Icons.help),
            label: 'FAQ',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings_outlined),
            selectedIcon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}

/// Bridges [consentProvider] state changes to GoRouter's redirect
/// evaluation. Without this, the router wouldn't leave `/consent`
/// automatically when the user hits Continue.
class _ConsentRefreshListenable extends ChangeNotifier {
  _ConsentRefreshListenable(this._ref) {
    _sub = _ref.listen(consentProvider, (_, _) => notifyListeners());
  }

  final Ref _ref;
  late final ProviderSubscription _sub;

  @override
  void dispose() {
    _sub.close();
    super.dispose();
  }
}
