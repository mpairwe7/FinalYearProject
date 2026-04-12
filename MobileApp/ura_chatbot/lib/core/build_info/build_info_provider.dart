import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';

/// Build / release metadata sourced from ``package_info_plus``.
///
/// Exposes:
///   * app name (e.g. "URA Chatbot")
///   * semantic version (e.g. "1.1.0")
///   * build number (e.g. "42")
///
/// Call from any UI that previously hardcoded "Version 1.1.0" so the
/// string stays in sync with ``pubspec.yaml`` on every release.
class BuildInfo {
  final String appName;
  final String packageName;
  final String version;
  final String buildNumber;

  const BuildInfo({
    required this.appName,
    required this.packageName,
    required this.version,
    required this.buildNumber,
  });

  /// Human-readable `"<version>+<build>"` label.
  String get displayVersion => '$version+$buildNumber';
}

/// Async provider — lets the UI render a placeholder while the native
/// channel resolves package metadata.
final buildInfoProvider = FutureProvider<BuildInfo>((ref) async {
  final pi = await PackageInfo.fromPlatform();
  return BuildInfo(
    appName: pi.appName,
    packageName: pi.packageName,
    version: pi.version,
    buildNumber: pi.buildNumber,
  );
});
