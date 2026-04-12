/// ASR post-processor: domain glossary fix-ups + digit normalisation.
///
/// Runs after every transcription to correct predictable ASR errors
/// (URA jargon, spaced-out acronyms) and normalise digit groups.
/// The [UraDomainGlossary.normalise] handles the domain-specific
/// part; this module adds digit and whitespace normalisation.
library;

import '../domain_glossary.dart';
import '../lid.dart';

/// Full post-processing pipeline for an ASR transcript.
class AsrPostProcessor {
  const AsrPostProcessor({
    LanguageIdentifier? lid,
  }) : _lid = lid;

  final LanguageIdentifier? _lid;

  /// Run all post-processing steps and return the cleaned transcript
  /// plus optional LID result.
  PostProcessResult process(String raw, {String? declaredLocale}) {
    // 1. Domain glossary fix-ups (URA-specific).
    var text = UraDomainGlossary.normalise(raw);

    // 2. Digit normalisation: collapse spaces in digit sequences.
    //    "1 2 3 4 5 6 7 8" → "12345678" (TIN numbers).
    text = _normaliseDigits(text);

    // 3. Trim excess whitespace.
    text = text.replaceAll(RegExp(r'\s{2,}'), ' ').trim();

    // 4. Language identification if a detector is provided.
    final lid = _lid?.identify(text);

    return PostProcessResult(
      text: text,
      lid: lid,
    );
  }

  /// Collapse sequences of single digits separated by spaces into
  /// a single number string. Useful for TIN numbers that ASR often
  /// spaces out: "1 0 0 2 3 4 5 6 7 8" → "1002345678".
  static String _normaliseDigits(String text) {
    // Match 3+ single digits separated by spaces.
    return text.replaceAllMapped(
      RegExp(r'(?<!\d)(\d(?:\s\d){2,})(?!\d)'),
      (m) => m.group(0)!.replaceAll(' ', ''),
    );
  }
}

class PostProcessResult {
  const PostProcessResult({
    required this.text,
    this.lid,
  });

  final String text;
  final LidResult? lid;
}
