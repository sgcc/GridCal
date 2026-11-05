# Retained Fidelity V1: freeze revision 2

Status: **FROZEN_NOT_EXECUTED**. Revision 2 retains the original scientific question, source identity, parser manifest, policy order (`BACKBONE_500`, `BACKBONE_220`, `BACKBONE_100`), operating point, PR-022 metric policy, and frozen threshold artifact. It binds a repaired reporting and experiment driver for a future, separate Attempt 004.

The authoritative machine-readable contract is `freeze-revision-2.json` (SHA-256 `25394674ba28e7de013bd3cc57da080155443109b77ec7f9eba7c8d57a8d2b04`). It retains the original freeze content and adds explicit revision-2 fields. The original freeze file and Attempts 001–003 were not edited.

## Implementation change and identity

- Original implementation: commit `30d8c3e2cb75732a8e2fe151d1fd50ea3319344c`; retained as historical provenance only.
- Typed report exporter correction: commit `c9021d1fbbd5926e78c7ef3810e52f01d1279a8d`, tree `a20ba19833e74d5b054da3d977943f8ec6d61fc7`.
- Integrated driver and synthetic Gate: commit `e1f075d9923b6ed530603eb2676eb73d1c35131a`, tree `eb9cb40f8d79bc95daccca1f4a893bcda869b6ef`.
- Package remains local development version `0.8.0`. These commits are not a replacement 0.8.0 release.

The exporter now reads declared field types correctly. The experiment tools save and reload PF results, observations, timing, per-policy comprehensive reports, and cross-policy comparison; they independently replay durable evidence without another PF solve. The Attempt 004 launcher binds all three driver file hashes and the locked runtime versions. PF executor parameters, reduction algorithms, MappingBundle semantics, PR-022 metrics and thresholds, admission, and retained-network metric definitions were not changed. Therefore revision 2 cannot truthfully cite the original implementation commit as the executed implementation; it must cite the integrated commit and tree above.

## Gate and execution boundary

The synthetic full E2E preflight is `PASS`; its evidence is `report-repair-full-e2e-preflight.json` (SHA-256 `9889d6946219e67e4a3fd673391f05fbc993fcd4318b14204478f7099d73a8a3`) and the accompanying coverage matrix. It includes actual synthetic PyPSA PF, 1 source plus 3 candidate solves, fresh construction for each policy, report exports, new-process replay, tamper tests, and explicit PF/Fidelity state separation. Two synthetic policies were outside frozen tolerance; the preflight does not convert those outcomes to acceptance.

Attempt 004 must create a new private output directory and stop at the first failed policy; all later policies remain `NOT_RUN`. It may never read a historical candidate. The old Attempt 001 failure, Attempt 002 preflight failure, and Attempt 003 stop remain distinct, immutable evidence.

No Beijing PF, Stage2D experiment, projection-fidelity acceptance, or physical-equivalence claim is part of this freeze revision.
