# PSDCalc Reduction

Standalone `psdcalc.reduction` package for replayable PyPSA network reduction.

The first phase includes strict Pydantic configuration, source-ID sidecars,
custom busmaps, PyPSA quotient construction, complete structural maps, and a
structural Gate. It also includes selective voltage-aware topology
simplification before energy K-means, HAC, or greedy modularity planning.

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

The topology stage retains Transformer interfaces and source voltage levels.
It collapses only eligible single-interface feeders and zero-injection series
chains; it records retained candidate reasons, voltage mappings, roles, moved
devices, absorbed branches, and one record per change. `lift_to_target_voltage`
is recognized but fails explicitly until a planning approximation is
implemented. Quotient construction with Transformer or multiple voltage levels
also fails explicitly.

The manifest Gate reports only `STRUCTURALLY_VALID` or
`STRUCTURALLY_INVALID`; a topology operation can additionally report
`STRUCTURALLY_SIMPLIFIED`. No result certifies AC power-flow convergence,
reactive-power or OPF equivalence, or full-grid physical acceptance.
