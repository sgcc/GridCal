# ADR-007: Selective voltage-aware topology simplification

Status: accepted for the first phase (2026-10-29).

The topology stage builds a separate line graph for each `v_nom`. Transformer
terminals are fixed interfaces; no ordinary cluster may cross their voltage
boundary. Cycle edges are marked by edge-removal reachability, alongside
2-core, bridge, and articulation roles. The stage retains every cycle edge.

It may absorb a plain static radial feeder only when it has one anchor and no
control, protected component, cross-region boundary, or complex line model.
It may replace a zero-injection, degree-two series corridor between two
anchors with one line whose series impedances and lengths are summed and whose
nominal capacity is the minimum of its sources. Every source bus, branch, and
device keeps an external stable-ID mapping. A rejected candidate receives a
reason in the topology role evidence.

`keep_transformer_interface` is the implemented default. The
`lift_to_target_voltage` mode is recognized in schema but rejected at runtime;
only a future planning approximation may implement it, with
`PLANNING_APPROXIMATION` status. The topology operation may report
`STRUCTURALLY_SIMPLIFIED` after its structural Gate. These states never imply
AC power-flow, voltage/reactive-power, or OPF equivalence.

The PyPSA quotient capability gate still rejects Transformer and multi-voltage
inputs. Energy planners run only after this topology stage and protect retained
cycle endpoints as singleton buses. This deliberately limits achievable
cluster counts.
