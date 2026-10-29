# PSDCalc Reduction

Standalone `psdcalc.reduction` package for replayable PyPSA network reduction.

PR-001 establishes package metadata, a locked dependency set, domain errors,
architecture decisions, and a single compatibility boundary for PyPSA's public
spatial clustering accessor. Planning, materialization, mappings, CLI and
validation are delivered in later first-batch increments.

Install the locked development environment with `uv sync --locked` from this
directory. Run the package tests with `uv run --locked pytest tests`.

The first batch can report structural validity only. It does not certify
power-flow convergence or full-grid physical acceptance.
