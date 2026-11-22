"""Operational Alert Manager and Incident Lifecycle (Phase C3).

Implements bounded, configurable alert rules:
- CRITICAL: PostgreSQL, Redis, Application, Critical Worker, Severe Disk Pressure.
- WARNING: Approaching resource limits, Degraded Providers, Cooldown DIDs, Elevated Failures.

Features:
- Sustained failure detection via consecutive check counters.
- Duplicate alert storm suppression with configurable cooldowns.
- First seen, latest seen, incident count, and sanitized diagnostic context.
- Automatic recovery/resolution dispatch when healthy state returns.
- Bounded in-memory incident history (1000 items) independent of DB availability.
- Acknowledgment support.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid

from loguru import logger

from api.services.health.external_notifier import external_notifier
from api.services.health.durable_incident_store import durable_incident_store, is_synthetic_incident
from api.services.health.models import (
    AlertRule,
    AlertSeverity,
    ComponentHealth,
    HealthStatus,
    HostMetrics,
    IncidentEvent,
    SystemHealthReport,
)

# Default Rules Specification
DEFAULT_RULES: Dict[str, AlertRule] = {
    "postgresql": AlertRule(
        rule_id="RULE-CRIT-PG",
        component="postgresql",
        severity=AlertSeverity.CRITICAL,
        consecutive_failures_required=1,
        cooldown_seconds=600,
        description="PostgreSQL connectivity lost or query timeout",
    ),
    "redis": AlertRule(
        rule_id="RULE-CRIT-REDIS",
        component="redis",
        severity=AlertSeverity.CRITICAL,
        consecutive_failures_required=1,
        cooldown_seconds=600,
        description="Redis unavailable or PING unresponsive",
    ),
    "backend_api": AlertRule(
        rule_id="RULE-CRIT-API",
        component="backend_api",
        severity=AlertSeverity.CRITICAL,
        consecutive_failures_required=1,
        cooldown_seconds=300,
        description="Backend API process unresponsive",
    ),
    "storage": AlertRule(
        rule_id="RULE-WARN-STORAGE",
        component="storage",
        severity=AlertSeverity.WARNING,
        consecutive_failures_required=2,
        cooldown_seconds=900,
        description="Storage bucket inaccessible or upload errors",
    ),
    "livekit": AlertRule(
        rule_id="RULE-CRIT-LIVEKIT",
        component="livekit",
        severity=AlertSeverity.CRITICAL,
        consecutive_failures_required=2,
        cooldown_seconds=600,
        description="LiveKit runtime or voice worker unavailable",
    ),
    "host_disk_severe": AlertRule(
        rule_id="RULE-CRIT-DISK",
        component="host_disk",
        severity=AlertSeverity.CRITICAL,
        consecutive_failures_required=1,
        cooldown_seconds=1800,
        description="Severe host disk pressure (>=90% utilized)",
    ),
    "host_resources_warn": AlertRule(
        rule_id="RULE-WARN-RESOURCES",
        component="host_resources",
        severity=AlertSeverity.WARNING,
        consecutive_failures_required=2,
        cooldown_seconds=1800,
        description="Approaching host resource thresholds (disk >=80% or RAM >=85%)",
    ),
    "vobiz": AlertRule(
        rule_id="RULE-WARN-VOBIZ",
        component="vobiz",
        severity=AlertSeverity.WARNING,
        consecutive_failures_required=2,
        cooldown_seconds=900,
        description="Vobiz carrier degraded or DIDs in cooldown/quarantine",
    ),
    "voicelink": AlertRule(
        rule_id="RULE-WARN-VOICELINK",
        component="voicelink",
        severity=AlertSeverity.WARNING,
        consecutive_failures_required=2,
        cooldown_seconds=900,
        description="VoiceLink carrier degraded or DIDs in cooldown/quarantine",
    ),
    "gemini_live": AlertRule(
        rule_id="RULE-WARN-GEMINI",
        component="gemini_live",
        severity=AlertSeverity.WARNING,
        consecutive_failures_required=2,
        cooldown_seconds=900,
        description="Gemini Live degraded or elevated startup errors",
    ),
}


class AlertManager:
    """Manages active incidents, consecutive failures, and alert dispatch."""

    def __init__(self, max_history: int = 1000) -> None:
        self.rules: Dict[str, AlertRule] = dict(DEFAULT_RULES)
        self.active_incidents: Dict[str, IncidentEvent] = {}
        self.incident_history: deque[IncidentEvent] = deque(maxlen=max_history)
        self._consecutive_failures: Dict[str, int] = {}
        self._last_alert_sent: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def evaluate_system_report(
        self,
        report: SystemHealthReport,
        provider_components: Optional[Dict[str, ComponentHealth]] = None,
        correlation_id: Optional[str] = None,
    ) -> List[IncidentEvent]:
        """Evaluates health reports against alert rules and triggers or resolves incidents."""
        async with self._lock:
            all_components: Dict[str, ComponentHealth] = dict(report.components)
            if provider_components:
                all_components.update(provider_components)

            fired_or_updated: List[IncidentEvent] = []
            now = datetime.now(UTC)

            # 1. Evaluate infrastructure & provider components
            for name, comp in all_components.items():
                rule = self.rules.get(name)
                if not rule:
                    continue

                is_failure = comp.status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED)
                if comp.status == HealthStatus.NOT_CONFIGURED:
                    # Not configured is expected in unprovisioned environments; do not alert
                    is_failure = False

                if is_failure:
                    self._consecutive_failures[name] = self._consecutive_failures.get(name, 0) + 1
                    count = self._consecutive_failures[name]

                    if count >= rule.consecutive_failures_required:
                        inc = await self._handle_component_failure(
                            name=name,
                            comp=comp,
                            rule=rule,
                            now=now,
                            correlation_id=correlation_id,
                        )
                        if inc:
                            fired_or_updated.append(inc)
                else:
                    # Component is healthy: check if we need to resolve an existing incident
                    self._consecutive_failures[name] = 0
                    resolved_inc = await self._handle_component_recovery(name, now)
                    if resolved_inc:
                        fired_or_updated.append(resolved_inc)

            # 2. Evaluate host metrics
            if report.host_metrics is not None:
                host_inc = await self._evaluate_host_metrics(report.host_metrics, now, correlation_id)
                if host_inc:
                    fired_or_updated.append(host_inc)

            # 3. Reconcile any unclosed historical incidents in durable store for components verified healthy
            healthy_comps = {k: v for k, v in all_components.items() if v.status == HealthStatus.HEALTHY}
            if healthy_comps:
                await durable_incident_store.reconcile_healthy_components(healthy_comps)

            return fired_or_updated

    async def _handle_component_failure(
        self,
        name: str,
        comp: ComponentHealth,
        rule: AlertRule,
        now: datetime,
        correlation_id: Optional[str],
    ) -> Optional[IncidentEvent]:
        """Handles an ongoing or new failure for a component."""
        existing = self.active_incidents.get(name)
        if existing:
            existing.latest_seen = now
            existing.count += 1
            existing.message = comp.message or existing.message
            existing.context.update(comp.details)

            # Check cooldown before re-dispatching
            last_sent = self._last_alert_sent.get(name)
            if last_sent and (now - last_sent).total_seconds() < rule.cooldown_seconds:
                # Suppressed by cooldown
                return None

            self._last_alert_sent[name] = now
            logger.warning(
                f"[Alert Re-dispatched] {rule.severity.value} on {name} "
                f"(count={existing.count}, incident={existing.incident_id})"
            )
            await external_notifier.notify(existing, is_recovery=False)
            await durable_incident_store.record_event(existing, event_type="REDISPATCHED")
            return existing

        # Create new incident
        inc_id = f"inc_{name}_{int(now.timestamp())}_{uuid.uuid4().hex[:6]}"
        incident = IncidentEvent(
            incident_id=inc_id,
            component=name,
            severity=rule.severity,
            health_status=comp.status,
            error_code=f"ERR_{name.upper()}_{comp.status.value}",
            message=comp.message or f"{name} failure detected",
            correlation_id=correlation_id,
            context=comp.details,
            first_seen=now,
            latest_seen=now,
            count=1,
        )
        self.active_incidents[name] = incident
        self._last_alert_sent[name] = now

        logger.error(
            f"[Alert Fired] {rule.severity.value} on {name}: {incident.message} "
            f"(incident_id={inc_id})"
        )
        await external_notifier.notify(incident, is_recovery=False)
        await durable_incident_store.record_event(incident, event_type="FIRED")
        return incident

    async def _handle_component_recovery(
        self, name: str, now: datetime
    ) -> Optional[IncidentEvent]:
        """Handles component recovery, resolving any active incident."""
        existing = self.active_incidents.pop(name, None)
        if not existing:
            return None

        existing.resolved_at = now
        existing.health_status = HealthStatus.HEALTHY
        self._last_alert_sent.pop(name, None)
        self.incident_history.append(existing)

        logger.info(
            f"[Alert Recovered] Incident {existing.incident_id} on {name} is now RESOLVED"
        )
        await external_notifier.notify(existing, is_recovery=True)
        await durable_incident_store.record_event(existing, event_type="RESOLVED")
        return existing

    async def _evaluate_host_metrics(
        self, host: HostMetrics, now: datetime, correlation_id: Optional[str]
    ) -> Optional[IncidentEvent]:
        """Evaluates disk and memory pressure rules."""
        if host.storage_pressure:
            comp = ComponentHealth(
                name="host_disk",
                status=HealthStatus.UNHEALTHY,
                message=f"Severe disk pressure: {host.disk_percent}% used ({host.disk_free_gb} GB free)",
                details={"disk_percent": host.disk_percent, "disk_free_gb": host.disk_free_gb},
            )
            rule = self.rules.get("host_disk_severe")
            if rule:
                return await self._handle_component_failure(
                    "host_disk", comp, rule, now, correlation_id
                )
        else:
            # Check recovery for host_disk
            await self._handle_component_recovery("host_disk", now)

        if host.status == HealthStatus.DEGRADED:
            comp = ComponentHealth(
                name="host_resources",
                status=HealthStatus.DEGRADED,
                message=f"Resource pressure: CPU {host.cpu_percent}%, RAM {host.memory_percent}%, Disk {host.disk_percent}%",
                details={
                    "cpu_percent": host.cpu_percent,
                    "memory_percent": host.memory_percent,
                    "disk_percent": host.disk_percent,
                },
            )
            rule = self.rules.get("host_resources_warn")
            if rule:
                return await self._handle_component_failure(
                    "host_resources", comp, rule, now, correlation_id
                )
        else:
            await self._handle_component_recovery("host_resources", now)

        return None

    async def acknowledge_incident(self, incident_id: str, actor_id: str) -> bool:
        """Acknowledges an active incident and durably records the event."""
        now = datetime.now(UTC)
        for inc in self.active_incidents.values():
            if inc.incident_id == incident_id:
                inc.acknowledged = True
                inc.acknowledged_by = actor_id
                inc.acknowledged_at = now
                logger.info(f"[Alert] Incident {incident_id} acknowledged by {actor_id}")
                await durable_incident_store.record_event(inc, event_type="ACKNOWLEDGED")
                return True
        return False

    def get_active_incidents(self) -> List[IncidentEvent]:
        """Returns list of currently active operational incidents (excludes synthetic test probes)."""
        return [
            inc for inc in self.active_incidents.values()
            if not is_synthetic_incident(inc) and inc.health_status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED)
        ]

    def get_incident_history(self, limit: int = 50) -> List[IncidentEvent]:
        """Returns recent incident history from in-memory ring buffer."""
        items = list(self.incident_history)
        return items[-limit:]


alert_manager = AlertManager()

