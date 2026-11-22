"""Schools & Educational Institution Workflow Engine for SPX Voice.

Manages authoritative student fee records, privacy-first balance verification,
polite fee reminders, admission inquiries, and parent-teacher meeting notices.
Backed by PostgreSQL table `school_fee_records`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import SchoolFeeRecordModel
from api.services.business.identity_verification import identity_verifier


class SchoolWorkflowController:
    """Manages verified fee records and polite parent communication workflows."""

    async def upsert_student_fee_record(
        self,
        organization_id: int,
        student_id: str,
        student_name: str,
        guardian_name: str,
        guardian_phone: str,
        class_section: str,
        academic_term: str,
        fee_category: str,
        total_fee: float | Decimal,
        paid_amount: float | Decimal = 0.0,
        concessions_waivers: float | Decimal = 0.0,
        due_date_val: Optional[date] = None,
        status: str = "pending",
        data_source: str = "manual_import",
        secondary_phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates or updates an authoritative student fee record."""
        total_d = Decimal(str(total_fee))
        paid_d = Decimal(str(paid_amount))
        waiver_d = Decimal(str(concessions_waivers))
        outstanding = max(Decimal("0.00"), total_d - paid_d - waiver_d)

        if outstanding == Decimal("0.00"):
            status = "paid"
        elif paid_d > Decimal("0.00"):
            status = "partial"

        if due_date_val is None:
            due_date_val = date.today()

        async with db_client.async_session() as session:
            stmt = select(SchoolFeeRecordModel).where(
                SchoolFeeRecordModel.organization_id == organization_id,
                SchoolFeeRecordModel.student_id == student_id,
                SchoolFeeRecordModel.academic_term == academic_term,
                SchoolFeeRecordModel.fee_category == fee_category,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()

            if record:
                record.student_name = student_name
                record.guardian_name = guardian_name
                record.guardian_phone = guardian_phone
                record.secondary_phone = secondary_phone
                record.class_section = class_section
                record.total_fee = total_d
                record.paid_amount = paid_d
                record.outstanding_amount = outstanding
                record.concessions_waivers = waiver_d
                record.due_date = due_date_val
                record.status = status
                record.data_source = data_source
                record.last_sync = datetime.now(UTC)
            else:
                record = SchoolFeeRecordModel(
                    organization_id=organization_id,
                    student_id=student_id,
                    student_name=student_name,
                    guardian_name=guardian_name,
                    guardian_phone=guardian_phone,
                    secondary_phone=secondary_phone,
                    class_section=class_section,
                    academic_term=academic_term,
                    fee_category=fee_category,
                    total_fee=total_d,
                    paid_amount=paid_d,
                    outstanding_amount=outstanding,
                    concessions_waivers=waiver_d,
                    due_date=due_date_val,
                    status=status,
                    data_source=data_source,
                    last_sync=datetime.now(UTC),
                )
                session.add(record)

            await session.commit()
            logger.info(
                f"[School Workflow] Upserted fee record for student {student_id} ({student_name}): "
                f"outstanding=₹{outstanding:.2f}, status={status}"
            )

            return {
                "student_id": student_id,
                "student_name": student_name,
                "outstanding_amount": float(outstanding),
                "status": status,
                "due_date": due_date_val.isoformat(),
            }

    async def evaluate_fee_reminder_eligibility(
        self,
        organization_id: int,
        student_id: str,
        academic_term: str,
        fee_category: str,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Rechecks latest authoritative records before placing or approving any fee reminder call.

        Prevents calling parents whose fee was recently paid, waived, or disputed.
        """
        async with db_client.async_session() as session:
            stmt = select(SchoolFeeRecordModel).where(
                SchoolFeeRecordModel.organization_id == organization_id,
                SchoolFeeRecordModel.student_id == student_id,
                SchoolFeeRecordModel.academic_term == academic_term,
                SchoolFeeRecordModel.fee_category == fee_category,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()

            if not record:
                return False, "Record not found in authoritative school database", {}

            # Authoritative status checks
            if record.status == "paid" or record.outstanding_amount <= Decimal("0.00"):
                return False, f"Fee already fully paid (₹{record.paid_amount:.2f} received). Reminder blocked.", {
                    "status": "paid",
                    "outstanding": 0.0,
                }

            if record.status in ("waived", "concession"):
                return False, "Fee record has active concession/waiver. Reminder blocked.", {
                    "status": record.status,
                    "outstanding": float(record.outstanding_amount),
                }

            if record.status == "disputed":
                return False, "Fee record is under administrative dispute review. Reminder blocked.", {
                    "status": "disputed",
                    "outstanding": float(record.outstanding_amount),
                }

            # Eligible for reminder
            reminder_type = "OVERDUE" if record.due_date < date.today() else "UPCOMING"
            data = {
                "student_id": record.student_id,
                "student_name": record.student_name,
                "guardian_name": record.guardian_name,
                "guardian_phone": record.guardian_phone,
                "class_section": record.class_section,
                "academic_term": record.academic_term,
                "fee_category": record.fee_category,
                "total_fee": float(record.total_fee),
                "paid_amount": float(record.paid_amount),
                "outstanding_amount": float(record.outstanding_amount),
                "due_date": record.due_date.isoformat(),
                "reminder_type": reminder_type,
            }
            return True, f"Eligible for {reminder_type} fee reminder", data

    async def get_verified_balance_for_caller(
        self,
        organization_id: int,
        caller_phone: str,
        student_id: str,
        provided_factors: Dict[str, Any],
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Provides verified balance only after identity factors match."""
        async with db_client.async_session() as session:
            stmt = select(SchoolFeeRecordModel).where(
                SchoolFeeRecordModel.organization_id == organization_id,
                SchoolFeeRecordModel.student_id == student_id,
            )
            res = await session.execute(stmt)
            record = res.scalars().first()

            if not record:
                return False, "Student record not found", None

            record_dict = {
                "admission_no": record.student_id,
                "student_name": record.student_name,
                "registered_guardian_name": record.guardian_name,
                "guardian_phone": record.guardian_phone,
            }

            is_verified, reason, fallback_msg = identity_verifier.evaluate_access(
                domain="schools",
                caller_phone=caller_phone,
                provided_factors=provided_factors,
                authoritative_record=record_dict,
            )

            if not is_verified:
                return False, fallback_msg, None

            return True, None, {
                "student_name": record.student_name,
                "class_section": record.class_section,
                "academic_term": record.academic_term,
                "fee_category": record.fee_category,
                "outstanding_amount": float(record.outstanding_amount),
                "due_date": record.due_date.isoformat(),
                "status": record.status,
            }


school_workflow = SchoolWorkflowController()
school_service = school_workflow

