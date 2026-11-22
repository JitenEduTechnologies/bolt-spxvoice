from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from api.constants import APP_ROOT_DIR, LIVEKIT_WORKER_MANAGED_BY_API
from api.services.livekit.runtime_config import (
    LiveKitRuntimeSettings,
    effective_livekit_settings,
    livekit_environment,
)

# Worker lifecycle states. Process liveness (`running`) is reported separately
# from LiveKit registration/readiness (`registration_verified`, which stays
# None because the API cannot verify provider registration without a call).
WORKER_STATE_MANAGED_ALIVE = "managed_alive"
WORKER_STATE_SURVIVING_VERIFIED = "surviving_verified"
WORKER_STATE_ABSENT = "absent"
WORKER_STATE_OWNERSHIP_UNCERTAIN = "ownership_uncertain"
WORKER_STATE_UNHEALTHY = "unhealthy"

_WORKER_MODULE = "api.services.livekit.worker"
_WORKER_ARGV_TAIL = ("-m", _WORKER_MODULE, "start")
# Coordination state lives OUTSIDE api/ because uvicorn --reload watches api/.
_LIFECYCLE_DIR = Path(tempfile.gettempdir()) / "spx_voice"
_LIFECYCLE_JSON = _LIFECYCLE_DIR / "livekit_worker.json"
_LIFECYCLE_LOCK = _LIFECYCLE_DIR / "livekit_worker.lock"

_SIGNATURE_HASH_FIELD = "signature_sha256"
_LEGACY_SIGNATURE_FIELD = "signature"
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_RETRY_INTERVAL_SECONDS = 0.05


class WorkerCoordinationError(RuntimeError):
    """Raised when exclusive lifecycle coordination cannot be acquired."""


def _signature_digest(signature: str) -> str:
    """Return a one-way digest of the worker signature.

    The raw signature includes the LiveKit API secret; only the digest may be
    persisted or compared.
    """
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


def _proc_argv(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [part.decode("utf-8", "ignore") for part in raw.rstrip(b"\x00").split(b"\x00")]


def _parse_stat_start_ticks(stat_text: str) -> int | None:
    try:
        return int(stat_text.rsplit(")", 1)[1].split()[19])
    except (IndexError, ValueError):
        return None


def _proc_start_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    return _parse_stat_start_ticks(stat)


def _pid_namespace() -> str | None:
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return None


def _is_worker_argv(argv: list[str] | None) -> bool:
    if not argv or len(argv) != len(_WORKER_ARGV_TAIL) + 1:
        return False
    return os.path.basename(argv[0]).startswith("python") and tuple(argv[1:]) == _WORKER_ARGV_TAIL


def _find_worker_processes() -> list[tuple[int, int | None]]:
    """Exact `python -m api.services.livekit.worker start` processes (pid, start_ticks)."""
    matches: list[tuple[int, int | None]] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return matches
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if _is_worker_argv(_proc_argv(pid)):
            matches.append((pid, _proc_start_ticks(pid)))
    return matches


def _read_record() -> dict | None:
    if _LIFECYCLE_JSON.is_symlink():
        return None
    try:
        data = json.loads(_LIFECYCLE_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Never surface or use a legacy plaintext signature field.
    data.pop(_LEGACY_SIGNATURE_FIELD, None)
    return data


def _write_record(pid: int, start_ticks: int | None, signature: str) -> None:
    payload = {
        "pid": pid,
        "start_ticks": start_ticks,
        _SIGNATURE_HASH_FIELD: _signature_digest(signature),
        "pid_namespace": _pid_namespace(),
        "recorded_at": time.time(),
    }
    temp_path: str | None = None
    try:
        if _LIFECYCLE_DIR.is_symlink() or _LIFECYCLE_JSON.is_symlink():
            logger.warning(
                "Refusing to write LiveKit worker lifecycle record through a symlink"
            )
            return
        _LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(_LIFECYCLE_DIR, 0o700)
        except OSError:
            pass
        fd, temp_path = tempfile.mkstemp(
            dir=str(_LIFECYCLE_DIR), prefix=".livekit_worker.", suffix=".tmp"
        )
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, _LIFECYCLE_JSON)
        temp_path = None
        try:
            os.chmod(_LIFECYCLE_JSON, 0o600)
        except OSError:
            pass
    except OSError as exc:  # pragma: no cover - non-fatal
        logger.warning(f"Could not persist LiveKit worker lifecycle record: {exc}")
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _clear_record() -> None:
    try:
        _LIFECYCLE_JSON.unlink()
    except OSError:
        pass


@contextlib.contextmanager
def _lifecycle_lock(timeout_seconds: float = _LOCK_TIMEOUT_SECONDS):
    """Process-safe coordination; fails closed if the lock cannot be acquired."""
    if _LIFECYCLE_DIR.is_symlink() or _LIFECYCLE_LOCK.is_symlink():
        raise WorkerCoordinationError("livekit_worker_lifecycle_lock_symlink")
    try:
        _LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        logger.warning(
            f"Could not create LiveKit worker lock directory: {type(exc).__name__}"
        )
        raise WorkerCoordinationError(
            "livekit_worker_lifecycle_lock_unavailable"
        ) from exc
    try:
        handle = open(_LIFECYCLE_LOCK, "a+")
    except OSError as exc:
        logger.warning(
            f"Could not open LiveKit worker lifecycle lock: {type(exc).__name__}"
        )
        raise WorkerCoordinationError(
            "livekit_worker_lifecycle_lock_unavailable"
        ) from exc

    locked = False
    try:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            try:
                if sys.platform == "win32":  # pragma: no cover - Windows only
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)
        if not locked:
            raise WorkerCoordinationError("livekit_worker_lifecycle_lock_timeout")
        yield
    finally:
        if locked:
            try:
                if sys.platform == "win32":  # pragma: no cover - Windows only
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            handle.close()
        except OSError:
            pass


@dataclass(frozen=True)
class LiveKitWorkerStatus:
    managed_by_api: bool
    running: bool
    pid: int | None = None
    message: str | None = None
    state: str = ""
    registration_verified: bool | None = None


_worker_process: subprocess.Popen | None = None
_worker_signature: str | None = None


def _surviving_status() -> LiveKitWorkerStatus:
    workers = _find_worker_processes()
    if not workers:
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            message="no_worker_process",
            state=WORKER_STATE_ABSENT,
        )
    if len(workers) > 1:
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            message="multiple_worker_candidates",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )
    pid, ticks = workers[0]
    record = _read_record()
    if not record:
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            pid=pid,
            message="surviving_worker_unverified_no_record",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )
    namespace = _pid_namespace()
    if namespace and record.get("pid_namespace") and record["pid_namespace"] != namespace:
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            pid=pid,
            message="process_namespace_changed",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )
    if record.get("pid") != pid or (
        record.get("start_ticks") is not None and record["start_ticks"] != ticks
    ):
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            pid=pid,
            message="record_mismatch_or_pid_reuse",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )
    recorded_digest = record.get(_SIGNATURE_HASH_FIELD)
    if not recorded_digest:
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            pid=pid,
            message="surviving_worker_unverified_no_signature_digest",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )
    current_digest = _signature_digest(effective_livekit_settings().worker_signature())
    if recorded_digest != current_digest:
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            pid=pid,
            message="worker_configuration_changed",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )
    return LiveKitWorkerStatus(
        managed_by_api=True,
        running=True,
        pid=pid,
        message="surviving_worker_verified",
        state=WORKER_STATE_SURVIVING_VERIFIED,
        registration_verified=None,
    )


def get_worker_status() -> LiveKitWorkerStatus:
    if not LIVEKIT_WORKER_MANAGED_BY_API:
        return LiveKitWorkerStatus(
            managed_by_api=False,
            running=False,
            message="worker_not_managed_by_api",
            state=WORKER_STATE_ABSENT,
        )
    if _worker_process is not None:
        if _worker_process.poll() is not None:
            return LiveKitWorkerStatus(
                managed_by_api=True,
                running=False,
                message="managed_worker_exited",
                state=WORKER_STATE_ABSENT,
            )
        record = _read_record()
        ticks = _proc_start_ticks(_worker_process.pid)
        if (
            record
            and record.get("pid") == _worker_process.pid
            and record.get("start_ticks") is not None
            and ticks is not None
            and record["start_ticks"] != ticks
        ):
            return LiveKitWorkerStatus(
                managed_by_api=True,
                running=False,
                pid=_worker_process.pid,
                message="pid_reused_identity_mismatch",
                state=WORKER_STATE_UNHEALTHY,
            )
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=True,
            pid=_worker_process.pid,
            state=WORKER_STATE_MANAGED_ALIVE,
        )
    return _surviving_status()


def apply_livekit_worker_settings(
    settings: LiveKitRuntimeSettings | None = None,
) -> LiveKitWorkerStatus:
    settings = settings or effective_livekit_settings()
    if not LIVEKIT_WORKER_MANAGED_BY_API:
        return LiveKitWorkerStatus(
            managed_by_api=False,
            running=False,
            message="worker_not_managed_by_api",
            state=WORKER_STATE_ABSENT,
        )

    if not settings.is_livekit:
        stop_livekit_worker()
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            message="runtime_pipecat",
            state=WORKER_STATE_ABSENT,
        )

    if not settings.configured:
        stop_livekit_worker()
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            message="livekit_not_configured",
            state=WORKER_STATE_ABSENT,
        )

    signature = settings.worker_signature()
    if (
        _worker_process
        and _worker_process.poll() is None
        and _worker_signature == signature
    ):
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=True,
            pid=_worker_process.pid,
            state=WORKER_STATE_MANAGED_ALIVE,
        )

    try:
        with _lifecycle_lock():
            if (
                _worker_process
                and _worker_process.poll() is None
                and _worker_signature == signature
            ):
                return LiveKitWorkerStatus(
                    managed_by_api=True,
                    running=True,
                    pid=_worker_process.pid,
                    state=WORKER_STATE_MANAGED_ALIVE,
                )

            surviving = _surviving_status()
            if surviving.state in (
                WORKER_STATE_SURVIVING_VERIFIED,
                WORKER_STATE_OWNERSHIP_UNCERTAIN,
            ):
                # Never spawn a duplicate worker or terminate one we cannot verify.
                return surviving

            stop_livekit_worker()
            _start_livekit_worker(settings, signature)
            return get_worker_status()
    except WorkerCoordinationError as exc:
        reason = "lock_timeout" if "timeout" in str(exc) else "unsafe_lifecycle_path"
        return LiveKitWorkerStatus(
            managed_by_api=True,
            running=False,
            message=f"coordination_failed_{reason}",
            state=WORKER_STATE_OWNERSHIP_UNCERTAIN,
        )


def stop_livekit_worker() -> None:
    global _worker_process, _worker_signature

    if not _worker_process:
        _worker_signature = None
        return

    if _worker_process.poll() is None:
        logger.info(f"Stopping managed LiveKit worker pid={_worker_process.pid}")
        _terminate_worker_process_group(_worker_process)
        try:
            _worker_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"Managed LiveKit worker pid={_worker_process.pid} did not stop; killing"
            )
            _kill_worker_process_group(_worker_process)
            _worker_process.wait(timeout=5)
    else:
        _terminate_worker_process_group(_worker_process)

    _worker_process = None
    _worker_signature = None
    _clear_record()


def _terminate_worker_process_group(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        process.terminate()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_worker_process_group(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _start_livekit_worker(settings: LiveKitRuntimeSettings, signature: str) -> None:
    global _worker_process, _worker_signature

    env = livekit_environment(settings)
    cmd = [sys.executable, "-m", _WORKER_MODULE, "start"]
    logger.info("Starting managed LiveKit worker")
    _worker_process = subprocess.Popen(
        cmd,
        cwd=str(APP_ROOT_DIR.parent),
        env=env,
        start_new_session=sys.platform != "win32",
    )
    _worker_signature = signature
    start_ticks: int | None = None
    for _ in range(20):
        start_ticks = _proc_start_ticks(_worker_process.pid)
        if start_ticks is not None:
            break
        time.sleep(0.05)
    _write_record(_worker_process.pid, start_ticks, signature)
