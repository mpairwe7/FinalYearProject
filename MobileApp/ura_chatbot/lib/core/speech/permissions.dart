/// Centralised permission management for the speech subsystem.
///
/// Every path that opens the microphone goes through this helper so
/// we have exactly one place that handles the three-state permission
/// model (granted / denied / permanently denied) and maps it to a
/// single [SpeechPermissionStatus] that callers can act on.
library;

import 'package:flutter/foundation.dart';
import 'package:permission_handler/permission_handler.dart' as ph;
import 'package:permission_handler/permission_handler.dart'
    show PermissionStatus, Permission;

enum SpeechPermissionStatus {
  /// User has granted microphone access. Safe to start recording.
  granted,

  /// User has explicitly denied access at least once but the OS
  /// still allows re-prompting. Caller should try again after a UX
  /// explanation.
  denied,

  /// User has permanently denied access (or the OS has suppressed
  /// future prompts). Caller should surface a "Open app settings"
  /// button rather than re-prompting in a loop.
  permanentlyDenied,

  /// Permission request resolved to a state we don't try to interpret
  /// (e.g., the OS is in a transitional state). Treat as "try again".
  unknown,
}

/// Thin wrapper around `permission_handler` that translates the
/// package's five-state enum into our three actionable outcomes.
class SpeechPermissions {
  SpeechPermissions({@visibleForTesting PermissionDelegate? delegate})
    : _delegate = delegate ?? const _RealPermissionDelegate();

  final PermissionDelegate _delegate;

  /// Current microphone permission status, without prompting.
  Future<SpeechPermissionStatus> status() async {
    final s = await _delegate.microphoneStatus();
    return _translate(s);
  }

  /// Request microphone permission. Safe to call repeatedly — the OS
  /// handles the "don't ask me again" state, and we surface
  /// [SpeechPermissionStatus.permanentlyDenied] so callers route to
  /// app settings.
  Future<SpeechPermissionStatus> requestMicrophone() async {
    final current = await _delegate.microphoneStatus();
    if (current == PermissionStatus.granted) {
      return SpeechPermissionStatus.granted;
    }
    if (current == PermissionStatus.permanentlyDenied) {
      return SpeechPermissionStatus.permanentlyDenied;
    }
    final next = await _delegate.requestMicrophone();
    return _translate(next);
  }

  /// Open the OS settings screen for this app (used when the user has
  /// permanently denied mic access and needs to re-enable it).
  Future<bool> openSettings() => _delegate.openAppSettings();

  static SpeechPermissionStatus _translate(PermissionStatus s) {
    return switch (s) {
      PermissionStatus.granted => SpeechPermissionStatus.granted,
      PermissionStatus.limited => SpeechPermissionStatus.granted,
      PermissionStatus.provisional => SpeechPermissionStatus.granted,
      PermissionStatus.denied => SpeechPermissionStatus.denied,
      PermissionStatus.permanentlyDenied =>
        SpeechPermissionStatus.permanentlyDenied,
      PermissionStatus.restricted => SpeechPermissionStatus.permanentlyDenied,
    };
  }
}

/// Seam so tests can swap in a fake permission backend without
/// touching the real platform channel.
abstract class PermissionDelegate {
  Future<PermissionStatus> microphoneStatus();
  Future<PermissionStatus> requestMicrophone();
  Future<bool> openAppSettings();
}

class _RealPermissionDelegate implements PermissionDelegate {
  const _RealPermissionDelegate();

  @override
  Future<PermissionStatus> microphoneStatus() => Permission.microphone.status;

  @override
  Future<PermissionStatus> requestMicrophone() =>
      Permission.microphone.request();

  @override
  Future<bool> openAppSettings() => ph.openAppSettings();
}
