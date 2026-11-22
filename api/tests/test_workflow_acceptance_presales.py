"""Functional Acceptance Tests for Pre-Sales A: Workflow Editor, Graph Validation & Settings."""

import copy
import pytest
from pydantic import ValidationError

from api.routes.public_embed import validate_origin
from api.routes.workflow import _validate_workflow_definition
from api.services.configuration.masking import mask_workflow_configurations
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


@pytest.fixture
def base_valid_workflow():
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "Greet caller",
                    "is_start": True,
                    "allow_interrupt": True,
                },
            },
            {
                "id": "agent_1",
                "type": "agentNode",
                "position": {"x": 200, "y": 0},
                "data": {
                    "name": "Agent 1",
                    "prompt": "Assist caller with inquiry",
                    "allow_interrupt": True,
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 400, "y": 0},
                "data": {
                    "name": "End",
                    "prompt": "Say goodbye",
                    "is_end": True,
                },
            },
        ],
        "edges": [
            {
                "id": "e_start_agent",
                "source": "start",
                "target": "agent_1",
                "data": {"label": "to_agent", "condition": "call started"},
            },
            {
                "id": "e_agent_end",
                "source": "agent_1",
                "target": "end",
                "data": {"label": "to_end", "condition": "inquiry resolved"},
            },
        ],
    }


def test_01_valid_multi_node_workflow_passes_dto_and_graph(base_valid_workflow):
    """Valid start -> agent -> end workflow compiles and passes graph validation."""
    dto = ReactFlowDTO.model_validate(base_valid_workflow)
    graph = WorkflowGraph(dto)
    assert graph.start_node_id == "start"
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2
    assert "agent_1" in graph.nodes["start"].out
    assert "end" in graph.nodes["agent_1"].out


def test_02_agent_node_without_prompt_fails_dto_validation(base_valid_workflow):
    """Agent node missing prompt raises ValueError during DTO validation."""
    invalid_wf = copy.deepcopy(base_valid_workflow)
    invalid_wf["nodes"][1]["data"]["prompt"] = ""
    with pytest.raises(ValidationError) as exc_info:
        ReactFlowDTO.model_validate(invalid_wf)
    assert "prompt is required for agent nodes" in str(exc_info.value).lower()


def test_03_disconnected_agent_node_fails_graph_constraints(base_valid_workflow):
    """An added Agent node with a prompt but no incoming edge fails graph constraint."""
    wf_with_disconnected = copy.deepcopy(base_valid_workflow)
    wf_with_disconnected["nodes"].append({
        "id": "agent_2",
        "type": "agentNode",
        "position": {"x": 600, "y": 0},
        "data": {
            "name": "Disconnected Agent",
            "prompt": "Handle escalation",
        },
    })
    dto = ReactFlowDTO.model_validate(wf_with_disconnected)
    with pytest.raises(ValueError) as exc_info:
        WorkflowGraph(dto)
    errors = exc_info.value.args[0]
    disconnected_errs = [
        e for e in errors
        if (getattr(e, "id", None) or (isinstance(e, dict) and e.get("id"))) == "agent_2"
        and "incoming edge" in (getattr(e, "message", None) or (isinstance(e, dict) and e.get("message", ""))).lower()
    ]
    assert len(disconnected_errs) == 1
    msg = getattr(disconnected_errs[0], "message", None) or disconnected_errs[0].get("message")
    assert "must have at least 1 incoming edge" in msg


def test_04_connecting_agent_node_resolves_graph_validation(base_valid_workflow):
    """Connecting the added Agent node with valid edges makes the graph valid."""
    wf = copy.deepcopy(base_valid_workflow)
    wf["nodes"].append({
        "id": "agent_2",
        "type": "agentNode",
        "position": {"x": 600, "y": 0},
        "data": {
            "name": "Escalation Agent",
            "prompt": "Handle tier 2 escalation",
        },
    })
    wf["edges"].append({
        "id": "e_agent1_agent2",
        "source": "agent_1",
        "target": "agent_2",
        "data": {"label": "escalate", "condition": "user requests supervisor"},
    })
    wf["edges"].append({
        "id": "e_agent2_end",
        "source": "agent_2",
        "target": "end",
        "data": {"label": "finish", "condition": "escalation resolved"},
    })
    dto = ReactFlowDTO.model_validate(wf)
    graph = WorkflowGraph(dto)
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 4
    assert "agent_2" in graph.nodes["agent_1"].out


def test_05_deleting_node_and_edges_leaves_clean_graph(base_valid_workflow):
    """Deleting a test node and its connected edges leaves the remaining workflow valid."""
    wf = copy.deepcopy(base_valid_workflow)
    # Add temporary node and edge
    wf["nodes"].append({
        "id": "temp_agent",
        "type": "agentNode",
        "position": {"x": 800, "y": 0},
        "data": {"name": "Temp", "prompt": "Temp prompt"},
    })
    wf["edges"].append({
        "id": "e_temp",
        "source": "agent_1",
        "target": "temp_agent",
        "data": {"label": "temp", "condition": "temp condition"},
    })
    # Now simulate deletion: remove temp_agent and e_temp
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] != "temp_agent"]
    wf["edges"] = [e for e in wf["edges"] if e["source"] != "temp_agent" and e["target"] != "temp_agent"]
    
    dto = ReactFlowDTO.model_validate(wf)
    graph = WorkflowGraph(dto)
    assert "temp_agent" not in graph.nodes
    assert len(graph.nodes) == 3


@pytest.mark.asyncio
async def test_06_validate_workflow_definition_pipeline(base_valid_workflow):
    """_validate_workflow_definition catches prompt errors and graph errors in pipeline."""
    # Test valid definition
    clean_errors = await _validate_workflow_definition(base_valid_workflow)
    assert len(clean_errors) == 0

    # Test missing prompt error
    broken_dto = copy.deepcopy(base_valid_workflow)
    broken_dto["nodes"][1]["data"]["prompt"] = ""
    prompt_errors = await _validate_workflow_definition(broken_dto)
    assert any(
        (getattr(e, "id", None) or (isinstance(e, dict) and e.get("id"))) == "agent_1"
        and "prompt is required" in (getattr(e, "message", None) or (isinstance(e, dict) and e.get("message", ""))).lower()
        for e in prompt_errors
    )

    # Test disconnected node error
    disconnected_wf = copy.deepcopy(base_valid_workflow)
    disconnected_wf["nodes"].append({
        "id": "unconnected",
        "type": "agentNode",
        "position": {"x": 500, "y": 500},
        "data": {"name": "Orphan", "prompt": "Has prompt"},
    })
    graph_errors = await _validate_workflow_definition(disconnected_wf)
    assert any(
        (getattr(e, "id", None) or (isinstance(e, dict) and e.get("id"))) == "unconnected"
        and "incoming edge" in (getattr(e, "message", None) or (isinstance(e, dict) and e.get("message", ""))).lower()
        for e in graph_errors
    )


def test_07_website_embedding_domain_validation():
    """Verify origin validation for allowed domains."""
    # Open / no domain restriction
    assert validate_origin("https://example.com", []) is True
    assert validate_origin("https://sub.client.com", ["*"]) is True

    # Explicit domain matching (handles www normalization)
    allowed = ["client.com", "partner.io"]
    assert validate_origin("https://client.com", allowed) is True
    assert validate_origin("https://www.client.com", allowed) is True
    assert validate_origin("https://partner.io", allowed) is True
    assert validate_origin("https://malicious.com", allowed) is False

    # Wildcard subdomain matching
    wildcard_allowed = ["*.trusted.org"]
    assert validate_origin("https://app.trusted.org", wildcard_allowed) is True
    assert validate_origin("https://nested.app.trusted.org", wildcard_allowed) is True
    assert validate_origin("https://untrusted.org", wildcard_allowed) is False


def test_08_workflow_settings_masking():
    """Verify sensitive keys in workflow configurations are masked."""
    configs = {
        "model_overrides": {
            "realtime": {
                "provider": "google_realtime",
                "api_key": "sensitive-realtime-key",
            }
        },
    }
    masked = mask_workflow_configurations(configs)
    realtime_key = masked["model_overrides"]["realtime"]["api_key"]
    assert realtime_key.startswith("****")
    assert "sensitive" not in realtime_key
