from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LanguagePack:
    code: str
    display_name: str
    locale: str
    default_voice: str
    greeting: str
    silence_stage1: str
    silence_stage2: str
    silence_stage3: str
    backchannel_keywords: tuple[str, ...]
    system_prompt_language_rule: str


LANGUAGE_PACKS: dict[str, LanguagePack] = {
    "en": LanguagePack(
        code="en",
        display_name="English",
        locale="en-IN",
        default_voice="Puck",
        greeting="Hi, thanks for calling. How can I assist you today?",
        silence_stage1="Take your time, I'm listening.",
        silence_stage2="Are you still there? Please let me know if you can hear me.",
        silence_stage3="It seems we might be disconnected. Thank you for your time. Goodbye.",
        backchannel_keywords=(
            "yeah", "yes", "yep", "uh-huh", "mhm", "okay", "ok", "right", "sure", "got it"
        ),
        system_prompt_language_rule=(
            "Speak clearly in polite, natural conversational English with standard Indian English pronunciation."
        ),
    ),
    "hi": LanguagePack(
        code="hi",
        display_name="Hindi",
        locale="hi-IN",
        default_voice="Kore",
        greeting="Namaste! Call karne ke liye dhanyavad. Main aapki kya sahayata kar sakta hoon?",
        silence_stage1="Aap aaram se boliye, main sun raha hoon.",
        silence_stage2="Kya aap mujhe sun paa rahe hain? Kripya bataiye.",
        silence_stage3="Lagta hai humara sampark toot gaya hai. Samay dene ke liye dhanyavad. Alvida.",
        backchannel_keywords=(
            "ha", "haan", "theek", "theek hai", "achha", "accha", "sahi", "ji", "ji haan", "bolo"
        ),
        system_prompt_language_rule=(
            "Shuddh evm saral Hindi mein baat karein. Vyavaharik bhasha ka upayog karein aur sunne wale ko samman dein."
        ),
    ),
    "te": LanguagePack(
        code="te",
        display_name="Telugu",
        locale="te-IN",
        default_voice="Kore",
        greeting="Namaskaram! Call chesinanduku dhanyavaadaalu. Nenu meeku ela sahayapadagalanu?",
        silence_stage1="Meeru aalochinchi cheppandi, nenu vintunnanu.",
        silence_stage2="Meeru line lo unnara? Dayachesi nenu matladedi vinapadithe cheppandi.",
        silence_stage3="Mee vaipu nundi maata vinapadatam ledu. Mee samayaniki dhanyavaadaalu. Selavu.",
        backchannel_keywords=(
            "avunu", "sare", "avuna", "alage", "alaage", "anthey", "kadu", "cheppandi", "haa"
        ),
        system_prompt_language_rule=(
            "Mariyadaga, spashtamaina Telugulo matladandi. Sahajamina vaduka bhashanu upayoginchandi."
        ),
    ),
    "hi-en": LanguagePack(
        code="hi-en",
        display_name="Hindi-English (Hinglish)",
        locale="hi-IN",
        default_voice="Puck",
        greeting="Namaste! Thanks for calling. Main aapki kya help kar sakta hoon?",
        silence_stage1="Take your time, main sun raha hoon.",
        silence_stage2="Are you still there? Kya aap mujhe sun paa rahe hain?",
        silence_stage3="Lagta hai connection issue hai. Thanks for calling, have a great day!",
        backchannel_keywords=(
            "yeah", "theek hai", "okay", "haan", "sahi", "got it", "achha", "right"
        ),
        system_prompt_language_rule=(
            "Respond naturally in conversational Hinglish (blend of conversational Hindi and common English terms). "
            "Keep technical and business terms in English while conversational framing stays in friendly Hindi."
        ),
    ),
    "te-en": LanguagePack(
        code="te-en",
        display_name="Telugu-English (Tenglish)",
        locale="te-IN",
        default_voice="Puck",
        greeting="Namaskaram! Thanks for calling. Nenu meeku ela help cheyagalanu?",
        silence_stage1="Take your time, nenu vintunnanu.",
        silence_stage2="Are you still there? Nenu matladedi vinapadutunda?",
        silence_stage3="Looks like connection drop ayyindi. Thanks for calling, bye!",
        backchannel_keywords=(
            "yeah", "sare", "okay", "avunu", "alage", "got it", "right", "anthey"
        ),
        system_prompt_language_rule=(
            "Respond naturally in conversational Tenglish (blend of Telugu and English common expressions). "
            "Keep technical, product, and business terms in English while connecting phrases remain natural Telugu."
        ),
    ),
}


def get_language_pack(language_code: str | None) -> LanguagePack:
    """Resolve LanguagePack by code, falling back to English."""
    if not language_code:
        return LANGUAGE_PACKS["en"]
    normalized = language_code.strip().lower()
    if normalized in LANGUAGE_PACKS:
        return LANGUAGE_PACKS[normalized]
    # Check 2-letter base code (e.g. "te-IN" -> "te")
    base = normalized.split("-")[0]
    return LANGUAGE_PACKS.get(base, LANGUAGE_PACKS["en"])

