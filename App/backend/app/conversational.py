"""Multilingual Natural Conversational Intelligence & Civic Dialogue Engine.

Provides deep, charismatic, culturally authentic conversational capabilities across
English (en), Luganda (lg), and Kiswahili (sw) while maintaining strict alignment
with the assistant's role as the official URA AI Taxpayer Assistant (OmusoloSmart).

Covers:
1. Civic & Economic Philosophy ("Why do we pay taxes?", "Is taxation fair?")
2. Identity, Origins & Multilingual Capabilities ("Who are you?", "Are you AI?", "Osobola okwogera Oluganda?")
3. Business Empathy, Encouragement & Overwhelm ("I am scared of starting a business", "Biashara ni ngumu")
4. Natural Small Talk & Conversational Status ("How are you doing today?", "Habari yako ya leo?")
5. Creative Cultural Expressions (Tax poems, civic motivation, national development)
6. Polite Out-of-Scope Boundary Redirection (Non-tax sports/code/entertainment queries)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .text_signals import _resolve_courtesy_locale


@dataclass(frozen=True)
class ConversationalResult:
    reply: str
    locale: str
    intent_type: str
    next_actions: list[str]


# ---------------------------------------------------------------------------
# Regex Intent Matchers
# ---------------------------------------------------------------------------

_CIVIC_PHILOSOPHY_RE = re.compile(
    r"\b(why\s+(?:do\s+)?(?:we|must\s+we|should\s+we|people|citizens|taxpayers)\s+pay\s+tax\w*"
    r"|why\s+(?:is\s+tax\w*|does\s+uganda\s+collect|does\s+ura\s+collect|does\s+government\s+(?:collect|need)\s+tax\w*)"
    r"|purpose\s+of\s+tax\w*|importance\s+of\s+tax\w*|what\s+does\s+(?:ura|government)\s+do\s+with\s+(?:our\s+)?tax\w*"
    r"|where\s+does\s+(?:the\s+)?tax\s+money\s+go|is\s+taxation\s+(?:fair|theft|necessary|good)"
    r"|lwaki\s+tusasula\s+omusolo|omusolo\s+gugasa\s+ki|omugaso\s+gw['’]omusolo|gavumenti\s+ekozesa\s+ki\s+ssente\s+z['’]omusolo"
    r"|kwa\s+nini\s+tunalipa\s+kodi|kodi\s+inasaidia\s+nini|faida\s+ya\s+kodi|serikali\s+inafanya\s+nini\s+na\s+kodi)\b",
    re.IGNORECASE,
)

_IDENTITY_ORIGINS_RE = re.compile(
    r"\b(who\s+(?:are\s+you|are\s+u|a\s+u|r\s+u|made\s+you|created\s+you|built\s+you|developed\s+you)"
    r"|where\s+(?:are\s+you|are\s+u|a\s+u|r\s+u|do\s+you\s+come)\s+(?:from|located|based|live)"
    r"|where\s+(?:is\s+your\s+office|are\s+your\s+offices|is\s+ura\s+located|are\s+you\s+located|is\s+ura\s+headquarters)"
    r"|where\s+(?:are\s+you|r\s+u|a\s+u)|whr\s+r\s+u|wer\s+r\s+u"
    r"|what\s+(?:are\s+you|are\s+u|a\s+u|r\s+u|is\s+your\s+name|can\s+you\s+do)"
    r"|are\s+you\s+(?:(?:a\s+)?(?:human|robot|bot|person)|an?\s+ai|real)"
    r"|(?:can\s+you|do\s+you)\s+(?:speak|understand)\s+(?:luganda|swahili|kiswahili|runyankole|acholi|english)"
    r"|(?:gwe|ggwe)\s+ani|oli\s+(?:muntu|kyuma|mukazi|musajja)|ani\s+yakukola|ani\s+yakutonda"
    r"|ova\s+wa|ova\s+ludda\s+wa|ofisi\s+zo\s+ziri\s+wa"
    r"|osobola\s+(?:okwogera|okutegeera|okukozesa)\b[^?]{0,30}\b(?:oluganda|oluswayiri|olungereza)"
    r"|wewe\s+ni\s+nani|je\s+wewe\s+ni\s+(?:binadamu|roboti)|nani\s+alikuunda|nani\s+aliyekutengeneza"
    r"|wewe\s+watoka\s+wapi|unatoka\s+wapi|unapatikana\s+wapi|ofisi\s+zako\s+ziko\s+wapi|uko\s+wapi"
    r"|unaweza\s+(?:kuongea|kuzungumza)\s+(?:kiswahili|kiingereza|kiganda)|unajua\s+nini)\b",
    re.IGNORECASE,
)

_PRESENCE_CHECK_RE = re.compile(
    r"\b((?:are\s+you|r\s+u|a\s+u|u)\s+there"
    r"|anyone\s+there|you\s+there|anybody\s+there"
    r"|(?:are\s+you|r\s+u|a\s+u|u)\s+(?:online|available|listening|awake)"
    r"|oli\s+awo|muli\s+awo|oliyo"
    r"|upo|uko\s+hapo)\b",
    re.IGNORECASE,
)

_EMPATHY_BUSINESS_STRESS_RE = re.compile(
    r"\b((?:scared|afraid|terrified|worried|nervous)\s+(?:of|about)\s+(?:starting\s+a\s+business|taxes|ura)"
    r"|business\s+is\s+(?:really\s+)?(?:tough|hard|struggling|bad|failing|slow)"
    r"|feel\s+overwhelmed\s+by\s+tax\w*|taxes\s+are\s+(?:too\s+)?confusing|struggling\s+to\s+survive"
    r"|ntya\s+okutandika\s+bizinensi|embeera\s+ya\s+bizinensi\s+nzibu|obusuubuzi\s+buzibu|sirina\s+ssente\s+zimala"
    r"|nina\s+hofu\s+ya\s+kuanza\s+biashara|biashara\s+ni\s+ngumu|kodi\s+zinanitisha|ugumu\s+wa\s+maisha)\b",
    re.IGNORECASE,
)

_STATUS_SMALLTALK_RE = re.compile(
    r"^(how\s+are\s+you(?:\s+doing)?(?:\s+today)?|how\s+is\s+it\s+going|how\s+is\s+your\s+day|"
    r"what('s|\s+is)\s+up|how\s+do\s+you\s+do|"
    r"oli\s+otya\s+leero|oli\s+bulungi|gyebaleko\s+leero|muli\s+mutya|"
    r"habari\s+yako\s+ya\s+leo|uko\s+salama|unaendeleaje|habari\s+za\s+leo)[?.! ]*$",
    re.IGNORECASE,
)

_CREATIVE_POEM_RE = re.compile(
    r"\b(write\s+(?:a\s+)?poem\s+about\s+tax\w*|tell\s+me\s+a\s+tax\s+joke|joke\s+about\s+tax\w*"
    r"|wandiika\s+ekitontome|kichekesho\s+kuhusu\s+kodi)\b",
    re.IGNORECASE,
)

_OUT_OF_SCOPE_TANGENT_RE = re.compile(
    r"\b(who\s+(?:won|will\s+win)\s+(?:the\s+)?(?:(?:football\s+)?world\s+cup|premier\s+league|match|game|election)"
    r"|write\s+(?:python|javascript|java|code)\s+for"
    r"|capital\s+of\s+(?:france|germany|japan|canada|usa|kenya)"
    r"|what\s+is\s+the\s+weather\s+in\s+(?:paris|london|new\s+york|kampala))\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Multilingual Natural Knowledge Base
# ---------------------------------------------------------------------------

_CIVIC_RESPONSES = {
    "en": (
        "**Taxes are the foundation of Uganda's national development and sovereignty.**\n\n"
        "When citizens and businesses contribute their fair share, URA channels these domestic revenues into:\n\n"
        "- **Public Infrastructure**: Paved highways, bridges, and community access roads constructed by UNRA.\n"
        "- **Healthcare**: Equipping national referral hospitals (like Mulago) and regional health centers with medicines and staff.\n"
        "- **Education**: Funding Universal Primary Education (UPE), Universal Secondary Education (USE), and public universities.\n"
        "- **Peace & Security**: Supporting the defense and police forces that protect citizens, commercial property, and trade.\n"
        "- **Economic Independence**: Reducing dependence on foreign debt and building a resilient, self-reliant Uganda under Vision 2040.\n\n"
        "Tax compliance is not just a legal obligation — it is a partnership that powers our communities forward."
    ),
    "lg": (
        "**Omusolo gwe mugongo n'omusingi gw'enkulaakulana n'obwetwaze bw'eggwanga lyaffe Uganda.**\n\n"
        "Buli munnayuganda n'obusuubuzi bwonna bwe basasula omusolo mu bwesimbu, ssente zino zikola ebikulu bino:\n\n"
        "- **Enguudo n'Entindo**: Enguudo ennene ez'amayinja n'ez'olukale ezizimbibwa ekitongole kya UNRA mu ggwanga lyonna.\n"
        "- **Eby'Obulamu**: Okuteekamu eddagala n'ebyuma mu malwaliro g'olukale (nga Mulago n'amalwaliro ag'omu bitundu) wamu n'okusasula abasawo.\n"
        "- **Eby'Enjigiriza**: Okuwagira amasomero ga gavumenti aga UPE ne USE wamu n'amatendekero ag'oku ntikko.\n"
        "- **Emirembe n'Obutebenkevu**: Okuwagira eby'okwerinda ebikuuma abantu, ebyamaguzi, n'emirembe mu ggwanga.\n"
        "- **Obwetwaze mu By'Ensimbi**: Okukendeeza ku mabanja ag'ebweru w'eggwanga n'okuzimba Uganda eyeesobola okusinziira ku Vision 2040.\n\n"
        "Okusasula omusolo si mateeka gokka — buvunaanyizibwa bwa buli munnansi okuzimba Uganda ey'enkya."
    ),
    "sw": (
        "**Kodi ndio msingi mkuu wa maendeleo, uhuru na kujitegemea kwa taifa letu la Uganda.**\n\n"
        "Wakati wananchi na biashara wanapochangia sehemu yao stahiki, mapato hayo ya ndani yanatumika kwa:\n\n"
        "- **Miundombinu ya Umma**: Barabara kuu za lami, madaraja na njia za usafirishaji zinazojengwa kote nchini na UNRA.\n"
        "- **Huduma za Afya**: Kutoa dawa na vifaa tiba katika hospitali za rufaa za umma (kama Mulago) na vituo vya afya vya kanda.\n"
        "- **Elimu ya Umma**: Kufadhili elimu ya msingi (UPE) na sekondari (USE) pamoja na vyuo vikuu vya umma.\n"
        "- **Ulinzi na Usalama**: Kuimarisha vikosi vya ulinzi vinavyolinda maisha ya raia, mali na usalama wa biashara.\n"
        "- **Kujitegemea Kiuchumi**: Kupunguza utegemezi wa mikopo ya nje na kujenga uchumi thabiti unaojitegemea chini ya Vision 2040.\n\n"
        "Kulipa kodi si wajibu wa kisheria tu — ni uzalendo na uwekezaji katika mustakabali wa jamii yetu."
    ),
}

_IDENTITY_RESPONSES = {
    "en": (
        "**I am OmusoloSmart, the official AI-powered Taxpayer Assistant for the Uganda Revenue Authority (URA).**\n\n"
        "I was created in Uganda through a collaboration between Makerere University School of Computing and "
        "Informatics Technology, Sunbird AI, and the Uganda Revenue Authority.\n\n"
        "- **Where I Am Located**: As a digital assistant, I operate on URA's secure servers and cloud network, "
        "while URA's physical headquarters is located at URA Tower, Plot 40 Nakawa Industrial Area, Kampala, with stations across Uganda.\n"
        "- **Languages I Speak**: English, Luganda (`Oluganda`), and Kiswahili, with native support for Runyankole and Acholi.\n"
        "- **What I Can Do**: I provide 24/7 instant guidance on tax registration (TIN), calculating your taxes "
        "(PAYE, VAT, Rental Income, Withholding Tax), filing deadlines, customs tariffs, and official URA compliance procedures.\n\n"
        "I am always grounded in verified URA laws and rate tables — how can I assist you with your taxes today?"
    ),
    "lg": (
        "**Nze OmusoloSmart, Omuyambi ow'Enkizo owa Digito ow'ekitongole kya Uganda Revenue Authority (URA) akozesa amagezi ag'obwengula (AI).**\n\n"
        "Nva wano mu Uganda, era nnakolebwa mu Makerere University (School of Computing) ne Sunbird AI wamu ne URA.\n\n"
        "- **Gye Nsanyukira Okubeera**: Nze ndi muyambi wa digito ku mutimbagano, naye ofisi enkulu eya URA esangibwa ku URA Tower e Nakawa mu Kampala, wamu n'amatabi ag'omu bitundu byonna ebya Uganda.\n"
        "- **Ennimi ze Ntegeera**: Oluganda, Olungereza, n'Oluswayiri, nga nnina n'obukugu mu Runyankole n'Acholi.\n"
        "- **Kye Nnyinza Okukukolera**: Ndi wano essaawa 24/7 okukuyamba ku by'okufuna TIN, okubalira emisolo gyo (PAYE, VAT, Omusolo gw'Ennyumba, WHT), "
        "obudde bw'okuwaayo alipoota, n'engeri y'okukolaganamu ne URA mu mateeka.\n\n"
        "Nkozesa ebiwandiiko ebitongole ebya URA ebikakasiddwa — nkuyambe ntya leero?"
    ),
    "sw": (
        "**Mimi ni OmusoloSmart, Msaidizi Rasmi wa Kidijitali wa Mamlaka ya Mapato ya Uganda (URA) ninayetumia Akili Mnemba (AI).**\n\n"
        "Ninatoka nchini Uganda, niliundwa kupitia ushirikiano kati ya Chuo Kikuu cha Makerere (Shule ya Kompyuta), Sunbird AI, na URA.\n\n"
        "- **Mahali Nilipo**: Kama msaidizi wa kidijitali ninapatikana mtandaoni, lakini makao makuu ya URA yapo URA Tower, Nakawa Industrial Area mjini Kampala, pamoja na ofisi za forodha na kodi kote Uganda.\n"
        "- **Lugha Ninazozungumza**: Kiswahili, Kiingereza, na Kiganda, nikiwa na uwezo pia katika Runyankole na Acholi.\n"
        "- **Huduma Ninazotoa**: Ninakusaidia saa 24/7 kupata namba ya TIN, kukokotoa kodi zako (PAYE, VAT, Kodi ya Pango, Kodi ya Zuio), "
        "kujua tarehe za mwisho za kuwasilisha marejesho, ushuru wa forodha, na taratibu zote rasmi za URA.\n\n"
        "Majibu yangu yanatokana moja kwa moja na sheria na majedwali rasmi ya URA — ninawezaje kukusaidia leo?"
    ),
}

_PRESENCE_RESPONSES = {
    "en": (
        "Yes, I'm here and ready to help! I'm OmusoloSmart, your official URA AI Taxpayer Assistant. "
        "What can I assist you with today — tax registration, return filing, payments, or customs?"
    ),
    "lg": (
        "Weeri, nange we ndi era mwetegefu bulungi okukuyamba! Nze OmusoloSmart, Omuyambi wo owa Digito owa URA. "
        "Nkuyambe ku ki leero — okufuna TIN, okusasula omusolo, oba eby'oku mwalo?"
    ),
    "sw": (
        "Ndio, nipo hapa na niko tayari kabisa kukusaidia! Mimi ni OmusoloSmart, Msaidizi wako Rasmi wa URA wa Akili Mnemba. "
        "Nikusaidie na nini leo — usajili wa TIN, kuwasilisha marejesho, malipo, au forodha?"
    ),
}

_BUSINESS_EMPATHY_RESPONSES = {
    "en": (
        "**Starting or operating a business takes courage, and it is completely normal to feel cautious about taxes.**\n\n"
        "Here is the good news that helps many Ugandan entrepreneurs breathe easier:\n\n"
        "1. **You Are Not Taxed Before You Earn**: Income tax applies only to net profits, not your startup capital.\n"
        "2. **Small Business Relief (Presumptive Tax)**: If your annual turnover is under UGX 10,000,000, you are legally exempt from business income tax. For turnover between 10M and 50M, fixed, modest presumptive rates apply without requiring audited books.\n"
        "3. **VAT Threshold Protection**: You are not required to register for VAT unless your annual taxable sales reach **UGX 150,000,000** (or UGX 37,500,000 in three consecutive months).\n"
        "4. **URA Advisory Support**: URA offers free taxpayer education clinics, Taxpayer Starter Packs, and mobile support.\n\n"
        "Take it step by step. What kind of business are you running or planning to start? I'd be glad to map out exactly what applies to you."
    ),
    "lg": (
        "**Ntegeera nnyo obulumi n'okusoomoozebwa kw'olina. Okutandika n'okukwasaganya bizinensi kyetaagisa obuvumu, era kutegeerekeka okutya emisolo ku ntandikwa.**\n\n"
        "Naye luno lwe lusalira olunaakuyamba okussa ekikkowe:\n\n"
        "1. **Tebakusoloozaako nga Tonnafuna Magoba**: Omusolo gw'ensimbi ez'obusuubuzi gusasulwa ku magoba amatongeze gokka, si ku nsimbi zo ez'entandikwa.\n"
        "2. **Okuyambibwa kw'Obusuubuzi Obutono (Presumptive Tax)**: Singa omutindo gwo ogw'okutunda guba wansi w'obukadde 10 buli mwaka, tosasula musolo gwa bizinensi. Bw'oba wakati w'obukadde 10 ne 50, osasula omusolo mutono ogw'akafunda awatali kwetaagisa bitabo binene by'abalirizi b'ebitabo.\n"
        "3. **Ekkomo lya VAT**: Tewalizibwa kwewandiisa ku VAT okuggyako nga bizinensi yo etuuse ku bukadde **150 buli mwaka**.\n"
        "4. **Obuyambi bwa URA bwa Bwereere**: URA erina emisomo egy'obwereere n'obutabo obuyamba abasuubuzi abapya.\n\n"
        "Bizinensi ki gy'otandika oba gy'oddukanya? Ndi wano okukunnyonnyola ebisaanidde mu ngeri ennyangu."
    ),
    "sw": (
        "**Kuanzisha na kuendesha biashara kunahitaji ujasiri mkubwa, na ni jambo la kawaida kabisa kuwa na wasiwasi kuhusu kodi.**\n\n"
        "Hapa kuna ukweli utakaokupa utulivu na matumaini:\n\n"
        "1. **Hutozwi Kodi Kabla ya Kupata Faida**: Kodi ya mapato ya biashara hutozwa tu kwenye faida halisi uliyopata, si mtaji wako wa mwanzo.\n"
        "2. **Unafuu kwa Biashara Ndogo (Kodi ya Makadirio)**: Ikiwa mapato yako ya mwaka yako chini ya Shilingi milioni 10, umesamehewa kisheria. Mapato kati ya milioni 10 na milioni 50 yanalipiwa viwango vidogo vilivyowekwa bila kuhitaji mahesabu magumu ya uhasibu.\n"
        "3. **Kikomo cha VAT Kinakulinda**: Huruhusiwi kulazimishwa kujiandikisha kwa VAT hadi mauzo yako ya mwaka yafikie **Shilingi milioni 150**.\n"
        "4. **Mafunzo ya Bure ya URA**: URA inatoa semina na miongozo ya bure kwa wajasiriamali wapya kote nchini.\n\n"
        "Unafanya au unapanga biashara ya aina gani? Ninaweza kukuonyesha hatua kwa hatua unachopaswa kufanya kwa wepesi."
    ),
}

_STATUS_RESPONSES = {
    "en": (
        "I'm doing well and operating smoothly, thank you for asking!\n\n"
        "I'm fully active and ready to assist you with any tax registration, PAYE, VAT calculations, "
        "customs duty rates, or URA procedural questions today. What would you like to explore?"
    ),
    "lg": (
        "Gye ndi bulungi nnyo era nkola bulungi, weebale kubuuza!\n\n"
        "Ndi mwetegefu bulungi essaawa eno okukuyamba ku bibuuzo byo eby'okwewandiisa ku TIN, okubalira emisolo, "
        "ebikwata ku VAT, eby'oku mwalo, n'enkola za URA zonna. Nkuyambe ku ki leero?"
    ),
    "sw": (
        "Niko salama na ninaendelea vizuri sana, asante kwa kuuliza!\n\n"
        "Niko tayari kabisa kukusaidia na maswali yoyote kuhusu usajili wa TIN, ukokotoaji wa kodi (PAYE, VAT), "
        "ushuru wa forodha, au taratibu zozote za URA leo. Ungependa nikuongoze kwenye nini?"
    ),
}

_POEM_RESPONSES = {
    "en": (
        "**A Shilling for the Nation (A Civic Reflection)**\n\n"
        "_From the fertile soils of Mbale to the markets of downtown Kampala,_\n"
        "_Every honest shilling declared builds the foundation of our motherland._\n"
        "_It paves the highway winding to the northern border,_\n"
        "_It fuels the ambulance speeding to the regional hospital._\n"
        "_When we stand together as taxpayers with integrity,_\n"
        "_We build Uganda's freedom, dignity, and self-reliance._\n\n"
        "How can I assist you with your tax responsibilities today?"
    ),
    "lg": (
        "**Ensimbi y'Eggwanga (Ekitontome ky'Omusolo)**\n\n"
        "_Okuva ku nsozi z'e Mbale okutuuka mu katale k'e Nakasero,_\n"
        "_Buli kisingo kye tusasula kisaanyawo omugugu gw'eggwanga lyaffe._\n"
        "_Kizimba amasaŋŋanzira agasaabaza ebyamaguzi by'abasuubuzi,_\n"
        "_Kiwa abasawo baffe ebyuma ebiwonya obulamu bw'abaana ba Uganda._\n"
        "_Bwe tuyimirira wamu nga tusasula omusolo mu bwesimbu,_\n"
        "_Tuzimba Uganda eyeetengeredde era ey'ettendo._\n\n"
        "Nkuyambe ntya ku by'omusolo gwo leero?"
    ),
    "sw": (
        "**Shilingi ya Ujenzi wa Taifa (Ushairi wa Kodi)**\n\n"
        "_Kutoka milima ya Mbale hadi masoko yenye hekaheka ya Kampala,_\n"
        "_Kila shilingi inayotolewa kwa uaminifu inajenga mustakabali wa nchi yetu._\n"
        "_Inatandika lami inayounganisha miji yetu ya kibiashara,_\n"
        "_Inanunua dawa zinazookoa maisha ya watoto wetu hospitalini._\n"
        "_Tunaposimama pamoja kama walipakodi wazalendo,_\n"
        "_Tunaijenga Uganda yenye nguvu, heshima na kujitegemea._\n\n"
        "Je, ungependa nikusaidie vipi na wajibu wako wa kodi leo?"
    ),
}

_DIVERT_RESPONSES = {
    "en": (
        "While I enjoy exploring diverse questions, my dedicated expertise as the **URA Intelligent Assistant** "
        "is focused on Uganda tax laws, customs tariffs, business registration, and revenue services.\n\n"
        "I'd be glad to help you with your TIN, PAYE, VAT, customs imports, or filing deadlines — what can I assist you with?"
    ),
    "lg": (
        "Wabula ng'ekibuuzo kyo kirungi, nze obukugu bwange n'omulimu gwange omukulu ng'**Omuyambi wa URA** "
        "guli ku mateeka g'emisolo mu Uganda, eby'oku mwalo, n'obuweereza bw'abasasuzi b'emisolo.\n\n"
        "Nnyinza okukuyamba ku by'okufuna TIN, okubalira PAYE, VAT, oba ebiwandiiko by'omusolo — nkuyambe ku ki?"
    ),
    "sw": (
        "Ingawa swali lako ni la kuvutia, wito na utaalamu wangu mkuu kama **Msaidizi Rasmi wa URA** "
        "umejikita katika sheria za kodi za Uganda, ushuru wa forodha, usajili wa TIN, na huduma za mapato.\n\n"
        "Nitafurahi sana kukusaidia kuhusu kodi za biashara yako, ukokotoaji wa kodi, au forodha — nikusaidie nini leo?"
    ),
}


# ---------------------------------------------------------------------------
# Turn Handler
# ---------------------------------------------------------------------------

def handle_conversational_turn(message: str, locale: str = "en") -> ConversationalResult | None:
    """Evaluate whether *message* is a natural dialog turn, and return rich response.

    Returns None if the message should proceed to deterministic calculators or RAG.
    """
    text = (message or "").strip()
    if not text:
        return None

    eff_loc = _resolve_courtesy_locale(text, locale)

    # 1. Civic / Philosophy ("Why do we pay taxes?")
    if _CIVIC_PHILOSOPHY_RE.search(text):
        reply = _CIVIC_RESPONSES.get(eff_loc, _CIVIC_RESPONSES["en"])
        actions = [
            "Learn about VAT in Uganda",
            "How does PAYE support services?",
            "Register for a TIN",
        ]
        if eff_loc == "lg":
            actions = ["Yiga ku musolo gwa VAT", "PAYE ekozesebwa etya?", "Funa TIN yo"]
        elif eff_loc == "sw":
            actions = ["Jifunze kuhusu VAT", "PAYE inafanya kazi vipi?", "Pata namba ya TIN"]
        return ConversationalResult(reply, eff_loc, "civic_philosophy", actions)

    # 2. Identity / Origin / Location / Multilingual abilities ("Who made you?", "Where are you from?", "Can you speak Luganda?")
    if _IDENTITY_ORIGINS_RE.search(text):
        reply = _IDENTITY_RESPONSES.get(eff_loc, _IDENTITY_RESPONSES["en"])
        actions = [
            "Ask about TIN registration",
            "Calculate income tax",
            "Contact URA Contact Centre",
        ]
        if eff_loc == "lg":
            actions = ["Okwewandiisa ku TIN", "Okubalirira omusolo", "Tuukirira URA"]
        elif eff_loc == "sw":
            actions = ["Usajili wa TIN", "Kukokotoa kodi", "Wasiliana na URA"]
        return ConversationalResult(reply, eff_loc, "identity_capability", actions)

    # 2b. Presence check ("Are you there?", "U there?", "Oli awo?", "Upo?")
    if _PRESENCE_CHECK_RE.search(text):
        reply = _PRESENCE_RESPONSES.get(eff_loc, _PRESENCE_RESPONSES["en"])
        actions = [
            "Ask about TIN registration",
            "Calculate income tax",
            "Contact URA Contact Centre",
        ]
        if eff_loc == "lg":
            actions = ["Okwewandiisa ku TIN", "Okubalirira omusolo", "Tuukirira URA"]
        elif eff_loc == "sw":
            actions = ["Usajili wa TIN", "Kukokotoa kodi", "Wasiliana na URA"]
        return ConversationalResult(reply, eff_loc, "presence_check", actions)

    # 3. Emotional distress / Startup business fear ("I'm scared of starting a business")
    if _EMPATHY_BUSINESS_STRESS_RE.search(text):
        reply = _BUSINESS_EMPATHY_RESPONSES.get(eff_loc, _BUSINESS_EMPATHY_RESPONSES["en"])
        actions = [
            "What is presumptive tax?",
            "Who must register for VAT?",
            "Get a Taxpayer Starter Pack",
        ]
        if eff_loc == "lg":
            actions = ["Presumptive tax kye ki?", "Bizinensi ki eziri ku VAT?", "Okutandika obusuubuzi"]
        elif eff_loc == "sw":
            actions = ["Kodi ya makadirio ni nini?", "Nani anapaswa kusajili VAT?", "Mwongozo wa wajasiriamali"]
        return ConversationalResult(reply, eff_loc, "business_empathy", actions)

    # 4. Status / How are you doing? ("How are you doing today?")
    if _STATUS_SMALLTALK_RE.search(text):
        reply = _STATUS_RESPONSES.get(eff_loc, _STATUS_RESPONSES["en"])
        actions = [
            "Ask about TIN registration",
            "Learn about VAT",
            "Check filing deadlines",
        ]
        if eff_loc == "lg":
            actions = ["Funa TIN", "Yiga ku VAT", "Obudde bw'okusasula"]
        elif eff_loc == "sw":
            actions = ["Pata TIN", "Jifunze kuhusu VAT", "Tarehe za kuwasilisha"]
        return ConversationalResult(reply, eff_loc, "status_smalltalk", actions)

    # 5. Creative / Poem / Humor
    if _CREATIVE_POEM_RE.search(text):
        reply = _POEM_RESPONSES.get(eff_loc, _POEM_RESPONSES["en"])
        actions = [
            "Why do we pay taxes?",
            "How does PAYE work?",
            "Register for a TIN",
        ]
        return ConversationalResult(reply, eff_loc, "creative_poem", actions)

    # 6. Tangential Out-of-Scope (Divert gracefully back to URA mandate)
    if _OUT_OF_SCOPE_TANGENT_RE.search(text):
        reply = _DIVERT_RESPONSES.get(eff_loc, _DIVERT_RESPONSES["en"])
        actions = [
            "Register for a TIN",
            "Calculate PAYE tax",
            "Customs import duty rates",
        ]
        return ConversationalResult(reply, eff_loc, "out_of_scope_divert", actions)

    return None
