/// URA-specific vocabulary for multilingual ASR hotword biasing and
/// post-transcript fix-up.
///
/// Each language gets its own hotword set combining universal acronyms
/// (URA, TIN, VAT, PAYE, EFRIS — which stay English across all languages)
/// with locale-specific tax vocabulary.
///
/// Keeping each set small (<=40 terms) preserves Whisper's 224-token
/// context window.
library;

class UraDomainGlossary {
  UraDomainGlossary._();

  /// Universal acronyms used across all languages.
  static const List<String> _universalAcronyms = [
    'URA',
    'TIN',
    'VAT',
    'PAYE',
    'EFRIS',
    'ASYCUDA',
    'URSB',
    'NTR',
    'FOB',
    'CIF',
  ];

  /// English tax terms.
  static const List<String> _englishTerms = [
    'Taxpayer Identification Number',
    'withholding tax',
    'excise duty',
    'stamp duty',
    'customs duty',
    'rental income tax',
    'corporation tax',
    'local service tax',
    'customs declaration',
    'bill of lading',
    'import declaration',
    'Malaba',
    'Busia',
    'Katuna',
    'Mutukula',
    'Entebbe',
    'Kampala',
  ];

  /// Luganda tax terms.
  static const List<String> _lugandaTerms = [
    'omusolo',
    'ettaka',
    'ssente',
    'okusasula',
    'enamba',
    'ebintu',
    'omuvuzi',
    'ebyokulya',
    'amateeka',
    'gavumenti',
    'obuwaguzi',
    'entimba',
    'ebisale',
    'risiti',
    'Malaba',
    'Busia',
    'Katuna',
    'Mutukula',
    'Entebbe',
    'Kampala',
  ];

  /// Swahili tax terms.
  static const List<String> _swahiliTerms = [
    'kodi',
    'ushuru',
    'risiti',
    'forodha',
    'malipo',
    'biashara',
    'mapato',
    'serikali',
    'sheria',
    'hesabu',
    'malipa kodi',
    'mfanyabiashara',
    'usajili',
    'Malaba',
    'Busia',
    'Katuna',
    'Mutukula',
    'Entebbe',
    'Kampala',
  ];

  /// Runyankole tax terms.
  static const List<String> _runyankoleTerms = [
    'omushoro',
    'obugyenyi',
    'ebikorwa',
    'amashuumi',
    'okuriha',
    'ensiimbi',
    'ebyokuza',
    'amateeka',
    'Malaba',
    'Busia',
    'Katuna',
    'Mutukula',
    'Entebbe',
    'Kampala',
  ];

  /// Acholi tax terms.
  static const List<String> _acholiTerms = [
    'cul',
    'jami',
    'gamente',
    'cente',
    'tic',
    'culo kwor',
    'ngat ma cul',
    'cobo',
    'ngec',
    'Malaba',
    'Busia',
    'Katuna',
    'Mutukula',
    'Entebbe',
    'Kampala',
  ];

  static const _localeTerms = <String, List<String>>{
    'en': _englishTerms,
    'lg': _lugandaTerms,
    'sw': _swahiliTerms,
    'nyn': _runyankoleTerms,
    'ach': _acholiTerms,
  };

  /// Return hotwords for a specific locale. Always includes universal
  /// acronyms + locale-specific tax vocabulary.
  static List<String> hotwordsForLocale(String locale) {
    return [..._universalAcronyms, ...(_localeTerms[locale] ?? _englishTerms)];
  }

  /// Default hotwords (English) — backwards compatible.
  static List<String> get hotwords => hotwordsForLocale('en');

  /// Pre-built initial-prompt string for a given locale.
  static String initialPromptForLocale(String locale) {
    final words = hotwordsForLocale(locale);
    return 'Uganda Revenue Authority glossary: ${words.join(', ')}.';
  }

  /// Default initial prompt (English).
  static String get initialPrompt => initialPromptForLocale('en');

  /// Regex-driven cleanup for predictable ASR mistakes. Applied
  /// regardless of locale since acronyms are universal.
  static final List<(RegExp, String)> postFixups = [
    (RegExp(r'\bp\.?\s*a\.?\s*y\.?\s*e\.?\b', caseSensitive: false), 'PAYE'),
    (RegExp(r'\bv\.?\s*a\.?\s*t\.?\b', caseSensitive: false), 'VAT'),
    (RegExp(r'\bt\.?\s*i\.?\s*n\.?\b', caseSensitive: false), 'TIN'),
    (
      RegExp(r'\be\.?\s*f\.?\s*r\.?\s*i\.?\s*s\.?\b', caseSensitive: false),
      'EFRIS',
    ),
    (RegExp(r'\bu\.?\s*r\.?\s*a\.?\b', caseSensitive: false), 'URA'),
    (RegExp(r'\bten\s+number\b', caseSensitive: false), 'TIN number'),
    (RegExp(r'\bfor\s+ross\b', caseSensitive: false), 'EFRIS'),
    (RegExp(r'\s{2,}'), ' '),
  ];

  /// Apply all [postFixups] to a raw transcript.
  static String normalise(String raw) {
    var out = raw;
    for (final (pattern, replacement) in postFixups) {
      out = out.replaceAll(pattern, replacement);
    }
    return out.trim();
  }
}
