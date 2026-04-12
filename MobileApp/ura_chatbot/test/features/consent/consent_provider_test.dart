import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ura_chatbot/features/consent/models/consent_models.dart';
import 'package:ura_chatbot/features/consent/providers/consent_provider.dart';

/// Minimal in-memory secure storage for unit tests.
///
/// `flutter_secure_storage` talks to Android Keystore / iOS Keychain
/// via platform channels, so we replace it with a pure-Dart double.
class _FakeSecureStorage implements FlutterSecureStorage {
  final Map<String, String> _store = {};

  @override
  Future<String?> read({
    required String key,
    AndroidOptions? aOptions,
    IOSOptions? iOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      _store[key];

  @override
  Future<void> write({
    required String key,
    required String? value,
    AndroidOptions? aOptions,
    IOSOptions? iOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    if (value == null) {
      _store.remove(key);
    } else {
      _store[key] = value;
    }
  }

  @override
  Future<void> delete({
    required String key,
    AndroidOptions? aOptions,
    IOSOptions? iOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    _store.remove(key);
  }

  @override
  noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// Pump microtasks until _load() completes and sets loaded=true.
Future<void> _waitForLoad(ProviderContainer container) async {
  for (var i = 0; i < 20; i++) {
    await Future<void>.delayed(Duration.zero);
    if (container.read(consentProvider).loaded) return;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  ProviderContainer makeContainer() => ProviderContainer(
        overrides: [
          consentStorageProvider.overrideWithValue(_FakeSecureStorage()),
        ],
      );

  test('starts in empty, unloaded state', () async {
    final container = makeContainer();
    final initial = container.read(consentProvider);
    expect(initial.grants, isEmpty);
    await _waitForLoad(container);
    final afterLoad = container.read(consentProvider);
    expect(afterLoad.loaded, isTrue);
    expect(afterLoad.bootstrapped, isFalse);
    container.dispose();
  });

  test('grant + withdraw round-trip persists to storage', () async {
    final container = makeContainer();
    await _waitForLoad(container);

    final notifier = container.read(consentProvider.notifier);
    final grantId = await notifier.grant(ConsentCategory.voiceRecord);
    expect(grantId, isNotEmpty);

    var state = container.read(consentProvider);
    expect(state.isGranted(ConsentCategory.voiceRecord), isTrue);
    expect(
      state.grants[ConsentCategory.voiceRecord]!.grantId,
      equals(grantId),
    );

    await notifier.withdraw(ConsentCategory.voiceRecord);
    state = container.read(consentProvider);
    expect(state.isGranted(ConsentCategory.voiceRecord), isFalse);
    expect(
      state.grants[ConsentCategory.voiceRecord]!.withdrawnAtMs,
      isNotNull,
    );

    container.dispose();
  });

  test('markBootstrapped flips the bootstrapped flag', () async {
    final container = makeContainer();
    await _waitForLoad(container);

    expect(container.read(consentProvider).bootstrapped, isFalse);
    await container.read(consentProvider.notifier).markBootstrapped();
    expect(container.read(consentProvider).bootstrapped, isTrue);

    container.dispose();
  });

  test('re-granting after withdrawal issues a new grant id', () async {
    final container = makeContainer();
    await _waitForLoad(container);

    final notifier = container.read(consentProvider.notifier);
    final firstId =
        await notifier.grant(ConsentCategory.voiceStoreTranscript);
    await notifier.withdraw(ConsentCategory.voiceStoreTranscript);
    final secondId =
        await notifier.grant(ConsentCategory.voiceStoreTranscript);

    expect(secondId, isNot(equals(firstId)));
    container.dispose();
  });
}
