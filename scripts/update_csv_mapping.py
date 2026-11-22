import os

# 1. Update source_sync.py
source_sync_path = "api/services/campaign/source_sync.py"
with open(source_sync_path, "r", encoding="utf-8") as f:
    ss_content = f.read()

target_str = """    @staticmethod
    def validate_source_data(
        headers: List[str], rows: List[List[str]]
    ) -> ValidationResult:
        \"\"\"
        Validate source data for campaign creation.

        Args:
            headers: List of column headers
            rows: List of data rows (excluding header)

        Returns:
            ValidationResult with is_valid=True if valid, or error details if invalid
        \"\"\"
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        # Check for phone_number column
        if "phone_number" not in normalized_headers:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="Source must contain a 'phone_number' column"
                ),
            )

        phone_number_idx = normalized_headers.index("phone_number")"""

replacement_str = """    PHONE_ALIASES = {
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
        \"\"\"Resolves phone and metadata column mappings from headers and optional overrides.\"\"\"
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)
        mapping = dict(field_mapping or {})

        # 1. Resolve phone column
        phone_col: Optional[str] = None
        if mapping.get("phone_number"):
            requested = mapping["phone_number"].strip().lower()
            if requested in normalized_headers:
                phone_col = requested
            else:
                raise ValidationError(
                    message=f"Explicitly mapped phone column '{mapping['phone_number']}' not found in CSV headers: {headers}"
                )
        elif "phone_number" in normalized_headers:
            phone_col = "phone_number"
        else:
            matches = [h for h in normalized_headers if h in CampaignSourceSyncService.PHONE_ALIASES]
            if len(matches) == 1:
                phone_col = matches[0]
            elif len(matches) > 1:
                raise ValidationError(
                    message=f"Ambiguous phone number columns detected: {matches}. Please specify explicit field_mapping."
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
        elif "customer_name" in normalized_headers:
            name_col = "customer_name"
        elif "name" in normalized_headers:
            name_col = "name"
        else:
            name_matches = [h for h in normalized_headers if h in CampaignSourceSyncService.NAME_ALIASES]
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
        \"\"\"
        Validate source data for campaign creation with flexible CRM field mapping.
        \"\"\"
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        try:
            resolved = CampaignSourceSyncService.resolve_column_mapping(headers, field_mapping)
        except ValidationError as err:
            return ValidationResult(is_valid=False, error=err)

        phone_col = resolved["phone_col"]
        phone_number_idx = normalized_headers.index(phone_col)"""

# Normalize CRLF/LF
ss_content_norm = ss_content.replace("\r\n", "\n")
target_str_norm = target_str.replace("\r\n", "\n")
replacement_str_norm = replacement_str.replace("\r\n", "\n")

if target_str_norm in ss_content_norm:
    ss_content_norm = ss_content_norm.replace(target_str_norm, replacement_str_norm)
    with open(source_sync_path, "w", encoding="utf-8") as f:
        f.write(ss_content_norm)
    print("source_sync.py successfully updated")
else:
    print("target_str_norm not found in source_sync.py")

