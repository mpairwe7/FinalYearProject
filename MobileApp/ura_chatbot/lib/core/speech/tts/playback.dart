/// Audio playback for TTS synthesis results.
///
/// Wraps `just_audio` for buffered playback of raw PCM16 data with
/// play/pause/stop semantics. The player is held as a singleton so we
/// don't open a new audio session on every play.
library;

import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';

/// Playback state exposed to the UI.
enum PlaybackState { idle, loading, playing, paused }

class TtsPlayback {
  TtsPlayback({@visibleForTesting AudioPlayer? player})
    : _player = player ?? AudioPlayer();

  final AudioPlayer _player;
  final _stateController = StreamController<PlaybackState>.broadcast();
  StreamSubscription<PlayerState>? _playerSub;

  PlaybackState _state = PlaybackState.idle;

  PlaybackState get state => _state;
  Stream<PlaybackState> get stateStream => _stateController.stream;

  /// Play raw PCM16 little-endian mono audio at [sampleRate].
  Future<void> play(Uint8List pcm16, {int sampleRate = 22050}) async {
    // Cancel any previous completion listener to avoid leaking subs.
    await _playerSub?.cancel();
    _setState(PlaybackState.loading);
    try {
      // Build a minimal WAV header so just_audio can decode it.
      final wav = _buildWav(pcm16, sampleRate: sampleRate);
      final source = _BytesAudioSource(wav);
      await _player.setAudioSource(source);
      _setState(PlaybackState.playing);

      _playerSub = _player.playerStateStream.listen((ps) {
        if (ps.processingState == ProcessingState.completed) {
          _setState(PlaybackState.idle);
        }
      });

      await _player.play();
    } catch (e) {
      _setState(PlaybackState.idle);
      debugPrint('TTS playback error: $e');
    }
  }

  Future<void> pause() async {
    await _player.pause();
    _setState(PlaybackState.paused);
  }

  Future<void> resume() async {
    await _player.play();
    _setState(PlaybackState.playing);
  }

  Future<void> stop() async {
    await _player.stop();
    _setState(PlaybackState.idle);
  }

  Future<void> dispose() async {
    await _playerSub?.cancel();
    await _player.dispose();
    await _stateController.close();
  }

  void _setState(PlaybackState s) {
    _state = s;
    _stateController.add(s);
  }

  /// Build a minimal 44-byte WAV header + PCM16 data.
  static Uint8List _buildWav(Uint8List pcm16, {required int sampleRate}) {
    final dataSize = pcm16.length;
    final fileSize = 36 + dataSize;
    final byteRate = sampleRate * 2; // 16-bit mono
    final header = ByteData(44);

    // "RIFF" chunk
    header.setUint8(0, 0x52); // R
    header.setUint8(1, 0x49); // I
    header.setUint8(2, 0x46); // F
    header.setUint8(3, 0x46); // F
    header.setUint32(4, fileSize, Endian.little);
    header.setUint8(8, 0x57); // W
    header.setUint8(9, 0x41); // A
    header.setUint8(10, 0x56); // V
    header.setUint8(11, 0x45); // E

    // "fmt " sub-chunk
    header.setUint8(12, 0x66); // f
    header.setUint8(13, 0x6D); // m
    header.setUint8(14, 0x74); // t
    header.setUint8(15, 0x20); // ' '
    header.setUint32(16, 16, Endian.little); // sub-chunk size
    header.setUint16(20, 1, Endian.little); // PCM format
    header.setUint16(22, 1, Endian.little); // mono
    header.setUint32(24, sampleRate, Endian.little);
    header.setUint32(28, byteRate, Endian.little);
    header.setUint16(32, 2, Endian.little); // block align
    header.setUint16(34, 16, Endian.little); // bits per sample

    // "data" sub-chunk
    header.setUint8(36, 0x64); // d
    header.setUint8(37, 0x61); // a
    header.setUint8(38, 0x74); // t
    header.setUint8(39, 0x61); // a
    header.setUint32(40, dataSize, Endian.little);

    final wav = Uint8List(44 + dataSize);
    wav.setRange(0, 44, header.buffer.asUint8List());
    wav.setRange(44, 44 + dataSize, pcm16);
    return wav;
  }
}

/// In-memory audio source for just_audio.
// ignore: experimental_member_use
class _BytesAudioSource extends StreamAudioSource {
  _BytesAudioSource(this._bytes);
  final Uint8List _bytes;

  @override
  // ignore: experimental_member_use
  Future<StreamAudioResponse> request([int? start, int? end]) async {
    final s = start ?? 0;
    final e = end ?? _bytes.length;
    // ignore: experimental_member_use
    return StreamAudioResponse(
      sourceLength: _bytes.length,
      contentLength: e - s,
      offset: s,
      stream: Stream.value(_bytes.sublist(s, e)),
      contentType: 'audio/wav',
    );
  }
}
