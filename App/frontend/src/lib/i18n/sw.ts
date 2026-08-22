import type { Dictionary } from './en';

/**
 * Swahili (sw).
 *
 * NEEDS A NATIVE-SPEAKER REVIEW BEFORE IT IS TREATED AS FINAL — see the note in
 * lg.ts. Swahili is also regionally variable, and URA's audience is closer to
 * the Tanzanian/Kenyan coastal register than to Congolese Swahili; a reviewer
 * should confirm the tax vocabulary (kodi, ushuru, TIN) reads the way URA's own
 * Swahili material does.
 *
 * Partial on purpose: an untranslated key falls back to English rather than
 * rendering blank.
 */
export const sw: Partial<Dictionary> = {
  'landing.headline': 'Nikusaidie vipi na kodi zako?',
  'landing.subtitle': 'Msaidizi rasmi wa URA unaotumia AI',
  'landing.authPrompt.signIn': 'Ingia',
  'landing.authPrompt.or': 'au',
  'landing.authPrompt.signUp': 'fungua akaunti',
  'landing.authPrompt.tail':
    'ili kuhifadhi mazungumzo na wasifu wako wa kodi — au anza kuuliza tu.',

  'starters.gettingStarted.category': 'Kuanza',
  'starters.gettingStarted.label': 'URA hutoa huduma gani?',
  'starters.registration.category': 'Usajili',
  'starters.registration.label': 'Ninawezaje kujisajili kupata TIN?',
  'starters.rates.category': 'Viwango',
  'starters.rates.label': 'Kiwango cha sasa cha VAT nchini Uganda ni kipi?',
  'starters.filing.category': 'Kuwasilisha',
  'starters.filing.label': 'Ninawezaje kuwasilisha marejesho ya kodi ya mwaka?',

  'composer.placeholder': 'Uliza chochote kuhusu URA...',
  'composer.placeholderVoice': 'Hali ya sauti imewashwa — sema, au andika',
  'composer.label': 'Andika ujumbe wako',
  'composer.send': 'Tuma ujumbe',
  'composer.attach': 'Ambatisha faili',
  'composer.removeAttachment': 'Ondoa kiambatisho',
  'composer.micStart': 'Anza kuzungumza',
  'composer.micStop': 'Acha kusikiliza',
  'composer.voiceEnter': 'Ingia hali ya sauti',
  'composer.voiceExit': 'Toka hali ya sauti',
  'composer.transcribing': 'Ninanukuu',
  'composer.transcribingTip': 'Ninanukuu…',
  'composer.dictate': 'Imba maandishi',
  'composer.stopAndInsert': 'Simamisha na uweke maandishi',
  'composer.recHintVoice': 'Gusa alama ya tiki kutuma, au X kughairi.',
  'composer.recHintDictation':
    'Gusa alama ya tiki kuongeza uliyosema kwenye ujumbe, au X kutupa.',
  'composer.stop': 'Simamisha',
  'composer.analysingTip': 'Ninachambua…',
  'composer.voiceHint':
    'Hali ya sauti: gusa maikrofoni useme, na majibu yatasomwa kwa sauti.',
  'composer.listening': 'Ninasikiliza...',
  'composer.cancelRecording': 'Ghairi kurekodi',
  'composer.sendRecording': 'Tuma rekodi',
  'composer.analysing': 'Ninachambua kiambatisho...',
  'composer.disclaimer':
    'Msaidizi wa URA anaweza kukosea. Thibitisha taarifa muhimu za kodi kwenye ura.go.ug.',

  'rail.newChat': 'Mazungumzo mapya',
  'rail.chats': 'Mazungumzo',
  'rail.empty': 'Mazungumzo yako yataonekana hapa.',
  'rail.viewAll': 'Ona mazungumzo yote',
  'rail.open': 'Fungua historia ya mazungumzo',
  'rail.close': 'Funga kizingiti',
  'rail.search': 'Tafuta mazungumzo',
  'rail.searchPlaceholder': 'Tafuta katika mazungumzo yako',
  'rail.noResults': 'Hakuna mazungumzo yanayolingana na hayo.',
  'rail.pin': 'Bandika',
  'rail.unpin': 'Ondoa bandiko',
  'rail.rename': 'Badilisha jina',
  'rail.delete': 'Futa',

  'account.signIn': 'Ingia',
  'account.signUp': 'Jisajili',
  'account.signOut': 'Toka',
  'account.settings': 'Mipangilio',
  'account.prompt': 'Ingia ili kuhifadhi mazungumzo na wasifu wako.',

  'menu.more': 'Chaguo zaidi',
  'menu.settings': 'Mipangilio',
  'menu.clear': 'Futa mazungumzo',
  'menu.blog': 'Blogu ya mradi',
  'menu.themeAuto': 'Otomatiki',
  'menu.themeLight': 'Angavu',
  'menu.themeDark': 'Giza',

  'language.trigger': 'Lugha',
  'language.title': 'Chagua lugha',
  'language.description':
    'Hubadilisha lugha ya kiolesura na lugha ambayo msaidizi hujibu.',

  'message.you': 'Ulisema',
  'message.assistant': 'Msaidizi alijibu',
  'message.listen': 'Sikiliza',
  'message.listenIn': 'Sikiliza kwa {language}',
  'message.stop': 'Simamisha',
  'message.copy': 'Nakili',
  'message.copied': 'Jibu limenakiliwa',
  'message.sources': 'Vyanzo ({count})',
  'message.helpful': 'Imesaidia',
  'message.notHelpful': 'Haikusaidia',
  'message.thinking': 'Ninatafuta katika hifadhi ya URA…',
  'message.latest': 'Ya hivi punde',

  'common.cancel': 'Ghairi',
  'common.delete': 'Futa',
  'common.clear': 'Futa',
  'common.close': 'Funga',
  'common.retry': 'Jaribu tena',
  'common.offline':
    'Hauko mtandaoni. Ujumbe utatumwa utakapounganishwa tena.',
};
