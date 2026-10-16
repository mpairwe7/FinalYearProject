"""Everything the receptionist says itself, in English, Luganda and Swahili.

Answers come from the RAG brain; this table is the rest — the greeting,
fillers while an answer is fetched, clarification prompts, the transfer and
timeout lines, and the confirmation spoken when the call changes language.
``brain.py`` and ``clarify.py`` read from it by ``room.state.locale``.

REVIEW STATUS: the Luganda and Swahili strings are drafts. They were written
from the project's existing, reviewed copy where it existed (``glossary.py``,
the ``lib/i18n`` dictionaries, ``text_signals`` courtesy replies) and have
**not yet been checked by a native speaker**. Do not demo them before that
review — see ``docs/runbooks/voice-receptionist-demo.md``. A missing key in a
language falls back to English rather than failing a call, and
``test_receptionist_phrases.py`` fails the build if any key is missing.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache

#: Languages whose strings still need a native speaker's sign-off.
UNREVIEWED: frozenset[str] = frozenset({"lg", "sw", "nyn", "ach"})

#: Lines shown on the caller's screen and never voiced: numbers a caller must
#: keep, which Orpheus reads badly (it said "999 or 112" as "nine nine or one
#: twelve"; see speech_normalization for the phone numbers it loops on).
#: Spoken lines give an emergency number in words, the screen in digits.
UNSPOKEN: frozenset[str] = frozenset(
    {"crisis_screen", "reference_screen", "tollfree_screen", "officer_hours_screen"}
)

_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        # The agreed line (docs/plans/multilingual-receptionist.md §1), plus
        # what a caller must hear first on an AI-answered line: that it is an
        # AI, and that a person is one request away.
        "greeting": (
            "Hi, thanks for contacting URA. I'm your AI assistant today. Ask your question "
            "in your preferred language — English, Luganda, Swahili, Runyankole or Acholi — and "
            "I'll give you the answer in that language. You can ask for an officer at any time. "
            "How can I help you today?"
        ),
        "more_detail": "Would you like more detail?",
        "timeout_transfer": (
            "I'm sorry, checking the database is taking longer than expected. "
            "Let me connect you to an officer."
        ),
        "error_transfer": (
            "I encountered an error looking up that tax information. "
            "Let me connect you to an officer."
        ),
        "transfer": "I'm connecting you to a URA officer, please hold.",
        "queue_disabled": (
            "I can't transfer you right now. Please call URA's toll-free line during working hours; "
            "the number is on your screen."
        ),
        "tollfree_screen": "URA toll-free line: 0800 117 000",
        # While a caller waits for an officer, and when nobody answers in time.
        "still_holding": "Thank you for holding. An officer will be with you shortly.",
        "officers_busy": (
            "All our officers are busy, so an officer will call you back. "
            "Meanwhile, I can still help with other questions."
        ),
        # Nobody is signed in to take the call (G180): said instead of a hold for nobody.
        "officers_unavailable": (
            "No officer is available to take your call right now, so an officer will call you back. "
            "Meanwhile, I can still help with other questions."
        ),
        # Outside the officers' working hours (G195): no hold, a callback when they return.
        "officers_off_duty": (
            "Our officers are off duty at this hour, so an officer will call you back when they "
            "are next on duty. Meanwhile, I can still help with other questions."
        ),
        "officer_hours_screen": "Officers' hours: {hours}",
        # The waiting caller asked to be called back instead (G186).
        "callback_confirmed": (
            "Of course. An officer will call you back; your reference is on your screen. "
            "Meanwhile, I can still help with other questions."
        ),
        "queue_next": "You're next in line.",
        # While an officer has put the caller on hold (G187).
        "officer_hold_update": "Thank you for holding. Your officer will be back with you shortly.",
        "reference_screen": "Your reference: {ref}",
        "clarify_term": "Excuse me, did you say {term}?",
        "clarify_confirm": "So if I got it right, you're asking about {highlight} — is that right?",
        "clarify_repeat_after": "Sorry, I didn't catch the word after '{prev}' — could you say it again?",
        "clarify_repeat": "Sorry, I didn't catch that — could you say it again?",
        "clarify_restart": "Sorry about that — please tell me your question again.",
        "switched": "Sure — let's continue in English.",
        "officer_offer": "Would you like to speak to an officer about this?",
        # A caller who speaks of ending their life (text_signals.detect_crisis):
        # the same help as the chat's crisis_support_reply, and the same
        # numbers, checked on CRISIS_LINES_VERIFIED_ON.
        "crisis_support": (
            "I'm really sorry you're feeling this way. Your safety matters more than any tax matter. "
            "If you are in danger right now, please call nine nine nine, or one one two. "
            "The number for free counselling is on your screen."
        ),
        "crisis_offer": "Would you like me to connect you to a URA officer as well?",
        "crisis_declined": (
            "That's okay. The numbers are on your screen whenever you need them. "
            "I'm here if there's anything else."
        ),
        "crisis_screen": (
            "Emergency: 999 or 112. Free counselling, Mental Health Uganda: 0800 21 21 21 "
            "(Monday to Friday, 8:30am to 5pm)."
        ),
        "crisis_waiting": "I've asked for an officer to come to you first. Please stay on the line.",
        # Call Desk: an officer joins the call, holds it, reconnects, and ends it.
        "officer_joining": "You're now connected to {name}.",
        "officer_reconnecting": "Please hold, I'm reconnecting you.",
        "on_hold": "Please hold.",
        "officer_closing": "Thank you for calling URA. Goodbye.",
        # A caller who has gone quiet: checked on, then the call is ended.
        "idle_check": "Are you still there? I'm here whenever you're ready to ask your question.",
        # "Speak slower": the local voices have no speed control, so the call
        # pauses between sentences — say so rather than promise a new speed.
        "slow_ack": "Of course. I'll slow down, with a pause between points. What is your question?",
        "offer_declined": "No problem. What else can I help you with?",
        "idle_goodbye": "I haven't heard anything, so I'll end the call now. Thank you for calling URA. Goodbye.",
        # The caller's review of the AI (review.py): asked once, after an answer.
        "review_ask_end": "Before you go, was this call helpful to you?",
        "review_thanks_end": "Thank you for your feedback. Goodbye.",
        "review_ask_transfer": "I'm connecting you to a URA officer now. While you wait, were my answers helpful so far?",
        "review_thanks_transfer": "Thank you. Please stay on the line; an officer will be with you shortly.",
        # A spoken confirmation (voice_agentic_workflows): asked again when the yes was unclear;
        # "done" only when the action's executor reported success, never otherwise.
        "confirm_reprompt": "Please confirm clearly once more: should I proceed with this action?",
        "confirm_done": "Thank you. That is confirmed and done.",
        "confirm_not_done": (
            "I'm sorry, I couldn't complete that on this call. "
            "Would you like to speak to an officer about it?"
        ),
    },
    "lg": {
        "greeting": (
            "Gyebale ko, weebale okutuukirira URA. Nze muyambi wo owa AI leero. Buuza ekibuuzo kyo "
            "mu lulimi lw'oyagala — Lungereza, Luganda, Kiswahili, Lunyankole oba Lwacholi — nange nja kukuddamu "
            "mu lulimi olwo. Osobola okusaba omukozi wa URA essaawa yonna. "
            "Nnyinza kukuyamba ntya leero?"
        ),
        "more_detail": "Oyagala okumanya ebisingawo?",
        "timeout_transfer": (
            "Nsonyiwa, okunoonya kutwala ekiseera kiwanvu okusinga bwe kyandibadde. "
            "Ka nkukwataganye n'omukozi wa URA."
        ),
        "error_transfer": (
            "Nsonyiwa, waliwo ekizibu mu kunoonya ebikwata ku musolo ogwo. "
            "Ka nkukwataganye n'omukozi wa URA."
        ),
        "transfer": "Nkukwataganya n'omukozi wa URA, nsaba olindeko.",
        "queue_disabled": (
            "Kati sisobola kukukwataganya n'omukozi. Nsaba okube ku layini ya URA ey'obwereere "
            "mu ssaawa z'okukola; namba eri ku ssirini yo."
        ),
        "tollfree_screen": "Layini ya URA ey'obwereere: 0800 117 000",
        "still_holding": "Webale okulinda. Omukozi wa URA ajja kukwogerako mu kaseera katono.",
        "officers_busy": (
            "Abakozi baffe bonna balina emirimu, kale omukozi ajja kukukubira essimu. "
            "Mu kiseera kino, nkyasobola okukuyamba ku bibuuzo ebirala."
        ),
        "officers_unavailable": (
            "Kati tewali mukozi asobola kukwogerako, kale omukozi ajja kukukubira essimu. "
            "Mu kiseera kino, nkyasobola okukuyamba ku bibuuzo ebirala."
        ),
        "officers_off_duty": (
            "Abakozi baffe tebali ku mulimu mu kiseera kino, kale omukozi ajja kukukubira essimu "
            "nga bakomyewo ku mulimu. Mu kiseera kino, nkyasobola okukuyamba ku bibuuzo ebirala."
        ),
        "officer_hours_screen": "Essaawa z'abakozi: {hours}",
        "callback_confirmed": (
            "Kale. Omukozi ajja kukukubira essimu; namba yo ey'okujuliza eri ku ssirini yo. "
            "Mu kiseera kino, nkyasobola okukuyamba ku bibuuzo ebirala."
        ),
        "queue_next": "Ggwe addako.",
        "officer_hold_update": "Webale okulinda. Omukozi wo ajja kudda gy'oli mangu.",
        "reference_screen": "Namba yo ey'okujuliza: {ref}",
        "clarify_term": "Nsonyiwa, ogambye {term}?",
        "clarify_confirm": "Kale, obuuza ku {highlight} — kituufu?",
        "clarify_repeat_after": "Nsonyiwa, ekigambo ekiddirira '{prev}' sikiwulidde — oyinza okukiddamu?",
        "clarify_repeat": "Nsonyiwa, sikuwulidde bulungi — oyinza okuddamu?",
        "clarify_restart": "Nsonyiwa ku ekyo — nsaba oddemu ekibuuzo kyo.",
        "switched": "Kale — tweyongere mu Luganda.",
        "officer_offer": "Wandiyagadde okwogera n'omukozi ku nsonga eno?",
        "crisis_support": (
            "Nsonyiwa nnyo olw'engeri gy'owuliramu. Obulamu bwo bukulu okusinga ensonga yonna "
            "ey'omusolo. Bw'oba ng'oli mu kabi kati, nsaba okube ku mwenda mwenda mwenda, "
            "oba emu emu bbiri. Namba ey'okuweebwa amagezi ku bwereere eri ku ssirini yo."
        ),
        "crisis_offer": "Wandiyagadde era nkukwataganye n'omukozi wa URA?",
        "crisis_declined": (
            "Kale. Namba ziri ku ssirini yo buli lw'onoozeetaaga. Ndi wano bw'oba olina ekirala."
        ),
        "crisis_screen": (
            "Obuyambi obw'amangu: 999 oba 112. Okuweebwa amagezi ku bwereere, Mental Health "
            "Uganda: 0800 21 21 21 (Balaza okutuuka ku Lwakutaano, 8:30–17:00)."
        ),
        "crisis_waiting": "Nsabye omukozi wa URA akusooke. Sigala ku ssimu; omukozi ajja kukwogerako mangu.",
        "officer_joining": "Kati oyogera ne {name}.",
        "officer_reconnecting": "Nsaba olindeko, nkyakukwataganya n'omukozi.",
        "on_hold": "Nsaba olindeko.",
        "officer_closing": "Webale okukuba essimu eri URA. Weeraba.",
        "idle_check": "Okyaliwo? Ndi wano, buuza ekibuuzo kyo bw'oba weetegese.",
        "slow_ack": "Kale. Nja kwogera mpola, nga nnyimiriramu wakati. Ekibuuzo kyo kiri kki?",
        "offer_declined": "Kale. Kiki ekirala kye nnyinza okukuyambako?",
        "idle_goodbye": "Siwulidde kintu kyonna, kale nkomya essimu kati. Webale okukuba essimu eri URA. Weeraba.",
        "review_ask_end": "Nga tonnagenda, essimu eno ekuyambye?",
        "review_thanks_end": "Webale okutugamba. Weeraba.",
        "review_ask_transfer": "Kati nkuyunga ku mukozi wa URA. Nga olindirira, by'okuddamu byange bikuyambye?",
        "review_thanks_transfer": "Webale. Sigala ku ssimu; omukozi ajja kukwogerako mangu.",
        "confirm_reprompt": "Nsaba oddemu okukakasa nga oyogera bulungi: oyagala tukomekkereze ekikolwa kino?",
        "confirm_done": "Webale, ekikolwa kikakasiddwa era kiwedde.",
        "confirm_not_done": (
            "Nsonyiwa, ekyo sisobodde kukimaliriza ku ssimu eno. "
            "Wandiyagadde okwogera n'omukozi ku nsonga eno?"
        ),
    },
    "sw": {
        "greeting": (
            "Habari, asante kwa kuwasiliana na URA. Mimi ni msaidizi wako wa AI leo. Uliza swali "
            "lako kwa lugha unayopendelea — Kiingereza, Luganda, Kiswahili, Kinyankole au Kiacholi — nami "
            "nitakujibu kwa lugha hiyo. Unaweza kuomba afisa wakati wowote. Nikusaidie vipi leo?"
        ),
        "more_detail": "Ungependa maelezo zaidi?",
        "timeout_transfer": (
            "Samahani, kutafuta kunachukua muda mrefu kuliko kawaida. "
            "Nitakuunganisha na afisa wa URA."
        ),
        "error_transfer": (
            "Samahani, kumetokea hitilafu katika kutafuta taarifa hiyo ya kodi. "
            "Nitakuunganisha na afisa wa URA."
        ),
        "transfer": "Ninakuunganisha na afisa wa URA, tafadhali subiri.",
        "queue_disabled": (
            "Siwezi kukuunganisha sasa hivi. Tafadhali piga simu ya bure ya URA wakati wa saa za kazi; "
            "nambari iko kwenye skrini yako."
        ),
        "tollfree_screen": "Simu ya bure ya URA: 0800 117 000",
        "still_holding": "Asante kwa kusubiri. Afisa atakuhudumia hivi punde.",
        "officers_busy": (
            "Maafisa wetu wote wana shughuli, kwa hiyo afisa atakupigia simu. "
            "Kwa sasa, bado ninaweza kukusaidia na maswali mengine."
        ),
        "officers_unavailable": (
            "Hakuna afisa anayepatikana kupokea simu yako sasa hivi, kwa hiyo afisa atakupigia simu. "
            "Kwa sasa, bado ninaweza kukusaidia na maswali mengine."
        ),
        "officers_off_duty": (
            "Maafisa wetu hawako kazini kwa saa hii, kwa hiyo afisa atakupigia simu watakaporudi "
            "kazini. Kwa sasa, bado ninaweza kukusaidia na maswali mengine."
        ),
        "officer_hours_screen": "Saa za maafisa: {hours}",
        "callback_confirmed": (
            "Sawa. Afisa atakupigia simu; nambari yako ya kumbukumbu iko kwenye skrini yako. "
            "Kwa sasa, bado ninaweza kukusaidia na maswali mengine."
        ),
        "queue_next": "Wewe ndiye unayefuata.",
        "officer_hold_update": "Asante kwa kusubiri. Afisa wako atarudi kwako hivi punde.",
        "reference_screen": "Nambari yako ya kumbukumbu: {ref}",
        "clarify_term": "Samahani, ulisema {term}?",
        "clarify_confirm": "Kwa hiyo, kama nimeelewa vizuri, unauliza kuhusu {highlight} — ni sahihi?",
        "clarify_repeat_after": "Samahani, sikusikia neno baada ya '{prev}' — unaweza kulirudia?",
        "clarify_repeat": "Samahani, sikusikia vizuri — unaweza kurudia?",
        "clarify_restart": "Samahani kwa hilo — tafadhali niambie swali lako tena.",
        "switched": "Sawa — tuendelee kwa Kiswahili.",
        "officer_offer": "Je, ungependa kuzungumza na afisa kuhusu hili?",
        "crisis_support": (
            "Samahani sana kwamba unajisikia hivi. Usalama wako ni muhimu kuliko jambo lolote la kodi. "
            "Ikiwa uko hatarini sasa hivi, tafadhali piga tisa tisa tisa, au moja moja mbili. "
            "Nambari ya ushauri nasaha bila malipo iko kwenye skrini yako."
        ),
        "crisis_offer": "Je, ungependa pia nikuunganishe na afisa wa URA?",
        "crisis_declined": (
            "Sawa. Nambari ziko kwenye skrini yako wakati wowote utakapozihitaji. "
            "Niko hapa ikiwa kuna jambo lingine."
        ),
        "crisis_screen": (
            "Dharura: 999 au 112. Ushauri nasaha bila malipo, Mental Health Uganda: 0800 21 21 21 "
            "(Jumatatu hadi Ijumaa, 8:30–17:00)."
        ),
        "crisis_waiting": "Nimeomba afisa wa URA akuhudumie kwanza. Tafadhali endelea kusubiri; afisa atakuhudumia hivi punde.",
        "officer_joining": "Sasa umeunganishwa na {name}.",
        "officer_reconnecting": "Tafadhali subiri, ninaendelea kukuunganisha na afisa.",
        "on_hold": "Tafadhali subiri.",
        "officer_closing": "Asante kwa kupiga simu URA. Kwaheri.",
        "idle_check": "Bado uko kwenye simu? Niko hapa, uliza swali lako ukiwa tayari.",
        "slow_ack": "Sawa. Nitaongea polepole, nikipumzika kati ya hoja. Una swali gani?",
        "offer_declined": "Sawa. Nikusaidie nini kingine?",
        "idle_goodbye": "Sijasikia chochote, kwa hiyo nitakata simu sasa. Asante kwa kupiga simu URA. Kwaheri.",
        "review_ask_end": "Kabla hujaondoka, je, simu hii imekusaidia?",
        "review_thanks_end": "Asante kwa maoni yako. Kwaheri.",
        "review_ask_transfer": "Ninakuunganisha na afisa wa URA sasa. Unaposubiri, je, majibu yangu yamekusaidia hadi sasa?",
        "review_thanks_transfer": "Asante. Tafadhali endelea kusubiri; afisa atakuhudumia hivi punde.",
        "confirm_reprompt": "Tafadhali thibitisha tena kwa sauti wazi: je, tuendelee na hatua hii?",
        "confirm_done": "Asante, hatua imethibitishwa na kukamilika.",
        "confirm_not_done": (
            "Samahani, sikuweza kukamilisha hilo kwenye simu hii. "
            "Je, ungependa kuzungumza na afisa kuhusu hili?"
        ),
    },
    "nyn": {
        "greeting": (
            "Agandi, webare kuhikirira URA. Ndi omuhwezi waawe wa AI erizooba. Buuza ekibuuzo kyawe "
            "omu rurimi oru orikukunda — Orungyereza, Oluganda, Kiswahili, Orunyankore nari Leb Acoli — "
            "kandi niija kukugarukamu omu rurimi orwo. Noobaasa kushaba omukozi wa URA obwire bwona. "
            "Nimbaasa kukuhwera nta erizooba?"
        ),
        "more_detail": "Nooyenda kumanya ebindi ebikukiraho?",
        "timeout_transfer": (
            "Nsaasira, okusherura nikitutwara obwire buraingwa okukira oku kyabaire kishemereire. "
            "Ka nkukwatanise n'omukozi wa URA."
        ),
        "error_transfer": (
            "Nsaasira, haabaho obuzibu omu kusherura amakuru g'omushoro ogwo. "
            "Ka nkukwatanise n'omukozi wa URA."
        ),
        "transfer": "Ninkukwatanisa n'omukozi wa URA, ninkushaba orindeho.",
        "queue_disabled": (
            "Hati timbaasa kukukwatanisa n'omukozi. Ninkushaba oteere esimu ya URA ey'obusha "
            "omu shaaha z'okukora; enamba eri aha screen yaawe."
        ),
        "tollfree_screen": "Esimu ya URA ey'obusha: 0800 117 000",
        "still_holding": "Webare kurinda. Omukozi wa URA naija kukugambaho omu kaire kakye.",
        "officers_busy": (
            "Abakozi baitu boona baine emirimo, nahabwekyo omukozi naija kukuteerera esimu. "
            "Omu bwire obu, nimbaasa kukuhwera aha bibuuzo ebindi."
        ),
        "officers_unavailable": (
            "Hati tihariho mukozi orikubaasa kukugambaho, nahabwekyo omukozi naija kukuteerera esimu. "
            "Omu bwire obu, nimbaasa kukuhwera aha bibuuzo ebindi."
        ),
        "officers_off_duty": (
            "Abakozi baitu tibari aha murimo omu bwire obu, nahabwekyo omukozi naija kukuteerera "
            "esimu baagaruka aha murimo. Omu bwire obu, nimbaasa kukuhwera aha bibuuzo ebindi."
        ),
        "officer_hours_screen": "Shaaha z'abakozi: {hours}",
        "callback_confirmed": (
            "Kare. Omukozi naija kukuteerera esimu; enamba yaawe y'okujuriza eri aha screen yaawe. "
            "Omu bwire obu, nimbaasa kukuhwera aha bibuuzo ebindi."
        ),
        "queue_next": "Niiwe orikukurataho.",
        "officer_hold_update": "Webare kurinda. Omukozi waawe naija kugaruka ahari iwe ahonaaho.",
        "reference_screen": "Enamba yaawe y'okujuriza: {ref}",
        "clarify_term": "Nsaasira, waagamba {term}?",
        "clarify_confirm": "Nahabwekyo, naaba nkyetegyereize gye, noobuuza ahari {highlight} — kihikire?",
        "clarify_repeat_after": "Nsaasira, ekigambo ekirikukurata '{prev}' tinkihuriire — noobaasa kukigarukamu?",
        "clarify_repeat": "Nsaasira, tinkuhuriire gye — noobaasa kugarukamu?",
        "clarify_restart": "Nsaasira ahari ekyo — ninkushaba ogarukemu ekibuuzo kyawe.",
        "switched": "Kare — tugumizemu omu Runyankole.",
        "officer_offer": "Nooyenda kugamba n'omukozi ahari enshonga egi?",
        "crisis_support": (
            "Nsaasira munonga ahabw'oku orikwehurira. Amagara gaawe nigakira enshonga yoona "
            "ey'omushoro. Waaba ori omu kabi hati, ninkushaba oteere mwenda mwenda mwenda, "
            "nari emwe emwe ibiri. Enamba y'obuhabuzi bw'obusha eri aha screen yaawe."
        ),
        "crisis_offer": "Nooyenda kandi nkukwatanise n'omukozi wa URA?",
        "crisis_declined": (
            "Kare. Enamba ziri aha screen yaawe buri obu oraazetengye. Ndi aha waaba oine ekindi."
        ),
        "crisis_screen": (
            "Obuhwezi bw'ahonaaho: 999 nari 112. Obuhabuzi bw'obusha, Mental Health "
            "Uganda: 0800 21 21 21 (Orwokubanza kuhika Orwakataano, 8:30–17:00)."
        ),
        "crisis_waiting": "Naashaba omukozi wa URA akubanze. Guma aha simu; omukozi naija kukugambaho ahonaaho.",
        "officer_joining": "Hati nookwatanisibwa na {name}.",
        "officer_reconnecting": "Ninkushaba orindeho, ninkyakukwatanisa n'omukozi.",
        "on_hold": "Ninkushaba orindeho.",
        "officer_closing": "Webare kuteera esimu ahari URA. Ogumeho.",
        "idle_check": "Okyariho? Ndi aha, buuza ekibuuzo kyawe waaba oyeteekateekire.",
        "slow_ack": "Kare. Niija kugamba mpora, ndikuhuumuraho ahagati y'enshonga. Ekibuuzo kyawe niki?",
        "offer_declined": "Kare. Niki ekindi eki ndikubaasa kukuhweraho?",
        "idle_goodbye": "Tihaine eki mpuriire, nahabwekyo niija kukingaho esimu hati. Webare kuteera esimu ahari URA. Ogumeho.",
        "review_ask_end": "Otakaagiire, esimu egi ekuhwereire?",
        "review_thanks_end": "Webare kutugambira. Ogumeho.",
        "review_ask_transfer": "Hati ninkukwatanisa n'omukozi wa URA. Waaba otetegyereize, eby'okugarukamu byangye bikuhwereire?",
        "review_thanks_transfer": "Webare. Guma aha simu; omukozi naija kukugambaho ahonaaho.",
        "confirm_reprompt": "Ninkushaba ogarukemu ohamye omu iraka eririkuhurirwa gye: tugumizemu n'ekikorwa eki?",
        "confirm_done": "Webare, ekikorwa kihamiibwe kandi kyaheza kukorwa.",
        "confirm_not_done": (
            "Nsaasira, tinkibaasize kukiheza aha simu egi. "
            "Nooyenda kugamba n'omukozi ahari enshonga egi?"
        ),
    },
    "ach": {
        "greeting": (
            "Kopango, apwoyo kubo ki URA. An lakony ni me AI tin. Peny lapeny ni "
            "i leb ma imito — Leb Munu, Luganda, Kiswahili, Runyankole onyo Leb Acoli — "
            "dok abigamo i leb meno. Itwero penyo pi latic me URA cawa mo keken. "
            "Atwero konyi nining tin?"
        ),
        "more_detail": "Imito ngec mukene?",
        "timeout_transfer": (
            "kica, yenyo ngec tye ka tero kare malac loyo kit ma myero obed. "
            "Wek akubi ki latic me URA."
        ),
        "error_transfer": (
            "Kica, peko obedo tye i yenyo ngec me mucoro meno. "
            "Wek akubi ki latic me URA."
        ),
        "transfer": "Atye ka kubi ki latic me URA, tim ber ikur.",
        "queue_disabled": (
            "Kombedi pe atwero kubi ki latic. Tim ber igoyo cim me nono me URA "
            "i cawa me tic; namba tye i screen ni."
        ),
        "tollfree_screen": "Cim me nono me URA: 0800 117 000",
        "still_holding": "Apwoyo kuro. Latic me URA biloko kwedi i kare manok.",
        "officers_busy": (
            "Luticwa weng gitye ki tic, pi meno latic bigoyo cim boti. "
            "Kombedi, pud atwero konyi ki lapeny mukene."
        ),
        "officers_unavailable": (
            "Kombedi pe latic mo ma twero loko kwedi, pi meno latic bigoyo cim boti. "
            "Kombedi, pud atwero konyi ki lapeny mukene."
        ),
        "officers_off_duty": (
            "Luticwa pe gitye i tic i cawa man, pi meno latic bigoyo cim boti ka gudwogo i tic. "
            "Kombedi, pud atwero konyi ki lapeny mukene."
        ),
        "officer_hours_screen": "Cawa pa lutic: {hours}",
        "callback_confirmed": (
            "Ber. Latic bigoyo cim boti; namba ni me ngec tye i screen ni. "
            "Kombedi, pud atwero konyi ki lapeny mukene."
        ),
        "queue_next": "In aye ilubo.",
        "officer_hold_update": "Apwoyo kuro. Latic ni bidwogo boti oyot.",
        "reference_screen": "Namba ni me ngec: {ref}",
        "clarify_term": "Kica, iwaco {term}?",
        "clarify_confirm": "Pi meno, ka aniang maber, itye ka penyo i kom {highlight} — tye kakare?",
        "clarify_repeat_after": "Kica, pe awinyo lok ma lubo '{prev}' — itwero nwoyo?",
        "clarify_repeat": "Kica, pe awinyo maber — itwero nwoyo?",
        "clarify_restart": "Kica pi meno — tim ber inwo lapeny ni doki.",
        "switched": "Ber — wamede i Leb Acoli.",
        "officer_offer": "Imito loko ki latic me URA i kom lok man?",
        "crisis_support": (
            "Kica matek pi kit ma itye ka winyo kwede. Kwoni pire tek loyo lok mo keken "
            "me mucoro. Ka itye i ayela kombedi, tim ber igoyo abongwen abongwen abongwen, "
            "onyo acel acel aryo. Namba me miyo tam me nono tye i screen ni."
        ),
        "crisis_offer": "Imito bene akubi ki latic me URA?",
        "crisis_declined": (
            "Ber. Namba gitye i screen ni cawa mo keken ma imito. Atye kany ka tye gin mukene."
        ),
        "crisis_screen": (
            "Kony me oyot: 999 onyo 112. Miyo tam me nono, Mental Health "
            "Uganda: 0800 21 21 21 (Ceng Baraja naka Ceng Abic, 8:30–17:00)."
        ),
        "crisis_waiting": "Apenyo latic me URA ni obin boti mukwongo. Bed i cim; latic biloko kwedi oyot.",
        "officer_joining": "Kombedi ikube ki {name}.",
        "officer_reconnecting": "Tim ber ikur, pud atye ka kubi ki latic.",
        "on_hold": "Tim ber ikur.",
        "officer_closing": "Apwoyo goyo cim bot URA. Wabineno.",
        "idle_check": "Pud itye? Atye kany, peny lapeny ni ka itye atera.",
        "slow_ack": "Ber. Abiloko mot, kun ayweyo i kin lok. Lapeny ni obedo ngo?",
        "offer_declined": "Ber. Gin ango mukene ma atwero konyi iye?",
        "idle_goodbye": "Pe awinyo gin mo, pi meno abigiko cim kombedi. Apwoyo goyo cim bot URA. Wabineno.",
        "review_ask_end": "Ma peya icit, cim man okonyi?",
        "review_thanks_end": "Apwoyo pi tam ni. Wabineno.",
        "review_ask_transfer": "Atye ka kubi ki latic me URA kombedi. Ka itye ka kuro, lagam na okonyi?",
        "review_thanks_transfer": "Apwoyo. Bed i cim; latic biloko kwedi oyot.",
        "confirm_reprompt": "Tim ber imok doki ki dwon maleng: wamede ki tic man?",
        "confirm_done": "Apwoyo, tic dong kimoko dok otum.",
        "confirm_not_done": (
            "Kica, pe atwero tyeko man i cim man. "
            "Imito loko ki latic me URA i kom lok man?"
        ),
    },
}

_FILLERS: dict[str, tuple[str, ...]] = {
    "en": (
        "mm, one sec",
        "okay, so",
        "let me see",
        "right, checking now",
        "one moment",
        "let me check that",
        "just a second",
        "Let me check that for you.",
    ),
    "lg": (
        "Lindako katono.",
        "Ka nkebere.",
        "Nkebera kati.",
        "Akaseera katono.",
        "Ka ndabe.",
        "Ka nkukeberere.",
    ),
    "sw": (
        "Subiri kidogo.",
        "Ngoja niangalie.",
        "Naangalia sasa.",
        "Sekunde moja.",
        "Hebu nione.",
        "Ngoja nikuangalie.",
    ),
    "nyn": (
        "Rindaho kakye.",
        "Ka nshwijume.",
        "Ninshwijuma hati.",
        "Akaire kakye.",
        "Ka ndeebe.",
        "Ka nkushwijumire.",
    ),
    "ach": (
        "Kur manok.",
        "Wek angi.",
        "Atye ka ngiyo kombedi.",
        "Kare manok.",
        "Wek anen.",
        "Wek ayeny piri.",
    ),
}

# Yes / no for the clarification loop, beyond the English words clarify.py
# already knows. Short particles that double as ordinary grammar ("nga",
# Swahili "la") are left out: a false "no" restarts the question.
_YES_WORDS: dict[str, tuple[str, ...]] = {
    "lg": ("yee", "ye", "weewaawo", "kituufu", "bwe kiri", "kyekyo", "kye kyo"),
    "sw": ("ndiyo", "ndio", "sawa", "kweli", "sahihi", "hasa", "naam"),
    "nyn": ("ego", "eego", "kihikire", "nikwo", "kare"),
    "ach": ("eyo", "eeyo", "kakare", "ada", "ber"),
}
_NO_WORDS: dict[str, tuple[str, ...]] = {
    "lg": ("nedda", "si kyo", "si kituufu", "ssi"),
    "sw": ("hapana", "sio", "si sahihi"),
    "nyn": ("ngaaha", "apana", "tikihikire", "tikyo"),
    "ach": ("pe", "pe kakare"),
}

KEYS: tuple[str, ...] = tuple(_PHRASES["en"])


def phrase(key: str, language: str, **fmt: str) -> str:
    """The *key* line in *language*, falling back to English."""
    table = _PHRASES.get(language) or _PHRASES["en"]
    text = table.get(key) or _PHRASES["en"][key]
    return text.format(**fmt) if fmt else text


_EMPATHETIC_FILLERS: dict[str, tuple[str, ...]] = {
    "en": (
        "I understand — let me check the best option for you right now.",
        "I hear you — let me look up the right guidance for you.",
    ),
    "lg": (
        "Ntegedde — ka nkebere engeri esinga okukuyamba kati.",
        "Nkuwulidde — ka nnoonye eky'okuddamu ekituufu mangu.",
    ),
    "sw": (
        "Naelewa — ngoja niangalie njia bora ya kukusaidia sasa hivi.",
        "Nimekusikia — wacha niangalie mwongozo sahihi mara moja.",
    ),
    "nyn": (
        "Nyetegyereza — ka nshwijume omuringo ogurikukirayo oburungi kukuhwera hati.",
        "Mpuriire — ka nsherure obuhabuzi obuhikire ahonaaho.",
    ),
    "ach": (
        "Aniang — wek angi yoo maber loyo me konyi kombedi.",
        "Awinyo — wek ayeny tela ma kakare oyot-oyot.",
    ),
}


def fillers(language: str) -> tuple[str, ...]:
    return _FILLERS.get(language) or _FILLERS["en"]


def empathetic_fillers(language: str) -> tuple[str, ...]:
    return _EMPATHETIC_FILLERS.get(language) or _EMPATHETIC_FILLERS["en"]


def pick_filler(language: str, last: str | None = None, *, affect: str = "calm") -> str:
    """A filler for *language* (affect-attuned when caller is in distress) that is not the one just used."""
    if affect in ("frustrated", "urgent_distress", "hardship"):
        pool = empathetic_fillers(language)
    else:
        pool = fillers(language)
    choices = [f for f in pool if f != last]
    return random.choice(choices) if choices else pool[0]  # noqa: S311 — variety, not security


@lru_cache(maxsize=8)
def yes_no_patterns(language: str) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Extra yes / no regexes for *language* (``None`` where English suffices)."""

    def build(words: tuple[str, ...]) -> re.Pattern[str] | None:
        if not words:
            return None
        return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)

    return build(_YES_WORDS.get(language, ())), build(_NO_WORDS.get(language, ()))


def prewarm_phrases(language: str) -> list[str]:
    """Fixed lines worth synthesising before the first call reaches them.

    Formatted lines (``{ref}``, ``{term}``…) are left out — their text is
    only known mid-call — and so are the ``UNSPOKEN`` screen lines.
    """
    fixed = [text for key, text in _PHRASES.get(language, {}).items() if "{" not in text and key not in UNSPOKEN]
    return [*fillers(language), *fixed]
