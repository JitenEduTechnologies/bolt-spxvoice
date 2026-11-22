"""Focused offline tests for the managed LiveKit worker lifecycle.

All tests are isolated: no real worker processes, no Docker, no provider calls.
"""

import asyncio
import contextlib
import json
import os
import stat as stat_module
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.services.livekit import worker_process as wp


class _FakePopen:
    def __init__(self, pid: int = 4242, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "LIVEKIT_WORKER_MANAGED_BY_API", True)
    monkeypatch.setattr(wp, "_LIFECYCLE_DIR", tmp_path)
    monkeypatch.setattr(wp, "_LIFECYCLE_JSON", tmp_path / "livekit_worker.json")
    monkeypatch.setattr(wp, "_LIFECYCLE_LOCK", tmp_path / "livekit_worker.lock")
    monkeypatch.setattr(wp, "_worker_process", None)
    monkeypatch.setattr(wp, "_worker_signature", None)
    monkeypatch.setattr(wp, "_pid_namespace", lambda: "ns:1")
    monkeypatch.setattr(wp, "livekit_environment", lambda settings: {})
    return tmp_path


def _settings(signature: str = "sig-1") -> SimpleNamespace:
    return SimpleNamespace(
        is_livekit=True,
        configured=True,
        worker_signature=lambda: signature,
    )


def _write_record(tmp_path, *, pid, ticks, signature="sig-1", namespace="ns:1") -> None:
    (tmp_path / "livekit_worker.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "start_ticks": ticks,
                "signature_sha256": wp._signature_digest(signature),
                "pid_namespace": namespace,
                "recorded_at": 0.0,
            }
        ),
        encoding="utf-8",
    )


def _write_legacy_record(tmp_path, *, pid, ticks, signature="sig-1", namespace="ns:1") -> None:
    """Pre-Gate-1C record layout that stored the raw signature."""
    (tmp_path / "livekit_worker.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "start_ticks": ticks,
                "signature": signature,
                "pid_namespace": namespace,
                "recorded_at": 0.0,
            }
        ),
        encoding="utf-8",
    )


def test_absent_when_no_worker_process(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [])
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_ABSENT
    assert status.running is False
    assert status.managed_by_api is True


def test_managed_alive_when_popen_alive_and_identity_matches(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_worker_process", _FakePopen(pid=4242))
    _write_record(lifecycle, pid=4242, ticks=111)
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 111)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_MANAGED_ALIVE
    assert status.running is True
    assert status.pid == 4242


def test_unhealthy_when_managed_pid_was_reused(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_worker_process", _FakePopen(pid=4242))
    _write_record(lifecycle, pid=4242, ticks=111)
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 999)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_UNHEALTHY
    assert status.running is False


def test_surviving_worker_verified(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=2798, ticks=222, signature="sig-1")
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    monkeypatch.setattr(wp, "effective_livekit_settings", lambda: _settings("sig-1"))
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_SURVIVING_VERIFIED
    assert status.running is True
    assert status.pid == 2798
    assert status.registration_verified is None


def test_stale_record_pid_mismatch_is_uncertain(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=1111, ticks=222)
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.running is False


def test_pid_reuse_detected_via_start_ticks(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=2798, ticks=111)
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.running is False


def test_multiple_candidates_is_uncertain(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=2798, ticks=222)
    monkeypatch.setattr(
        wp, "_find_worker_processes", lambda: [(2798, 222), (2800, 333)]
    )
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.running is False


def test_missing_record_is_uncertain(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.running is False


def test_namespace_change_is_uncertain(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=2798, ticks=222, namespace="ns:old")
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN


def test_configuration_mismatch_is_uncertain_and_never_kills(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=2798, ticks=222, signature="sig-old")
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    monkeypatch.setattr(wp, "effective_livekit_settings", lambda: _settings("sig-new"))
    kill = Mock()
    monkeypatch.setattr(wp, "_terminate_worker_process_group", kill)
    monkeypatch.setattr(wp, "_kill_worker_process_group", kill)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.message == "worker_configuration_changed"
    kill.assert_not_called()


def test_apply_does_not_spawn_duplicate_when_surviving_verified(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=2798, ticks=222, signature="sig-1")
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    monkeypatch.setattr(wp, "effective_livekit_settings", lambda: _settings("sig-1"))
    popen = Mock()
    monkeypatch.setattr(wp.subprocess, "Popen", popen)
    status = wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert status.state == wp.WORKER_STATE_SURVIVING_VERIFIED
    popen.assert_not_called()


def test_apply_refuses_spawn_when_ownership_uncertain(lifecycle, monkeypatch):
    _write_record(lifecycle, pid=1111, ticks=222)
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    popen = Mock()
    monkeypatch.setattr(wp.subprocess, "Popen", popen)
    status = wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    popen.assert_not_called()


def test_apply_spawns_exactly_one_when_absent(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 555)
    fake = _FakePopen(pid=4242)
    popen = Mock(return_value=fake)
    monkeypatch.setattr(wp.subprocess, "Popen", popen)
    status = wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert popen.call_count == 1
    assert status.state == wp.WORKER_STATE_MANAGED_ALIVE
    record = json.loads((lifecycle / "livekit_worker.json").read_text())
    assert record["pid"] == 4242
    assert record["start_ticks"] == 555


def test_second_apply_does_not_respawn_managed_worker(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 555)
    fake = _FakePopen(pid=4242)
    popen = Mock(return_value=fake)
    monkeypatch.setattr(wp.subprocess, "Popen", popen)
    wp.apply_livekit_worker_settings(_settings("sig-1"))
    wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert popen.call_count == 1


def test_managed_by_api_false_reports_not_managed(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "LIVEKIT_WORKER_MANAGED_BY_API", False)
    status = wp.get_worker_status()
    assert status.managed_by_api is False
    assert status.running is False
    assert status.message == "worker_not_managed_by_api"


def test_record_never_contains_livekit_secret(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 555)
    monkeypatch.setattr(wp.subprocess, "Popen", Mock(return_value=_FakePopen(pid=4242)))
    secret = "livekit-secret-do-not-persist"
    wp.apply_livekit_worker_settings(_settings(secret))
    raw = (lifecycle / "livekit_worker.json").read_text(encoding="utf-8")
    assert secret not in raw
    payload = json.loads(raw)
    assert "signature" not in payload
    assert payload["signature_sha256"] == wp._signature_digest(secret)


def test_legacy_plaintext_signature_is_never_returned(lifecycle):
    _write_legacy_record(lifecycle, pid=2798, ticks=222, signature="legacy-secret")
    record = wp._read_record()
    assert record is not None
    assert "signature" not in record
    assert "legacy-secret" not in json.dumps(record)


def test_legacy_record_without_digest_is_uncertain(lifecycle, monkeypatch):
    _write_legacy_record(lifecycle, pid=2798, ticks=222)
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.message == "surviving_worker_unverified_no_signature_digest"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced on Windows"
)
def test_record_and_directory_permissions_are_private(lifecycle):
    wp._write_record(4242, 555, "sig-1")
    assert stat_module.S_IMODE(os.stat(lifecycle).st_mode) == 0o700
    assert stat_module.S_IMODE(os.stat(lifecycle / "livekit_worker.json").st_mode) == 0o600


def test_record_write_is_atomic_and_leaves_no_temp_files(lifecycle):
    wp._write_record(4242, 555, "sig-1")
    wp._write_record(4243, 556, "sig-2")
    assert list(lifecycle.glob(".livekit_worker.*.tmp")) == []
    record = json.loads((lifecycle / "livekit_worker.json").read_text(encoding="utf-8"))
    assert record["pid"] == 4243


def test_corrupt_record_is_treated_as_uncertain(lifecycle, monkeypatch):
    (lifecycle / "livekit_worker.json").write_text("{ not valid json", encoding="utf-8")
    assert wp._read_record() is None
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    status = wp.get_worker_status()
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.message == "surviving_worker_unverified_no_record"


def test_incomplete_record_never_spawns(lifecycle, monkeypatch):
    (lifecycle / "livekit_worker.json").write_text('{"pid": 2798}', encoding="utf-8")
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [(2798, 222)])
    monkeypatch.setattr(wp, "_proc_start_ticks", lambda pid: 222)
    popen = Mock()
    monkeypatch.setattr(wp.subprocess, "Popen", popen)
    status = wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    popen.assert_not_called()


def test_lock_timeout_fails_closed(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [])
    popen = Mock()
    monkeypatch.setattr(wp.subprocess, "Popen", popen)

    @contextlib.contextmanager
    def _lock_timeout():
        raise wp.WorkerCoordinationError("livekit_worker_lifecycle_lock_timeout")
        yield  # pragma: no cover

    monkeypatch.setattr(wp, "_lifecycle_lock", _lock_timeout)
    status = wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.message == "coordination_failed_lock_timeout"
    assert status.running is False
    popen.assert_not_called()


def test_lock_is_released_after_exception(lifecycle):
    with pytest.raises(RuntimeError):
        with wp._lifecycle_lock(timeout_seconds=0.2):
            raise RuntimeError("boom")
    with wp._lifecycle_lock(timeout_seconds=0.2):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX flock from a child process")
def test_lock_contention_times_out_without_entering(lifecycle):
    import subprocess

    script = (
        "import fcntl, sys, time\n"
        f"h = open({str(lifecycle / 'livekit_worker.lock')!r}, 'a+')\n"
        "fcntl.flock(h.fileno(), fcntl.LOCK_EX)\n"
        "print('locked', flush=True)\n"
        "time.sleep(3)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with pytest.raises(wp.WorkerCoordinationError):
            with wp._lifecycle_lock(timeout_seconds=0.2):
                raise AssertionError("must not enter the critical section")
        assert time.monotonic() - started < 5.0
    finally:
        holder.wait(timeout=10)


def test_parse_stat_start_ticks_extracts_starttime_field():
    fields = [
        "S", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "424242", "999",
    ]
    stat_line = "1234 (python -m api.services.livekit.worker start) " + " ".join(fields) + "\n"
    assert wp._parse_stat_start_ticks(stat_line) == 424242
    assert wp._parse_stat_start_ticks("") is None
    assert wp._parse_stat_start_ticks("1234 (python) S 1 2") is None


def test_is_worker_argv_requires_exact_worker_command():
    assert wp._is_worker_argv(
        ["/usr/local/bin/python3", "-m", "api.services.livekit.worker", "start"]
    ) is True
    assert wp._is_worker_argv(
        ["/usr/local/bin/python3", "-m", "api.services.livekit.worker", "start", "--dev"]
    ) is False
    assert wp._is_worker_argv(
        ["/usr/bin/node", "-m", "api.services.livekit.worker", "start"]
    ) is False
    assert wp._is_worker_argv(None) is False


@pytest.mark.skipif(not os.path.exists("/proc/self/cmdline"), reason="requires Linux /proc")
def test_proc_argv_and_start_ticks_read_real_process():
    argv = wp._proc_argv(os.getpid())
    assert argv is not None and len(argv) >= 1
    ticks = wp._proc_start_ticks(os.getpid())
    assert isinstance(ticks, int) and ticks > 0


def _health_for(monkeypatch, *, state, running, message=None):
    import importlib

    # The package exports a `system_monitor` singleton that shadows the module
    # attribute, so resolve the module explicitly.
    sm = importlib.import_module("api.services.health.system_monitor")

    monkeypatch.setattr(sm, "livekit_configured", lambda: True)
    monkeypatch.setattr(
        sm,
        "get_worker_status",
        lambda: wp.LiveKitWorkerStatus(
            managed_by_api=True,
            running=running,
            pid=4242,
            message=message,
            state=state,
        ),
    )
    return asyncio.run(sm.SystemMonitor().check_livekit())


def test_health_consumer_maps_lifecycle_states(monkeypatch):
    from api.services.health.models import HealthStatus

    managed = _health_for(monkeypatch, state=wp.WORKER_STATE_MANAGED_ALIVE, running=True)
    assert managed.status == HealthStatus.UNVERIFIED
    assert "not verified" in managed.message
    assert managed.details["registration_verified"] is None

    surviving = _health_for(monkeypatch, state=wp.WORKER_STATE_SURVIVING_VERIFIED, running=True)
    assert surviving.status == HealthStatus.UNVERIFIED
    assert "surviving" in surviving.message.lower()
    assert "stopped" not in surviving.message.lower()

    absent = _health_for(monkeypatch, state=wp.WORKER_STATE_ABSENT, running=False)
    assert absent.status == HealthStatus.DEGRADED
    assert "not running" in absent.message

    uncertain = _health_for(
        monkeypatch,
        state=wp.WORKER_STATE_OWNERSHIP_UNCERTAIN,
        running=False,
        message="worker_configuration_changed",
    )
    assert uncertain.status == HealthStatus.DEGRADED
    assert "ownership uncertain" in uncertain.message.lower()
    assert "worker_configuration_changed" in uncertain.message

    unhealthy = _health_for(
        monkeypatch,
        state=wp.WORKER_STATE_UNHEALTHY,
        running=False,
        message="worker_pid_reused",
    )
    assert unhealthy.status == HealthStatus.DEGRADED
    assert "identity" in unhealthy.message.lower()


def test_record_write_does_not_follow_symlink(lifecycle):
    victim = lifecycle / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    link = lifecycle / "livekit_worker.json"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available in this environment")
    wp._write_record(4242, 555, "sig-1")
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert link.is_symlink()
    assert wp._read_record() is None


def test_lock_refuses_symlinked_lifecycle_path(lifecycle):
    target = lifecycle / "target"
    target.mkdir()
    link = lifecycle / "livekit_worker.lock"
    try:
        link.symlink_to(target / "lock")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available in this environment")
    with pytest.raises(wp.WorkerCoordinationError):
        with wp._lifecycle_lock(timeout_seconds=0.1):
            raise AssertionError("must not enter the critical section")


def _fail_lock_open(monkeypatch):
    import builtins

    real_open = builtins.open

    def _open(path, *args, **kwargs):
        if str(path).endswith("livekit_worker.lock"):
            raise OSError("simulated lock open failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)


def test_lock_open_failure_raises_coordination_error(lifecycle, monkeypatch):
    _fail_lock_open(monkeypatch)
    with pytest.raises(wp.WorkerCoordinationError):
        with wp._lifecycle_lock(timeout_seconds=0.1):
            raise AssertionError("must not enter the critical section")


def test_lock_open_failure_fails_closed_without_spawn(lifecycle, monkeypatch):
    monkeypatch.setattr(wp, "_find_worker_processes", lambda: [])
    popen = Mock()
    monkeypatch.setattr(wp.subprocess, "Popen", popen)
    _fail_lock_open(monkeypatch)
    status = wp.apply_livekit_worker_settings(_settings("sig-1"))
    assert status.state == wp.WORKER_STATE_OWNERSHIP_UNCERTAIN
    assert status.message == "coordination_failed_unsafe_lifecycle_path"
    assert status.running is False
    popen.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX flock")
def test_concurrent_startup_is_serialized(lifecycle):
    import subprocess

    marker = lifecycle / "entered.log"
    script = (
        "import fcntl, sys, time\n"
        f"lock = {str(lifecycle / 'livekit_worker.lock')!r}\n"
        f"marker = {str(marker)!r}\n"
        "h = open(lock, 'a+')\n"
        "fcntl.flock(h.fileno(), fcntl.LOCK_EX)\n"
        "open(marker, 'a').write('enter\\n')\n"
        "time.sleep(0.4)\n"
        "open(marker, 'a').write('exit\\n')\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(2)]
    for proc in procs:
        assert proc.wait(timeout=20) == 0
    events = marker.read_text(encoding="utf-8").split()
    assert events == ["enter", "exit", "enter", "exit"]
