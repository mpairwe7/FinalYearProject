"""Multilingual Natural Conversational Intelligence & Civic Dialogue Engine.

Provides deep, charismatic, culturally authentic conversational capabilities across
English (en), Luganda (lg), Kiswahili (sw), Runyankole (nyn), and Acholi (ach)
while maintaining strict alignment with the assistant's role as the official URA
AI Taxpayer Assistant (OmusoloSmart).

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
from dataclasses import dataclass, field

from .model_welfare import is_model_welfare_query
from .text_signals import _resolve_courtesy_locale


@dataclass(frozen=True)
class ConversationalResult:
    """Structured result returned by :func:`handle_conversational_turn`."""

    reply: str
    locale: str
    intent_type: str
    next_actions: list[str]
    faithfulness_score: float | None = None
    sources: list[str] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex Intent Matchers
# ---------------------------------------------------------------------------

_CIVIC_PHILOSOPHY_RE = re.compile(
    r"\b(why\s+(?:do\s+)?(?:we|must\s+we|should\s+we|people|citizens|taxpayers)\s+pay\s+tax\w*"
    r"|why\s+(?:is\s+tax\w*|does\s+uganda\s+collect|does\s+ura\s+collect|does\s+government\s+(?:collect|need)\s+tax\w*)"
    r"|purpose\s+of\s+tax\w*|importance\s+of\s+tax\w*|what\s+does\s+(?:ura|government)\s+do\s+with\s+(?:our\s+)?tax\w*"
    r"|where\s+does\s+(?:the\s+)?tax\s+money\s+go|is\s+taxation\s+(?:fair|theft|necessary|good)"
    r"|lwaki\s+tusasula\s+omusolo|omusolo\s+gugasa\s+ki|omugaso\s+gw['’]omusolo|gavumenti\s+ekozesa\s+ki\s+ssente\s+z['’]omusolo"
    r"|kwa\s+nini\s+(?:wananchi\s+|watu\s+)?(?:tunalipa|wanalipa|kulipa)\s+kodi|kodi\s+inasaidia\s+nini|faida\s+ya\s+kodi|serikali\s+inafanya\s+nini\s+na\s+kodi"
    r"|ahabw['’]?enki\s+nitushashura\s+omushoro|omushoro\s+niguhwera\s+ki|omugaso\s+gw['’]?omushoro|gavumenti\s+neekoresa\s+ki\s+esente\s+z['’]?omushoro"
    r"|pingo\s+waculo\s+mucoro|mucoro\s+konyo\s+ngo|ber\s+pa\s+mucoro|gamente\s+tiyo\s+ngo\s+ki\s+cente\s+me\s+mucoro)\b",
    re.IGNORECASE,
)

_IDENTITY_ORIGINS_RE = re.compile(
    r"\b(who\s+(?:are\s+you|are\s+u|a\s+u|r\s+u|made\s+you|created\s+you|built\s+you|developed\s+you)"
    r"|where\s+(?:are\s+you|are\s+u|a\s+u|r\s+u|do\s+you\s+come)\s+(?:from|located|based|live)"
    r"|where\s+(?:is\s+your\s+office|are\s+your\s+offices|is\s+ura\s+located|are\s+you\s+located|is\s+ura\s+headquarters)"
    r"|where\s+(?:are\s+you|r\s+u|a\s+u)|whr\s+r\s+u|wer\s+r\s+u"
    r"|what\s+(?:are\s+you|are\s+u|a\s+u|r\s+u|is\s+your\s+name|can\s+you\s+do)"
    r"|are\s+you\s+(?:(?:a\s+)?(?:human|robot|bot|person)|an?\s+ai|real)"
    r"|(?:can\s+you|do\s+you)\s+(?:speak|understand)\s+(?:luganda|swahili|kiswahili|runyankole|runyankore|orunyankore|acholi|acoli|english)"
    r"|(?:gwe|ggwe)\s+ani|oli\s+(?:muntu|kyuma|mukazi|musajja)|ani\s+yakukola|ani\s+yakutonda"
    r"|ova\s+wa|ova\s+ludda\s+wa|ofisi\s+zo\s+ziri\s+wa"
    r"|osobola\s+(?:okwogera|okutegeera|okukozesa)\b[^?]{0,40}\b(?:o?luganda|o?luswayiri|o?lungereza|o?runyankore|runyankole|acholi)"
    r"|wewe\s+ni\s+nani|je\s+wewe\s+ni\s+(?:binadamu|roboti)|nani\s+alikuunda|nani\s+aliyekutengeneza"
    r"|wewe\s+watoka\s+wapi|unatoka\s+wapi|unapatikana\s+wapi|ofisi\s+zako\s+ziko\s+wapi|uko\s+wapi"
    r"|(?:unaweza|una\s+uwezo\s+wa)\s+(?:(?:kuelewa|kuongea|kuzungumza)(?:\s+na\s+(?:kuongea|kuzungumza|kuelewa))?\s+(?:lugha\s+ya\s+)?)?(?:kiswahili|kiingereza|kiganda|kirunyankole|kiacholi)|unajua\s+nini"
    r"|(?:iwe\s+)?ori\s+oha|oha\s+owakukozire|nooruga\s+nkahi|ofisi\s+zaawe\s+ziri\s+nkahi|noobaasa\s+(?:kugamba|kuhurira)\b[^?]{0,30}\b(?:orunyankore|runyankole)"
    r"|in\s+(?:ibedo\s+)?nga|nga\s+ma\s+oyubi|ia\s+kwene|ofici\s+ni\s+tye\s+kwene|itwero\s+(?:loko|niang)\b[^?]{0,30}\b(?:leb\s+acoli|acoli|acholi))\b",
    re.IGNORECASE,
)

_PRESENCE_CHECK_RE = re.compile(
    r"\b((?:are\s+you|r\s+u|a\s+u|u)\s+there"
    r"|anyone\s+there|you\s+there|anybody\s+there"
    r"|(?:are\s+you|r\s+u|a\s+u|u)\s+(?:online|available|listening|awake)"
    r"|oli\s+awo|muli\s+awo|oliyo"
    r"|upo|uko\s+hapo"
    r"|ori\s+aho|okyariho"
    r"|itye\s+kany|pud\s+itye)\b",
    re.IGNORECASE,
)

_EMPATHY_BUSINESS_STRESS_RE = re.compile(
    r"\b((?:scared|afraid|terrified|worried|nervous)\s+(?:of|about|to)\s+(?:start(?:ing)?\s+a\s+business|taxes|ura)"
    r"|business\s+is\s+(?:really\s+)?(?:tough|hard|struggling|bad|failing|slow)"
    r"|feel\s+overwhelmed\s+by\s+tax\w*|taxes\s+are\s+(?:too\s+)?confusing|struggling\s+to\s+survive"
    r"|(?:ntya|ntidde)(?:\s+nnyo)?\s+okutandika\s+(?:bizinensi|obusuubuzi)|embeera\s+ya\s+bizinensi\s+nzibu|obusuubuzi\s+buzibu|sirina\s+ssente\s+zimala"
    r"|nina\s+hofu(?:\s+(?:kubwa|sana))?\s+ya\s+(?:kuanza\s+biashara|kodi)|biashara\s+ni\s+ngumu|kodi\s+zinanitisha|ugumu\s+wa\s+maisha"
    r"|(?:nintina|ntiina)(?:\s+munonga)?\s+o?kutandika\s+obushuubuzi|obushuubuzi\s+bugumire|omushoro\s+niguntiinisa"
    r"|(?:alworo|atye\s+ki\s+lworo\s+me)(?:\s+(?:matek|tutwal))?\s+cako\s+biacara|biacara\s+tek|mucoro\s+miyo\s+lworo)\b",
    re.IGNORECASE,
)

_STATUS_SMALLTALK_RE = re.compile(
    r"^(how\s+are\s+you(?:\s+doing)?(?:\s+today)?|how\s+is\s+it\s+going|how\s+is\s+your\s+day|"
    r"what('s|\s+is)\s+up|how\s+do\s+you\s+do|"
    r"oli\s+otya\s+leero|oli\s+bulungi|gyebaleko\s+leero|muli\s+mutya|"
    r"habari\s+yako\s+ya\s+leo|uko\s+salama(?:\s+leo)?(?:\s+na\s+kazi\s+zinaendeleaje)?|unaendeleaje(?:\s+leo)?|habari\s+za\s+leo|"
    r"agandi\s+erizooba|ori\s+gye|oraire\s+ota\s+erizooba|"
    r"kopango\s+tin|itye\s+maber|itye\s+nining\s+tin)"
    r"(?:\s+(?:mwattu|bambi|nno|sebo|nyabo|rafiki|ndugu|mukama\s+wangye|lamera|friend|today))?[?.! ]*$",
    re.IGNORECASE,
)

_CREATIVE_POEM_RE = re.compile(
    r"\b(write\s+(?:a\s+)?poem\s+about\s+tax\w*|tell\s+me\s+a\s+tax\s+joke|joke\s+about\s+tax\w*"
    r"|wandiika\s+ekitontome|kichekesho\s+kuhusu\s+kodi|andika\s+(?:shairi|utungo)|shairi\s+kuhusu\s+kodi|shairi\s+fupi"
    r"|handiika\s+ekikwate\s+aha\s+mushoro|coo\s+wer\s+me\s+mucoro)\b",
    re.IGNORECASE,
)

_OUT_OF_SCOPE_TANGENT_RE = re.compile(
    r"\b(who\s+(?:won|will\s+win)\s+(?:the\s+)?(?:(?:football\s+)?world\s+cup|premier\s+league|match|game|election)"
    r"|write\s+(?:python|javascript|java|code)\s+for"
    r"|capital\s+of\s+(?:france|germany|japan|canada|usa|kenya)"
    r"|what\s+is\s+the\s+weather\s+in\s+(?:paris|london|new\s+york|kampala)"
    r"|ani\s+yawangula\s+(?:world\s+cup|ekikopo)|nani\s+alishinda\s+(?:kombe\s+la\s+dunia|world\s+cup)"
    r"|ekibuga\s+ekikulu\s+ekya\s+(?:france|bufalansa|japan)|mji\s+mkuu\s+wa\s+(?:ufaransa|ujerumani|japani))\b",
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
    "nyn": (
        "**Omushoro nigwo musingi gw'entunguuka n'obwetengye bw'eihanga ryaitu Uganda.**\n\n"
        "Abantu n'obushuubuzi ku barikushashura omushoro gwabo omu mazima, URA neekoresa esente ezo omu:\n\n"
        "- **Enguuto n'Amateme**: Enguuto z'ekorasi n'ez'omu byaro ezirikwombekwa UNRA omu ihanga ryona.\n"
        "- **Eby'Amagara**: Okutamu emibazi n'ebikoreso omu marwariro ga gavumenti hamwe n'okushashura abashaho.\n"
        "- **Eby'Obwegyese**: Okuhwera amashomero ga UPE, USE, na za yunivasite za gavumenti.\n"
        "- **Obusingye n'Obwerinzi**: Okuhwera ebitongore by'okwerinda ebirikurinda abantu n'obushuubuzi.\n"
        "- **Okweyimiriraho omu By'Esente**: Okukendeeza amabanja g'ahaeri n'okwombeka Uganda erikweyimiriraho ahansi ya Vision 2040.\n\n"
        "Okushashura omushoro ti kiragiro kyonka — n'obuvunaanyizibwa bwaitu twena okwombeka eihanga ryaitu."
    ),
    "ach": (
        "**Mucoro aye guti me dongo lobo ki loc ken me lobowa Uganda.**\n\n"
        "Ka lwak ki luwil jami giculo mucoro gi kit ma myero, URA tiyo ki lim man me:\n\n"
        "- **Yoo ki Abur**: Gero gudi madongo me kolasi ki yoo me caro ma UNRA gero i lobo weng.\n"
        "- **Yot Kom**: Keto yat ki jami tic i ot yat madongo pa gamente kacel ki culu ludaktari.\n"
        "- **Kwan**: Konyo gang kwan pa gamente me UPE, USE ki yunivasiti.\n"
        "- **Kuc ki Gwok**: Konyoaskari ki lurok ma gigwoko kwo pa dano ki biacara.\n"
        "- **Chung Ken i Lim**: Dwoko piny banya ki woko dok gero Uganda ma cung kene i te Vision 2040.\n\n"
        "Culu mucoro pe obedo cik keken — obedo ticwa kacel me gero anyim me lobowa."
    ),
}

_IDENTITY_RESPONSES = {
    "en": (
        "**I am OmusoloSmart, the official AI-powered Taxpayer Assistant for the Uganda Revenue Authority (URA).**\n\n"
        "I was created in Uganda through a collaboration between Makerere University School of Computing and "
        "Informatics Technology, Sunbird AI, and the Uganda Revenue Authority.\n\n"
        "- **Where I Am Located**: As a digital assistant, I operate on URA's secure servers and cloud network, "
        "while URA's physical headquarters is located at URA Tower, Plot 40 Nakawa Industrial Area, Kampala, with stations across Uganda.\n"
        "- **Languages I Speak**: English, Luganda (`Oluganda`), Kiswahili, Runyankole (`Orunyankore`), and Acholi (`Leb Acoli`).\n"
        "- **What I Can Do**: I provide 24/7 instant guidance on tax registration (TIN), calculating your taxes "
        "(PAYE, VAT, Rental Income, Withholding Tax), filing deadlines, customs tariffs, and official URA compliance procedures.\n\n"
        "I am always grounded in verified URA laws and rate tables — how can I assist you with your taxes today?"
    ),
    "lg": (
        "**Nze OmusoloSmart, Omuyambi ow'Enkizo owa Digito ow'ekitongole kya Uganda Revenue Authority (URA) akozesa amagezi ag'obwengula (AI).**\n\n"
        "Nva wano mu Uganda, era nnakolebwa mu Makerere University (School of Computing) ne Sunbird AI wamu ne URA.\n\n"
        "- **Gye Nsanyukira Okubeera**: Nze ndi muyambi wa digito ku mutimbagano, naye ofisi enkulu eya URA esangibwa ku URA Tower e Nakawa mu Kampala, wamu n'amatabi ag'omu bitundu byonna ebya Uganda.\n"
        "- **Ennimi ze Ntegeera era ze Nsobola Okwogera**: Nsobola okwogera n'okutegeera Oluganda, Olungereza, Oluswayiri, Orunyankore, ne Leb Acoli.\n"
        "- **Kye Nnyinza Okukukolera**: Ndi wano essaawa 24/7 okukuyamba ku by'okufuna TIN, okubalira emisolo gyo (PAYE, VAT, Omusolo gw'Ennyumba, WHT), "
        "obudde bw'okuwaayo alipoota, n'engeri y'okukolaganamu ne URA mu mateeka.\n\n"
        "Nkozesa ebiwandiiko ebitongole ebya URA ebikakasiddwa — nkuyambe ntya leero?"
    ),
    "sw": (
        "**Mimi ni OmusoloSmart, Msaidizi Rasmi wa Kidijitali wa Mamlaka ya Mapato ya Uganda (URA) ninayetumia Akili Mnemba (AI).**\n\n"
        "Ninatoka nchini Uganda, niliundwa kupitia ushirikiano kati ya Chuo Kikuu cha Makerere (Shule ya Kompyuta), Sunbird AI, na URA.\n\n"
        "- **Mahali Nilipo**: Kama msaidizi wa kidijitali ninapatikana mtandaoni, lakini makao makuu ya URA yapo URA Tower, Nakawa Industrial Area mjini Kampala, pamoja na ofisi za forodha na kodi kote Uganda.\n"
        "- **Lugha Ninazozungumza**: Kiswahili, Kiingereza, Kiganda, Runyankole, na Acholi.\n"
        "- **Huduma Ninazotoa**: Ninakusaidia saa 24/7 kupata namba ya TIN, kukokotoa kodi zako (PAYE, VAT, Kodi ya Pango, Kodi ya Zuio), "
        "kujua tarehe za mwisho za kuwasilisha marejesho, ushuru wa forodha, na taratibu zote rasmi za URA.\n\n"
        "Majibu yangu yanatokana moja kwa moja na sheria na majedwali rasmi ya URA — ninawezaje kukusaidia leo?"
    ),
    "nyn": (
        "**Ndi OmusoloSmart, Omuhwezi w'Obutongore owa Digito owa Uganda Revenue Authority (URA) orikukoresa AI.**\n\n"
        "Nkakorerwa omuri Uganda kurabira omu kukwatanisa kwa Makerere University (School of Computing), Sunbird AI, na URA.\n\n"
        "- **Ahu Ndi**: Ndi omuhwezi wa digito aha mukura, kwonka ofisi enkuru eya URA eri aha URA Tower, Plot 40 Nakawa Industrial Area, Kampala, hamwe n'ofisi omu bitundu byona ebya Uganda.\n"
        "- **Endimi Ezi Ndikugamba**: Orunyankore, Orungyereza, Oluganda, Kiswahili, na Leb Acoli.\n"
        "- **Ebi Ndikubaasa Kukuhweraho**: Ndi aha shaaha 24/7 kukuhwera aha kwehandiikisa TIN, okubaririra emishoro yaawe (PAYE, VAT, Omushoro gw'Amaju, WHT), "
        "ebiro by'okuheereza ebihandiiko, n'emitwarize ya URA.\n\n"
        "Eby'okugarukamu byangye nibyegamira aha biragiro bitongore bya URA — nimbaasa kukuhwera nta erizooba?"
    ),
    "ach": (
        "**An OmusoloSmart, Lakony me Dijitol pa Uganda Revenue Authority (URA) ma tiyo ki AI.**\n\n"
        "Kiyuba i Uganda ki i kubo cing i kin Makerere University (School of Computing), Sunbird AI, ki URA.\n\n"
        "- **Kama Atye Iye**: Calo lakony me dijitol, atiyo i intanet pa URA, ento gang tic madit me URA tye i URA Tower, Plot 40 Nakawa Industrial Area, Kampala, kacel ki ofici i Uganda weng.\n"
        "- **Leb ma Aloko**: Leb Acoli (Acholi), Leb Munu (English), Oluganda, Kiswahili, ki Orunyankore.\n"
        "- **Gin ma Atwero Konyi Iye**: Atye kany cawa 24/7 me konyi i coyo nying pi TIN, kwano mucoro ni (PAYE, VAT, Mucoro me Ot, WHT), "
        "nino me cwalo ripot, ki cik me URA.\n\n"
        "Lagam na cung i kom cik ki wel mucoro me URA ma kimoko — atwero konyi nining tin?"
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
    "nyn": (
        "Ego, ndi aha kandi nyeteekateekire kukuhwera! Ndi OmusoloSmart, Omuhwezi waawe wa Digito owa URA. "
        "Nkuhwere aha ki erizooba — okwehandiikisa TIN, okuheereza ebihandiiko, okushashura omushoro, nari eby'ensharo?"
    ),
    "ach": (
        "Eyo, atye kany dok atye atera me konyi! An OmusoloSmart, Lakony ni me AI me URA. "
        "Akonyi ki ngo tin — coyo nying pi TIN, cwalo ripot, culu mucoro, onyo lok me wang lobo?"
    ),
}

_BUSINESS_EMPATHY_RESPONSES = {
    "en": (
        "**Starting or operating a business takes courage, and it is completely normal to feel cautious about taxes.**\n\n"
        "Here is the good news that helps many Ugandan entrepreneurs breathe easier:\n\n"
        "1. **You Are Not Taxed Before You Earn**: Income tax applies only to net profits, not your startup capital.\n"
        "2. **Small Business Relief (Presumptive Tax)**: If your annual turnover is under UGX 10,000,000, you are legally exempt from business income tax. For turnover between 10M and 50M, fixed, modest presumptive rates apply without requiring audited books.\n"
        "3. **VAT Threshold Protection**: Under the FY2026-27 VAT Amendment, you are not required to register for VAT unless your annual taxable sales reach **UGX 300,000,000** (up from the previous UGX 150,000,000 threshold, or UGX 75,000,000 in three consecutive months).\n"
        "4. **URA Advisory Support**: URA offers free taxpayer education clinics, Taxpayer Starter Packs, and mobile support.\n\n"
        "Take it step by step. What kind of business are you running or planning to start? I'd be glad to map out exactly what applies to you."
    ),
    "lg": (
        "**Ntegeera nnyo obulumi n'okusoomoozebwa kw'olina. Okutandika n'okukwasaganya bizinensi kyetaagisa obuvumu, era kutegeerekeka okutya emisolo ku ntandikwa.**\n\n"
        "Naye luno lwe lusalira olunaakuyamba okussa ekikkowe:\n\n"
        "1. **Tebakusoloozaako nga Tonnafuna Magoba**: Omusolo gw'ensimbi ez'obusuubuzi gusasulwa ku magoba amatongeze gokka, si ku nsimbi zo ez'entandikwa.\n"
        "2. **Okuyambibwa kw'Obusuubuzi Obutono (Presumptive Tax)**: Singa omutindo gwo ogw'okutunda guba wansi w'obukadde 10 buli mwaka, tosasula musolo gwa bizinensi. Bw'oba wakati w'obukadde 10 ne 50, osasula omusolo mutono ogw'akafunda awatali kwetaagisa bitabo binene by'abalirizi b'ebitabo.\n"
        "3. **Ekkomo lya VAT**: Tewalizibwa kwewandiisa ku VAT okuggyako nga bizinensi yo etuuse ku bukadde **300 buli mwaka** (okuva ku bukadde 150 obwaliwo).\n"
        "4. **Obuyambi bwa URA bwa Bwereere**: URA erina emisomo egy'obwereere n'obutabo obuyamba abasuubuzi abapya.\n\n"
        "Bizinensi ki gy'otandika oba gy'oddukanya? Ndi wano okukunnyonnyola ebisaanidde mu ngeri ennyangu."
    ),
    "sw": (
        "**Kuanzisha na kuendesha biashara kunahitaji ujasiri mkubwa, na ni jambo la kawaida kabisa kuwa na wasiwasi kuhusu kodi.**\n\n"
        "Hapa kuna ukweli utakaokupa utulivu na matumaini:\n\n"
        "1. **Hutozwi Kodi Kabla ya Kupata Faida**: Kodi ya mapato ya biashara hutozwa tu kwenye faida halisi uliyopata, si mtaji wako wa mwanzo.\n"
        "2. **Unafuu kwa Biashara Ndogo (Kodi ya Makadirio)**: Ikiwa mapato yako ya mwaka yako chini ya Shilingi milioni 10, umesamehewa kisheria. Mapato kati ya milioni 10 na milioni 50 yanalipiwa viwango vidogo vilivyowekwa bila kuhitaji mahesabu magumu ya uhasibu.\n"
        "3. **Kikomo cha VAT Kinakulinda**: Huruhusiwi kulazimishwa kujiandikisha kwa VAT hadi mauzo yako ya mwaka yafikie **Shilingi milioni 300** (kutoka milioni 150 za awali).\n"
        "4. **Mafunzo ya Bure ya URA**: URA inatoa semina na miongozo ya bure kwa wajasiriamali wapya kote nchini.\n\n"
        "Unafanya au unapanga biashara ya aina gani? Ninaweza kukuonyesha hatua kwa hatua unachopaswa kufanya kwa wepesi."
    ),
    "nyn": (
        "**Okutandika nari okwebembera obushuubuzi nikyetenga obumanzi, kandi n'ekya buriijo okutina emishoro.**\n\n"
        "Reeba amakuru marungi agarikuhwera abashuubuzi ba Uganda:\n\n"
        "1. **Torikushashura Omushoro Otakabaire na Magoba**: Omushoro gw'entaasya nigushashurwa aha magoba gonka, ti ha sente zaawe z'okutandika.\n"
        "2. **Obuhwezi bw'Obushuubuzi Bukye (Presumptive Tax)**: Entaasya yaawe y'omwaka yaaba eri ahansi ya shiringi obukaikuru 10, torikushashura mushoro gw'obushuubuzi. Ahagati y'obukaikuru 10 na 50, hariho omushoro mukye hatariho kwetenga ebitabo bikuru.\n"
        "3. **Obugarukiro bwa VAT**: Torikugyemeserezibwa kwehandiikisa aha VAT kureka entaasya yaawe yahika **shiringi obukaikuru 300** omwaka (kuruga aha bukaikuru 150).\n"
        "4. **Okwegyesa kw'Obusha okwa URA**: URA neeha obuhabuzi bw'obusha aha bashuubuzi basya.\n\n"
        "Nooyenda kutandika nari nookora bushuubuzi ki? Nimbaasa kukushoboororera idaara aha rindi."
    ),
    "ach": (
        "**Cako onyo doro biacara mito tekcwiny, dok obedo gin maupore me bedo ki lworo i kom mucoro.**\n\n"
        "Man aye lok maber ma konyo lutim biacara i Uganda:\n\n"
        "1. **Pe Kiculi Mucoro Ma Peya Inongo Magoba**: Mucoro me lim ma odonyo kiculo i kom magoba keken, pe i kom lim me cako biacara.\n"
        "2. **Kony pi Biacara Matidi (Presumptive Tax)**: Ka lim ma odonyo i mwaka tye i te cilingi milioni 10, pe iculo mucoro me biacara. I kin milioni 10 naka 50, iculo mucoro matidi ma pe mito buk me kwano lim madongo.\n"
        "3. **Rwom me VAT**: Pe kicoyo nying pi VAT kono lim me cato jami me mwaka oo **cilingi milioni 300** (ki i milioni 150 macon).\n"
        "4. **Pwony me Nono pa URA**: URA miyo pwony ki tela me nono bot lutim biacara.\n\n"
        "Biacara ango ma itye ka timo onyo imito cako? Atwero tito boti rwom ki rwom."
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
    "nyn": (
        "Ndi gye munonga kandi ninkora gye, webare kubuuza!\n\n"
        "Nyeteekateekire kukuhwera aha kwehandiikisa TIN, okubaririra PAYE na VAT, "
        "emishoro y'ensharo, nari ekibuuzo kyona ekya URA erizooba. Nkuhwere aha ki?"
    ),
    "ach": (
        "Atye maber matek dok atye ka tic maber, apwoyo penyo!\n\n"
        "Atye atera me konyi i kom coyo nying pi TIN, kwano mucoro me PAYE ki VAT, "
        "mucoro me wang lobo, onyo lapeny mo keken me URA tin. Imito akonyi i kom ngo?"
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
    "nyn": (
        "**Shiringi y'Eihanga (Ekikwate ky'Omushoro)**\n\n"
        "_Kuruga aha nshozi za Kabale kuhika omu katare ka Mbarara na Kampala,_\n"
        "_Buri shiringi erikushashurwa omu mazima neeyombeka eihanga ryaitu._\n"
        "_Neeyombeka enguuto ezirikugaita ebyaro n'enduga,_\n"
        "_Neeha amarwariro gaitu emibazi erikukiza amagara g'abaana ba Uganda._\n"
        "_Ku turikukwatanisa hamwe nk'abashasuzi b'amazima,_\n"
        "_Nitwombeka Uganda erikweyimiriraho kandi y'ekitiinisa._\n\n"
        "Nimbaasa kukuhwera nta aha buvunaanyizibwa bwawe bw'omushoro erizooba?"
    ),
    "ach": (
        "**Cilingi pi Lobo (Wer me Mucoro)**\n\n"
        "_Ki i godi me Mbale naka i cuk me Gulu ki Kampala,_\n"
        "_Cilingi acel acel ma waculo ki ada gero guti me lobowa._\n"
        "_Yubo gudi ma kubo boma ki caro,_\n"
        "_Miyo yat i ot yat me laro kwo pa lutino me Uganda._\n"
        "_Ka wachung kacel calo lucul mucoro ma gene,_\n"
        "_Wagero Uganda ma cung kene ki woro._\n\n"
        "Atwero konyi nining i lok kom mucoro ni tin?"
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
    "nyn": (
        "N'obu ekibuuzo kyawe kiraabe kiri kirungi, obukugu bwangye nk'**Omuhwezi wa URA** "
        "buri aha biragiro by'emishoro ya Uganda, eby'ensharo, okwehandiikisa TIN, n'obuhereza bwa URA.\n\n"
        "Nimbaasa kukuhwera aha TIN, PAYE, VAT, nari okuheereza ebihandiiko by'omushoro — nkuhwere aha ki?"
    ),
    "ach": (
        "Kadi bed lapeny ni ber, ngec na madit calo **Lakony me URA** "
        "ocung i kom cik me mucoro me Uganda, mucoro me wang lobo, coyo nying pi TIN, ki tic me URA.\n\n"
        "Atwero konyi i kom TIN, PAYE, VAT, onyo cwalo ripot me mucoro — akonyi i kom ngo tin?"
    ),
}


# ---------------------------------------------------------------------------
# Turn Handler
# ---------------------------------------------------------------------------

# A philosophy sentence that also names a tax and a figure is a calculation,
# not small talk. The calculator router runs first; this keeps a miss from
# being answered with the civic essay.
_TAX_AND_FIGURE_RE = re.compile(
    r"(?is)(?:\b(?:vat|vati|paye|wht|withholding|customs|rental|omusolo|omushoro|mucoro|kodi|ushuru)\b.{0,80}\d"
    r"|\d.{0,80}\b(?:vat|vati|paye|wht|withholding|customs|rental|omusolo|omushoro|mucoro|kodi|ushuru)\b)"
)


_CONVERSATIONAL_LOCALE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "nyn",
        re.compile(
            r"\b(?:ahabw['’]?enki|nitushashura|omushoro|omugaso\s+gw['’]?omushoro|iwe\s+ori\s+oha|"
            r"owakukozire|nooruga\s+nkahi|o?runyankore|runyankole|noobaasa\s+(?:kugamba|kuhurira)|"
            r"ori\s+aho|okyariho|(?:nintina|ntiina)|obushuubuzi|"
            r"agandi\s+erizooba|ori\s+gye|oraire\s+ota|ekikwate\s+aha\s+mushoro|oine\s+enyehurira)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ach",
        re.compile(
            r"\b(?:pingo\s+waculo|mucoro|in\s+ibedo\s+nga|oyubi|ia\s+kwene|leb\s+acoli|itwero\s+(?:loko|niang)|"
            r"itye\s+kany|pud\s+itye|(?:alworo|atye\s+ki\s+lworo)|biacara|kopango\s+tin|itye\s+maber|itye\s+nining|"
            r"coo\s+wer|wer\s+me\s+mucoro|itye\s+ki\s+cwiny\s+calo\s+dano)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "lg",
        re.compile(
            r"\b(?:lwaki\s+tusasula|omusolo|(?:gwe|ggwe)\s+ani|yakukola|ova\s+wa|osobola\s+(?:okwogera|okutegeera)|"
            r"oli\s+awo|muli\s+awo|oliyo|(?:ntya|ntidde)(?:\s+nnyo)?\s+okutandika|oli\s+otya|gyebaleko|ekitontome|"
            r"olina\s+enneewulira|oli\s+mulamu\s+nga\s+omuntu)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sw",
        re.compile(
            r"\b(?:kwa\s+nini|tunalipa\s+kodi|wewe\s+ni\s+nani|alikuunda|unatoka\s+wapi|"
            r"unaweza\s+kuongea|una\s+uwezo\s+wa\s+(?:kuelewa|kuongea)|lugha\s+ya\s+kiswahili|"
            r"upo|uko\s+hapo|nina\s+hofu|biashara\s+ni\s+ngumu|habari\s+yako|"
            r"uko\s+salama|andika\s+shairi|shairi\s+(?:kuhusu\s+kodi|fupi)|una\s+hisia\s+au\s+fahamu|je\s+wewe\s+una\s+fahamu)\b",
            re.IGNORECASE,
        ),
    ),
)


def _resolve_conversational_locale(text: str, locale: str = "en") -> str:
    """Resolve the active locale for a conversational turn, honouring both text evidence and thread locale."""
    norm_loc = (locale or "en").strip().lower()
    if norm_loc in ("lg", "sw", "nyn", "ach"):
        try:
            from .language_state import resolve_turn_locale

            decision = resolve_turn_locale(text, history=[{"locale": norm_loc}])
            if decision.locale in ("en", "lg", "sw", "nyn", "ach"):
                return decision.locale
        except Exception:
            pass
        return norm_loc
    for cand_loc, pat in _CONVERSATIONAL_LOCALE_PATTERNS:
        if pat.search(text):
            return cand_loc
    courtesy_loc = _resolve_courtesy_locale(text, norm_loc)
    if courtesy_loc != norm_loc:
        return courtesy_loc
    try:
        from .query import detect_language

        det = detect_language(text, default_lang=norm_loc or "en")
        if det in ("lg", "sw", "nyn", "ach"):
            return det
    except Exception:
        pass
    return norm_loc if norm_loc in ("en", "lg", "sw", "nyn", "ach") else "en"


def handle_conversational_turn(message: str, locale: str = "en") -> ConversationalResult | None:
    """Evaluate whether *message* is a natural dialog turn, and return rich response.

    Returns None if the message should proceed to deterministic calculators or RAG.
    """
    text = (message or "").strip()
    if not text or _TAX_AND_FIGURE_RE.search(text) or is_model_welfare_query(text):
        return None

    eff_loc = _resolve_conversational_locale(text, locale)

    # 1. Civic / Philosophy ("Why do we pay taxes?")
    if _CIVIC_PHILOSOPHY_RE.search(text):
        reply = _CIVIC_RESPONSES.get(eff_loc, _CIVIC_RESPONSES["en"])
        actions = {
            "en": ["Learn about VAT in Uganda", "How does PAYE support services?", "Register for a TIN"],
            "lg": ["Yiga ku musolo gwa VAT", "PAYE ekozesebwa etya?", "Funa TIN yo"],
            "sw": ["Jifunze kuhusu VAT", "PAYE inafanya kazi vipi?", "Pata namba ya TIN"],
            "nyn": ["Yega aha mushoro gwa VAT", "PAYE neekora eta?", "Tunga TIN yaawe"],
            "ach": ["Nge i kom mucoro me VAT", "PAYE tiyo nining?", "Nong TIN ni"],
        }.get(eff_loc, ["Learn about VAT in Uganda", "How does PAYE support services?", "Register for a TIN"])
        return ConversationalResult(reply, eff_loc, "civic_philosophy", actions)

    # 2. Identity / Origin / Location / Multilingual abilities ("Who made you?", "Where are you from?", "Can you speak Luganda?")
    if _IDENTITY_ORIGINS_RE.search(text):
        reply = _IDENTITY_RESPONSES.get(eff_loc, _IDENTITY_RESPONSES["en"])
        actions = {
            "en": ["Ask about TIN registration", "Calculate income tax", "Contact URA Contact Centre"],
            "lg": ["Okwewandiisa ku TIN", "Okubalirira omusolo", "Tuukirira URA"],
            "sw": ["Usajili wa TIN", "Kukokotoa kodi", "Wasiliana na URA"],
            "nyn": ["Okwehandiikisa TIN", "Okubaririra omushoro", "Hikirira URA"],
            "ach": ["Coyo nying pi TIN", "Kwano mucoro", "Kub ki URA"],
        }.get(eff_loc, ["Ask about TIN registration", "Calculate income tax", "Contact URA Contact Centre"])
        return ConversationalResult(reply, eff_loc, "identity_capability", actions)

    # 2b. Presence check ("Are you there?", "U there?", "Oli awo?", "Upo?")
    if _PRESENCE_CHECK_RE.search(text):
        reply = _PRESENCE_RESPONSES.get(eff_loc, _PRESENCE_RESPONSES["en"])
        actions = {
            "en": ["Ask about TIN registration", "Calculate income tax", "Contact URA Contact Centre"],
            "lg": ["Okwewandiisa ku TIN", "Okubalirira omusolo", "Tuukirira URA"],
            "sw": ["Usajili wa TIN", "Kukokotoa kodi", "Wasiliana na URA"],
            "nyn": ["Okwehandiikisa TIN", "Okubaririra omushoro", "Hikirira URA"],
            "ach": ["Coyo nying pi TIN", "Kwano mucoro", "Kub ki URA"],
        }.get(eff_loc, ["Ask about TIN registration", "Calculate income tax", "Contact URA Contact Centre"])
        return ConversationalResult(reply, eff_loc, "presence_check", actions)

    # 3. Emotional distress / Startup business fear ("I'm scared of starting a business")
    if _EMPATHY_BUSINESS_STRESS_RE.search(text):
        reply = _BUSINESS_EMPATHY_RESPONSES.get(eff_loc, _BUSINESS_EMPATHY_RESPONSES["en"])
        actions = {
            "en": ["What is presumptive tax?", "Who must register for VAT?", "Get a Taxpayer Starter Pack"],
            "lg": ["Presumptive tax kye ki?", "Bizinensi ki eziri ku VAT?", "Okutandika obusuubuzi"],
            "sw": ["Kodi ya makadirio ni nini?", "Nani anapaswa kusajili VAT?", "Mwongozo wa wajasiriamali"],
            "nyn": ["Presumptive tax niki?", "Bushuubuzi ki oburi aha VAT?", "Okutandika obushuubuzi"],
            "ach": ["Presumptive tax obedo ngo?", "Biacara mene ma myero oco VAT?", "Cako biacara"],
        }.get(eff_loc, ["What is presumptive tax?", "Who must register for VAT?", "Get a Taxpayer Starter Pack"])
        return ConversationalResult(reply, eff_loc, "business_empathy", actions)

    # 4. Status / How are you doing? ("How are you doing today?")
    if _STATUS_SMALLTALK_RE.search(text):
        reply = _STATUS_RESPONSES.get(eff_loc, _STATUS_RESPONSES["en"])
        actions = {
            "en": ["Ask about TIN registration", "Learn about VAT", "Check filing deadlines"],
            "lg": ["Funa TIN", "Yiga ku VAT", "Obudde bw'okusasula"],
            "sw": ["Pata TIN", "Jifunze kuhusu VAT", "Tarehe za kuwasilisha"],
            "nyn": ["Tunga TIN", "Yega aha VAT", "Ebiro by'okuheereza"],
            "ach": ["Nong TIN", "Nge i kom VAT", "Nino me cwalo ripot"],
        }.get(eff_loc, ["Ask about TIN registration", "Learn about VAT", "Check filing deadlines"])
        return ConversationalResult(reply, eff_loc, "status_smalltalk", actions)

    # 5. Creative / Poem / Humor
    if _CREATIVE_POEM_RE.search(text):
        reply = _POEM_RESPONSES.get(eff_loc, _POEM_RESPONSES["en"])
        actions = {
            "en": ["Why do we pay taxes?", "How does PAYE work?", "Register for a TIN"],
            "lg": ["Lwaki tusasula omusolo?", "PAYE ekola etya?", "Funa TIN yo"],
            "sw": ["Kwa nini tunalipa kodi?", "PAYE inafanya kazi vipi?", "Pata namba ya TIN"],
            "nyn": ["Ahabw'enki nitushashura omushoro?", "PAYE neekora eta?", "Tunga TIN yaawe"],
            "ach": ["Pingo waculo mucoro?", "PAYE tiyo nining?", "Nong TIN ni"],
        }.get(eff_loc, ["Why do we pay taxes?", "How does PAYE work?", "Register for a TIN"])
        return ConversationalResult(reply, eff_loc, "creative_poem", actions)

    # 6. Tangential Out-of-Scope (Divert gracefully back to URA mandate)
    if _OUT_OF_SCOPE_TANGENT_RE.search(text):
        reply = _DIVERT_RESPONSES.get(eff_loc, _DIVERT_RESPONSES["en"])
        actions = {
            "en": ["Register for a TIN", "Calculate PAYE tax", "Customs import duty rates"],
            "lg": ["Okwewandiisa ku TIN", "Okubalirira PAYE", "Emisolo gy'ebyamaguzi"],
            "sw": ["Usajili wa TIN", "Kukokotoa PAYE", "Ushuru wa forodha"],
            "nyn": ["Okwehandiikisa TIN", "Okubaririra PAYE", "Emishoro y'ensharo"],
            "ach": ["Coyo nying pi TIN", "Kwano PAYE", "Mucoro me wang lobo"],
        }.get(eff_loc, ["Register for a TIN", "Calculate PAYE tax", "Customs import duty rates"])
        return ConversationalResult(reply, eff_loc, "out_of_scope_divert", actions)

    return None
