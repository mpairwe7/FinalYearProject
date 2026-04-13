/// Local, append-only, hash-chained audit ledger.
///
/// Mirrors the server-side hash-chained ledger at
/// `App/backend/app/audit/ledger.py` — every mobile-side sensitive
/// event (consent grant/withdraw, mic opened, ASR result, TTS played,
/// model downloaded) is appended with:
///
///   row_hash = sha256(prev_hash || seq || payload_hash)
///
/// Tampering with any prior row invalidates every subsequent row.
/// The ledger is deliberately local-only: audio bytes never leave the
/// device, and neither do the audit rows (which would undermine the
/// "fully offline" guarantee). Users can export the ledger from the
/// dev SpeechLab screen if they want to audit their own data.
///
/// Implementation note: we use `sqflite` for durability, a single
/// `events` table, and a background `Completer` guard to serialise
/// writes so two concurrent callers cannot produce duplicate `seq`
/// values. Read paths are lock-free.
library;

import 'dart:async';
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

/// Canonical event types — keep this enum in sync with the kinds of
/// things we log. Unknown strings are accepted in [append] for forward
/// compatibility, but well-known types should use the constants.
class LedgerEventType {
  LedgerEventType._();

  static const consentGrant = 'consent_grant';
  static const consentWithdraw = 'consent_withdraw';
  static const micOpened = 'mic_opened';
  static const asrResult = 'asr_result';
  static const asrError = 'asr_error';
  static const ttsPlayed = 'tts_played';
  static const ttsCacheHit = 'tts_cache_hit';
  static const modelDownloaded = 'model_downloaded';
  static const modelDeleted = 'model_deleted';
  static const voiceSynthLabeled = 'voice_synth_labeled';
}

/// One event row. Immutable.
@immutable
class LedgerEvent {
  const LedgerEvent({
    required this.seq,
    required this.timestampMs,
    required this.type,
    required this.payload,
    required this.prevHash,
    required this.rowHash,
  });

  final int seq;
  final int timestampMs;
  final String type;
  final Map<String, Object?> payload;
  final String prevHash;
  final String rowHash;

  Map<String, Object?> toJson() => {
    'seq': seq,
    'timestamp_ms': timestampMs,
    'type': type,
    'payload': payload,
    'prev_hash': prevHash,
    'row_hash': rowHash,
  };
}

/// Singleton ledger for the app. Must call [LocalLedger.init] once at
/// startup (done from `main.dart`) before any [append] call.
class LocalLedger {
  LocalLedger._();

  static Database? _db;
  static int _lastSeq = 0;
  static String _lastHash = _genesis;
  static Completer<void>? _writeLock;

  /// SHA-256 of the ASCII string "ura-chatbot-v1" — genesis anchor so
  /// the very first row has a non-empty `prev_hash`.
  static const _genesis =
      '5ea63c17d1c63c8d01bfa2a6d78f4b60e9b4f6e08c5cf73fde8a88de5c9e11d9';

  /// Open the SQLite database and load the tail state (last seq + row
  /// hash). Safe to call multiple times; subsequent calls are no-ops.
  static Future<void> init() async {
    if (_db != null) return;
    final dir = await getApplicationSupportDirectory();
    final path = p.join(dir.path, 'audit_ledger.db');
    _db = await openDatabase(
      path,
      version: 1,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_ms INTEGER NOT NULL,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            row_hash TEXT NOT NULL
          )
        ''');
        await db.execute('CREATE INDEX idx_events_type ON events(type)');
      },
    );

    final tail = await _db!.rawQuery(
      'SELECT seq, row_hash FROM events ORDER BY seq DESC LIMIT 1',
    );
    if (tail.isNotEmpty) {
      _lastSeq = tail.first['seq'] as int;
      _lastHash = tail.first['row_hash'] as String;
    }
  }

  /// Append an event to the ledger. Serialised under a completer lock
  /// so two concurrent callers cannot race and produce duplicate
  /// sequence numbers.
  ///
  /// Returns the newly-appended row (never `null`). Throws only if
  /// the database layer rejects the insert — which is fatal and should
  /// surface to [AppErrorHandler] by the caller.
  static Future<LedgerEvent> append(
    String type,
    Map<String, Object?> payload,
  ) async {
    assert(_db != null, 'LocalLedger.init() must run before append().');

    // Serialise writes. Each caller waits on the previous completer
    // before constructing its own, guaranteeing strict ordering.
    final previous = _writeLock;
    final mine = Completer<void>();
    _writeLock = mine;
    if (previous != null) await previous.future;

    try {
      final ts = DateTime.now().millisecondsSinceEpoch;
      final payloadJson = jsonEncode(payload);
      final payloadHash = sha256.convert(utf8.encode(payloadJson)).toString();
      final seq = _lastSeq + 1;
      final prevHash = _lastHash;

      // Hash input mirrors the backend format: prev || seq || payload.
      final rowHash = sha256
          .convert(utf8.encode('$prevHash|$seq|$payloadHash'))
          .toString();

      await _db!.insert('events', {
        'timestamp_ms': ts,
        'type': type,
        'payload': payloadJson,
        'prev_hash': prevHash,
        'row_hash': rowHash,
      });

      _lastSeq = seq;
      _lastHash = rowHash;

      return LedgerEvent(
        seq: seq,
        timestampMs: ts,
        type: type,
        payload: payload,
        prevHash: prevHash,
        rowHash: rowHash,
      );
    } finally {
      mine.complete();
    }
  }

  /// Walk the chain and verify every row's hash. Returns `null` if
  /// the chain is intact, or the sequence number of the first broken
  /// row otherwise. Used by SpeechLab's "Verify ledger" button.
  static Future<int?> verify() async {
    assert(_db != null);
    final rows = await _db!.query('events', orderBy: 'seq ASC');
    var prev = _genesis;
    for (final row in rows) {
      final seq = row['seq'] as int;
      final payloadJson = row['payload'] as String;
      final payloadHash = sha256.convert(utf8.encode(payloadJson)).toString();
      final expected = sha256
          .convert(utf8.encode('$prev|$seq|$payloadHash'))
          .toString();
      if (expected != row['row_hash']) return seq;
      prev = row['row_hash'] as String;
    }
    return null;
  }

  /// Read recent events, newest first. Used by SpeechLab.
  static Future<List<LedgerEvent>> tail({int limit = 50}) async {
    assert(_db != null);
    final rows = await _db!.query('events', orderBy: 'seq DESC', limit: limit);
    return rows.map(_rowToEvent).toList();
  }

  /// Count of events in the ledger. Cheap — O(1) on SQLite.
  static Future<int> count() async {
    assert(_db != null);
    final rows = await _db!.rawQuery('SELECT COUNT(*) AS n FROM events');
    return (rows.first['n'] as int?) ?? 0;
  }

  /// Test-only: wipe the ledger and reset state. Do not call in app code.
  @visibleForTesting
  static Future<void> debugReset() async {
    assert(_db != null);
    await _db!.delete('events');
    _lastSeq = 0;
    _lastHash = _genesis;
  }

  static LedgerEvent _rowToEvent(Map<String, Object?> row) => LedgerEvent(
    seq: row['seq'] as int,
    timestampMs: row['timestamp_ms'] as int,
    type: row['type'] as String,
    payload: jsonDecode(row['payload'] as String) as Map<String, Object?>,
    prevHash: row['prev_hash'] as String,
    rowHash: row['row_hash'] as String,
  );
}
