# Retained Fidelity V1 — Attempt 003 preflight STOP

**Status: `STOPPED_BEFORE_REAL_CASE_EXECUTION`.** The focused synthetic driver
suite passed (`7 passed / 0 failed`) on Python 3.11.15 and PyPSA 1.2.4. It
exercised fresh candidate and mapping reconstruction, an actual synthetic AC
PF, durable result readback, and fail-closed behavior. The full frozen
**execution contract** did not pass preflight.

The original freeze and Attempt 003 lease matched their committed file hashes.
The driver and launcher matched the leased byte hashes. The package's frozen
implementation source remained unchanged. The prior full-package result is
`688 passed / 0 failed`; it was not rerun for this STOP.

## Blocking contract gaps

1. **Comprehensive report export is not wired to the frozen execution path.**
   The driver and launcher do not call comprehensive report construction or
   JSON/Markdown/CSV export. The package pipeline creates its existing
   structural and opportunity reports, which do not satisfy the per-policy
   comprehensive and cross-policy report requirement.
2. **Numeric observation and full timing evidence are not wired.** The driver
   and launcher do not produce the versioned numeric observation companion or
   the required solver-only and end-to-end stage timings. The package pipeline
   records some structural-stage durations, which are insufficient for the
   frozen timing contract.

The precise frozen source and lease identities, driver hashes, and failure
classification are in `attempt-003-preflight-stop.json`. Supplying new runtime
reporting code during the unique formal attempt would change the leased driver
after preflight. This run therefore stops before accessing the Beijing model
or invoking Beijing PF. No Attempt 003 private run directory was created.

| Stage | State |
| --- | --- |
| Synthetic focused tests | `7 passed / 0 failed` |
| Complete frozen driver preflight | `FAIL` |
| Parser manifest and real source identity | `NOT_RUN` |
| BACKBONE_500 / BACKBONE_220 / BACKBONE_100 | `NOT_RUN` / `NOT_RUN` / `NOT_RUN` |
| Beijing source PF / candidate PF / fidelity | `NOT_RUN` / `NOT_RUN` / `NOT_RUN` |
| Adaptive Controller / Stage2D | `NOT_RUN` / `NOT_RUN` |

Attempt 001 remains `FAILED_EVIDENCE_SERIALIZATION`; Attempt 002 remains
`FAILED_PREFLIGHT_CONTRACT_CONFLICT`. Their STOP evidence and the original
freeze were not changed. No result from this STOP establishes projection
fidelity or physical equivalence.

**Next step:** a separate driver/reporting remediation and new execution lease.
Do not resume Attempt 003 by changing its frozen driver or rerunning it.
