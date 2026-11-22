"""Secure audio retrieval and caching abstraction for Asterisk Economy IVR.

Never mounts MinIO's raw storage directory directly.
Validates tenant ownership, enforces path isolation, verifies Asterisk format compatibility,
and manages cache lifecycle and cleanup.
"""

from __future__ import annotations

import os
import pathlib
import re
import time
from typing import Optional
from loguru import logger

from api.db import db_client
from api.services.storage import storage_fs


DEFAULT_CACHE_DIR = os.getenv("IVR_AUDIO_CACHE_DIR", "/tmp/spx_ivr_audio_cache")

# Formats supported by Asterisk format modules (res_format_attr_*, format_pcm, format_wav, format_gsm)
ALLOWED_ASTERISK_FORMATS = {"wav", "gsm", "sln", "sln16", "ulaw", "alaw", "g722", "mp3", "ogg"}


class EconomyIVRAudioCache:
    """Manages cached audio files for Asterisk playback with tenant isolation."""

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR):
        self.cache_dir = pathlib.Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_tenant_dir(self, organization_id: int) -> pathlib.Path:
        tenant_dir = (self.cache_dir / str(organization_id)).resolve()
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir

    def _validate_safe_id(self, recording_id: str) -> str:
        """Ensure recording_id contains strictly alphanumeric, underscore, or dash characters."""
        if not recording_id or not isinstance(recording_id, str):
            raise ValueError("Recording ID must be a non-empty string")
        if not re.match(r"^[a-zA-Z0-9_\-]+$", recording_id):
            logger.error(f"[IVR Audio Cache] Illegal recording_id characters detected: {recording_id}")
            raise ValueError(f"Invalid recording ID format: '{recording_id}'")
        return recording_id

    async def get_or_cache_recording(
        self,
        recording_id: str,
        organization_id: int,
    ) -> Optional[str]:
        """Retrieve and cache an owner recording, enforcing tenant isolation.

        Args:
            recording_id: Unique recording ID (e.g. 'rec_abc123')
            organization_id: Requesting organization ID

        Returns:
            Local file path suitable for Asterisk playback (or None if not found/unauthorized).

        Raises:
            PermissionError: If the recording belongs to a different organization.
            ValueError: If recording_id contains illegal path traversal characters.
        """
        if not recording_id or not organization_id:
            logger.warning("[IVR Audio Cache] Missing recording_id or organization_id")
            return None

        # 1. Path safety verification
        safe_id = self._validate_safe_id(recording_id)

        # 2. Authorize via DB: Verify recording exists AND belongs to this organization
        rec = await db_client.get_recording_by_recording_id(
            recording_id=safe_id,
            organization_id=organization_id,
        )
        if not rec:
            logger.warning(
                f"[IVR Audio Cache] Recording '{safe_id}' not found or access denied "
                f"for organization {organization_id}"
            )
            return None

        if rec.organization_id != organization_id:
            logger.error(
                f"[IVR Audio Cache] Cross-tenant violation: Recording '{safe_id}' "
                f"belongs to org {rec.organization_id}, requested by {organization_id}"
            )
            raise PermissionError(f"Cross-organization audio access denied for recording {safe_id}")

        # 3. Check local cache with verified tenant path
        tenant_dir = self._get_tenant_dir(organization_id)
        raw_ext = rec.storage_key.rsplit(".", 1)[-1].lower() if "." in rec.storage_key else "wav"
        ext = raw_ext if raw_ext in ALLOWED_ASTERISK_FORMATS else "wav"

        cached_file = (tenant_dir / f"{safe_id}.{ext}").resolve()

        # Path traversal guard
        if not str(cached_file).startswith(str(tenant_dir)):
            logger.error(f"[IVR Audio Cache] Path traversal detected: {cached_file}")
            raise PermissionError("Path traversal violation in recording cache")

        if cached_file.exists() and cached_file.stat().st_size > 0:
            logger.debug(f"[IVR Audio Cache] Cache HIT for {safe_id} (org {organization_id})")
            return str(cached_file)

        # 4. Cache MISS: Download securely via storage abstraction (never raw data-volume mounts)
        logger.info(
            f"[IVR Audio Cache] Cache MISS for {safe_id}. Downloading from storage key '{rec.storage_key}'"
        )
        temp_file = (tenant_dir / f"{safe_id}.tmp_{os.getpid()}_{int(time.time()*1000)}").resolve()
        try:
            success = await storage_fs.adownload_file(rec.storage_key, str(temp_file))
            if not success or not temp_file.exists() or temp_file.stat().st_size == 0:
                logger.error(f"[IVR Audio Cache] Failed to download storage key '{rec.storage_key}'")
                if temp_file.exists():
                    temp_file.unlink(missing_ok=True)
                return None

            # Ensure file mode is readable by Asterisk process
            try:
                temp_file.chmod(0o644)
            except Exception:
                pass

            # Atomic rename into final cache location
            temp_file.replace(cached_file)
            logger.info(f"[IVR Audio Cache] Successfully cached {safe_id} at {cached_file}")
            return str(cached_file)

        except Exception as e:
            logger.error(f"[IVR Audio Cache] Error downloading recording {safe_id}: {e}")
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            return None

    def remove_cached_recording(self, recording_id: str, organization_id: int) -> bool:
        """Evict a specific recording from cache when updated or deleted."""
        try:
            safe_id = self._validate_safe_id(recording_id)
            tenant_dir = self._get_tenant_dir(organization_id)
            removed = False
            for p in tenant_dir.glob(f"{safe_id}.*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
                    removed = True
            return removed
        except Exception as e:
            logger.warning(f"[IVR Audio Cache] Failed to evict {recording_id}: {e}")
            return False

    def cleanup_temp_files(self) -> int:
        """Clean up orphaned temporary download files."""
        cleaned = 0
        try:
            for p in self.cache_dir.rglob("*.tmp_*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
                    cleaned += 1
        except Exception as e:
            logger.warning(f"[IVR Audio Cache] Error cleaning temp files: {e}")
        return cleaned

    def cleanup_old_cache(self, max_age_seconds: int = 86400) -> int:
        """Evict cache files older than max_age_seconds (default 24h)."""
        now = time.time()
        evicted = 0
        try:
            for p in self.cache_dir.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    mtime = p.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        p.unlink(missing_ok=True)
                        evicted += 1
        except Exception as e:
            logger.warning(f"[IVR Audio Cache] Error during old cache eviction: {e}")
        return evicted


# Global audio cache singleton
ivr_audio_cache = EconomyIVRAudioCache()
