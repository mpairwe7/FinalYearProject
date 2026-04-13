/// Lightweight WER/CER computation in Dart.
///
/// Used by the SpeechLab eval screen to compute on-device accuracy
/// metrics against the bundled gold evaluation set.
library;

import 'dart:math' as math;

/// Word Error Rate between a reference and hypothesis transcript.
/// Returns a value in [0, ∞) — 0 means perfect, 1.0 means 100% error.
double wordErrorRate(String reference, String hypothesis) {
  final ref = _tokenise(reference);
  final hyp = _tokenise(hypothesis);
  if (ref.isEmpty) return hyp.isEmpty ? 0 : 1;
  final dist = _editDistance(ref, hyp);
  return dist / ref.length;
}

/// Character Error Rate — same metric at the character level.
double charErrorRate(String reference, String hypothesis) {
  final ref = reference.toLowerCase().replaceAll(RegExp(r'\s+'), '').split('');
  final hyp = hypothesis.toLowerCase().replaceAll(RegExp(r'\s+'), '').split('');
  if (ref.isEmpty) return hyp.isEmpty ? 0 : 1;
  final dist = _editDistance(ref, hyp);
  return dist / ref.length;
}

List<String> _tokenise(String text) => text
    .toLowerCase()
    .split(RegExp(r'\s+'))
    .where((w) => w.isNotEmpty)
    .toList();

/// Standard Levenshtein edit distance over two lists of tokens.
int _editDistance(List<String> a, List<String> b) {
  final n = a.length;
  final m = b.length;

  // Space-optimised: two rows.
  var prev = List.generate(m + 1, (j) => j);
  var curr = List<int>.filled(m + 1, 0);

  for (var i = 1; i <= n; i++) {
    curr[0] = i;
    for (var j = 1; j <= m; j++) {
      final cost = a[i - 1] == b[j - 1] ? 0 : 1;
      curr[j] = [
        prev[j] + 1, // deletion
        curr[j - 1] + 1, // insertion
        prev[j - 1] + cost, // substitution
      ].reduce(math.min);
    }
    final tmp = prev;
    prev = curr;
    curr = tmp;
  }
  return prev[m];
}
