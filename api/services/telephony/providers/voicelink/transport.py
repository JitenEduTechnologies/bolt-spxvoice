"""VoiceLink transport factory.

VoiceLink is an enterprise SIP trunking provider. Primary media streaming
is handled via LiveKit SIP gateway and WebRTC audio tracks.
"""

from typing import Any, Dict, Optional

from fastapi import WebSocket
from loguru import logger

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.factory import load_credentials_for_transport


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: Optional[dict] = None,
    telephony_configuration_id: Optional[int] = None,
    is_realtime: bool = False,
    **kwargs: Any,
) -> Any:
    """Create transport for VoiceLink.

    VoiceLink media terminates primarily on LiveKit SIP trunks. Direct WebSocket
    transport is supported via standard G.711 / PCM media bridging if configured.
    """
    logger.info(
        f"[run {workflow_run_id}] Initializing VoiceLink transport for org {organization_id}"
    )
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="voicelink"
    )

    sip_host = config.get("sip_host")
    if not sip_host:
        raise ValueError(
            f"Incomplete VoiceLink configuration for organization {organization_id}: missing sip_host"
        )

    # In LiveKit runtime, media is carried by WebRTC tracks inside the room.
    # For legacy/direct websocket fallback, an informative NotImplementedError is raised.
    raise NotImplementedError(
        "VoiceLink media terminates directly through LiveKit SIP Trunks. "
        "Direct WebSocket media transport is not utilized for VoiceLink."
    )

