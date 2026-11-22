"""API routes for workflow recording operations."""

from typing import Annotated, Optional

import hashlib
import hmac
import pathlib
import time
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from api.constants import OSS_JWT_SECRET
from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.workflow_recording_client import generate_short_id
from api.enums import StorageBackend
from api.schemas.workflow_recording import (
    BatchRecordingCreateRequestSchema,
    BatchRecordingCreateResponseSchema,
    BatchRecordingUploadRequestSchema,
    BatchRecordingUploadResponseSchema,
    RecordingListResponseSchema,
    RecordingResponseSchema,
    RecordingUpdateRequestSchema,
    RecordingUploadResponseSchema,
)
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.dograh_cloud import require_dograh_cloud
from api.services.mps_service_key_client import mps_service_key_client
from api.services.storage import storage_fs

router = APIRouter(prefix="/workflow-recordings", tags=["workflow-recordings"])

def create_playback_token(recording_id: str, organization_id: int, expires_in: int = 300) -> str:
    """Generate a short-lived HMAC token for browser audio playback."""
    secret = (OSS_JWT_SECRET or "spx-voice-playback-secret").encode("utf-8")
    expiry = int(time.time()) + expires_in
    payload = f"{recording_id}:{organization_id}:{expiry}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_playback_token(token: str, recording_id: str) -> Optional[int]:
    """Verify playback token signature and expiration. Returns organization_id if valid."""
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    if len(parts) != 4:
        return None
    tok_rid, tok_org_id, tok_expiry, tok_sig = parts
    if tok_rid != recording_id:
        return None
    try:
        expiry = int(tok_expiry)
        if time.time() > expiry:
            return None
        org_id = int(tok_org_id)
    except ValueError:
        return None

    secret = (OSS_JWT_SECRET or "spx-voice-playback-secret").encode("utf-8")
    payload = f"{tok_rid}:{tok_org_id}:{tok_expiry}"
    expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(tok_sig, expected_sig):
        return None
    return org_id


async def _generate_unique_recording_id(organization_id: int) -> str:
    """Generate a unique short recording ID within an organization."""
    for _ in range(10):
        rid = generate_short_id(8)
        exists = await db_client.check_recording_id_exists(rid, organization_id)
        if not exists:
            return rid
    raise HTTPException(
        status_code=500, detail="Failed to generate unique recording ID"
    )


def _build_response(rec) -> RecordingResponseSchema:
    return RecordingResponseSchema(
        id=rec.id,
        recording_id=rec.recording_id,
        workflow_id=rec.workflow_id,
        organization_id=rec.organization_id,
        tts_provider=rec.tts_provider,
        tts_model=rec.tts_model,
        tts_voice_id=rec.tts_voice_id,
        transcript=rec.transcript,
        storage_key=rec.storage_key,
        storage_backend=rec.storage_backend,
        metadata=rec.recording_metadata or {},
        created_by=rec.created_by,
        created_at=rec.created_at,
        is_active=rec.is_active,
    )


@router.post(
    "/upload-url",
    response_model=BatchRecordingUploadResponseSchema,
    summary="Get presigned URLs for recording uploads",
)
async def get_upload_urls(
    request: BatchRecordingUploadRequestSchema,
    user=Depends(get_user),
):
    """Generate presigned PUT URLs for uploading one or more audio recordings."""
    try:
        ALLOWED_AUDIO_EXTENSIONS = {
            ".wav", ".wave", ".mp3", ".mpeg", ".m4a", ".mp4", ".webm", ".ogg"
        }
        ALLOWED_AUDIO_MIMES = {
            "audio/wav", "audio/x-wav", "audio/wave",
            "audio/mpeg", "audio/mp3",
            "audio/x-m4a", "audio/m4a", "audio/mp4", "video/mp4",
            "audio/webm", "video/webm", "audio/ogg", "application/ogg"
        }
        items = []
        for fd in request.files:
            raw_mime = (fd.mime_type or "").lower().strip()
            base_mime = raw_mime.split(";")[0].strip() if raw_mime else ""
            ext = pathlib.Path(fd.filename).suffix.lower()

            if not base_mime or base_mime in ("application/octet-stream", "application/binary"):
                if ext == ".webm":
                    base_mime = "audio/webm"
                elif ext in (".wav", ".wave"):
                    base_mime = "audio/wav"
                elif ext in (".mp3", ".mpeg"):
                    base_mime = "audio/mpeg"
                elif ext == ".m4a":
                    base_mime = "audio/m4a"
                elif ext == ".mp4":
                    base_mime = "audio/mp4"
                elif ext == ".ogg":
                    base_mime = "audio/ogg"

            if base_mime not in ALLOWED_AUDIO_MIMES and ext not in ALLOWED_AUDIO_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported audio type '{fd.mime_type}'. Allowed types: wav, mp3, m4a, mp4, webm, ogg",
                )
            if fd.file_size > 5_242_880:
                raise HTTPException(
                    status_code=400,
                    detail=f"File '{fd.filename}' exceeds maximum allowed size of 5MB",
                )

            clean_filename = pathlib.Path(fd.filename).name.replace("..", "").strip()
            if not clean_filename:
                clean_filename = "recording.wav"

            recording_id = await _generate_unique_recording_id(
                user.selected_organization_id
            )

            storage_key = (
                f"recordings/{user.selected_organization_id}"
                f"/{recording_id}"
                f"/{clean_filename}"
            )

            effective_mime = base_mime or "audio/wav"
            upload_url = await storage_fs.aget_presigned_put_url(
                file_path=storage_key,
                expiration=1800,
                content_type=effective_mime,
                max_size=5_242_880,
            )

            if not upload_url:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to generate presigned upload URL for {fd.filename}",
                )

            items.append(
                RecordingUploadResponseSchema(
                    upload_url=upload_url,
                    recording_id=recording_id,
                    storage_key=storage_key,
                )
            )

        logger.info(
            f"Generated {len(items)} recording upload URL(s), "
            f"org {user.selected_organization_id}"
        )

        return BatchRecordingUploadResponseSchema(items=items)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error generating recording upload URLs: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to generate upload URLs"
        ) from exc


@router.post(
    "",
    response_model=BatchRecordingCreateResponseSchema,
    include_in_schema=False,
)
@router.post(
    "/",
    response_model=BatchRecordingCreateResponseSchema,
    summary="Create recording records after upload",
)
async def create_recordings(
    request: BatchRecordingCreateRequestSchema,
    user=Depends(get_user),
):
    """Create one or more recording records after audio files have been uploaded."""
    try:
        backend = StorageBackend.get_current_backend()
        results = []

        for rec_req in request.recordings:
            recording = await db_client.create_recording(
                recording_id=rec_req.recording_id,
                organization_id=user.selected_organization_id,
                transcript=rec_req.transcript,
                storage_key=rec_req.storage_key,
                storage_backend=backend.value,
                created_by=user.id,
                tts_provider=rec_req.tts_provider,
                tts_model=rec_req.tts_model,
                tts_voice_id=rec_req.tts_voice_id,
                metadata=rec_req.metadata,
            )
            results.append(_build_response(recording))

        logger.info(
            f"Created {len(results)} recording(s) for org {user.selected_organization_id}"
        )

        return BatchRecordingCreateResponseSchema(recordings=results)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error creating recordings: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to create recordings"
        ) from exc


@router.get(
    "",
    response_model=RecordingListResponseSchema,
    include_in_schema=False,
)
@router.get(
    "/",
    response_model=RecordingListResponseSchema,
    summary="List recordings",
    **sdk_expose(
        method="list_recordings",
        description="List workflow recordings available to the authenticated organization.",
    ),
)
async def list_recordings(
    workflow_id: Annotated[
        Optional[int], Query(description="Filter by workflow ID")
    ] = None,
    tts_provider: Annotated[
        Optional[str], Query(description="Filter by TTS provider")
    ] = None,
    tts_model: Annotated[
        Optional[str], Query(description="Filter by TTS model")
    ] = None,
    tts_voice_id: Annotated[
        Optional[str], Query(description="Filter by TTS voice ID")
    ] = None,
    user=Depends(get_user),
):
    """List recordings for the organization, optionally filtered."""
    try:
        recordings = await db_client.get_recordings(
            organization_id=user.selected_organization_id,
            workflow_id=workflow_id,
            tts_provider=tts_provider,
            tts_model=tts_model,
            tts_voice_id=tts_voice_id,
        )

        return RecordingListResponseSchema(
            recordings=[_build_response(r) for r in recordings],
            total=len(recordings),
        )

    except Exception as exc:
        logger.error(f"Error listing recordings: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to list recordings"
        ) from exc


@router.delete(
    "/{recording_id}",
    summary="Delete a recording",
)
async def delete_recording(
    recording_id: str,
    user=Depends(get_user),
):
    """Soft delete a recording."""
    try:
        success = await db_client.delete_recording(
            recording_id=recording_id,
            organization_id=user.selected_organization_id,
        )

        if not success:
            raise HTTPException(status_code=404, detail="Recording not found")

        logger.info(
            f"Deleted recording {recording_id}, org {user.selected_organization_id}"
        )

        return {"success": True, "message": "Recording deleted successfully"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error deleting recording: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to delete recording"
        ) from exc


@router.patch(
    "/{id}",
    response_model=RecordingResponseSchema,
    summary="Update a recording's Recording ID",
)
async def update_recording(
    id: int,
    request: RecordingUpdateRequestSchema,
    user=Depends(get_user),
):
    """Update the recording_id (descriptive name) of a recording."""
    try:
        new_id = request.recording_id.strip()
        if not new_id:
            raise HTTPException(status_code=400, detail="Recording ID cannot be empty")

        existing = await db_client.get_recording_by_id(
            id, user.selected_organization_id
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Recording not found")

        if new_id == existing.recording_id:
            return _build_response(existing)

        exists = await db_client.check_recording_id_exists(
            new_id, user.selected_organization_id
        )
        if exists:
            raise HTTPException(
                status_code=409,
                detail=f"Recording ID '{new_id}' is already in use",
            )

        old_id = existing.recording_id

        recording = await db_client.update_recording_id(
            id=id,
            new_recording_id=new_id,
            organization_id=user.selected_organization_id,
        )

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

        # Replace old recording ID in all non-legacy workflow definitions
        updated = await db_client.replace_recording_id_in_workflows(
            old_id=old_id,
            new_id=new_id,
            organization_id=user.selected_organization_id,
        )
        if updated:
            logger.info(
                f"Updated {updated} workflow definition(s) with new recording ID "
                f"'{old_id}' -> '{new_id}'"
            )

        return _build_response(recording)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error updating recording: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to update recording"
        ) from exc


@router.post(
    "/transcribe",
    summary="Transcribe an audio file",
)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("en"),
    user=Depends(get_user),
):
    """Transcribe an uploaded audio file using MPS STT."""
    require_dograh_cloud("Dograh audio transcription")

    try:
        audio_data = await file.read()

        if DEPLOYMENT_MODE == "oss":
            result = await mps_service_key_client.transcribe_audio(
                audio_data=audio_data,
                filename=file.filename or "audio.wav",
                content_type=file.content_type or "audio/wav",
                language=language,
                created_by=str(user.provider_id),
            )
        else:
            result = await mps_service_key_client.transcribe_audio(
                audio_data=audio_data,
                filename=file.filename or "audio.wav",
                content_type=file.content_type or "audio/wav",
                language=language,
                organization_id=user.selected_organization_id,
            )

        return result

    except Exception as exc:
        logger.error(f"Error transcribing audio: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to transcribe audio"
        ) from exc


@router.get(
    "/{recording_id}/playback",
    summary="Authenticated audio playback with Range request seeking",
)
async def playback_audio(
    recording_id: str,
    request: Request,
    token: Optional[str] = Query(None, description="Short-lived playback token"),
    authorization: Optional[str] = Header(None),
):
    """Stream recording audio with HTTP 206 Partial Content (Range) support.
    
    Can authenticate via short-lived signed token (query param for HTML5 audio tags)
    or user session.
    """
    authorized_org_id = None
    if token:
        authorized_org_id = verify_playback_token(token, recording_id)
        if not authorized_org_id:
            raise HTTPException(status_code=403, detail="Invalid or expired playback token")

    if not authorized_org_id and authorization:
        try:
            user = await get_user(authorization=authorization)
            if user and user.selected_organization_id:
                authorized_org_id = user.selected_organization_id
        except Exception:
            pass

    if not authorized_org_id:
        raise HTTPException(status_code=401, detail="Authentication required for playback")

    recording = await db_client.get_recording_by_recording_id(
        recording_id, organization_id=authorized_org_id
    )
    if not recording or not recording.is_active:
        raise HTTPException(status_code=404, detail="Recording not found or deleted")

    # Read audio from storage
    try:
        from io import BytesIO
        buf = BytesIO()
        # Download from storage
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(delete=True) as tmp:
            success = await storage_fs.adownload_file(recording.storage_key, tmp.name)
            if not success:
                raise HTTPException(status_code=404, detail="Audio file not found in storage")
            with open(tmp.name, "rb") as f:
                content = f.read()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error fetching audio for playback: {exc}")
        raise HTTPException(status_code=500, detail="Failed to retrieve audio stream") from exc

    total_len = len(content)
    ext = recording.storage_key.rsplit(".", 1)[-1].lower() if "." in recording.storage_key else "wav"
    mime_map = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4", "webm": "audio/webm", "ogg": "audio/ogg"}
    media_type = mime_map.get(ext, "audio/wav")

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        # Range request (e.g. bytes=0-1024 or bytes=1024- or bytes=-500)
        range_val = range_header[6:].strip()
        parts = range_val.split("-")
        if not parts[0] and len(parts) > 1 and parts[1]:
            # Suffix range: bytes=-500 means last 500 bytes
            suffix_len = int(parts[1])
            start = max(0, total_len - suffix_len)
            end = total_len - 1
        else:
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else total_len - 1
        start = max(0, min(start, total_len - 1))
        end = max(start, min(end, total_len - 1))
        chunk_len = end - start + 1

        headers = {
            "Content-Range": f"bytes {start}-{end}/{total_len}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_len),
        }
        return Response(content[start:end+1], status_code=206, headers=headers, media_type=media_type)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(total_len),
    }
    return Response(content, status_code=200, headers=headers, media_type=media_type)
