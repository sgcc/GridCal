"""Full Line semantic hashing across backend renaming and reload."""

import pandas as pd
import numpy as np
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.materializers.layer_quotient import LayerQuotientMaterializer
from psdcalc.reduction.validation.protected_parallel import LineSemanticHash


def test_line_hash_uses_stable_ordered_terminals_and_dynamic_values() -> None:
    """Renaming is harmless; direction, type, status and time series are not."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("LineType", "type", r_per_length=0.1, x_per_length=0.2,
          c_per_length=0.01, i_nom=1.0)
    n.add("Line", "source", bus0="a", bus1="b", type="type", length=10,
          s_max_pu=pd.Series([0.9, 0.8], index=n.snapshots))
    ids = PypsaAdapter().identity_sidecar(n, "test")
    line = ids.source_id("Line", "source")
    source_digest = LineSemanticHash().digest(n, ids, line)
    target = n.copy()
    LayerQuotientMaterializer._rename_local(
        target, "Line", {"source": "renamed"})
    target_ids = PypsaAdapter().identity_sidecar(target, "test")
    target_ids = type(ids).from_rows([
        {**row, "source_id": line.encode()} if row["component"] == "Line"
        else row for row in target_ids.to_rows()])
    assert LineSemanticHash().digest(target, target_ids, line) == source_digest
    target.lines.loc["renamed", "bus0"] = "b"
    target.lines.loc["renamed", "bus1"] = "a"
    assert LineSemanticHash().digest(target, target_ids, line) != source_digest
    target.lines.loc["renamed", "bus0"] = "a"
    target.lines.loc["renamed", "bus1"] = "b"
    target.components["Line"].dynamic["s_max_pu"].loc[:, "renamed"] = [0.9, 0.7]
    assert LineSemanticHash().digest(target, target_ids, line) != source_digest
    target.components["Line"].dynamic["s_max_pu"].loc[:, "renamed"] = [0.9, 0.8]
    target.line_types.loc["type", "r_per_length"] = 0.8
    assert LineSemanticHash().digest(target, target_ids, line) != source_digest


def test_line_dynamic_presence_mask_and_static_fallback_reload(tmp_path) -> None:
    """A sparse dynamic input retains its missing-value and dense semantics."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "source", bus0="a", bus1="b", r=0.1, x=0.2,
          s_max_pu=0.95)
    n.components["Line"].dynamic["s_max_pu"] = pd.DataFrame(
        {"source": [0.9, np.nan]}, index=n.snapshots)
    ids = PypsaAdapter().identity_sidecar(n, "test")
    line = ids.source_id("Line", "source")
    before = LineSemanticHash().digest(n, ids, line)
    path = tmp_path / "sparse.nc"
    n.export_to_netcdf(path)
    restored = PypsaAdapter().load(path, "netcdf")
    reloaded_ids = PypsaAdapter().identity_sidecar(restored, "test")
    assert LineSemanticHash().digest(restored, reloaded_ids, line) == before
