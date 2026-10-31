"""Final persisted VoltageCutoffPolicy structural gates."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import VoltagePolicyConfig
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.policies.voltage_cutoff import (
    VoltageCutoffPolicy, voltage_policy_evidence,
)
from psdcalc.reduction.validation.multi_voltage_final import (
    MultiVoltageFinalValidator,
    _resolved_policy_evidence_matches,
    _retained_bus_mappings_are_identity,
    _retained_line_mappings_are_identity,
)

from test_three_voltage_pipeline import pipeline_config, three_voltage_source


_POLICY_GATES = {
    "voltage_policy_resolved",
    "voltage_policy_singleton_buses",
    "voltage_policy_line_identity",
    "voltage_policy_voltage_membership",
    "voltage_policy_round_trip",
}


def _source_and_config(tmp_path: Path, *, enabled: bool) -> tuple[object, object]:
    """Create a fresh in-memory source and its explicit policy configuration."""
    network = three_voltage_source()
    input_path = tmp_path / "source.nc"
    network.export_to_netcdf(input_path)
    config = pipeline_config(input_path, run_id="voltage-policy-final")
    if enabled:
        config = config.model_copy(update={
            "voltage_policy": VoltagePolicyConfig(
            enabled=enabled, mode="freeze_at_or_above",
                cutoff_kv=110.0,
            ),
        })
    return network, config


def test_base_policy_is_persisted_and_all_final_gates_pass(tmp_path: Path) -> None:
    """An omitted policy resolves to BASE and survives clean final reload."""
    network, config = _source_and_config(tmp_path, enabled=False)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    store = ArtifactStore(tmp_path / "runs", config.run.id)

    result = MultiVoltageQuotientPipeline().run(
        original_network=network, source_sidecar=sidecar,
        config=config, store=store,
    )

    assert _POLICY_GATES <= result.structural.checks.keys()
    assert all(result.structural.checks[name] for name in _POLICY_GATES)
    assert result.structural.checks["input_semantic_immutability"] is True
    evidence_path = store.run_dir / "voltage_policy/resolved_policy.json"
    assert evidence_path.exists()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["request"] == {
        "enabled": False,
        "mode": "freeze_at_or_above",
        "cutoff_kv": None,
    }
    assert evidence["resolved"]["policy_id"] == "BASE"
    assert result.manifest.artifacts["voltage_policy_evidence"] == (
        "voltage_policy/resolved_policy.json")


def test_cutoff_policy_final_gate_passes_after_clean_reload(tmp_path: Path) -> None:
    """Retained high-voltage Buses and Lines pass all five final policy gates."""
    network, config = _source_and_config(tmp_path, enabled=True)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    store = ArtifactStore(tmp_path / "runs", config.run.id)

    result = MultiVoltageQuotientPipeline().run(
        original_network=network, source_sidecar=sidecar,
        config=config, store=store,
    )

    assert result.structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    assert all(result.structural.checks[name] for name in _POLICY_GATES)
    assert result.structural.checks["input_semantic_immutability"] is True
    evidence = json.loads((store.run_dir / "voltage_policy/resolved_policy.json").read_text())
    assert evidence["resolved"]["retained_bus_count"] == 3
    assert evidence["resolved"]["retained_line_count"] == 3


def test_cutoff_gates_pass_but_parallel_cycle_gate_still_fails(tmp_path: Path) -> None:
    """Voltage policy success cannot waive the existing cycle corridor Gate."""
    network = three_voltage_source()
    for name in ("xz1", "z1z2", "z2y"):
        network.remove("Line", name)
    for name in ("mid1", "mid2"):
        network.remove("Load", name)
    for name in ("z1", "z2"):
        network.remove("Bus", name)
    network.add("Line", "parallel_low_1", bus0="x", bus1="y",
                r=0.01, x=0.1, s_nom=100)
    network.add("Line", "parallel_low_2", bus0="x", bus1="y",
                r=0.02, x=0.2, s_nom=100)
    input_path = tmp_path / "parallel-source.nc"
    network.export_to_netcdf(input_path)
    config = pipeline_config(input_path, run_id="parallel-cycle-cutoff")
    config = config.model_copy(update={
        "partition": config.partition.model_copy(update={
            "target": config.partition.target.model_copy(update={
                "n_clusters": 6,
                "per_layer": {"10": 1, "35": 2, "110": 3},
            }),
        }),
        "voltage_policy": VoltagePolicyConfig(
            enabled=True, mode="freeze_at_or_above", cutoff_kv=110.0),
    })
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    store = ArtifactStore(tmp_path / "runs", config.run.id)

    result = MultiVoltageQuotientPipeline().run(
        original_network=network, source_sidecar=sidecar,
        config=config, store=store,
    )

    assert all(result.structural.checks[name] for name in _POLICY_GATES)
    assert result.structural.checks["cycle_corridors"] is False
    assert result.structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"


class _TamperedPolicyStore(ArtifactStore):
    """Corrupt persisted policy evidence before the pipeline clean-read phase."""

    def write_json(self, relative: str, value: object) -> Path:
        path = super().write_json(relative, value)
        if relative == "voltage_policy/resolved_policy.json":
            corrupted = json.loads(path.read_text(encoding="utf-8"))
            corrupted["resolved"]["retained_bus_ids"] = []
            path.write_text(json.dumps(corrupted), encoding="utf-8")
        return path


def test_persisted_policy_evidence_tamper_fails_closed(tmp_path: Path) -> None:
    """A corrupted artifact is reloaded and cannot be rescued by memory state."""
    network, config = _source_and_config(tmp_path, enabled=True)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    store = _TamperedPolicyStore(tmp_path / "runs", config.run.id)

    result = MultiVoltageQuotientPipeline().run(
        original_network=network, source_sidecar=sidecar,
        config=config, store=store,
    )

    assert not result.structural.checks["voltage_policy_resolved"]
    assert not result.structural.checks["voltage_policy_round_trip"]
    assert result.structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"


def test_policy_fingerprint_is_recomputed_against_source(tmp_path: Path) -> None:
    """Persisted fingerprints are recomputed, not compared with themselves."""
    network = three_voltage_source()
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    policy = VoltageCutoffPolicy(True, cutoff_kv=110.0)
    evidence = voltage_policy_evidence(policy, policy.resolve(network, sidecar))
    evidence["resolved"]["resolved_policy_fingerprint"] = "0" * 64

    assert not _resolved_policy_evidence_matches(policy, network, sidecar, evidence)


def test_tampered_policy_contract_fields_fail_evidence_match() -> None:
    """Each request, layer, and StableId evidence field is authoritative."""
    network = three_voltage_source()
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    policy = VoltageCutoffPolicy(True, cutoff_kv=110.0)
    evidence = voltage_policy_evidence(policy, policy.resolve(network, sidecar))
    mutations = (
        lambda item: item["request"].update(cutoff_kv=115.0),
        lambda item: item["resolved"].update(retained_voltage_layers=[35.0]),
        lambda item: item["resolved"].update(compressible_voltage_layers=[110.0]),
        lambda item: item["resolved"].update(retained_bus_ids=[]),
        lambda item: item["resolved"].update(retained_line_ids=[]),
        lambda item: item["resolved"].update(resolved_policy_fingerprint="bad"),
    )
    for mutate in mutations:
        tampered = json.loads(json.dumps(evidence))
        mutate(tampered)
        assert not _resolved_policy_evidence_matches(
            policy, network, sidecar, tampered)


def _identity_maps(network: object, sidecar: object) -> MappingBundle:
    """Build identity mappings for the programmatic three-voltage fixture."""
    bus_rows = tuple(
        MapEntry(sidecar.source_id("Bus", str(name)),
                 sidecar.source_id("Bus", str(name)), "preserved", "test")
        for name in network.buses.index
    )
    branch_rows = tuple(
        MapEntry(sidecar.source_id("Line", str(name)),
                 sidecar.source_id("Line", str(name)), "preserved", "test")
        for name in network.lines.index
    )
    return MappingBundle(bus_rows, branch_rows, ())


def test_retained_bus_mapping_tampering_fails_identity_gate() -> None:
    """Drop, retype, swap, collide, or remove a retained target Bus => fail."""
    network = three_voltage_source()
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    resolved = VoltageCutoffPolicy(True, cutoff_kv=110.0).resolve(network, sidecar)
    a = sidecar.source_id("Bus", "a")
    b = sidecar.source_id("Bus", "b")
    base = _identity_maps(network, sidecar)

    assert _retained_bus_mappings_are_identity(resolved, base, network, sidecar)
    dropped = replace(base, bus_map=tuple(r for r in base.bus_map if r.source_id != a))
    assert not _retained_bus_mappings_are_identity(resolved, dropped, network, sidecar)
    retyped = replace(base, bus_map=tuple(
        replace(r, mapping_type="clustered") if r.source_id == a else r
        for r in base.bus_map))
    assert not _retained_bus_mappings_are_identity(resolved, retyped, network, sidecar)
    swapped = replace(base, bus_map=tuple(
        replace(r, target_id=b) if r.source_id == a else r
        for r in base.bus_map))
    assert not _retained_bus_mappings_are_identity(resolved, swapped, network, sidecar)
    collision = replace(base, bus_map=tuple(
        replace(r, target_id=a) if r.source_id == sidecar.source_id("Bus", "x") else r
        for r in base.bus_map))
    assert not _retained_bus_mappings_are_identity(resolved, collision, network, sidecar)
    missing = network.copy()
    missing.remove("Bus", "a")
    assert not _retained_bus_mappings_are_identity(resolved, base, missing, sidecar)


def test_retained_line_mapping_and_semantic_tampering_fails_gate() -> None:
    """Retained Line mapping or any hashed electrical field change => fail."""
    network = three_voltage_source()
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    resolved = VoltageCutoffPolicy(True, cutoff_kv=110.0).resolve(network, sidecar)
    line_id = sidecar.source_id("Line", "ab")
    other_id = sidecar.source_id("Line", "bc")
    base = _identity_maps(network, sidecar)

    assert _retained_line_mappings_are_identity(
        resolved, network, sidecar, network.copy(), sidecar, base)
    dropped = replace(base, branch_map=tuple(r for r in base.branch_map
                                             if r.source_id != line_id))
    assert not _retained_line_mappings_are_identity(
        resolved, network, sidecar, network.copy(), sidecar, dropped)
    retyped = replace(base, branch_map=tuple(
        replace(r, mapping_type="aggregated") if r.source_id == line_id else r
        for r in base.branch_map))
    assert not _retained_line_mappings_are_identity(
        resolved, network, sidecar, network.copy(), sidecar, retyped)
    swapped = replace(base, branch_map=tuple(
        replace(r, target_id=other_id) if r.source_id == line_id else r
        for r in base.branch_map))
    assert not _retained_line_mappings_are_identity(
        resolved, network, sidecar, network.copy(), sidecar, swapped)
    missing = network.copy()
    missing.remove("Line", "ab")
    assert not _retained_line_mappings_are_identity(
        resolved, network, sidecar, missing, sidecar, base)
    changed = network.copy()
    changed.lines.at["ab", "x"] += 0.5
    assert not _retained_line_mappings_are_identity(
        resolved, network, sidecar, changed, sidecar, base)


@pytest.mark.parametrize("tamper,expected_gate", [
    ("retained_bus_mapping", "voltage_policy_singleton_buses"),
    ("retained_line_mapping", "voltage_policy_line_identity"),
])
def test_retained_mapping_tamper_makes_final_status_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    tamper: str, expected_gate: str,
) -> None:
    """A changed reloaded retained-object mapping fails final structural status."""
    network, config = _source_and_config(tmp_path, enabled=True)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    original_validate = MultiVoltageFinalValidator.validate

    def validate_with_tamper(self: MultiVoltageFinalValidator, *args: object,
                             **kwargs: object) -> object:
        mappings = kwargs["composed_mappings"]
        if tamper == "retained_bus_mapping":
            retained = sidecar.source_id("Bus", "a")
            kwargs["composed_mappings"] = replace(
                mappings,
                bus_map=tuple(row for row in mappings.bus_map
                              if row.source_id != retained),
            )
        else:
            retained = sidecar.source_id("Line", "ab")
            kwargs["composed_mappings"] = replace(
                mappings,
                branch_map=tuple(row for row in mappings.branch_map
                                 if row.source_id != retained),
            )
        return original_validate(self, *args, **kwargs)

    monkeypatch.setattr(MultiVoltageFinalValidator, "validate", validate_with_tamper)
    result = MultiVoltageQuotientPipeline().run(
        original_network=network, source_sidecar=sidecar, config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id),
    )

    assert result.structural.checks[expected_gate] is False
    assert result.structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"
