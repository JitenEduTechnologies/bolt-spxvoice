import os

csv_path = "/app/api/services/campaign/sources/csv.py"
with open(csv_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update validate_source signature
old_vs = """    async def validate_source(
        self, source_id: str, organization_id: Optional[int] = None
    ) -> ValidationResult:"""

new_vs = """    async def validate_source(
        self,
        source_id: str,
        organization_id: Optional[int] = None,
        field_mapping: Optional[dict] = None,
    ) -> ValidationResult:"""

# 2. Update validate_source call
old_call = "return self.validate_source_data(headers, data_rows)"
new_call = "return self.validate_source_data(headers, data_rows, field_mapping=field_mapping)"

# 3. Update sync_source_data to use resolved column mapping
old_sync_headers = """        headers = self.normalize_headers(csv_data[0])
        rows = csv_data[1:]"""

new_sync_headers = """        headers = self.normalize_headers(csv_data[0])
        rows = csv_data[1:]

        meta = campaign.orchestrator_metadata or {}
        field_mapping = meta.get("field_mapping")
        resolved = self.resolve_column_mapping(headers, field_mapping)
        phone_col = resolved["phone_col"]
        name_col = resolved["name_col"]"""

old_phone_extract = """            raw_phone = context_vars.get("phone_number")
            if not raw_phone:
                logger.debug(f"Skipping row {idx}: no phone_number")
                continue

            norm_phone = normalize_phone_number(raw_phone)"""

new_phone_extract = """            raw_phone = context_vars.get(phone_col)
            if not raw_phone:
                logger.debug(f"Skipping row {idx}: no phone in column '{phone_col}'")
                continue

            norm_phone = normalize_phone_number(raw_phone)"""

old_update_ctx = """            # Update context_vars with normalized phone
            context_vars["phone_number"] = norm_phone"""

new_update_ctx = """            # Update context_vars with normalized phone and canonical customer_name
            context_vars["phone_number"] = norm_phone
            if name_col and context_vars.get(name_col):
                val_name = str(context_vars[name_col]).strip()
                if "customer_name" not in context_vars:
                    context_vars["customer_name"] = val_name
                if "name" not in context_vars:
                    context_vars["name"] = val_name"""

# Normalize CRLF/LF
content_norm = content.replace("\r\n", "\n")
assert old_vs in content_norm, "old_vs not found"
assert old_call in content_norm, "old_call not found"
assert old_sync_headers in content_norm, "old_sync_headers not found"
assert old_phone_extract in content_norm, "old_phone_extract not found"
assert old_update_ctx in content_norm, "old_update_ctx not found"

content_norm = content_norm.replace(old_vs, new_vs)
content_norm = content_norm.replace(old_call, new_call)
content_norm = content_norm.replace(old_sync_headers, new_sync_headers)
content_norm = content_norm.replace(old_phone_extract, new_phone_extract)
content_norm = content_norm.replace(old_update_ctx, new_update_ctx)

with open(csv_path, "w", encoding="utf-8") as f:
    f.write(content_norm)

print("csv.py successfully updated")

