import csv
import hashlib
from io import StringIO
from typing import List, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.services.campaign.source_sync import (
    CampaignSourceSyncService,
    ValidationError,
    ValidationResult,
)
from api.services.storage import storage_fs
from api.services.telephony.compliance import compliance_manager, normalize_phone_number


class CSVSyncService(CampaignSourceSyncService):
    """Implementation for CSV file synchronization with chunked insertion,
    phone deduplication, and Do-Not-Call (DNC) suppression checks.
    """

    async def _fetch_csv_data(self, file_key: str) -> List[List[str]]:
        """Download and parse CSV file from storage. Returns all rows including header."""
        signed_url = await storage_fs.aget_signed_url(
            file_key, expiration=3600, use_internal_endpoint=True
        )

        if not signed_url:
            raise ValueError(f"Failed to access CSV file: {file_key}")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(signed_url)
                response.raise_for_status()
                csv_content = response.text
            except httpx.HTTPError as e:
                logger.error(f"Failed to download CSV file: {e} for url: {signed_url}")
                raise ValueError(f"Failed to download CSV file from storage: {str(e)}")

        return self._parse_csv(csv_content)

    async def validate_source(
        self,
        source_id: str,
        organization_id: Optional[int] = None,
        field_mapping: Optional[dict] = None,
    ) -> ValidationResult:
        """Validate a CSV source file for campaign creation."""
        try:
            csv_data = await self._fetch_csv_data(source_id)
        except ValueError as e:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(message=str(e)),
            )

        if not csv_data or len(csv_data) < 2:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="CSV file must have a header row and at least one data row"
                ),
            )

        headers = csv_data[0]
        data_rows = csv_data[1:]

        return self.validate_source_data(headers, data_rows, field_mapping=field_mapping)

    async def sync_source_data(self, campaign_id: int) -> int:
        """Fetches data from CSV file in S3/MinIO and creates queued_runs.
        Features:
        - Deduplication across phone numbers in the batch.
        - DNC suppression filtering against customer_suppression_dnc.
        - Chunked database inserts in batches of 1,000 for large lists (up to 20k+ contacts).
        """
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        file_key = campaign.source_id
        csv_data = await self._fetch_csv_data(file_key)

        if not csv_data or len(csv_data) < 2:
            logger.warning(f"No data found in CSV for campaign {campaign_id}")
            return 0

        headers = self.normalize_headers(csv_data[0])
        rows = csv_data[1:]

        meta = campaign.orchestrator_metadata or {}
        field_mapping = meta.get("field_mapping")
        resolved = self.resolve_column_mapping(headers, field_mapping)
        phone_col = resolved["phone_col"]
        name_col = resolved["name_col"]

        # Create hash of file_key for consistent source_uuid prefix
        file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]

        # Fetch existing queued runs to ensure re-import idempotency (T4-02)
        existing_uuids = await db_client.get_existing_source_uuids(campaign_id)

        seen_phones = set()
        queued_runs = []
        suppressed_count = 0
        duplicate_count = 0

        for idx, row_values in enumerate(rows, 1):
            # Generate unique source UUID: csv_{hash(source_id)}_row_{idx}
            source_uuid = f"csv_{file_hash}_row_{idx}"

            if source_uuid in existing_uuids:
                duplicate_count += 1
                continue

            # Pad row to match headers length
            padded_row = row_values + [""] * (len(headers) - len(row_values))

            # Create context variables dict
            context_vars = dict(zip(headers, padded_row))

            raw_phone = context_vars.get(phone_col)
            if not raw_phone:
                logger.debug(f"Skipping row {idx}: no phone in column '{phone_col}'")
                continue

            norm_phone = normalize_phone_number(raw_phone)

            # Deduplication
            if norm_phone in seen_phones:
                duplicate_count += 1
                continue
            seen_phones.add(norm_phone)

            # Check DNC suppression
            is_suppressed, reason = await compliance_manager.is_suppressed_db(
                organization_id=campaign.organization_id,
                phone_number=norm_phone,
            )
            if is_suppressed:
                logger.info(
                    f"[Campaign Sync] Suppressing DNC phone {norm_phone} for campaign {campaign_id}: {reason}"
                )
                suppressed_count += 1
                continue

            # Update context_vars with normalized phone and canonical customer_name
            context_vars["phone_number"] = norm_phone
            if name_col and context_vars.get(name_col):
                val_name = str(context_vars[name_col]).strip()
                if "customer_name" not in context_vars:
                    context_vars["customer_name"] = val_name
                if "name" not in context_vars:
                    context_vars["name"] = val_name

            queued_runs.append(
                {
                    "campaign_id": campaign_id,
                    "source_uuid": source_uuid,
                    "context_variables": context_vars,
                    "state": "queued",
                }
            )

        logger.info(
            f"[Campaign Sync] Parsed {len(rows)} rows: {len(queued_runs)} valid queued runs, "
            f"{suppressed_count} DNC suppressed, {duplicate_count} duplicates skipped."
        )

        # Chunked Bulk insert in batches of 1,000
        BATCH_SIZE = 1000
        for i in range(0, len(queued_runs), BATCH_SIZE):
            chunk = queued_runs[i : i + BATCH_SIZE]
            await db_client.bulk_create_queued_runs(chunk)
            logger.debug(
                f"[Campaign Sync] Inserted chunk {i // BATCH_SIZE + 1}/{(len(queued_runs) + BATCH_SIZE - 1) // BATCH_SIZE} ({len(chunk)} runs)"
            )

        # Update campaign total_rows from authoritative database count
        total_in_db = await db_client.count_queued_runs(campaign_id)
        await db_client.update_campaign(
            campaign_id=campaign_id,
            total_rows=total_in_db,
            source_sync_status="completed",
        )

        return len(queued_runs)

    def _parse_csv(self, csv_content: str) -> List[List[str]]:
        """Parse CSV content into rows"""
        try:
            csv_file = StringIO(csv_content)
            reader = csv.reader(csv_file)
            return list(reader)
        except Exception as e:
            logger.error(f"Failed to parse CSV: {e}")
            raise ValueError(f"Invalid CSV format: {str(e)}")
