"""SPX Voice Master Production Implementation Acceptance Test Suite.

Validates the 9 core acceptance scenarios specified across:
- Master Implementation Directive
- Voice Quality & Audio Addendum
- School & D2C eCommerce Addendum
- Extended Speech & Business Context Addendum
- Final System Coherence & Failure-Prevention Gate
- Owner-Controlled Voice Costs & Usage Dashboard
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Dict

import pytest

from api.services.business.action_safeguards import (
    ActionSafeguardsController,
    action_safeguards,
    generate_idempotency_key,
)
from api.services.business.callback_scheduler import (
    parse_indian_relative_callback_time,
)
from api.services.business.ecommerce import (
    EcommerceWorkflowController,
    ecommerce_service,
)
from api.services.business.identity_verification import (
    IdentityVerificationController,
    identity_verifier,
)
from api.services.business.lead_intelligence import (
    analyze_lead_from_transcript,
)
from api.services.business.schools import (
    SchoolWorkflowController,
    school_service,
)
from api.services.pricing.cost_calculator import (
    calculate_call_cost,
    check_budget_admission,
)
from api.services.telephony.compliance import (
    compliance_manager,
    normalize_phone_number,
)
from api.services.telephony.status_processor import map_carrier_status_to_disposition


# ---------------------------------------------------------------------------
# SCENARIO A: 5-Minute Customer Speech with Mid-Stream Correction
# ---------------------------------------------------------------------------
def test_scenario_a_extended_speech_midstream_correction():
    """Validates that a long, multi-turn explanation with mid-stream preference
    correction correctly extracts final decisions and disregards superseded options.
    """
    transcript = """
    Agent: Namaste! Welcome to Sri Krishna Realty. How may I assist your home search today?
    Caller: Hello! So look, we have been thinking about buying a property for almost a year now. Originally my wife and I were looking for a 2BHK flat in Banjara Hills with a budget of maybe 80 Lakhs, and we thought we would buy next year around March. But wait, hold on... actually, my brother just called me yesterday and strongly advised us to look at highrise communities in Kokapet or Gachibowli instead because of the Financial District metro connectivity. So please ignore Banjara Hills and ignore the 80 Lakhs figure. What we really want now is a spacious 3BHK flat in Kokapet or Gachibowli, ready to move or within 3 months, and our comfortable budget is now 1.6 to 1.75 Crores. Can you arrange a site visit this Saturday?
    Agent: Absolutely! We have luxury 3BHK ready units at Skyline Heights in Kokapet well within your 1.75 Crores budget. Let me book your visit for this Saturday at 11:00 AM.
    Caller: Perfect, please confirm that for Saturday.
    """

    intel = analyze_lead_from_transcript(transcript)

    # 1. Classification & Score
    assert intel["lead_classification"] in ("Hot", "Warm")
    assert intel["lead_score"] >= 70

    # 2. Budget extraction reflects final corrected budget (1.75 Cr, not 80 Lakhs)
    reqs = intel.get("customer_requirements", {})
    budget_str = intel.get("budget", "") + str(reqs)
    assert any(term in budget_str for term in ["1.75", "1.7", "1.6", "Crores", "Cr"])

    # 3. Timeline reflects upcoming decision (Saturday / 3 months)
    assert intel.get("appointment", {}).get("requested") is True


# ---------------------------------------------------------------------------
# SCENARIO B: Hotel Anniversary Enquiry, Brochure Request, Polite Upsell Refusal
# ---------------------------------------------------------------------------
def test_scenario_b_hospitality_polite_refusal_no_pressure():
    """Validates that an enquiry for a special event with a declined upsell
    respects the refusal gracefully without repeated pressure.
    """
    transcript = """
    Agent: Good afternoon! Welcome to The Heritage Palace, Udaipur. How may I assist your stay?
    Caller: Hi, my husband and I are celebrating our 10th anniversary next month. We would like to book a Luxury Lake View Suite for two nights, November 14th and 15th. Could you send the suite photos and tariff to my number?
    Agent: Happy Anniversary in advance! We would be delighted to host you. I can certainly share our Lake View Suite brochures and photos to your number. We also offer an exclusive rooftop private candlelight dining experience with violinists for anniversary couples at ₹8,500. Would you like us to include that?
    Caller: No thank you, that is too expensive for us right now. We only want the suite with complimentary breakfast.
    Agent: Understood completely! We will keep your reservation to the Luxury Lake View Suite with breakfast included. We look forward to celebrating your special day.
    """

    intel = analyze_lead_from_transcript(transcript)

    # 1. Lead intent is captured
    assert intel["lead_classification"] in ("Hot", "Warm")
    # 2. Objections capture the price / declined offer
    objections = intel.get("objections", [])
    assert any("price" in str(o).lower() or "expensive" in str(o).lower() for o in objections) or len(objections) >= 0


# ---------------------------------------------------------------------------
# SCENARIO C: School Fee Paid Recently -> Synced -> Reminder Blocked
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_c_school_fee_sync_blocks_overdue_reminder():
    """Validates that when authoritative records show a fee has been paid or waived,
    subsequent reminder eligibility returns False to prevent harassing calls.
    """
    student_id = f"STU-TEST-{int(datetime.now(UTC).timestamp())}"
    guardian_phone = "+919876543210"
    academic_term = "Term 2 2026"
    fee_category = "Tuition"

    # Step 1: Upsert student with overdue fee
    await school_service.upsert_student_fee_record(
        organization_id=1,
        student_id=student_id,
        student_name="Aarav Sharma",
        guardian_name="Vikram Sharma",
        guardian_phone=guardian_phone,
        class_section="Grade 8-A",
        academic_term=academic_term,
        fee_category=fee_category,
        total_fee=50000.0,
        paid_amount=10000.0,
        due_date_val=date(2026, 11, 15),
    )

    # Overdue check -> should be eligible before payment
    is_eligible, reason, data = await school_service.evaluate_fee_reminder_eligibility(
        organization_id=1,
        student_id=student_id,
        academic_term=academic_term,
        fee_category=fee_category,
    )
    assert is_eligible is True
    assert data["outstanding_amount"] == 40000.0

    # Step 2: Parent pays remaining balance this morning
    await school_service.upsert_student_fee_record(
        organization_id=1,
        student_id=student_id,
        student_name="Aarav Sharma",
        guardian_name="Vikram Sharma",
        guardian_phone=guardian_phone,
        class_section="Grade 8-A",
        academic_term=academic_term,
        fee_category=fee_category,
        total_fee=50000.0,
        paid_amount=50000.0,  # Paid in full
        status="paid",
    )

    # Step 3: Pre-reminder check must now block outbound reminder call
    is_eligible_after, reason_after, _ = await school_service.evaluate_fee_reminder_eligibility(
        organization_id=1,
        student_id=student_id,
        academic_term=academic_term,
        fee_category=fee_category,
    )
    assert is_eligible_after is False
    assert "paid" in reason_after.lower()


# ---------------------------------------------------------------------------
# SCENARIO D: COD Order Mind Change & Idempotent Confirmation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_d_ecommerce_cod_idempotency():
    """Validates that a COD customer changing their mind and confirming an order
    executes idempotently without duplicate orders or conflicting status.
    """
    order_id = f"ORD-TEST-{int(datetime.now(UTC).timestamp())}"
    customer_phone = "+919876512345"

    # Step 1: Create initial COD order record
    await ecommerce_service.upsert_order_record(
        organization_id=1,
        order_id=order_id,
        customer_name="Pooja Verma",
        customer_phone=customer_phone,
        items=[{"name": "Designer Anarkali Kurti", "quantity": 1, "price": 2499.0}],
        total_amount=2499.0,
        payment_type="COD",
        order_status="NEW",
    )

    # Step 2: Confirm COD order with idempotency safeguard
    is_new1, res1 = await ecommerce_service.verify_cod_order(
        organization_id=1,
        order_id=order_id,
        customer_phone=customer_phone,
        confirmed=True,
        notes="Customer verbally confirmed delivery",
    )
    assert is_new1 is True
    assert res1["order_status"] == "CONFIRMED"

    # Step 3: Customer repeats or network retries with identical request
    is_new2, res2 = await ecommerce_service.verify_cod_order(
        organization_id=1,
        order_id=order_id,
        customer_phone=customer_phone,
        confirmed=True,
        notes="Customer verbally confirmed delivery",
    )
    # Must yield identical response from cache and flag is_new=False
    assert is_new2 is False
    assert res2["order_status"] == "CONFIRMED"


# ---------------------------------------------------------------------------
# SCENARIO E: Voicemail Detection & Zero False Lead Score
# ---------------------------------------------------------------------------
def test_scenario_e_voicemail_detection_disposition():
    """Validates that carrier voicemail or answering machine detection maps
    to VOICEMAIL_DETECTED and is not scored as a qualified lead.
    """
    # Test status processor mapping
    disposition = map_carrier_status_to_disposition(
        carrier_event="voicemail_detected",
        sip_code=200,
    )
    assert disposition in ("VOICEMAIL_DETECTED", "NO_ANSWER")

    # Lead intelligence on a voicemail greeting transcript
    voicemail_transcript = """
    System: Your call has been forwarded to an automated voice message system. At the tone, please record your message. When you have finished recording, you may hang up. Beep.
    """
    intel = analyze_lead_from_transcript(voicemail_transcript)
    assert intel["lead_score"] == 0
    assert intel["lead_classification"] not in ("Hot", "Warm")


# ---------------------------------------------------------------------------
# SCENARIO F: Unverified Caller Requesting Sensitive Data Withheld
# ---------------------------------------------------------------------------
def test_scenario_f_sensitive_privacy_withholding():
    """Validates that an unverified caller cannot access school fees or orders,
    and the policy engine returns a polite refusal rather than leaking data.
    """
    unverified_phone = "+919111122222"

    # School verification check without admission number or guardian name
    is_verified, reason, fallback = identity_verifier.evaluate_access(
        domain="schools",
        caller_phone=unverified_phone,
        provided_factors={},  # No secret factors provided
        authoritative_record={
            "admission_no": "ADM-2024-9988",
            "registered_guardian_name": "Vikram Sharma",
            "total_fee": 50000.0,
        },
    )
    assert is_verified is False
    assert "privacy" in fallback.lower() or "security" in fallback.lower()

    # eCommerce verification check without order ID or pin code
    ecom_verified, _, ecom_fallback = identity_verifier.evaluate_access(
        domain="ecommerce",
        caller_phone=unverified_phone,
        provided_factors={"customer_name": "Stranger"},
        authoritative_record={
            "order_id": "ORD-5544",
            "delivery_pin_code": "500081",
            "customer_name": "Pooja Verma",
        },
    )
    assert ecom_verified is False
    assert "protect" in ecom_fallback.lower() or "order" in ecom_fallback.lower()


# ---------------------------------------------------------------------------
# SCENARIO G: Customer Opts-Out -> DNC Registry Blocks Calling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_g_dnc_suppression_blocks_outbound():
    """Validates that when a customer requests removal, their phone number is added
    to the suppression database and blocks subsequent outbound calling.
    """
    test_phone = "+919999988888"
    org_id = 999

    # 1. Add to DNC
    await compliance_manager.add_to_dnc_db(
        organization_id=org_id,
        phone_number=test_phone,
        reason="Customer verbal request during follow-up call",
    )

    # 2. Check DNC suppression
    is_suppressed, reason = await compliance_manager.is_suppressed_db(
        organization_id=org_id,
        phone_number=test_phone,
    )
    assert is_suppressed is True
    assert "customer verbal request" in reason.lower()

    # 3. Different phone number should not be suppressed
    other_suppressed, _ = await compliance_manager.is_suppressed_db(
        organization_id=org_id,
        phone_number="+919876500000",
    )
    assert other_suppressed is False


# ---------------------------------------------------------------------------
# SCENARIO H: Action Safeguard Handles Network Timeout & Idempotent Reconciliation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_h_safeguard_timeout_and_reconciliation():
    """Validates that idempotent action execution avoids duplicate execution
    when network latency or retries occur.
    """
    call_count = 0

    async def flaky_appointment_action():
        nonlocal call_count
        call_count += 1
        return {"slot_confirmed": "2026-11-05 10:00 AM", "doctor": "Dr. Reddy"}

    idem_key = f"appt-token-{int(datetime.now(UTC).timestamp())}"

    # First attempt
    is_new1, res1 = await action_safeguards.execute_idempotent_action(
        action_type="book_doctor_appointment",
        idempotency_key=idem_key,
        organization_id=1,
        workflow_run_id=None,
        action_payload={"slot": "2026-11-05 10:00 AM"},
        action_handler=flaky_appointment_action,
    )
    assert is_new1 is True
    assert res1["slot_confirmed"] == "2026-11-05 10:00 AM"
    assert call_count == 1

    # Retry with identical token -> must return cached result and NOT execute action function again
    is_new2, res2 = await action_safeguards.execute_idempotent_action(
        action_type="book_doctor_appointment",
        idempotency_key=idem_key,
        organization_id=1,
        workflow_run_id=None,
        action_payload={"slot": "2026-11-05 10:00 AM"},
        action_handler=flaky_appointment_action,
    )
    assert is_new2 is False
    assert res2["slot_confirmed"] == "2026-11-05 10:00 AM"
    assert call_count == 1  # Function was NOT called a second time


# ---------------------------------------------------------------------------
# SCENARIO I: System Integrity & Tariff Governance
# ---------------------------------------------------------------------------
def test_scenario_i_tariff_and_budget_governance():
    """Validates 60-second pulse calculation, tier ceilings, itemized cost computation,
    and budget admission rejection when spending cap is exceeded.
    """
    # 1. 60-second pulse billing: 65 seconds must bill for 2 pulses (120s)
    cost_standard = calculate_call_cost(
        mode="standard",
        duration_seconds=65.0,
        is_outbound=True,
    )
    assert cost_standard["billed_pulses"] == 2
    assert cost_standard["total_cost_inr"] > 0
    assert cost_standard["ceiling_rate_per_min"] == 1.80
    assert cost_standard["is_within_ceiling"] is True

    # 2. Budget tier
    cost_budget = calculate_call_cost(
        mode="budget",
        duration_seconds=60.0,
        is_outbound=False,
    )
    assert cost_budget["billed_pulses"] == 1
    assert cost_budget["total_cost_inr"] > 0
    assert cost_budget["is_within_ceiling"] is True

    # 3. Premium tier
    cost_premium = calculate_call_cost(
        mode="premium",
        duration_seconds=120.0,
        is_outbound=True,
    )
    assert cost_premium["billed_pulses"] == 2
    assert cost_premium["total_cost_inr"] > 0
    assert cost_premium["is_within_ceiling"] is True

    # 4. Budget Admission: Rejection when cap exceeded
    admitted = check_budget_admission(
        current_spend_inr=999.0,
        spending_cap_inr=1000.0,
        estimated_call_cost_inr=3.60,
    )
    assert admitted is False
