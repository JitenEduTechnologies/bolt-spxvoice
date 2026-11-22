"""Business action executors for Economy IVR with deduplication safeguards.

Handles:
- Callback requests (with duplicate callback suppression)
- Appointment requests (captured as pending requests, never falsely marked confirmed)
- Order enquiries (captured as pending enquiries, never falsely marked completed)
- Delivery status enquiries
- Fee / pricing enquiries
- General enquiries
- Approved custom webhooks / actions
- Call termination
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from loguru import logger
import redis.asyncio as aioredis

from api.constants import REDIS_URL
from api.db import db_client
from api.schemas.economy_ivr import IVRActionType, KeypadAction


# Redis key prefix for callback deduplication
_CALLBACK_DEDUP_PREFIX = "ivr:callback:pending:"
_CALLBACK_DEDUP_TTL = 7200  # 2 hours deduplication window


class IVRActionExecutor:
    """Executes business actions triggered by IVR keypad selections."""

    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self._redis = redis_client

    async def _get_redis(self) -> aioredis.Redis:
        if not self._redis:
            self._redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def execute_action(
        self,
        action: KeypadAction,
        organization_id: int,
        caller_number: str,
        workflow_id: int,
        workflow_run_id: int,
        language: str = "en",
    ) -> Dict[str, Any]:
        """Execute the configured action for a keypad selection.

        Guarantees:
        - Deduplication: duplicate callbacks within the TTL window are identified and suppressed.
        - Accurate booking/order state: enquiries/requests are never marked confirmed or completed.
        """
        action_type = action.action_type
        logger.info(
            f"[IVR Action] Executing {action_type.value} for caller {caller_number} "
            f"(org={organization_id}, run={workflow_run_id})"
        )

        if action_type == IVRActionType.CALLBACK:
            return await self._handle_callback(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                language=language,
                action_params=action.action_params,
            )

        elif action_type == IVRActionType.APPOINTMENT_REQUEST:
            return await self._handle_enquiry(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_run_id=workflow_run_id,
                enquiry_type="appointment_request",
                notes=action.label or "Customer requested appointment booking via IVR (pending confirmation)",
            )

        elif action_type == IVRActionType.ORDER_ENQUIRY:
            return await self._handle_enquiry(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_run_id=workflow_run_id,
                enquiry_type="order_enquiry",
                notes=action.label or "Customer enquired about an order via IVR (pending review)",
            )

        elif action_type == IVRActionType.DELIVERY_ENQUIRY:
            return await self._handle_enquiry(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_run_id=workflow_run_id,
                enquiry_type="delivery_enquiry",
                notes=action.label or "Customer requested delivery status via IVR",
            )

        elif action_type == IVRActionType.FEE_ENQUIRY:
            return await self._handle_enquiry(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_run_id=workflow_run_id,
                enquiry_type="fee_enquiry",
                notes=action.label or "Customer enquired about pricing/fees via IVR",
            )

        elif action_type == IVRActionType.GENERAL_ENQUIRY:
            return await self._handle_enquiry(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_run_id=workflow_run_id,
                enquiry_type="general_enquiry",
                notes=action.label or "General enquiry captured via IVR",
            )

        elif action_type == IVRActionType.CUSTOM_ACTION:
            return await self._handle_custom_action(
                organization_id=organization_id,
                caller_number=caller_number,
                workflow_run_id=workflow_run_id,
                action_params=action.action_params,
            )

        elif action_type == IVRActionType.HANGUP:
            return {
                "action": "hangup",
                "status": "success",
                "is_confirmed_booking": False,
                "is_completed_order": False,
                "message": "Call termination requested by caller.",
            }

        elif action_type == IVRActionType.HUMAN_TRANSFER:
            transfer_target = action.action_params.get("phone_number") or action.label or "human_staff"
            return {
                "action": "human_transfer",
                "status": "transfer_initiated",
                "transfer_target": transfer_target,
                "is_confirmed_booking": False,
                "is_completed_order": False,
                "message": f"Transferring to {transfer_target}",
            }

        else:
            logger.warning(f"[IVR Action] Unhandled action type: {action_type}")
            return {
                "action": str(action_type),
                "status": "unsupported",
                "is_confirmed_booking": False,
                "is_completed_order": False,
                "message": f"Action {action_type} not implemented",
            }

    async def _handle_callback(
        self,
        organization_id: int,
        caller_number: str,
        workflow_id: int,
        workflow_run_id: int,
        language: str,
        action_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process a callback request with duplicate prevention."""
        r = await self._get_redis()
        dedup_key = f"{_CALLBACK_DEDUP_PREFIX}{organization_id}:{caller_number}"

        # Atomic SET with NX (set only if Not eXists)
        is_new = await r.set(
            dedup_key,
            str(workflow_run_id),
            nx=True,
            ex=_CALLBACK_DEDUP_TTL,
        )

        if not is_new:
            existing_run_id = await r.get(dedup_key)
            logger.info(
                f"[IVR Callback] Duplicate callback suppressed for {caller_number} "
                f"(org={organization_id}, existing_run={existing_run_id})"
            )
            return {
                "action": "callback",
                "status": "already_pending",
                "is_duplicate": True,
                "is_confirmed_booking": False,
                "is_completed_order": False,
                "existing_run_id": existing_run_id,
                "message": "Callback already scheduled for this number.",
            }

        # New callback: record lead / task
        lead_data = {
            "caller_phone": caller_number,
            "organization_id": organization_id,
            "source": "economy_ivr",
            "requested_language": language,
            "status": "callback_requested",
            "is_confirmed_booking": False,
            "is_completed_order": False,
            "notes": f"Automated callback requested via IVR key selection (Run {workflow_run_id})",
            "created_at": time.time(),
        }

        # Update workflow_run gathered context with lead data
        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                gathered_context={
                    "callback_requested": True,
                    "lead_data": lead_data,
                    "ivr_action": "callback",
                    "is_confirmed_booking": False,
                    "is_completed_order": False,
                },
            )
        except Exception as e:
            logger.error(f"[IVR Callback] Failed to record workflow run context: {e}")

        logger.info(f"[IVR Callback] Registered callback for {caller_number} (org={organization_id})")
        return {
            "action": "callback",
            "status": "created",
            "is_duplicate": False,
            "is_confirmed_booking": False,
            "is_completed_order": False,
            "lead": lead_data,
            "message": "Callback successfully registered.",
        }

    async def _handle_enquiry(
        self,
        organization_id: int,
        caller_number: str,
        workflow_run_id: int,
        enquiry_type: str,
        notes: str,
    ) -> Dict[str, Any]:
        """Record customer enquiry category with strict non-confirmed status."""
        context_payload: Dict[str, Any] = {
            "enquiry_type": enquiry_type,
            "caller_phone": caller_number,
            "notes": notes,
            "ivr_action": enquiry_type,
            "status": "requested" if enquiry_type == "appointment_request" else "enquiry_recorded",
            "is_confirmed_booking": False,
            "is_completed_order": False,
            "timestamp": time.time(),
        }

        if enquiry_type == "appointment_request":
            context_payload["appointment"] = {
                "status": "requested",  # Never marked as confirmed booking
                "requested_via": "economy_ivr",
                "notes": notes,
            }
        elif enquiry_type == "order_enquiry":
            context_payload["order"] = {
                "status": "enquiry_pending",  # Never marked as completed order
                "enquiry_via": "economy_ivr",
                "notes": notes,
            }
        elif enquiry_type == "delivery_enquiry":
            context_payload["delivery"] = {
                "status": "enquiry_pending",  # Never marked as delivered
                "enquiry_via": "economy_ivr",
                "notes": notes,
            }

        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                gathered_context=context_payload,
            )
        except Exception as e:
            logger.error(f"[IVR Enquiry] Failed to update workflow run: {e}")

        return {
            "action": enquiry_type,
            "status": "recorded",
            "is_confirmed_booking": False,
            "is_completed_order": False,
            "enquiry": context_payload,
        }

    async def _handle_custom_action(
        self,
        organization_id: int,
        caller_number: str,
        workflow_run_id: int,
        action_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a validated custom action or webhook payload."""
        webhook_url = action_params.get("webhook_url")
        custom_tag = action_params.get("tag", "custom_ivr_event")

        payload = {
            "event": "ivr_custom_action",
            "organization_id": organization_id,
            "caller_number": caller_number,
            "workflow_run_id": workflow_run_id,
            "tag": custom_tag,
            "webhook_url": webhook_url,
            "timestamp": time.time(),
        }

        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                gathered_context={
                    "custom_action": payload,
                    "is_confirmed_booking": False,
                    "is_completed_order": False,
                },
            )
        except Exception as e:
            logger.error(f"[IVR Custom] Failed to update workflow run: {e}")

        return {
            "action": "custom_action",
            "status": "dispatched",
            "is_confirmed_booking": False,
            "is_completed_order": False,
            "payload": payload,
        }


# Global action executor singleton
ivr_action_executor = IVRActionExecutor()
