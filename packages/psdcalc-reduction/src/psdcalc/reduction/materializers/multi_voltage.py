"""Pure in-memory multi-voltage quotient Builder."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, MaterializationError, UnsupportedFeatureError,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.materializers.cross_layer import CrossLayerComponentReconnector
from psdcalc.reduction.materializers.layer_quotient import LayerQuotientMaterializer
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.parallel_corridors import ParallelCorridorAnalyzer
from psdcalc.reduction.topology.parallel_corridors import ProtectedParallelPlan
from psdcalc.reduction.topology.parallel_provenance import (
    ParallelSourceLedger, SourceProfileContract,
)
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier
from psdcalc.reduction.validation.multi_voltage import (
    AggregationInvariantSpec, MultiVoltageStructuralValidator,
    PreliminaryStructuralEvidence,
)
from psdcalc.reduction.validation.protected_parallel import (
    ProtectedParallelStructuralValidator,
)


@dataclass(frozen=True)
class MultiVoltageBuildResult:
    """Detached child, parent-to-target maps, records, and precheck evidence."""

    network: pypsa.Network
    target_sidecar: IdentitySidecar
    mappings: MappingBundle
    records: tuple[ReductionRecord, ...]
    preliminary: PreliminaryStructuralEvidence
    layer_cluster_counts: Mapping[str, tuple[int, int]]
    requested_cluster_count: int
    planned_cluster_count: int
    actual_cluster_count: int
    voltage_layer_map: Mapping[StableId, tuple[float, float]]
    parallel_plan: ProtectedParallelPlan | None = None
    source_ledger: ParallelSourceLedger | None = None


def _semantic_hash(network: pypsa.Network) -> str:
    """Hash deterministic in-memory component and snapshot content for records."""
    # MultiIndex.__repr__ can change after PyPSA's dense accessor runs even
    # when its values and level names remain identical. Hash those semantics.
    snapshots = network.snapshots
    chunks = [repr((tuple(snapshots.names), tuple(snapshots.tolist()))),
              repr(network.snapshot_weightings),
              repr(network.investment_periods),
              repr(network.investment_period_weightings)]
    for component in sorted(network.components, key=lambda item: item.name):
        chunks.append(component.name)
        static = component.static.sort_index()
        if component.name == "SubNetwork":
            # PyPSA's regenerated SubNetwork.obj contains process-local
            # pointers, not network semantics.
            static = static.drop(columns="obj", errors="ignore")
        chunks.append(static.to_json(
            orient="split", date_format="iso", default_handler=str
        ))
        for attribute, frame in sorted(component.dynamic.items()):
            if not frame.empty:
                chunks.append(attribute)
                chunks.append(frame.sort_index(axis=1).to_json(
                    orient="split", date_format="iso", default_handler=str
                ))
    return "memory:" + hashlib.sha256("\n".join(chunks).encode()).hexdigest()


class MultiVoltageQuotientBuilder:
    """Build a detached quotient without persistence or final run status."""

    def build(self, simplified_network: pypsa.Network,
              parent_sidecar: IdentitySidecar,
              partition: PartitionPlan,
              aggregation: AggregationConfig,
              protection: ProtectionSet,
              voltage_aliases: Mapping[float, float] | None = None,
              invariants: tuple[AggregationInvariantSpec, ...] = (),
              *, source_ledger: ParallelSourceLedger | None = None,
              ) -> MultiVoltageBuildResult:
        """Return a detached multi-voltage quotient and preliminary evidence."""
        adapter = PypsaAdapter()
        policy = aggregation.parallel_corridors.policy
        if policy == "preserve_all_members":
            raise UnsupportedFeatureError(
                "preserve_all_members is not implemented")
        if policy not in {"aggregate_corridor", "preserve_protected_members"}:
            raise UnsupportedFeatureError(f"Unsupported parallel policy: {policy}")
        source_ledger = source_ledger or ParallelSourceLedger(
            SourceProfileContract("programmatic_pypsa", "unavailable"))
        adapter.validate_model(simplified_network, parent_sidecar)
        if not simplified_network.shapes.empty:
            raise BackendCapabilityError("Nonempty Shape table is unsupported")
        supported = {"Bus", "Line", "Transformer", "Link", "Generator",
                     "Load", "StorageUnit", "Store", "ShuntImpedance",
                     "Carrier", "LineType", "TransformerType", "Shape",
                     "SubNetwork"}
        for component in simplified_network.components:
            if component.name not in supported and not component.static.empty:
                raise BackendCapabilityError(
                    f"Unsupported component table: {component.name}"
                )
        invalid_one_ports = set(aggregation.aggregate_one_ports) - set(
            simplified_network.one_port_components
        )
        if invalid_one_ports:
            raise BackendCapabilityError(
                "Unsupported one-port aggregation component: " +
                ", ".join(sorted(invalid_one_ports))
            )
        parent_hash = _semantic_hash(simplified_network)
        for name, row in simplified_network.links.iterrows():
            for column in simplified_network.links.columns:
                if str(column).startswith("bus") and str(column) not in {"bus0", "bus1"}:
                    value = row[column]
                    if str(value) not in {"", "nan"}:
                        raise BackendCapabilityError(
                            f"Multi-terminal Link is unsupported: {name}"
                        )
        layers = VoltageLayerClassifier().classify(
            simplified_network, parent_sidecar, aliases=voltage_aliases
        )
        voltage_layer_map = {
            source: (float(simplified_network.buses.loc[
                parent_sidecar.backend_name(source), "v_nom"]), layer.v_nom_kv)
            for layer in layers for source in layer.bus_ids
        }
        canonical_by_name = {
            parent_sidecar.backend_name(source): canonical
            for source, (_, canonical) in voltage_layer_map.items()
        }
        for name, row in simplified_network.links.iterrows():
            if canonical_by_name[str(row.bus0)] != canonical_by_name[str(row.bus1)]:
                raise BackendCapabilityError(
                    f"Cross-layer Link is unsupported: {name}"
                )
        parallel_plan = None
        identity_preserved_line_ids = frozenset(
            source_id for source_id in protection.preserved_components
            if source_id.component == "Line"
        )
        if policy == "preserve_protected_members":
            source_ledger.validate(
                simplified_network, parent_sidecar,
                voltage_aliases=voltage_aliases)
            parallel_plan = ParallelCorridorAnalyzer().analyze(
                simplified_network, parent_sidecar, protection,
                voltage_aliases=voltage_aliases,
                lplus_base_lines=frozenset(
                    item.base_line_id for item in source_ledger.l_plus))
            protection = ProtectionSet(
                protection.traced,
                protection.preserved_components | frozenset(
                    item.shunt_id for item in source_ledger.l_plus),
                protection.terminal_buses,
                protection.singleton_buses | parallel_plan.mandatory_singletons,
                protection.cannot_link,
            )
            identity_preserved_line_ids |= parallel_plan.protected_lines
            identity_preserved_line_ids |= frozenset(
                item.base_line_id for item in source_ledger.l_plus
            )
        unknown_identity_lines = identity_preserved_line_ids - {
            parent_sidecar.source_id("Line", str(name))
            for name in simplified_network.lines.index
        }
        if unknown_identity_lines:
            raise MaterializationError(
                "Identity-preserved Line is absent: " +
                ", ".join(sorted(item.encode() for item in unknown_identity_lines))
            )
        allowed_line_dynamic_names = frozenset(
            parent_sidecar.backend_name(line_id)
            for line_id in identity_preserved_line_ids
        )
        MultiVoltageStructuralValidator().validate_supported_dynamic(
            simplified_network, invariants,
            allowed_line_dynamic_names=allowed_line_dynamic_names)
        busmaps = MultiVoltageBusmapValidator().validate(
            simplified_network, parent_sidecar, partition, layers, protection
        )
        layer_results = LayerQuotientMaterializer().materialize(
            simplified_network, parent_sidecar, layers, busmaps,
            aggregation, protection, parallel_plan, source_ledger,
            identity_preserved_line_ids,
        )
        reconnected = CrossLayerComponentReconnector().reconnect(
            simplified_network, parent_sidecar, layer_results, busmaps
        )
        tx_rows = tuple(
            MapEntry(parent_sidecar.source_id("Transformer", str(name)),
                     parent_sidecar.source_id("Transformer", str(name)),
                     "preserved", "mvq-reconnect")
            for name in sorted(simplified_network.transformers.index)
        )
        mapping = MappingBundle(
            tuple(row for layer in layer_results for row in layer.mapping.bus_map),
            tuple(row for layer in layer_results for row in layer.mapping.branch_map)
            + tx_rows,
            tuple(row for layer in layer_results for row in layer.mapping.device_map),
            tuple(row for layer in layer_results for row in layer.mapping.control_map),
        )
        evidence = MultiVoltageStructuralValidator().precheck(
            simplified_network, reconnected.network, parent_sidecar,
            reconnected.target_sidecar, mapping, invariants, protection,
            voltage_layer_map=voltage_layer_map,
            allowed_line_dynamic_names=allowed_line_dynamic_names,
        )
        if parallel_plan is not None or identity_preserved_line_ids:
            validation_plan = parallel_plan or ProtectedParallelPlan(
                (), frozenset(), frozenset())
            parallel_checks, parallel_details = (
                ProtectedParallelStructuralValidator().check(
                    simplified_network, reconnected.network, parent_sidecar,
                    reconnected.target_sidecar, mapping, validation_plan,
                    busmaps.global_busmap, source_ledger=source_ledger,
                    protection=protection,
                    identity_preserved_line_ids=identity_preserved_line_ids))
            evidence = PreliminaryStructuralEvidence(
                "builder", False,
                {**evidence.checks, **parallel_checks},
                {**evidence.details, **parallel_details},
                policy,
            )
        else:
            evidence = PreliminaryStructuralEvidence(
                "builder", False, evidence.checks, evidence.details, policy)
        if _semantic_hash(simplified_network) != parent_hash:
            raise MaterializationError("Builder mutated simplified_network")
        output_hash = _semantic_hash(reconnected.network)
        records: list[ReductionRecord] = []
        for layer in layer_results:
            child_hash = _semantic_hash(layer.network)
            records.append(ReductionRecord(
                operation_id=f"mvq-layer-{layer.layer.key.replace('.', '_')}",
                operation_type="aggregation",
                parent_grid_hash=parent_hash,
                child_grid_hash=child_hash,
                input_signature=parent_hash,
                output_signature=child_hash,
                parameters={"voltage_layer": layer.layer.key,
                            "requested_cluster_count": None,
                            "planned_cluster_count":
                                busmaps.per_layer_counts[layer.layer.key][0],
                            "actual_cluster_count":
                                busmaps.per_layer_counts[layer.layer.key][1],
                            "parallel_corridor_policy": policy},
            ))
        records.append(ReductionRecord(
            operation_id="mvq-reconnect", operation_type="aggregation",
            parent_grid_hash=parent_hash, child_grid_hash=output_hash,
            input_signature=parent_hash, output_signature=output_hash,
            parameters={"transformer_count": len(tx_rows),
                        "parallel_corridor_policy": policy,
                        "protected_parallel_line_count": (
                            len(parallel_plan.protected_corridor_lines)
                            if parallel_plan is not None else 0),
                        "requested_cluster_count": busmaps.requested_cluster_count,
                        "planned_cluster_count": busmaps.planned_cluster_count,
                        "actual_cluster_count": busmaps.actual_cluster_count},
        ))
        return MultiVoltageBuildResult(
            reconnected.network, reconnected.target_sidecar, mapping,
            tuple(records), evidence, busmaps.per_layer_counts,
            busmaps.requested_cluster_count, busmaps.planned_cluster_count,
            busmaps.actual_cluster_count,
            voltage_layer_map,
            parallel_plan, source_ledger if parallel_plan is not None else None,
        )
