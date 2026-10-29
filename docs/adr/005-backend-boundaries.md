# ADR-005: Backend boundaries

Status: Accepted for the first batch, 2026-10-29.

## Decision

Publish `psdcalc.reduction` as an independent project under `packages/psdcalc-reduction/`. Its first implementation uses PyPSA. PyPSA-Earth is a workflow reference; VeraGrid Ward/PTDF/Di-Shi are future backends. Do not edit their source trees. A backend protocol will define the shared boundary without forcing unimplemented backends into the first batch.

## Consequences

The package can evolve separately from VeraGrid's distribution. Multi-voltage and transformer networks require an explicit capability gate; no silent voltage flattening or cross-voltage clustering is permitted. The first-batch build rejects any network with Transformer components because the inspected PyPSA quotient path does not carry them forward.
