"""Structured AI & Rule-Based Lead Intelligence Engine for Indian Businesses."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from loguru import logger

from api.services.business.callback_scheduler import parse_indian_relative_callback_time


def resolve_industry_from_context_and_intent(
    business_context: Optional[Dict[str, Any]] = None,
    intent: Optional[str] = None,
    full_text: Optional[str] = None,
) -> str:
    """Resolve industry category using business_context, detected intent, or keyword cues.
    
    Returns one of:
      'Healthcare', 'Fitness', 'Real Estate', 'Automotive', 'Car Rentals',
      'Beauty & Wellness', 'Retail', 'Renewable Energy', 'Home Interiors',
      'Hospitality', or 'General Business'.
    """
    biz_ctx = business_context or {}
    raw_ind = (biz_ctx.get("industry") or biz_ctx.get("name") or biz_ctx.get("id") or "").lower()

    if any(k in raw_ind for k in ["health", "clinic", "doctor", "medical", "hospital", "dental"]):
        return "Healthcare"
    if any(k in raw_ind for k in ["gym", "fit", "workout"]):
        return "Fitness"
    if any(k in raw_ind for k in ["real estate", "housing", "property", "realty"]):
        return "Real Estate"
    if any(k in raw_ind for k in ["rental", "self-drive", "self drive"]):
        return "Car Rentals"
    if any(k in raw_ind for k in ["automotive", "car dealership", "dealership", "motor"]):
        return "Automotive"
    if any(k in raw_ind for k in ["salon", "spa", "beauty", "grooming"]):
        return "Beauty & Wellness"
    if any(k in raw_ind for k in ["retail", "supermarket", "electronics", "store"]):
        return "Retail"
    if any(k in raw_ind for k in ["solar", "energy", "renewable"]):
        return "Renewable Energy"
    if any(k in raw_ind for k in ["interior", "architecture", "design"]):
        return "Home Interiors"
    if any(k in raw_ind for k in ["hotel", "resort", "stay"]):
        return "Hospitality"

    # 2. Check detected intent
    it = (intent or "").lower()
    if "doctor" in it or "clinic" in it or "medical" in it:
        return "Healthcare"
    if "gym" in it or "workout" in it or "fitness" in it:
        return "Fitness"
    if "site visit" in it or "property" in it:
        return "Real Estate"
    if "self-drive" in it or "vehicle reservation" in it:
        return "Car Rentals"
    if "test drive" in it or "new vehicle" in it:
        return "Automotive"
    if "salon" in it or "grooming" in it or "spa" in it:
        return "Beauty & Wellness"
    if "solar" in it or "subsidy" in it:
        return "Renewable Energy"
    if "interior" in it:
        return "Home Interiors"
    if "retail" in it or "price & stock" in it:
        return "Retail"
    if "hotel" in it or "resort" in it:
        return "Hospitality"

    # 3. Check dialogue text
    txt = (full_text or "").lower()
    if any(k in txt for k in ["doctor", "clinic", "fever", "consultation", "patient", "medical", "dental"]):
        return "Healthcare"
    if any(k in txt for k in ["gym", "workout", "fitness", "trainer", "muscle", "crossfit"]):
        return "Fitness"
    if any(k in txt for k in ["bhk", "villa", "flat", "site visit", "property visit", "sq yards", "rera"]):
        return "Real Estate"
    if any(k in txt for k in ["car rental", "self drive", "rent a car", "hire car", "innova"]):
        return "Car Rentals"
    if any(k in txt for k in ["test drive", "dealership", "new car", "exchange bonus"]):
        return "Automotive"
    if any(k in txt for k in ["haircut", "facial", "salon", "spa", "hair spa", "bridal"]):
        return "Beauty & Wellness"
    if any(k in txt for k in ["solar", "rooftop", "current bill", "electricity bill", "surya ghar"]):
        return "Renewable Energy"
    if any(k in txt for k in ["modular kitchen", "wardrobe", "false ceiling", "home interior"]):
        return "Home Interiors"
    if any(k in txt for k in ["washing machine", "refrigerator", "smart tv", "appliances"]):
        return "Retail"

    return "General Business"


def get_industry_recommended_action(
    classification: str,
    industry: Optional[str] = None,
    intent: Optional[str] = None,
    preferred_slot: Optional[str] = None,
) -> str:
    """Produce industry-appropriate next actions.
    
    If industry is unrecognized or generic, produces safe generic recommendations for staff review.
    Never fabricates appointments, promises availability, or triggers automated messages.
    """
    if classification == "Do Not Contact":
        return "Compliance: Add phone number to Suppression / Do Not Call registry."
    if classification == "Human Assistance Required":
        return "Escalation: Connect customer with staff supervisor or manager to address specialized inquiry."
    if classification == "Voicemail / No Human Answer":
        return "No Action: Automated voicemail detected; no human response recorded."
    if classification == "Not Interested":
        return "Low Priority: Record response; do not initiate follow-up outreach."

    if classification == "Callback Requested":
        if preferred_slot:
            return f"Follow-up: Dial caller back at requested time slot ({preferred_slot}) to assist with their enquiry."
        return "Follow-up: Dial caller back at requested time slot to assist with their enquiry."

    ind = (industry or "").strip()

    if classification == "Hot":
        if ind == "Healthcare":
            return "Immediate Priority: Medical staff to contact patient within 1 hour to verify consultation requirements and schedule slot."
        elif ind == "Fitness":
            return "Immediate Priority: Fitness consultant to reach out within 1 hour to schedule complimentary trial workout session."
        elif ind == "Real Estate":
            return "Immediate Priority: Assign property advisor to confirm site visit / consultation slot within 1 hour."
        elif ind == "Automotive":
            return "Immediate Priority: Product specialist to connect within 1 hour to schedule doorstep test drive or showroom visit."
        elif ind == "Car Rentals":
            return "Immediate Priority: Fleet desk to confirm vehicle reservation and coordinate pickup/delivery details within 1 hour."
        elif ind == "Beauty & Wellness":
            return "Immediate Priority: Front desk to contact client within 1 hour to confirm salon appointment and stylist preference."
        elif ind == "Renewable Energy":
            return "Immediate Priority: Solar technical engineer to contact within 1 hour to schedule rooftop feasibility survey."
        elif ind == "Home Interiors":
            return "Immediate Priority: Interior design consultant to connect within 1 hour to schedule 3D experience center session."
        elif ind == "Retail":
            return "Immediate Priority: Store representative to verify product stock and contact customer within 1 hour."
        elif ind == "Hospitality":
            return "Immediate Priority: Reservations desk to confirm suite availability and guest requirements within 1 hour."
        else:
            return "Immediate Priority: Staff to contact customer within 1 hour to verify request and provide details."

    elif classification == "Warm":
        if ind == "Healthcare":
            return "Patient Follow-up: Clinic staff to review consultation inquiry and provide department details or schedule appointment."
        elif ind == "Fitness":
            return "Member Nurture: Share membership plans and trial options, schedule follow-up check-in within 48 hours."
        elif ind == "Real Estate":
            return "Nurture: Share project details and property specifications, schedule check-in within 48 hours."
        elif ind == "Automotive":
            return "Prospect Nurture: Share vehicle specifications and pricing breakdown, follow up within 48 hours."
        elif ind == "Car Rentals":
            return "Follow-up: Share fleet availability and transparent rental tariff, follow up within 24-48 hours."
        elif ind == "Beauty & Wellness":
            return "Client Nurture: Share service menu and treatment options, assist with booking within 48 hours."
        elif ind == "Renewable Energy":
            return "Consultation Follow-up: Share solar subsidy calculations and estimated bill savings, follow up within 48 hours."
        elif ind == "Home Interiors":
            return "Design Nurture: Share portfolio and finish catalogs, schedule design consultation follow-up within 48 hours."
        elif ind == "Retail":
            return "Customer Follow-up: Share product pricing, festival offers, and store reservation options."
        elif ind == "Hospitality":
            return "Guest Follow-up: Share room amenities, package options, and seasonal rates within 48 hours."
        else:
            return "Staff Review: Review customer inquiry and share relevant service information or schedule follow-up."

    # Cold / Low Priority / Fallback
    if ind == "Healthcare":
        return "General Inquiry: Clinic contact logged; provide hours and department assistance upon request."
    elif ind == "Fitness":
        return "Low Priority: Keep in contact for seasonal fitness promotions and club open days."
    elif ind == "Real Estate":
        return "Low Priority: Maintain contact for future project launches and property updates."
    elif ind == "Automotive":
        return "Low Priority: Keep on file for upcoming seasonal offers and model launches."
    elif ind == "Beauty & Wellness":
        return "Low Priority: Share upcoming seasonal beauty packages and salon offers."
    else:
        return "Staff Review: Low-priority enquiry logged for team review."


def _normalize_turn_text(text: str) -> str:
    """Collapse embedded newlines so one turn is exactly one transcript line."""
    return re.sub(r"\s*\n\s*", " ", text or "").strip()


def _message_text(item: Any) -> str:
    if not item:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return _message_text(item.get("text") or item.get("content") or item.get("message") or "")
    if isinstance(item, list):
        return " ".join([_message_text(x) for x in item if x]).strip()
    return str(item or "")


def _iso_timestamp(value: Any) -> Optional[str]:
    """ISO-8601 UTC for a *recorded* timestamp (epoch seconds or ISO text); never invented."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            if value <= 0:
                return None
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        text = str(value).strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _is_caller_role(role: Any) -> bool:
    return str(role or "User").lower() in ("user", "caller", "customer")


def extract_timed_turns(logs: Any) -> List[Dict[str, Any]]:
    """Ordered ``{label, text, timestamp}`` turns from any supported log shape.

    ``label`` is ``"Caller"`` / ``"Agent"`` (or ``None`` for already-flattened text lines).
    ``timestamp`` is present only when the source recorded one. System/developer
    messages are never part of a transcript.
    """
    if not logs:
        return []
    if isinstance(logs, str):
        return [{"label": None, "text": line, "timestamp": None} for line in logs.split("\n") if line.strip()]

    turns: List[Dict[str, Any]] = []

    def add_message(m: Dict[str, Any]) -> None:
        role = m.get("role") or m.get("speaker") or "User"
        if str(role).lower() in ("system", "developer"):
            return
        text = _normalize_turn_text(_message_text(m.get("text") or m.get("content") or m.get("message")))
        if text:
            turns.append(
                {
                    "label": "Caller" if _is_caller_role(role) else "Agent",
                    "text": text,
                    "timestamp": _iso_timestamp(m.get("created_at") or m.get("timestamp")),
                }
            )

    if isinstance(logs, dict):
        if "livekit_history" in logs:
            lkh = logs["livekit_history"]
            if isinstance(lkh, dict):
                logs = {**lkh, **logs}
            elif isinstance(lkh, list):
                logs = {"messages": lkh}
        messages = logs.get("messages") or logs.get("transcript") or logs.get("items") or []
        if isinstance(messages, list):
            for m in messages:
                if isinstance(m, dict):
                    add_message(m)
                elif isinstance(m, str):
                    turns.append({"label": None, "text": m, "timestamp": None})
        text_chat = logs.get("turns") or []
        if isinstance(text_chat, list):
            for t in text_chat:
                if isinstance(t, dict):
                    caller_text = _message_text(t.get("user_message") or t.get("caller"))
                    agent_text = _message_text(t.get("agent_message") or t.get("agent"))
                    if caller_text:
                        turns.append({"label": "Caller", "text": caller_text, "timestamp": None})
                    if agent_text:
                        turns.append({"label": "Agent", "text": agent_text, "timestamp": None})
    elif isinstance(logs, list):
        for item in logs:
            if isinstance(item, dict):
                add_message(item)
            elif isinstance(item, str):
                turns.append({"label": None, "text": item, "timestamp": None})
    return turns


def extract_transcript_from_logs(logs: Any) -> str:
    """Normalize raw workflow run logs or session data into clean speaker turns."""
    if isinstance(logs, str):
        return logs
    lines = [
        f"{t['label']}: {t['text']}" if t["label"] else t["text"] for t in extract_timed_turns(logs)
    ]
    return "\n".join(lines).strip()


ANALYZER_VERSION = "cp02-rules-v1"

_AFFIRMATIVE_RE = re.compile(
    r"\b(yes|yeah|yep|yup|correct|right|exactly|avunu|avunandi|sari|haan|ha|ji|hanji)\b"
)

# Places whose spellings are easily confused by speech recognition. A mention is
# only recorded as a confirmed location when the transcript shows the caller
# explicitly confirming it; otherwise it stays an *unconfirmed* observation.
_AMBIGUOUS_PLACE_GROUPS: List[tuple] = [
    ("madhapur", "madhurawada", "madhurwada", "madhuravada"),
]

_KNOWN_LOCATIONS = (
    "gachibowli|kondapur|kokapet|miyapur|madhapur|banjara hills|jubilee hills|"
    "kukatpally|ameerpet|dilsukhnagar|secunderabad|airport|hyderabad|bengaluru|bangalore"
)
_LOCATION_RE = re.compile(rf"\b({_KNOWN_LOCATIONS})\b")
_BUDGET_RE = re.compile(
    r"\b(\d+(\.\d+)?\s*(cr|crore|crores|lakh|lakhs|lac|lacs|k|thousand))\b"
)

# (label, strong pattern, optional weak pattern counted only with a strong hit, priority)
_INTENT_RULES: List[tuple] = [
    (
        "Site Visit & Property Booking",
        r"\b(site visit|property visit|see (the )?flat|visit (the )?site|flat visit)\b",
        None,
        3,
    ),
    (
        "Property Purchase Enquiry",
        r"\b([1-5]\s*-?\s*bhk|[1-5]\s*-?\s*bedroom|flats?|apartments?|villas?|plots?|propert(y|ies)|real estate|sq\.?\s?(ft|yards?))\b",
        None,
        2,
    ),
    (
        "Doctor Consultation & Appointment",
        r"\b(doctors?|clinic|fever|consultation|consult|medical|dentist|dental|toothache|check-?up|physician|patient)\b",
        None,
        2,
    ),
    ("Gym Membership & Trial Workout", r"\b(membership|gym|workout|fitness)\b", r"\b(trial)\b", 2),
    (
        "Self-Drive Vehicle Reservation",
        r"\b(car rental|self[- ]drive|rent a car|hire (a )?car|innova)\b",
        None,
        2,
    ),
    (
        "Solar Rooftop Assessment & Subsidy",
        r"\b(solar|rooftop|current bill|electricity bill|subsidy)\b",
        None,
        2,
    ),
    (
        "Home Interior Design Consultation",
        r"\b(interiors?|modular kitchen|wardrobe|renovation)\b",
        r"\b(design)\b",
        2,
    ),
    (
        "New Vehicle Test Drive & Purchase",
        r"\b(test drive|new car|buy (a )?car|exchange bonus)\b",
        None,
        2,
    ),
    (
        "Salon Grooming & Spa Appointment",
        r"\b(haircut|facial|salon|spa|hair spa|bridal)\b",
        None,
        2,
    ),
    (
        "Retail Product Price & Stock Inquiry",
        r"\b(tv|television|ac|air conditioner|refrigerator|fridge|washing machine|laptop|mobile phone)\b",
        r"\b(stock|price|prices|cost|discount)\b",
        2,
    ),
    (
        "Hotel & Resort Suite Reservation",
        r"\b(hotel|resort|suite|palace|check-?in|check-?out)\b",
        r"\b(room|rooms|stay|anniversary)\b",
        2,
    ),
]
_PRICE_ONLY_RE = re.compile(r"\b(price|prices|cost|how much|rate|rates)\b")

# Industry implied by a customer intent label; used only to detect when the
# customer's enquiry does not match the business's configured industry.
_INTENT_INDUSTRY = {
    "Site Visit & Property Booking": "Real Estate",
    "Property Purchase Enquiry": "Real Estate",
    "Doctor Consultation & Appointment": "Healthcare",
    "Gym Membership & Trial Workout": "Fitness",
    "Self-Drive Vehicle Reservation": "Car Rentals",
    "Solar Rooftop Assessment & Subsidy": "Renewable Energy",
    "Home Interior Design Consultation": "Home Interiors",
    "New Vehicle Test Drive & Purchase": "Automotive",
    "Salon Grooming & Spa Appointment": "Beauty & Wellness",
    "Retail Product Price & Stock Inquiry": "Retail",
    "Hotel & Resort Suite Reservation": "Hospitality",
}

_SPEAKER_RE = re.compile(r"^\s*(caller|agent)\s*:\s*(.*)$", re.IGNORECASE)


def parse_transcript_turns(transcript: str) -> List[Dict[str, str]]:
    """Split a ``Caller:`` / ``Agent:`` transcript into ordered speaker turns.

    Lines without a speaker prefix continue the previous turn. Text before any
    prefix is attributed to ``unknown`` so it is never mistaken for caller speech.
    """
    turns: List[Dict[str, str]] = []
    for raw in (transcript or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _SPEAKER_RE.match(line)
        if m:
            turns.append({"speaker": m.group(1).lower(), "text": m.group(2).strip()})
        elif turns:
            turns[-1]["text"] = f"{turns[-1]['text']} {line}".strip()
        else:
            turns.append({"speaker": "unknown", "text": line})
    return turns


def _detect_intent(scope_text: str) -> tuple:
    """Return (intent_label, confidence) using word-boundary matches on caller speech."""
    best_label, best_score, best_priority = None, 0, -1
    for label, strong, weak, priority in _INTENT_RULES:
        strong_hits = len(set(m.group(0) for m in re.finditer(strong, scope_text)))
        if not strong_hits:
            continue
        score = strong_hits
        if weak:
            score += len(set(m.group(0) for m in re.finditer(weak, scope_text)))
        weighted = score * priority
        if weighted > best_score or (weighted == best_score and priority > best_priority):
            best_label, best_score, best_priority = label, weighted, priority
    if best_label == "Property Purchase Enquiry":
        # An explicit site-visit phrase is the more specific property intent.
        if re.search(_INTENT_RULES[0][1], scope_text):
            best_label = _INTENT_RULES[0][0]
    if best_label:
        return best_label, ("high" if best_score >= 4 else "medium")
    if _PRICE_ONLY_RE.search(scope_text):
        return "Price Enquiry", "low"
    return "General Inquiry", "none"


def _place_confirmation(turns: List[Dict[str, str]], token: str) -> bool:
    """True only if the agent repeated ``token`` and the caller then affirmed it."""
    for idx, turn in enumerate(turns):
        if turn["speaker"] != "agent" or token not in turn["text"].lower():
            continue
        for nxt in turns[idx + 1 : idx + 3]:
            if nxt["speaker"] == "caller":
                return bool(_AFFIRMATIVE_RE.search(nxt["text"].lower()))
    return False


def _detect_ambiguous_place(caller_text: str, turns: List[Dict[str, str]]):
    for group in _AMBIGUOUS_PLACE_GROUPS:
        heard = [tok for tok in group if re.search(rf"\b{tok}\b", caller_text)]
        if not heard:
            continue
        token = heard[0]
        confirmed = _place_confirmation(turns, token)
        alternatives = sorted({t.title() for t in group} - {token.title()})
        return token.title(), alternatives, confirmed
    return None



# Letters outside the Latin script (Telugu, Hindi/Devanagari, Tamil, ...). The deterministic
# keyword rules only understand English and Latin transliteration, so speech that is largely in
# another script must never be scored or classified as if it had been understood.
_NON_LATIN_SPEECH_THRESHOLD = 0.30


def non_latin_letter_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    non_latin = sum(1 for c in letters if ord(c) > 0x024F)
    return non_latin / len(letters)


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


_TITLE_BY_INTENT = {
    "Gym Membership & Trial Workout": ("Free Trial Request", "Gym Membership Enquiry"),
    "Self-Drive Vehicle Reservation": ("Self-Drive Booking Request", "Self-Drive Rental Enquiry"),
    "Solar Rooftop Assessment & Subsidy": ("Solar Survey Request", "Solar Rooftop Enquiry"),
    "Home Interior Design Consultation": ("Interior Consultation Request", "Home Interior Enquiry"),
    "New Vehicle Test Drive & Purchase": ("Test Drive Request", "New Vehicle Enquiry"),
    "Salon Grooming & Spa Appointment": ("Salon Appointment Request", "Salon Service Enquiry"),
    "Retail Product Price & Stock Inquiry": ("Product Reservation Request", "Product Price Enquiry"),
    "Hotel & Resort Suite Reservation": ("Room Booking Request", "Hotel Room Enquiry"),
    "Price Enquiry": ("Price Enquiry", "Price Enquiry"),
    "General Inquiry": ("General Enquiry — Appointment Requested", "General Enquiry"),
}


def build_conversation_title(intel: Dict[str, Any], caller_text: str = "") -> str:
    """Evidence-based, identity-free title derived only from the analysis result."""
    status = intel.get("analysis_status")
    cls = intel.get("lead_classification")
    if status == "no_caller_speech":
        return "No-Speech Call"
    if status == "no_transcript":
        return "No Transcript — Analysis Unavailable"
    if cls == "Voicemail / No Human Answer":
        return "Voicemail — No Human Answer"
    if cls == "Do Not Contact":
        return "Do Not Contact Request"
    if cls == "Not Interested":
        return "Not Interested"
    if cls == "Human Assistance Required":
        return "Human Assistance Request"
    if cls == "Callback Requested":
        return "Callback Requested"
    if cls == "Needs Review":
        if intel.get("language_support") == "unsupported_script":
            return "Telugu/Hindi-script Call — Needs Review"
        return "Unclear Call — Needs Review"
    intent = intel.get("customer_intent") or "General Inquiry"
    appt = bool((intel.get("appointment") or {}).get("requested"))
    reqs = intel.get("customer_requirements") or {}
    if intent in ("Site Visit & Property Booking", "Property Purchase Enquiry"):
        bhk = reqs.get("bhk")
        kind = f"{bhk} " if bhk else ""
        if intent == "Site Visit & Property Booking":
            return f"{kind}Site Visit Request".strip()
        if appt:
            return f"{kind}Property Visit Request".strip()
        return f"{kind}Property Enquiry".strip()
    if intent == "Doctor Consultation & Appointment":
        dental = bool(re.search(r"\b(dentist|dental|tooth\w*)\b", caller_text))
        if dental:
            return "Dental Appointment Request" if appt else "Dental Enquiry"
        return "Doctor Appointment Request" if appt else "Doctor Consultation Enquiry"
    pair = _TITLE_BY_INTENT.get(intent)
    if pair:
        return pair[0] if appt else pair[1]
    return "General Enquiry"


def build_conversation_summary(intel: Dict[str, Any]) -> str:
    """Concise summary composed only from extracted, evidenced facts."""
    status = intel.get("analysis_status")
    if status == "no_caller_speech":
        return "No caller speech was captured in this conversation."
    if status == "no_transcript":
        return "No transcript is available for this conversation."
    if status == "needs_review":
        if intel.get("language_support") == "unsupported_script":
            return (
                "The caller spoke mostly in a script this automatic analysis cannot read reliably; "
                "review the transcript."
            )
        return "The caller's speech was too brief or unclear to classify; review the transcript."
    parts: List[str] = []
    intent = intel.get("customer_intent")
    if intent and intent != "General Inquiry":
        parts.append(f"Caller enquired about: {intent}.")
    else:
        parts.append("Caller made a general enquiry.")
    reqs = intel.get("customer_requirements") or {}
    facts = []
    if reqs.get("bhk"):
        facts.append(f"configuration {reqs['bhk']}")
    if reqs.get("preferred_location"):
        facts.append(f"location {reqs['preferred_location']}")
    if reqs.get("budget"):
        facts.append(f"budget {reqs['budget']}")
    if facts:
        parts.append("Stated " + ", ".join(facts) + ".")
    for item in intel.get("unconfirmed_information") or []:
        if item.get("field") == "preferred_location":
            parts.append(
                f"Location heard as '{item.get('heard')}' but not confirmed "
                f"(could also be {', '.join(item.get('alternatives') or [])})."
            )
    appt = intel.get("appointment") or {}
    if appt.get("requested"):
        parts.append("Caller asked for an appointment or visit (not confirmed by a booking system).")
    if (intel.get("callback") or {}).get("requested"):
        parts.append("Caller asked for a callback.")
    if (intel.get("human_escalation") or {}).get("requested"):
        parts.append("Caller asked to speak to a person.")
    return " ".join(parts)


def derive_conversation_outcome(intel: Dict[str, Any]) -> str:
    status = intel.get("analysis_status")
    cls = intel.get("lead_classification")
    if status in ("no_caller_speech", "no_transcript", "needs_review"):
        return status
    if cls == "Voicemail / No Human Answer":
        return "voicemail"
    if cls == "Do Not Contact":
        return "do_not_contact"
    if cls == "Not Interested":
        return "not_interested"
    if cls == "Human Assistance Required":
        return "human_handover_requested"
    if cls == "Callback Requested" or (intel.get("callback") or {}).get("requested"):
        return "callback_requested"
    if (intel.get("appointment") or {}).get("requested"):
        return "appointment_requested"
    return "information_provided"


def _empty_analysis(status: str, business_context: Dict[str, Any]) -> Dict[str, Any]:
    classification = "Unqualified" if status == "no_caller_speech" else "Needs Review"
    industry = resolve_industry_from_context_and_intent(business_context, None, "")
    if status == "no_caller_speech":
        action = "No action: no caller speech was captured."
    else:
        action = "Staff Review: no transcript is available; confirm whether the call connected."
    intel: Dict[str, Any] = {
        "analysis_method": "Deterministic Rule Engine v2 (caller-utterance based)",
        "analyzer_version": ANALYZER_VERSION,
        "analysis_status": status,
        "language_support": "supported",
        "customer_intent": "No Caller Speech" if status == "no_caller_speech" else "General Inquiry",
        "intent_confidence": "none",
        "industry": industry,
        "industry_mismatch": False,
        "customer_requirements": {},
        "confirmed_information": [],
        "unconfirmed_information": [],
        "budget": "Not specified",
        "timeline": "Not specified",
        "objections": [],
        "objection_categories": {},
        "corrections_history": [],
        "buying_signals": [],
        "missing_information": [],
        "lead_score": 0,
        "score_basis": "insufficient_evidence",
        "lead_classification": classification,
        "transcript_evidence": "",
        "recommended_next_action": action,
        "appointment": {"requested": False, "type": "Consultation / Visit", "preferred_slot": "To be scheduled", "status": "None"},
        "callback": {"requested": False, "status": "None", "preferred_slot": None, "scheduled_time": None, "scheduled_reason": None},
        "human_escalation": {"requested": False},
    }
    intel["title"] = build_conversation_title(intel)
    intel["summary"] = build_conversation_summary(intel)
    intel["outcome"] = derive_conversation_outcome(intel)
    return intel


def analyze_lead_from_transcript(
    transcript: str,
    business_context: Dict[str, Any] | None = None,
    gathered_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Extract intent, requirements, score and classification from *caller* speech.

    Deterministic, evidence-quoting and conservative: agent speech is never used to
    infer the customer's intent, budget, location or timeline; ambiguous
    speech-recognition results stay as unconfirmed observations; calls with no
    caller speech are Unqualified rather than invented leads. No paid calls.
    """
    business_context = business_context or {}
    gathered_context = gathered_context or {}
    transcript = transcript or ""

    turns = parse_transcript_turns(transcript)
    labeled = any(t["speaker"] in ("caller", "agent") for t in turns)
    if labeled:
        caller_lines = [t["text"] for t in turns if t["speaker"] == "caller" and t["text"]]
    else:
        # Legacy: unlabeled text is treated as caller speech.
        caller_lines = [t["text"] for t in turns if t["text"]]
    caller_text = " ".join(caller_lines).lower()
    full_text = transcript.lower()
    scope_text = caller_text

    if not turns:
        return _empty_analysis("no_transcript", business_context)
    if labeled and not caller_lines:
        return _empty_analysis("no_caller_speech", business_context)

    # 1. Customer intent (caller speech only)
    intent, intent_confidence = _detect_intent(scope_text)

    # 2. Customer requirements (caller speech only)
    requirements: Dict[str, Any] = {}
    confirmed: List[Dict[str, Any]] = []
    unconfirmed: List[Dict[str, Any]] = []

    bhk_match = re.search(r"\b([1-5]\s*-?\s*bhk|[1-5]\s*-?\s*bedroom|villa|plot)\b", scope_text)
    if bhk_match:
        requirements["bhk"] = re.sub(r"[\s-]+", "", bhk_match.group(1)).upper()
        confirmed.append({"field": "bhk", "value": requirements["bhk"]})

    budget_match = _BUDGET_RE.search(scope_text)
    if budget_match:
        requirements["budget"] = budget_match.group(1).title()
    elif "budget" in scope_text:
        m = re.search(r"budget\s*(?:is|around|of)?\s*([0-9]+[a-zA-Z\s]+)", scope_text)
        if m:
            requirements["budget"] = m.group(1).strip()
    if requirements.get("budget"):
        confirmed.append({"field": "budget", "value": requirements["budget"]})

    ambiguous = _detect_ambiguous_place(scope_text, turns if labeled else [])
    location_match = _LOCATION_RE.search(scope_text)
    if ambiguous:
        heard, alternatives, place_confirmed = ambiguous
        if place_confirmed:
            requirements["preferred_location"] = heard
            confirmed.append({"field": "preferred_location", "value": heard})
        else:
            unconfirmed.append(
                {
                    "field": "preferred_location",
                    "heard": heard,
                    "alternatives": alternatives,
                    "reason": "speech_recognition_ambiguity",
                }
            )
    elif location_match:
        requirements["preferred_location"] = location_match.group(1).title()
        confirmed.append({"field": "preferred_location", "value": requirements["preferred_location"]})

    # Timeline: only from caller speech; absent means "Not specified".
    timeline = "Not specified"
    if any(w in scope_text for w in ["immediate", "ready to move", "asap", "this week", "today", "tomorrow"]):
        timeline = "Immediate (0-1 Month)"
    elif any(w in scope_text for w in ["next month", "3 months", "under 3 months", "soon"]):
        timeline = "Near Term (1-3 Months)"
    elif any(w in scope_text for w in ["6 months", "year", "next year", "future"]):
        timeline = "Mid Term (3-6 Months)"
    if timeline != "Not specified":
        requirements["timeline"] = timeline
        confirmed.append({"field": "timeline", "value": timeline})

    # 3. Buying signals
    buying_signals: List[str] = []
    if any(w in scope_text for w in ["site visit", "visit this saturday", "visit this sunday", "come to site", "come over"]):
        buying_signals.append("Requested on-site physical visit")
    if any(w in scope_text for w in ["book appointment", "appointment slot", "consultation", "schedule"]):
        buying_signals.append("Expressed clear willingness to schedule appointment")
    if "budget" in requirements or budget_match:
        buying_signals.append("Shared specific budget comfort range")
    if any(w in scope_text for w in ["discount", "offer", "best price", "negotiable", "payment plan"]):
        buying_signals.append("Actively inquired about pricing and payment schedules")
    if any(w in scope_text for w in ["loan", "bank approval", "sbi", "hdfc", "financing"]):
        buying_signals.append("Asked about loan approvals / financing assistance")
    if any(w in scope_text for w in ["ready to move", "possession", "delivery date"]):
        buying_signals.append("Inquired about project readiness / move-in date")

    # 4. Objections: 9 canonical categories with verbatim quotes
    objection_categories: Dict[str, Dict[str, Any]] = {}
    objections: List[str] = []
    category_patterns = {
        "PRICE": {"keywords": ["expensive", "too high", "costly", "out of budget", "price is high", "rates are high", "discount"], "desc": "Price sensitivity or high cost objection"},
        "TIMING": {"keywords": ["not right now", "call later", "next year", "not ready", "too early", "busy right now", "maybe later"], "desc": "Timing delay or postponement"},
        "TRUST": {"keywords": ["never heard of", "who are you", "is this genuine", "fraud", "scam", "how do i trust", "guarantee"], "desc": "Credibility or trust hesitation"},
        "COMPETITOR": {"keywords": ["comparing with", "other builder", "another vendor", "already talking to", "better price elsewhere"], "desc": "Active competitor comparison"},
        "AUTHORITY": {"keywords": ["check with family", "talk to husband", "talk to wife", "consult parents", "discuss with partner", "not my decision"], "desc": "Decision-making authority dependency"},
        "NEED": {"keywords": ["don't need", "no requirement", "not looking", "not interested", "why do i need"], "desc": "Lack of perceived need"},
        "ALREADY_HAVE": {"keywords": ["already bought", "already have", "already booked", "already purchased", "existing vendor"], "desc": "Requirement already fulfilled"},
        "WRONG_PERSON": {"keywords": ["wrong number", "not this person", "don't know who this is", "no one by that name"], "desc": "Incorrect contact or wrong person"},
        "COMPLIANCE": {"keywords": ["do not call", "stop calling", "remove my number", "dnc", "why are you calling me", "who gave you my number"], "desc": "Regulatory / DNC compliance objection"},
    }
    for cat, data in category_patterns.items():
        for line in caller_lines:
            line_lower = line.lower()
            if any(k in line_lower for k in data["keywords"]):
                objection_categories[cat] = {"category": cat, "description": data["desc"], "verbatim_quote": line.strip()}
                objections.append(f"[{cat}] {data['desc']}: \"{line.strip()}\"")
                break

    # 4b. Mid-stream corrections (caller speech)
    corrections_history: List[Dict[str, Any]] = []
    for line in caller_lines:
        line_lower = line.lower()
        if any(marker in line_lower for marker in ["actually", "change to", "instead", "rather than", "prefer", "scratch that"]):
            loc_match = _LOCATION_RE.search(line_lower)
            if loc_match and not ambiguous:
                new_loc = loc_match.group(1).title()
                if requirements.get("preferred_location") and requirements["preferred_location"] != new_loc:
                    corrections_history.append({"field": "preferred_location", "previous": requirements["preferred_location"], "corrected": new_loc, "statement": line.strip()})
                requirements["preferred_location"] = new_loc
            b_matches = list(_BUDGET_RE.finditer(line_lower))
            if b_matches:
                new_budget = b_matches[-1].group(1).title()
                if requirements.get("budget") and requirements["budget"] != new_budget:
                    corrections_history.append({"field": "budget", "previous": requirements["budget"], "corrected": new_budget, "statement": line.strip()})
                requirements["budget"] = new_budget

    # 5. Missing information
    missing_info: List[str] = []
    if "bhk" not in requirements and (business_context.get("industry") or "") == "Real Estate":
        missing_info.append("BHK configuration not confirmed")
    if "budget" not in requirements:
        missing_info.append("Exact budget range not stated")
    if not any(w in scope_text for w in ["visit", "appointment", "schedule", "slot"]):
        missing_info.append("Appointment / visit date not finalized")
    for item in unconfirmed:
        missing_info.append(f"{item['field'].replace('_', ' ').title()} not confirmed (heard '{item['heard']}')")

    # 6. Appointment and callback requests (caller speech)
    appointment_requested = False
    appointment_type = "Consultation / Visit"
    preferred_slot = None
    composite_slot_match = re.search(
        r"\b((?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|after\s+\w+)"
        r"(?:\s+(?:at|around|by|in the)?\s*(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\b(?:morning|afternoon|evening|post lunch)\b))?)\b",
        scope_text,
        re.IGNORECASE,
    )
    if composite_slot_match and composite_slot_match.group(1).strip():
        preferred_slot = composite_slot_match.group(1).strip().title()
    else:
        slot_match = re.search(
            r"\b(saturday|sunday|tomorrow|monday|tuesday|wednesday|thursday|friday|today|morning|evening|afternoon|\d{1,2}\s*(?:am|pm))\b",
            scope_text,
            re.IGNORECASE,
        )
        if slot_match:
            preferred_slot = slot_match.group(0).strip().title()

    if any(w in scope_text for w in ["site visit", "site survey", "survey", "appointment", "trial session", "test drive", "schedule a visit", "book slot", "book visit", "visit", "book a", "reserve a", "reservation"]):
        appointment_requested = True
        if "site visit" in scope_text or "site survey" in scope_text or "survey" in scope_text:
            appointment_type = "Site Survey / Visit"
        elif "test drive" in scope_text:
            appointment_type = "Test Drive"
        elif "doctor" in scope_text or "clinic" in scope_text:
            appointment_type = "Doctor Consultation"
        elif "trial" in scope_text:
            appointment_type = "Free Trial Workout"
        elif "salon" in scope_text or "haircut" in scope_text:
            appointment_type = "Salon Appointment"

    callback_requested = any(w in caller_text for w in ["call me back", "call back", "call later", "busy right now", "speak later", "call tomorrow"])
    human_assistance_required = any(w in caller_text for w in ["talk to human", "speak to manager", "transfer to human", "real person", "connect to executive", "supervisor", "connect me to manager"])
    do_not_contact = any(w in caller_text for w in ["do not call", "stop calling", "remove my number", "dnc", "wrong number", "don't call"])

    # 7. Score (0-100) from evidenced signals only
    is_voicemail_transcript = any(w in full_text for w in [
        "automated voice message", "record your message", "leave a message after the tone",
        "at the tone, please record", "mailbox is full", "voice mailbox"
    ])
    score = 25
    if is_voicemail_transcript:
        score = 0
    if requirements.get("bhk"):
        score += 20
    if requirements.get("budget"):
        score += 20
    if requirements.get("preferred_location"):
        score += 10
    if timeline == "Immediate (0-1 Month)":
        score += 20
    elif timeline == "Near Term (1-3 Months)":
        score += 10
    if appointment_requested:
        score += 25
    if len(buying_signals) >= 2:
        score += 15
    if objections:
        score -= min(15, len(objections) * 8)
    if any(w in scope_text for w in ["not interested", "not looking", "no requirement", "wrong number"]):
        score = 10
    if do_not_contact or is_voicemail_transcript:
        score = 0
    else:
        score = max(5, min(98, score))

    # 8. Classification
    non_timeline_reqs = {k: v for k, v in requirements.items() if k != "timeline"}
    thin_speech = _word_count(caller_text) < 4 and not non_timeline_reqs and not appointment_requested
    analysis_status = "complete"
    if is_voicemail_transcript:
        classification = "Voicemail / No Human Answer"
    elif do_not_contact:
        classification = "Do Not Contact"
    elif any(w in scope_text for w in ["not interested", "no requirement", "don't need"]):
        classification = "Not Interested"
    elif human_assistance_required:
        classification = "Human Assistance Required"
    elif callback_requested:
        classification = "Callback Requested"
    elif thin_speech and intent == "General Inquiry":
        classification = "Needs Review"
        analysis_status = "needs_review"
        score = 0
    elif score >= 75:
        classification = "Hot"
    elif score >= 45:
        classification = "Warm"
    else:
        classification = "Cold"

    # 8b. Language support: never present native-script speech as understood.
    language_support = "supported"
    if non_latin_letter_ratio(caller_text) >= _NON_LATIN_SPEECH_THRESHOLD:
        language_support = "unsupported_script"
        analysis_status = "needs_review"
        classification = "Needs Review"
        score = 0

    # 9. Business industry vs. the customer's enquiry
    industry = resolve_industry_from_context_and_intent(business_context, intent, full_text)
    intent_industry = _INTENT_INDUSTRY.get(intent)
    configured_industry = resolve_industry_from_context_and_intent(business_context, None, "")
    industry_mismatch = bool(
        intent_industry and configured_industry not in ("General Business", intent_industry)
    )

    evidence = ""
    for line in caller_lines:
        if any(term in line.lower() for term in [
            "bhk", "cr", "lakh", "budget", "visit", "appointment", "interested", "tomorrow",
            "saturday", "sunday", "doctor", "consultation", "clinic", "trial", "membership",
            "gym", "rental", "test drive", "booking", "price", "treatment", "subsidy", "service"
        ]):
            evidence = f'Caller: "{line.strip()}"'
            break
    if not evidence and caller_lines:
        evidence = f'Caller: "{caller_lines[0].strip()}"'

    # 10. Industry-aware recommended next action
    if language_support == "unsupported_script":
        recommended_action = (
            "Staff Review: the caller spoke mostly in a script this automatic analysis cannot "
            "read reliably (e.g. Telugu or Hindi script); read the transcript before follow-up."
        )
    elif classification == "Needs Review":
        recommended_action = "Staff Review: caller speech was too brief or unclear; review the transcript before follow-up."
    elif industry_mismatch and classification in ("Hot", "Warm", "Cold"):
        recommended_action = (
            f"Staff Review: the enquiry ({intent}) does not match the configured business "
            f"({configured_industry}); verify before follow-up."
        )
    else:
        recommended_action = get_industry_recommended_action(
            classification=classification,
            industry=industry,
            intent=intent,
            preferred_slot=preferred_slot,
        )

    intel: Dict[str, Any] = {
        "analysis_method": "Deterministic Rule Engine v2 (caller-utterance based)",
        "analyzer_version": ANALYZER_VERSION,
        "analysis_status": analysis_status,
        "language_support": language_support,
        "customer_intent": intent,
        "intent_confidence": intent_confidence,
        "industry": industry,
        "industry_mismatch": industry_mismatch,
        "customer_requirements": requirements,
        "confirmed_information": confirmed,
        "unconfirmed_information": unconfirmed,
        "budget": requirements.get("budget", "Not specified"),
        "timeline": timeline,
        "objections": objections,
        "objection_categories": objection_categories,
        "corrections_history": corrections_history,
        "buying_signals": buying_signals,
        "missing_information": missing_info,
        "lead_score": score,
        "score_basis": "evidence" if analysis_status == "complete" else "insufficient_evidence",
        "lead_classification": classification,
        "transcript_evidence": evidence,
        "recommended_next_action": recommended_action,
        "appointment": {
            "requested": appointment_requested,
            "type": appointment_type,
            "preferred_slot": preferred_slot or "To be scheduled",
            "status": "Requested" if appointment_requested else "None",
        },
        "callback": {
            "requested": callback_requested,
            "status": "Pending" if callback_requested else "None",
            "preferred_slot": preferred_slot,
            "scheduled_time": (
                parse_indian_relative_callback_time(scope_text)[0].isoformat()
                if (callback_requested or preferred_slot)
                else None
            ),
            "scheduled_reason": (
                parse_indian_relative_callback_time(scope_text)[1]
                if (callback_requested or preferred_slot)
                else None
            ),
        },
        "human_escalation": {
            "requested": human_assistance_required,
        },
    }
    intel["title"] = build_conversation_title(intel, caller_text)
    intel["summary"] = build_conversation_summary(intel)
    intel["outcome"] = derive_conversation_outcome(intel)
    return intel
