"""Smart Callback Scheduler & Asia/Kolkata Festival Date Parser for SPX Voice.

Parses natural language relative dates (e.g. 'tomorrow morning', 'after Dussehra',
'this Saturday afternoon') into precise Asia/Kolkata timezone timestamps.
Provides culturally nuanced multilingual consultative scripts in EN, Hinglish, and Tenglish.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
from loguru import logger

IST_TZ = ZoneInfo("Asia/Kolkata")

# Reference Indian festival calendars (approximate/canonical dates for 2026/2027)
INDIAN_FESTIVALS_2026_2027: Dict[str, date] = {
    "makar sankranti": date(2027, 1, 14),
    "sankranti": date(2027, 1, 14),
    "pongal": date(2027, 1, 14),
    "republic day": date(2027, 1, 26),
    "maha shivaratri": date(2027, 3, 7),
    "holi": date(2027, 3, 24),
    "ugadi": date(2027, 4, 8),
    "eid": date(2027, 4, 11),
    "independence day": date(2027, 8, 15),
    "ganesh chaturthi": date(2027, 9, 14),
    "vinayaka chavithi": date(2027, 9, 14),
    "dussehra": date(2026, 10, 20),
    "vijayadashami": date(2026, 10, 20),
    "diwali": date(2026, 11, 8),
    "deepavali": date(2026, 11, 8),
    "christmas": date(2026, 12, 25),
    "new year": date(2027, 1, 1),
}


def parse_indian_relative_callback_time(
    natural_phrase: str,
    reference_now: Optional[datetime] = None,
) -> Tuple[datetime, str]:
    """Parses natural speech into an exact Asia/Kolkata timestamp.

    Returns:
        Tuple of (target_datetime: datetime in IST, explanation: str)
    """
    now = reference_now or datetime.now(IST_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST_TZ)
    else:
        now = now.astimezone(IST_TZ)

    text = natural_phrase.lower().strip()
    target_date = now.date()
    target_hour = 11  # Default comfortable business hour (11:00 AM IST)
    target_minute = 0
    matched_reason = "standard_schedule"

    # 1. Parse time of day
    if "morning" in text or "kal subah" in text or "repu morning" in text:
        target_hour = 10
        target_minute = 30
    elif "afternoon" in text or "dopahar" in text or "madhyanam" in text:
        target_hour = 14
        target_minute = 30
    elif "evening" in text or "shaam" in text or "saayantram" in text:
        target_hour = 17
        target_minute = 0
    elif "post lunch" in text:
        target_hour = 15
        target_minute = 0

    # Specific hour regex (e.g. 4 pm, 11 am)
    hour_match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text)
    if hour_match:
        h = int(hour_match.group(1))
        meridiem = hour_match.group(2)
        if meridiem == "pm" and h < 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        target_hour = h
        target_minute = 0

    # 2. Check for Indian Festivals (e.g. "after Dussehra", "after Diwali")
    for festival, fest_date in INDIAN_FESTIVALS_2026_2027.items():
        if festival in text:
            if "after" in text or "tarvata" in text or "baad" in text:
                target_date = fest_date + timedelta(days=1)
                matched_reason = f"day_after_{festival.replace(' ', '_')}"
            else:
                target_date = fest_date
                matched_reason = f"on_{festival.replace(' ', '_')}"
            break
    else:
        # 3. Standard relative date parsing
        if "day after tomorrow" in text or "parso" in text or "ellundi" in text:
            target_date = now.date() + timedelta(days=2)
            matched_reason = "day_after_tomorrow"
        elif "tomorrow" in text or "kal" in text or "repu" in text:
            target_date = now.date() + timedelta(days=1)
            matched_reason = "tomorrow"
        elif "today" in text or "aaj" in text or "ee roju" in text:
            target_date = now.date()
            matched_reason = "today"
        elif "saturday" in text or "shanivaram" in text:
            days_ahead = (5 - now.weekday()) % 7
            if days_ahead == 0 and now.hour >= 17:
                days_ahead = 7
            target_date = now.date() + timedelta(days=days_ahead)
            matched_reason = "upcoming_saturday"
        elif "sunday" in text or "aadivaram" in text:
            days_ahead = (6 - now.weekday()) % 7
            if days_ahead == 0 and now.hour >= 17:
                days_ahead = 7
            target_date = now.date() + timedelta(days=days_ahead)
            matched_reason = "upcoming_sunday"
        elif "monday" in text or "somavaram" in text:
            days_ahead = (0 - now.weekday()) % 7
            if days_ahead <= 0:
                days_ahead += 7
            target_date = now.date() + timedelta(days=days_ahead)
            matched_reason = "next_monday"
        else:
            # Default to tomorrow if ambiguous
            target_date = now.date() + timedelta(days=1)
            matched_reason = "default_next_business_day"

    # Business hours clamp: 9:30 AM to 7:30 PM IST
    target_hour = max(9, min(19, target_hour))

    result_dt = datetime.combine(
        target_date,
        time(hour=target_hour, minute=target_minute),
        tzinfo=IST_TZ,
    )

    return result_dt, matched_reason


# Culturally respectful, consultative multilingual scripts
CONSULTATIVE_CALLBACK_SCRIPTS: Dict[str, Dict[str, str]] = {
    "en": {
        "confirm": "Certainly. I have scheduled our senior executive to call you back on {formatted_time}. Thank you for your time!",
        "reschedule": "No problem at all. We will connect with you after the holidays on {formatted_time}. Have a great day!",
    },
    "hinglish": {
        "confirm": "Zaroor! Maine hamare senior executive ke liye {formatted_time} ko call schedule kar diya hai. Baat karke bohot achha laga!",
        "reschedule": "Bilkul koi baat nahi. Festivals ke baad hum {formatted_time} ko aapse connect karenge. Aapka din shubh ho!",
    },
    "tenglish": {
        "confirm": "Tappakunda andi! Ma senior executive meeku {formatted_time} ki call cheselaa schedule chesanu. Thank you!",
        "reschedule": "Meeru busy unte no problem andi. Festivals ayyaka {formatted_time} ki malli connect avthamu. Have a nice day!",
    },
}

