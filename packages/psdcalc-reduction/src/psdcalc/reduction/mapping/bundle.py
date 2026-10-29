"""Complete first-batch quotient mapping bundle."""

from dataclasses import dataclass
from collections.abc import Iterable
import json
import os
from pathlib import Path
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.materializers.quotient import QuotientResult
from psdcalc.reduction.planners.base import PartitionPlan


_DEVICE_COMPONENTS = frozenset({
    "Generator", "Load", "StorageUnit", "Store", "ShuntImpedance",
})


@dataclass(frozen=True)
class MappingBundle:
    """Bus, branch, device, and control ownership maps for one quotient."""

    bus_map: tuple[MapEntry, ...]
    branch_map: tuple[MapEntry, ...]
    device_map: tuple[MapEntry, ...]
    control_map: tuple[MapEntry, ...] = ()
    region_map: tuple[MapEntry, ...] = ()
    boundary_map: tuple[MapEntry, ...] = ()
    equivalent_component_map: tuple[MapEntry, ...] = ()

    @property
    def line_map(self) -> tuple[MapEntry, ...]:
        """Return the authoritative Line rows from the branch map."""
        return tuple(row for row in self.branch_map
                     if row.source_id.component == "Line")

    @property
    def transformer_map(self) -> tuple[MapEntry, ...]:
        """Return the authoritative Transformer rows from the branch map."""
        return tuple(row for row in self.branch_map
                     if row.source_id.component == "Transformer")

    @classmethod
    def from_quotient(
        cls,
        original: pypsa.Network,
        sidecar: IdentitySidecar,
        partition: PartitionPlan,
        quotient: QuotientResult,
        *,
        operation_id: str,
    ) -> "MappingBundle":
        """Trace every source bus, line, and supported device to the quotient."""
        bus_rows = tuple(MapEntry(source, target,
                                  "preserved" if source == target else "clustered",
                                  operation_id)
                         for source, target in sorted(partition.stable_busmap.items()))
        line_rows: list[MapEntry] = []
        for component_name, source_frame in (("Line", original.lines),
                                             ("Link", original.links)):
            target_frame = (quotient.network.lines if component_name == "Line"
                            else quotient.network.links)
            for name, line in source_frame.iterrows():
                source = sidecar.source_id(component_name, str(name))
                target_bus0 = partition.backend_busmap[str(line.bus0)]
                target_bus1 = partition.backend_busmap[str(line.bus1)]
                if target_bus0 == target_bus1:
                    if str(name) in target_frame.index:
                        raise MappingError(f"Internal branch unexpectedly survived: {source.encode()}")
                    line_rows.append(MapEntry(source, None, "absorbed_internal",
                                              operation_id, reason="both terminals in one cluster"))
                    continue
                target_name = (quotient.linemap.get(str(name)) if component_name == "Line"
                               else str(name))
                if target_name is None or target_name not in target_frame.index:
                    raise MappingError(f"Missing quotient branch for {source.encode()}")
                target = StableId(source.namespace, component_name,
                                  f"{operation_id}:{target_name}")
                line_rows.append(MapEntry(source, target, "aggregated", operation_id))
        device_rows: list[MapEntry] = []
        target_components = {c.name: c.static for c in quotient.network.components}
        for component in original.components:
            if component.name not in _DEVICE_COMPONENTS:
                continue
            target_frame = target_components.get(component.name)
            for name, row in component.static.iterrows():
                source = sidecar.source_id(component.name, str(name))
                if target_frame is not None and str(name) in target_frame.index:
                    device_rows.append(MapEntry(source, source, "preserved", operation_id))
                    continue
                if target_frame is None or "bus" not in row:
                    raise MappingError(f"Missing quotient device: {source.encode()}")
                target_bus = partition.backend_busmap[str(row.bus)]
                candidates = target_frame[target_frame.bus == target_bus]
                if "carrier" in row and "carrier" in candidates:
                    candidates = candidates[candidates.carrier == row.carrier]
                if len(candidates) != 1:
                    raise MappingError(f"Ambiguous quotient device ownership: {source.encode()}")
                target_name = str(candidates.index[0])
                target = StableId(source.namespace, source.component,
                                  f"{operation_id}:{target_name}")
                device_rows.append(MapEntry(source, target, "aggregated", operation_id))
        device_by_source = {row.source_id: row for row in device_rows}
        control_rows: list[MapEntry] = []
        from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
        for item in PypsaAdapter().inventory(original, sidecar).items:
            if not item.controllable:
                continue
            owner = device_by_source.get(item.source_id)
            if owner is None or owner.target_id is None:
                raise MappingError(f"Control ownership is missing: {item.source_id.encode()}")
            control_rows.append(MapEntry(item.source_id, owner.target_id,
                                         owner.mapping_type, operation_id))
        return cls(bus_rows, tuple(line_rows), tuple(device_rows), tuple(control_rows))

    def save_parquet(self, directory: str | Path) -> dict[str, Path]:
        """Write each nonempty first-batch map to Parquet with stable ID columns."""
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        output: dict[str, Path] = {}
        for name in ("bus_map", "branch_map", "device_map", "control_map"):
            rows: tuple[MapEntry, ...] = getattr(self, name)
            if not rows:
                continue
            target = root / f"{name}.parquet"
            table = pa.Table.from_pylist([row.to_row() for row in rows])
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=root, prefix=f".{name}.",
                                                 suffix=".parquet", delete=False) as stream:
                    temporary = Path(stream.name)
                pq.write_table(table, temporary)
                os.replace(temporary, target)
            except (OSError, pa.ArrowException) as exc:
                raise MappingError(f"Cannot write mapping {target}: {exc}") from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            output[name] = target
        return output

    @classmethod
    def load_parquet(
        cls, directory: str | Path, *,
        expected_files: Iterable[str] | None = None,
    ) -> "MappingBundle":
        """Read persisted maps and verify branch projections when present."""
        root = Path(directory)
        names = ("bus_map", "branch_map", "device_map", "control_map")
        expected = set(expected_files) if expected_files is not None else None
        if expected is not None and expected - set(names) - {
                "line_map", "transformer_map"}:
            raise MappingError("Unknown expected map file")

        def read(name: str) -> tuple[MapEntry, ...]:
            path = root / f"{name}.parquet"
            if not path.exists():
                if (name == "bus_map" or
                        expected is not None and name in expected):
                    raise MappingError(f"Missing persisted map: {name}")
                return ()
            try:
                rows = tuple(MapEntry.from_row(row) for row in
                             pq.read_table(path).to_pylist())
            except (OSError, pa.ArrowException, ValueError, TypeError,
                    MappingError) as exc:
                raise MappingError(f"Cannot read persisted map {path}: {exc}") from exc
            if len({row.source_id for row in rows}) != len(rows):
                raise MappingError(f"Duplicate source StableId in {name}")
            return rows

        bundle = cls(*(read(name) for name in names))
        for name, actual in (("line_map", bundle.line_map),
                             ("transformer_map", bundle.transformer_map)):
            projected = read(name)
            if projected or root.joinpath(f"{name}.parquet").exists() or (
                    expected is not None and name in expected):
                if tuple(sorted((json.dumps(row.to_row(), sort_keys=True)
                                 for row in projected))) != tuple(sorted(
                        json.dumps(row.to_row(), sort_keys=True)
                        for row in actual)):
                    raise MappingError(f"{name} differs from branch_map")
        return bundle

    def canonical_rows(self) -> dict[str, tuple[str, ...]]:
        """Return order-independent complete map content for readback checks."""
        return {
            name: tuple(sorted(json.dumps(row.to_row(), sort_keys=True)
                               for row in getattr(self, name)))
            for name in ("bus_map", "branch_map", "device_map", "control_map")
        }
