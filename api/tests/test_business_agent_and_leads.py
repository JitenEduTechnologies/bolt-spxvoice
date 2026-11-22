import pytest
from httpx import ASGITransport, AsyncClient

from api.app import app
from api.db import db_client
from api.db.models import WorkflowModel
from api.services.business.agent_training import (
    UNIVERSAL_INDUSTRY_PRESETS,
    compile_agent_training_to_workflow,
    generate_agent_prompt,
    get_preset_by_id,
)
from api.services.business.lead_intelligence import (
    analyze_lead_from_transcript,
    extract_transcript_from_logs,
)
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph
from api.utils.auth import create_jwt_token


def test_universal_industry_presets_count_and_keys():
    """Verify all 10 universal presets are present with valid keys."""
    expected_ids = {
        "real_estate",
        "clinics",
        "gyms",
        "car_rentals",
        "automotive",
        "salons",
        "retail",
        "solar",
        "interiors",
        "custom",
    }
    present_ids = {p["id"] for p in UNIVERSAL_INDUSTRY_PRESETS}
    assert expected_ids.issubset(present_ids), f"Missing presets: {expected_ids - present_ids}"

    for preset in UNIVERSAL_INDUSTRY_PRESETS:
        assert preset["business_name"]
        assert preset["agent_name"]
        assert preset["greeting"]
        assert preset["closing_script"]
        assert len(preset["products_and_pricing"]) >= 1
        assert len(preset["qualification_fields"]) >= 1
        assert "language_instructions" in preset
        assert "max_call_duration_seconds" in preset


def test_compile_agent_training_to_valid_workflow():
    """Verify compile_agent_training_to_workflow outputs a valid ReactFlow graph."""
    preset = get_preset_by_id("real_estate")
    assert preset is not None

    preset["business_name"] = "Sri Krishna Real Estate Group"
    preset["agent_name"] = "Priya"

    definition = compile_agent_training_to_workflow(preset)

    # Must validate cleanly against ReactFlowDTO & WorkflowGraph
    dto = ReactFlowDTO.model_validate(definition)
    graph = WorkflowGraph(dto)

    assert graph.start_node_id == "start"
    node_ids = {n.id for n in dto.nodes}
    assert {"start", "assistant", "end"}.issubset(node_ids)

    # Verify start greeting matches
    start_node = next(n for n in dto.nodes if n.id == "start")
    assert start_node.data.greeting == preset["greeting"]

    # Verify assistant prompt has business specifics and language rules
    assistant_node = next(n for n in dto.nodes if n.id == "assistant")
    prompt = assistant_node.data.prompt
    assert "Sri Krishna Real Estate Group" in prompt
    assert "Priya" in prompt
    assert "Telugu" in prompt
    assert "Hindi" in prompt
    assert "Skyline Heights" in prompt

    # Verify end call prompt has closing
    end_node = next(n for n in dto.nodes if n.id == "end")
    assert preset["closing_script"] in end_node.data.prompt


def test_lead_intelligence_hot_lead_extraction():
    """Test extracting hot lead metrics from transcript with high intent."""
    sample_transcript = (
        "Agent: Namaste! Welcome to Sri Krishna Realty. How can I help you today?\n"
        "Caller: Namaskaram! I am looking for a 3BHK flat in Gachibowli with a budget of 1.6 Crores.\n"
        "Agent: We have Skyline Heights in Gachibowli fitting your 1.6 Cr budget. Would you like to schedule a site visit this Saturday at 11 AM?\n"
        "Caller: Yes, please book the site visit for this Saturday at 11 AM. Thank you!"
    )

    intel = analyze_lead_from_transcript(sample_transcript)

    assert intel["customer_intent"] == "Site Visit & Property Booking"
    assert intel["customer_requirements"].get("bhk") == "3BHK"
    assert "1.6" in intel["budget"]
    assert intel["customer_requirements"].get("preferred_location") == "Gachibowli"
    assert intel["lead_score"] >= 75
    assert intel["lead_classification"] == "Hot"
    assert intel["appointment"]["requested"] is True
    assert intel["appointment"]["status"] == "Requested"
    assert len(intel["transcript_evidence"]) > 0


def test_lead_intelligence_dnc_opt_out():
    """Test explicit DNC / opt-out results in Do Not Contact classification and 0 score."""
    sample_transcript = (
        "Agent: Hello, I am calling from IronFit Fitness Club.\n"
        "Caller: Please stop calling and remove my number immediately. Do not call me again."
    )

    intel = analyze_lead_from_transcript(sample_transcript)

    assert intel["lead_classification"] == "Do Not Contact"
    assert intel["lead_score"] == 0
    assert "Do Not Call" in intel["recommended_next_action"]


def test_lead_intelligence_human_escalation():
    """Test human escalation request detection."""
    sample_transcript = (
        "Agent: Hello, welcome to customer service.\n"
        "Caller: I have a serious billing issue. Please connect to executive or speak to manager right now."
    )

    intel = analyze_lead_from_transcript(sample_transcript)

    assert intel["lead_classification"] == "Human Assistance Required"
    assert intel["human_escalation"]["requested"] is True


@pytest.mark.asyncio
async def test_business_api_flow():
    """Integration test for presets, overview, simulation, and lead patch endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Presets endpoint (public)
        res = await ac.get("/api/v1/business/presets")
        assert res.status_code == 200
        presets = res.json()
        assert len(presets) >= 10

        # Get existing user for auth token
        user = await db_client.get_user_by_email("admin@spxvoice.com")
        assert user is not None
        token = create_jwt_token(user.id, user.email)
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Get Agent Training
        res = await ac.get("/api/v1/business/agent", headers=headers)
        assert res.status_code == 200
        agent_data = res.json()
        assert "training_data" in agent_data
        assert "versions" in agent_data

        # 3. Simulate a call
        sim_payload = {
            "customer_name": "Siddharth Rao",
            "customer_phone": "+91-9876599999",
            "call_type": "inbound",
            "scenario": "real_estate_site_visit",
        }
        sim_res = await ac.post("/api/v1/business/test-call/simulate", headers=headers, json=sim_payload)
        assert sim_res.status_code == 200
        sim_data = sim_res.json()
        assert sim_data["status"] == "success"
        run_id = sim_data["run_id"]

        # 4. Get Overview metrics
        ov_res = await ac.get("/api/v1/business/overview", headers=headers)
        assert ov_res.status_code == 200
        ov_data = ov_res.json()
        assert ov_data["total_calls"] >= 1
        assert ov_data["qualified_leads"] >= 1

        # 5. List Conversations
        conv_res = await ac.get("/api/v1/business/conversations?limit=10", headers=headers)
        assert conv_res.status_code == 200
        conv_data = conv_res.json()
        assert conv_data["total"] >= 1

        # 6. Patch Lead Intelligence / Appointment
        patch_payload = {
            "lead_classification": "Hot",
            "appointment_status": "Confirmed",
            "owner_notes": "Followed up via WhatsApp. Client confirmed Saturday visit.",
        }
        patch_res = await ac.patch(f"/api/v1/business/conversations/{run_id}/lead", headers=headers, json=patch_payload)
        assert patch_res.status_code == 200
        patched = patch_res.json()
        assert patched["lead_intelligence"]["lead_classification"] == "Hot"
        assert patched["lead_intelligence"]["appointment"]["status"] == "Confirmed"
        assert "WhatsApp" in patched["owner_notes"]

        # 7. List Leads API verification
        leads_res = await ac.get("/api/v1/business/leads", headers=headers)
        assert leads_res.status_code == 200
        leads_data = leads_res.json()
        assert len(leads_data) >= 1
        lead_0 = leads_data[0]
        assert "call_source" in lead_0
        assert "is_test" in lead_0
        assert "mode" in lead_0
        assert "run_name" in lead_0
        # The simulated call should be identified as simulated
        assert lead_0["call_source"] == "simulated"
        assert lead_0["is_test"] is True


def test_industry_aware_recommendations_healthcare():
    """Verify clinics / healthcare / dental transcript receives healthcare action, not real-estate brochure."""
    transcript = (
        "Agent: Welcome to Sanjeevani Clinic. How may I assist you today?\n"
        "Caller: Hello, I have had a severe toothache since yesterday. Can I see a dentist or doctor tomorrow afternoon?\n"
        "Agent: We have consultation slots available tomorrow between 3 PM and 6 PM. Shall I reserve 4 PM for you?\n"
        "Caller: Yes, please book 4 PM tomorrow for consultation."
    )
    intel = analyze_lead_from_transcript(transcript, business_context={"industry": "Healthcare", "name": "Dental Clinic"})
    assert intel["customer_intent"] == "Doctor Consultation & Appointment"
    assert intel["industry"] == "Healthcare"
    assert "brochure" not in intel["recommended_next_action"].lower()
    assert "payment plans" not in intel["recommended_next_action"].lower()
    assert any(term in intel["recommended_next_action"] for term in ["Medical staff", "Clinic staff", "patient"])


def test_industry_aware_recommendations_fitness():
    """Verify gym / fitness transcript receives fitness action, not real-estate brochure."""
    transcript = (
        "Agent: Hi, welcome to IronFit Fitness Club!\n"
        "Caller: I want to know about your annual membership and if I can try a workout tomorrow.\n"
        "Agent: Our annual plan is ₹17,999 and we offer a complimentary 1-day trial workout. Would you like to come tomorrow morning?\n"
        "Caller: Yes, tomorrow morning at 7 AM would be great."
    )
    intel = analyze_lead_from_transcript(transcript, business_context={"industry": "Fitness"})
    assert intel["customer_intent"] == "Gym Membership & Trial Workout"
    assert intel["industry"] == "Fitness"
    assert "brochure" not in intel["recommended_next_action"].lower()
    assert "payment plans" not in intel["recommended_next_action"].lower()
    assert any(term in intel["recommended_next_action"] for term in ["Fitness consultant", "Member Nurture", "trial"])


def test_industry_aware_recommendations_generic_fallback():
    """Verify unclassified business context returns safe staff review recommendation without fabricating commitments."""
    transcript = (
        "Agent: Hello, welcome to our office. How can I help you?\n"
        "Caller: I wanted to understand your service pricing and discuss options with someone.\n"
        "Agent: I will note down your details and someone from our team will get in touch."
    )
    intel = analyze_lead_from_transcript(transcript, business_context={})
    assert "brochure" not in intel["recommended_next_action"].lower()
    assert "Staff Review" in intel["recommended_next_action"] or "Staff to contact" in intel["recommended_next_action"]


