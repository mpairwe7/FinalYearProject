/// Dart-side mirror of `ml/configs/speech_config.yaml` (2026).
///
/// Kept deliberately small — the Dart layer only needs the ids, budgets
/// and languages. Training hyperparameters and license fields live on
/// the ML side.
library;

class SpeechAudioConfig {
  const SpeechAudioConfig({
    this.sampleRate = 16000,
    this.channels = 1,
    this.chunkMs = 200,
    this.maxUtteranceS = 30,
    this.vadMinSilenceMs = 500,
    this.vadThreshold = 0.5,
  });

  final int sampleRate;
  final int channels;
  final int chunkMs;
  final int maxUtteranceS;
  final int vadMinSilenceMs;
  final double vadThreshold;
}

class SpeechAsrConfig {
  const SpeechAsrConfig({
    this.enComponentId = 'whisper-small-en',
    this.lgComponentId = 'whisper-small-lg-ft',
    this.vadComponentId = 'silero-vad',
    this.primaryLanguage = 'en',
    this.languages = const ['en', 'lg'],
    this.streaming = true,
  });

  final String enComponentId;
  final String lgComponentId;
  final String vadComponentId;
  final String primaryLanguage;
  final List<String> languages;
  final bool streaming;

  String componentFor(String language) {
    if (language == 'lg') return lgComponentId;
    return enComponentId;
  }
}

class SpeechTtsConfig {
  const SpeechTtsConfig({
    this.enComponentId = 'piper-en_US-lessac-medium',
    this.lgComponentId = 'luganda-vits-v1',
    this.streaming = true,
  });

  final String enComponentId;
  final String lgComponentId;
  final bool streaming;

  String componentFor(String language) {
    if (language == 'lg') return lgComponentId;
    return enComponentId;
  }
}

class SpeechMtConfig {
  const SpeechMtConfig({
    this.componentId = 'madlad-student-int8',
    this.directions = const ['en_lg', 'lg_en'],
  });

  final String componentId;
  final List<String> directions;
}

class SpeechPipelineConfig {
  const SpeechPipelineConfig({
    this.audio = const SpeechAudioConfig(),
    this.asr = const SpeechAsrConfig(),
    this.tts = const SpeechTtsConfig(),
    this.mt = const SpeechMtConfig(),
    this.enableTranslationRouting = true,
    this.bargeInEnabled = true,
  });

  final SpeechAudioConfig audio;
  final SpeechAsrConfig asr;
  final SpeechTtsConfig tts;
  final SpeechMtConfig mt;

  /// When true, speech input in one language is translated to the model's
  /// prompt language (en) before hitting the LLM, and the reply is
  /// translated back before TTS.
  final bool enableTranslationRouting;

  /// When true, a new VAD-detected utterance interrupts ongoing TTS
  /// playback (full-duplex conversation).
  final bool bargeInEnabled;
}
