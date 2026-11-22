"""Gate 2 Correction A — worker log-site redaction and dead-regex removal.

Source-level regression checks that the recording/startup log sites in
worker.py route through _safe_finalize_error and that the previously dead
credential-regex declarations are gone.
"""


def test_worker_recording_log_sites_use_safe_finalize_error_and_dead_regexes_removed():
    import inspect

    from api.services.livekit import worker

    source = inspect.getsource(worker)
    for fragment in (
        'failed to persist startup failure: "',
        "recording start skipped: {_safe_finalize_error",
        'failed to persist recording start: "',
    ):
        assert fragment in source
    assert "_AUTHORIZATION_PATTERN" not in source
    assert "_BEARER_PATTERN" not in source
    assert "_SECRET_VALUE_PATTERN" not in source
    assert "_DSN_CREDENTIALS_PATTERN" not in source
