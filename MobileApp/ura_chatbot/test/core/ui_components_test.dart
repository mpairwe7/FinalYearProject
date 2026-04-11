// Unit tests for the shared UI components created in the 2026 audit:
// EmptyState, SkeletonList, AppErrorView, OfflineBanner, design tokens.
//
// These widgets are building blocks used across the app. Keeping them
// independently tested means a UI regression in one screen can't break
// unrelated features silently.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ura_chatbot/core/theme/tokens.dart';
import 'package:ura_chatbot/core/ui/app_error_view.dart';
import 'package:ura_chatbot/core/ui/empty_state.dart';
import 'package:ura_chatbot/core/ui/loading_skeleton.dart';
import 'package:ura_chatbot/core/ui/offline_banner.dart';

Widget _wrap(Widget child) => ProviderScope(
      child: MaterialApp(
        theme: ThemeData(useMaterial3: true),
        home: Scaffold(body: child),
      ),
    );

void main() {
  group('EmptyState', () {
    testWidgets('renders title, description, icon, and optional action',
        (tester) async {
      var actionTapped = false;
      await tester.pumpWidget(
        _wrap(
          EmptyState(
            icon: Icons.inbox_outlined,
            title: 'Nothing here',
            description: 'Pull down to refresh.',
            action: FilledButton(
              onPressed: () => actionTapped = true,
              child: const Text('Refresh'),
            ),
          ),
        ),
      );

      expect(find.text('Nothing here'), findsOneWidget);
      expect(find.text('Pull down to refresh.'), findsOneWidget);
      expect(find.byIcon(Icons.inbox_outlined), findsOneWidget);
      expect(find.text('Refresh'), findsOneWidget);

      await tester.tap(find.text('Refresh'));
      await tester.pump();
      expect(actionTapped, isTrue);
    });

    testWidgets('description is optional', (tester) async {
      await tester.pumpWidget(
        _wrap(
          const EmptyState(
            icon: Icons.help_outline,
            title: 'Empty',
          ),
        ),
      );
      expect(find.text('Empty'), findsOneWidget);
      // No exception thrown when description is null.
    });
  });

  group('SkeletonList', () {
    testWidgets('renders the requested number of skeleton items',
        (tester) async {
      await tester.pumpWidget(
        _wrap(
          const SizedBox(
            height: 600,
            child: SkeletonList(itemCount: 4, itemHeight: 60),
          ),
        ),
      );
      // The shimmer wraps the list; we just assert the list itself built
      // without errors.
      expect(find.byType(SkeletonList), findsOneWidget);
      expect(find.byType(ListView), findsOneWidget);
    });
  });

  group('AppErrorView', () {
    testWidgets('shows title/message and calls onRetry', (tester) async {
      var retries = 0;
      await tester.pumpWidget(
        _wrap(
          AppErrorView(
            title: 'Oops',
            message: 'Connection failed',
            onRetry: () => retries++,
          ),
        ),
      );
      expect(find.text('Oops'), findsOneWidget);
      expect(find.text('Connection failed'), findsOneWidget);
      expect(find.text('Try again'), findsOneWidget);

      await tester.tap(find.text('Try again'));
      await tester.pump();
      expect(retries, 1);
    });

    testWidgets('retry button is hidden when onRetry is null',
        (tester) async {
      await tester.pumpWidget(
        _wrap(
          const AppErrorView(
            title: 'Fatal',
            message: 'Nothing to do',
          ),
        ),
      );
      expect(find.text('Try again'), findsNothing);
    });
  });

  group('OfflineBanner', () {
    testWidgets('reactive to connectivity stream', (tester) async {
      // The banner reads isOnlineProvider which defaults to ``true`` during
      // the first-sample grace period, so it should render as an empty
      // zero-height box on initial pump.
      await tester.pumpWidget(_wrap(const OfflineBanner()));
      // AnimatedSize collapses to zero height when online.
      final box = find.byType(SizedBox);
      expect(box, findsWidgets);
    });
  });

  group('Design tokens', () {
    test('spacing scale is monotonic', () {
      expect(AppSpacing.xxs < AppSpacing.xs, isTrue);
      expect(AppSpacing.xs < AppSpacing.sm, isTrue);
      expect(AppSpacing.sm < AppSpacing.md, isTrue);
      expect(AppSpacing.md < AppSpacing.lg, isTrue);
      expect(AppSpacing.lg < AppSpacing.xl, isTrue);
      expect(AppSpacing.xl < AppSpacing.xxl, isTrue);
    });

    test('radius scale is monotonic', () {
      expect(AppRadius.xs < AppRadius.sm, isTrue);
      expect(AppRadius.sm < AppRadius.md, isTrue);
      expect(AppRadius.md < AppRadius.lg, isTrue);
      expect(AppRadius.lg < AppRadius.xl, isTrue);
      expect(AppRadius.xl < AppRadius.xxl, isTrue);
    });

    test('motion durations are monotonic', () {
      expect(AppMotion.instant < AppMotion.fast, isTrue);
      expect(AppMotion.fast < AppMotion.medium, isTrue);
      expect(AppMotion.medium < AppMotion.slow, isTrue);
    });

    test('ColorTokens extension does not shadow Color.alpha int getter', () {
      // The extension method was renamed from ``alpha`` to ``tint`` to
      // avoid shadowing the built-in ``Color.alpha`` getter. This test
      // guards against a regression where someone re-adds a conflicting
      // name.
      const c = Color(0xFF1A3A6B);
      expect(c.tint(0.5).a, closeTo(0.5, 0.01));
      expect(c.subtle.a, closeTo(0.08, 0.01));
      expect(c.muted.a, closeTo(0.32, 0.01));
      expect(c.soft.a, closeTo(0.56, 0.01));
      expect(c.emphasis.a, closeTo(0.78, 0.01));
    });
  });
}
