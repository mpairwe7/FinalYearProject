import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Live network connectivity state.
///
/// Emits a stream of booleans (``true`` when online, ``false`` when
/// offline) derived from ``connectivity_plus``. Consumers that want to
/// show an offline banner or disable the send button use this instead
/// of polling the API themselves.
///
/// Note: on mobile, [ConnectivityResult.none] means "no radio link" —
/// the device could still be on a captive portal or behind a broken
/// proxy. For a true end-to-end check, combine this with an HTTP
/// heartbeat to the /ready endpoint.
final connectivityStatusProvider = StreamProvider<bool>((ref) {
  final controller = StreamController<bool>();
  final connectivity = Connectivity();

  bool hasConnectivity(List<ConnectivityResult> results) {
    if (results.isEmpty) return false;
    return results.any((r) => r != ConnectivityResult.none);
  }

  // Seed the initial value before the stream fires.
  unawaited(() async {
    try {
      final initial = await connectivity.checkConnectivity();
      controller.add(hasConnectivity(initial));
    } catch (_) {
      controller.add(true); // best-effort; assume online on failure
    }
  }());

  final sub = connectivity.onConnectivityChanged.listen((results) {
    controller.add(hasConnectivity(results));
  });

  ref.onDispose(() {
    sub.cancel();
    controller.close();
  });

  return controller.stream;
});

/// Synchronous helper: ``true`` if the last known state is online.
///
/// Use this when you need a quick boolean (e.g. to disable a button)
/// without pattern-matching the [AsyncValue] from
/// [connectivityStatusProvider]. Defaults to ``true`` while the first
/// sample is still in flight so the UI doesn't flash an "offline"
/// banner on cold start.
final isOnlineProvider = Provider<bool>((ref) {
  final status = ref.watch(connectivityStatusProvider);
  return status.maybeWhen(data: (online) => online, orElse: () => true);
});
