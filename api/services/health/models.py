"""Health Monitoring and Alerting Models (Phase C3).

Strict architectural rules:
- Distinguish: HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN, NOT_CONFIGURED, UNVERIFIED.
- UNKNOWN must not appear as HEALTHY.
- Structured event formatting compatible with C1 correlation IDs, C2 audits, and C4 centralized search.
- Safe serialization without credentials or sensitive PII.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNVERIFIED = "UNVERIFIED"


class AlertSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class ComponentHealth(BaseModel):
    name: str
    status: HealthStatus
    latency_ms: Optional[float] = None
    message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HostMetrics(BaseModel):
    cpu_percent: float
    memory_percent: float
    memory_total_mb: float
    memory_available_mb: float
    disk_percent: float
    disk_total_gb: float
    disk_free_gb: float
    status: HealthStatus
    storage_pressure: bool = False
    metrics_scope: str = "container_linux"  # "local_docker_wsl2", "hostinger_vps", "local_windows", "container_linux"
    environment: str = "local"
    node_platform: str = ""
    is_container: bool = True
    scope_description: str = ""
    measured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IncidentEvent(BaseModel):
    incident_id: str
    component: str
    severity: AlertSeverity
    health_status: HealthStatus
    error_code: str
    message: str
    correlation_id: Optional[str] = None
    call_id: Optional[str] = None
    organization_id: Optional[int] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    first_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latest_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: Optional[datetime] = None
    count: int = 1
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


class AlertRule(BaseModel):
    rule_id: str
    component: str
    severity: AlertSeverity
    consecutive_failures_required: int = 1
    cooldown_seconds: int = 900  # 15 minutes default
    description: str


class SystemHealthReport(BaseModel):
    overall_status: HealthStatus
    liveness: bool
    readiness: bool
    components: Dict[str, ComponentHealth]
    host_metrics: Optional[HostMetrics] = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

