from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger


@dataclass
class ValidationError(Exception):
    """Represents a validation error with details."""

    message: str
    invalid_rows: Optional[List[int]] = None

    def __str__(self) -> str:
        return self.message


@dataclass
class ValidationResult:
    """Result of source validation."""

    is_valid: bool
    error: Optional[ValidationError] = None
    headers: Optional[List[str]] = field(default=None, repr=False)
    rows: Optional[List[List[str]]] = field(default=None, repr=False)


class CampaignSourceSyncService(ABC):
    """Base class for campaign data source synchronization"""

    @staticmethod
    def normalize_headers(headers: List[str]) -> List[str]:
        """Normalize headers by stripping whitespace and lowercasing."""
        return [h.strip().lower() for h in headers]

    PHONE_ALIASES = {
        "phone_number",
        "phone",
        "mobile",
        "cell",
        "telephone",
        "customer_phone",
        "contact_phone",
        "contact_number",
        "mobile_number",
        "phone_no",
        "mobile_no",
    }

    NAME_ALIASES = {
        "name",
        "full_name",
        "customer_name",
        "contact_name",
        "client_name",
        "first_name",
    }

    @staticmethod
    def resolve_column_mapping(
        headers: List[str],
        field_mapping: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Resolves phone and metadata column mappings from headers and optional overrides."""
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)
        mapping = dict(field_mapping or {})

        # 1. Resolve phone column
        clean_headers = [h.replace(" ", "_").replace("-", "_") for h in normalized_headers]
        header_map = {clean: orig for clean, orig in zip(clean_headers, normalized_headers)}

        phone_col: Optional[str] = None
        if mapping.get("phone_number"):
            requested = mapping["phone_number"].strip().lower()
            if requested in normalized_headers:
                phone_col = requested
            elif requested.replace(" ", "_").replace("-", "_") in header_map:
                phone_col = header_map[requested.replace(" ", "_").replace("-", "_")]
            else:
                raise ValidationError(
                    message=f"Explicitly mapped phone column '{mapping['phone_number']}' not found in CSV headers: {headers}"
                )
        elif "phone_number" in normalized_headers:
            phone_col = "phone_number"
        elif "phone_number" in header_map:
            phone_col = header_map["phone_number"]
        else:
            matches = [header_map[c] for c in clean_headers if c in CampaignSourceSyncService.PHONE_ALIASES]
            seen = set()
            unique_matches = [m for m in matches if not (m in seen or seen.add(m))]
            if len(unique_matches) == 1:
                phone_col = unique_matches[0]
            elif len(unique_matches) > 1:
                raise ValidationError(
                    message=f"Ambiguous phone number columns detected: {unique_matches}. Please specify explicit field_mapping."
                )
            else:
                raise ValidationError(
                    message="Source must contain a 'phone_number' column (or recognized alias such as 'phone', 'mobile')."
                )

        # 2. Resolve name column
        name_col: Optional[str] = None
        if mapping.get("customer_name") or mapping.get("name"):
            req_name = (mapping.get("customer_name") or mapping.get("name", "")).strip().lower()
            if req_name in normalized_headers:
                name_col = req_name
            elif req_name.replace(" ", "_").replace("-", "_") in header_map:
                name_col = header_map[req_name.replace(" ", "_").replace("-", "_")]
        elif "customer_name" in normalized_headers:
            name_col = "customer_name"
        elif "customer_name" in header_map:
            name_col = header_map["customer_name"]
        elif "name" in normalized_headers:
            name_col = "name"
        elif "name" in header_map:
            name_col = header_map["name"]
        else:
            name_matches = [header_map[c] for c in clean_headers if c in CampaignSourceSyncService.NAME_ALIASES]
            if name_matches:
                name_col = name_matches[0]

        inferred: Dict[str, str] = {"phone_number": phone_col}
        if name_col:
            inferred["customer_name"] = name_col

        return {
            "phone_col": phone_col,
            "name_col": name_col,
            "inferred_mapping": inferred,
        }

    @staticmethod
    def validate_source_data(
        headers: List[str],
        rows: List[List[str]],
        field_mapping: Optional[Dict[str, str]] = None,
    ) -> ValidationResult:
        """
        Validate source data for campaign creation with flexible CRM field mapping.
        """
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        try:
            resolved = CampaignSourceSyncService.resolve_column_mapping(headers, field_mapping)
        except ValidationError as err:
            return ValidationResult(is_valid=False, error=err)

        phone_col = resolved["phone_col"]
        phone_number_idx = normalized_headers.index(phone_col)

        # Validate phone numbers in all data rows
        invalid_rows = []
        for row_idx, row in enumerate(
            rows, start=2
        ):  # Start at 2 (1-indexed, skip header)
            if len(row) <= phone_number_idx:
                continue  # Skip rows that don't have enough columns

            phone_number = row[phone_number_idx].strip()
            if phone_number and not phone_number.startswith("+"):
                invalid_rows.append(row_idx)

        if invalid_rows:
            # Limit the number of rows shown in error message
            if len(invalid_rows) > 5:
                rows_str = f"{', '.join(map(str, invalid_rows[:5]))} and {len(invalid_rows) - 5} more"
            else:
                rows_str = ", ".join(map(str, invalid_rows))

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Invalid phone numbers in rows: {rows_str}. All phone numbers must include country code (start with '+')",
                    invalid_rows=invalid_rows,
                ),
            )

        # Check for duplicate phone numbers
        seen_phones: dict[str, int] = {}  # phone -> first row where it appeared
        duplicate_rows = []
        for row_idx, row in enumerate(rows, start=2):
            if len(row) <= phone_number_idx:
                continue

            phone_number = row[phone_number_idx].strip()
            if not phone_number:
                continue

            if phone_number in seen_phones:
                duplicate_rows.append(row_idx)
            else:
                seen_phones[phone_number] = row_idx

        if duplicate_rows:
            if len(duplicate_rows) > 5:
                rows_str = f"{', '.join(map(str, duplicate_rows[:5]))} and {len(duplicate_rows) - 5} more"
            else:
                rows_str = ", ".join(map(str, duplicate_rows))

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Duplicate phone numbers found in rows: {rows_str}. Phone numbers in a campaign must be unique.",
                    invalid_rows=duplicate_rows,
                ),
            )

        return ValidationResult(is_valid=True, headers=normalized_headers, rows=rows)

    @staticmethod
    def validate_template_columns(
        headers: List[str],
        rows: List[List[str]],
        required_columns: Set[str],
    ) -> ValidationResult:
        """Validate that template variable columns exist and are non-empty in all rows."""
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        # Check for missing columns
        missing = required_columns - set(normalized_headers)
        if missing:
            missing_str = ", ".join(f"'{c}'" for c in sorted(missing))
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Workflow uses template variables that are missing from the source data: {missing_str}. "
                    "Add the missing columns or remove them from the workflow."
                ),
            )

        # Check for empty values in required columns
        col_indices = {col: normalized_headers.index(col) for col in required_columns}

        for col, idx in col_indices.items():
            empty_rows = []
            for row_idx, row in enumerate(rows, start=2):
                if len(row) <= idx or not row[idx].strip():
                    empty_rows.append(row_idx)

            if empty_rows:
                if len(empty_rows) > 5:
                    rows_str = f"{', '.join(map(str, empty_rows[:5]))} and {len(empty_rows) - 5} more"
                else:
                    rows_str = ", ".join(map(str, empty_rows))

                return ValidationResult(
                    is_valid=False,
                    error=ValidationError(
                        message=f"Template variable '{col}' is empty in rows: {rows_str}. "
                        "All template variables used in the workflow must have values in every row.",
                        invalid_rows=empty_rows,
                    ),
                )

        return ValidationResult(is_valid=True)

    @abstractmethod
    async def validate_source(
        self, source_id: str, organization_id: Optional[int] = None
    ) -> ValidationResult:
        """Validate source data before campaign creation."""
        pass

    @abstractmethod
    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from source and creates queued_runs
        Each record gets a unique source_uuid based on source type
        Returns: number of records synced
        """
        pass

    async def get_source_credentials(
        self, organization_id: int, source_type: str
    ) -> Dict[str, Any]:
        """Gets source credentials when a sync service requires them."""
        logger.info(
            f"Getting credentials for org {organization_id}, source {source_type}"
        )
        return {}
