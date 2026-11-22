import pytest
from unittest.mock import AsyncMock, patch
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests import MockLLMService

from api.db.models import OrganizationModel, UserModel, WorkflowModel
from api.schemas.user_configuration import UserConfiguration
from api.tests.integrations._run_pipeline_helpers import USER_CONFIGURATION


class PresalesMockLLMService(MockLLMService):
    """
    Subclass of MockLLMService ensuring generated frames (tokens, tool calls, end of response)
    always travel DOWNSTREAM towards the consumer aggregator, even when responding to an upstream LLMContextFrame.
    """
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMContextFrame):
            await super().process_frame(frame, FrameDirection.DOWNSTREAM)
        else:
            await super().process_frame(frame, direction)


def _build_multi_node_workflow_definition() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are the greeting agent. Greet the caller.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to SPX Enterprise Voice.",
                },
            },
            {
                "id": "agent1",
                "type": "agentNode",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "Sales Representative",
                    "prompt": "You qualify commercial leads and transfer to technical specialist.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "agent2",
                "type": "agentNode",
                "position": {"x": 0, "y": 400},
                "data": {
                    "name": "Technical Specialist",
                    "prompt": "You handle technical architecture reviews and schedule demos.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 600},
                "data": {
                    "name": "End Call",
                    "prompt": "Politely close the call and say goodbye.",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-agent1",
                "source": "start",
                "target": "agent1",
                "data": {
                    "label": "Go To Sales",
                    "condition": "Caller wants sales or pricing.",
                },
            },
            {
                "id": "agent1-agent2",
                "source": "agent1",
                "target": "agent2",
                "data": {
                    "label": "Go To Tech Specialist",
                    "condition": "Caller requires deep technical architecture.",
                },
            },
            {
                "id": "agent2-end",
                "source": "agent2",
                "target": "end",
                "data": {
                    "label": "End Call",
                    "condition": "Technical demo successfully booked.",
                },
            },
        ],
    }


async def _create_test_environment(db_session, async_session, suffix: str):
    org = OrganizationModel(provider_id=f"mn-org-{suffix}")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id=f"mn-user-{suffix}",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()

    await db_session.update_user_configuration(
        user_id=user.id,
        configuration=UserConfiguration.model_validate(USER_CONFIGURATION),
    )

    wf_def = _build_multi_node_workflow_definition()
    workflow = await db_session.create_workflow(
        name=f"Multi-Node Acceptance Workflow {suffix}",
        workflow_definition=wf_def,
        user_id=user.id,
        organization_id=org.id,
    )
    return user, workflow


@pytest.mark.asyncio
async def test_multi_node_sequential_execution_start_to_end(
    db_session,
    async_session,
    test_client_factory,
):
    """
    Verify complete multi-node runtime execution across:
    Start -> Agent 1 (Sales) -> Agent 2 (Tech) -> End.
    Proves state transfer, tool generation, node transitions, and call conclusion.
    """
    user, workflow = await _create_test_environment(db_session, async_session, "sequential")

    initial_def = await db_session.get_workflow_by_id(workflow.id)
    initial_nodes_count = len(initial_def.workflow_definition["nodes"])

    llm_responses = [
        # Turn 0: Session Init (greeting at Start)
        PresalesMockLLMService(mock_steps=[], chunk_delay=0.001),
        # Turn 1: Transition Start -> Agent 1 (Sales)
        PresalesMockLLMService(
            mock_steps=[
                PresalesMockLLMService.create_mixed_chunks(
                    "Transferring to sales representative.",
                    "go_to_sales",
                    {},
                    tool_call_id="call_sales",
                ),
                PresalesMockLLMService.create_text_chunks("Hello, Sales here. How can I help with SPX Voice?"),
            ],
            chunk_delay=0.001,
        ),
        # Turn 2: Transition Agent 1 -> Agent 2 (Tech Specialist)
        PresalesMockLLMService(
            mock_steps=[
                PresalesMockLLMService.create_mixed_chunks(
                    "Connecting you with our technical specialist.",
                    "go_to_tech_specialist",
                    {},
                    tool_call_id="call_tech",
                ),
                PresalesMockLLMService.create_text_chunks("Technical specialist on the line. Let's discuss your architecture."),
            ],
            chunk_delay=0.001,
        ),
        # Turn 3: Transition Agent 2 -> End
        PresalesMockLLMService(
            mock_steps=[
                PresalesMockLLMService.create_mixed_chunks(
                    "Demo is booked for Tuesday. Goodbye!",
                    "end_call",
                    {},
                    tool_call_id="call_end",
                ),
            ],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                side_effect=llm_responses,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            # 1. Initialize Session at Start
            init_res = await client.post(f"/api/v1/workflow/{workflow.id}/text-chat/sessions", json={})
            assert init_res.status_code == 200
            session = init_res.json()
            run_id = session["workflow_run_id"]

            assert session["checkpoint"]["current_node_id"] == "start"
            assert "Start" in session["gathered_context"].get("nodes_visited", [])
            assert session["session_data"]["turns"][0]["assistant_message"]["text"] == "Welcome to SPX Enterprise Voice."

            # 2. Turn 1: Transition to Sales (Agent 1)
            t1_res = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{run_id}/messages",
                json={"text": "I need commercial pricing for 30,000 calls/day", "expected_revision": session["revision"]},
            )
            assert t1_res.status_code == 200
            t1_data = t1_res.json()
            assert t1_data["checkpoint"]["current_node_id"] == "agent1"
            assert "Sales Representative" in t1_data["gathered_context"].get("nodes_visited", [])
            assert "Sales here" in t1_data["session_data"]["turns"][1]["assistant_message"]["text"]

            # 3. Turn 2: Transition to Technical Specialist (Agent 2)
            t2_res = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{run_id}/messages",
                json={"text": "I need to review LiveKit SIP trunk latency", "expected_revision": t1_data["revision"]},
            )
            assert t2_res.status_code == 200
            t2_data = t2_res.json()
            assert t2_data["checkpoint"]["current_node_id"] == "agent2"
            assert "Technical Specialist" in t2_data["gathered_context"].get("nodes_visited", [])
            assert "Technical specialist on the line" in t2_data["session_data"]["turns"][2]["assistant_message"]["text"]

            # 4. Turn 3: Conclude Call (End Node)
            t3_res = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{run_id}/messages",
                json={"text": "Everything is confirmed. Thank you.", "expected_revision": t2_data["revision"]},
            )
            assert t3_res.status_code == 200
            t3_data = t3_res.json()
            assert t3_data["checkpoint"]["current_node_id"] == "end"
            assert "End Call" in t3_data["gathered_context"].get("nodes_visited", [])
            assert "Demo is booked" in t3_data["session_data"]["turns"][3]["assistant_message"]["text"]

    # Verify workflow definition was not modified by the execution
    final_def = await db_session.get_workflow_by_id(workflow.id)
    assert len(final_def.workflow_definition["nodes"]) == initial_nodes_count


@pytest.mark.asyncio
async def test_multi_node_simultaneous_sessions_state_isolation(
    db_session,
    async_session,
    test_client_factory,
):
    """
    Verify two concurrent sessions on the same multi-node workflow maintain
    completely separate conversational state, checkpoints, revisions, and run IDs.
    """
    user, workflow = await _create_test_environment(db_session, async_session, "simultaneous")

    all_llms = [
        PresalesMockLLMService(mock_steps=[], chunk_delay=0.001),  # 1. init A
        PresalesMockLLMService(mock_steps=[], chunk_delay=0.001),  # 2. init B
        PresalesMockLLMService(
            mock_steps=[
                PresalesMockLLMService.create_mixed_chunks("Going to Sales.", "go_to_sales", {}),
                PresalesMockLLMService.create_text_chunks("Welcome to Sales A."),
            ],
            chunk_delay=0.001,
        ),  # 3. msg A
        PresalesMockLLMService(
            mock_steps=[PresalesMockLLMService.create_text_chunks("Still at Start for B.")],
            chunk_delay=0.001,
        ),  # 4. msg B
    ]

    async with test_client_factory(user) as client:
        with (
            patch("api.services.workflow.text_chat_runner.create_llm_service", side_effect=all_llms),
            patch("api.services.workflow.text_chat_runner.db_client.has_active_recordings", new=AsyncMock(return_value=False)),
        ):
            # 1. Initialize Session A
            res_a = await client.post(f"/api/v1/workflow/{workflow.id}/text-chat/sessions", json={})
            assert res_a.status_code == 200
            sess_a = res_a.json()

            # 2. Initialize Session B
            res_b = await client.post(f"/api/v1/workflow/{workflow.id}/text-chat/sessions", json={})
            assert res_b.status_code == 200
            sess_b = res_b.json()

            # Verify completely distinct Run IDs
            assert sess_a["workflow_run_id"] != sess_b["workflow_run_id"]

            # 3. Advance Session A to Agent 1
            msg_a = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{sess_a['workflow_run_id']}/messages",
                json={"text": "Transfer me to Sales", "expected_revision": sess_a["revision"]},
            )
            assert msg_a.status_code == 200
            data_a = msg_a.json()
            assert data_a["checkpoint"]["current_node_id"] == "agent1"
            assert data_a["revision"] > sess_a["revision"]

            # 4. Advance Session B (stays on Start)
            msg_b = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{sess_b['workflow_run_id']}/messages",
                json={"text": "General question", "expected_revision": sess_b["revision"]},
            )
            assert msg_b.status_code == 200
            data_b = msg_b.json()
            # Session B is strictly isolated at Start node
            assert data_b["checkpoint"]["current_node_id"] == "start"
            assert "Welcome to Sales A." not in data_b["session_data"]["turns"][-1]["assistant_message"]["text"]


@pytest.mark.asyncio
async def test_multi_node_stale_revision_conflict_handling(
    db_session,
    async_session,
    test_client_factory,
):
    """
    Verify concurrency protection: sending a turn with a stale expected_revision
    is rejected with HTTP 409 Conflict, preventing race conditions or corrupted turn state.
    """
    user, workflow = await _create_test_environment(db_session, async_session, "conflict")

    llm_responses = [
        PresalesMockLLMService(mock_steps=[], chunk_delay=0.001),
        PresalesMockLLMService(mock_steps=[PresalesMockLLMService.create_text_chunks("Turn 1 response.")], chunk_delay=0.001),
    ]

    async with test_client_factory(user) as client:
        with (
            patch("api.services.workflow.text_chat_runner.create_llm_service", side_effect=llm_responses),
            patch("api.services.workflow.text_chat_runner.db_client.has_active_recordings", new=AsyncMock(return_value=False)),
        ):
            init_res = await client.post(f"/api/v1/workflow/{workflow.id}/text-chat/sessions", json={})
            session = init_res.json()
            run_id = session["workflow_run_id"]

            # First message succeeds
            msg1 = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{run_id}/messages",
                json={"text": "Hello", "expected_revision": session["revision"]},
            )
            assert msg1.status_code == 200

            # Stale message with old revision is rejected with HTTP 409
            stale_msg = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{run_id}/messages",
                json={"text": "Concurrent duplicate turn", "expected_revision": session["revision"]},
            )
            assert stale_msg.status_code == 409
            assert "revision" in str(stale_msg.json().get("detail", "")).lower()
