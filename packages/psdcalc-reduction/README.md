# PSDCalc Reduction

Standalone `psdcalc.reduction` package for replayable PyPSA network reduction.

The package includes strict Pydantic configuration, source-ID sidecars,
custom busmaps, PyPSA quotient construction, complete structural maps, and a
structural Gate. Phase 1.5 adds selective voltage-aware topology simplification,
voltage-layer energy planning, a pure in-memory multi-voltage quotient Builder,
and a persisted Pipeline with NetCDF reload and a final structural Gate.

Install the locked development environment with `uv sync --locked` from this
directory. Run the package tests with `uv run --locked pytest tests`.

Run the PR-013 distribution Gate from the package directory with:

```bash
.venv/bin/python tools/packaging_gate.py --output-dir /tmp/psdcalc-pr013
```

This runs the package tests, builds an isolated sdist and wheel with
`setuptools>=70`, checks archive members and resources, installs the wheel with
binary dependencies in a clean venv, and exercises the installed CLI from
outside the checkout. It generates `test_summary.json` in the output directory
alongside the distributions. The summary is build evidence, not a source
fixture. Dependency installation may use the package index; the tests do not
access the internet.

The CLI commands are `validate-config`, `inspect`, `plan`, `build`, `report`, and
`dump-schema`. For example:

```bash
uv run --locked psdcalc-reduction validate-config examples/reduction.first-batch.yaml
# Set input.path to an existing local PyPSA NetCDF or CSV artifact before build.
uv run --locked psdcalc-reduction build examples/reduction.first-batch.yaml
uv run --locked psdcalc-reduction report examples/runs/small-pypsa-kmeans
```

For the three-voltage structural pipeline, use
`examples/reduction.three-voltage.yaml` after setting `input.path` to a local
PyPSA network. `plan` reports requested, planned, and actual cluster counts.
`build` saves `levels/L1.nc` and `levels/L2.nc` as mandatory audit artifacts,
even when `output.save_all_levels` is false. That flag is reserved for optional
hierarchy levels. The run also saves `resolved_config.yaml` and its SHA-256,
authoritative sidecars, complete bus/branch/device/control maps, line and
Transformer map projections, operation records, preliminary Builder evidence,
post-reload final Gate evidence, and a compression opportunity report. The
final Gate reads the persisted maps and sidecar back before validation.
`report` reads the persisted evidence.

The topology stage retains Transformer interfaces and source voltage levels.
It collapses only eligible single-interface feeders and zero-injection series
chains; it records retained candidate reasons, voltage mappings, roles, moved
devices, absorbed branches, and one record per change. `lift_to_target_voltage`
is recognized but fails explicitly until a planning approximation is
implemented. The multi-voltage path retains Transformer terminals as
singletons, clusters within exact or explicitly aliased voltage layers, and
preserves Transformer identity and parameters through export and reload.

The legacy manifest Gate reports `STRUCTURALLY_VALID` or
`STRUCTURALLY_INVALID`; a standalone topology operation can additionally
report `STRUCTURALLY_SIMPLIFIED`. The multi-voltage Pipeline reports only
`MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`,
`MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID`, or `FAILED`. These are structural
statuses; they do not certify AC power-flow convergence, reactive-power or OPF
equivalence, or full-grid physical acceptance.
