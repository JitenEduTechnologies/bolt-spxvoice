"""D2C eCommerce Customer Service & Revenue Recovery Engine for SPX Voice.

Manages verified store orders, idempotent COD verification, order status checks,
delivery rescheduling, returns, and customer service follow-ups.
Backed by PostgreSQL table `ecommerce_order_records`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import EcommerceOrderRecordModel
from api.services.business.action_safeguards import action_safeguards, generate_idempotency_key
from api.services.business.identity_verification import identity_verifier


class EcommerceWorkflowController:
    """Manages D2C order workflows, COD verification, and returns."""

    async def upsert_order_record(
        self,
        organization_id: int,
        order_id: str,
        customer_name: str,
        customer_phone: str,
        items: List[Dict[str, Any]],
        total_amount: float | Decimal,
        payment_type: str = "COD",
        order_status: str = "NEW",
        delivery_status: Optional[str] = "PROCESSING",
        shipping_carrier: Optional[str] = None,
        tracking_number: Optional[str] = None,
        customer_email: Optional[str] = None,
        store_provider: str = "manual_import",
    ) -> Dict[str, Any]:
        """Creates or updates an authoritative eCommerce order record."""
        total_d = Decimal(str(total_amount))

        async with db_client.async_session() as session:
            stmt = select(EcommerceOrderRecordModel).where(
                EcommerceOrderRecordModel.organization_id == organization_id,
                EcommerceOrderRecordModel.order_id == order_id,
            )
            res = await session.execute(stmt)
            order = res.scalar_one_or_none()

            if order:
                order.customer_name = customer_name
                order.customer_phone = customer_phone
                order.customer_email = customer_email
                order.items = items
                order.total_amount = total_d
                order.payment_type = payment_type
                order.order_status = order_status
                order.delivery_status = delivery_status
                order.shipping_carrier = shipping_carrier
                order.tracking_number = tracking_number
                order.store_provider = store_provider
                order.last_sync = datetime.now(UTC)
            else:
                order = EcommerceOrderRecordModel(
                    organization_id=organization_id,
                    order_id=order_id,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    customer_email=customer_email,
                    items=items,
                    total_amount=total_d,
                    payment_type=payment_type,
                    order_status=order_status,
                    delivery_status=delivery_status,
                    shipping_carrier=shipping_carrier,
                    tracking_number=tracking_number,
                    store_provider=store_provider,
                    last_sync=datetime.now(UTC),
                )
                session.add(order)

            await session.commit()
            logger.info(f"[eCommerce Workflow] Upserted order {order_id} for {customer_name}: status={order_status}")

            return {
                "order_id": order_id,
                "customer_name": customer_name,
                "order_status": order_status,
                "payment_type": payment_type,
                "total_amount": float(total_d),
            }

    async def verify_cod_order(
        self,
        organization_id: int,
        order_id: str,
        customer_phone: str,
        confirmed: bool,
        workflow_run_id: Optional[int] = None,
        notes: str = "",
    ) -> Tuple[bool, Dict[str, Any]]:
        """Idempotently confirms or cancels a Cash On Delivery (COD) order.

        Prevents duplicate status updates when a customer repeats confirmation.
        """
        idempotency_key = generate_idempotency_key(
            organization_id=organization_id,
            action_type="CONFIRM_COD_ORDER",
            unique_identifiers={"order_id": order_id, "confirmed": confirmed},
        )

        async def _execute_confirmation() -> Dict[str, Any]:
            async with db_client.async_session() as session:
                stmt = select(EcommerceOrderRecordModel).where(
                    EcommerceOrderRecordModel.organization_id == organization_id,
                    EcommerceOrderRecordModel.order_id == order_id,
                )
                res = await session.execute(stmt)
                order = res.scalar_one_or_none()

                if not order:
                    raise ValueError(f"Order {order_id} not found in store database")

                history = list(order.interaction_history or [])
                now_iso = datetime.now(UTC).isoformat()

                if confirmed:
                    order.cod_confirmed = True
                    order.order_status = "CONFIRMED"
                    history.append({
                        "event": "COD_CONFIRMED",
                        "timestamp": now_iso,
                        "notes": notes or "Customer confirmed COD delivery verbally",
                        "run_id": workflow_run_id,
                    })
                else:
                    order.cod_confirmed = False
                    order.order_status = "CANCELLED_BY_CUSTOMER"
                    history.append({
                        "event": "COD_DECLINED",
                        "timestamp": now_iso,
                        "notes": notes or "Customer declined or cancelled COD order verbally",
                        "run_id": workflow_run_id,
                    })

                order.interaction_history = history
                status_val = order.order_status
                confirmed_val = order.cod_confirmed
                name_val = order.customer_name
                await session.commit()

                return {
                    "order_id": order_id,
                    "order_status": status_val,
                    "cod_confirmed": confirmed_val,
                    "customer_name": name_val,
                    "updated_at": now_iso,
                }

        is_new, result = await action_safeguards.execute_idempotent_action(
            action_type="CONFIRM_COD_ORDER",
            idempotency_key=idempotency_key,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            action_payload={"order_id": order_id, "confirmed": confirmed, "notes": notes},
            action_handler=_execute_confirmation,
        )

        return is_new, result

    async def reschedule_delivery(
        self,
        organization_id: int,
        order_id: str,
        rescheduled_date: date,
        workflow_run_id: Optional[int] = None,
        reason: str = "",
    ) -> Tuple[bool, Dict[str, Any]]:
        """Idempotently reschedules delivery date for an order."""
        idempotency_key = generate_idempotency_key(
            organization_id=organization_id,
            action_type="RESCHEDULE_DELIVERY",
            unique_identifiers={"order_id": order_id, "date": rescheduled_date.isoformat()},
        )

        async def _execute_reschedule() -> Dict[str, Any]:
            async with db_client.async_session() as session:
                stmt = select(EcommerceOrderRecordModel).where(
                    EcommerceOrderRecordModel.organization_id == organization_id,
                    EcommerceOrderRecordModel.order_id == order_id,
                )
                res = await session.execute(stmt)
                order = res.scalar_one_or_none()

                if not order:
                    raise ValueError(f"Order {order_id} not found in store database")

                order.delivery_rescheduled_date = rescheduled_date
                history = list(order.interaction_history or [])
                history.append({
                    "event": "DELIVERY_RESCHEDULED",
                    "rescheduled_date": rescheduled_date.isoformat(),
                    "reason": reason or "Customer requested alternate delivery date",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "run_id": workflow_run_id,
                })
                order.interaction_history = history
                await session.commit()

                return {
                    "order_id": order_id,
                    "rescheduled_date": rescheduled_date.isoformat(),
                    "status": "RESCHEDULED",
                }

        return await action_safeguards.execute_idempotent_action(
            action_type="RESCHEDULE_DELIVERY",
            idempotency_key=idempotency_key,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            action_payload={"order_id": order_id, "date": rescheduled_date.isoformat()},
            action_handler=_execute_reschedule,
        )


ecommerce_workflow = EcommerceWorkflowController()
ecommerce_service = ecommerce_workflow

