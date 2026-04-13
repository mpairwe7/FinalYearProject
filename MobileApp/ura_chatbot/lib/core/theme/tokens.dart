import 'package:flutter/material.dart';

/// Design tokens used across the app.
///
/// Every layout value in the app should come from one of these constants
/// rather than a hardcoded magic number. This keeps spacing, radius,
/// durations and motion curves consistent with Material 3 (2024+ specs)
/// and makes wholesale visual tweaks one-line changes.
///
/// Source: Material 3 density + spatial guidance, adapted to URA's
/// content-heavy chat surface.

/// Spacing scale (4dp base grid, Material 3 recommended).
class AppSpacing {
  AppSpacing._();

  static const double xxs = 2;
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;
  static const double xxl = 24;
  static const double xxxl = 32;
  static const double huge = 48;

  // Common EdgeInsets built from the scale above.
  static const EdgeInsets screenPadding = EdgeInsets.all(lg);
  static const EdgeInsets listPadding = EdgeInsets.symmetric(
    horizontal: md,
    vertical: sm,
  );
  static const EdgeInsets bubbleInner = EdgeInsets.symmetric(
    horizontal: 14,
    vertical: 10,
  );
  static const EdgeInsets cardInner = EdgeInsets.all(lg);
}

/// Border radius scale.
class AppRadius {
  AppRadius._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;
  static const double xxl = 24;
  static const double pill = 999;

  // Reusable BorderRadius instances.
  static const BorderRadius cardRadius = BorderRadius.all(Radius.circular(lg));
  static const BorderRadius bubbleRadius = BorderRadius.all(
    Radius.circular(18),
  );
  static const BorderRadius inputRadius = BorderRadius.all(
    Radius.circular(xxl),
  );
  static const BorderRadius chipRadius = BorderRadius.all(Radius.circular(xl));
}

/// Motion tokens — Material 3 durations + curves.
///
/// "Emphasized" curves give non-linear ease-out which feels more
/// responsive than the stock [Curves.easeOut]. They're the 2024 default
/// for state transitions in Material 3.
class AppMotion {
  AppMotion._();

  static const Duration instant = Duration(milliseconds: 100);
  static const Duration fast = Duration(milliseconds: 150);
  static const Duration medium = Duration(milliseconds: 300);
  static const Duration slow = Duration(milliseconds: 500);

  static const Curve emphasized = Cubic(0.2, 0.0, 0, 1.0);
  static const Curve emphasizedDecelerate = Cubic(0.05, 0.7, 0.1, 1.0);
  static const Curve emphasizedAccelerate = Cubic(0.3, 0.0, 0.8, 0.15);
}

/// Elevation tokens (shadow + surface tint in Material 3).
class AppElevation {
  AppElevation._();

  static const double level0 = 0;
  static const double level1 = 1;
  static const double level2 = 3;
  static const double level3 = 6;
  static const double level4 = 8;
  static const double level5 = 12;
}

/// Size tokens for interactive elements and icons.
///
/// Material 3 standard icon sizes: 18, 20, 24. Touch targets: 48dp min.
class AppSize {
  AppSize._();

  static const double iconSmall = 18;
  static const double iconMedium = 20;
  static const double iconLarge = 24;
  static const double touchTarget = 48;
  static const double spinnerSmall = 14;
  static const double buttonLarge = 52;
}

/// Convenience: alpha helpers that match Flutter 3.27+ ``Color.withValues``.
///
/// Flutter deprecated ``Color.withAlpha(int)`` in 3.27 in favour of
/// ``withValues(alpha: double)``. These extensions give us ergonomic
/// call sites that don't litter the codebase with the verbose form.
///
/// The named method ``tint(double)`` is used instead of ``alpha`` so it
/// does not shadow the built-in ``Color.alpha`` int getter (which would
/// silently resolve to the wrong thing at call sites).
extension ColorTokens on Color {
  /// Apply an alpha value in [0, 1]. Alias for ``withValues(alpha: v)``.
  Color tint(double value) => withValues(alpha: value);

  /// Common alpha shortcuts — use these when you find yourself reaching
  /// for magic percentages in the UI.
  Color get subtle => withValues(alpha: 0.08);
  Color get muted => withValues(alpha: 0.32);
  Color get soft => withValues(alpha: 0.56);
  Color get emphasis => withValues(alpha: 0.78);
}
