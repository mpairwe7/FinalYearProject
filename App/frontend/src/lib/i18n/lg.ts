import type { Dictionary } from './en';

/**
 * Luganda (lg).
 *
 * NEEDS A NATIVE-SPEAKER REVIEW BEFORE IT IS TREATED AS FINAL. These strings
 * were written by an English speaker working from dictionaries, and this is a
 * public revenue service: a label that is merely understandable is not the same
 * as one a Luganda speaker would write, and tax vocabulary in particular
 * (omusolo, TIN, okuwandiisa) carries specific meaning at URA that a general
 * translation can miss.
 *
 * The type is Partial on purpose. Anything not listed here falls back to the
 * English string in en.ts, which is a worse experience but never a broken one.
 * Removing a doubtful line is therefore always safe.
 */
export const lg: Partial<Dictionary> = {
  'landing.headline': 'Nkuyambe ntya ku misolo gyo?',
  'landing.subtitle': 'Omuyambi wa URA akozesa AI',
  'chat.greeting': 'Oli otya! Nsobola okukuyamba ku bibuuzo byo ebikwata ku URA. Wandiika oba yogera ekibuuzo kyo okutandika.',
  'landing.authPrompt.signIn': 'Yingira',
  'landing.authPrompt.or': 'oba',
  'landing.authPrompt.signUp': 'tondawo akawunti',
  'landing.authPrompt.tail':
    'okutereka emboozi zo n’okukuuma profayiro y’omusolo — oba tandika kubuuza.',

  'starters.gettingStarted.category': 'Okutandika',
  'starters.gettingStarted.label': 'Bikolwa ki URA by’ewa?',
  'starters.registration.category': 'Okwewandiisa',
  'starters.registration.label': 'Nnyinza ntya okwewandiisa TIN?',
  'starters.rates.category': 'Emiwendo',
  'starters.rates.label': 'Omuwendo gwa VAT mu Uganda guli gutya kati?',
  'starters.filing.category': 'Okuwaayo',
  'starters.filing.label': 'Nnyinza ntya okuwaayo emisolo gya buli mwaka?',

  'composer.placeholder': 'Buuza ekintu kyonna ku URA...',
  'composer.placeholderVoice': 'Eddoboozi likola — yogera, oba wandiika',
  'composer.label': 'Wandiika obubaka bwo',
  'composer.send': 'Sindika obubaka',
  'composer.attach': 'Gattako fayiro',
  'composer.removeAttachment': 'Ggyawo fayiro',
  'composer.micStart': 'Tandika okwogera',
  'composer.micStop': 'Lekera awo okuwuliriza',
  'composer.voiceEnter': 'Yingira mu ddoboozi',
  'composer.voiceExit': 'Va mu ddoboozi',
  'composer.micStarting': 'Nzigula maykolofoni',
  'composer.micStartingTip': 'Nzigula maykolofoni…',
  'composer.transcribing': 'Nkyusa mu bigambo',
  'composer.transcribingTip': 'Nkyusa mu bigambo…',
  'composer.dictate': 'Yogera owandiike',
  'composer.stopAndInsert': 'Yimirira oteeke ebigambo',
  'composer.recHintVoice': 'Nyiga akakwe osindike, oba X osazeemu.',
  'composer.recHintDictation':
    'Nyiga akakwe oteeke by’oyogedde mu bubaka, oba X obisuule.',
  'composer.stop': 'Yimirira',
  'composer.analysingTip': 'Nkebera…',
  'composer.voiceHint':
    'Eddoboozi: nyiga akalobo oyogere, era ebiddibwamu bijja kusomerwa.',
  'composer.listening': 'Mpuliriza...',
  'composer.cancelRecording': 'Sazaamu okukwata eddoboozi',
  'composer.sendRecording': 'Sindika eddoboozi',
  'composer.analysing': 'Nkebera fayiro...',
  'composer.disclaimer':
    'Omuyambi wa URA ayinza okukyamu. Kakasa amawulire g’omusolo ku ura.go.ug.',

  'rail.newChat': 'Emboozi empya',
  'rail.chats': 'Emboozi',
  'rail.empty': 'Emboozi zo zijja kulabika wano.',
  'rail.viewAll': 'Laba emboozi zonna',
  'rail.open': 'Bikkula emboozi ezaayita',
  'rail.close': 'Ggalawo olukalala',
  'rail.search': 'Noonya emboozi',
  'rail.searchPlaceholder': 'Noonya mu mboozi zo',
  'rail.noResults': 'Tewali mboozi ezikwatagana n’ekyo.',
  'rail.pin': 'Nyweza',
  'rail.unpin': 'Ggyako okunyweza',
  'rail.rename': 'Kyusa erinnya',
  'rail.delete': 'Gyawo',

  'account.signIn': 'Yingira',
  'account.signUp': 'Weewandiise',
  'account.signOut': 'Fuluma',
  'account.settings': 'Enteekateeka',
  'account.prompt': 'Yingira okukuuma emboozi zo ne profayiro yo.',

  'menu.more': 'Ebirala',
  'menu.settings': 'Enteekateeka',
  'menu.clear': 'Sangula emboozi',
  'menu.blog': 'Blogu y’omulimu',
  'menu.themeAuto': 'Yeekolera',
  'menu.themeLight': 'Ekitangaala',
  'menu.themeDark': 'Ekizikiza',

  'language.trigger': 'Olulimi',
  'language.title': 'Okulonda olulimi',
  'language.description':
    'Kikyusa olulimi lw’ekifaananyi n’olulimi omuyambi lw’addamu.',

  'message.you': 'Ogambye',
  'message.assistant': 'Omuyambi addamu',
  'message.listen': 'Wuliriza',
  'message.listenIn': 'Wuliriza mu {language}',
  'message.stop': 'Yimirira',
  'message.copy': 'Koppa',
  'message.copied': 'Ekiddibwamu kikoppeddwa',
  'message.sources': 'Ensibuko ({count})',
  'message.helpful': 'Kiyambye',
  'message.notHelpful': 'Tekiyambye',
  'message.thinking': 'Nnoonya mu tterekero lya URA…',
  'phase.thinking': 'Nlowooza',
  'phase.searching': 'Nnoonya mu tterekero lya URA',
  'phase.churning': 'Mpandiika eky’okuddamu',
  'phase.translating': 'Nvvuunula eky’okuddamu',
  'message.latest': 'Ekisembayo',

  'handoff.ask': 'Yogera n’omukozi wa URA',
  'handoff.requesting': 'Nsaba omukozi…',
  'handoff.reference': 'Namba y’ekiwandiiko',
  'handoff.queued':
    'Omukozi wa URA asabiddwa okutunuulira kino. Eky’okuddamu kijja kulabika wano.',
  'handoff.failed':
    'Kino tekisobose kutuusibwa ku mukozi. Kuba essimu ya bwereere ku 0800 117 000.',
  'handoff.offline':
    'Olabika toli ku yintaneeti. Gezaako nate bw’oddamu okuyingira, oba kuba 0800 117 000.',

  'common.cancel': 'Sazaamu',
  'common.delete': 'Gyawo',
  'common.clear': 'Sangula',
  'common.close': 'Ggalawo',
  'common.retry': 'Ddamu ogezeeko',
  'common.offline':
    'Toli ku yintaneeti. Obubaka bujja kusindikibwa bw’oddamu okukwatagana.',
  'common.low_bandwidth': 'Yintaneeti eri wansi. Enkozesa ya data ekendeddwa.',
  'common.offline_notice': 'Tolina yintaneeti kaakano. Eby\'emisolo eby\'amangu n\'ebibalirwako bikola.',
  'common.offline_calculator': 'Ekibala Emisolo',

  // Documents & Extraction ------------------------------------------------
  'documents.inspect': 'Kebera',
  'documents.downloadReport': 'Funa Alipoota',
  'documents.reconciliation': 'Okubalirira Omusolo',
  'documents.verified': 'Kikakasiddwa',
  'documents.discrepancy': 'Enjawukana ezuliddwa',
  'documents.modalTitle': "Okwekebejja Ekiwandiiko n'Omusolo",
  'documents.summary': "Ekinnyonnyolo ky'Ekiwandiiko",
  'documents.entities': "Ebibuuzo n'Ensimbi Ezuliddwa",
  'documents.tables': "Emmeeza n'Ebikolwa",
  'documents.suggested': "Ebirowoozo eby'Okukola",

  // Call URA receptionist
  'call.button': 'Kuba Essimu ku URA',
  'call.dialing': 'Kukuba 0800 117 000…',
  'call.assistantName': 'Omuyambi ow’ekikugu owa URA',
  'call.connected': 'Kukwataganye',
  'call.mute': 'Sirisa eddoboozi',
  'call.unmute': 'Zaayo eddoboozi',
  'call.talkOfficer': 'Yogera n’omukozi wa URA',
  'call.hangup': 'Mala essimu',
  'call.transferring': 'Kukukwataganya n’omukozi wa URA…',
  'call.onHold': 'Nsaba olindeko',
  'call.reconnecting': 'Tukyakukwataganya n’omukozi…',
  'call.officerJoined': 'Omukozi {name} ayingidde',
  'call.officerSpeaking': 'Omukozi ayogera',
  'call.ended': 'Essimu ewedde',
  'call.thankYou': 'Weebale kukuba ku URA.',
  'call.preTitle': 'Kuba Essimu ku URA',
  'call.preSubtitle': '0800 117 000 · Tekusasulwa',
  'call.preConsent': 'Essimu ziwandiikibwa omukozi asobole okuddamu ng’amanyi byonna ebyogeddwa.',
  'call.jumpToLatest': 'Genda ku bisembyeyo',
  'call.bridgeActive': 'Eddoboozi likwataganyiziddwa butereevu',
  'call.consentTitle': 'Olukusa lw’okukwata eddoboozi',
  'call.consentText': 'Essimu eno ejja kukwatibwa n’okuwandiikibwa okusobola okuyamba abakozi ba URA.',
  'call.consentAccept': 'Kkiriza & Kuba',
  'call.consentDecline': 'Gaana',
  'call.you': 'Ggwe',
  'call.officer': 'Omukozi',
  'call.systemNote': 'Sisitemu',
  'call.transcriptLabel': 'Ebiwandiikiddwa mu ssimu',
  'call.connecting': 'Tukwataganya essimu…',
  'call.listening': 'Tuwuliriza… buuza ekibuuzo kyo ku musolo.',
  'call.listeningHint': 'Bye woyogera bijja kulabika wano nga okyayogera.',
  'call.mutedHint': 'Eddoboozi lisiriziddwa — lizzeeyo osobole okweyongera okwogera.',
  'call.languageAuto': '{language} · auto',
  'call.languageChosen': '{language}',
  'call.languageMenu': 'Olulimi lw’essimu',
  'call.languageMenuHint': 'Londa olulimi essimu yonna mw’eneebeera.',
  'call.languageSwitched': 'Kati twogera {language}',
  'call.lang.en': 'Lungereza',
  'call.lang.lg': 'Luganda',
  'call.lang.sw': 'Kiswahili',
};
