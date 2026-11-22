# CHECKPOINT P1-01 ITERATION 5 (IN PROGRESS)

## Applied
- Per-recipient blocked branch: 4 ops fully isolated (slot/requeue/pause/event) with accurate flags slot_released/row_requeued/pause_persisted in details; critical local log when event append fails; always returns None.
- Batch blocked branch: pause_persisted tracked; message never claims paused unless persisted; critical log on event failure; stale-sweep described as scheduled worker-dependent.
- Acceptance test file REBUILT: construction (5), redaction (2), resume events (3), race retained-mocks, fault-isolation (4), requeue strengthened (D), stale-recovery DB-backed, real-auth via P1_TEST_REDIS_URL + urllib.parse + 127.0.0.1:6381 guard (no literals).
- Evidence logs started: p1-01-iter5-orderA.log / orderB.log (contain collection error - see below).

## BLOCKER (current)
test_circuit_breaker_p1_acceptance.py line 326: IndentationError - the fault-isolation class insert at line 236 landed mid-method (race test tail). File needs a one-line splice fix: re-read lines 230-340, re-indent the fault-isolation class as top-level, then rerun. Logs A/B preserved with the collection error as evidence.

## Next single action
Fix line 326 splice; py_compile; rerun Order A, Order B, T4-08 standalone, real-auth standalone (P1_TEST_REDIS_URL set) - preserve 4 logs.

## Status: NOT READY (collection error, not test failures).
