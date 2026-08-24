/**
 * English — the source of truth for every user-facing string on the taxpayer
 * surface.
 *
 * `lg.ts` and `sw.ts` are `Partial` of this shape, so a key that has not been
 * translated yet renders the English string rather than a blank or a raw key.
 * That is the only safe failure mode for a public tax service: a missing
 * Luganda label should read as English, never as `composer.placeholder`.
 *
 * Adding a string: add it here first. TypeScript will not force the other two
 * dictionaries to follow — deliberately, so a new string can ship in English
 * and be translated afterwards without blocking the build.
 */
export const en = {
  // Landing ---------------------------------------------------------------
  'landing.headline': 'How can I help with your taxes?',
  'landing.subtitle': 'Official AI-powered assistant for Uganda Revenue Authority',
  'landing.authPrompt.signIn': 'Sign in',
  'landing.authPrompt.or': 'or',
  'landing.authPrompt.signUp': 'create an account',
  'landing.authPrompt.tail':
    'to save conversations and keep a tax profile — or just start asking.',

  // Starter prompts. The label is sent to the assistant as the question, so it
  // is translated too: the backend answers Luganda and Swahili natively.
  'starters.gettingStarted.category': 'Getting started',
  'starters.gettingStarted.label': 'What services does URA provide?',
  'starters.registration.category': 'Registration',
  'starters.registration.label': 'How do I register for a TIN?',
  'starters.rates.category': 'Rates',
  'starters.rates.label': 'What is the current VAT rate in Uganda?',
  'starters.filing.category': 'Filing',
  'starters.filing.label': 'How do I file my annual tax returns?',

  // Composer --------------------------------------------------------------
  'composer.placeholder': 'Ask anything about URA...',
  'composer.placeholderVoice': 'Voice mode on — speak, or type',
  'composer.label': 'Type your message',
  'composer.send': 'Send message',
  'composer.attach': 'Attach a file',
  'composer.removeAttachment': 'Remove attachment',
  'composer.micStart': 'Start speaking',
  'composer.micStop': 'Stop listening',
  'composer.voiceEnter': 'Enter voice mode',
  'composer.voiceExit': 'Exit voice mode',
  'composer.micStarting': 'Opening the microphone',
  'composer.micStartingTip': 'Opening the microphone…',
  'composer.transcribing': 'Transcribing',
  'composer.transcribingTip': 'Transcribing…',
  'composer.dictate': 'Dictate',
  'composer.stopAndInsert': 'Stop and insert text',
  'composer.recHintVoice': 'Tap checkmark to send, or X to cancel.',
  'composer.recHintDictation':
    'Tap checkmark to add what you said to the message, or X to discard.',
  'composer.stop': 'Stop',
  'composer.analysingTip': 'Analysing…',
  'composer.voiceHint':
    'Voice mode: tap the mic to speak, and replies are read back to you.',
  'composer.listening': 'Listening...',
  'composer.cancelRecording': 'Cancel recording',
  'composer.sendRecording': 'Send recording',
  'composer.analysing': 'Analysing attachment...',
  'composer.disclaimer':
    'URA Assistant can make mistakes. Verify important tax information at ura.go.ug.',

  // Conversation rail -----------------------------------------------------
  'rail.newChat': 'New chat',
  'rail.chats': 'Chats',
  'rail.empty': 'Your conversations will appear here.',
  'rail.viewAll': 'View all conversations',
  'rail.open': 'Open conversation history',
  'rail.close': 'Close sidebar',
  'rail.search': 'Search conversations',
  'rail.searchPlaceholder': 'Search your conversations',
  'rail.noResults': 'No conversations match that.',
  'rail.pin': 'Pin',
  'rail.unpin': 'Unpin',
  'rail.rename': 'Rename',
  'rail.delete': 'Delete',

  // Account ---------------------------------------------------------------
  'account.signIn': 'Sign in',
  'account.signUp': 'Sign up',
  'account.signOut': 'Sign out',
  'account.settings': 'Settings',
  'account.prompt': 'Sign in to keep your conversations and profile.',

  // Header menu -----------------------------------------------------------
  'menu.more': 'More options',
  'menu.theme': 'Theme: {value}',
  'menu.themeAuto': 'Auto',
  'menu.themeLight': 'Light',
  'menu.themeDark': 'Dark',
  'menu.language': 'Response language: {value}',
  'menu.settings': 'Settings',
  'menu.clear': 'Clear conversation',
  'menu.blog': 'Project blog',

  // Language picker -------------------------------------------------------
  'language.trigger': 'Language',
  'language.title': 'Language selection',
  'language.description':
    'Changes the interface and the language the assistant answers in.',

  // Messages --------------------------------------------------------------
  'message.you': 'You said',
  'message.assistant': 'Assistant replied',
  'message.listen': 'Listen',
  'message.listenIn': 'Listen in {language}',
  'message.stop': 'Stop',
  'message.copy': 'Copy',
  'message.copied': 'Reply copied',
  'message.sources': 'Sources ({count})',
  'message.helpful': 'Helpful',
  'message.notHelpful': 'Not helpful',
  'message.thinking': 'Searching the URA knowledge base…',
  // Turn phases. `phase.translating` is the one that matters most to
  // translate: it only ever shows on a non-English turn, and it is there to
  // explain a wait to someone who has just been shown an English answer.
  'phase.thinking': 'Thinking',
  'phase.searching': 'Searching the URA knowledge base',
  'phase.churning': 'Writing the answer',
  'phase.translating': 'Translating the answer',
  'message.escalated':
    'This has been passed to a URA officer. They will reply here.',
  'message.latest': 'Latest',

  // Human handoff. The taxpayer's own way into the officer queue — every
  // other route into it is a judgement the system makes for them.
  'handoff.ask': 'Talk to a URA officer',
  'handoff.requesting': 'Asking an officer…',
  'handoff.reference': 'Reference',
  'handoff.queued':
    'A URA officer has been asked to look at this. Their reply will appear here.',
  'handoff.failed':
    'This could not be passed to an officer. Call URA toll-free on 0800 117 000.',
  'handoff.offline':
    'You appear to be offline. Try again when you reconnect, or call URA toll-free on 0800 117 000.',

  // Shared ----------------------------------------------------------------
  'common.cancel': 'Cancel',
  'common.delete': 'Delete',
  'common.clear': 'Clear',
  'common.close': 'Close',
  'common.retry': 'Try again',
  'common.offline': 'You are offline. Messages will send when you reconnect.',
} as const;

export type TranslationKey = keyof typeof en;
export type Dictionary = Record<TranslationKey, string>;
