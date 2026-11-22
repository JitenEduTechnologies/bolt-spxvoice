"""Common filter utilities for database queries."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Float, Integer, Text, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from api.db.models import WorkflowModel, WorkflowRunModel


def get_workflow_run_order_clause(
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
):
    """
    Get the order clause for workflow run queries.

    Args:
        sort_by: Field to sort by ('duration', 'created_at', etc.)
        sort_order: 'asc' or 'desc'

    Returns:
        SQLAlchemy order clause
    """
    # Determine sort column
    if sort_by == "duration":
        sort_column = WorkflowRunModel.cost_info.op("->>")(
            "call_duration_seconds"
        ).cast(Float)
    elif sort_by == "cost":
        # Total estimated/reconciled INR cost recorded on the run.
        sort_column = cast(
            cast(WorkflowRunModel.cost_info, JSONB)
            .op("->")("actual_cost")
            .op("->>")("total_inr"),
            Float,
        )
    else:
        # Default to created_at
        sort_column = WorkflowRunModel.created_at

    # Apply sort order
    if sort_order == "asc":
        return sort_column.asc().nullslast()
    else:
        return sort_column.desc().nullslast()


# Mapping of attribute names to database fields
ATTRIBUTE_FIELD_MAPPING = {
    "dateRange": "created_at",
    "dispositionCode": "gathered_context.mapped_call_disposition",
    "duration": "cost_info.call_duration_seconds",
    "status": "is_completed",
    "tokenUsage": "cost_info.total_cost_usd",
    "runId": "id",
    "workflowId": "workflow_id",
    "campaignId": "campaign_id",
    "callTags": "gathered_context.call_tags",
    "callerNumber": "initial_context.caller_number",
    "calledNumber": "initial_context.called_number",
    # CP-02 cost analytics filters
    "costRange": "cost_info.actual_cost.total_inr",
    "billingStatus": "cost_info.billing_status",
    "callOrigin": "derived.call_origin",
    "engine": "mode",
    "direction": "call_type",
    "organizationId": "workflow.organization_id",
    "search": "derived.search",
}


def apply_workflow_run_filters(
    base_query,
    filters: Optional[List[Dict[str, Any]]] = None,
):
    """
    Apply filters to a workflow run query.

    Supports filtering by:
    - dateRange: Filter by created_at date range
    - dispositionCode: Filter by gathered_context.mapped_call_disposition
    - duration: Filter by usage_info.call_duration_seconds range
    - status: Filter by is_completed status
    - tokenUsage: Filter by cost_info.total_cost_usd range
    - runId: Filter by workflow run ID (exact match)
    - workflowId: Filter by workflow ID (exact match)
    - callTags: Filter by gathered_context.call_tags (array of strings)
    - callerNumber: Filter by initial_context.caller_number (text search)
    - calledNumber: Filter by initial_context.called_number (text search)

    Args:
        base_query: The base SQLAlchemy query to apply filters to
        filters: List of filter dictionaries with structure:
            {"attribute": "filterName", "type": "filterType", "value": {...}}

            Where type is one of:
            - "dateRange": Date range filter with {"from": ..., "to": ...}
            - "multiSelect": Multi-select filter with {"codes": [...]}
            - "numberRange": Number range filter with {"min": ..., "max": ...}
            - "number": Exact number filter with {"value": number}
            - "text": Text search filter with {"value": string}
            - "radio": Radio/status filter with {"status": ...}
            - "tags": Tags filter with {"codes": [...]}

    Returns:
        The query with filters applied
    """

    if not filters:
        return base_query

    filter_conditions = []

    for filter_item in filters:
        attribute = filter_item.get("attribute")
        filter_type = filter_item.get("type")
        value = filter_item.get("value", {})

        # Resolve field from attribute mapping
        field = ATTRIBUTE_FIELD_MAPPING.get(attribute)
        if not field:
            # Skip unknown attributes
            continue

        # Apply the filter based on provided type
        if field and filter_type:
            if filter_type == "number" and field == "id":
                # Filter by exact workflow run ID
                if value.get("value") is not None:
                    filter_conditions.append(WorkflowRunModel.id == value["value"])

            elif filter_type == "number" and field == "workflow_id":
                # Filter by exact workflow ID
                if value.get("value") is not None:
                    filter_conditions.append(
                        WorkflowRunModel.workflow_id == value["value"]
                    )

            elif filter_type == "number" and field == "campaign_id":
                if value.get("value") is not None:
                    filter_conditions.append(
                        WorkflowRunModel.campaign_id == value["value"]
                    )

            elif filter_type == "dateRange" and field == "created_at":
                # Same as attribute-based dateRange
                if value.get("from"):
                    filter_conditions.append(
                        WorkflowRunModel.created_at
                        >= datetime.fromisoformat(value["from"])
                    )
                if value.get("to"):
                    filter_conditions.append(
                        WorkflowRunModel.created_at
                        <= datetime.fromisoformat(value["to"])
                    )

            elif (
                filter_type == "multiSelect"
                and field == "gathered_context.mapped_call_disposition"
            ):
                codes = value.get("codes", [])
                if codes:
                    # Use ->> operator for compatibility with all PostgreSQL versions
                    # (subscript [] only works in PostgreSQL 14+)
                    filter_conditions.append(
                        cast(WorkflowRunModel.gathered_context, JSONB)
                        .op("->>")("mapped_call_disposition")
                        .in_(codes)
                    )

            elif filter_type == "radio" and field == "is_completed":
                status = value.get("status")
                if status == "completed":
                    filter_conditions.append(WorkflowRunModel.is_completed == True)
                elif status == "in_progress":
                    filter_conditions.append(WorkflowRunModel.is_completed == False)

            elif (
                filter_type in ("tags", "multiSelect")
                and field == "gathered_context.call_tags"
            ):
                tags = value.get("codes", [])
                if tags:
                    # The gathered_context column is JSON type (not JSONB)
                    # JSON type doesn't support subscripting, so we must cast to JSONB first
                    # Then extract call_tags and check containment with @>
                    gathered_context_jsonb = cast(
                        WorkflowRunModel.gathered_context, JSONB
                    )
                    # Use -> operator with literal text key to get call_tags as JSONB
                    call_tags = gathered_context_jsonb.op("->")("call_tags")
                    filter_conditions.append(call_tags.op("@>")(func.cast(tags, JSONB)))

            elif filter_type == "text" and field == "initial_context.caller_number":
                phone = value.get("value", "").strip()
                if phone:
                    # Cast ->> result to Text so .contains() emits LIKE,
                    # not the JSONB @> operator (the default for untyped exprs).
                    filter_conditions.append(
                        cast(
                            cast(WorkflowRunModel.initial_context, JSONB).op("->>")(
                                "caller_number"
                            ),
                            Text,
                        ).contains(phone)
                    )

            elif filter_type == "text" and field == "initial_context.called_number":
                phone = value.get("value", "").strip()
                if phone:
                    filter_conditions.append(
                        cast(
                            cast(WorkflowRunModel.initial_context, JSONB).op("->>")(
                                "called_number"
                            ),
                            Text,
                        ).contains(phone)
                    )

            elif filter_type == "numberRange" and field == "cost_info.actual_cost.total_inr":
                total_inr = cast(
                    cast(WorkflowRunModel.cost_info, JSONB)
                    .op("->")("actual_cost")
                    .op("->>")("total_inr"),
                    Float,
                )
                if value.get("min") is not None:
                    filter_conditions.append(total_inr >= float(value["min"]))
                if value.get("max") is not None:
                    filter_conditions.append(total_inr <= float(value["max"]))

            elif filter_type == "multiSelect" and field == "cost_info.billing_status":
                codes = [c for c in value.get("codes", []) if isinstance(c, str)]
                if codes:
                    status_expr = cast(WorkflowRunModel.cost_info, JSONB).op("->>")(
                        "billing_status"
                    )
                    conds = []
                    if "NOT_COSTED" in codes:
                        conds.append(status_expr.is_(None))
                    real = [c for c in codes if c != "NOT_COSTED"]
                    if real:
                        conds.append(status_expr.in_(real))
                    filter_conditions.append(or_(*conds))

            elif filter_type == "multiSelect" and field == "derived.call_origin":
                from api.services.business import conversation_query as _cq

                origins = [c for c in value.get("codes", []) if isinstance(c, str)]
                conds = []
                if "simulated" in origins:
                    conds.append(_cq._simulated_condition())
                if "pstn" in origins:
                    conds.append(_cq._pstn_condition())
                if "browser" in origins:
                    conds.append(_cq._browser_condition())
                if conds:
                    filter_conditions.append(or_(*conds))

            elif filter_type == "multiSelect" and field == "mode":
                modes = [c for c in value.get("codes", []) if isinstance(c, str)]
                if modes:
                    filter_conditions.append(WorkflowRunModel.mode.in_(modes))

            elif filter_type == "radio" and field == "call_type":
                direction = value.get("status")
                if direction in ("inbound", "outbound"):
                    filter_conditions.append(WorkflowRunModel.call_type == direction)

            elif filter_type == "number" and field == "workflow.organization_id":
                if value.get("value") is not None:
                    filter_conditions.append(
                        WorkflowRunModel.workflow_id.in_(
                            select(WorkflowModel.id).where(
                                WorkflowModel.organization_id == int(value["value"])
                            )
                        )
                    )

            elif filter_type == "text" and field == "derived.search":
                needle = str(value.get("value", "")).strip().lower()
                if len(needle) >= 2:
                    from api.services.business.conversation_query import _escape_like

                    like = f"%{_escape_like(needle)}%"
                    filter_conditions.append(
                        or_(
                            func.lower(WorkflowRunModel.name).like(like, escape="!"),
                            func.lower(
                                func.coalesce(
                                    cast(WorkflowRunModel.initial_context, JSONB).op("->>")("phone_number"),
                                    "",
                                )
                            ).like(like, escape="!"),
                            func.lower(
                                func.coalesce(
                                    cast(WorkflowRunModel.initial_context, JSONB).op("->>")("caller_number"),
                                    "",
                                )
                            ).like(like, escape="!"),
                        )
                    )

            elif filter_type == "numberRange":
                min_val = value.get("min")
                max_val = value.get("max")

                if field == "cost_info.call_duration_seconds":
                    # Use ->> operator for compatibility with all PostgreSQL versions
                    # (subscript [] only works in PostgreSQL 14+)
                    duration_text = cast(WorkflowRunModel.usage_info, JSONB).op("->>")(
                        "call_duration_seconds"
                    )
                    if min_val is not None:
                        filter_conditions.append(
                            cast(duration_text, Integer) >= min_val
                        )
                    if max_val is not None:
                        filter_conditions.append(
                            cast(duration_text, Integer) <= max_val
                        )

                elif field == "cost_info.total_cost_usd":
                    # Use ->> operator for compatibility with all PostgreSQL versions
                    cost_text = cast(WorkflowRunModel.cost_info, JSONB).op("->>")(
                        "total_cost_usd"
                    )
                    if min_val is not None:
                        filter_conditions.append(cast(cost_text, Integer) >= min_val)
                    if max_val is not None:
                        filter_conditions.append(cast(cost_text, Integer) <= max_val)

    if filter_conditions:
        base_query = base_query.where(and_(*filter_conditions))

    return base_query
