"""Bus-indexed load, generation, and renewable schedule features."""

from collections.abc import Sequence

import pandas as pd
import pypsa

from psdcalc.reduction.exceptions import FeatureError


_SUPPORTED = frozenset({"geographic_xy", "load_generation", "renewable_time"})
_RENEWABLE_MARKERS = ("wind", "solar", "hydro", "renewable")


class EnergyFeatureBuilder:
    """Build deterministic bus-level features from a simplified network."""

    def build(self, network: pypsa.Network,
              builders: Sequence[str]) -> pd.DataFrame:
        """Return raw feature columns or raise FeatureError for bad input."""
        names = tuple(builders)
        unknown = set(names) - _SUPPORTED
        if unknown:
            raise FeatureError(f"Unsupported energy feature builders: {sorted(unknown)}")
        buses = pd.Index(sorted(str(name) for name in network.buses.index), name="Bus")
        result = pd.DataFrame(index=buses, dtype=float)
        if "geographic_xy" in names:
            for column in ("x", "y"):
                result[column] = pd.to_numeric(
                    network.buses.loc[buses, column], errors="coerce"
                ).fillna(0.0).astype(float)
        if "load_generation" in names:
            result["load_mw"] = 0.0
            result["generation_mw"] = 0.0
            for component, column in ((network.loads, "load_mw"),
                                      (network.generators, "generation_mw")):
                source_column = "p_set" if column == "load_mw" else "p_nom"
                for _, row in component.iterrows():
                    bus = str(row.bus)
                    if bus not in result.index:
                        raise FeatureError(f"Unknown bus for {column}: {bus}")
                    value = pd.to_numeric(row.get(source_column, 0.0), errors="coerce")
                    result.at[bus, column] += 0.0 if pd.isna(value) else float(value)
        if "renewable_time" in names:
            if len(network.snapshots) == 0:
                raise FeatureError("renewable_time requires at least one snapshot")
            columns = [f"renewable_{snapshot}_mw" for snapshot in network.snapshots]
            if len(set(columns)) != len(columns):
                raise FeatureError("Snapshot labels produce duplicate feature names")
            for column in columns:
                result[column] = 0.0
            dynamic = network.generators_t.p_max_pu
            for name, row in network.generators.iterrows():
                carrier = str(row.get("carrier", "")).lower()
                if not any(marker in carrier for marker in _RENEWABLE_MARKERS):
                    continue
                bus = str(row.bus)
                if bus not in result.index:
                    raise FeatureError(f"Unknown renewable bus: {bus}")
                p_nom = float(row.p_nom)
                static_profile = float(row.get("p_max_pu", 1.0))
                for snapshot, column in zip(network.snapshots, columns):
                    value = (dynamic.at[snapshot, name]
                             if name in dynamic.columns and snapshot in dynamic.index
                             else static_profile)
                    if pd.isna(value):
                        raise FeatureError(f"Missing renewable profile: {name}/{snapshot}")
                    result.at[bus, column] += p_nom * float(value)
        if result.empty or result.shape[1] == 0:
            raise FeatureError("At least one enabled feature builder is required")
        return result.fillna(0.0)
