"""Provider endpoint policy: a stored API key only travels to approved hosts.

Threat: a user (or an org member editing a workflow override that runs with the
owner's key) sets ``base_url``/``endpoint`` to a host they control and receives
the owner's key in the Authorization header.

Policy, applied whenever the section carries a credential:
* built-in providers: HTTPS only, exact host (or documented suffix for Azure),
  default port;
* anything else must be an operator-approved endpoint listed in the
  ``SPX_ADMIN_PROVIDER_ENDPOINTS`` environment variable
  (``provider=https://host[:port][/path],...``), i.e. a privileged path outside
  the dashboard. Approved endpoints are still SSRF-checked: no loopback,
  private, link-local, reserved, multicast or cloud-metadata addresses (literal
  or resolved), unless ``SPX_TRUSTED_LOCAL_ENDPOINTS=true`` (local development);
* always rejected: non-http(s) schemes, embedded credentials (``user@``),
  percent-encoded or non-ASCII hosts, backslashes, whitespace/control chars,
  numeric host encodings (decimal/hex/octal IPv4) for built-ins;
* workflow ``model_overrides`` may only name built-in hosts: an override is run
  with the workflow owner's key, so it can never use a custom endpoint.

Sections without any credential (e.g. a local, keyless Speaches server) have
nothing to exfiltrate and are not restricted here.

Errors name the provider and field only; URLs with credentials are never echoed.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

URL_FIELDS = ("base_url", "endpoint")
SECTIONS = ("llm", "tts", "stt", "embeddings", "realtime")

BUILTIN_HOSTS: dict[str, frozenset[str]] = {
    "openai": frozenset({"api.openai.com"}),
    "openai_realtime": frozenset({"api.openai.com"}),
    "deepseek": frozenset({"api.deepseek.com"}),
    "openrouter": frozenset({"openrouter.ai"}),
    "google": frozenset({"generativelanguage.googleapis.com"}),
    "google_realtime": frozenset({"generativelanguage.googleapis.com"}),
    "groq": frozenset({"api.groq.com"}),
    "elevenlabs": frozenset({"api.elevenlabs.io"}),
    "cartesia": frozenset({"api.cartesia.ai"}),
    "deepgram": frozenset({"api.deepgram.com"}),
    "sarvam": frozenset({"api.sarvam.ai"}),
}
BUILTIN_SUFFIXES: dict[str, tuple[str, ...]] = {
    "azure": (".openai.azure.com", ".cognitiveservices.azure.com"),
    # Official regional/residency hosts, e.g. api.eu.residency.elevenlabs.io
    "elevenlabs": (".elevenlabs.io",),
}
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$", re.I)


class EndpointPolicyError(ValueError):
    """A provider endpoint is not allowed to receive a credential."""

    def __init__(self, provider: str, field: str, reason: str):
        self.provider, self.field, self.reason = provider, field, reason
        super().__init__(
            f"The {field} for provider '{provider}' is not allowed: {reason}. "
            "Use the provider's official endpoint; custom endpoints must be "
            "approved by the deployment operator."
        )


def _trusted_local() -> bool:
    return os.getenv("SPX_TRUSTED_LOCAL_ENDPOINTS", "false").strip().lower() == "true"


def _parse(url: str, provider: str, field: str):
    if not isinstance(url, str):
        raise EndpointPolicyError(provider, field, "not a string")
    if url != url.strip() or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise EndpointPolicyError(provider, field, "contains whitespace, control or non-ASCII characters")
    if "\\" in url:
        raise EndpointPolicyError(provider, field, "contains a backslash")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise EndpointPolicyError(provider, field, "only https endpoints are allowed")
    netloc = parts.netloc
    if not netloc:
        raise EndpointPolicyError(provider, field, "has no host")
    if "@" in netloc:
        raise EndpointPolicyError(provider, field, "must not embed credentials")
    if "%" in netloc:
        raise EndpointPolicyError(provider, field, "host must not be percent-encoded")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise EndpointPolicyError(provider, field, "has no host")
    try:
        port = parts.port
    except ValueError:
        raise EndpointPolicyError(provider, field, "has an invalid port") from None
    return parts.scheme, host, port, parts


def _origin(scheme: str, host: str, port: Optional[int]) -> str:
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}:{port or default}"


def _approved_origins(provider: str) -> set[str]:
    approved: set[str] = set()
    raw = os.getenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "")
    for item in raw.split(","):
        name, sep, url = item.strip().partition("=")
        if not sep or name.strip().lower() != provider:
            continue
        try:
            scheme, host, port, _ = _parse(url.strip(), provider, "approved endpoint")
        except EndpointPolicyError:
            continue  # malformed operator entries approve nothing
        approved.add(_origin(scheme, host, port))
    return approved


def _literal_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST.match(host):
        try:  # accepts decimal / octal / hex / short forms like 127.1
            return ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return None
    return None


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTOFOUR = ipaddress.ip_network("2002::/16")


def _unsafe_ip(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:  # embedded IPv4 in the low 32 bits
            return _unsafe_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        elif ip in _SIXTOFOUR:  # embedded IPv4 in bits 16..48
            return _unsafe_ip(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or str(ip) in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
    )


def _ssrf_check(host: str, provider: str, field: str, resolve: bool) -> None:
    if _trusted_local():
        return
    if host in _METADATA_HOSTS or host == "localhost" or host.endswith(".localhost"):
        raise EndpointPolicyError(provider, field, "points at a local or metadata host")
    ip = _literal_ip(host)
    if ip is not None:
        if _unsafe_ip(ip):
            raise EndpointPolicyError(provider, field, "points at a private, loopback or metadata address")
        return
    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise EndpointPolicyError(provider, field, "host does not resolve") from None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if _unsafe_ip(addr):
            raise EndpointPolicyError(provider, field, "resolves to a private, loopback or metadata address")


def is_builtin_endpoint(provider: str, url: str) -> bool:
    try:
        scheme, host, port, _ = _parse(url, provider, "endpoint")
    except EndpointPolicyError:
        return False
    if scheme != "https" or port not in (None, 443):
        return False
    if host in BUILTIN_HOSTS.get(provider, frozenset()):
        return True
    return any(host.endswith(s) for s in BUILTIN_SUFFIXES.get(provider, ()))


def check_endpoint(
    provider: str,
    url: Optional[str],
    *,
    field: str = "base_url",
    has_credential: bool = True,
    allow_custom: bool = True,
    resolve: bool = True,
) -> None:
    """Raise EndpointPolicyError unless *url* may receive this provider's key."""
    provider = str(getattr(provider, "value", provider) or "").lower()
    if url is None or url == "":
        return
    scheme, host, port, _ = _parse(url, provider, field)
    if not has_credential:
        return
    if is_builtin_endpoint(provider, url):
        return
    if not allow_custom:
        raise EndpointPolicyError(provider, field, "workflow settings may only use the provider's official endpoint")
    if _origin(scheme, host, port) not in _approved_origins(provider):
        raise EndpointPolicyError(provider, field, "custom endpoints require operator approval")
    if scheme != "https" and not _trusted_local():
        raise EndpointPolicyError(provider, field, "only https endpoints are allowed")
    _ssrf_check(host, provider, field, resolve)


def _has_credential(section: Any) -> bool:
    get = section.get if isinstance(section, dict) else lambda k, d=None: getattr(section, k, d)
    for name in ("api_key", "aws_secret_key", "credentials"):
        value = get(name, None)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(isinstance(v, str) and v.strip() for v in value):
            return True
    return False


def _iter_sections(container: Any) -> Iterable[tuple[str, Any]]:
    for name in SECTIONS:
        section = container.get(name) if isinstance(container, dict) else getattr(container, name, None)
        if section is not None:
            yield name, section


def enforce_config_endpoints(config: Any, *, resolve: bool = True) -> None:
    """Check every credentialed section of a (effective) user configuration."""
    for _name, section in _iter_sections(config):
        get = section.get if isinstance(section, dict) else lambda k, d=None: getattr(section, k, d)
        provider = get("provider", "")
        for field in URL_FIELDS:
            check_endpoint(
                provider,
                get(field, None),
                field=field,
                has_credential=_has_credential(section),
                resolve=resolve,
            )


def enforce_override_endpoints(model_overrides: Optional[dict]) -> None:
    """Workflow overrides: URL fields may only name built-in endpoints."""
    if not isinstance(model_overrides, dict):
        return
    for _name, section in _iter_sections(model_overrides):
        if not isinstance(section, dict):
            continue
        provider = str(section.get("provider") or "")
        for field in URL_FIELDS:
            url = section.get(field)
            if url in (None, ""):
                continue
            check_endpoint(provider, url, field=field, has_credential=True, allow_custom=False)
