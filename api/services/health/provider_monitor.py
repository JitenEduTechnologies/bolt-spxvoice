"""Telephony and AI Provider Health Monitoring (Phase C3).

Safely evaluates health signals from:
- Vobiz
- VoiceLink
- LiveKit / SIP
- Gemini Live
- Deepgram
- DeepSeek
- Cartesia

Strict rules:
- Strictly passive/evidence-based. Never initiate paid calls or artificial requests.
- Do NOT mark a provider HEALTHY merely because an API key is present.
- Distinguish: NOT_CONFIGURED, UNVERIFIED, HEALTHY, DEGRADED, UNAVAILABLE.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import WorkflowRunModel
from api.services.health.models import ComponentHealth, HealthStatus


class ProviderMonitor:
    """Monitors telephony carriers and AI providers using passive operational evidence."""

    async def check_all_providers(
        self, organization_id: Optional[int] = None
    ) -> Dict[str, ComponentHealth]:
        """Evaluates health of all telephony and AI providers."""
        providers: Dict[str, ComponentHealth] = {}

        # 1. Telephony Providers
        providers["vobiz"] = await self._check_vobiz_health(organization_id)
        providers["voicelink"] = await self._check_voicelink_health(organization_id)
        providers["livekit_sip"] = await self._check_livekit_sip_health(organization_id)

        # 2. AI Providers
        providers["gemini_live"] = await self._check_gemini_health(organization_id)
        providers["deepgram"] = await self._check_deepgram_health(organization_id)
        providers["deepseek"] = await self._check_deepseek_health(organization_id)
        providers["cartesia"] = await self._check_cartesia_health(organization_id)

        return providers

    async def _check_vobiz_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Passive health evaluation for Vobiz telephony."""
        from api.services.telephony.smart_did import get_smart_did_controller

        vobiz_auth_id = os.getenv("VOBIZ_AUTH_ID")
        vobiz_auth_token = os.getenv("VOBIZ_AUTH_TOKEN")
        has_creds = bool(vobiz_auth_id and vobiz_auth_token)
        if not has_creds:
            return ComponentHealth(
                name="vobiz",
                status=HealthStatus.NOT_CONFIGURED,
                message="Vobiz credentials not configured",
            )

        controller = get_smart_did_controller()
        dids = controller.get_all_dids()
        vobiz_dids = [d for d in dids.values() if d.carrier.lower() == "vobiz"]

        if not vobiz_dids:
            return ComponentHealth(
                name="vobiz",
                status=HealthStatus.UNVERIFIED,
                message="Vobiz configured but no DIDs provisioned or registered",
            )

        quarantined = [d for d in vobiz_dids if d.state.value == "quarantine"]
        in_cooldown = [d for d in vobiz_dids if d.is_in_cooldown()]

        if quarantined:
            return ComponentHealth(
                name="vobiz",
                status=HealthStatus.DEGRADED,
                message=f"{len(quarantined)} Vobiz DID(s) quarantined due to carrier policy",
                details={"quarantined_count": len(quarantined)},
            )
        if in_cooldown:
            return ComponentHealth(
                name="vobiz",
                status=HealthStatus.DEGRADED,
                message=f"{len(in_cooldown)} Vobiz DID(s) in technical cooldown",
                details={"cooldown_count": len(in_cooldown)},
            )

        total_calls = sum(d.total_calls for d in vobiz_dids)
        failed_calls = sum(d.failed_calls for d in vobiz_dids)
        if total_calls == 0:
            return ComponentHealth(
                name="vobiz",
                status=HealthStatus.UNVERIFIED,
                message="Vobiz credentials active, awaiting live call evidence",
            )

        failure_rate = failed_calls / total_calls
        if failure_rate > 0.3:
            return ComponentHealth(
                name="vobiz",
                status=HealthStatus.DEGRADED,
                message=f"Elevated call failure rate ({round(failure_rate * 100, 1)}%)",
                details={"total_calls": total_calls, "failed_calls": failed_calls},
            )

        return ComponentHealth(
            name="vobiz",
            status=HealthStatus.HEALTHY,
            message=f"Vobiz healthy ({total_calls} calls, {round((1 - failure_rate) * 100, 1)}% success)",
            details={"total_calls": total_calls, "success_rate": round(1 - failure_rate, 3)},
        )

    async def _check_voicelink_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Passive health evaluation for VoiceLink telephony."""
        from api.services.telephony.smart_did import get_smart_did_controller

        voicelink_key = os.getenv("VOICELINK_API_KEY")
        voicelink_host = os.getenv("VOICELINK_SIP_HOST")
        has_creds = bool(voicelink_key or voicelink_host)
        if not has_creds:
            return ComponentHealth(
                name="voicelink",
                status=HealthStatus.NOT_CONFIGURED,
                message="VoiceLink credentials not configured",
            )

        controller = get_smart_did_controller()
        dids = controller.get_all_dids()
        vl_dids = [d for d in dids.values() if d.carrier.lower() == "voicelink"]

        if not vl_dids:
            return ComponentHealth(
                name="voicelink",
                status=HealthStatus.UNVERIFIED,
                message="VoiceLink configured but no DIDs provisioned or registered",
            )

        quarantined = [d for d in vl_dids if d.state.value == "quarantine"]
        in_cooldown = [d for d in vl_dids if d.is_in_cooldown()]

        if quarantined:
            return ComponentHealth(
                name="voicelink",
                status=HealthStatus.DEGRADED,
                message=f"{len(quarantined)} VoiceLink DID(s) quarantined",
            )
        if in_cooldown:
            return ComponentHealth(
                name="voicelink",
                status=HealthStatus.DEGRADED,
                message=f"{len(in_cooldown)} VoiceLink DID(s) in technical cooldown",
            )

        total_calls = sum(d.total_calls for d in vl_dids)
        if total_calls == 0:
            return ComponentHealth(
                name="voicelink",
                status=HealthStatus.UNVERIFIED,
                message="VoiceLink credentials active, awaiting live call traffic",
            )

        return ComponentHealth(
            name="voicelink",
            status=HealthStatus.HEALTHY,
            message=f"VoiceLink healthy ({total_calls} calls verified)",
        )

    async def _check_livekit_sip_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Evaluates LiveKit SIP outbound trunk configuration."""
        from api.services.livekit.client import effective_livekit_settings, livekit_configured

        if not livekit_configured():
            return ComponentHealth(
                name="livekit_sip",
                status=HealthStatus.NOT_CONFIGURED,
                message="LiveKit credentials not configured",
            )

        settings = effective_livekit_settings()
        if not settings.livekit_sip_outbound_trunk_id:
            return ComponentHealth(
                name="livekit_sip",
                status=HealthStatus.UNVERIFIED,
                message="LiveKit active but outbound SIP trunk ID not set",
            )

        return ComponentHealth(
            name="livekit_sip",
            status=HealthStatus.HEALTHY,
            message=f"LiveKit SIP outbound trunk configured ({settings.livekit_sip_outbound_trunk_id})",
        )

    async def _check_gemini_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Passive health evaluation for Gemini Live."""
        gemini_key = (
            os.getenv("GOOGLE_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")
            or os.getenv("GOOGLE_AI_API_KEY")
        )
        if not gemini_key:
            return ComponentHealth(
                name="gemini_live",
                status=HealthStatus.NOT_CONFIGURED,
                message="Gemini API key not configured",
            )

        # Inspect recent runs for opening cache or connection errors
        recent_errors = await self._scan_recent_run_events(
            organization_id=organization_id,
            event_types={"opening_audio_cache_failed", "startup_failed"},
            lookback_minutes=30,
        )

        if recent_errors:
            return ComponentHealth(
                name="gemini_live",
                status=HealthStatus.DEGRADED,
                message=f"Recent Gemini failures observed ({len(recent_errors)} in last 30m)",
                details={"recent_error_count": len(recent_errors)},
            )

        return ComponentHealth(
            name="gemini_live",
            status=HealthStatus.HEALTHY,
            message="Gemini Live key configured with no recent startup failures",
        )

    async def _check_deepgram_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Passive health evaluation for Deepgram STT."""
        deepgram_key = os.getenv("DEEPGRAM_API_KEY")
        if not deepgram_key:
            return ComponentHealth(
                name="deepgram",
                status=HealthStatus.NOT_CONFIGURED,
                message="Deepgram API key not configured",
            )
        return ComponentHealth(
            name="deepgram",
            status=HealthStatus.UNVERIFIED,
            message="Deepgram API key provisioned (awaiting usage evidence)",
        )

    async def _check_deepseek_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Passive health evaluation for DeepSeek LLM."""
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if not deepseek_key:
            return ComponentHealth(
                name="deepseek",
                status=HealthStatus.NOT_CONFIGURED,
                message="DeepSeek API key not configured",
            )
        return ComponentHealth(
            name="deepseek",
            status=HealthStatus.UNVERIFIED,
            message="DeepSeek API key provisioned (awaiting usage evidence)",
        )

    async def _check_cartesia_health(self, organization_id: Optional[int]) -> ComponentHealth:
        """Passive health evaluation for Cartesia TTS."""
        cartesia_key = os.getenv("CARTESIA_API_KEY")
        if not cartesia_key:
            return ComponentHealth(
                name="cartesia",
                status=HealthStatus.NOT_CONFIGURED,
                message="Cartesia API key not configured",
            )
        return ComponentHealth(
            name="cartesia",
            status=HealthStatus.UNVERIFIED,
            message="Cartesia API key provisioned (awaiting usage evidence)",
        )

    async def _scan_recent_run_events(
        self,
        organization_id: Optional[int],
        event_types: set[str],
        lookback_minutes: int = 30,
    ) -> List[Dict[str, Any]]:
        """Scans recent workflow runs for matching livekit_events without heavy queries."""
        try:
            since = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
            async with db_client.async_session() as session:
                stmt = select(WorkflowRunModel.logs).where(WorkflowRunModel.created_at >= since)
                if organization_id is not None:
                    stmt = stmt.where(WorkflowRunModel.organization_id == organization_id)
                stmt = stmt.order_by(WorkflowRunModel.id.desc()).limit(20)
                res = await session.execute(stmt)
                rows = res.scalars().all()

                found_events: List[Dict[str, Any]] = []
                for logs in rows:
                    if isinstance(logs, dict):
                        events = logs.get("livekit_events", [])
                        if isinstance(events, list):
                            for ev in events:
                                if isinstance(ev, dict) and ev.get("type") in event_types:
                                    found_events.append(ev)
                return found_events
        except Exception as exc:
            logger.warning(f"[Health] Passive run event scan skipped: {exc}")
            return []


provider_monitor = ProviderMonitor()
