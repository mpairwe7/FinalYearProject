import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/build_info/build_info_provider.dart';
import '../../../core/config/api_config.dart';
import '../../../core/config/feature_flags.dart';
import '../../../core/network/api_client.dart';
import '../../../core/router/app_router.dart';
import '../../../core/storage/local_storage.dart';
import '../../../core/theme/tokens.dart';
import '../../consent/models/consent_models.dart';
import '../../consent/providers/consent_provider.dart';
import '../providers/settings_provider.dart';
import 'model_manager_screen.dart';
import 'speech_lab_screen.dart';

/// App settings: theme, language, server health, about.
///
/// Design notes (2026):
/// * Section headers live in a single [_SectionHeader] widget instead of
///   ad-hoc padded Texts.
/// * Theme / language rows react to haptic taps.
/// * About row reads its version string from [buildInfoProvider] so the
///   user-visible version automatically tracks ``pubspec.yaml``.
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsProvider);
    final buildInfo = ref.watch(buildInfoProvider);
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          // --- Appearance ---
          const _SectionHeader('Appearance'),
          ListTile(
            leading: const Icon(Icons.palette_outlined),
            title: const Text('Theme'),
            subtitle: Text(_themeLabel(settings.themeMode)),
            trailing: SegmentedButton<ThemeMode>(
              segments: const [
                ButtonSegment(
                  value: ThemeMode.system,
                  icon: Icon(Icons.auto_mode, size: 18),
                ),
                ButtonSegment(
                  value: ThemeMode.light,
                  icon: Icon(Icons.light_mode, size: 18),
                ),
                ButtonSegment(
                  value: ThemeMode.dark,
                  icon: Icon(Icons.dark_mode, size: 18),
                ),
              ],
              selected: {settings.themeMode},
              onSelectionChanged: (v) {
                HapticFeedback.selectionClick();
                ref.read(settingsProvider.notifier).setThemeMode(v.first);
              },
              style: const ButtonStyle(
                visualDensity: VisualDensity.compact,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
            ),
          ),

          // --- Language ---
          const _SectionHeader('Language'),
          ListTile(
            leading: const Icon(Icons.language),
            title: const Text('Language'),
            subtitle: Text(_localeName(settings.locale)),
            trailing: DropdownButton<String>(
              value: settings.locale,
              underline: const SizedBox.shrink(),
              onChanged: (v) {
                if (v == null) return;
                HapticFeedback.selectionClick();
                ref.read(settingsProvider.notifier).setLocale(v);
              },
              items: const [
                DropdownMenuItem(value: 'en', child: Text('English')),
                DropdownMenuItem(value: 'lg', child: Text('Luganda')),
                DropdownMenuItem(value: 'sw', child: Text('Swahili')),
                DropdownMenuItem(value: 'nyn', child: Text('Runyankole')),
                DropdownMenuItem(value: 'ach', child: Text('Acholi')),
              ],
            ),
          ),

          const Divider(),

          // --- Voice ---
          const _SectionHeader('Voice'),
          ListTile(
            leading: const Icon(Icons.record_voice_over_outlined),
            title: const Text('Speech models'),
            subtitle: const Text('Download and manage offline models'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const ModelManagerScreen(),
              ),
            ),
          ),
          if (kSpeechLabEnabled)
            ListTile(
              leading: const Icon(Icons.science_outlined),
              title: const Text('SpeechLab (Dev)'),
              subtitle: const Text('Engine diagnostics and eval'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const SpeechLabScreen(),
                ),
              ),
            ),

          const Divider(),

          // --- Server Status ---
          const _SectionHeader('Server'),
          const _ServerStatusTile(),

          const Divider(),

          // --- About ---
          const _SectionHeader('About'),
          ListTile(
            leading: const Icon(Icons.info_outline),
            title: Text(buildInfo.maybeWhen(
              data: (b) => b.appName,
              orElse: () => 'URA Tax Assistant',
            )),
            subtitle: Text(buildInfo.maybeWhen(
              data: (b) => 'Version ${b.displayVersion}',
              orElse: () => 'Loading version…',
            )),
          ),
          Consumer(
            builder: (context, ref, _) {
              final consent = ref.watch(consentProvider);
              final grantedCount = consent.grants.values
                  .where((g) => g.granted)
                  .length;
              final summary = consent.loaded
                  ? '$grantedCount of ${ConsentCategory.values.length} '
                      'voice permissions granted'
                  : 'Loading…';
              return ListTile(
                leading: const Icon(Icons.shield_outlined),
                title: const Text('Privacy & voice consent'),
                subtitle: Text(summary),
                trailing: const Icon(Icons.chevron_right),
                onTap: () =>
                    context.push(AppRoutes.consent, extra: false),
              );
            },
          ),
          const ListTile(
            leading: Icon(Icons.verified_user_outlined),
            title: Text('Data retention'),
            subtitle: Text(
              'PII is redacted before storage. Conversations expire after '
              '7 days. Voice audio is never persisted.',
            ),
          ),
          ListTile(
            leading: const Icon(Icons.open_in_new),
            title: const Text('Uganda Revenue Authority'),
            subtitle: const Text('ura.go.ug'),
            onTap: () => launchUrl(
              Uri.parse('https://ura.go.ug'),
              mode: LaunchMode.externalApplication,
            ),
          ),

          const SizedBox(height: AppSpacing.xxl),
          Center(
            child: Text(
              'Session: ${LocalStorage.sessionId.substring(0, 8)}…',
              style: TextStyle(
                fontSize: 11,
                color: cs.onSurfaceVariant.withValues(alpha: 0.48),
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
        ],
      ),
    );
  }

  static String _localeName(String code) => switch (code) {
        'en' => 'English',
        'lg' => 'Luganda',
        'sw' => 'Swahili',
        'nyn' => 'Runyankole',
        'ach' => 'Acholi',
        _ => code.toUpperCase(),
      };

  static String _themeLabel(ThemeMode mode) {
    return switch (mode) {
      ThemeMode.system => 'System',
      ThemeMode.light => 'Light',
      ThemeMode.dark => 'Dark',
    };
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);
  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.xs,
      ),
      child: Text(
        title,
        style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
    );
  }
}

/// Shows live server health status.
class _ServerStatusTile extends ConsumerStatefulWidget {
  const _ServerStatusTile();

  @override
  ConsumerState<_ServerStatusTile> createState() => _ServerStatusTileState();
}

class _ServerStatusTileState extends ConsumerState<_ServerStatusTile> {
  String _status = 'Checking…';
  String _mode = '';
  bool _ok = false;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _check();
  }

  Future<void> _check() async {
    setState(() => _loading = true);
    try {
      final dio = ref.read(apiClientProvider);
      final res = await dio.get<Map<String, dynamic>>(ApiConfig.ready);
      final data = res.data ?? const {};
      _status = data['status'] as String? ?? 'unknown';
      _mode = data['retrieval_mode'] as String? ?? '';
      _ok = _status == 'ready';
    } catch (_) {
      _status = 'unreachable';
      _ok = false;
    }
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return ListTile(
      leading: _loading
          ? const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : Icon(
              _ok ? Icons.check_circle : Icons.error,
              color: _ok ? Colors.green : cs.error,
            ),
      title: const Text('API: ${ApiConfig.baseUrl}'),
      subtitle: Text(
        _loading
            ? 'Checking…'
            : '$_status${_mode.isNotEmpty ? ' | $_mode retrieval' : ''}',
      ),
      trailing: IconButton(
        icon: const Icon(Icons.refresh),
        onPressed: () {
          HapticFeedback.selectionClick();
          _check();
        },
        tooltip: 'Refresh',
      ),
    );
  }
}
