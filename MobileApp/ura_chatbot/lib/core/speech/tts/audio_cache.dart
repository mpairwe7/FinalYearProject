/// SHA-256-keyed LRU disk cache for TTS synthesis results.
///
/// Keyed on `(text_hash, voice_id)` so repeated FAQ questions ("What
/// is the VAT rate?") are served instantly on the second ask. Default
/// capacity is 50 MB — configurable in Settings.
///
/// The cache stores raw PCM16 files with a `.pcm` extension under
/// `<app_support>/speech/tts_cache/`. A companion `.meta` JSON sidecar
/// holds the sample rate, locale, and model ID needed to reconstruct
/// a [TtsResult].
library;

import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'tts_engine.dart';

class TtsAudioCache {
  TtsAudioCache({this.maxSizeBytes = 50 * 1024 * 1024});

  final int maxSizeBytes;
  late final String _cacheDir;
  bool _initialized = false;

  Future<void> init() async {
    if (_initialized) return;
    final dir = await getApplicationSupportDirectory();
    _cacheDir = p.join(dir.path, 'speech', 'tts_cache');
    await Directory(_cacheDir).create(recursive: true);
    _initialized = true;
  }

  String _key(String text, String modelId) {
    final hash = sha256.convert(utf8.encode('$modelId:$text'));
    return hash.toString().substring(0, 32);
  }

  File _pcmFile(String key) => File(p.join(_cacheDir, '$key.pcm'));
  File _metaFile(String key) => File(p.join(_cacheDir, '$key.meta'));

  /// Look up a cached result. Returns `null` on miss.
  Future<TtsResult?> get(String text, String modelId) async {
    await init();
    final key = _key(text, modelId);
    final pcm = _pcmFile(key);
    final meta = _metaFile(key);
    if (!await pcm.exists() || !await meta.exists()) return null;

    try {
      final metaJson =
          jsonDecode(await meta.readAsString()) as Map<String, Object?>;
      final wavBytes = await pcm.readAsBytes();
      return TtsResult(
        wavBytes: wavBytes,
        sampleRate: (metaJson['sample_rate'] as num).toInt(),
        locale: metaJson['locale'] as String,
        modelId: metaJson['model_id'] as String,
        latencyMs: 0, // cached — zero synthesis latency
        durationMs: (metaJson['duration_ms'] as num).toDouble(),
      );
    } catch (_) {
      // Corrupted cache entry — delete and return miss.
      await _deleteKey(key);
      return null;
    }
  }

  /// Store a synthesis result. Evicts oldest entries if over capacity.
  Future<void> put(String text, TtsResult result) async {
    await init();
    final key = _key(text, result.modelId);
    final pcm = _pcmFile(key);
    final meta = _metaFile(key);

    // Atomic write: write to .tmp first, then rename. Prevents
    // corrupt entries if the process is killed mid-write.
    await pcm.writeAsBytes(result.wavBytes);
    final metaTmp = File('${meta.path}.tmp');
    await metaTmp.writeAsString(jsonEncode({
      'sample_rate': result.sampleRate,
      'locale': result.locale,
      'model_id': result.modelId,
      'duration_ms': result.durationMs,
    }));
    await metaTmp.rename(meta.path);

    await _evictIfNeeded();
  }

  /// Total bytes used by the cache.
  Future<int> sizeBytes() async {
    await init();
    final dir = Directory(_cacheDir);
    if (!await dir.exists()) return 0;
    int total = 0;
    await for (final entity in dir.list()) {
      if (entity is File) {
        total += await entity.length();
      }
    }
    return total;
  }

  /// Delete all cached entries.
  Future<void> clear() async {
    await init();
    final dir = Directory(_cacheDir);
    if (await dir.exists()) {
      await dir.delete(recursive: true);
      await dir.create(recursive: true);
    }
  }

  Future<void> _evictIfNeeded() async {
    final dir = Directory(_cacheDir);
    if (!await dir.exists()) return;

    final files = <File>[];
    int total = 0;
    await for (final entity in dir.list()) {
      if (entity is File) {
        files.add(entity);
        total += await entity.length();
      }
    }

    if (total <= maxSizeBytes) return;

    // Sort by last modified (oldest first) and delete until under limit.
    // Use async stat to avoid blocking the UI thread.
    final stats = await Future.wait(files.map((f) => f.stat()));
    final indexed = List.generate(files.length, (i) => i);
    indexed.sort(
        (a, b) => stats[a].modified.compareTo(stats[b].modified));
    final sorted = indexed.map((i) => files[i]).toList();

    for (final file in sorted) {
      if (total <= maxSizeBytes) break;
      final size = await file.length();
      await file.delete();
      total -= size;
    }
  }

  Future<void> _deleteKey(String key) async {
    try {
      await _pcmFile(key).delete();
    } catch (_) {}
    try {
      await _metaFile(key).delete();
    } catch (_) {}
  }
}
