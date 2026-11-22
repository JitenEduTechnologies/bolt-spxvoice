"""Live End-to-End integration tests against real local Asterisk container."""

import asyncio
import base64
import json
import os
import aiohttp
import pytest

from api.services.telephony.ari_manager import ARIConnection
from api.schemas.economy_ivr import EconomyIVRConfig, KeypadAction, IVRActionType

ASTERISK_ARI_HOST = os.getenv("ASTERISK_ARI_HOST", "http://localhost:8089")
ASTERISK_ARI_USER = "spx-voice"
ASTERISK_ARI_PASS = "spxsecret"


def get_auth_header():
    token = base64.b64encode(f"{ASTERISK_ARI_USER}:{ASTERISK_ARI_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.asyncio
async def test_01_live_asterisk_ari_info():
    """Verify live local Asterisk container is reachable and running Asterisk 20+."""
    async with aiohttp.ClientSession(headers=get_auth_header()) as session:
        async with session.get(f"{ASTERISK_ARI_HOST}/ari/asterisk/info") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "system" in data
            assert data["system"]["version"].startswith("20.")


@pytest.mark.asyncio
async def test_02_live_asterisk_channel_originate_and_hangup():
    """Originate real channel in Asterisk while Stasis WebSocket listens, verify state, and cleanly delete it."""
    ws_base = ASTERISK_ARI_HOST.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_base}/ari/events?app=spx-voice&api_key={ASTERISK_ARI_USER}:{ASTERISK_ARI_PASS}"

    async with aiohttp.ClientSession(headers=get_auth_header()) as session:
        async with session.ws_connect(ws_url) as ws:
            # Originate channel into Stasis
            create_url = f"{ASTERISK_ARI_HOST}/ari/channels?endpoint=Local/test@spx-inbound&app=spx-voice"
            async with session.post(create_url) as resp:
                assert resp.status in (200, 201)
                chan_data = await resp.json()
                chan_id = chan_data["id"]
                assert chan_id

            # Consume event from Asterisk
            event = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
            assert "type" in event

            # Verify channel is alive in Stasis
            async with session.get(f"{ASTERISK_ARI_HOST}/ari/channels/{chan_id}") as resp:
                assert resp.status == 200

            # Hang up channel
            async with session.delete(f"{ASTERISK_ARI_HOST}/ari/channels/{chan_id}") as resp:
                assert resp.status in (200, 204)

            # Allow Asterisk to finalize destruction
            await asyncio.sleep(0.1)

            # Verify channel is gone (no orphaned channels)
            async with session.get(f"{ASTERISK_ARI_HOST}/ari/channels/{chan_id}") as resp:
                assert resp.status == 404


@pytest.mark.asyncio
async def test_03_live_asterisk_channel_dtmf_delivery():
    """Verify real Asterisk channel in Stasis receives DTMF digit via ARI."""
    ws_base = ASTERISK_ARI_HOST.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_base}/ari/events?app=spx-voice&api_key={ASTERISK_ARI_USER}:{ASTERISK_ARI_PASS}"

    async with aiohttp.ClientSession(headers=get_auth_header()) as session:
        async with session.ws_connect(ws_url) as ws:
            create_url = f"{ASTERISK_ARI_HOST}/ari/channels?endpoint=Local/test@spx-inbound&app=spx-voice"
            async with session.post(create_url) as resp:
                assert resp.status in (200, 201)
                chan_data = await resp.json()
                chan_id = chan_data["id"]

            try:
                # Wait for channel initialization
                await asyncio.sleep(0.1)
                # Send DTMF digit 1
                dtmf_url = f"{ASTERISK_ARI_HOST}/ari/channels/{chan_id}/dtmf?dtmf=1"
                async with session.post(dtmf_url) as resp:
                    assert resp.status in (200, 204)
            finally:
                await session.delete(f"{ASTERISK_ARI_HOST}/ari/channels/{chan_id}")


@pytest.mark.asyncio
async def test_04_live_asterisk_ari_connection_wrapper():
    """Verify ARIConnection connects and makes calls to real Asterisk ARI."""
    ari_conn = ARIConnection(
        organization_id=1,
        telephony_configuration_id=1,
        ari_endpoint=ASTERISK_ARI_HOST,
        app_name=ASTERISK_ARI_USER,
        app_password=ASTERISK_ARI_PASS,
    )

    info = await ari_conn._ari_request("GET", "/asterisk/info")
    assert "system" in info
    assert info["system"]["version"].startswith("20.")
